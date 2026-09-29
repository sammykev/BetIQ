"""The basketball walk-forward check on a simulated league: our chances
come in about as often as we say, and the spread scale is near one."""

import basketball_data as bd
import backtest_basketball as bb
from tests.unit.test_basketball_model import simulate


def results_from(games):
    from datetime import datetime, timezone
    return [{"id": str(i), "t": "Sim · League", "h": g.home, "a": g.away,
             "ko": int(datetime.fromisoformat(g.date).replace(tzinfo=timezone.utc).timestamp()),
             "hs": g.hs, "as": g.as_, "q": [list(p) for p in g.periods], "ot": g.ot} for i, g in enumerate(games)]


def test_walk_forward_on_a_simulated_league():
    games, *_ = simulate(seasons=2, seed=3)
    report = bb.run(results_from(games), min_games=150)
    lg = report["leagues"]["Sim · League"]
    assert lg["tested"] > 100 and 0.85 < lg["sigma_scale"]["margin"] < 1.2
    for row in report["overall"]:
        if row["n"] >= 200:
            assert abs(row["came_in"] - row["said"]) < 0.06, row
    # The server applies the scale to the league's spreads
    league = bd.fit_all(results_from(games))["Sim · League"]
    before = league.sigma["margin"]
    bd.apply_calibration(league, {"leagues": {"Sim · League": {"sigma_scale": {"margin": 1.2, "total": 1.0}}}})
    assert league.sigma["margin"] == before * 1.2
