"""
Posting the daily odds on X (x_poster.py): OAuth 1.0a signing, the post's
text and length, and posting once a day from the daily tick.
"""

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
import x_poster
from tests.unit.test_user_endpoints import FakeRedis

KEYS = {"X_API_KEY": "xvz1evFS4wEEPTGEFPHBog", "X_API_SECRET": "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
        "X_ACCESS_TOKEN": "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
        "X_ACCESS_SECRET": "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE"}


def test_signature_matches_xs_documented_example():
    # developer.x.com "Creating a signature": this request signs to hCtSmYh+iHYCEqBWrE7C7hYmtUk=
    h = x_poster.oauth_header(
        "POST", "https://api.twitter.com/1.1/statuses/update.json", KEYS,
        nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", timestamp=1318622958,
        signed={"include_entities": "true", "status": "Hello Ladies + Gentlemen, a signed OAuth request!"})
    assert 'oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D"' in h
    assert 'oauth_consumer_key="xvz1evFS4wEEPTGEFPHBog"' in h and 'oauth_version="1.0"' in h
    assert "include_entities" not in h and "status" not in h          # signed, but not sent in the header


def slip(target, odds, code, status="pending"):
    return {"target": target, "status": status, "total_odds": odds, "picks": [{"home": "A"}],
            "booking": {"code": code} if code is not None else None}


DOC = {"date": "2026-09-28", "slips": [slip(10, 10.24, "ABC123"), slip(15, 15.1, None), slip(20, 20.31, "GHI789")]}


class TestCompose:
    def test_the_post(self):
        text = x_poster.compose(DOC, "https://predict-withbetiq.vercel.app")
        assert text.splitlines()[0] == "BetIQ Daily Odds · Mon 28 Sep"
        assert "10x slip: 10.24 odds · code ABC123" in text
        assert "15x slip: 15.10 odds · code on the site" in text
        assert "https://predict-withbetiq.vercel.app/daily" in text and "18+" in text
        assert x_poster.weight(text) <= 280

    def test_nothing_to_post(self):
        assert x_poster.compose({"date": "2026-09-28", "slips": [slip(10, None, None, status="none")]}, "https://x") is None
        assert x_poster.compose({"date": "2026-09-28", "slips": [slip(10, 10.2, None)]}, "https://x") is None

    def test_weight_counts_links_as_23(self):
        assert x_poster.weight("see https://" + "a" * 100 + ".com now") == 4 + 23 + 4

    def test_errors_explain_themselves(self):
        assert "Read and write" in x_poster.error_text(403, {"detail": "Forbidden"})
        assert "X_ keys" in x_poster.error_text(401, {"title": "Unauthorized"})


@pytest.fixture
def site(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    monkeypatch.setattr(main, "_daily_memory", {})
    for k, v in KEYS.items():
        monkeypatch.setenv(k, v)
    posts = []

    async def post(text):
        posts.append(text)
        if getattr(post, "fail", False):
            return 403, {"detail": "Forbidden"}
        return 201, {"data": {"id": f"19{len(posts)}", "text": text}}
    post.fail = False
    monkeypatch.setattr(x_poster, "post", post)

    async def identity(request):
        return "secret", None
    monkeypatch.setattr(main, "_admin_identity", identity)
    today = date.today().isoformat()
    fake.kv[main.DAILY_KEY.format(today)] = json.dumps({**DOC, "date": today})
    return fake, posts, post


def test_posts_once_a_day_when_switched_on(site, monkeypatch):
    fake, posts, _ = site
    monkeypatch.setattr(main, "_x_ready", lambda doc, now: True)
    asyncio.run(main._daily_tick())
    assert posts == []                                   # off until switched on
    c = TestClient(main.app)
    assert c.put("/api/admin/x", json={"enabled": True}).json()["enabled"] is True
    asyncio.run(main._daily_tick())
    asyncio.run(main._daily_tick())
    assert len(posts) == 1 and "code ABC123" in posts[0]
    state = c.get("/api/admin/x").json()
    assert state["today"]["status"] == "posted" and state["today"]["url"] == "https://x.com/i/status/191"
    assert state["configured"] and "BetIQ Daily Odds" in state["preview"]
    # Post now: refuses a second post unless asked again
    assert c.post("/api/admin/x/post", json={}).json()["status"] == "posted" and len(posts) == 1
    c.post("/api/admin/x/post", json={"again": True})
    assert len(posts) == 2


def test_waits_for_the_codes_until_the_deadline():
    now = datetime(2026, 9, 28, 6, 30, tzinfo=timezone.utc)
    later = {"home": "A", "away": "B", "date": "2026-09-28", "time": "18:00", "status": "pending"}
    waiting = {"slips": [{"status": "pending", "picks": [later], "booking": None}]}
    assert not main._x_ready(waiting, now)
    assert main._x_ready(waiting, now.replace(hour=8))
    booked = {"slips": [{"status": "pending", "picks": [later], "booking": {"code": "X1"}}]}
    assert main._x_ready(booked, now)


def test_failures_stop_after_three_and_alert(site, monkeypatch):
    fake, posts, post = site
    post.fail = True
    monkeypatch.setattr(main, "_x_ready", lambda doc, now: True)
    monkeypatch.setattr(main, "_STARTED_AT", datetime.now().timestamp())
    fake.kv[main.X_CONFIG_KEY] = json.dumps({"enabled": True})
    for _ in range(5):
        asyncio.run(main._daily_tick())
    assert len(posts) == 3
    alerts = TestClient(main.app).get("/api/admin/alerts").json()["alerts"]
    x_alert = next(a for a in alerts if "X" in a["title"])
    assert "Read and write" in x_alert["detail"]


def test_missing_keys(site, monkeypatch):
    monkeypatch.delenv("X_ACCESS_SECRET")
    c = TestClient(main.app)
    assert c.get("/api/admin/x").json()["missing"] == ["X_ACCESS_SECRET"]
    r = c.post("/api/admin/x/post", json={})
    assert r.status_code == 400 and "X_ACCESS_SECRET" in r.json()["detail"]
