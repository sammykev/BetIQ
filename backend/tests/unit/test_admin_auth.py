"""
Admin access: the X-Admin-Secret header or a Clerk session of a user in
ADMIN_USER_IDS. The secret is never accepted in a URL or a request body.
"""

import pytest
from fastapi.testclient import TestClient

import auth
import main

client = TestClient(main.app)

ADMIN_GETS = ["/api/admin/data-status", "/api/admin/stats", "/api/admin/revenue", "/api/admin/popular",
              "/api/admin/model-metrics", "/api/admin/model-status"]
ADMIN_POSTS = [("/api/admin/banner", {"text": "hi"}), ("/api/config/maintenance", {"enabled": False}),
               ("/api/admin/clear-cache", {}), ("/api/admin/featured", {"keys": []}),
               ("/api/config/paywall", {"enabled": False})]


@pytest.fixture(autouse=True)
def config(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_SECRET", "s3cret")
    monkeypatch.setattr(main, "ADMIN_USER_IDS", {"user_admin"})
    monkeypatch.setattr(main, "_get_redis", lambda: None)

    async def fake_user(request):  # stands in for Clerk token verification
        return {"Bearer admin-token": "user_admin", "Bearer fan-token": "user_fan"}.get(
            request.headers.get("authorization", ""))
    monkeypatch.setattr(auth, "optional_user", fake_user)


@pytest.mark.parametrize("path", ADMIN_GETS)
def test_secret_in_the_url_no_longer_works(path):
    assert client.get(path, params={"secret": "s3cret"}).status_code == 403


@pytest.mark.parametrize("path,body", ADMIN_POSTS)
def test_secret_in_the_body_no_longer_works(path, body):
    assert client.post(path, json={**body, "secret": "s3cret"}).status_code == 403


@pytest.mark.parametrize("path", ADMIN_GETS)
@pytest.mark.parametrize("headers,ok", [
    ({"X-Admin-Secret": "s3cret"}, True),
    ({"Authorization": "Bearer admin-token"}, True),
    ({"X-Admin-Secret": "wrong"}, False),
    ({"Authorization": "Bearer fan-token"}, False),
    ({}, False),
])
def test_header_secret_or_admin_clerk_session(path, headers, ok):
    assert (client.get(path, headers=headers).status_code != 403) == ok


def test_empty_secret_configuration_admits_nobody(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_SECRET", "")
    assert client.get("/api/admin/stats", headers={"X-Admin-Secret": ""}).status_code == 403
    r = client.post("/api/admin/upload/tennis-csv", headers={"X-Admin-Secret": ""})
    assert r.status_code == 403


@pytest.mark.parametrize("headers,expected", [
    ({"X-Admin-Secret": "s3cret"}, {"admin": True, "via": "secret", "user_id": None}),
    ({"Authorization": "Bearer admin-token"}, {"admin": True, "via": "clerk", "user_id": "user_admin"}),
    ({"Authorization": "Bearer fan-token"}, {"admin": False, "via": None, "user_id": "user_fan"}),
    ({}, {"admin": False, "via": None, "user_id": None}),
])
def test_whoami(headers, expected):
    r = client.get("/api/admin/whoami", headers=headers).json()
    assert {k: r[k] for k in expected} == expected and r["clerk_admins_configured"] is True


def test_signing_in_no_longer_switches_the_paywall_on(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_get_redis", lambda: calls.append("redis") or None)
    client.get("/api/admin/whoami", headers={"X-Admin-Secret": "s3cret"})
    assert calls == []
