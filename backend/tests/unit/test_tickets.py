"""
Booking codes as tickets (tickets.py): every market settled from the final
score and stats, ticket status, and recording from /api/booking/convert.
"""

import json

import pytest
from fastapi.testclient import TestClient

import main
import sportybet
import tickets
from tests.unit.test_user_endpoints import FakeRedis

FT = lambda hg, ag, **kw: {"status": "finished", "hg": hg, "ag": ag, **kw}


@pytest.mark.parametrize("market,code,result,expected", [
    ("1x2", "1", FT(2, 1), "won"), ("1x2", "X", FT(2, 1), "lost"), ("1x2", "2", FT(0, 1), "won"),
    ("double_chance", "1X", FT(1, 1), "won"), ("double_chance", "12", FT(1, 1), "lost"),
    ("goals_ou", "O25", FT(2, 1), "won"), ("goals_ou", "U15", FT(1, 1), "lost"),
    ("home_goals_ou", "O15", FT(2, 0), "won"), ("away_goals_ou", "U05", FT(2, 1), "lost"),
    ("btts", "BTTS-Y", FT(1, 1), "won"), ("btts", "BTTS-N", FT(1, 1), "lost"),
    ("clean_sheet", "CS-H", FT(1, 0), "won"), ("clean_sheet", "CS-A", FT(1, 0), "lost"),
    ("win_to_nil", "WTN-H", FT(2, 0), "won"), ("win_to_nil", "WTN-A", FT(0, 0), "lost"),
    ("handicap", "H-1.5", FT(2, 0), "won"), ("handicap", "H-1.5", FT(1, 0), "lost"),
    ("handicap", "A+1.5", FT(1, 0), "won"), ("handicap", "A+2.5", FT(3, 0), "lost"),
    ("dc_goals", "1X&O15", FT(1, 1), "won"), ("dc_goals", "X2&U25", FT(0, 3), "lost"),
    ("corners_ou", "O95", FT(1, 0, corners=[6, 5]), "won"), ("corners_ou", "O95", FT(1, 0), "pending"),
    ("cards_ou", "U45", FT(1, 0, bookings=[2, 1]), "won"),
    ("home_corners_ou", "O45", FT(0, 0, corners=[4, 9]), "lost"),
    ("away_corners_ou", "O45", FT(0, 0, corners=[4, 9]), "won"),
    ("corners_1x2", "CR-X", FT(0, 0, corners=[5, 5]), "won"), ("corners_1x2", "CR-1", FT(0, 0, corners=[5, 6]), "lost"),
    ("1x2", "1", {"status": "postponed"}, "void"), ("1x2", "1", {"status": "live", "hg": 1, "ag": 0}, "pending"),
    ("1x2", "1", FT(2, 1, aet=True), "pending"), ("1x2", "1", None, "pending"),
    ("sportybet", "123", FT(1, 0), "pending"),
])
def test_grade_leg(market, code, result, expected):
    assert tickets.grade_leg(market, code, result) == expected


class TestStatus:
    @pytest.mark.parametrize("legs,status", [
        (["won", "won"], "won"), (["won", "lost"], "lost"), (["won", "pending"], "pending"),
        (["won", "void"], "won"), (["void"], "void"), (["won", "unknown"], "open"), (["lost", "unknown"], "lost"),
    ])
    def test_ticket_status(self, legs, status):
        assert tickets.ticket_status([{"status": s} for s in legs]) == status

    def test_only_booked_legs_count(self):
        sels = [{"home": "A", "away": "B", "date": "2026-09-26", "market": "1x2", "code": "1"},
                {"home": "C", "away": "D", "date": "2026-09-26", "market": "btts", "code": "BTTS-Y"},
                {"home": "E", "away": "F", "date": "2026-09-26", "market": "sportybet", "code": "9"}]
        picks = [{"status": "booked", "odds": 1.5}, {"status": "unavailable"}, {"status": "booked", "odds": 2.0}]
        t = tickets.new_ticket("C1", sels, picks, "slip", "u", 3.0, "now")
        assert [(l["home"], l["odds"], l["status"]) for l in t["legs"]] == [("A", 1.5, "pending"), ("E", 2.0, "unknown")]

    def test_summary(self):
        s = tickets.summary([{"status": "won", "legs": [{"status": "won"}]},
                             {"status": "lost", "legs": [{"status": "won"}, {"status": "lost"}]},
                             {"status": "open", "legs": []}])
        assert (s["won"], s["lost"], s["pending"], s["hit_rate"], s["leg_hit_rate"]) == (1, 1, 1, 0.5, 0.667)


class TestRecording:
    @pytest.fixture
    def redis(self, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr(main, "_get_redis", lambda: fake)
        return fake

    def book(self, monkeypatch, body):
        async def fetch(date): return [{"eventId": "sr:match:1", "homeTeamName": "Arsenal", "awayTeamName": "Chelsea",
                                        "estimateStartTime": 0, "markets": []}]
        async def share(s): return {"code": "ABC123", "url": "u", "odds": {}, "unavailable": set()}
        monkeypatch.setattr(sportybet, "fetch_events_for_date", fetch)
        monkeypatch.setattr(sportybet, "share_selections", share)
        monkeypatch.setattr(main, "_linked_event", lambda s: None)
        return TestClient(main.app).post("/api/booking/convert", json={
            "platform": "sportybet", "selections": [{"home": "Arsenal", "away": "Chelsea", "date": "2026-09-26",
                                                     "market": "1x2", "code": "1", "label": "Arsenal Win"}], **body})

    def test_signed_in_codes_become_tickets(self, redis, monkeypatch):
        r = self.book(monkeypatch, {"uid": "u1", "source": "optimizer"})
        assert r.json()["code"] == "ABC123" and r.json()["tracked"] is True
        [t] = json.loads(redis.kv["betiq:user:u1:tickets"])
        assert (t["code"], t["source"], t["legs"][0]["market"]) == ("ABC123", "optimizer", "1x2")
        assert "u1" in redis.smembers(main.TICKETS_OPEN_KEY)
        got = TestClient(main.app).get("/api/user/tickets?uid=u1").json()
        assert got["tickets"][0]["code"] == "ABC123" and got["summary"]["tickets"] == 1
        # Booking the same code again doesn't duplicate it
        self.book(monkeypatch, {"uid": "u1"})
        assert len(json.loads(redis.kv["betiq:user:u1:tickets"])) == 1

    def test_anonymous_codes_are_not_tracked(self, redis, monkeypatch):
        r = self.book(monkeypatch, {})
        assert r.json()["code"] == "ABC123" and "tracked" not in r.json()
        assert not any(k.endswith(":tickets") for k in redis.kv)

    def test_admin_stats(self, redis, monkeypatch):
        monkeypatch.setattr(main, "ADMIN_SECRET", "s3cret")
        self.book(monkeypatch, {"uid": "u1", "source": "code_check"})
        got = TestClient(main.app).get("/api/admin/tickets", headers={"X-Admin-Secret": "s3cret"}).json()
        assert got["created"] == 1 and got["sources"] == {"code_check": 1} and got["daily"][-1]["codes"] == 1
