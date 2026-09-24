"""
Slip optimizer: candidate picks with the best available price, and an exact
search for the highest win chance within a target odds range.
"""

import itertools
import math
import random
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import main
import optimizer
import sportybet
from optimizer import Option, candidates, optimize


def pred(i=0, day=None, **kw):
    return {"home": f"Home{i}", "away": f"Away{i}", "date": day or (date.today() + timedelta(days=1)).isoformat(),
            "time": "15:00", "league": "PL", "league_name": "Premier League",
            "p_home": 0.6, "p_draw": 0.25, "p_away": 0.15, "p_over15": 0.8, "p_over25": 0.55, **kw}


class TestCandidates:
    def test_prices_best_source_first(self):
        event = {"eventId": "sr:match:1", "markets": [
            {"id": "1", "specifier": "", "outcomes": [{"id": "1", "odds": "1.70"}]}]}
        opts = {o.code: o for o in candidates(pred(odds_home=1.65, odds_draw=3.8, odds_away=6.0), event, 0.5)}
        assert (opts["1"].odds, opts["1"].odds_source) == (1.7, "sportybet")
        assert opts["1X"].odds_source == "bookmaker"
        assert opts["1X"].odds == round(1 / (1 / 1.65 + 1 / 3.8), 2)
        assert (opts["O15"].odds, opts["O15"].odds_source) == (round(optimizer.MARGIN / 0.8, 2), "estimated")

    def test_minimum_confidence_and_markets(self):
        codes = {o.code for o in candidates(pred(), None, 0.7)}
        assert codes == {"1X", "12", "O15"}  # 0.85, 0.75, 0.8
        assert {o.code for o in candidates(pred(), None, 0.5, {"goals_ou"})} == {"O15", "O25"}

    def test_every_candidate_can_be_booked_on_sportybet(self):
        from booking_slip import sportybet_ids
        for o in candidates(pred(), None, 0.0):
            assert sportybet_ids(o.market, o.code), (o.market, o.code)

    def test_prediction_without_probabilities_has_none(self):
        assert candidates({"home": "A", "away": "B", "date": "2026-09-26"}) == []


def option(i, prob, odds, code="1"):
    return Option(f"H{i}", f"A{i}", "2026-09-26", "15:00", "PL", "1x2", "Match Result", code, "x", prob, odds, "bookmaker")


def brute_force(groups, lo, hi, max_games):
    best = None
    for choice in itertools.product(*[[None] + g for g in groups]):
        picks = [o for o in choice if o]
        if not picks or len(picks) > max_games:
            continue
        total = math.prod(o.odds for o in picks)
        if lo <= total <= hi:
            chance = math.prod(o.prob for o in picks)
            if best is None or chance > best:
                best = chance
    return best


class TestOptimize:
    @pytest.mark.parametrize("seed", range(6))
    def test_finds_the_best_slip(self, seed):
        rng = random.Random(seed)
        groups = [[option(i, p := rng.uniform(0.5, 0.95), round(rng.uniform(0.9, 1.1) / p, 2), c)
                   for c in ("1", "X")] for i in range(7)]
        lo, hi = 3.0, 6.0
        result = optimize(groups, lo, hi, max_games=5)
        best = brute_force(groups, lo, hi, 5)
        if best is None:
            assert result is None or not result["within_target"]
        else:
            assert result["within_target"] and lo <= result["total_odds"] <= hi
            assert result["win_chance"] == pytest.approx(best, rel=0.02)

    def test_one_pick_per_match_and_game_limit(self):
        groups = [[option(i, 0.9, 1.1, "1"), option(i, 0.85, 1.2, "X")] for i in range(40)]
        r = optimize(groups, 5, 8, max_games=30)
        matches = [p["home"] for p in r["picks"]]
        assert len(matches) == len(set(matches)) == r["games"] <= 30

    def test_unreachable_target(self):
        assert optimize([[option(0, 0.9, 1.1)]], 50, 100) is None

    def test_counts_estimated_prices(self):
        g = [[Option("H", "A", "2026-09-26", "", "PL", "1x2", "Match Result", "1", "x", 0.6, 1.6, "estimated")]]
        assert optimize(g, 1.5, 2)["estimated_prices"] == 1


