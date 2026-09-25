"""
Match days (matchday.py): pre-match snapshots locked at kick-off, results
from ESPN and the league CSVs (results_feed.py), grading and the track
record; and the endpoints and refresh job in main.py.
"""

import asyncio
import json
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
