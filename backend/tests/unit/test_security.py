"""
Request-level protections (security.py) and the endpoints hardened in the
security review.
"""

import asyncio
import io
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import auth
import main
import security
from security import SlidingWindow


class FakeRedis:
    def __init__(self):
        self.kv, self.lists, self.sets = {}, {}, {}

    def get(self, k): return self.kv.get(k)
    def set(self, k, v, ex=None, nx=False):
        if nx and k in self.kv:
            return None
        self.kv[k] = str(v)
        return True
    def incr(self, k):
        self.kv[k] = str(int(self.kv.get(k, 0)) + 1)
        return int(self.kv[k])
    def lpush(self, k, v): self.lists.setdefault(k, []).insert(0, v)
    def ltrim(self, k, a, b): self.lists[k] = self.lists.get(k, [])[a:b + 1]
    def lrange(self, k, a, b): return self.lists.get(k, [])[a:b + 1]
    def sadd(self, k, v): self.sets.setdefault(k, set()).add(v)
    def srem(self, k, v): self.sets.get(k, set()).discard(v)
    def scard(self, k): return len(self.sets.get(k, set()))
    def zincrby(self, k, n, m): self.kv.setdefault(k, {}); self.kv[k][m] = self.kv[k].get(m, 0) + n


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    return fake


@pytest.fixture
def client():
    return TestClient(main.app)


class TestSlidingWindow:
    def test_allows_up_to_the_limit_then_refuses_until_the_window_passes(self):
        w = SlidingWindow()
        assert all(w.hit("a", 3, 60, now=t)[0] for t in (0, 1, 2))
        ok, retry = w.hit("a", 3, 60, now=3)
        assert not ok and 55 <= retry <= 61
        assert w.hit("a", 3, 60, now=61)[0]
        assert w.hit("b", 3, 60, now=3)[0]  # per key


class TestClientIp:
    def _req(self, headers):
        from starlette.requests import Request
        return Request({"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
                        "client": ("10.0.0.1", 1)})

    def test_proxy_headers_and_spoofing(self):
        assert security.client_ip(self._req({"cf-connecting-ip": "102.89.1.2"})) == "102.89.1.2"
        # A client-sent X-Forwarded-For is prepended to; the proxy's entry is last
        assert security.client_ip(self._req({"x-forwarded-for": "1.1.1.1, 102.89.1.2"})) == "102.89.1.2"
        assert security.client_ip(self._req({"x-forwarded-for": "not-an-ip"})) == "10.0.0.1"
        assert security.mask_ip("102.89.1.2") == "102.89.x.x"


class TestRateLimits:
    def test_optimizer_is_limited_per_ip(self, client, monkeypatch):
        monkeypatch.setattr(main, "_predictions_cache", [])
        codes = [client.post("/api/optimizer", json={"min_odds": 2, "max_odds": 3}).status_code for _ in range(22)]
        assert codes[:20] == [200] * 20 and codes[-1] == 429

    def test_large_bodies_are_refused(self, client):
        r = client.post("/api/optimizer", content=b"x" * (security.MAX_BODY_BYTES + 1),
                        headers={"Content-Type": "application/json"})
        assert r.status_code == 413

    def test_security_headers(self, client):
        r = client.get("/api/sportybet/status")
        assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"


class TestAdminLockout:
    def test_wrong_secrets_lock_the_ip_out(self, client, redis, monkeypatch):
        monkeypatch.setattr(main, "ADMIN_SECRET", "right-secret")
        for _ in range(security.ADMIN_MAX_FAILURES):
            assert client.get("/api/admin/stats", headers={"X-Admin-Secret": "guess"}).status_code == 403
        # Locked: even the right secret is refused for now
        assert client.get("/api/admin/stats", headers={"X-Admin-Secret": "right-secret"}).status_code == 429
        types = [json.loads(e)["type"] for e in redis.lists[security.EVENTS_KEY]]
        assert "admin_lockout" in types and "admin_bad_secret" in types


class TestLockedDownEndpoints:
    @pytest.mark.parametrize("path", ["/api/debug/pipeline", "/api/debug/odds", "/api/debug/calendar-status",
                                      "/api/debug/odds-sample"])
    def test_debug_endpoints_need_admin(self, client, path):
        assert client.get(path).status_code == 403

    def test_results_feedback_needs_admin(self, client):
        assert client.post("/api/feedback/result", json={"home": "A", "away": "B", "date": "2026-09-20",
                                                         "result": "H"}).status_code == 403

    def test_public_refresh_is_throttled(self, client, monkeypatch):
        started = []
        monkeypatch.setattr(main, "_run_pipeline", lambda: started.append(1))
        monkeypatch.setattr(main, "_last_public_refresh", 0.0)
        monkeypatch.setattr(main, "_is_training", False)
        assert client.post("/api/refresh").json()["started"] is True
        assert client.post("/api/refresh").json()["started"] is False
        assert len(started) == 1

    def test_unknown_sport_is_refused(self, client):
        assert client.get("/api/sports/../../etc/event?home=a&away=b&date=2026-01-01").status_code == 404
        assert client.get("/api/sports/cricket").status_code == 404


