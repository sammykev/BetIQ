"""
Match days (matchday.py): pre-match snapshots locked at kick-off, results
from ESPN and the league CSVs (results_feed.py), grading and the track
record; and the endpoints and refresh job in main.py.
"""

import asyncio
import json
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
import matchday
import results_feed
from tests.unit.test_user_endpoints import FakeRedis

NOW = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)


def pred(home="Arsenal", away="Chelsea", d="2026-09-25", t="19:00", **kw):
    return {"home": home, "away": away, "date": d, "time": t, "league": "PL", "league_name": "Premier League",
            "flag": "E", "p_home": 0.55, "p_draw": 0.25, "p_away": 0.2, "p_over15": 0.8, "p_over25": 0.6,
            "p_over35": 0.3, "p_btts": 0.58, "tip_1x2": "Arsenal Win", "tip_code": "1", "tip_goals": "Over 1.5",
            "goals_confidence": 0.8, "odds_home": 1.8, "odds_draw": 3.6, "odds_away": 4.5,
            "set_pieces": {"corners": {"mean": 10.2, "over": {"9.5": 0.6}}, "bookings": {"mean": 4.1, "over": {"4.5": 0.42}}},
            **kw}


def finished(hg, ag, **kw):
    return {"status": "finished", "hg": hg, "ag": ag, "source": "espn", **kw}


class TestSnapshots:
    def test_refreshed_until_kickoff_then_locked(self):
        day = {}
        assert matchday.merge_predictions(day, [pred()], NOW)
        e = day["arsenal|chelsea"]
        assert e["pred"]["p_home"] == 0.55 and e["pred"]["corners_over"] == 0.6 and not e["locked"]
        assert not matchday.merge_predictions(day, [pred()], NOW)  # nothing new: no write
        assert matchday.merge_predictions(day, [pred(p_home=0.6)], NOW)
        assert e["pred"]["p_home"] == 0.6
        later = NOW + timedelta(hours=2)
        matchday.merge_predictions(day, [pred(p_home=0.7)], later)
        assert e["locked"] and e["pred"]["p_home"] == 0.6  # the pre-match prediction stays

    def test_match_added_after_kickoff_is_locked(self):
        day = {}
        matchday.merge_predictions(day, [pred(t="17:00")], NOW)
        assert day["arsenal|chelsea"]["locked"]


class TestGrading:
    def entry(self, **kw):
        day = {}
        matchday.merge_predictions(day, [pred(**kw)], NOW)
        return day["arsenal|chelsea"]

    def test_every_market(self):
        e = self.entry()
        assert matchday.apply_result(e, finished(2, 1, corners=[7, 4], bookings=[2, 1]))
        g = e["grades"]
        assert g["tip"] == {"pick": "Arsenal Win", "prob": 0.55, "verdict": "won"}
        assert g["favourite"]["verdict"] == "won" and g["goals"]["verdict"] == "won"
        assert g["ou25"] == {"pick": "Over 2.5", "prob": 0.6, "verdict": "won"}
        assert g["btts"]["verdict"] == "won"
        assert g["corners"] == {"pick": "Over 9.5", "prob": 0.6, "verdict": "won"}
        assert g["bookings"] == {"pick": "Under 4.5", "prob": 0.58, "verdict": "won"}

    def test_live_then_final_never_back(self):
        e = self.entry()
        assert matchday.apply_result(e, {"status": "live", "minute": "55'", "hg": 0, "ag": 0, "source": "espn"})
        assert e["grades"] is None and e["result"]["minute"] == "55'"
        assert matchday.apply_result(e, finished(0, 1))
        assert e["grades"]["tip"]["verdict"] == "lost"
        assert not matchday.apply_result(e, {"status": "live", "hg": 0, "ag": 0})

    def test_second_source_only_fills_stats(self):
        e = self.entry()
        matchday.apply_result(e, finished(2, 1))
        assert "corners" not in e["grades"]
        assert matchday.apply_result(e, finished(2, 1, corners=[6, 3], source="football-data.co.uk"))
        assert e["result"]["source"] == "espn" and e["grades"]["corners"]["verdict"] == "lost"
        assert not matchday.apply_result(e, finished(2, 1, corners=[6, 3], source="football-data.co.uk"))

    def test_extra_time_is_not_graded(self):
        e = self.entry()
        matchday.apply_result(e, finished(2, 1, aet=True))
        assert e["grades"] is None

    def test_needs_result(self):
        e = self.entry(t="17:00")
        assert matchday.needs_result(e, NOW)
        matchday.apply_result(e, finished(1, 0, corners=[5, 5], bookings=[1, 1]))
        assert not matchday.needs_result(e, NOW)
        assert not matchday.needs_result(self.entry(t="21:00"), NOW)  # not started


