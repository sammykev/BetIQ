"""
Per-market accuracy (market_accuracy.py): every pick the model rated ≥ 50%,
kept with the match snapshot and settled at full time.
"""

import matchday
import market_accuracy as ma

PRED = {"home": "A", "away": "B", "date": "2026-09-20", "time": "15:00", "p_home": 0.55, "p_draw": 0.25,
        "p_away": 0.2, "p_over15": 0.8, "p_over25": 0.6, "p_over35": 0.3, "p_btts": 0.45,
        "set_pieces": {"corners": {"mean": 10, "over": {"7.5": 0.8, "8.5": 0.7, "9.5": 0.55, "10.5": 0.4, "11.5": 0.3}}}}
RESULT = {"status": "finished", "hg": 2, "ag": 1, "corners": [6, 5], "bookings": [2, 1]}


class TestPicks:
    def test_snapshot_keeps_every_pick_at_50_or_more_but_the_site_does_not_show_them(self):
        snap = matchday.snapshot(PRED)
        assert snap["picks"]["1x2"] == {"1": 0.55}
        assert snap["picks"]["goals_ou"] == {"O15": 0.8, "O25": 0.6, "U35": 0.7}
        assert snap["picks"]["btts"] == {"BTTS-N": 0.55}
        assert snap["picks"]["corners_ou"]["O95"] == 0.55 and "U75" not in snap["picks"]["corners_ou"]
        assert "picks" not in matchday.public({**PRED, "pred": snap})["pred"]


class TestReport:
    def test_settles_by_market_and_band(self):
        entry = {**PRED, "pred": matchday.snapshot(PRED), "result": RESULT}
        rep = ma.report([entry, {**PRED, "pred": matchday.snapshot(PRED), "result": None}])
        assert rep["matches"] == 1
        goals = next(m for m in rep["by_market"] if m["market"] == "goals_ou")
        assert (goals["picks"], goals["won"]) == (3, 3) and goals["name"] == "Total Goals"
        btts = next(m for m in rep["by_market"] if m["market"] == "btts")
        assert (btts["picks"], btts["won"], btts["hit_rate"]) == (1, 0, 0.0)
        corners = next(m for m in rep["by_market"] if m["market"] == "corners_ou")
        assert corners["picks"] == 5 and corners["won"] == 4          # 11 corners: over 10.5 won, under 11.5 won
        assert sum(b["picks"] for b in rep["by_band"]) == rep["all"]["picks"]

    def test_older_snapshots_still_count(self):
        snap = {k: v for k, v in matchday.snapshot(PRED).items() if k != "picks"}
        snap["bookings_over"] = 0.3
        rep = ma.report([{**PRED, "pred": snap, "result": RESULT}])
        markets = {m["market"]: m for m in rep["by_market"]}
        assert {"1x2", "double_chance", "goals_ou", "btts", "corners_ou", "cards_ou"} <= set(markets)
        assert markets["cards_ou"]["picks"] == 1 and markets["cards_ou"]["won"] == 1   # under 4.5 at 70%, 3 bookings
