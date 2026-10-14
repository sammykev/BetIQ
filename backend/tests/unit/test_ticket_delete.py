"""Removing a ticket the account doesn't want, and Undo putting it back."""

import json

import pytest
from fastapi.testclient import TestClient

import auth
import main
from tests.unit.test_auth import ISSUER, _StubJWKS, token
from tests.unit.test_user_endpoints import FakeRedis


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    return fake


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(auth, "CLERK_ISSUER", ISSUER)
    monkeypatch.setattr(auth, "AUTHORIZED_PARTIES", [])
    monkeypatch.setattr(auth, "_jwks_client", _StubJWKS())


def as_user(sub):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def ticket(code, status="pending"):
    return {"code": code, "status": status, "created_at": "2026-10-10T10:00:00", "source": "slip",
            "legs": [{"home": "A", "away": "B", "date": "2026-10-10", "market": "1x2", "code": "1", "status": status}]}


def stored(redis, uid, kind):
    raw = redis.kv.get(f"betiq:user:{uid}:{kind}")
    return [t["code"] for t in json.loads(raw)] if raw else []


def test_delete_then_undo_puts_it_back_where_it_was(redis, enforced):
    uid = "user_alice"
    redis.set(f"betiq:user:{uid}:tickets", json.dumps([ticket("AAA"), ticket("BBB", "lost"), ticket("CCC")]))
    c = TestClient(main.app)
    r = c.delete("/api/user/tickets?code=BBB", headers=as_user(uid))
    assert r.json() == {"deleted": True}
    assert stored(redis, uid, "tickets") == ["AAA", "CCC"]
    assert [t["code"] for t in c.get("/api/user/tickets", headers=as_user(uid)).json()["tickets"]] == ["AAA", "CCC"]

    r = c.post("/api/user/tickets/restore", json={"code": "BBB"}, headers=as_user(uid))
    assert r.json() == {"restored": True}
    assert stored(redis, uid, "tickets") == ["AAA", "BBB", "CCC"]
    assert stored(redis, uid, "tickets_trash") == []
    # A second undo has nothing left to restore
    assert c.post("/api/user/tickets/restore", json={"code": "BBB"}, headers=as_user(uid)).json() == {"restored": False}


def test_older_saved_codes_can_be_removed_too(redis, enforced):
    uid = "user_alice"
    redis.set(f"betiq:user:{uid}:codes", json.dumps([{"code": "OLD1", "date": "2026-01-01"}]))
    c = TestClient(main.app)
    assert c.delete("/api/user/tickets?code=OLD1", headers=as_user(uid)).json() == {"deleted": True}
    assert c.get("/api/user/tickets", headers=as_user(uid)).json()["older"] == []
    c.post("/api/user/tickets/restore", json={"code": "OLD1"}, headers=as_user(uid))
    assert stored(redis, uid, "codes") == ["OLD1"]


def test_missing_code_is_a_no_op(redis, enforced):
    c = TestClient(main.app)
    assert c.delete("/api/user/tickets?code=NOPE", headers=as_user("user_alice")).json() == {"deleted": False}
    assert "betiq:user:user_alice:tickets_trash" not in redis.kv


def test_only_the_owner_can_delete_or_restore(redis, enforced):
    redis.set("betiq:user:user_bob:tickets", json.dumps([ticket("BOB1")]))
    c = TestClient(main.app)
    assert c.delete("/api/user/tickets?code=BOB1").status_code == 401
    assert c.delete("/api/user/tickets?code=BOB1&uid=user_bob", headers=as_user("user_alice")).status_code == 403
    # Alice's own (empty) list is what her token acts on
    assert c.delete("/api/user/tickets?code=BOB1", headers=as_user("user_alice")).json() == {"deleted": False}
    assert c.post("/api/user/tickets/restore", json={"code": "BOB1", "uid": "user_bob"},
                  headers=as_user("user_alice")).status_code == 403
    assert stored(redis, "user_bob", "tickets") == ["BOB1"]


def test_restore_only_takes_back_what_was_removed(redis, enforced):
    """The client sends only a code: it can't plant a made-up ticket."""
    c = TestClient(main.app)
    r = c.post("/api/user/tickets/restore", json={"code": "FAKE", "ticket": ticket("FAKE", "won")},
               headers=as_user("user_alice"))
    assert r.json() == {"restored": False}
    assert stored(redis, "user_alice", "tickets") == []