class TestPushSubscriptions:
    GOOD = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "k", "auth": "a"}}

    def test_only_real_push_services(self, client, redis):
        assert client.post("/api/push/subscribe", json={"subscription": self.GOOD}).json()["ok"]
        for endpoint in ("http://fcm.googleapis.com/x", "https://169.254.169.254/latest",
                         "https://evil.example.com/fcm.googleapis.com", "https://localhost/x"):
            sub = {**self.GOOD, "endpoint": endpoint}
            assert not client.post("/api/push/subscribe", json={"subscription": sub}).json()["ok"], endpoint
        assert redis.scard(main.PUSH_SUBS_KEY) == 1


class TestReferral:
    def test_counts_once_per_user_and_never_their_own(self, client, redis, monkeypatch):
        monkeypatch.setattr(auth, "CLERK_ISSUER", "")  # legacy uid mode for the test
        use = lambda uid, code: client.post("/api/referral/use", json={"uid": uid, "code": code})
        use("user_aaaaaaaa11111111", "ref_22222222")
        use("user_aaaaaaaa11111111", "ref_22222222")          # same user again
        use("user_bbbbbbbb22222222", "ref_22222222")          # their own code
        use("user_cccccccc33333333", "ref_22222222; DROP")    # malformed
        assert redis.get("betiq:referral:ref_22222222:count") == "1"


class TestTracking:
    def test_only_real_fixtures_are_counted(self, client, redis, monkeypatch):
        monkeypatch.setattr(main, "_predictions_cache", [{"home": "Arsenal", "away": "Chelsea"}])
        client.post("/api/track/match", json={"home": "Arsenal", "away": "Chelsea"})
        client.post("/api/track/match", json={"home": "x" * 5000, "away": "junk"})
        assert redis.kv["betiq:stats:matches"] == {"Arsenal vs Chelsea": 1}


class TestUploads:
    def test_tennis_upload_keeps_files_in_their_folder(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(main, "ADMIN_SECRET", "s3cret")
        monkeypatch.setattr(main, "DATA_DIR", str(tmp_path))
        r = client.post("/api/admin/upload/tennis-csv", headers={"X-Admin-Secret": "s3cret"},
                        files={"file": ("../../main.py", io.BytesIO(b"x"), "text/csv")})
        assert r.status_code == 400
        assert not (tmp_path.parent / "main.py").exists()


class TestPremium:
    def _req(self):
        from starlette.requests import Request
        return Request({"type": "http", "headers": [(b"authorization", b"Bearer t")], "client": ("1.2.3.4", 1)})

    def test_metadata(self):
        assert auth.is_premium_metadata({"subscription": "premium", "subscription_expires": "2999-01-01T00:00:00Z"})
        assert not auth.is_premium_metadata({"subscription": "premium", "subscription_expires": "2000-01-01T00:00:00Z"})
        assert not auth.is_premium_metadata({"subscription": "free"})
        assert not auth.is_premium_metadata(None)

    def test_require_premium(self, monkeypatch):
        monkeypatch.setattr(main, "_paywall_enabled", lambda: True)
        monkeypatch.setattr(auth, "premium_enforced", lambda: True)

        async def identity(request):
            return None, "user_1"
        monkeypatch.setattr(main, "_admin_identity", identity)

        async def free(uid): return False
        async def paid(uid): return True
        monkeypatch.setattr(auth, "user_is_premium", free)
        with pytest.raises(HTTPException) as e:
            asyncio.run(main.require_premium(self._req()))
        assert e.value.status_code == 402
        monkeypatch.setattr(auth, "user_is_premium", paid)
        assert asyncio.run(main.require_premium(self._req())) == "user_1"
        # Paywall off: everyone
        monkeypatch.setattr(main, "_paywall_enabled", lambda: False)
        monkeypatch.setattr(auth, "user_is_premium", free)
        assert asyncio.run(main.require_premium(self._req())) is None

    def test_signed_out_needs_sign_in(self, monkeypatch):
        monkeypatch.setattr(main, "_paywall_enabled", lambda: True)
        monkeypatch.setattr(auth, "premium_enforced", lambda: True)

        async def identity(request):
            return None, None
        monkeypatch.setattr(main, "_admin_identity", identity)
        with pytest.raises(HTTPException) as e:
            asyncio.run(main.require_premium(self._req()))
        assert e.value.status_code == 401

    @pytest.mark.parametrize("path, body", [("/api/optimizer", {"min_odds": 2, "max_odds": 5}),
                                            ("/api/optimizer/code", {"code": "ABC123"})])
    def test_optimizer_is_premium(self, monkeypatch, path, body):
        monkeypatch.setattr(main, "_paywall_enabled", lambda: True)
        monkeypatch.setattr(auth, "premium_enforced", lambda: True)

        async def identity(request):
            return None, None
        monkeypatch.setattr(main, "_admin_identity", identity)
        assert TestClient(main.app).post(path, json=body).status_code == 401

        async def signed_in(request):
            return None, "user_1"
        async def free(uid): return False
        monkeypatch.setattr(main, "_admin_identity", signed_in)
        monkeypatch.setattr(auth, "user_is_premium", free)
        r = TestClient(main.app).post(path, json=body)
        assert (r.status_code, r.json()["detail"]) == (402, "premium_required")


class TestCors:
    def test_other_sites_are_not_allowed(self, client):
        r = client.get("/api/sportybet/status", headers={"Origin": "https://evil.example.com"})
        assert "access-control-allow-origin" not in r.headers
        r = client.get("/api/sportybet/status", headers={"Origin": "https://predict-withbetiq.vercel.app"})
        assert r.headers["access-control-allow-origin"] == "https://predict-withbetiq.vercel.app"
