"""
Live scores that stalled (30 Sep 2026): a football day the snapshot never
stored, matches ESPN doesn't score, live jobs stuck behind a hung request,
and matches left "live" after their live window.
"""

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest

import basketball_data as bd
import main
import results_feed
import sportybet
import tennis_facts as tf
from tests.unit.test_matchday import pred
from tests.unit.test_user_endpoints import FakeRedis


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    return fake


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class TestSportyBetFootball:
    KO = datetime(2026, 9, 30, 1, 0, tzinfo=timezone.utc)

    def ev(self, **kw):
        return {"eventId": "sr:match:1", "homeTeamName": "Mexico", "awayTeamName": "Peru",
                "estimateStartTime": ms(self.KO), **kw}

    def test_in_play_with_the_minute(self):
        got = results_feed.parse_sportybet(self.ev(status=1, matchStatus="H2", setScore="1:0", playedSeconds="63:12"))
        assert got["status"] == "live" and (got["hg"], got["ag"]) == (1, 0) and got["minute"] == "64'"
        assert got["date"] == "2026-09-30" and got["source"] == "sportybet"
        assert results_feed.parse_sportybet(self.ev(status=1, matchStatus="Halftime", setScore="0:0"))["minute"] == "HT"

    def test_finals_extra_time_and_called_off(self):
        ft = results_feed.parse_sportybet(self.ev(status=3, matchStatus="Ended", setScore="2:1"))
        assert ft["status"] == "finished" and (ft["hg"], ft["ag"]) == (2, 1) and not ft["aet"]
        assert results_feed.parse_sportybet(self.ev(status=4, matchStatus="AET", setScore="2:2"))["aet"]
        assert results_feed.parse_sportybet(self.ev(matchStatus="Postponed", setScore=""))["status"] == "postponed"

    def test_not_started_or_unscored_is_nothing(self):
        assert results_feed.parse_sportybet(self.ev(status=0, matchStatus="", setScore=None)) is None
        assert results_feed.parse_sportybet(self.ev(status=1, matchStatus="H1", setScore="")) is None
        assert results_feed.parse_sportybet({"homeTeamName": "A", "awayTeamName": "B"}) is None


class TestFootballRefresh:
    def test_a_day_the_snapshot_missed_is_stored_and_scored_from_sportybet(self, redis, monkeypatch):
        now = datetime.now(timezone.utc)
        ko = now - timedelta(minutes=50)
        d = ko.date().isoformat()
        p = pred(home="Mexico", away="Peru", d=d, t=ko.strftime("%H:%M"), league="INT-FRI",
                 league_name="International Friendly")
        monkeypatch.setattr(main, "_predictions_cache", [p])
        assert redis.get(f"betiq:md:{d}") is None                      # never snapshotted

        import curl_cffi.requests as cr

        class Session:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
        monkeypatch.setattr(cr, "AsyncSession", Session)

        async def fetch_espn(client, days, slugs):
            return {"results": [], "requests": 1, "errors": []}      # ESPN doesn't score it

        async def sb_live():
            return [{"date": d, "home": "Mexico", "away": "Peru", "status": "live", "minute": "51'",
                     "hg": 1, "ag": 1, "aet": False, "source": "sportybet"}]

        async def sb_results(day):
            return []
        monkeypatch.setattr(results_feed, "fetch_espn", fetch_espn)
        monkeypatch.setattr(results_feed, "fetch_sportybet_live", sb_live)
        monkeypatch.setattr(results_feed, "fetch_sportybet_results", sb_results)
        monkeypatch.setattr(main, "_sb_results_at", {})
        monkeypatch.setattr(main, "_settle_tickets", lambda r: {"settled": 0})

        rep = asyncio.run(main._refresh_matchdays(1, "live"))
        assert d in rep["stored_from_predictions"] and rep["updated"] == 1
        e = json.loads(redis.get(f"betiq:md:{d}"))["mexico|peru"]
        assert e["result"]["status"] == "live" and e["result"]["hg"] == 1 and e["result"]["minute"] == "51'"
        assert rep["sportybet"]["live_events"] == 1

    def test_one_failed_date_doesnt_stop_the_snapshot(self, redis, monkeypatch):
        preds = [pred(d="2026-10-01"), pred(d="2026-10-02"), pred(d="2026-10-03")]
        monkeypatch.setattr(main, "_predictions_cache", preds)
        real = main._md_save

        def flaky(r, d, day):
            if d == "2026-10-02":
                raise ConnectionError("Redis timed out")
            real(r, d, day)
        monkeypatch.setattr(main, "_md_save", flaky)
        assert main._snapshot_matchdays(redis) == 2
        assert redis.get("betiq:md:2026-10-01") and redis.get("betiq:md:2026-10-03")


