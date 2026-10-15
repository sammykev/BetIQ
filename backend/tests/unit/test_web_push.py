"""
Browser push notifications (web_push.py).
"""

import json

import web_push


class FakeRedis:
    def __init__(self, subs=()):
        self.subs, self.kv = set(subs), {}

    def smembers(self, k):
        return set(self.subs)

    def srem(self, k, v):
        self.subs.discard(v)

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True


class Gone(Exception):
    def __init__(self, status):
        super().__init__(f"Push failed: {status}")
        self.response = type("R", (), {"status_code": status})()


def sub(n):
    return json.dumps({"endpoint": f"https://fcm.googleapis.com/x/{n}", "keys": {"p256dh": "a", "auth": "b"}}, sort_keys=True)


def test_sends_to_everyone_and_drops_expired(monkeypatch):
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "k")
    r = FakeRedis([sub(1), sub(2), sub(3)])
    sent = []

    def sender(subscription_info, data, vapid_private_key, vapid_claims, ttl):
        n = subscription_info["endpoint"][-1]
        if n == "2":
            raise Gone(410)
        if n == "3":
            raise Gone(500)
        sent.append((n, json.loads(data)["url"], vapid_claims["sub"]))

    out = web_push.send_all(r, web_push.payload("T", "B", "/daily"), sender)
    assert out == {"sent": 1, "dropped": 1, "failed": 1}
    assert sent == [("1", "/daily", "mailto:admin@betiq.app")]
    assert r.subs == {sub(1), sub(3)}          # a server error keeps the subscription


def test_payload_and_once():
    p = json.loads(web_push.payload("x" * 200, "y", "https://evil.example", "daily"))
    assert len(p["title"]) == 80 and p["url"] == "/" and p["tag"] == "betiq-daily"
    r = FakeRedis()
    assert web_push.once(r, "daily", "2026-10-15") and not web_push.once(r, "daily", "2026-10-15")
    assert web_push.once(r, "daily", "2026-10-16")


def test_daily_text():
    assert web_push.daily_text({"slips": [{"status": "none"}]}) is None
    t = web_push.daily_text({"slips": [{"status": "pending", "picks": [1], "total_odds": 5.123, "target": 5},
                                       {"status": "pending", "picks": [1], "target": 10}]})
    assert t["title"] == "Today's odds are out" and "2 slips at 5.12, ~10 odds" in t["body"]


def test_configured(monkeypatch):
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "k")
    assert not web_push.configured()
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "p")
    assert web_push.configured()
