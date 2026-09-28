"""
Admin → every booking code made on the site (/api/admin/tickets/all):
newest first, with the account's name and email, filterable.
"""

import fnmatch
import json

import pytest
from fastapi.testclient import TestClient

import auth
import main
from tests.unit.test_user_endpoints import FakeRedis


class ScanRedis(FakeRedis):
    def scan_iter(self, match="*", count=None):
        return iter([k for k in list(self.kv) if fnmatch.fnmatch(k, match)])


def ticket(code, at, status="open", source="slip"):
    return {"code": code, "created_at": at, "source": source, "share_url": f"https://sb/{code}", "status": status,
            "total_odds": 3.2, "settled_at": None,
            "legs": [{"home": "A", "away": "B", "date": "2026-09-28", "market": "1x2", "code": "1", "label": "A to win",
                      "odds": 1.8, "status": "pending"}]}


@pytest.fixture
def admin(monkeypatch):
    fake = ScanRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    monkeypatch.setattr(main, "_all_tickets_cache", [0.0, None])
    monkeypatch.setattr(main, "_names_cache", {})
    monkeypatch.setattr(auth, "CLERK_SECRET_KEY", "sk_test")
    asked = []

    async def names(uids):
        asked.append(sorted(uids))
        return {"user_a": {"name": "Ada Obi", "email": "ada@gmail.com"}, "user_b": {"name": "", "email": "bayo@yahoo.com"}}
    monkeypatch.setattr(auth, "clerk_names", names)

    async def identity(request):
        return "secret", None
    monkeypatch.setattr(main, "_admin_identity", identity)
    fake.kv[main._ukey("user_a", "tickets")] = json.dumps([ticket("AAA1", "2026-09-27T10:00:00+00:00"),
                                                          ticket("AAA2", "2026-09-25T10:00:00+00:00", "won", "daily")])
    fake.kv[main._ukey("user_b", "tickets")] = json.dumps([ticket("BBB1", "2026-09-26T10:00:00+00:00", "lost")])
    fake.kv[main._ukey("user_b", "saves")] = json.dumps([])        # not tickets
    return TestClient(main.app), asked


def test_every_code_newest_first_with_who_made_it(admin):
    c, asked = admin
    got = c.get("/api/admin/tickets/all").json()
    assert got["total"] == 3 and [t["code"] for t in got["tickets"]] == ["AAA1", "BBB1", "AAA2"]
    first = got["tickets"][0]
    assert (first["name"], first["email"], first["uid"]) == ("Ada Obi", "ada@gmail.com", "user_a")
    assert first["legs"][0]["label"] == "A to win" and first["share_url"] == "https://sb/AAA1"
    # Names are kept: a second look doesn't ask Clerk again
    c.get("/api/admin/tickets/all")
    assert asked == [["user_a", "user_b"]]


def test_filters(admin):
    c, _ = admin
    assert [t["code"] for t in c.get("/api/admin/tickets/all?status=won").json()["tickets"]] == ["AAA2"]
    assert [t["code"] for t in c.get("/api/admin/tickets/all?source=daily").json()["tickets"]] == ["AAA2"]
    assert [t["code"] for t in c.get("/api/admin/tickets/all?q=bayo").json()["tickets"]] == ["BBB1"]
    assert [t["code"] for t in c.get("/api/admin/tickets/all?q=aaa1").json()["tickets"]] == ["AAA1"]
    assert c.get("/api/admin/tickets/all?limit=1").json()["total"] == 3


def test_admin_only(monkeypatch):
    monkeypatch.setattr(main, "_get_redis", lambda: ScanRedis())
    assert TestClient(main.app).get("/api/admin/tickets/all").status_code in (401, 403)


def test_codes_made_signed_out_are_kept_and_named(admin):
    c, _ = admin
    import tickets
    main._record_ticket(main.ANON_UID, tickets.new_ticket(
        "ANON1", [{"home": "A", "away": "B", "date": "2026-09-28", "market": "1x2", "code": "1"}],
        [{"status": "booked", "odds": 1.5}], "slip", None, 1.5, "2026-09-28T09:00:00+00:00"))
    main._all_tickets_cache[:] = [0.0, None]
    first = c.get("/api/admin/tickets/all").json()["tickets"][0]
    assert (first["code"], first["name"], first["uid"]) == ("ANON1", "Not signed in", "anonymous")
