"""
SportyBet prices kept with each match (price_book.py) and the profit report.
"""

import matchday
import price_book


def pred(**kw):
    return {"home": "Arsenal", "away": "Chelsea", "date": "2026-09-20", "time": "15:00", "league": "PL",
            "p_home": 0.55, "p_draw": 0.25, "p_away": 0.20, "p_over15": 0.8, "p_over25": 0.6, **kw}


EVENT = {"markets": [
    {"id": "1", "specifier": "", "outcomes": [{"id": "1", "odds": "2.10"}, {"id": "2", "odds": "3.40"},
                                               {"id": "3", "odds": "3.60"}]},
    {"id": "18", "specifier": "total=2.5", "outcomes": [{"id": "12", "odds": "1.90"}, {"id": "13", "odds": "1.90"}]},
]}


class TestPrices:
    def test_only_sportybet_priced_outcomes(self):
        got = price_book.prices(pred(), EVENT)
        assert ["1x2", "1", 0.55, 2.1] in got and ["goals_ou", "O25", 0.6, 1.9] in got
        assert all(len(x) == 4 for x in got)
        assert not any(m == "btts" for m, *_ in got)          # SportyBet didn't price it
        assert price_book.prices(pred(), None) is None

    def test_kept_with_the_snapshot_but_not_shown(self):
        snap = matchday.snapshot({**pred(), "_sb_prices": [["1x2", "1", 0.55, 2.1]]})
        assert snap["prices"] == [["1x2", "1", 0.55, 2.1]]
        entry = {**pred(), "pred": snap, "result": None}
        assert "prices" not in matchday.public(entry)["pred"]


class TestReport:
    def test_settles_and_splits_by_edge(self):
        entry = {"pred": {"prices": [["1x2", "1", 0.55, 2.1], ["1x2", "2", 0.2, 3.6], ["goals_ou", "O25", 0.6, 1.9]]},
                 "result": {"status": "finished", "hg": 2, "ag": 1}}
        pending = {"pred": {"prices": [["1x2", "1", 0.5, 2.0]]}, "result": None}
        rep = price_book.report([entry, pending])
        assert rep["matches"] == 2
        assert rep["all_priced"] == {"bets": 3, "won": 2, "profit": round(1.1 + 0.9 - 1, 2), "roi": round(1.0 / 3, 4),
                                     "avg_prob": 0.45, "hit_rate": 0.667}
        # EV: home 0.155, away -0.28, over 0.14 → value = home + over
        assert rep["value"]["bets"] == 2 and rep["value"]["won"] == 2
        edges = {b["edge"]: b["bets"] for b in rep["by_edge"]}
        assert edges["below 0"] == 1 and edges["10–20%"] == 2
        assert {m["market"] for m in rep["by_market"]} == {"1x2", "goals_ou"}
