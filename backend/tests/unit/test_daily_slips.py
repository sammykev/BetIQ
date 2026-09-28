"""
Daily odds (daily_slips.py): three slips a day from the day's 80%+ picks, none at 2.0 odds or more, built once,
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


def result(target, legs, time="00:00"):
    picks = [{"home": h, "away": a, "date": d, "time": time, "league": "PL", "market": m, "market_name": m,
              "code": c, "label": c, "prob": 0.88, "odds": 1.2, "odds_source": "sportybet", "bookable": True}
             for h, a, d, m, c in legs]
    return {"picks": picks, "total_odds": target, "win_chance": 0.16, "within_target": True}


class TestSlips:
    def test_requests_ask_for_80_percent_picks_under_2_odds_near_the_target(self):
        body = daily_slips.request(10, daily_slips.ATTEMPTS[0])
        assert body["min_prob"] == 0.80 and body["max_leg_odds"] == 2.0
        assert body["min_odds"] < 10 < body["max_odds"]
        assert body["bookable_only"] is True and body["days"] == 1

    def test_only_the_days_own_matches(self):
        assert all(a["days"] == 1 for a in daily_slips.ATTEMPTS)

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
    monkeypatch.setattr(main, "_daily_memory", {})
    return fake


@pytest.fixture
def sportybet(monkeypatch):
    """SportyBet booking: a code per call, unless told to fail."""
    import booking_slip
    calls = []

    async def to_sportybet(sels, *a, **kw):
        calls.append(sels)
        if calls and getattr(to_sportybet, "fail", False):
            return {"code": None, "error": "SportyBet didn't return a booking code.", "picks": []}
        return {"code": f"CODE{len(calls)}", "share_url": f"https://sb/{len(calls)}", "total_odds": 10.1,
                "picks": [{"status": "booked", "odds": 1.2} for _ in sels], "error": None}
    to_sportybet.fail = False
    monkeypatch.setattr(booking_slip, "to_sportybet", to_sportybet)
    return to_sportybet, calls


def test_built_once_booked_graded_and_recorded(api, monkeypatch, sportybet):
    import asyncio
    today = date.today().isoformat()
    monkeypatch.setattr(main, "_predictions_cache", [pred(d=today)])
    calls = []

    async def optimize(body):
        calls.append(body)
        if body["target_odds"] == 20:
            return {"error": "Not enough 80% picks for 20x"}
        return result(body["target_odds"], [("Arsenal", "Chelsea", today, "goals_ou", "O15")], time="23:59")
    monkeypatch.setattr(main, "_optimize_request", optimize)

    c = TestClient(main.app)
    # Visitors never make the slips: before the morning job there are none
    assert c.get("/api/daily-slips").json()["slips"] == [] and calls == []

    asyncio.run(main._build_daily(today))
    got = c.get("/api/daily-slips").json()
    ten, fifteen, twenty = got["slips"]
    assert (ten["target"], ten["status"], ten["win_chance"]) == (10, "pending", 0.16)
    assert twenty["status"] == "none" and len([b for b in calls if b["target_odds"] == 20]) == len(daily_slips.ATTEMPTS)
    # Booked by the server, one code per slip, the same for everyone
    assert ten["booking"]["code"] == "CODE1" and fifteen["booking"]["code"] == "CODE2"
    assert (ten["booking"]["booked"], ten["booking"]["of"]) == (1, 1) and "on_code" not in ten["booking"]
    assert "booking" not in twenty and len(sportybet[1]) == 2
    # Kept: asking again (or the job running again) doesn't rebuild or rebook
    n = len(calls)
    c.get("/api/daily-slips")
    asyncio.run(main._daily_job())
    asyncio.run(main._daily_tick())
    assert len(calls) == n and len(sportybet[1]) == 2

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


def test_a_slip_sportybet_refused_is_tried_again_later(api, monkeypatch, sportybet):
    import asyncio
    book, calls = sportybet
    book.fail = True
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    monkeypatch.setattr(main, "_predictions_cache", [pred(d=tomorrow)])

    async def optimize(body):
        return result(body["target_odds"], [("Arsenal", "Chelsea", tomorrow, "goals_ou", "O15")])
    monkeypatch.setattr(main, "_optimize_request", optimize)
    today = date.today().isoformat()
    asyncio.run(main._build_daily(today))
    slip = TestClient(main.app).get("/api/daily-slips").json()["slips"][0]
    assert slip["booking"]["code"] is None and "didn't return" in slip["booking"]["error"]
    # Too soon: not tried again
    asyncio.run(main._daily_tick())
    assert len(calls) == 3
    # After RETRY_MINUTES the tick books it
    book.fail = False
    doc = json.loads(api.kv[f"betiq:daily:{today}"])
    old = (datetime.now(timezone.utc) - timedelta(minutes=daily_slips.RETRY_MINUTES + 1)).isoformat(timespec="seconds")
    for s in doc["slips"]:
        s["booking"]["at"] = old
    api.kv[f"betiq:daily:{today}"] = json.dumps(doc)
    main._daily_memory.clear()
    asyncio.run(main._daily_tick())
    slips = TestClient(main.app).get("/api/daily-slips").json()["slips"]
    assert all(s["booking"]["code"] for s in slips) and len(calls) == 6


def test_missed_morning_job_is_made_up_by_the_tick(api, monkeypatch, sportybet):
    import asyncio
    monkeypatch.setattr(main, "_predictions_cache", [pred()])
    made = []

    async def build(day, force=False):
        made.append(day)
    monkeypatch.setattr(main, "_build_daily", build)
    monkeypatch.setattr(daily_slips, "due", lambda now: False)
    asyncio.run(main._daily_tick())
    assert made == []
    monkeypatch.setattr(daily_slips, "due", lambda now: True)
    asyncio.run(main._daily_tick())
    assert made == [date.today().isoformat()]


def test_booking_leaves_out_started_and_settled_picks():
    now = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)
    s = {"status": "pending", "picks": [
        {"home": "A", "away": "B", "date": "2026-09-27", "time": "14:00", "market": "1x2", "code": "1", "status": "pending"},
        {"home": "C", "away": "D", "date": "2026-09-27", "time": "18:00", "market": "1x2", "code": "2", "status": "pending"},
        {"home": "E", "away": "F", "date": "2026-09-28", "time": "12:00", "market": "btts", "code": "BTTS-Y", "status": "won"}]}
    assert [x["home"] for x in daily_slips.selections(s, now)] == ["C"]
    assert daily_slips.needs_booking(s, now)
    s["booking"] = {"code": "X1"}
    assert not daily_slips.needs_booking(s, now)


def test_an_account_can_track_the_slip(api, monkeypatch, sportybet):
    import asyncio
    import auth
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    monkeypatch.setattr(main, "_predictions_cache", [pred(d=tomorrow)])

    async def optimize(body):
        return result(body["target_odds"], [("Arsenal", "Chelsea", tomorrow, "goals_ou", "O15")])
    monkeypatch.setattr(main, "_optimize_request", optimize)
    monkeypatch.setattr(auth, "auth_enforced", lambda: False)
    today = date.today().isoformat()
    asyncio.run(main._build_daily(today))
    c = TestClient(main.app)
    r = c.post("/api/daily-slips/track", json={"date": today, "target": 10, "uid": "user_abc"}).json()
    assert r == {"tracked": True, "code": "CODE1"}
    [ticket] = json.loads(api.kv[main._ukey("user_abc", "tickets")])
    assert ticket["code"] == "CODE1" and ticket["source"] == "daily" and ticket["legs"][0]["odds"] == 1.2
    assert c.post("/api/daily-slips/track", json={"date": "2026-01-01", "target": 10, "uid": "user_abc"}).status_code == 404


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