class TestMatching:
    def test_names_and_sides(self):
        entries = {"man united|man city": {"home": "Man United", "away": "Man City", "date": "2026-09-25"}}
        results = [{"date": "2026-09-25", "home": "Manchester City", "away": "Manchester United"},
                   {"date": "2026-09-26", "home": "Manchester United", "away": "Manchester City", "hg": 1}]
        [(k, res)] = matchday.match_results(entries, results)
        assert res["hg"] == 1  # not the reversed fixture

    def test_internationals_by_nation(self):
        entries = {"k": {"home": "South Korea", "away": "Japan", "date": "2026-09-25"}}
        assert matchday.match_results(entries, [{"date": "2026-09-25", "home": "Korea Republic", "away": "Japan"}])


def espn_event(state, name, hs, as_, stats=True, details=None, completed=True, detail="FT"):
    comp = {"competitors": [
        {"homeAway": "home", "score": hs, "team": {"id": "1", "displayName": "Arsenal"},
         "statistics": [{"name": "wonCorners", "displayValue": "7"}] if stats else []},
        {"homeAway": "away", "score": as_, "team": {"id": "2", "displayName": "Chelsea"},
         "statistics": [{"name": "wonCorners", "displayValue": "3"}] if stats else []}],
        "status": {"type": {"state": state, "name": name, "completed": completed, "shortDetail": detail}}}
    if details is not None:
        comp["details"] = details
    return {"date": "2026-09-25T19:00Z", "competitions": [comp]}


class TestEspn:
    def test_statuses_scores_and_stats(self):
        cards = [{"yellowCard": True, "team": {"id": "1"}}, {"redCard": True, "team": {"id": "2"}},
                 {"yellowCard": True, "team": {"id": "2"}}]
        data = {"events": [espn_event("post", "STATUS_FULL_TIME", "2", "1", details=cards),
                           espn_event("in", "STATUS_SECOND_HALF", "0", "0", completed=False, detail="67'"),
                           espn_event("pre", "STATUS_SCHEDULED", "0", "0", completed=False),
                           espn_event("post", "STATUS_POSTPONED", "0", "0"),
                           espn_event("post", "STATUS_FINAL_AET", "3", "2")]}
        ft, live, pre, pp, aet = results_feed.parse_espn(data)
        assert (ft["status"], ft["hg"], ft["ag"], ft["corners"], ft["bookings"]) == ("finished", 2, 1, [7, 3], [1, 3])
        assert (live["status"], live["minute"], live["hg"]) == ("live", "67'", 0)
        assert pre["status"] == "scheduled" and pre["hg"] is None
        assert pp["status"] == "postponed"
        assert aet["aet"] is True

    def test_no_card_data_means_no_bookings(self):
        [r] = results_feed.parse_espn({"events": [espn_event("post", "STATUS_FULL_TIME", "1", "1")]})
        assert r["bookings"] is None

    def test_slugs(self):
        assert results_feed.slugs_for("PL") == ["eng.1"]
        assert "fifa.friendly" in results_feed.slugs_for("INT-FRI")
        assert results_feed.slugs_for("XYZ") == []


class TestCsv:
    def test_final_scores_with_stats(self, tmp_path):
        results_feed._csv_cache.update(key=None, rows=[])
        d = date.today() - timedelta(days=2)
        (tmp_path / "E0_2627.csv").write_text(
            "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HC,AC,HY,AY,HR,AR\n"
            f"E0,{d:%d/%m/%Y},Arsenal,Chelsea,2,0,H,8,2,1,3,0,1\n")
        [r] = results_feed.csv_results({d.isoformat()}, str(tmp_path))
        assert (r["hg"], r["corners"], r["bookings"], r["source"]) == (2, [8, 2], [1, 5], "football-data.co.uk")
        assert results_feed.csv_results({"2020-01-01"}, str(tmp_path)) == []


