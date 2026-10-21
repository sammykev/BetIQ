"""
Ticket watch: the watched accounts' tickets join a round on their own,
each is reported as it finishes, then which underperformed, with the 70%+
market legs next to the fallback ones.
"""

import asyncio
import fnmatch
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

import auth
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


def test_new_tickets_from_watched_accounts_join_the_round_once():
    w = tw._migrate(None)
    items = [{**ticket("A1", "open", []), "uid": "u1"}, {**ticket("B2", "won", []), "uid": "u1"},
             {**ticket("C3", "pending", []), "uid": "u2"}, {**ticket("D4", "open", []), "uid": "other"}]
    assert [t["code"] for t in tw.add_new(w, items, ["u1", "u2"])] == ["A1", "C3"]
    assert [t["code"] for t in w["round"]["tickets"]] == ["A1", "C3"]
    assert tw.add_new(w, items, ["u1", "u2"]) == []
    assert tw.added_text(items[0]).startswith("Watching ticket A1")


def test_first_version_watch_is_carried_over():
    w = tw._migrate({"started_at": "x", "tickets": [{"code": "A1", "uid": "u1"}], "reported": [],
                     "done_at": None, "summary": None, "missing": []})
    assert w["round"]["tickets"] == [{"code": "A1", "uid": "u1"}] and w["seen"] == ["A1"] and w["history"] == []


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
    watch = tw._migrate(None)
    tw.add_new(watch, [{**ticket("GOOD", "open", []), "uid": "u"}, {**ticket("BAD", "open", []), "uid": "u"}], ["u"])
    good = ticket("GOOD", "won", [leg("1x2", .7, "won"), leg("1x2", .7, "won")])
    bad = ticket("BAD", "open", [leg("ou25", .8, "lost"), leg("1x2", .8)])
    msgs = tw.check(watch, {"GOOD": good, "BAD": bad}, TODAY, RANKED, .7, sport)
    assert len(msgs) == 1 and msgs[0].startswith("Ticket GOOD") and watch["round"]
    assert tw.check(watch, {"GOOD": good, "BAD": bad}, TODAY, RANKED, .7, sport) == []
    bad = ticket("BAD", "lost", [leg("ou25", .8, "lost"), leg("1x2", .8, "won")])
    msgs = tw.check(watch, {"GOOD": good, "BAD": bad}, TODAY, RANKED, .7, sport)
    assert len(msgs) == 2 and msgs[0].startswith("Ticket BAD") and "Underperformed most: BAD" in msgs[1]
    assert watch["round"] is None and watch["history"][0]["codes"] == ["GOOD", "BAD"]
    s = watch["history"][0]["summary"]
    assert s["underperformer"] == "BAD" and s["top"] == {"won": 3, "n": 3} and s["fallback"] == {"won": 0, "n": 1}
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


def test_watch_runs_by_itself_for_the_watched_email(api, monkeypatch):
    c, fake, sent = api
    monkeypatch.setenv("TICKET_WATCH_EMAILS", "Watched@Example.com")
    lookups = []

    async def by_email(emails):
        lookups.append(emails)
        return {"watched@example.com": "u1"}
    monkeypatch.setattr(auth, "clerk_uids_by_email", by_email)

    async def names(uids):
        return {u: {"name": "W", "email": "watched@example.com"} for u in uids}
    monkeypatch.setattr(main, "_account_names", names)
    fake.kv[main._ukey("u2", "tickets")] = json.dumps([ticket("NOT", "open", [leg("1x2", .8)])])
    # the deploy / hourly check: T1 (open) joins on its own, T2 (won) and u2's ticket don't
    assert asyncio.run(main._ticket_watch_tick()) == {"messages": 1, "sent": 1}
    assert sent[0].startswith("Watching ticket T1")
    got = c.get("/api/admin/tickets/watch").json()
    assert got["source"] == "env" and got["emails"] == {"watched@example.com": "u1"}
    assert [t["code"] for t in got["round"]["tickets"]] == ["T1"] and got["round"]["tickets"][0]["finished"] is False
    assert lookups == [["watched@example.com"]]   # the id is kept
    # a second ticket booked later joins the same round
    fake.kv[main._ukey("u1", "tickets")] = json.dumps([
        ticket("T1", "open", [leg("1x2", .8, "won"), leg("1x2", .8, day="2026-10-20")]),
        ticket("T3", "pending", [leg("1x2", .7, day="2026-10-20")])])
    assert c.post("/api/admin/tickets/watch/check").json()["messages"] == 1 and sent[-1].startswith("Watching ticket T3")
    # both settle: each is reported, then the round's comparison, and the round is archived
    fake.kv[main._ukey("u1", "tickets")] = json.dumps([
        ticket("T1", "lost", [leg("1x2", .8, "won"), leg("ou25", .8, "lost", "2026-10-20")]),
        ticket("T3", "won", [leg("1x2", .7, "won", "2026-10-20")])])
    assert asyncio.run(main._ticket_watch_tick())["sent"] == 3
    assert sent[-3].startswith("Ticket T1") and sent[-2].startswith("Ticket T3") and "Underperformed most: T1" in sent[-1]
    got = c.get("/api/admin/tickets/watch").json()
    assert got["round"] is None and got["history"][0]["codes"] == ["T1", "T3"]
    assert asyncio.run(main._ticket_watch_tick()) == {"messages": 0}
    # the admin's list replaces the env's
    assert c.put("/api/admin/tickets/watch/emails", json={"emails": ["nope"]}).status_code == 400
    assert c.put("/api/admin/tickets/watch/emails", json={"emails": ["other@example.com"]}).json()["ok"]
    assert c.get("/api/admin/tickets/watch").json()["source"] == "setting"


def test_old_racket_legs_are_ranked_by_either_sport():
    ranked = {"tennis:rk_set1_games": {"n": 30, "hit_rate": 0.55}, "table_tennis:rk_winner": {"n": 30, "hit_rate": 0.8}}
    racket = lambda l: l.get("sport") or "football"
    assert tw._ranked(ranked, {"sport": "racket", "market": "rk_set1_games"}, racket)["hit_rate"] == 0.55
    assert tw._ranked(ranked, {"sport": "racket", "market": "rk_winner"}, racket)["hit_rate"] == 0.8
    assert tw._ranked(ranked, {"sport": "tennis", "market": "rk_winner"}, racket) is None
    assert tw._ranked(None, {"sport": "racket", "market": "rk_winner"}, racket) is None
