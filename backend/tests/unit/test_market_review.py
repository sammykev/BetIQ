"""
The weekly accuracy review (market_review.py): markets whose picks come in
clearly less often than the model said are paused from the optimizer, the
daily slips and the code check until they recover.
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
import market_review as mr
from tests.unit.test_optimizer import pred
from tests.unit.test_user_endpoints import FakeRedis


def rows(n_matches, prob, hit_rate, per_match=1):
    """n_matches matches, per_match picks each at `prob`, the first
    hit_rate share of matches winning all their picks."""
    wins = round(n_matches * hit_rate)
    return [(m, prob, m < wins) for m in range(n_matches) for _ in range(per_match)]


def entry(i, day, won_goals, prob=0.75):
    """A settled match whose only pick is over 1.5 goals at `prob`."""
    goals = (2, 1) if won_goals else (1, 0)
    return {"home": f"H{i}", "away": f"A{i}", "date": day,
            "pred": {"picks": {"goals_ou": {"O15": prob}}},
            "result": {"status": "finished", "hg": goals[0], "ag": goals[1]}}


class TestCheck:
    def test_counts_and_gap(self):
        c = mr.check(rows(40, 0.75, 0.5))
        assert (c["picks"], c["matches"], c["hit_rate"], c["model_said"], c["gap"]) == (40, 40, 0.5, 0.75, -0.25)
        assert c["z"] < -3

    def test_picks_of_one_match_count_as_one_chance(self):
        # The same shortfall spread over fewer matches is less sure
        single = mr.check(rows(40, 0.75, 0.5))
        grouped = mr.check(rows(10, 0.75, 0.5, per_match=4))
        assert grouped["gap"] == single["gap"] and grouped["z"] > single["z"]

    def test_empty(self):
        assert mr.check([])["picks"] == 0


class TestVerdict:
    def v(self, overall, top=()):
        return mr.verdict(mr.check(overall), mr.check(list(top)))[0]

    def test_clearly_short_over_enough_matches_pauses(self):
        assert self.v(rows(60, 0.75, 0.5)) == "pause"

    def test_short_but_too_few_matches_is_only_watched(self):
        assert self.v(rows(20, 0.75, 0.45)) == "watch"

    def test_small_gap_is_ok(self):
        assert self.v(rows(200, 0.75, 0.73)) == "ok"

    def test_thin_market(self):
        assert self.v(rows(8, 0.75, 0.75)) == "few"

    def test_undersold(self):
        assert self.v(rows(200, 0.6, 0.8)) == "better"

    def test_high_confidence_picks_are_judged_on_their_own(self):
        # Fine overall, but the 80%+ picks (what the daily slips use) fall well short
        top = [(m + 1000, 0.88, m < 24) for m in range(40)]
        low = rows(300, 0.6, 0.66)
        status, why = mr.verdict(mr.check(low + top), mr.check(top))
        assert status == "pause" and "at 80%+" in why


class TestReview:
    def test_newly_paused_kept_while_watched_restored_when_ok(self):
        day = "2026-09-20"
        bad = [entry(i, day, i < 25) for i in range(60)]        # 42% vs 75%
        first = mr.review(bad)
        assert first["paused"] == ["goals_ou"] and first["newly_paused"] == ["goals_ou"]
        goals = first["markets"][0]
        assert goals["status"] == "pause" and goals["paused"] and "came in 42%" in goals["why"]
        # A week that's short but not clearly: still paused
        meh = [entry(i, day, i < 11) for i in range(20)]   # 55%, 20 matches
        assert mr.review(meh, ["goals_ou"])["paused"] == ["goals_ou"]
        # No picks at all this time: stays paused (and still listed)
        empty = mr.review([], ["goals_ou"])
        assert empty["paused"] == ["goals_ou"] and empty["markets"][0]["status"] == "few"
        # Back to what the model says: restored
        good = [entry(i, day, i < 45) for i in range(60)]
        back = mr.review(good, ["goals_ou"])
        assert back["paused"] == [] and back["restored"] == ["goals_ou"]

    def test_remember_keeps_a_short_history(self):
        state = {}
        for i in range(mr.HISTORY + 3):
            state = mr.remember(state, mr.review([]), f"2026-09-{i + 1:02d}T05:50:00+00:00")
        assert len(state["history"]) == mr.HISTORY and state["history"][0]["at"].startswith("2026-09-15")
        assert state["latest"]["at"].startswith("2026-09-15")


class TestSettings:
    def test_blocked(self):
        state = {"mode": "auto", "overrides": {"btts": "off", "goals_ou": "on"},
                 "latest": {"paused": ["goals_ou", "corners_ou"]}}
        assert mr.blocked(state) == {"btts", "corners_ou"}
        assert mr.blocked({**state, "mode": "flag"}) == {"btts"}

    def test_validation(self):
        new = mr.settings({"mode": "flag", "overrides": {"btts": "off", "1x2": "auto"}}, {})
        assert new == {"mode": "flag", "overrides": {"btts": "off"}}
        for bad in ({"mode": "sometimes"}, {"overrides": {"nope": "off"}}, {"overrides": {"btts": "maybe"}},
                    {"overrides": []}):
            with pytest.raises(ValueError):
                mr.settings(bad, {})

    def test_alert(self):
        paused = {"market": "goals_ou", "status": "pause"}
        state = {"mode": "auto", "latest": {"markets": [paused], "newly_paused": ["goals_ou"]}}
        assert "Total Goals" in mr.alert(state, 1)["title"]
        assert mr.alert(state, 9) is None                                   # old news
        assert mr.alert({**state, "latest": {**state["latest"], "newly_paused": []}}, 1) is None
        assert "Flag mode" in mr.alert({**state, "mode": "flag"}, 1)["detail"]


@pytest.fixture
def server(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    monkeypatch.setattr(main, "_review", {"mode": "auto", "overrides": {}, "latest": None, "history": [], "loaded": False})

    async def identity(request):
        return "secret", None
    monkeypatch.setattr(main, "_admin_identity", identity)
    return fake


class TestServer:
    def test_weekly_run_reads_the_last_four_weeks_and_saves(self, server, monkeypatch):
        days = {}
        for d in range(1, 29):
            day = (date.today() - timedelta(days=d)).isoformat()
            days[day] = {f"{i}": entry(i, day, (d * 3 + i) % 5 < 2) for i in range(3)}   # 40% vs 75%
        seen = []
        monkeypatch.setattr(main, "_md_many", lambda r, ds: seen.extend(ds) or {d: days.get(d, {}) for d in ds})
        result = main._run_review()
        assert len(seen) == mr.WINDOW_DAYS and (date.today().isoformat() not in seen)
        assert result["paused"] == ["goals_ou"]
        saved = json.loads(server.kv[main.REVIEW_KEY])
        assert saved["latest"]["paused"] == ["goals_ou"] and len(saved["history"]) == 1
        assert main._paused_markets() == {"goals_ou"}

    def test_admin_settings_and_public_list(self, server):
        c = TestClient(main.app)
        main._review.update(latest={"paused": ["goals_ou"], "markets": [
            {"market": "goals_ou", "status": "pause", "why": "came in 40% vs 75% said, over 60 matches"}]})
        assert c.get("/api/admin/market-review").json()["blocked"] == ["goals_ou"]
        paused = c.get("/api/market-review/paused").json()["paused"]
        assert paused == [{"market": "goals_ou", "name": "Total Goals", "why": "came in 40% vs 75% said, over 60 matches"}]
        r = c.put("/api/admin/market-review", json={"overrides": {"goals_ou": "on", "btts": "off"}}).json()
        assert r["blocked"] == ["btts"] and r["overrides"] == {"goals_ou": "on", "btts": "off"}
        assert json.loads(server.kv[main.REVIEW_KEY])["overrides"]["btts"] == "off"
        assert c.get("/api/market-review/paused").json()["paused"][0]["why"] == "switched off by the admin"
        assert c.put("/api/admin/market-review", json={"mode": "never"}).status_code == 400

    def test_alert_after_a_review_paused_something(self, server, monkeypatch):
        monkeypatch.setattr(main, "_STARTED_AT", 0.0)
        monkeypatch.setitem(main._md_status, "at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        main._review.update(loaded=True, latest={"at": now, "paused": ["btts"], "newly_paused": ["btts"],
                                                 "markets": [{"market": "btts", "status": "pause"}]})
        alerts = TestClient(main.app).get("/api/admin/alerts").json()["alerts"]
        assert [a["title"] for a in alerts] == ["Accuracy review paused Both Teams to Score"]


class TestOptimizerLeavesPausedMarketsOut:
    @pytest.fixture(autouse=True)
    def cache(self, monkeypatch):
        preds = [pred(i, odds_home=1.7, odds_draw=3.6, odds_away=5.0) for i in range(8)]
        monkeypatch.setattr(main, "_predictions_cache", preds)
        monkeypatch.setattr(main, "_sb_links", {})
        monkeypatch.setattr(main, "_get_redis", lambda: None)
        monkeypatch.setattr(main, "_review", {"mode": "auto", "overrides": {}, "loaded": True,
                                              "latest": {"paused": ["1x2"]}})

    def post(self, **body):
        return TestClient(main.app).post("/api/optimizer", json={"min_odds": 3, "max_odds": 6, "days": 3, **body}).json()

    def test_paused_market_never_picked(self):
        r = self.post(markets=["1x2", "double_chance", "goals_ou"])
        assert r["picks"] and all(p["market"] != "1x2" for p in r["picks"])

    def test_only_paused_markets_asked_for(self):
        r = self.post(markets=["1x2"])
        assert "Match Result is paused" in r["error"] and r["paused"] == ["1x2"]

    def test_flag_mode_leaves_it_in(self):
        main._review["mode"] = "flag"
        assert "picks" in self.post(markets=["1x2"])
