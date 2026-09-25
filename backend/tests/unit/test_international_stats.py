"""
International corners/cards collection (international_stats.py) and how the
international model feeds predictions (main._international_set_pieces).
"""

import asyncio
from datetime import date, datetime, timezone

import pandas as pd
import pytest

import international_stats as ist
import main
import set_pieces


def sofa_day(events):
    return {"events": events}


def sofa_event(eid, home, away, day="2025-03-21", status="finished", comp="World Cup Qualification"):
    ts = int(datetime.fromisoformat(f"{day}T18:00:00+00:00").timestamp())
    return {"id": eid, "homeTeam": {"name": home, "national": True}, "awayTeam": {"name": away, "national": True},
            "tournament": {"uniqueTournament": {"id": 11, "name": comp}}, "startTimestamp": ts,
            "status": {"type": status}}


def sofa_stats(hc, ac, hy, ay, reds=None):
    items = [{"key": "cornerKicks", "name": "Corner kicks", "homeValue": hc, "awayValue": ac},
             {"key": "yellowCards", "name": "Yellow cards", "homeValue": hy, "awayValue": ay}]
    if reds:
        items.append({"key": "redCards", "name": "Red cards", "homeValue": reds[0], "awayValue": reds[1]})
    return {"statistics": [{"period": "1ST", "groups": []}, {"period": "ALL", "groups": [{"statisticsItems": items}]}]}


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class Session:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, params))
        for key, reply in self.routes.items():
            if key in url and (not isinstance(reply, dict) or "__params" not in reply or reply["__params"] == params):
                return Resp(*reply) if isinstance(reply, tuple) else Resp(200, reply)
        return Resp(404, {})


class TestParsing:
    def test_sofascore_whole_match_and_missing_reds(self):
        assert ist.parse_sofa_stats(sofa_stats(7, 2, 1, 3)) == {"HC": 7, "AC": 2, "HY": 1, "AY": 3, "HR": 0, "AR": 0}
        assert ist.parse_sofa_stats(sofa_stats(7, 2, 1, 3, (0, 1)))["AR"] == 1
        assert ist.parse_sofa_stats({"statistics": []}) is None

    def test_api_football(self):
        fixtures = {"response": [
            {"fixture": {"id": 5}, "league": {"country": "World", "name": "World Cup - Qualification Europe"},
             "teams": {"home": {"id": 1, "name": "Spain"}, "away": {"id": 2, "name": "Malta"}}},
            {"fixture": {"id": 6}, "league": {"country": "England", "name": "Premier League"},
             "teams": {"home": {"id": 3, "name": "A"}, "away": {"id": 4, "name": "B"}}}]}
        assert [f["id"] for f in ist.parse_af_fixtures(fixtures)] == [5]
        stats = {"response": [
            {"team": {"id": 2}, "statistics": [{"type": "Corner Kicks", "value": 1}, {"type": "Yellow Cards", "value": 3},
                                               {"type": "Red Cards", "value": None}]},
            {"team": {"id": 1}, "statistics": [{"type": "Corner Kicks", "value": 11}, {"type": "Yellow Cards", "value": 0},
                                               {"type": "Red Cards", "value": 1}]}]}
        assert ist.parse_af_stats(stats, 1, 2) == {"HC": 11, "AC": 1, "HY": 0, "AY": 3, "HR": 1, "AR": 0}