class TestAccuracy:
    def test_hit_rates_calibration_and_brier(self):
        days = {"2026-09-24": {}, "2026-09-25": {}}
        for i, (score, d) in enumerate([((2, 0), "2026-09-24"), ((0, 1), "2026-09-24"), ((1, 1), "2026-09-25")]):
            e = {}
            matchday.merge_predictions(e, [pred(home=f"H{i}", d=d, t="12:00")], NOW - timedelta(days=3))
            [entry] = e.values()
            matchday.apply_result(entry, finished(*score))
            days[d][f"k{i}"] = entry
        a = matchday.accuracy(days)
        assert a["matches"] == 3
        assert a["markets"]["tip"] == {"n": 3, "hit_rate": 0.333, "avg_prob": 0.55, "name": matchday.MARKET_NAMES["tip"]}
        assert a["brier"]["vs_bookmaker"]["matches"] == 3 and a["brier"]["model"] > 0
        assert [d["date"] for d in a["daily"]] == ["2026-09-24", "2026-09-25"]
        assert a["leagues"][0]["n"] == 3
        assert sum(b["n"] for b in a["calibration"]) == sum(m["n"] for m in a["markets"].values())

    def test_legacy_entries(self):
        e = matchday.from_legacy({**pred(), "outcome": "lost", "score": "3-0"})
        assert e["grades"]["tip"]["verdict"] == "won" and e["locked"]


# ── Endpoints and the refresh job ────────────────────────────────────────────

@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    return fake


class TestEndpoints:
    def test_past_day_from_the_store(self, redis):
        d = (date.today() - timedelta(days=2)).isoformat()
        day = {}
        matchday.merge_predictions(day, [pred(d=d)], NOW - timedelta(days=10))
        matchday.apply_result(day["arsenal|chelsea"], finished(1, 0))
        main._md_save(redis, d, day)
        got = TestClient(main.app).get(f"/api/matchday?date={d}").json()
        [m] = got["matches"]
        assert (m["status"], m["score"], m["grades"]["tip"]["verdict"]) == ("finished", [1, 0], "won")
        assert got["summary"]["tip"] == [1, 0]

    def test_today_merges_current_predictions(self, redis, monkeypatch):
        today = date.today().isoformat()
        monkeypatch.setattr(main, "_predictions_cache", [pred(d=today, t="23:59")])
        [m] = TestClient(main.app).get(f"/api/matchday?date={today}").json()["matches"]
        assert m["status"] == "scheduled" and m["pred"]["tip_1x2"] == "Arsenal Win"

    def test_range_and_format(self, redis):
        c = TestClient(main.app)
        assert c.get("/api/matchday?date=2020-01-01").status_code == 400
        assert c.get("/api/matchday?date=nope").status_code == 400

    def test_strip_and_accuracy(self, redis, monkeypatch):
        today = date.today()
        monkeypatch.setattr(main, "_predictions_cache", [pred(d=(today + timedelta(days=3)).isoformat())])
        strip = TestClient(main.app).get("/api/matchday/strip").json()
        assert len(strip["days"]) == 22 and strip["days"][7]["date"] == today.isoformat()
        assert strip["days"][10]["total"] == 1
        acc = TestClient(main.app).get("/api/accuracy?days=7").json()
        assert acc["days"] == 7 and acc["matches"] == 0

    def test_save_hook_snapshots(self, redis, monkeypatch):
        d = (date.today() + timedelta(days=2)).isoformat()
        monkeypatch.setattr(main, "_predictions_cache", [pred(d=d)])
        main._save_predictions_cache()
        assert "arsenal|chelsea" in json.loads(redis.kv[f"betiq:md:{d}"])

    def test_open_tickets_show_their_matches_as_they_stand(self, redis):
        import tickets
        d = date.today().isoformat()
        day = {}
        matchday.merge_predictions(day, [pred(d=d, t="00:00")], datetime.now(timezone.utc) - timedelta(days=1))
        matchday.apply_result(day["arsenal|chelsea"], {"status": "live", "minute": "63'", "hg": 2, "ag": 1, "source": "espn",
                                                       "stats": {"shots": [11, 7]},
                                                       "events": [{"minute": "12'", "side": "home", "kind": "goal", "player": "Saka"}]})
        main._md_save(redis, d, day)
        legs = [{"home": "Arsenal", "away": "Chelsea", "date": d, "market": m, "code": c, "label": c}
                for m, c in (("goals_ou", "O25"), ("1x2", "2"), ("sportybet", "x"))]
        t = tickets.new_ticket("LIVE1", legs, [{"status": "booked", "odds": 1.5}] * 3, "slip", None, 3.4, "x")
        main._record_ticket("u1", t)
        [got] = TestClient(main.app).get("/api/user/tickets?uid=u1").json()["tickets"]
        over, away, other = got["legs"]
        assert over["live"]["score"] == [2, 1] and over["live"]["minute"] == "63'"
        assert over["live"]["stats"] == {"shots": [11, 7]} and over["live"]["events"][0]["player"] == "Saka"
        assert (over["live"]["as_it_stands"], away["live"]["as_it_stands"]) == ("won", "lost")
        assert "as_it_stands" not in other["live"]           # a SportyBet-only market we can't judge
        assert over["status"] == "pending"                    # nothing settles before full time
        assert "live" not in json.loads(redis.kv["betiq:user:u1:tickets"])[0]["legs"][0]  # never saved

    def test_settled_tickets_keep_scores_and_other_sports_show_theirs(self, redis):
        import tickets
        d = date.today().isoformat()
        day = {}
        matchday.merge_predictions(day, [pred(d=d, t="00:00")], datetime.now(timezone.utc) - timedelta(days=1))
        matchday.apply_result(day["arsenal|chelsea"], {"status": "finished", "minute": "FT", "hg": 0, "ag": 2,
                                                       "source": "espn", "stats": {"shots": [9, 12]}})
        main._md_save(redis, d, day)
        # A table tennis match in play and a basketball one finished, by their SportyBet events
        main._rkmd_save(redis, "table_tennis", d, {"sr:match:111111114432685": {
            "id": "sr:match:111111114432685", "home": "Kim", "away": "Lee",
            "result": {"status": "live", "score": [1, 0], "periods": [[11, 8], [5, 3]], "minute": "Game 2"}, "pred": {}}})
        main._bbmd_save(redis, d, {"sr:match:72016786": {
            "id": "sr:match:72016786", "home": "Breakers", "away": "Cairns",
            "result": {"status": "finished", "score": [88, 80], "periods": [[20, 18], [22, 20], [24, 22], [22, 20]]},
            "pred": {}}})
        legs = [{"home": "Arsenal", "away": "Chelsea", "date": d, "market": "1x2", "code": "1", "label": "1"},
                {"home": "Kim", "away": "Lee", "date": d, "market": "rk_winner", "code": "1", "label": "Kim",
                 "sb": {"eventId": "sr:match:111111114432685"}},
                {"home": "Breakers", "away": "Cairns", "date": d, "market": "bb_winner", "code": "1", "label": "Breakers",
                 "sb": {"eventId": "sr:match:72016786"}}]
        sel = [{"status": "booked", "odds": 1.5, "sb": l.get("sb")} for l in legs]
        t = tickets.new_ticket("MIX1", legs, sel, "optimizer", None, 3.4, "x")
        t["status"] = "lost"                                  # settled: the scores stay on it
        main._record_ticket("u1", t)
        [got] = TestClient(main.app).get("/api/user/tickets?uid=u1").json()["tickets"]
        fb, tt, bb = got["legs"]
        assert fb["live"]["status"] == "finished" and fb["live"]["score"] == [0, 2] and fb["live"]["stats"] == {"shots": [9, 12]}
        assert tt["live"]["status"] == "live" and tt["live"]["score"] == [1, 0] and tt["live"]["sport"] == "table_tennis"
        assert {r["key"] for r in tt["live"]["rows"]} >= {"points", "games"}      # SportyBet's own event: from the score
        assert bb["live"]["score"] == [88, 80] and bb["live"]["sport"] == "basketball"
        # Older than TICKET_LIVE_DAYS: settled tickets aren't looked up any more
        old = (date.today() - timedelta(days=main.TICKET_LIVE_DAYS + 1)).isoformat()
        items = [{"status": "lost", "legs": [{**legs[0], "date": old}]}]
        main._attach_live(redis, items)
        assert "live" not in items[0]["legs"][0]


