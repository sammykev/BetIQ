"""
Daily odds (daily_slips.py): three slips a day from 85%+ picks, built once,
graded from the results, and counted in the record once each.
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import daily_slips
import main
import matchday
from tests.unit.test_matchday import finished, pred
from tests.unit.test_user_endpoints import FakeRedis


def result(target, legs):
    picks = [{"home": h, "away": a, "date": d, "time": "00:00", "league": "PL", "market": m, "market_name": m,
              "code": c, "label": c, "prob": 0.88, "odds": 1.2, "odds_source": "sportybet", "bookable": True}
             for h, a, d, m, c in legs]
    return {"picks": picks, "total_odds": target, "win_chance": 0.16, "within_target": True}


class TestSlips:
    def test_requests_ask_for_85_percent_picks_near_the_target(self):
        body = daily_slips.request(10, daily_slips.ATTEMPTS[0])
        assert body["min_prob"] == 0.85 and body["min_odds"] < 10 < body["max_odds"]
        assert body["bookable_only"] is True and body["days"] == 1

    def test_slip_and_status(self):
        s = daily_slips.slip(10, result(10.2, [("A", "B", "2026-09-27", "1x2", "1")]), daily_slips.ATTEMPTS[0])
        assert (s["status"], s["win_chance"], s["picks"][0]["status"]) == ("pending", 0.16, "pending")
        none = daily_slips.slip(20, {"error": "Not enough matches"}, daily_slips.ATTEMPTS[-1])
        assert none["status"] == "none" and none["error"] == "Not enough matches"
        assert daily_slips.status([{"status": "won"}, {"status": "lost"}]) == "lost"
        assert daily_slips.status([{"status": "won"}, {"status": "void"}]) == "won"
        assert daily_slips.status([{"status": "won"}, {"status": "pending"}]) == "pending"


@pytest.fixture
def api(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    monkeypatch.setattr(main, "_paywall_enabled", lambda: False)
    monkeypatch.setattr(main, "_features_cache", [0.0, None])
    return fake


def test_built_once_graded_and_recorded(api, monkeypatch):
    today = date.today().isoformat()
    monkeypatch.setattr(main, "_predictions_cache", [pred(d=today)])
    calls = []

    async def optimize(body):
        calls.append(body)
        if body["target_odds"] == 20:
            return {"error": "Not enough 85% picks for 20x"}
        return result(body["target_odds"], [("Arsenal", "Chelsea", today, "goals_ou", "O15")])
    monkeypatch.setattr(main, "_optimize_request", optimize)

    c = TestClient(main.app)
    got = c.get("/api/daily-slips").json()
    ten, fifteen, twenty = got["slips"]
    assert (ten["target"], ten["status"], ten["win_chance"]) == (10, "pending", 0.16)
    assert twenty["status"] == "none" and len([b for b in calls if b["target_odds"] == 20]) == len(daily_slips.ATTEMPTS)
    # Kept: asking again doesn't rebuild
    n = len(calls)
    c.get("/api/daily-slips")
    assert len(calls) == n

    # The match finishes 2-1: over 1.5 comes in, both slips win, counted once
    day = {}
    matchday.merge_predictions(day, [pred(d=today, t="00:00")], datetime.now(timezone.utc) - timedelta(days=1))
    matchday.apply_result(day["arsenal|chelsea"], finished(2, 1))
    main._md_save(api, today, day)
    got = c.get("/api/daily-slips").json()
    assert [s["status"] for s in got["slips"]] == ["won", "won", "none"]
    assert got["slips"][0]["picks"][0]["live"]["score"] == [2, 1]
    c.get("/api/daily-slips")
    assert c.get("/api/daily-slips").json()["record"] == {"10": {"won": 1, "lost": 0}, "15": {"won": 1, "lost": 0}}
    saved = json.loads(api.kv[f"betiq:daily:{today}"])
    assert "live" not in saved["slips"][0]["picks"][0]          # live scores are never stored


def test_past_days_and_premium_only(api, monkeypatch):
    import auth
    c = TestClient(main.app)
    assert c.get("/api/daily-slips?date=2026-01-01").json()["slips"] == []
    assert c.get("/api/daily-slips?date=nope").status_code == 400
    monkeypatch.setattr(main, "_paywall_enabled", lambda: True)
    monkeypatch.setattr(auth, "premium_enforced", lambda: True)

    async def identity(request):
        return None, "u1"

    async def lite(uid):
        return "lite"
    monkeypatch.setattr(main, "_admin_identity", identity)
    monkeypatch.setattr(auth, "user_tier", lite)
    r = c.get("/api/daily-slips?date=2026-01-01")
    assert (r.status_code, r.json()["detail"]) == (402, "premium_required")