class TestCollect:
    def test_days_newest_first_and_resume(self):
        data = {**ist.empty(), "sofa_days": ["2025-03-20"]}
        days = ist.days_to_scan(data, date(2025, 3, 25))
        assert days[:3] == [date(2025, 3, 24), date(2025, 3, 23), date(2025, 3, 22)]
        assert date(2025, 3, 20) not in days and days[-1] == ist.START

    def test_sofascore_rows_and_missing(self):
        data = ist.empty()
        session = Session({
            "scheduled-events/2025-03-22": sofa_day([sofa_event(1, "Spain", "Malta", "2025-03-22"),
                                                     sofa_event(2, "Chad", "Mali", "2025-03-22"),
                                                     sofa_event(3, "Peru", "Chile", "2025-03-22", status="notstarted")]),
            "event/1/statistics": sofa_stats(12, 1, 0, 2),
            "event/2/statistics": (404, {}),
        })
        rep = asyncio.run(ist.collect_sofascore(session, data, deadline=float("inf"), today=date(2025, 3, 23), pause=0))
        assert rep["matches"] == 1 and rep["no_stats"] == 1
        [row] = data["rows"].values()
        assert (row["home"], row["HC"], row["source"]) == ("Spain", 12, "sofascore")
        assert [m["home"] for m in data["missing"]] == ["Chad"]

    def test_stops_when_blocked(self):
        data = ist.empty()
        session = Session({"scheduled-events": (403, {})})
        rep = asyncio.run(ist.collect_sofascore(session, data, float("inf"), date(2025, 3, 23), pause=0))
        assert rep["stopped"] == "HTTP 403" and data["sofa_days"] == []

    def test_api_football_fills_gaps_within_budget(self):
        data = {**ist.empty(), "missing": [{"key": ist.match_key("2025-03-22", "Chad", "Mali"), "date": "2025-03-22",
                                           "home": "Chad", "away": "Mali", "competition": "WCQ"}]}
        session = Session({
            "/fixtures/statistics": {"response": [
                {"team": {"id": 1}, "statistics": [{"type": "Corner Kicks", "value": 4}, {"type": "Yellow Cards", "value": 2}]},
                {"team": {"id": 2}, "statistics": [{"type": "Corner Kicks", "value": 3}, {"type": "Yellow Cards", "value": 1}]}]},
            "/fixtures": {"response": [{"fixture": {"id": 9}, "league": {"country": "World", "name": "WCQ Africa"},
                                        "teams": {"home": {"id": 1, "name": "Chad"}, "away": {"id": 2, "name": "Mali"}}}]},
        })
        rep = asyncio.run(ist.collect_api_football(session, data, "k", budget=5))
        assert rep == {"requests": 2, "matches": 1, "stopped": None}
        assert list(data["rows"].values())[0]["source"] == "api-football" and data["missing"] == []
        # Out of budget: nothing marked as tried, so the next run tries again
        data2 = {**ist.empty(), "missing": [{"key": "k2", "date": "2025-03-23", "home": "A", "away": "B", "competition": ""}]}
        rep2 = asyncio.run(ist.collect_api_football(Session({}), data2, "k", budget=0))
        assert rep2["stopped"] == "budget" and data2["af_tried"] == []

    def test_storage_round_trip(self):
        class R:
            def __init__(self): self.v = None
            def get(self, k): return self.v
            def set(self, k, v): self.v = v
        r, data = R(), {**ist.empty(), "rows": {"k": {"date": "2025-03-22", "home": "Spain", "away": "Malta",
                                                       "competition": "World Cup Qualification", "source": "sofascore",
                                                       "HC": 12, "AC": 1, "HY": 0, "AY": 2, "HR": 0, "AR": 0}}}
        assert ist.save(r, data) < 1000
        back = ist.load(r)
        assert back["rows"] == data["rows"]
        frame = ist.rows_frame(back)
        assert frame.iloc[0]["league"] == "INT-WCQ" and frame.iloc[0]["HC"] == 12


def club_event(eid, home, away, day, tournament=17, status="finished"):
    ev = sofa_event(eid, home, away, day, status)
    ev["tournament"] = {"uniqueTournament": {"id": tournament, "name": "League"}}
    ev["homeTeam"]["national"] = ev["awayTeam"]["national"] = False
    return ev


def referee_page(name, games=100, yellow=400, red=10, yellow_red=2):
    return {"event": {"referee": {"name": name, "games": games, "yellowCards": yellow,
                                  "redCards": red, "yellowRedCards": yellow_red}}}


