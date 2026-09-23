"""
User-data endpoints: saves (incl. delete), bets, codes, stats, leaderboard —
with auth enforced, requests act only as the verified user.

Runs the FastAPI app against an in-memory Redis stand-in; startup events
(model training, fixture fetching) don't run because the client isn't used
as a context manager.
"""

import pytest
from fastapi.testclient import TestClient

import auth
import main
from tests.unit.test_auth import ISSUER, _StubJWKS, token


class FakeRedis:
    def __init__(self):
        self.kv, self.z = {}, {}

    def get(self, k): return self.kv.get(k)
    def set(self, k, v, ex=None): self.kv[k] = v
    def incr(self, k): self.kv[k] = str(int(self.kv.get(k) or 0) + 1)
    def zincrby(self, name, amount, member):
        zs = self.z.setdefault(name, {})
        zs[member] = zs.get(member, 0) + amount
    def zrevrange(self, name, start, end, withscores=False):
        items = sorted(self.z.get(name, {}).items(), key=lambda kv: -kv[1])[start:end + 1]
        return items if withscores else [m for m, _ in items]


PRED = {"home": "Arsenal", "away": "Chelsea", "date": "2026-09-27", "tip_1x2": "Arsenal Win"}


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


@pytest.fixture
def client():
    return TestClient(main.app)


def as_user(sub):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


class TestAuthEnforced:
    def test_reading_saves_needs_a_session(self, client, redis, enforced):
        assert client.get("/api/user/saves?uid=user_alice").status_code == 401

    def test_cannot_read_someone_elses_data(self, client, redis, enforced):
        # The old attack: pass the victim's uid. Now the token decides.
        r = client.get("/api/user/bets?uid=user_bob", headers=as_user("user_alice"))
        assert r.status_code == 403

    def test_cannot_write_someone_elses_data(self, client, redis, enforced):
        r = client.post("/api/user/saves", json={"uid": "user_bob", "prediction": PRED}, headers=as_user("user_alice"))
        assert r.status_code == 403
        assert redis.get("betiq:user:user_bob:saves") is None

    def test_uid_comes_from_the_token(self, client, redis, enforced):
        client.post("/api/user/saves", json={"prediction": PRED, "saved": True}, headers=as_user("user_alice"))
        assert client.get("/api/user/saves", headers=as_user("user_alice")).json()[0]["home"] == "Arsenal"
        assert client.get("/api/user/saves", headers=as_user("user_bob")).json() == []

    def test_every_user_endpoint_is_protected(self, client, redis, enforced):
        for path in ["/api/user/saves", "/api/user/bets", "/api/user/codes",
                     "/api/user/stats", "/api/user/prefs", "/api/referral/stats"]:
            assert client.get(f"{path}?uid=user_bob").status_code == 401, path
        for path, body in [("/api/user/bets", {"uid": "user_bob", "bet": {"result": "won"}}),
                           ("/api/user/codes", {"uid": "user_bob", "entry": {"code": "X"}}),
                           ("/api/user/prefs", {"uid": "user_bob", "digest": True})]:
            assert client.post(path, json=body).status_code == 401, path
        assert client.delete("/api/user/saves?home=A&away=B&uid=user_bob").status_code == 401


class TestSaves:
    def test_explicit_save_is_idempotent(self, client, redis, enforced):
        h = as_user("user_alice")
        for _ in range(2):
            r = client.post("/api/user/saves", json={"prediction": PRED, "saved": True}, headers=h)
            assert r.json() == {"saved": True, "count": 1}

    def test_delete_removes_and_is_idempotent(self, client, redis, enforced):
        h = as_user("user_alice")
        client.post("/api/user/saves", json={"prediction": PRED, "saved": True}, headers=h)
        q = "/api/user/saves?home=Arsenal&away=Chelsea&date=2026-09-27"
        assert client.delete(q, headers=h).json() == {"saved": False, "count": 0}
        assert client.delete(q, headers=h).json() == {"saved": False, "count": 0}
        assert client.get("/api/user/saves", headers=h).json() == []

    def test_undo_restores_a_deleted_pick(self, client, redis, enforced):
        h = as_user("user_alice")
        client.post("/api/user/saves", json={"prediction": PRED, "saved": True}, headers=h)
        client.delete("/api/user/saves?home=Arsenal&away=Chelsea&date=2026-09-27", headers=h)
        client.post("/api/user/saves", json={"prediction": PRED, "saved": True}, headers=h)
        saves = client.get("/api/user/saves", headers=h).json()
        assert [s["tip_1x2"] for s in saves] == ["Arsenal Win"]

    def test_legacy_toggle_still_works(self, client, redis, enforced):
        h = as_user("user_alice")
        assert client.post("/api/user/saves", json={"prediction": PRED}, headers=h).json()["saved"] is True
        assert client.post("/api/user/saves", json={"prediction": PRED}, headers=h).json()["saved"] is False


class TestLeaderboard:
    def test_never_exposes_user_ids(self, client, redis, enforced):
        client.post("/api/user/bets", json={"bet": {"result": "won", "stake": 100}}, headers=as_user("user_2abcdefXYZ123"))
        rows = client.get("/api/leaderboard").json()
        assert rows == [{"name": "#XYZ123", "wins": 1, "you": False}]
        assert "user_2abcdefXYZ123" not in str(rows)

    def test_marks_the_callers_row(self, client, redis, enforced):
        client.post("/api/user/bets", json={"bet": {"result": "won"}}, headers=as_user("user_alice"))
        client.post("/api/user/bets", json={"bet": {"result": "won"}}, headers=as_user("user_bob"))
        rows = client.get("/api/leaderboard", headers=as_user("user_bob")).json()
        assert {r["name"]: r["you"] for r in rows} == {"#_alice": False, "#er_bob": True}


class TestLegacyMode:
    """Before CLERK_ISSUER is configured the old uid-based calls keep working."""

    def test_uid_param_used_when_auth_not_configured(self, client, redis, monkeypatch):
        monkeypatch.setattr(auth, "CLERK_ISSUER", "")
        client.post("/api/user/saves", json={"uid": "user_alice", "prediction": PRED, "saved": True})
        assert len(client.get("/api/user/saves?uid=user_alice").json()) == 1


class TestCors:
    """The frontend is on another origin, so the browser preflights these."""

    def test_delete_with_authorization_header_is_allowed(self, client):
        r = client.options("/api/user/saves", headers={
            "Origin": "https://predict-withbetiq.vercel.app",
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "authorization",
        })
        assert r.status_code == 200
        assert "DELETE" in r.headers["access-control-allow-methods"]
        assert "authorization" in r.headers["access-control-allow-headers"].lower()
