"""
Free trial settings (main /api/trial, /api/admin/trial): the site starts a
new account's trial from these; switching it on dates it, so older accounts
don't get one.
"""

import json

import pytest
from fastapi.testclient import TestClient

import auth
import main
from tests.unit.test_user_endpoints import FakeRedis


@pytest.fixture
def admin(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)

    async def identity(request):
        return "secret", None
    monkeypatch.setattr(main, "_admin_identity", identity)
    return TestClient(main.app), fake


def test_off_by_default(admin):
    c, _ = admin
    assert c.get("/api/trial").json() == {"enabled": False, "days": 7, "tier": "premium", "since": None}


def test_switching_on_dates_it_and_keeps_the_date(admin):
    c, fake = admin
    on = c.put("/api/admin/trial", json={"enabled": True}).json()
    assert on["enabled"] and on["since"] and on["days"] == 7
    again = c.put("/api/admin/trial", json={"days": 14, "tier": "lite", "enabled": True}).json()
    assert (again["since"], again["days"], again["tier"]) == (on["since"], 14, "lite")
    assert json.loads(fake.kv[main.TRIAL_KEY])["days"] == 14
    assert c.get("/api/trial").json() == again


@pytest.mark.parametrize("body", [{"days": 0}, {"days": 31}, {"days": "x"}, {"tier": "gold"}])
def test_rejects_bad_settings(admin, body):
    c, _ = admin
    assert c.put("/api/admin/trial", json=body).status_code == 400


def test_admin_only(monkeypatch):
    monkeypatch.setattr(main, "_get_redis", lambda: FakeRedis())
    assert TestClient(main.app).put("/api/admin/trial", json={"enabled": True}).status_code in (401, 403)


def test_free_is_rechecked_within_seconds(monkeypatch):
    import asyncio
    import time
    tiers = iter(["free", "premium"])

    async def clerk_user(uid):
        return 200, {"subscription": next(tiers), "subscription_expires": "2999-01-01T00:00:00Z"}
    monkeypatch.setattr(auth, "clerk_user", clerk_user)
    monkeypatch.setattr(auth, "_tier_cache", {})
    assert asyncio.run(auth.user_tier("u1")) == "free"
    auth._tier_cache["u1"] = (time.time() - auth.FREE_CACHE_SECONDS - 1, "free")
    assert asyncio.run(auth.user_tier("u1")) == "premium"
