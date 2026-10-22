"""
Does SofaScore xG (club_xg.py) improve the club model? A walk-forward
backtest (backtest.walk_forward, retrained monthly) over the last MONTHS
months, with the xG features off, in the goals models only, and in every
model; scored on the leagues we have xG for. The verdict goes to Redis
(club_xg.CHECK_KEY), which main._train_new reads:

- "goals" if the over-2.5 log loss improves by at least MIN_GAIN and the
  1X2 log loss isn't worse;
- True if, on top of that, every-model also improves the 1X2 log loss by
  MIN_GAIN (and keeps the over-2.5 gain);
- else False (the model stays as it is).

    python xg_check.py               # needs UPSTASH_REDIS_URL
    python xg_check.py --months 6
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

import pandas as pd

import backtest
import club_xg
from international_stats import CLUB_TOURNAMENTS

MONTHS = int(os.getenv("XG_CHECK_MONTHS", "8"))
MIN_GAIN = 0.001
MIN_COVERAGE = 0.6   # share of the tested xG-league matches with xG for both teams' history


def scores(records: List[Dict]) -> Dict[str, Any]:
    if not records:
        return {"n": 0}
    return {
        "n": len(records),
        "1x2": backtest._scores_1x2([backtest._model_1x2(r) for r in records], [r["result"] for r in records]),
        "over25": backtest._scores_binary([r["p_over25"] for r in records],
                                          [r["home_goals"] + r["away_goals"] >= 3 for r in records]),
    }


def decide(results: Dict[str, Dict]) -> Any:
    """The setting to adopt from the scores of the three variants."""
    base, goals, every = results.get(False), results.get("goals"), results.get(True)
    if not base or not base.get("n"):
        return False

    def ll(res, market):
        return (res or {}).get(market, {}).get("log_loss")

    def gain(res, market):
        a, b = ll(base, market), ll(res, market)
        return None if a is None or b is None else a - b

    choice = False
    g_ou, g_1x2 = gain(goals, "over25"), gain(goals, "1x2")
    if g_ou is not None and g_ou >= MIN_GAIN and g_1x2 is not None and g_1x2 >= 0:
        choice = "goals"
    e_ou, e_1x2 = gain(every, "over25"), gain(every, "1x2")
    if (choice == "goals" and e_1x2 is not None and e_1x2 >= MIN_GAIN
            and e_ou is not None and e_ou >= MIN_GAIN):
        choice = True
    return choice


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=MONTHS)
    args = ap.parse_args()
    import main as app
    import model_store
    import tune_goals
    from predictor import LeaguePredictor

    r = model_store._client()
    if r is None:
        print("UPSTASH_REDIS_URL isn't set. Skipping.")
        return 0
    data = club_xg.load(r)
    print("Dataset:", json.dumps(club_xg.summary(data), indent=1))
    asyncio.run(app._sync_football_data(force=True))
    assembled = app._assemble_training_data()
    if assembled is None:
        raise SystemExit("no training data")
    matches = club_xg.attach(assembled[1], data)
    cover = club_xg.coverage(matches)
    print("Coverage:", json.dumps(cover, indent=1))
    print("Unmatched SofaScore names:", json.dumps(club_xg.unmatched_teams(matches, data), indent=1))

    leagues = set(CLUB_TOURNAMENTS.values())
    scored = matches[matches["league"].isin(leagues) & matches["FTHG"].notna()]
    end = scored["Date"].max()
    start = end - pd.DateOffset(months=args.months)
    window = scored[scored["Date"] >= start]
    share = float(window["HXG"].notna().mean()) if len(window) else 0.0
    verdict: Dict[str, Any] = {"at": datetime.now(timezone.utc).isoformat(), "window": [str(start.date()), str(end.date())],
                               "coverage": cover, "test_xg_share": round(share, 3)}
    if share < MIN_COVERAGE:
        verdict.update(use_xg=False, reason=f"only {share:.0%} of the test window's matches have xG yet; collect more")
        r.set(club_xg.CHECK_KEY, json.dumps(verdict))
        print(json.dumps(verdict, indent=1))
        return 0

    params = tune_goals.load(r)
    results: Dict[Any, Dict] = {}
    for setting in (False, "goals", True):
        def make(setting=setting) -> LeaguePredictor:   # as main._train_new
            p = LeaguePredictor()
            p.use_league_strength = app._club_league_strength
            p.elo.seed_national_teams()
            p.league_params = params
            p.use_xg = setting
            return p
        t0 = time.time()
        records = backtest.walk_forward(matches, str(start.date()), str(end.date()), make_model=make,
                                        log=lambda m: None)
        results[setting] = {
            "xg_leagues": scores([x for x in records if x["league"] in leagues]),
            "all": scores(records),
        }
        print(f"use_xg={setting!r}: {json.dumps(results[setting])} ({time.time() - t0:.0f}s)")

    choice = decide({k: v["xg_leagues"] for k, v in results.items()})
    # Never worse overall either (the other leagues run on default xG)
    if choice is not False and decide({k: v["all"] for k, v in results.items()}) is False:
        all_1x2 = [results[s]["all"].get("1x2", {}).get("log_loss") for s in (False, choice)]
        if None not in all_1x2 and all_1x2[1] > all_1x2[0]:
            choice = False
    verdict.update(use_xg=choice, results={repr(k): v for k, v in results.items()})
    r.set(club_xg.CHECK_KEY, json.dumps(verdict))
    print("Verdict:", json.dumps(verdict, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