class TestSettling:
    def test_a_lost_tickets_other_legs_still_settle_and_no_account_stops_the_rest(self, redis, monkeypatch):
        import tickets
        d = (date.today() - timedelta(days=1)).isoformat()
        day = {}
        matchday.merge_predictions(day, [pred(d=d, t="15:00"), pred(d=d, t="18:00", home="Leeds", away="Hull")],
                                   datetime.now(timezone.utc) - timedelta(days=2))
        matchday.apply_result(day["arsenal|chelsea"], {"status": "finished", "hg": 0, "ag": 1, "source": "espn"})
        matchday.apply_result(day["leeds|hull"], {"status": "finished", "hg": 3, "ag": 1, "source": "espn"})
        main._md_save(redis, d, day)

        def leg(home, away, market, code, status="pending"):
            return {"home": home, "away": away, "date": d, "market": market, "code": code, "label": code, "status": status}
        # Lost on its first leg; the second was never graded
        lost = {"code": "LOST1", "status": "lost", "created_at": d, "legs": [
            leg("Arsenal", "Chelsea", "1x2", "1", "lost"), leg("Leeds", "Hull", "goals_ou", "O25")]}
        # An account whose tickets are all settled (this used to stop the run)
        done = {"code": "DONE1", "status": "won", "created_at": d, "legs": [leg("Leeds", "Hull", "1x2", "1", "won")]}
        redis.set("betiq:user:a1:tickets", json.dumps([done]))
        redis.set("betiq:user:a2:tickets", json.dumps([lost]))
        redis.sadd(main.TICKETS_OPEN_KEY, "a1")          # a2 dropped out of the open set
        monkeypatch.setattr(main, "_tickets_rescan_at", [0.0])
        report = main._settle_tickets(redis)
        assert "errors" not in report and "rescan_added" in report
        got = json.loads(redis.kv["betiq:user:a2:tickets"])[0]
        assert got["status"] == "lost" and [l["status"] for l in got["legs"]] == ["lost", "won"]
        assert main.TICKETS_OPEN_KEY not in redis.sets or "a1" not in redis.sets[main.TICKETS_OPEN_KEY]
        assert redis.h.get(main.TICKETS_STATS_KEY, {}).get("lost") is None   # still the same loss, not counted again
        # A week after its last match, a settled ticket's leftover legs are left alone
        old = (date.today() - timedelta(days=main.SETTLE_LEGS_DAYS + 1)).isoformat()
        assert not main._ticket_needs_settling({"status": "lost", "legs": [{"date": old, "status": "pending"}]}, date.today())
        assert main._ticket_needs_settling({"status": "lost", "legs": [{"date": d, "status": "pending"}]}, date.today())


