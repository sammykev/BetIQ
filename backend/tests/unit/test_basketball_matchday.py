"""
Basketball match days (basketball_matchday.py): each game's prediction kept
until tip-off, its live score, the final graded, and the strip and day
endpoints the basketball tab reads.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import basketball_data as bd
import basketball_matchday as bbmd
import main
from tests.unit.test_user_endpoints import FakeRedis

NOW = datetime(2026, 9, 29, 18, 0, tzinfo=timezone.utc)


def pred(eid="sr:match:1", hours=2, home="Real Madrid", away="Barcelona", **kw):
    ko = NOW + timedelta(hours=hours)
    return {"sportybet_event_id": eid, "home": home, "away": away, "date": ko.strftime("%Y-%m-%d"),
            "time": ko.strftime("%H:%M"), "league": "Spain · ACB", "league_name": "ACB", "flag": "🇪🇸",
            "p_home": 0.7, "p_away": 0.3, "tip_1x2": "Real Madrid Win", "tip_code": "1", "tip_confidence": 0.7,
            "tip_goals": "Over 160.5", "p_over_line": 0.62, "total_line": 160.5,
            "bb_markets": [
                {"market": "bb_handicap", "family": "bb_handicap", "market_name": "Handicap", "code": "H+4.5",
                 "label": "Real Madrid +4.5", "prob": 0.86, "odds": 1.22},
                {"market": "bb_overtime", "family": "bb_overtime", "market_name": "Overtime", "code": "OT-N",
                 "label": "No overtime", "prob": 0.94, "odds": 1.04},
                {"market": "bb_total", "family": "bb_total", "market_name": "Total", "code": "O150.5",
                 "label": "Over 150.5 points", "prob": 0.9, "odds": 1.10}], **kw}


class TestStore:
    def test_prediction_kept_until_tip_off(self):
        day: dict = {}
        assert bbmd.merge_predictions(day, [pred()], NOW)
        e = day["sr:match:1"]
        assert not e["locked"] and e["pred"]["tip_code"] == "1"
        # Best line: likeliest at 1.15+, never overtime (1.04) or a 1.10 line
        assert e["pred"]["best"]["code"] == "H+4.5"
        assert bbmd.merge_predictions(day, [pred(tip_confidence=0.72)], NOW)
        assert e["pred"]["tip_confidence"] == 0.72
        # Tipped off: the prediction is frozen
        later = NOW + timedelta(hours=3)
        bbmd.merge_predictions(day, [pred(tip_confidence=0.5)], later)
        assert e["locked"] and e["pred"]["tip_confidence"] == 0.72

    def test_final_graded(self):
        day: dict = {}
        bbmd.merge_predictions(day, [pred()], NOW)
        e = day["sr:match:1"]
        game = {"id": "sr:match:1", "hs": 85, "as": 82, "q": [[20, 20], [22, 20], [21, 22], [22, 20]], "ot": False}
        assert bbmd.apply_result(e, game)
        g = e["grades"]
        assert g["tip"]["verdict"] == "won" and g["points"]["verdict"] == "won" and g["best"]["verdict"] == "won"
        assert not bbmd.apply_result(e, game)                 # nothing new
        m = bbmd.public(e)
        assert m["status"] == "finished" and m["score"] == [85, 82] and m["pred"]["goals_confidence"] == 0.62
        s = bbmd.day_summary(day.values())
        assert s["total"] == 1 and s["finished"] == 1 and s["tip"] == [1, 0] and s["points"] == [1, 0]

    def test_live_then_final_never_back(self):
        day: dict = {}
        bbmd.merge_predictions(day, [pred(hours=-1)], NOW)
        e = day["sr:match:1"]
        assert bbmd.needs_result(e, NOW)
        assert bbmd.apply_live(e, {"score": [40, 38], "periods": [[20, 18], [20, 20]], "minute": "HT"})
        assert bbmd.public(e)["status"] == "live" and bbmd.day_summary([e])["live"] == 1
        # Checked and no longer in play: ended, final to come
        assert bbmd.stale_live(e, {}, {"sr:match:1"})
        assert bbmd.public(e)["status"] == "scheduled"
        # Not checked (SportyBet didn't answer): left as it was
        bbmd.apply_live(e, {"score": [50, 48], "periods": None, "minute": "Q3 05:00"})
        assert not bbmd.stale_live(e, {}, set())
        bbmd.apply_result(e, {"id": "sr:match:1", "hs": 80, "as": 90, "q": None, "ot": False})
        assert not bbmd.apply_live(e, {"score": [1, 1], "periods": None, "minute": "Q1"})
        assert e["grades"]["tip"]["verdict"] == "lost"
        assert not bbmd.needs_result(e, NOW)


class TestLiveParse:
    def test_in_play(self):
        x = bd.parse_live({"eventId": "sr:match:9", "homeTeamName": "A", "awayTeamName": "B", "status": 1,
                           "matchStatus": "2nd quarter", "setScore": "40:38", "gameScore": ["20:18", "20:20"],
                           "remainingTimeInPeriod": "04:12"})
        assert x == {"id": "sr:match:9", "score": [40, 38], "periods": [[20, 18], [20, 20]], "minute": "Q2 04:12"}
        assert bd.parse_live({"eventId": "1", "matchStatus": "Halftime", "setScore": "40:38"})["minute"] == "HT"

    def test_not_in_play(self):
        assert bd.parse_live({"eventId": "1", "matchStatus": "Ended", "setScore": "80:78"}) is None
        assert bd.parse_live({"eventId": "1", "matchStatus": "Not started", "setScore": "0:0"}) is None
        assert bd.parse_live({"eventId": "1", "matchStatus": "1st quarter"}) is None


@pytest.fixture
def api(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)

    async def open_to_all(request, sport):
        return None
    monkeypatch.setattr(main, "_check_sport_access", open_to_all)
    main._bb_strip_cache.clear()
    return fake


def test_tick_grades_finals_and_endpoints_show_them(api, monkeypatch):
    now = datetime.now(timezone.utc)
    started = pred(eid="sr:match:1", hours=0)
    started["date"], started["time"] = (now - timedelta(hours=3)).strftime("%Y-%m-%d"), (now - timedelta(hours=3)).strftime("%H:%M")
    playing = pred(eid="sr:match:2", home="Olympiacos", away="Panathinaikos")
    playing["date"], playing["time"] = (now - timedelta(minutes=40)).strftime("%Y-%m-%d"), (now - timedelta(minutes=40)).strftime("%H:%M")
    later = pred(eid="sr:match:3", home="Fenerbahce", away="Efes")
    later["date"], later["time"] = (now + timedelta(days=2)).strftime("%Y-%m-%d"), "18:00"
    for p in (started, playing, later):
        day = main._bbmd_load(api, p["date"])
        bbmd.merge_predictions(day, [p], now - timedelta(hours=4))
        main._bbmd_save(api, p["date"], day)

    async def no_fetch(d):
        return []

    async def live(ids, **kw):
        return ({"sr:match:2": {"id": "sr:match:2", "score": [30, 25], "periods": None, "minute": "Q2 03:00"}},
                "test", set(ids))
    monkeypatch.setattr(bd, "fetch_live", live)
    monkeypatch.setattr(main, "_bb_results", lambda r, days=None: [
        {"id": "sr:match:1", "hs": 90, "as": 70, "q": [[20, 20], [25, 15], [25, 15], [20, 20]], "ot": False}])
    monkeypatch.setattr(main, "_bb_upcoming", lambda: [later])
    got = asyncio.run(main._bb_live_tick())
    assert got["open"] == 2 and got["playing"] == 1

    c = TestClient(main.app)
    day = c.get(f"/api/basketball/matchday?date={started['date']}").json()
    by = {m["id"]: m for m in day["matches"]}
    assert by["sr:match:1"]["status"] == "finished" and by["sr:match:1"]["grades"]["tip"]["verdict"] == "won"
    if playing["date"] == started["date"]:
        assert by["sr:match:2"]["status"] == "live" and by["sr:match:2"]["minute"] == "Q2 03:00"
    strip = c.get("/api/basketball/matchday/strip").json()
    assert len(strip["days"]) == main.MD_DAYS_BACK + main.MD_DAYS_AHEAD + 1
    days = {d["date"]: d for d in strip["days"]}
    assert days[later["date"]]["total"] == 1
    assert days[started["date"]]["tip"][0] >= 1
    assert c.get("/api/basketball/matchday?date=2020-01-01").status_code == 400


def test_live_reads_games_the_listing_left_out(monkeypatch):
    """SportyBet's live list may leave some of ours out: those are read from
    their own pages, not taken as over."""
    import sportybet
    pages = []

    async def request(session, method, path, **kw):
        if path == "/factsCenter/event":
            pages.append(kw["params"]["eventId"])
            return {"data": {"eventId": kw["params"]["eventId"], "matchStatus": "3rd quarter", "status": 1,
                             "setScore": "60:58", "gameScore": ["20:18", "22:20", "18:20"]}}
        return {"data": [{"eventId": "sr:match:1", "homeTeamName": "A", "awayTeamName": "B", "matchStatus": "2nd quarter",
                          "status": 1, "setScore": "30:25", "gameScore": ["18:12", "12:13"]},
                         {"eventId": "sr:match:9", "homeTeamName": "C", "awayTeamName": "D", "matchStatus": "1st quarter",
                          "status": 1, "setScore": "5:4"}]}
    monkeypatch.setattr(sportybet, "_request", request)
    monkeypatch.setattr(sportybet, "shared_session", lambda: None)
    live, how, checked = asyncio.run(bd.fetch_live(["sr:match:1", "sr:match:2"]))
    assert set(live) == {"sr:match:1", "sr:match:2"} and pages == ["sr:match:2"]
    assert live["sr:match:2"]["score"] == [60, 58] and checked == {"sr:match:1", "sr:match:2"}
    assert "1 of ours" in how and "event pages: 1 read" in how
