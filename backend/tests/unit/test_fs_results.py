"""Flashscore day-feed finals for basketball / racket legs no other source scores."""

import asyncio
import json
from datetime import datetime, timezone

import basketball_markets
import fs_results
import racket_markets
from tests.unit.test_sr_results import FakeRedis


def ts(day, hhmm="18:00"):
    return int(datetime.fromisoformat(f"{day}T{hhmm}:00+00:00").timestamp())


def rec(**f):
    return "AA÷" + f.pop("AA") + "¬" + "¬".join(f"{k}÷{v}" for k, v in f.items()) + "¬"


# As the feed lists them (the probe's records for real pending legs)
ALKAR = rec(AA="xK3pQ1aB", AD=ts("2026-10-11"), AB="3", CR="3", AC="3", CX="Alkar Sinj", AE="Alkar Sinj",
            WU="alkar-sinj", AF="Skrljevo", FK="Skrljevo", WV="skrljevo", AG="73", AH="88",
            BA="23", BB="22", BC="23", BD="21", BE="18", BF="26", BG="9", BH="19")
TENNIS = rec(AA="tN7rR2cD", AD=ts("2026-10-12", "15:00"), AB="3", AC="3", AE="Rivadeneira Gallegos M.",
             AF="Salazar J.", WU="rivadeneira-gallegos-maria", WV="salazar-julia", AG="1", AH="2",
             BA="6", BB="4", BC="2", BD="6", BE="4", BF="6")
LIVE = rec(AA="zZ9", AD=ts("2026-10-14"), AB="2", AC="13", AE="Ourense", AF="Estela", AG="40", AH="38")
FEED = "SA÷3¬~ZA÷CROATIA: Premijer liga¬ZEE÷x¬~" + ALKAR + "~" + LIVE + "~A1÷end¬~"
NOW = ts("2026-10-14", "12:00")


def one(text, sport):
    return fs_results.parse(fs_results.records(text)[0], sport)


class TestParse:
    def test_basketball(self):
        res = one(ALKAR, "basketball")
        assert res == {"status": "finished", "final": [73, 88],
                       "periods": [[23, 22], [23, 21], [18, 26], [9, 19]], "ot": False}
        assert basketball_markets.settle("bb_winner", "2", res) == "won"

    def test_basketball_overtime(self):
        res = one(rec(AA="o", AB="3", AC="10", AG="90", AH="85", BA="20", BB="20", BC="20", BD="20", BE="20",
                      BF="20", BG="20", BH="20", BI="10", BJ="5"), "basketball")
        assert res["ot"] is True and res["final"] == [90, 85] and len(res["periods"]) == 4

    def test_tennis(self):
        res = one(TENNIS, "racket")
        assert res == {"status": "finished", "sets": [1, 2], "games": [[6, 4], [2, 6], [4, 6]], "ret": False}
        assert racket_markets.settle("rk_s1_winner", "1", res) == "won"

    def test_retired(self):
        res = one(rec(AA="r", AB="3", AC="8", AG="1", AH="0", BA="6", BB="3", BC="2", BD="1"), "racket")
        assert res["ret"] is True and res["games"] == [[6, 3], [2, 1]]

    def test_not_over_or_called_off(self):
        assert one(LIVE, "basketball") is None
        assert one(rec(AA="c", AB="5", AC="5"), "basketball") is None     # cancelled
        assert one(rec(AA="p", AB="3", AC="4", AG="0", AH="0"), "racket") is None


class TestFind:
    def test_matches_both_sides_near_the_kickoff(self):
        recs = fs_results.records(FEED)
        assert fs_results.find(recs, "Alkar", "KK Skrljevo", ts("2026-10-11", "18:30"))["AA"] == "xK3pQ1aB"
        assert fs_results.find(recs, "Alkar", "Zadar", ts("2026-10-11")) is None
        assert fs_results.find(recs, "Alkar", "Skrljevo", ts("2026-10-05")) is None

    def test_generic_words_dont_match(self):
        recs = fs_results.records(rec(AA="g", AD=ts("2026-10-11"), AE="Basket Club Paris", AF="Basket Lyon"))
        assert fs_results.find(recs, "Basket Club Nice", "Basket Marseille", ts("2026-10-11")) is None


class TestFlow:
    def test_wanted_then_found_then_stored(self):
        r = FakeRedis()
        leg = {"date": "2026-10-11", "time": "18:00", "home": "Alkar Sinj", "away": "Skrljevo"}
        key = fs_results.leg_key("basketball", leg["date"], leg["home"], leg["away"])
        fs_results.want(r, leg, "basketball")
        urls = []

        async def get(url):
            urls.append(url)
            return FEED if url.endswith("f_3_-3_0_en_1") else "0"
        out = asyncio.run(fs_results.refresh(r, get=get, now=NOW))
        assert sorted(u.rsplit("/", 1)[1] for u in urls) == ["f_3_-2_0_en_1", "f_3_-3_0_en_1", "f_3_-4_0_en_1"]
        assert out == {"asked": 1, "found": 1, "feeds": 3, "unread": 0}
        assert fs_results.stored(r, key)["final"] == [73, 88]
        assert r.hgetall(fs_results.WANTED_KEY) == {}

    def test_not_listed_tried_again_then_dropped_with_the_feed_window(self):
        r = FakeRedis()
        leg = {"date": "2026-10-12", "home": "Nobody", "away": "Nowhere"}
        fs_results.want(r, leg, "tennis")

        async def get(url):
            return None
        out = asyncio.run(fs_results.refresh(r, get=get, now=NOW))
        assert out["found"] == 0 and out["unread"] == out["feeds"]
        k = fs_results.leg_key("tennis", "2026-10-12", "Nobody", "Nowhere")
        assert json.loads(r.hget(fs_results.WANTED_KEY, k))["tries"] == 1
        asyncio.run(fs_results.refresh(r, get=get, now=ts("2026-10-25")))
        assert r.hgetall(fs_results.WANTED_KEY) == {}


class TestGrader:
    def test_after_sportradar_for_bb_and_rk_legs(self, monkeypatch):
        import main
        r = FakeRedis()
        r.hset(fs_results.KEY, fs_results.leg_key("basketball", "2026-10-11", "Alkar Sinj", "Skrljevo"),
               json.dumps(one(ALKAR, "basketball")))
        r.hset(fs_results.KEY, fs_results.leg_key("tennis", "2026-10-12", "Rivadeneira", "Salazar"),
               json.dumps(one(TENNIS, "racket")))
        monkeypatch.setattr(main, "_bb_result_for", lambda *a: None)
        monkeypatch.setattr(main, "_rk_result_for", lambda *a: None)
        monkeypatch.setattr(main, "_other_sport_md_result", lambda *a: None)
        result_for = main._leg_results(r, {})
        bb = {"market": "bb_winner", "date": "2026-10-11", "home": "Alkar Sinj", "away": "Skrljevo",
              "sb": {"eventId": "sr:match:1"}}
        assert result_for(bb)["final"] == [73, 88]
        rk = {"market": "rk_s1_winner", "date": "2026-10-12", "home": "Rivadeneira", "away": "Salazar",
              "event_id": "sr:match:2", "sport": "tennis"}
        assert result_for(rk)["sets"] == [1, 2]
        miss = {**rk, "home": "Other"}
        assert result_for(miss) is None
        assert fs_results.leg_key("tennis", "2026-10-12", "Other", "Salazar") in r.hgetall(fs_results.WANTED_KEY)
