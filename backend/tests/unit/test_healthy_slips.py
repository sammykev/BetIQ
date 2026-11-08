"""
High-confidence slips (min chance per pick ≥ 80%, or a least slip chance):
markets and leagues the confidence check found falling short stay out, and
a slip the user builds stops at HIGH_CONF_LEGS legs, the rest of a longer
ask offered as separate slips.
"""

import json

import pytest
from fastapi.testclient import TestClient

import confidence_check
import main
from routers import optimizer as opt_router
from tests.unit.test_optimizer import pred


def test_weak_cells_need_both_a_low_z_and_a_real_gap():
    rep = {"markets": [{"cell": "football:1x2", "z": -3.9, "gap": -0.4},
                       {"cell": "football:goals_ou", "z": -2.5, "gap": -0.01},
                       {"cell": "football:btts", "z": 0.3, "gap": 0.02}],
           "leagues": [{"cell": "football:CL", "z": -7.7, "gap": -0.63},
                       {"cell": "football:PD", "z": None, "gap": None}]}
    assert confidence_check.weak_cells(rep) == {"football:1x2", "football:CL"}
    assert confidence_check.weak_cells(None) == set()


def test_weak_cells_read_from_the_stored_report_once_an_hour(monkeypatch):
    reads = []

    class R:
        def get(self, k):
            reads.append(k)
            return json.dumps({"markets": [{"cell": "football:1x2", "z": -4, "gap": -0.3}], "leagues": []})
    monkeypatch.setattr(main, "_get_redis", lambda: R())
    monkeypatch.setattr(opt_router, "_weak_cache", {"at": 0.0, "cells": set()})
    assert main._weak_cells() == {"football:1x2"}
    assert main._weak_cells() == {"football:1x2"} and reads == [confidence_check.REPORT_KEY]


class TestHighConfidenceSlips:
    @pytest.fixture(autouse=True)
    def cache(self, monkeypatch):
        preds = [pred(i, odds_home=1.7, odds_draw=3.6, odds_away=5.0, league="SA" if i % 5 == 0 else "PL")
                 for i in range(30)]
        monkeypatch.setattr(main, "_predictions_cache", preds)
        monkeypatch.setattr(main, "_sb_links", {})
        monkeypatch.setattr(main, "_get_redis", lambda: None)
        monkeypatch.setattr(main, "_review", {"mode": "auto", "overrides": {}, "loaded": True, "latest": {}})
        self.weak = set()
        monkeypatch.setattr(main, "_weak_cells", lambda: self.weak)

    def post(self, **body):
        return TestClient(main.app).post("/api/optimizer", json={
            "min_odds": 3, "max_odds": 4.5, "days": 3, "min_prob": 0.8, "no_live_check": True, **body}).json()

    def test_weak_markets_and_leagues_left_out(self):
        self.weak = {"football:double_chance", "football:SA"}
        r = self.post()
        assert r["picks"] and all(p["market"] != "double_chance" and p["league"] != "SA" for p in r["picks"])
        assert r["weak_left_out"] == ["football:SA", "football:double_chance"]

    def test_low_confidence_slips_keep_them(self):
        self.weak = {"football:double_chance"}
        r = self.post(min_prob=0.6, markets=["double_chance"])
        assert r["picks"] and "weak_left_out" not in r
        assert "weak_left_out" in self.post(min_prob=0.6, healthy_only=True, markets=["double_chance", "goals_ou"])

    def test_slips_the_user_builds_arent_cut_to_10_legs(self, monkeypatch):
        asked = []
        real = main._solve

        async def solve(groups, lo, hi, target, max_games, *a, **k):
            asked.append(max_games)
            return await real(groups, lo, hi, target, max_games, *a, **k)
        monkeypatch.setattr(main, "_solve", solve)
        r = self.post(max_games=30)
        assert asked and set(asked) == {30} and "leg_cap" not in r and "extra_slips" not in r

    def test_75_percent_picks_are_high_confidence(self):
        assert opt_router.HIGH_CONF_PROB == 0.75
        self.weak = {"football:double_chance"}
        assert "weak_left_out" in self.post(min_prob=0.75)
        assert "weak_left_out" not in self.post(min_prob=0.7, markets=["double_chance"])


def test_daily_slips_stop_at_10_legs_and_the_optimizer_at_50():
    import booking_slip
    import daily_slips
    import optimizer
    assert daily_slips.request(10, daily_slips.ATTEMPTS[0])["max_games"] == daily_slips.MAX_LEGS == 10
    assert optimizer.MAX_GAMES == booking_slip.MAX_SELECTIONS == 50