class TestSettlingFromMatchDays:
    def test_basketball_and_racket_legs_settle_from_the_match_day_score(self, redis, monkeypatch):
        d = (date.today() - timedelta(days=1)).isoformat()
        redis.hmget = lambda key, fields: [None] * len(list(fields))     # SportyBet's results feed: empty
        # Finished on our match days, but never in SportyBet's results feed
        main._bbmd_save(redis, d, {"sr:match:74932474": {
            "id": "sr:match:74932474", "home": "Haukar", "away": "Fjolnir",
            "result": {"status": "finished", "score": [96, 65],
                       "periods": [[38, 10], [17, 13], [21, 19], [20, 23]]}, "pred": {}}})
        main._rkmd_save(redis, "tennis", d, {"sr:match:74983242": {
            "id": "sr:match:74983242", "home": "Giustino", "away": "Donald",
            "result": {"status": "finished", "score": [2, 0], "periods": [[6, 3], [6, 4]]}, "pred": {}}})
        legs = [{"home": "Haukar", "away": "Fjolnir", "date": d, "market": "bb_handicap", "code": "H-15.5",
                 "status": "pending", "sport": "basketball", "event_id": "sr:match:74932474"},
                {"home": "Giustino", "away": "Donald", "date": d, "market": "rk_s1_total", "code": "U10.5",
                 "status": "pending", "sport": "tennis", "event_id": "sr:match:74983242"}]
        redis.set("betiq:user:a3:tickets", json.dumps([{"code": "MD1", "status": "pending", "created_at": d, "legs": legs}]))
        redis.sadd(main.TICKETS_OPEN_KEY, "a3")
        report = main._settle_tickets(redis)
        got = json.loads(redis.kv["betiq:user:a3:tickets"])[0]
        assert "errors" not in report
        # 96-65 covers -15.5; the first set's 6-3 is 9 games, under 10.5
        assert [l["status"] for l in got["legs"]] == ["won", "won"] and got["status"] == "won"