class TestReferees:
    def test_parse_referee(self):
        assert ist.parse_sofa_referee(referee_page("Anthony Taylor")) == {
            "name": "Anthony Taylor", "games": 100, "yellow": 400, "red": 12}
        assert ist.parse_sofa_referee({"event": {}}) is None
        assert ist.parse_sofa_referee(None) is None

    def test_api_football_referee_drops_the_country(self):
        fixtures = {"response": [{"fixture": {"id": 5, "referee": "C. Turpin, France"},
                                  "league": {"country": "World", "name": "Friendlies"},
                                  "teams": {"home": {"id": 1, "name": "Spain"}, "away": {"id": 2, "name": "Malta"}}},
                                 {"fixture": {"id": 6, "referee": None}, "league": {"country": "World", "name": "Friendlies"},
                                  "teams": {"home": {"id": 3, "name": "A"}, "away": {"id": 4, "name": "B"}}}]}
        assert [f["referee"] for f in ist.parse_af_fixtures(fixtures)] == ["C. Turpin", None]

    def test_collects_international_and_club_referees(self):
        data = ist.empty()
        session = Session({
            "event/1/statistics": sofa_stats(12, 1, 0, 2),
            "event/1": referee_page("Clément Turpin"),
            "event/7": referee_page("Anthony Taylor", games=300),
            "event/8": {"event": {}},
            "scheduled-events/2025-03-22": sofa_day([
                sofa_event(1, "Spain", "Malta", "2025-03-22"),
                club_event(7, "Arsenal", "Chelsea", "2025-03-22"),
                club_event(8, "Burnley", "Leeds United", "2025-03-22", tournament=18),
                club_event(9, "Boca", "River", "2025-03-22", tournament=155),        # not our league
                club_event(10, "Everton", "Fulham", "2025-03-22", status="notstarted")]),
        })
        rep = asyncio.run(ist.collect_sofascore(session, data, float("inf"), date(2025, 3, 23), pause=0))
        [row] = data["rows"].values()
        assert row["referee"] == "Clément Turpin"
        assert data["club_refs"] == {"2025-03-22|Arsenal|Chelsea": "Anthony Taylor", "2025-03-22|Burnley|Leeds United": ""}
        assert rep["club_referees"] == 1
        assert data["referees"]["Anthony Taylor"]["games"] == 300
        assert not any("event/9" in url or "event/10" in url for url, _ in session.calls)
        assert ist.rows_frame(data).iloc[0]["Referee"] == "Clément Turpin"
        s = ist.summary(data)
        assert (s["with_referee"], s["club_referees"], s["referees_known"]) == (1, 1, 2)

    def test_old_days_skip_club_referees(self):
        data = ist.empty()
        session = Session({"scheduled-events/2020-03-22": sofa_day([club_event(7, "Arsenal", "Chelsea", "2020-03-22")])})
        asyncio.run(ist.collect_sofascore(session, {**data, "sofa_days": []}, float("inf"), date(2020, 3, 23), pause=0))
        assert not any("event/7" in url for url, _ in session.calls)

    def test_known_matches_are_not_asked_again(self):
        data = {**ist.empty(), "club_refs": {"2025-03-22|Arsenal|Chelsea": ""}}
        session = Session({})
        n = asyncio.run(ist._club_referees(session, sofa_day([club_event(7, "Arsenal", "Chelsea", "2025-03-22")]), data, 0))
        assert n == 0 and session.calls == []


class TestClubReferees:
    def history(self):
        return pd.DataFrame([
            {"Date": pd.Timestamp("2025-03-22"), "HomeTeam": "Man United", "AwayTeam": "Man City", "Referee": None, "league": "PL"},
            {"Date": pd.Timestamp("2025-03-22"), "HomeTeam": "Real Madrid", "AwayTeam": "Getafe", "Referee": None, "league": "PD"},
            {"Date": pd.Timestamp("2025-03-23"), "HomeTeam": "Arsenal", "AwayTeam": "Chelsea", "Referee": "M Oliver", "league": "PL"},
            {"Date": pd.Timestamp("2020-03-22"), "HomeTeam": "Lazio", "AwayTeam": "Roma", "Referee": None, "league": "SA"},
        ])

    def test_fills_missing_referees_by_date_and_teams(self):
        refs = {"2025-03-21|Real Madrid|Getafe": "José Munuera",          # a day off: UTC date
                "2025-03-22|Manchester City|Manchester United": "Wrong",  # the reverse fixture
                "2025-03-23|Arsenal|Chelsea": "Someone Else",             # CSV already names one
                "2020-03-22|Lazio|Roma": "Too Early",
                "2025-03-22|Burnley|Leeds": ""}
        out = ist.add_club_referees(self.history(), refs)
        assert [r if isinstance(r, str) else None for r in out["Referee"]] == [None, "José Munuera", "M Oliver", None]
        # Short names match SofaScore's full ones
        out = ist.add_club_referees(self.history(), {"2025-03-22|Manchester United|Manchester City": "Simon Hooper"})
        assert out.loc[0, "Referee"] == "Simon Hooper"

    def test_no_data_leaves_history_alone(self):
        h = self.history()
        assert ist.add_club_referees(h, {}) is h
        no_col = h.drop(columns=["Referee"])
        out = ist.add_club_referees(no_col, {"2025-03-22|Real Madrid|Getafe": "José Munuera"})
        assert out["Referee"].tolist()[1] == "José Munuera"

    def test_pipeline_helper_survives_redis_errors(self, monkeypatch):
        import model_store
        monkeypatch.setattr(model_store, "_client", lambda: (_ for _ in ()).throw(RuntimeError("no redis")))
        h = self.history()
        assert main._with_club_referees(h) is h