class TestGuardedTicks:
    def test_a_hung_tick_is_given_up_and_recorded(self, monkeypatch):
        recorded = {}
        monkeypatch.setattr(main, "_record_job", lambda name, rep: recorded.update({name: rep}))

        async def hang():
            await asyncio.sleep(60)
        got = asyncio.run(main._guarded_tick("basketball_live", hang, 0.05))
        assert "gave up" in got["error"] and "gave up" in recorded["basketball_live"]["error"]

    def test_a_failing_tick_is_recorded_not_raised(self, monkeypatch):
        recorded = {}
        monkeypatch.setattr(main, "_record_job", lambda name, rep: recorded.update({name: rep}))

        async def boom():
            raise RuntimeError("SportyBet said no")
        assert "SportyBet said no" in asyncio.run(main._guarded_tick("tennis_live", boom, 5))["error"]

    def test_the_live_jobs_are_guarded(self):
        assert main._bb_live_guarded.__name__ and "_guarded_tick" in main._tennis_live_tick.__code__.co_names


class TestOverdue:
    def test_each_page_read_now_and_then_longest_waiting_first(self, monkeypatch):
        monkeypatch.setattr(main, "_overdue_at", {})
        ids = [f"sr:match:{i}" for i in range(20)]
        first = main._overdue("tennis", ids)
        assert len(first) == main.OVERDUE_PER_TICK
        rest = main._overdue("tennis", ids)
        assert set(rest) == set(ids) - set(first)                     # the ones not read yet
        assert main._overdue("tennis", ids) == []                      # all read within OVERDUE_EVERY
        assert main._overdue("basketball", ids[:2]) == ids[:2]         # per sport

    def test_an_event_page_that_shows_the_match_over_gives_the_final(self, monkeypatch):
        ko = datetime(2026, 9, 29, 22, 30, tzinfo=timezone.utc)

        async def request(session, method, path, **kw):
            if path == "/factsCenter/event":
                return {"data": {"eventId": kw["params"]["eventId"], "homeTeamName": "Indiana Fever",
                                 "awayTeamName": "Las Vegas Aces", "matchStatus": "Ended", "status": 3,
                                 "setScore": "88:92", "gameScore": ["20:25", "22:20", "24:22", "22:25"],
                                 "estimateStartTime": ms(ko)}}
            return {"data": []}                                         # not in the live list any more
        monkeypatch.setattr(sportybet, "_request", request)
        monkeypatch.setattr(sportybet, "shared_session", lambda: None)
        finals = {}
        live, how, checked = asyncio.run(bd.fetch_live(["sr:match:7"], final=bd.parse_result, finals=finals))
        assert live == {} and checked == {"sr:match:7"}
        assert finals["sr:match:7"]["hs"] == 88 and finals["sr:match:7"]["as"] == 92

    def test_a_hung_request_is_given_up(self, monkeypatch):
        async def request(session, method, path, **kw):
            await asyncio.sleep(60)
        monkeypatch.setattr(sportybet, "_request", request)
        monkeypatch.setattr(sportybet, "shared_session", lambda: None)
        live, how, checked = asyncio.run(bd.fetch_live(["sr:match:1"], timeout=0.05))
        assert live == {} and checked == set() and "1 failed" in how

    def test_racket_finals_from_the_page(self):
        ev = {"eventId": "sr:match:5", "homeTeamName": "Stevens, Amy", "awayTeamName": "Sato, Hikaru",
              "matchStatus": "Ended", "status": 3, "setScore": "2:1", "gameScore": ["6:4", "3:6", "6:2"],
              "estimateStartTime": ms(datetime(2026, 9, 30, tzinfo=timezone.utc))}
        assert tf.parse_result(ev)["sets"] == [2, 1]