class TestRefresh:
    def test_scores_come_in_and_tickets_settle(self, redis, monkeypatch):
        import curl_cffi.requests as cr
        d = date.today().isoformat()
        started = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%H:%M")
        if started > datetime.now(timezone.utc).strftime("%H:%M"):  # just after midnight: yesterday's
            d = (date.today() - timedelta(days=1)).isoformat()
        day = {}
        matchday.merge_predictions(day, [pred(d=d, t=started)], datetime.now(timezone.utc) - timedelta(hours=4))
        main._md_save(redis, d, day)
        # A ticket on this match
        import tickets
        t = tickets.new_ticket("CODE1", [{"home": "Arsenal", "away": "Chelsea", "date": d, "market": "goals_ou",
                                          "code": "O25", "label": "Over 2.5"}],
                               [{"status": "booked", "odds": 1.9}], "slip", None, 1.9, "x")
        main._record_ticket("u1", t)

        class Session:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
        monkeypatch.setattr(cr, "AsyncSession", Session)

        async def fetch_espn(client, days, slugs):
            return {"results": [{"date": d, "home": "Arsenal", "away": "Chelsea", **finished(3, 1)}],
                    "requests": 1, "errors": []}
        monkeypatch.setattr(results_feed, "fetch_espn", fetch_espn)
        monkeypatch.setattr(results_feed, "csv_results", lambda days: [])
        rep = asyncio.run(main._refresh_matchdays(1, "test"))
        assert rep["updated"] == 1 and rep["tickets"]["settled"] == 1
        e = json.loads(redis.kv[f"betiq:md:{d}"])["arsenal|chelsea"]
        assert e["result"]["hg"] == 3 and e["grades"]["tip"]["verdict"] == "won"
        [saved] = json.loads(redis.kv["betiq:user:u1:tickets"])
        assert saved["status"] == "won" and saved["legs"][0]["status"] == "won"
        assert "u1" not in redis.smembers(main.TICKETS_OPEN_KEY)
        assert redis.hgetall(main.TICKETS_STATS_KEY)["won"] == 1

    def test_live_runs_ask_espn_only_and_settle_on_new_scores(self, redis, monkeypatch):
        import curl_cffi.requests as cr
        d = date.today().isoformat()
        started = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%H:%M")
        if started > datetime.now(timezone.utc).strftime("%H:%M"):
            d = (date.today() - timedelta(days=1)).isoformat()
        day = {}
        matchday.merge_predictions(day, [pred(d=d, t=started)], datetime.now(timezone.utc) - timedelta(hours=2))
        main._md_save(redis, d, day)

        class Session:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
        monkeypatch.setattr(cr, "AsyncSession", Session)
        score = {"hg": 1}

        async def fetch_espn(client, days, slugs):
            return {"results": [{"date": d, "home": "Arsenal", "away": "Chelsea", "status": "live", "minute": 50,
                                 "hg": score["hg"], "ag": 0}], "requests": 1, "errors": []}
        monkeypatch.setattr(results_feed, "fetch_espn", fetch_espn)
        monkeypatch.setattr(results_feed, "csv_results", lambda days: pytest.fail("live runs don't read the CSVs"))
        settles = []
        monkeypatch.setattr(main, "_settle_tickets", lambda r: settles.append(1) or {"settled": 0})
        monkeypatch.setattr(main, "_md_last_settle", [0.0])

        first = asyncio.run(main._refresh_matchdays(1, "live"))
        assert first["updated"] == 1 and len(settles) == 1          # a new score: settle
        again = asyncio.run(main._refresh_matchdays(1, "live"))
        assert again["updated"] == 0 and again["tickets"] == {"skipped": "no new scores"} and len(settles) == 1
        score["hg"] = 2
        asyncio.run(main._refresh_matchdays(1, "live"))
        assert len(settles) == 2                                      # the next goal settles again
        assert main.MD_LIVE_MINUTES == 3


def test_live_stats_and_events_from_an_espn_scoreboard():
    import results_feed as rf
    page = {"events": [{"id": "740001", "date": "2026-10-15T19:00Z", "competitions": [{
        "status": {"type": {"state": "in", "name": "STATUS_SECOND_HALF", "shortDetail": "63'"}},
        "competitors": [
            {"homeAway": "home", "score": "2", "team": {"id": "359", "displayName": "Arsenal"},
             "statistics": [{"name": "possessionPct", "displayValue": "58.4"}, {"name": "totalShots", "displayValue": "11"},
                            {"name": "shotsOnTarget", "displayValue": "5"}, {"name": "wonCorners", "displayValue": "6"},
                            {"name": "yellowCards", "displayValue": "1"}]},
            {"homeAway": "away", "score": "1", "team": {"id": "363", "displayName": "Chelsea"},
             "statistics": [{"name": "possessionPct", "displayValue": "41.6"}, {"name": "totalShots", "displayValue": "7"},
                            {"name": "shotsOnTarget", "displayValue": "2"}, {"name": "wonCorners", "displayValue": "3"},
                            {"name": "yellowCards", "displayValue": "2"}]}],
        "details": [
            {"type": {"text": "Goal"}, "clock": {"displayValue": "12'"}, "team": {"id": "359"}, "scoringPlay": True,
             "athletesInvolved": [{"displayName": "Bukayo Saka"}]},
            {"type": {"text": "Yellow Card"}, "clock": {"displayValue": "30'"}, "team": {"id": "363"}, "yellowCard": True,
             "athletesInvolved": [{"displayName": "Moises Caicedo"}]},
            {"type": {"text": "Penalty - Scored"}, "clock": {"displayValue": "45'+2'"}, "team": {"id": "363"},
             "scoringPlay": True, "penaltyKick": True, "athletesInvolved": [{"displayName": "Cole Palmer"}]},
            {"type": {"text": "Own Goal"}, "clock": {"displayValue": "58'"}, "team": {"id": "363"}, "ownGoal": True,
             "scoringPlay": True, "athletesInvolved": [{"displayName": "Levi Colwill"}]},
            {"type": {"text": "Substitution"}, "clock": {"displayValue": "60'"}, "team": {"id": "359"}}]}]}]}
    [res] = rf.parse_espn(page)
    assert res["status"] == "live" and (res["hg"], res["ag"]) == (2, 1)
    assert res["stats"] == {"possession": [58.4, 41.6], "shots": [11, 7], "sot": [5, 2], "corners": [6, 3],
                            "yellow": [1, 2]}
    assert [(e["minute"], e["side"], e["kind"], e["player"]) for e in res["events"]] == [
        ("12'", "home", "goal", "Bukayo Saka"), ("30'", "away", "yellow", "Moises Caicedo"),
        ("45'+2'", "away", "penalty_goal", "Cole Palmer"), ("58'", "away", "own_goal", "Levi Colwill")]
    # Kept with the match, and shown by the site
    e = {"home": "Arsenal", "away": "Chelsea", "date": "2026-10-15", "time": "19:00", "pred": {}, "result": None}
    assert matchday.apply_result(e, res)
    pub = matchday.public(e)
    assert pub["stats"]["possession"] == [58.4, 41.6] and len(pub["events"]) == 4
    # A later live update with only new stats changes the entry but not the score
    res2 = {**res, "stats": {**res["stats"], "shots": [12, 7]}}
    assert matchday.apply_result(e, res2) and e["result"]["hg"] == 2