class TestPredictions:
    def test_only_approved_stats_are_used(self, monkeypatch):
        class Model:
            def markets(self, home, away, league, referee=None, career=None):
                return {"corners": {"over": {"9.5": 0.5}}, "bookings": {"over": {"4.5": 0.4}},
                        "corners_home": {"over": {}}, "corners_away": {"over": {}}, "corners_1x2": {"home": 0.6}}
        monkeypatch.setattr(main, "_intl_set_pieces", Model())
        monkeypatch.setattr(main, "_intl_sp_info", {"check": {"use": {"corners": True, "bookings": False,
                                                                      "corners_home": True, "corners_away": False}}})
        got = main._international_set_pieces({"home": "Spain", "away": "Malta", "league": "INT-WCQ"})
        assert set(got) == {"corners", "corners_home"}  # no most-corners without both teams' lines

    def test_sportybet_lines_fill_what_the_model_left_out(self):
        event = {"markets": [{"id": "139", "specifier": "total=4.5", "desc": "Total Bookings",
                              "outcomes": [{"id": "12", "odds": "1.9"}, {"id": "13", "odds": "1.9"}]}]}
        p = main._with_priced_set_pieces({"set_pieces": {"corners": {"over": {"9.5": 0.5}}}}, event)
        assert set(p["set_pieces"]) == {"corners", "bookings"} and p["set_pieces"]["bookings"]["source"] == "sportybet"


class TestTuning:
    def test_needs_enough_data(self):
        r = set_pieces.tune_international(pd.DataFrame())
        assert r["use"] == {} and "not enough" in r["reason"]


def espn_board(*events):
    return {"events": list(events)}


def espn_match(home, away, day, stats=True):
    team = lambda tid, name, corners: {"id": tid, "displayName": name}
    comp = {"competitors": [
        {"homeAway": "home", "score": "2", "team": team("1", home, 0),
         "statistics": [{"name": "wonCorners", "displayValue": "6"}] if stats else []},
        {"homeAway": "away", "score": "0", "team": team("2", away, 0),
         "statistics": [{"name": "wonCorners", "displayValue": "2"}] if stats else []}],
        "details": [{"yellowCard": True, "team": {"id": "2"}}, {"redCard": True, "team": {"id": "2"}}] if stats else None,
        "status": {"type": {"state": "post", "name": "STATUS_FULL_TIME", "completed": True}}}
    if not stats:
        comp.pop("details")
    return {"date": f"{day}T19:00Z", "competitions": [comp]}


class TestEspn:
    def test_months_newest_first_and_recheck(self):
        data = {**ist.empty(), "espn_months": ["fifa.friendly|2025-01"]}
        months = ist.espn_months(data, date(2025, 3, 10))
        keys = [k for k, *_ in months]
        assert keys[0].endswith("|2025-03") and keys[-1].endswith("|2019-01")
        assert "fifa.friendly|2025-01" not in keys           # done and not recent
        first = months[0]
        assert (first[2], first[3]) == (date(2025, 3, 1), date(2025, 3, 9))  # up to yesterday

    def test_collects_rows_and_queues_the_rest(self):
        data = ist.empty()
        session = Session({
            "fifa.friendly/scoreboard": {"__params": {"dates": "20250301-20250309", "limit": 1000},
                                         "events": [espn_match("Spain", "Malta", "2025-03-05"),
                                                    espn_match("Chad", "Mali", "2025-03-06", stats=False)]},
        })
        rep = asyncio.run(ist.collect_espn(session, data, float("inf"), date(2025, 3, 10), pause=0))
        [row] = data["rows"].values()
        assert (row["source"], row["HC"], row["AC"], row["HY"], row["AY"]) == ("espn", 6, 2, 0, 3)
        assert rep["matches"] == 1 and rep["no_stats"] == 1
        assert [m["home"] for m in data["missing"]] == ["Chad"]
        assert "fifa.friendly|2025-03" in data["espn_months"]
        frame = ist.rows_frame(data)
        assert frame.iloc[0]["league"] == "INT-FRI" or frame.iloc[0]["league"].startswith("INT")
        # A second run doesn't add the same match twice
        asyncio.run(ist.collect_espn(session, data, float("inf"), date(2025, 3, 10), pause=0))
        assert len(data["rows"]) == 1 and len(data["missing"]) == 1

    def test_stops_when_blocked(self):
        data = ist.empty()
        rep = asyncio.run(ist.collect_espn(Session({"scoreboard": (403, {})}), data, float("inf"),
                                           date(2025, 3, 10), pause=0))
        assert rep["stopped"] == "HTTP 403" and rep["requests"] == 1 and data["espn_months"] == []

    def test_summary_counts_espn_progress(self):
        slugs = list(ist.intl.ESPN_COMPETITIONS)
        data = {**ist.empty(), "espn_months": [f"{s}|2019-01" for s in slugs]}
        s = ist.summary(data)
        assert s["oldest_day_scanned"] == "2019-01-01" and s["backfill_complete"]