class TestEndpoint:
    @pytest.fixture(autouse=True)
    def cache(self, monkeypatch):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        preds = [pred(i, odds_home=1.7, odds_draw=3.6, odds_away=5.0) for i in range(8)]
        preds[0]["sportybet"] = True
        preds[1]["league"] = "INT-FRI"
        preds.append(pred(99, day=(date.today() + timedelta(days=9)).isoformat()))
        monkeypatch.setattr(main, "_predictions_cache", preds)
        monkeypatch.setattr(main, "_sb_links", {})
        monkeypatch.setattr(main, "_get_redis", lambda: None)
        self.tomorrow = tomorrow

    def post(self, **body):
        return TestClient(main.app).post("/api/optimizer", json=body)

    def test_builds_a_slip_in_range(self):
        r = self.post(min_odds=3, max_odds=6, days=3).json()
        assert r["within_target"] and 3 <= r["total_odds"] <= 6
        assert r["matches_considered"] == 8  # the day-9 match is outside 3 days
        assert all(p["date"] == self.tomorrow for p in r["picks"])

    def test_filters(self):
        assert self.post(min_odds=1.1, max_odds=2, bookable_only=True).json()["matches_considered"] == 1
        assert self.post(min_odds=1.1, max_odds=2, leagues=["INT-FRI"]).json()["matches_considered"] == 1

    def test_unreachable_target_explains(self):
        r = self.post(min_odds=100000, max_odds=200000, max_games=2).json()
        assert "gets near" in r["error"]

    @pytest.mark.parametrize("body", [{"min_odds": 5, "max_odds": 2}, {"min_odds": 0.5, "max_odds": 2},
                                      {"min_odds": "x", "max_odds": 2}])
    def test_bad_settings(self, body):
        assert self.post(**body).status_code == 400


class TestMoreMarkets:
    SP = {"corners": {"mean": 9.6, "over": {"7.5": 0.74, "8.5": 0.62, "9.5": 0.5, "10.5": 0.38, "11.5": 0.27}},
          "bookings": {"mean": 4.2, "over": {"2.5": 0.8, "3.5": 0.6, "4.5": 0.4, "5.5": 0.25, "6.5": 0.15}}}

    def test_corners_bookings_btts_and_over_35(self):
        opts = {(o.market, o.code): o for o in candidates(pred(p_over35=0.3, p_btts=0.62, set_pieces=self.SP), None, 0.6)}
        assert opts[("corners_ou", "O75")].prob == 0.74 and opts[("corners_ou", "O75")].label == "Over 7.5 corners"
        assert opts[("cards_ou", "O25")].prob == 0.8
        assert opts[("cards_ou", "U55")].prob == 0.75
        assert opts[("goals_ou", "U35")].prob == 0.7
        assert opts[("btts", "BTTS-Y")].prob == 0.62
        # corners/bookings are estimated with a wider bookmaker margin
        assert opts[("corners_ou", "O75")].odds == round(optimizer._MARGINS["corners_ou"] / 0.74, 2)

    def test_matches_without_stats_offer_no_corners(self):
        assert not [o for o in candidates(pred(), None, 0.5) if o.market in ("corners_ou", "cards_ou", "btts")]

    def test_every_new_market_can_be_booked(self):
        from booking_slip import sportybet_ids
        for o in candidates(pred(p_over35=0.3, p_btts=0.5, set_pieces=self.SP), None, 0.0):
            assert sportybet_ids(o.market, o.code), (o.market, o.code)

    def test_sportybet_corner_price_only_when_its_label_says_corners(self):
        def event(desc):
            return {"eventId": "sr:match:1", "markets": [{"id": "166", "specifier": "total=8.5", "desc": desc,
                                                         "outcomes": [{"id": "12", "odds": "1.55"}]}]}
        p = pred(set_pieces=self.SP)
        good = {o.code: o for o in candidates(p, event("Total Corners"), 0.6, {"corners_ou"})}
        wrong = {o.code: o for o in candidates(p, event("Total Goals"), 0.6, {"corners_ou"})}
        assert (good["O85"].odds, good["O85"].odds_source) == (1.55, "sportybet")
        assert wrong["O85"].odds_source == "estimated"


class TestTargetOdds:
    def test_lands_within_tolerance_of_the_target(self):
        random.seed(3)
        groups = [[option(i, random.uniform(0.55, 0.9), round(random.uniform(1.15, 2.2), 2))] for i in range(40)]
        for target in (3, 12.5, 47, 260, 1800):
            res = optimize(groups, target * 0.98, target * 1.02, 30)
            assert res and res["within_target"], target
            assert abs(res["total_odds"] / target - 1) <= 0.02 + 1e-9

    def test_endpoint_falls_back_to_the_nearest_slip(self, monkeypatch):
        preds = [pred(i, p_home=0.62, p_draw=0.22, p_away=0.16, odds_home=1.5, odds_draw=4.0, odds_away=6.0)
                 for i in range(2)]
        monkeypatch.setattr(main, "_predictions_cache", preds)
        body = {"target_odds": 2.0, "min_odds": 1.96, "max_odds": 2.04, "markets": ["1x2"], "min_prob": 0.6, "days": 3}
        res = TestClient(main.app).post("/api/optimizer", json=body).json()
        # 1.5 or 2.25 only: nothing within ±2%, the nearest within ±25% comes back flagged
        assert res["within_target"] is False and res["target_odds"] == 2.0
        assert res["total_odds"] in (1.5, 2.25)
