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
        self.kv, self.z, self.sets, self.h = {}, {}, {}, {}

    def get(self, k): return self.kv.get(k)
    def mget(self, keys): return [self.kv.get(k) for k in keys]
    def scan_iter(self, match="*", count=None):
        import fnmatch
        return [k for k in list(self.kv) if fnmatch.fnmatch(k, match)]
    def set(self, k, v, ex=None): self.kv[k] = v
    def expire(self, k, s): pass
    def incr(self, k): self.kv[k] = str(int(self.kv.get(k) or 0) + 1)
    def sadd(self, k, *v): self.sets.setdefault(k, set()).update(v)
    def srem(self, k, *v): self.sets.setdefault(k, set()).difference_update(v)
    def smembers(self, k): return set(self.sets.get(k, set()))
    def scard(self, k): return len(self.sets.get(k, set()))
    def hincrby(self, k, f, n=1):
        self.h.setdefault(k, {})[f] = self.h.setdefault(k, {}).get(f, 0) + n
        return self.h[k][f]
    def hgetall(self, k): return dict(self.h.get(k, {}))
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
        r = client.get("/api/user/stats?uid=user_bob", headers=as_user("user_alice"))
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
        for path in ["/api/user/saves", "/api/user/codes", "/api/user/stats",
                     "/api/user/tickets", "/api/referral/stats"]:
            assert client.get(f"{path}?uid=user_bob").status_code == 401, path
        assert client.post("/api/user/saves", json={"uid": "user_bob", "prediction": PRED}).status_code == 401
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


class TestDeadEndpointsAreGone:
    """Self-reported bets, the old code log and prefs had no screen left."""

    def test_removed(self, client, redis, enforced):
        h = as_user("user_alice")
        for method, path in [("get", "/api/user/bets"), ("post", "/api/user/bets"), ("post", "/api/user/codes"),
                             ("get", "/api/user/prefs"), ("post", "/api/user/prefs")]:
            assert getattr(client, method)(path, headers=h).status_code in (404, 405), (method, path)


def tickets_for(redis, uid, *statuses):
    import json
    redis.set(f"betiq:user:{uid}:tickets", json.dumps(
        [{"code": f"C{i}", "status": s, "legs": [], "created_at": f"2026-09-2{i}"} for i, s in enumerate(statuses)]))


class TestLeaderboard:
    @pytest.fixture(autouse=True)
    def fresh(self, monkeypatch):
        monkeypatch.setattr(main, "_all_tickets_cache", [0.0, None])

    def test_never_exposes_user_ids(self, client, redis, enforced):
        tickets_for(redis, "user_2abcdefXYZ123", "won", "lost")
        rows = client.get("/api/leaderboard").json()
        assert rows == [{"name": "#XYZ123", "wins": 1, "you": False}]
        assert "user_2abcdefXYZ123" not in str(rows)

    def test_marks_the_callers_row(self, client, redis, enforced):
        tickets_for(redis, "user_alice", "won", "won")
        tickets_for(redis, "user_bob", "won")
        rows = client.get("/api/leaderboard", headers=as_user("user_bob")).json()
        assert rows == [{"name": "#_alice", "wins": 2, "you": False}, {"name": "#er_bob", "wins": 1, "you": True}]

    def test_only_wins_the_server_graded_count(self, client, redis, enforced):
        # Open, lost and void tickets don't count; nor do signed-out codes,
        # nor anything left in the old self-reported counter
        tickets_for(redis, "user_alice", "pending", "open", "lost", "void")
        tickets_for(redis, main.ANON_UID, "won", "won")
        redis.zincrby(main.OLD_LEADERBOARD_KEY, 50, "user_mallory")
        assert client.get("/api/leaderboard").json() == []


class TestLegacyMode:
    """Local development (ALLOW_UNVERIFIED_UID, no CLERK_ISSUER): uid-based calls work."""

    def test_uid_param_used_when_auth_not_configured(self, client, redis, monkeypatch):
        monkeypatch.setattr(auth, "CLERK_ISSUER", "")
        monkeypatch.setattr(auth, "ALLOW_UNVERIFIED_UID", True)
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
