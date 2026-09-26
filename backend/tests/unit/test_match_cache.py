import asyncio
import time
from datetime import date, datetime, timedelta, timezone

import llm_service
import match_cache


class FakeRedis:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v, ex=None):
        self.d[k] = v

    def delete(self, k):
        self.d.pop(k, None)


def test_first_visitor_builds_later_ones_are_served_from_the_store():
    async def go():
        cache = match_cache.MatchCache(lambda: FakeRedis())
        calls = []

        async def build():
            calls.append(1)
            await asyncio.sleep(0.01)
            return {"x": len(calls)}

        # Two visitors at once: one build
        (a, ia), (b, ib) = await asyncio.gather(cache.get("t", "k", build, fp="1"),
                                                cache.get("t", "k", build, fp="1"))
        assert a == b == {"x": 1} and len(calls) == 1
        v, info = await cache.get("t", "k", build, fp="1")
        assert v == {"x": 1} and info["cached"] and not info["refreshing"] and len(calls) == 1
    asyncio.run(go())


def test_new_information_serves_the_old_one_and_rebuilds_in_the_background():
    async def go():
        r = FakeRedis()
        cache = match_cache.MatchCache(lambda: r)
        n = {"v": 0}

        async def build():
            n["v"] += 1
            return {"v": n["v"]}

        await cache.get("t", "k", build, fp="old")
        v, info = await cache.get("t", "k", build, fp="new")
        assert v == {"v": 1} and info["refreshing"]
        await asyncio.sleep(0.01)
        v, info = await cache.get("t", "k", build, fp="new")
        assert v == {"v": 2} and not info["refreshing"]
    asyncio.run(go())


def test_a_value_can_ask_to_be_retried_soon():
    async def go():
        cache = match_cache.MatchCache(lambda: None)   # no Redis: in-process
        n = {"v": 0}

        async def build():
            n["v"] += 1
            return {"v": n["v"], "_fresh_for": 0}

        await cache.get("t", "k", build, fp="1")
        _, info = await cache.get("t", "k", build, fp="1")
        assert info["refreshing"]
    asyncio.run(go())


def test_kept_until_after_kickoff():
    now = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
    assert match_cache.ttl_seconds(now + timedelta(hours=5), now) == 8 * 3600
    assert match_cache.ttl_seconds(now - timedelta(days=1), now) == 600
    assert match_cache.news_fresh_for(now + timedelta(hours=1), now) < match_cache.news_fresh_for(now + timedelta(days=3), now)
    assert match_cache.match_key("Man United", "Chelsea FC", "2026-09-27") == "2026-09-27:man-united:chelsea-fc"


def test_news_keeps_only_dated_items_from_the_last_week():
    reply = """- [2026-09-25] Saka out with a hamstring injury (BBC)
- [2026-08-02] Odegaard ruled out for a month (Sky)
Arsenal have a strong squad.
* 2026-09-24: Chelsea's James back in training (Guardian)
NO RECENT NEWS"""
    items = llm_service.recent_items(reply, date(2026, 9, 26))
    assert items == [{"date": "2026-09-25", "text": "Saka out with a hamstring injury (BBC)"},
                     {"date": "2026-09-24", "text": "Chelsea's James back in training (Guardian)"}]
    assert llm_service.recent_items("NO RECENT NEWS", date(2026, 9, 26)) == []
