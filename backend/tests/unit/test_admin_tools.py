"""
Admin tools: traffic counting (traffic.py), revenue summary, audit log,
security overview and jobs.
"""

import json
from collections import defaultdict
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import main
import traffic


class FakeRedis:
    def __init__(self):
        self.lists, self.kv, self.hll, self.ttls = defaultdict(list), {}, defaultdict(set), {}

    def rpush(self, k, v): self.lists[k].append(v)
    def lpush(self, k, v): self.lists[k].insert(0, v)
    def ltrim(self, k, a, b): self.lists[k] = self.lists[k][a:b + 1]
    def lrange(self, k, a, b): return list(self.lists[k][a:None if b == -1 else b + 1])
    def delete(self, k): self.lists.pop(k, None)
    def expire(self, k, s): self.ttls[k] = s
    def ttl(self, k): return self.ttls.get(k, -1)
    def pfadd(self, k, *v): self.hll[k].update(v)
    def pfcount(self, *ks): return len(set().union(*[self.hll[k] for k in ks]))
    def get(self, k): return self.kv.get(k)
    def set(self, k, v, **kw): self.kv[k] = v


def hit(**kw):
    return traffic.clean_hit({"path": "/", "visitor": "v1", "country": "ng", "city": "Lagos", "region": "LA",
                              "lat": "6.45", "lon": "3.39", "device": "mobile", "browser": "Chrome", "os": "Android",
                              "lang": "en-NG", "plan": "free", "new_session": True, "new_visitor": True,
                              "referrer": "www.Google.com", **kw})


class TestCleanHit:
    def test_bounds_and_defaults(self):
        h = hit(path="/match?home=A&away=B", country="Nigeria", device="fridge", plan="vip")
        assert h["path"] == "/match" and h["country"] == "??" and h["device"] == "desktop" and h["plan"] == "anon"
        assert h["referrer"] == "google.com" and h["lat"] == 6.5
        assert traffic.clean_hit({"path": "http://evil", "visitor": "x"}) is None
        assert traffic.clean_hit({"path": "/", "visitor": "<script>"})["visitor"] == "script"
        assert hit(lat="999")["lat"] is None


class TestCounting:
    def test_record_flush_and_summary(self):
        t, r = traffic.Traffic(), FakeRedis()
        now = datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc).timestamp()
        t.record(hit(), now=now)
        t.record(hit(path="/optimizer", new_session=False, new_visitor=False), now=now + 10)
        t.record(hit(visitor="v2", country="GH", city="Accra", lat="5.6", lon="-0.2", device="desktop",
                     referrer="", utm_source="whatsapp", utm_medium="social"), now=now + 20)
        assert t.live(now=now + 30)["online"] == 2
        assert t.flush(r) == 3
        assert t.flush(r) == 0  # nothing new
        s = traffic.summary(r, days=7, now=datetime(2026, 9, 24, 20, tzinfo=timezone.utc))
        assert s["totals"] == {"pageviews": 3, "visitors": 2, "sessions": 2, "new_visitors": 2, "pages_per_session": 1.5}
        assert s["countries"][0] == {"code": "NG", "pageviews": 2, "sessions": 1}
        assert {c["city"] for c in s["cities"]} == {"Lagos", "Accra"}
        assert s["utm"] == [{"key": "whatsapp/social/-", "count": 1}]
        assert s["hours"][18] == 3 and s["series"][-1]["pageviews"] == 3

    def test_finished_days_are_compacted(self):
        t, r = traffic.Traffic(), FakeRedis()
        now = datetime(2026, 9, 23, 12, tzinfo=timezone.utc).timestamp()
        for i in range(3):
            t.record(hit(visitor=f"v{i}"), now=now + i)
            t.flush(r)
        assert len(r.lists["betiq:traffic:2026-09-23"]) == 3
        s = traffic.summary(r, days=2, now=datetime(2026, 9, 24, 1, tzinfo=timezone.utc))
        assert s["totals"]["pageviews"] == 3
        assert len(r.lists["betiq:traffic:2026-09-23"]) == 1  # merged into one entry

    def test_live_forgets_after_five_minutes(self):
        t = traffic.Traffic()
        t.record(hit(), now=1000)
        assert t.live(now=1000 + traffic.LIVE_SECONDS + 1)["online"] == 0


class TestEndpoints:
    @pytest.fixture
    def client(self, monkeypatch):
        self.redis = FakeRedis()
        monkeypatch.setattr(main, "_get_redis", lambda: self.redis)
        monkeypatch.setattr(main, "ADMIN_SECRET", "s3cret-admin")
        monkeypatch.setattr(main, "_traffic", traffic.Traffic())
        return TestClient(main.app)

    def test_hits_need_the_key(self, client, monkeypatch):
        monkeypatch.delenv("TRAFFIC_KEY", raising=False)
        body = {"path": "/", "visitor": "abc", "country": "NG"}
        assert client.post("/api/traffic/hit", json=body).status_code == 403
        assert client.post("/api/traffic/hit", json=body, headers={"X-Traffic-Key": "nope"}).status_code == 403
        assert client.post("/api/traffic/hit", json=body, headers={"X-Traffic-Key": "s3cret-admin"}).json()["ok"]
        admin = {"X-Admin-Secret": "s3cret-admin"}
        s = client.get("/api/admin/traffic?days=7", headers=admin).json()
        assert s["totals"]["pageviews"] == 1 and s["live"]["online"] == 1
        assert client.get("/api/admin/traffic").status_code == 403

    def test_audit_log_records_admin_actions(self, client):
        admin = {"X-Admin-Secret": "s3cret-admin"}
        client.post("/api/admin/banner", json={"text": "x" * 500}, headers=admin)
        client.post("/api/config/paywall", json={"enabled": False}, headers=admin)
        entries = client.get("/api/admin/audit", headers=admin).json()["entries"]
        assert [e["action"] for e in entries] == ["paywall", "banner"]
        assert entries[0]["actor"] == "secret" and entries[0]["enabled"] is False
        assert len(entries[1]["text"]) == 300

    def test_security_overview(self, client):
        body = client.get("/api/admin/security", headers={"X-Admin-Secret": "s3cret-admin"}).json()
        ids = {c["id"]: c["ok"] for c in body["checks"]}
        assert ids["admin_secret"] is False  # 12 characters
        assert ids["cors"] is True and ids["redis"] is True

    def test_unknown_job(self, client):
        r = client.post("/api/admin/jobs/rm-rf/run", headers={"X-Admin-Secret": "s3cret-admin"})
        assert r.status_code == 404


class TestRevenue:
    def test_summary(self):
        now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
        tx = [{"amount": 150000, "paid_at": "2026-09-24T08:00:00.000Z", "customer": {"email": "A@x.com"}},
              {"amount": 150000, "paid_at": "2026-09-20T08:00:00.000Z", "customer": {"email": "a@x.com"}},
              {"amount": 300000, "paid_at": "2026-07-01T08:00:00.000Z", "customer": {"email": "b@x.com"}}]
        s = main._revenue_summary(tx, now=now)
        assert (s["total_revenue"], s["today"], s["last_7_days"], s["last_30_days"]) == (6000, 1500, 3000, 3000)
        assert s["customers"] == 2 and s["average"] == 2000
        assert s["series"][-1] == {"date": "2026-09-24", "amount": 1500}