class TestLiveReadings:
    """_apply_live_reading: what one live read means for a started match."""
    NOW = datetime(2026, 9, 30, 5, 30, tzinfo=timezone.utc)

    def entry(self, status="live", minutes_ago=5, minute="Q1 2:26"):
        at = (self.NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
        return {"id": "sr:match:1", "result": {"status": status, "score": [10, 8], "periods": None,
                                               "minute": minute, "at": at}}

    def run(self, e, overdue=False, live=None, ended=None, checked=(), paged=()):
        import basketball_matchday as bbmd
        return main._apply_live_reading(e, "sr:match:1", overdue, live or {}, ended or {}, set(checked), set(paged),
                                        self.NOW, bbmd.apply_result, bbmd.apply_live, bbmd.stale_live)

    def test_a_game_past_its_window_takes_no_live_score(self):
        e = self.entry(minutes_ago=600)
        same = {"sr:match:1": {"score": [10, 8], "periods": None, "minute": "Q1 2:26"}}
        assert self.run(e, overdue=True, live=same, checked={"sr:match:1"}, paged={"sr:match:1"})
        assert e["result"]["status"] == "scheduled" and e["result"]["minute"] == "Final soon"

    def test_a_page_score_frozen_for_20_minutes_is_taken_as_over(self):
        e = self.entry(minutes_ago=25)
        same = {"sr:match:1": {"score": [10, 8], "periods": None, "minute": "Q1 2:26"}}
        assert self.run(e, live=same, checked={"sr:match:1"}, paged={"sr:match:1"})
        assert e["result"]["minute"] == "Final soon"

    def test_a_moving_page_score_or_a_listing_score_stays_live(self):
        e = self.entry(minutes_ago=25)
        moved = {"sr:match:1": {"score": [12, 8], "periods": None, "minute": "Q1 1:40"}}
        assert self.run(e, live=moved, checked={"sr:match:1"}, paged={"sr:match:1"})
        assert e["result"]["status"] == "live" and e["result"]["score"] == [12, 8]
        still = self.entry(minutes_ago=25)
        same = {"sr:match:1": {"score": [10, 8], "periods": None, "minute": "Q1 2:26"}}
        assert not self.run(still, live=same, checked={"sr:match:1"})   # from the listing: trusted
        assert still["result"]["status"] == "live"

    def test_a_final_wins(self):
        e = self.entry()
        final = {"sr:match:1": {"hs": 88, "as": 92, "q": None, "ot": False}}
        assert self.run(e, overdue=True, ended=final)
        assert e["result"]["status"] == "finished" and e["result"]["score"] == [88, 92]


class TestResultsJobs:
    def test_racket_results_read_for_open_days_and_today(self, redis, monkeypatch):
        import racket_matchday as rmd
        now = datetime.now(timezone.utc)
        yday = (now.date() - timedelta(days=1)).isoformat()
        day = {"sr:match:1": {"id": "sr:match:1", "home": "A", "away": "B", "date": yday, "time": "14:00",
                              "result": {"status": "live"}, "locked": True}}
        monkeypatch.setattr(main, "_rkmd_load", lambda r, sport, d: day if d == yday else {})
        asked = []

        async def fetch(d, sport="tennis"):
            asked.append(d)
            return [{"id": "sr:match:1"}]
        monkeypatch.setattr(tf, "fetch_results_day", fetch)
        stored = {}
        monkeypatch.setattr(redis, "hset", lambda k, f, v: stored.update({f: v}), raising=False)
        got = asyncio.run(main._rk_results_refresh("tennis"))
        assert sorted(asked) == sorted({yday, now.date().isoformat()}) and set(stored) == set(asked)
        assert got["results"][yday] == 1

    def test_the_results_jobs_are_scheduled_apart_from_the_live_ticks(self):
        import inspect
        src = inspect.getsource(main._rk_live_tick) + inspect.getsource(main._bb_live_tick)
        assert "fetch_results_day" not in src
