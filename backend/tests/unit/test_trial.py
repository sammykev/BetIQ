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
    assert c.get("/api/trial").json() == {"enabled": False, "days": 7, "tier": "premium", "since": None,
                                          "require_phone": False, "per_ip_week": 3}


def test_switching_on_dates_it_and_keeps_the_date(admin):
    c, fake = admin
    on = c.put("/api/admin/trial", json={"enabled": True}).json()
    assert on["enabled"] and on["since"] and on["days"] == 7
    again = c.put("/api/admin/trial", json={"days": 14, "tier": "lite", "enabled": True}).json()
    assert (again["since"], again["days"], again["tier"]) == (on["since"], 14, "lite")
    assert json.loads(fake.kv[main.TRIAL_KEY])["days"] == 14
    assert c.get("/api/trial").json() == again


@pytest.mark.parametrize("body", [{"days": 0}, {"days": 31}, {"days": "x"}, {"tier": "gold"}, {"per_ip_week": 0}])
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


# ── Who gets one (trial.check) and the start endpoint ──
from datetime import datetime, timedelta, timezone

import trial

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
CFG = {**trial.DEFAULTS, "enabled": True, "since": "2026-09-20T00:00:00+00:00"}


def clerk(uid="user_new", email="Ok.Nkwocha+bets@Gmail.com", created=NOW - timedelta(days=1), meta=None,
          email_status="verified", phones=()):
    return {"id": uid, "created_at": int(created.timestamp() * 1000), "public_metadata": meta or {},
            "primary_email_address_id": "e1",
            "email_addresses": [{"id": "e1", "email_address": email, "verification": {"status": email_status}}],
            "phone_numbers": [{"phone_number": p, "verification": {"status": "verified"}} for p in phones]}


def check(user, cfg=CFG, device="device-123456", seen=None, ip=0):
    seen = seen or {}
    return trial.check(user, cfg, device, lambda kind, d: seen.get((kind, d)), ip, NOW)


class TestCheck:
    def test_a_new_account_gets_it_until_sign_up_plus_days(self):
        v = check(clerk())
        assert v["expires"] == NOW - timedelta(days=1) + timedelta(days=7)
        assert set(v["ids"]) == {"email", "device"}

    def test_gmail_dots_and_tags_are_the_same_mailbox(self):
        assert trial.canonical_email("Ok.Nkwocha+bets@Gmail.com") == "oknkwocha@gmail.com"
        assert trial.canonical_email("o.k.nkwocha@googlemail.com") == "oknkwocha@gmail.com"
        assert trial.canonical_email("a.b+x@yahoo.com") == "a.b@yahoo.com"
        first = check(clerk())["ids"]["email"]
        seen = {("email", first): "user_old"}
        assert check(clerk(email="oknkwocha@gmail.com"), seen=seen) == {"reason": "email_used"}

    def test_same_device_or_phone_or_too_many_from_one_network(self):
        ids = check(clerk(phones=["+234 801 234 5678"]))["ids"]
        assert check(clerk(email="new@yahoo.com"), seen={("device", ids["device"]): "user_old"}) == {"reason": "device_used"}
        assert check(clerk(email="new@yahoo.com", phones=["2348012345678"]), device="other-device-1",
                     seen={("phone", ids["phone"]): "user_old"}) == {"reason": "phone_used"}
        assert check(clerk(), ip=3) == {"reason": "ip_limit"}
        # Its own earlier record doesn't count against it
        assert "expires" in check(clerk(), seen={("device", ids["device"]): "user_new"})

    def test_email_must_be_real_and_verified(self):
        assert check(clerk(email="x@mailinator.com")) == {"reason": "disposable_email"}
        assert check(clerk(email_status="unverified")) == {"reason": "email_unverified"}

    def test_phone_when_required(self):
        cfg = {**CFG, "require_phone": True}
        assert check(clerk(), cfg=cfg) == {"reason": "needs_phone"}
        assert "phone" in check(clerk(phones=["+2348012345678"]), cfg=cfg)["ids"]

    def test_not_for_old_accounts_plans_or_repeat(self):
        assert check(clerk(created=NOW - timedelta(days=30))) == {"reason": "account_too_old"}
        assert check(clerk(meta={"trial_used": True})) == {"reason": "already_used"}
        assert check(clerk(meta={"subscription": "lite", "subscription_expires": "2999-01-01T00:00:00Z"})) == {"reason": "has_plan"}
        assert check(clerk(), cfg={**CFG, "enabled": False}) == {"reason": "trial_off"}
        late = {**CFG, "days": 1, "since": "2026-09-01T00:00:00+00:00"}
        assert check(clerk(created=NOW - timedelta(days=2)), cfg=late) == {"reason": "window_passed"}


@pytest.fixture
def site(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    fake.kv[main.TRIAL_KEY] = json.dumps({**CFG, "since": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()})
    monkeypatch.setattr(auth, "premium_enforced", lambda: True)
    users, writes = {}, []

    async def require_user(request, claimed_uid=""):
        return request.headers["x-test-user"]

    async def clerk_record(uid):
        return 200, users[uid]

    async def set_public_metadata(uid, values):
        writes.append((uid, values))
        users[uid]["public_metadata"].update(values)
        return 200
    monkeypatch.setattr(auth, "require_user", require_user)
    monkeypatch.setattr(auth, "clerk_record", clerk_record)
    monkeypatch.setattr(auth, "set_public_metadata", set_public_metadata)
    now = datetime.now(timezone.utc)
    users["user_a"] = clerk("user_a", "someone@gmail.com", created=now - timedelta(hours=1))
    users["user_b"] = clerk("user_b", "some.one+2@gmail.com", created=now - timedelta(hours=1))
    users["user_c"] = clerk("user_c", "fresh@yahoo.com", created=now - timedelta(hours=1))
    return TestClient(main.app), fake, writes


def start(c, uid, device="device-aaaaaa"):
    return c.post("/api/trial/start", json={"device": device}, headers={"x-test-user": uid}).json()


def test_start_records_and_refuses_a_second_account(site):
    c, fake, writes = site
    got = start(c, "user_a")
    assert got["started"] and got["tier"] == "premium" and got["days"] == 7
    assert writes[0][1]["trial"] is True and writes[0][1]["trial_used"] is True
    assert "someone" not in json.dumps(fake.kv)                        # identifiers are hashed
    # Same mailbox, other device: refused, and told why; the account stops asking
    again = start(c, "user_b", device="device-bbbbbb")
    assert (again["started"], again["reason"]) == (False, "email_used") and "already had" in again["message"]
    assert writes[-1] == ("user_b", {"trial_used": True, "trial_denied": "email_used"})
    # New mailbox, same device: refused
    assert start(c, "user_c")["reason"] == "device_used"
    assert fake.h[main.TRIAL_STATS_KEY] == {"started": 1, "refused:email_used": 1, "refused:device_used": 1}


def test_start_when_off_or_unconfigured(site, monkeypatch):
    c, fake, _ = site
    fake.kv[main.TRIAL_KEY] = json.dumps({**CFG, "enabled": False})
    assert start(c, "user_a") == {"started": False, "reason": "trial_off", "message": None}
    monkeypatch.setattr(auth, "premium_enforced", lambda: False)
    assert start(c, "user_a")["reason"] == "not_configured"
