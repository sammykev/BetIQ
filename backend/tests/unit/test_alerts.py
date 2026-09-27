"""
Admin alerts: the database missing (e.g. its setting's name broken in .env),
settings with stray characters around their names, and live scores stalling.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
from tests.unit.test_user_endpoints import FakeRedis


@pytest.fixture
def admin(monkeypatch):
    async def identity(request):
        return "secret", None
    monkeypatch.setattr(main, "_admin_identity", identity)
    monkeypatch.setattr(main, "_STARTED_AT", 0.0)      # up for a long time
    monkeypatch.setitem(main._md_status, "at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    monkeypatch.setitem(main._md_status, "error", None)
    return TestClient(main.app)


def test_a_broken_setting_name_is_named(monkeypatch, admin):
    monkeypatch.setattr(main, "REDIS_URL", "")
    monkeypatch.setattr(main, "_redis", None)
    monkeypatch.delenv("UPSTASH_REDIS_URL", raising=False)
    monkeypatch.setenv("4145r1546UPSTASH_REDIS_URL", "rediss://x")
    [alert] = admin.get("/api/admin/alerts").json()["alerts"]
    assert alert["level"] == "danger" and alert["title"] == "Database not connected"
    assert '"4145r1546UPSTASH_REDIS_URL"' in alert["detail"] and "data itself is safe" in alert["detail"]
    assert ("4145r1546UPSTASH_REDIS_URL", "UPSTASH_REDIS_URL") in main._mangled_settings()


def test_missing_setting(monkeypatch, admin):
    monkeypatch.setattr(main, "REDIS_URL", "")
    monkeypatch.setattr(main, "_redis", None)
    [alert] = admin.get("/api/admin/alerts").json()["alerts"]
    assert "isn't set in the server's .env" in alert["detail"]


def test_all_well_and_other_broken_names(monkeypatch, admin):
    fake = FakeRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    assert admin.get("/api/admin/alerts").json()["alerts"] == []
    monkeypatch.delenv("APIFOOTBALL_KEY", raising=False)
    monkeypatch.setenv("xAPIFOOTBALL_KEY", "k")
    [alert] = admin.get("/api/admin/alerts").json()["alerts"]
    assert alert["title"] == "APIFOOTBALL_KEY isn't set" and '"xAPIFOOTBALL_KEY"' in alert["detail"]


def test_stalled_live_scores(monkeypatch, admin):
    monkeypatch.setattr(main, "_get_redis", lambda: FakeRedis())
    old = (datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat(timespec="seconds")
    monkeypatch.setitem(main._md_status, "at", old)
    monkeypatch.setitem(main._md_status, "error", "timed out at asking ESPN (4 scoreboards)")
    [alert] = admin.get("/api/admin/alerts").json()["alerts"]
    assert alert["title"] == "Live scores aren't updating" and "timed out at asking ESPN" in alert["detail"]


def test_failing_database_is_retried_every_30s_not_every_request(monkeypatch):
    import redis as redis_lib
    calls = []

    def broken(url, **kw):
        calls.append(url)
        raise ConnectionError("refused")
    monkeypatch.setattr(main, "REDIS_URL", "redis://example:6379")
    monkeypatch.setattr(main, "_redis", None)
    monkeypatch.setitem(main._redis_problem, "retry_at", 0.0)
    monkeypatch.setattr(redis_lib, "from_url", broken)
    assert main._get_redis() is None and main._get_redis() is None
    assert len(calls) == 1 and "refused" in main._redis_missing_reason()


def test_admins_only():
    assert TestClient(main.app).get("/api/admin/alerts").status_code in (401, 403)
