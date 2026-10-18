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

    def test_long_ask_capped_and_split(self):
        r = self.post(max_games=30)
        assert len(r["picks"]) <= opt_router.HIGH_CONF_LEGS
        assert r["leg_cap"]["cap"] == 10 and r["leg_cap"]["asked"] == 30 and "73.9" not in r["leg_cap"]["note"]
        assert "74%" in r["leg_cap"]["note"]
        seen = [(p["home"], p["away"]) for s in [r] + r["extra_slips"] for p in s["picks"]]
        assert r["extra_slips"] and len(seen) == len(set(seen))
        assert all(len(s["picks"]) <= 10 and "bookable" in s["picks"][0] for s in r["extra_slips"])

    def test_no_cap_below_high_confidence_or_when_allowed(self):
        assert "leg_cap" not in self.post(max_games=30, min_prob=0.6)
        assert "leg_cap" not in self.post(max_games=30, allow_long=True)
        assert "leg_cap" not in self.post(max_games=8)
