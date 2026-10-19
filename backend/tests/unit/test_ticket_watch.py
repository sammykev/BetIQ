"""
Ticket watch: follow chosen tickets until all are done, report each one as
it finishes and then which underperformed, with the 70%+ market legs next
to the fallback ones.
"""

import asyncio
import fnmatch
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

import main
import telegram_poster
import ticket_watch as tw
from tests.unit.test_user_endpoints import FakeRedis

TODAY = date(2026, 10, 19)
RANKED = {"football:1x2": {"n": 40, "hit_rate": 0.75}, "football:ou25": {"n": 40, "hit_rate": 0.62}}


def sport(leg):
    return leg.get("sport") or "football"


def leg(market, prob, status="pending", day="2026-10-18"):
    return {"home": "A", "away": "B", "date": day, "market": market, "code": "1", "label": f"{market} pick",
            "prob": prob, "odds": 1.5, "status": status}


def ticket(code, status, legs):
    return {"code": code, "status": status, "source": "optimizer", "total_odds": 4.5, "legs": legs}


def test_start_takes_the_open_tickets_or_the_codes_given():
    items = [ticket("A1", "open", []), ticket("B2", "won", []), ticket("C3", "pending", []), ticket("A1", "open", [])]
    assert [t["code"] for t in tw.start(items)["tickets"]] == ["A1", "C3"]
    w = tw.start(items, ["b2", "zz9"])
    assert [t["code"] for t in w["tickets"]] == ["B2"] and w["missing"] == ["ZZ9"]


def test_finished_when_settled_and_graded_or_long_past():
    assert tw.finished(ticket("A", "lost", [leg("1x2", .8, "lost")]), TODAY)
    assert not tw.finished(ticket("A", "lost", [leg("1x2", .8, "lost"), leg("1x2", .8)]), TODAY)
    assert not tw.finished(ticket("A", "open", [leg("1x2", .8)]), TODAY)
    assert tw.finished(ticket("A", "open", [leg("1x2", .8, day="2026-10-10")]), TODAY)


def test_performance_splits_70_percent_markets_from_fallback():
    p = tw.performance(ticket("A", "lost", [leg("1x2", .8, "won"), leg("1x2", .7, "won"),
                                            leg("ou25", .6, "lost"), leg("btts", .6, "won")]), RANKED, .7, sport)
    assert (p["won"], p["graded"], p["expected"], p["gap"]) == (3, 4, 2.7, 0.3)
    assert p["tiers"]["top"] == {"won": 2, "n": 2} and p["tiers"]["fallback"] == {"won": 0, "n": 1}
    assert p["tiers"]["unranked"] == {"won": 1, "n": 1}
    assert "ou25 pick (model 60%, market 62%)" in tw.ticket_text(p)


def test_check_reports_each_ticket_once_then_the_comparison():
    watch = tw.start([ticket("GOOD", "open", []), ticket("BAD", "open", [])])
    good = ticket("GOOD", "won", [leg("1x2", .7, "won"), leg("1x2", .7, "won")])
    bad = ticket("BAD", "open", [leg("ou25", .8, "lost"), leg("1x2", .8)])
    msgs = tw.check(watch, {"GOOD": good, "BAD": bad}, TODAY, RANKED, .7, sport)
    assert len(msgs) == 1 and msgs[0].startswith("Ticket GOOD") and not watch["done_at"]
    assert tw.check(watch, {"GOOD": good, "BAD": bad}, TODAY, RANKED, .7, sport) == []
    bad = ticket("BAD", "lost", [leg("ou25", .8, "lost"), leg("1x2", .8, "won")])
    msgs = tw.check(watch, {"GOOD": good, "BAD": bad}, TODAY, RANKED, .7, sport)
    assert len(msgs) == 2 and msgs[0].startswith("Ticket BAD") and "Underperformed most: BAD" in msgs[1]
    assert watch["done_at"] and watch["summary"]["underperformer"] == "BAD"
    assert watch["summary"]["top"] == {"won": 3, "n": 3} and watch["summary"]["fallback"] == {"won": 0, "n": 1}
    assert tw.check(watch, {"GOOD": good, "BAD": bad}, TODAY, RANKED, .7, sport) == []


class ScanRedis(FakeRedis):
    def scan_iter(self, match="*", count=None):
        return iter([k for k in list(self.kv) if fnmatch.fnmatch(k, match)])

    def delete(self, *keys):
        for k in keys:
            self.kv.pop(k, None)


@pytest.fixture
def api(monkeypatch):
    fake = ScanRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    monkeypatch.setattr(main, "_all_tickets_cache", [0.0, None])
    monkeypatch.setattr(main, "_ranked_codes", lambda: RANKED)
    sent = []

    async def notify(text):
        sent.append(text)
        return {"ok": True}
    monkeypatch.setattr(telegram_poster, "notify_admin", notify)
    main.app.dependency_overrides[main.require_admin] = lambda: "admin"
    fake.kv[main._ukey("u1", "tickets")] = json.dumps([
        ticket("T1", "open", [leg("1x2", .8, "won"), leg("1x2", .8, day="2026-10-20")]),
        ticket("T2", "won", [leg("1x2", .8, "won")])])
    yield TestClient(main.app), fake, sent
    main.app.dependency_overrides.pop(main.require_admin, None)


def test_watch_endpoints_and_the_job_check(api):
    c, fake, sent = api
    assert c.post("/api/admin/tickets/watch", json={}).json()["watching"] == ["T1"]
    got = c.get("/api/admin/tickets/watch").json()
    assert got["tickets"][0]["code"] == "T1" and got["tickets"][0]["finished"] is False and sent == []
    # T1 settles: the job's check sends its result and then the comparison
    fake.kv[main._ukey("u1", "tickets")] = json.dumps([
        ticket("T1", "lost", [leg("1x2", .8, "won"), leg("ou25", .8, "lost", "2026-10-20")])])
    msgs = main._check_ticket_watch(fake)
    assert asyncio.run(main._send_watch_messages(msgs)) == {"messages": 2, "sent": 2}
    assert sent[0].startswith("Ticket T1") and "Underperformed most: T1" in sent[1]
    assert main._check_ticket_watch(fake) == []
    assert c.delete("/api/admin/tickets/watch").json()["ok"] and c.get("/api/admin/tickets/watch").json() == {"watch": None}
    assert c.post("/api/admin/tickets/watch", json={"codes": ["nope"]}).status_code == 400