class TestNeverStuck:
    """A run that hangs (a dropped connection, a slow source) is given up,
    so the 3-minute live runs keep coming instead of queueing behind it."""

    def test_a_hung_run_is_given_up_and_says_where(self, monkeypatch):
        async def hang(days_back, trigger):
            main._md_status["stage"] = "asking ESPN (2 scoreboards)"
            await asyncio.sleep(3600)
        monkeypatch.setattr(main, "_refresh_matchdays", hang)
        monkeypatch.setitem(main.MD_RUN_LIMIT, "live", 0.05)
        asyncio.run(main._matchday_live())
        assert main._md_status["error"] == "timed out at asking ESPN (2 scoreboards)"
        assert main._md_status["failed_at"]

    def test_slow_ticket_settling_doesnt_hold_up_scores(self, redis, monkeypatch):
        import threading
        release = threading.Event()
        monkeypatch.setattr(main, "_settle_tickets", lambda r: release.wait(0.5) or {"settled": 0})
        real_wait_for = asyncio.wait_for

        async def quick(aw, timeout):
            return await real_wait_for(aw, 0.05 if timeout == 90 else timeout)
        monkeypatch.setattr(asyncio, "wait_for", quick)
        monkeypatch.setattr(main, "_md_last_settle", [0.0])
        rep = asyncio.run(main._refresh_matchdays(1, "live"))
        release.set()
        assert "over 90s" in rep["tickets"]["error"]
        assert main._md_status["stage"] == "done" and main._md_status["at"]

    def test_redis_calls_have_time_limits(self, monkeypatch):
        import redis as redis_lib
        seen = {}
        monkeypatch.setattr(main, "_redis", None)
        monkeypatch.setattr(main, "REDIS_URL", "redis://example:6379")

        class Fake:
            def ping(self): return True
        monkeypatch.setattr(redis_lib, "from_url", lambda url, **kw: seen.update(kw) or Fake())
        main._get_redis()
        monkeypatch.setattr(main, "_redis", None)
        assert seen["socket_timeout"] and seen["socket_keepalive"] and seen["health_check_interval"]


def af_fixture(home, away, short, gh=None, ga=None, elapsed=None, when="2026-09-27T04:00:00+00:00", ft=None):
    return {"fixture": {"date": when, "status": {"short": short, "elapsed": elapsed}},
            "teams": {"home": {"name": home}, "away": {"name": away}},
            "goals": {"home": gh, "away": ga}, "score": {"fulltime": ft or {"home": gh, "away": ga}}}


