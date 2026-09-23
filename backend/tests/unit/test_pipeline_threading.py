"""
Training runs on a worker thread, so the API keeps answering while a new
model trains (a blocked event loop made Render return errors without CORS
headers, seen in the browser as net::ERR_FAILED).
"""

import asyncio
import threading
import time

import pandas as pd

import main
from predictor import LeaguePredictor


def test_training_does_not_block_the_event_loop(monkeypatch, tmp_path):
    from tests.unit.test_model_upgrades import season

    trained_on = {}

    def slow_train(self, df):
        trained_on["thread"] = threading.current_thread()
        time.sleep(0.5)  # stands in for minutes of XGBoost
        self._ready = False  # nothing downstream should use this model

    async def no_sync(*a, **k):
        return None

    monkeypatch.setattr(LeaguePredictor, "train", slow_train)
    monkeypatch.setattr(LeaguePredictor, "load_cache", classmethod(lambda cls, m: None))
    monkeypatch.setattr(LeaguePredictor, "save_cache", lambda self, m: None)
    monkeypatch.setattr(main, "_sync_football_data", no_sync)
    monkeypatch.setattr(main, "_load_football_data_csvs", lambda: season())
    for loader in ("_load_epl_csv", "_load_ucl_csv", "_load_international_csv"):
        monkeypatch.setattr(main, loader, lambda: pd.DataFrame())
    monkeypatch.setattr(main, "RESULTS_CSV", str(tmp_path / "none.csv"))
    monkeypatch.setattr(main, "_get_redis", lambda: None)
    monkeypatch.setattr(main, "API_KEY", "")
    monkeypatch.setattr(main, "_is_training", False)

    async def scenario():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.05)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        try:
            await main._run_pipeline()
        finally:
            beat.cancel()
        return ticks

    ticks = asyncio.run(scenario())
    assert trained_on["thread"] is not threading.main_thread()
    assert ticks >= 5  # the loop kept running during the 0.5 s "training"
