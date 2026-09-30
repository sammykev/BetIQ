"""
Live match stats for basketball, tennis and table tennis (live_stats.py):
Sportradar's stats docs read into the rows the site shows, which matches are
due a read, and the job and match-day endpoints that store and serve them.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import basketball_matchday as bbmd
import live_stats as ls
import main
from tests.unit.test_basketball_matchday import pred
from tests.unit.test_user_endpoints import FakeRedis

# From Sportradar (30 Sep 2026), trimmed
BASKETBALL = {"_doc": "details", "teams": {"home": "New Zealand Breakers", "away": "Cairns"},
              "index": [110, 1065, "twopointers", "threepointers", 1067, 129, 1654, 1714],
              "values": {"110": {"name": "Ball possession", "value": {"home": 54, "away": 46}},
                         "1065": {"name": "Free throws scored", "value": {"home": "0/2/2", "away": "1/1/2"}},
                         "twopointers": {"name": "Two pointers scored", "value": {"home": "6/6/12", "away": "8/7/15"}},
                         "threepointers": {"name": "Three pointers scored", "value": {"home": "5/5/10", "away": "3/10/13"}},
                         "1067": {"name": "Rebounds", "value": {"home": 17, "away": 12}},
                         "129": {"name": "Total Fouls", "value": {"home": 3, "away": 5}},
                         "1654": {"name": "Time spent in lead", "value": {"home": 603, "away": 196}},
                         "1714": None}}
TENNIS = {"values": {"130": {"name": "Aces", "value": {"home": 2, "away": 2}},
                     "139": {"name": "Break Points Won", "value": {"home": "0/0", "away": "2/5"}},
                     "1189": {"name": "1st Serve Successful", "value": {"home": "14/19/33", "away": "15/5/20"}},
                     "136": {"name": "Points won", "value": {"home": 21, "away": 32}}}}
TABLE_TENNIS = {"values": {"141": {"name": "Service Points Won", "value": {"home": "3/2/5", "away": "1/3/4"}},
                           "136": {"name": "Points won", "value": {"home": 6, "away": 3}},
                           "1077": {"name": "Service errors", "value": {"home": 0, "away": 0}}}}


class TestRows:
    def test_basketball(self):
        rows = {r["key"]: r for r in ls.rows(BASKETBALL, "basketball")}
        assert list(rows)[:4] == ["possession", "twos", "threes", "free_throws"]      # SHOWN's order
        assert rows["possession"] == {"key": "possession", "label": "Possession", "h": 54, "a": 46, "hs": "54%", "as": "46%"}
        assert rows["twos"]["h"] == 6 and rows["twos"]["hs"] == "6/12 (50%)" and rows["twos"]["as"] == "8/15 (53%)"
        assert rows["free_throws"]["hs"] == "0/2 (0%)"
        assert rows["rebounds"] == {"key": "rebounds", "label": "Rebounds", "h": 17, "a": 12}
        assert rows["time_in_lead"]["hs"] == "10:03" and rows["time_in_lead"]["as"] == "3:16"

    def test_tennis_and_table_tennis(self):
        t = {r["key"]: r for r in ls.rows(TENNIS, "tennis")}
        assert t["aces"]["h"] == 2 and "hs" not in t["aces"]
        assert t["break_points"]["hs"] == "0/0" and t["break_points"]["as"] == "2/5" and t["break_points"]["a"] == 2
        assert t["first_serve_in"]["hs"] == "14/33 (42%)"
        tt = {r["key"]: r for r in ls.rows(TABLE_TENNIS, "table_tennis")}
        assert tt["points"]["h"] == 6 and tt["service_points"]["hs"] == "3/5 (60%)" and tt["service_errors"]["a"] == 0

    def test_nothing_readable(self):
        assert ls.rows([], "tennis") == [] and ls.rows({"values": {}}, "tennis") == []
        assert ls.rows({"values": {"1": {"name": "Aces", "value": {"home": None, "away": 3}}}}, "tennis") == []

    def test_only_sportradar_ids(self):
        assert ls.sr_number("sr:match:75103556") == "75103556"
        assert ls.sr_number("sr:match:BAaDswk7me6i4doEmJKRIxg") is None
        assert ls.sr_number("sr:match:111111114432685") is None           # SportyBet's own, not Sportradar's


class TestDue:
    NOW = datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)

    def entry(self, eid, status, at_min_ago=1):
        return {"id": eid, "result": {"status": status, "at": (self.NOW - timedelta(minutes=at_min_ago)).isoformat()}}

    def test_live_matches_every_so_often_and_each_final_once(self):
        entries = [self.entry("sr:match:1", "live"), self.entry("sr:match:2", "live"),
                   self.entry("sr:match:3", "finished", 5), self.entry("sr:match:4", "finished", 120),
                   self.entry("sr:match:5", "finished", 5), {"id": "sr:match:6", "result": None}]
        stored = {"sr:match:2": {"at": (self.NOW - timedelta(seconds=30)).isoformat()},
                  "sr:match:5": {"at": self.NOW.isoformat(), "final": True}}
        assert ls.due(entries, stored, self.NOW) == ["sr:match:1", "sr:match:3"]


def test_matches_are_read_several_at_a_time():
    seen = []

    async def get(url):
        seen.append(url)
        await asyncio.sleep(0.05)
        return {"doc": [{"data": TENNIS}]}
    got = asyncio.run(ls.fetch([f"sr:match:{i}" for i in range(16)] + ["sr:match:abc"], "tennis", get))
    assert len(got) == 16 and len(seen) == 16 and got["sr:match:3"][0]["key"] == "aces"


class Redis(FakeRedis):
    def hset(self, k, mapping):
        self.h.setdefault(k, {}).update(mapping)

    def pipeline(self, transaction=False):
        r = self

        class P:
            def hset(self, k, mapping): r.hset(k, mapping)
            def expire(self, k, s): pass
            def execute(self): pass
        return P()


def test_the_job_stores_stats_and_the_day_shows_them(monkeypatch):
    fake = Redis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)

    async def open_to_all(request, sport):
        return None
    monkeypatch.setattr(main, "_check_sport_access", open_to_all)
    now = datetime.now(timezone.utc)
    p = pred(eid="sr:match:72016786", hours=0)
    p["date"], p["time"] = now.strftime("%Y-%m-%d"), (now - timedelta(minutes=30)).strftime("%H:%M")
    day: dict = {}
    bbmd.merge_predictions(day, [p], now - timedelta(hours=1))
    bbmd.apply_live(day["sr:match:72016786"], {"score": [27, 28], "periods": None, "minute": "Q2"})
    main._bbmd_save(fake, p["date"], day)

    async def fetch(ids, sport, get=None):
        return {i: ls.rows(BASKETBALL, sport) for i in ids if sport == "basketball"}
    monkeypatch.setattr(ls, "fetch", fetch)
    report = asyncio.run(main._live_stats_run())
    assert report["basketball"]["with_stats"] == 1 and report["tennis"] == {"due": 0}
    stored = json.loads(fake.h[ls.key("basketball", p["date"])]["sr:match:72016786"])
    assert stored["rows"][0]["key"] == "possession" and stored["final"] is False
    # Read again only after EVERY seconds
    assert asyncio.run(main._live_stats_run())["basketball"] == {"due": 0}

    c = TestClient(main.app)
    m = c.get(f"/api/basketball/matchday?date={p['date']}").json()["matches"][0]
    assert m["live_stats"][1]["hs"] == "6/12 (50%)"
