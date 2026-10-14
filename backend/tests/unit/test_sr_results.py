"""Sportradar match finals for basketball / racket legs no other source scores."""

import asyncio
import json

import basketball_markets
import racket_markets
import sr_results


class FakeRedis:
    def __init__(self):
        self.h = {}

    def hget(self, k, f): return self.h.get(k, {}).get(f)
    def hset(self, k, f, v): self.h.setdefault(k, {})[f] = v
    def hsetnx(self, k, f, v): self.h.setdefault(k, {}).setdefault(f, v)
    def hdel(self, k, f): self.h.get(k, {}).pop(f, None)
    def hgetall(self, k): return dict(self.h.get(k, {}))
    def expire(self, k, s): pass


def body(**match):
    return {"doc": [{"data": {"match": match}}]}


def p(h, a): return {"home": h, "away": a}


BB = body(status={"name": "Ended"}, result=p(80, 75),
          periods={"p1": p(20, 18), "p2": p(22, 20), "p3": p(18, 19), "p4": p(20, 18)})
BB_OT = body(status={"name": "AOT"}, matchstatus="ended", result=p(90, 85),
             periods={"p1": p(20, 20), "p2": p(20, 20), "p3": p(20, 20), "p4": p(20, 20), "ot": p(10, 5)})
TENNIS = body(status={"name": "Ended"}, result=p(2, 1),
              periods={"p1": p(6, 4), "p2": p(3, 6), "p3": p(7, 5)})


class TestParse:
    def test_basketball_regulation(self):
        res = sr_results.parse(BB, "basketball")
        assert res == {"status": "finished", "final": [80, 75],
                       "periods": [[20, 18], [22, 20], [18, 19], [20, 18]], "ot": False}
        assert basketball_markets.settle("bb_winner", "1", res) == "won"

    def test_basketball_overtime(self):
        res = sr_results.parse(BB_OT, "basketball")
        assert res["ot"] is True and res["periods"] == [[20, 20]] * 4
        assert basketball_markets.settle("bb_overtime", "yes", res) in ("won", "lost")

    def test_tennis_sets_and_games(self):
        res = sr_results.parse(TENNIS, "tennis")
        assert res == {"status": "finished", "sets": [2, 1], "games": [[6, 4], [3, 6], [7, 5]], "ret": False}
        assert racket_markets.settle("rk_s1_winner", "1", res) == "won"

    def test_retirement_flagged(self):
        res = sr_results.parse(body(status={"name": "Ended"}, result=p(1, 0), retired=True,
                                    periods={"p1": p(6, 3), "p2": p(2, 1)}), "tennis")
        assert res["ret"] is True

    def test_not_over_or_called_off(self):
        assert sr_results.parse(body(status={"name": "2nd quarter"}, result=p(30, 28)), "basketball") is None
        assert sr_results.parse(body(status={"name": "Ended"}, result=p(0, 0), canceled=True), "basketball") is None
        assert sr_results.parse({"doc": [{"data": {}}]}, "tennis") is None
        assert sr_results.parse(None, "tennis") is None


class TestFlow:
    def test_wanted_then_read_then_stored(self):
        r = FakeRedis()
        assert sr_results.stored(r, "sr:match:123") is None
        sr_results.want(r, "sr:match:123", "basketball")
        sr_results.want(r, "sb:999", "basketball")          # not a Sportradar id: never asked

        async def get(url):
            assert url.endswith("/match_info/123")
            return BB
        out = asyncio.run(sr_results.refresh(r, get=get))
        assert out == {"asked": 1, "found": 1, "unread": 0}
        assert sr_results.stored(r, "sr:match:123")["final"] == [80, 75]
        assert r.hgetall(sr_results.WANTED_KEY) == {}

    def test_unfinished_tried_again_then_given_up(self):
        r = FakeRedis()
        sr_results.want(r, "sr:match:5", "tennis")

        async def get(url):
            return None
        asyncio.run(sr_results.refresh(r, get=get))
        assert json.loads(r.hget(sr_results.WANTED_KEY, "sr:match:5"))["tries"] == 1
        asyncio.run(sr_results.refresh(r, get=get, now=10 ** 12))
        assert r.hgetall(sr_results.WANTED_KEY) == {}


class TestGrader:
    def test_last_resort_for_bb_and_rk_legs(self, monkeypatch):
        import main
        r = FakeRedis()
        r.hset(sr_results.KEY, "sr:match:1", json.dumps(sr_results.parse(BB, "basketball")))
        r.hset(sr_results.KEY, "sr:match:2", json.dumps(sr_results.parse(TENNIS, "tennis")))
        monkeypatch.setattr(main, "_bb_result_for", lambda *a: None)
        monkeypatch.setattr(main, "_rk_result_for", lambda *a: None)
        monkeypatch.setattr(main, "_other_sport_md_result", lambda *a: None)
        result_for = main._leg_results(r, {})
        assert result_for({"market": "bb_winner", "sb": {"eventId": "sr:match:1"}})["final"] == [80, 75]
        assert result_for({"market": "rk_s1_winner", "event_id": "sr:match:2", "sport": "tennis"})["sets"] == [2, 1]
        assert result_for({"market": "rk_s1_winner", "event_id": "sr:match:3", "sport": "tennis"}) is None
        assert "sr:match:3" in r.hgetall(sr_results.WANTED_KEY)