class TestApiFootballBackup:
    """ESPN lists some small friendlies but never scores them: API-Football
    fills those in, sparingly (a small daily share of the free plan)."""

    def test_parse(self):
        got = results_feed.parse_api_football({"response": [
            af_fixture("Cook Islands", "Tahiti", "FT", 0, 3),
            af_fixture("Fiji", "Papua New Guinea", "2H", 1, 0, elapsed=67),
            af_fixture("China", "New Zealand", "HT", 0, 0),
            af_fixture("Mali", "Togo", "AET", 2, 1, ft={"home": 1, "away": 1}),
            af_fixture("Seychelles", "Sri Lanka", "NS"),
            af_fixture("Chad", "Niger", "PST")]})
        by = {r["home"]: r for r in got}
        assert (by["Cook Islands"]["status"], by["Cook Islands"]["hg"], by["Cook Islands"]["ag"]) == ("finished", 0, 3)
        assert (by["Fiji"]["status"], by["Fiji"]["minute"]) == ("live", "67'") and by["China"]["minute"] == "HT"
        assert (by["Mali"]["hg"], by["Mali"]["ag"], by["Mali"]["aet"]) == (1, 1, True)   # 90-minute score
        assert "Seychelles" not in by and by["Chad"]["status"] == "postponed"
        assert by["Cook Islands"]["date"] == "2026-09-27" and by["Cook Islands"]["source"] == "api-football"

    def test_scheduled_never_wipes_a_live_score(self):
        e = TestGrading().entry()
        matchday.apply_result(e, {"status": "live", "minute": "30'", "hg": 1, "ag": 0, "source": "api-football"})
        assert not matchday.apply_result(e, {"status": "scheduled", "source": "espn"})
        assert e["result"]["hg"] == 1 and e["result"]["status"] == "live"

    def test_unscored_matches_get_the_backup(self, redis, monkeypatch):
        import curl_cffi.requests as cr
        d = date.today().isoformat()
        early = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%H:%M")
        if early > datetime.now(timezone.utc).strftime("%H:%M"):
            d = (date.today() - timedelta(days=1)).isoformat()
        day = {}
        matchday.merge_predictions(day, [pred("Cook Islands", "Tahiti", d=d, t=early),
                                         pred("Arsenal", "Chelsea", d=d, t=early)],
                                   datetime.now(timezone.utc) - timedelta(hours=4))
        main._md_save(redis, d, day)

        class Session:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
        monkeypatch.setattr(cr, "AsyncSession", Session)

        async def fetch_espn(client, days, slugs):   # ESPN: Arsenal final, Cook Islands never started
            return {"results": [{"date": d, "home": "Arsenal", "away": "Chelsea", **finished(2, 0)},
                                {"date": d, "home": "Cook Islands", "away": "Tahiti", "status": "scheduled", "source": "espn"}],
                    "requests": 1, "errors": []}
        asked = []

        async def fetch_af(client, day, key):
            asked.append(day)
            return results_feed.parse_api_football({"response": [
                af_fixture("Cook Islands", "Tahiti", "FT", 0, 3, when=f"{d}T{early}:00+00:00")]}), None
        monkeypatch.setattr(results_feed, "fetch_espn", fetch_espn)
        monkeypatch.setattr(results_feed, "fetch_api_football", fetch_af)
        monkeypatch.setattr(main, "_af_live_last", [0.0])
        monkeypatch.setenv("APIFOOTBALL_KEY", "k")

        rep = asyncio.run(main._refresh_matchdays(1, "live"))
        saved = json.loads(redis.kv[f"betiq:md:{d}"])
        assert saved["cook islands|tahiti"]["result"]["hg"] == 0 and saved["cook islands|tahiti"]["result"]["ag"] == 3
        assert saved["arsenal|chelsea"]["result"]["hg"] == 2
        assert rep["backup"]["requests"] == 1 and rep["backup"]["unscored"] == 1 and asked == [date.fromisoformat(d)]
        # Everything scored now: no more backup requests
        rep = asyncio.run(main._refresh_matchdays(1, "live"))
        assert "backup" not in rep and len(asked) == 1

    def test_backup_is_rationed(self, redis, monkeypatch):
        stale = {"a|b": {"date": "2026-09-27"}}
        monkeypatch.delenv("APIFOOTBALL_KEY", raising=False)
        assert "APIFOOTBALL_KEY" in asyncio.run(main._backup_results(redis, stale))[1]["skipped"]
        monkeypatch.setenv("APIFOOTBALL_KEY", "k")
        monkeypatch.setattr(main, "_af_live_last", [time.time()])
        assert asyncio.run(main._backup_results(redis, stale))[1]["skipped"] == "asked recently"
        monkeypatch.setattr(main, "_af_live_last", [0.0])
        redis.kv[f"betiq:af:live:{date.today().isoformat()}"] = str(main.AF_LIVE_DAILY_CAP)
        assert "used today's" in asyncio.run(main._backup_results(redis, stale))[1]["skipped"]
