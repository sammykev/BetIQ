"""
European competitions and domestic cups from ESPN (club_cups.py): calendar,
rows for training, collection, the check, and the merge into training.
"""

import asyncio
from datetime import date

import pandas as pd

import club_cups as cc
import main


def ev(home, away, day, hs="2", as_="1", stats=True, name="STATUS_FULL_TIME"):
    stat = lambda c, s, st: [{"name": "wonCorners", "displayValue": c}, {"name": "totalShots", "displayValue": s},
                             {"name": "shotsOnTarget", "displayValue": st}] if stats else []
    return {"date": f"{day}T20:00Z", "competitions": [{
        "competitors": [{"homeAway": "home", "score": hs, "team": {"id": "1", "displayName": home}, "statistics": stat("7", "15", "6")},
                        {"homeAway": "away", "score": as_, "team": {"id": "2", "displayName": away}, "statistics": stat("3", "8", "2")}],
        "details": [{"yellowCard": True, "team": {"id": "2"}}] if stats else None,
        "status": {"type": {"state": "post", "name": name, "completed": True}}}]}


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, ""

    def json(self):
        return self._body


class Session:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, params))
        for (slug, d), body in self.routes.items():
            if f"/{slug}/scoreboard" in url and params["dates"] == d:
                return Resp(*body) if isinstance(body, tuple) else Resp(200, body)
        return Resp(400, {"message": "Failed to get events endpoint."})


class TestCalendar:
    def test_strings_objects_and_season_window(self):
        page = {"leagues": [{"calendar": ["2025-09-16T07:00Z", {"startDate": "2025-10-01T07:00Z"}, "2027-01-01T00:00Z"]}]}
        assert cc.calendar_days(page, 2025) == ["2025-09-16", "2025-10-01"]
        assert cc.calendar_days(None, 2025) == []

    def test_fallback(self):
        eu = cc.fallback_days(2025, "europe")
        assert all(date.fromisoformat(d).weekday() in (1, 2, 3) for d in eu) and len(eu) > 140
        assert len(cc.fallback_days(2025, "cup")) == 365


class TestRows:
    def test_row_with_shots_corners_cards(self):
        import results_feed
        [res] = results_feed.parse_espn({"events": [ev("Arsenal", "Bayern Munich", "2025-10-01")]})
        row = cc.row_from(res, "CL")
        assert row == {"Date": "2025-10-01", "HomeTeam": "Arsenal", "AwayTeam": "Bayern Munich", "FTHG": 2, "FTAG": 1,
                       "Result": "H", "league": "CL", "HS": 15, "AS": 8, "HST": 6, "AST": 2, "HC": 7, "AC": 3,
                       "HY": 0, "AY": 1, "HR": 0, "AR": 0}

    def test_no_extra_time_results(self):
        import results_feed
        [res] = results_feed.parse_espn({"events": [ev("A", "B", "2025-10-01", name="STATUS_FINAL_AET")]})
        assert cc.row_from(res, "FAC") is None


class TestCollect:
    def test_calendar_then_days(self):
        data = cc.empty()
        routes = {("uefa.champions", "20251015"): {"leagues": [{"calendar": ["2025-09-16T07:00Z", "2025-10-01T07:00Z"]}],
                                                   "events": []},
                  ("uefa.champions", "20251001"): {"events": [ev("Arsenal", "Bayern Munich", "2025-10-01")]},
                  ("uefa.champions", "20250916"): {"events": [ev("Inter", "Ajax", "2025-09-16", stats=False)]}}
        rep = asyncio.run(cc.collect(Session(routes), data, float("inf"), date(2025, 11, 1), pause=0))
        assert rep["matches"] == 2 and rep["with_shots"] == 1
        assert set(data["rows"]) == {"2025-10-01|Arsenal|Bayern Munich", "2025-09-16|Inter|Ajax"}
        assert "2025-10-01|uefa.champions" in data["days"] and data["calendars"]["uefa.champions|2025"]
        assert rep["fallback_calendars"] > 0   # the other competitions had no calendar in this stub

    def test_stops_when_blocked(self):
        rep = asyncio.run(cc.collect(Session({("uefa.champions", "20251015"): (403, {})}), cc.empty(),
                                     float("inf"), date(2025, 11, 1), pause=0))
        assert rep["stopped"] == "HTTP 403" and rep["requests"] == 1

    def test_storage_and_frame(self):
        class R:
            def __init__(self): self.v = None
            def get(self, k): return self.v
            def set(self, k, v): self.v = v
        r, data = R(), cc.empty()
        data["rows"]["k"] = {"Date": "2025-10-01", "HomeTeam": "A", "AwayTeam": "B", "FTHG": 1, "FTAG": 0,
                             "Result": "H", "league": "CL"}
        data["rows"]["k2"] = {**data["rows"]["k"], "league": "FAC"}
        cc.save(r, data)
        back = cc.load(r)
        assert len(cc.rows_frame(back)) == 2 and list(cc.rows_frame(back, {"CL"})["league"]) == ["CL"]
        assert cc.approved({"check": {"use": {"europe": True, "cups": False}}}) == cc.EUROPE_CODES
        assert cc.summary(back)["by_competition"] == {"CL": 1, "FAC": 1}


class TestCheck:
    def test_picks_the_best_way_that_beats_the_league_data(self, monkeypatch):
        import backtest
        league = pd.DataFrame([{"Date": pd.Timestamp(f"2025-0{m}-{d:02d}"), "HomeTeam": "A", "AwayTeam": "B",
                                "FTHG": 1, "FTAG": 0, "Result": "H", "league": "PL"}
                               for m in (1, 2, 3) for d in range(1, 29) for _ in range(6)])
        seen = []

        def walk_forward(data, start, end, make_model=None, log=None):
            model = make_model()
            strength = data["StrengthOnly"].fillna(False).astype(bool) if "StrengthOnly" in data else None
            europe = (data["league"] == "CL")
            # Europe as ratings-only rows helps most, as full rows a little; cups hurt
            p = 0.5
            if europe.any():
                p = 0.7 if (strength is not None and strength[europe].all() and model.use_league_strength) else 0.55
            if (data["league"] == "FAC").any():
                p -= 0.1
            seen.append((model.use_league_strength, round(p, 2)))
            return [{"league": "PL", "date": f"{start}-{i}", "home": "A", "away": "B", "result": "H",
                     "p_home": p, "p_draw": (1 - p) / 2, "p_away": (1 - p) / 2, "odds_home": 2.0, "odds_draw": 3.5,
                     "odds_away": 4.0, "home_goals": 1, "away_goals": 0, "p_over25": 0.5} for i in range(400)]
        monkeypatch.setattr(backtest, "walk_forward", walk_forward)
        europe = pd.DataFrame([{"Date": pd.Timestamp("2025-01-10"), "HomeTeam": "A", "AwayTeam": "C", "FTHG": 1,
                                "FTAG": 1, "Result": "D", "league": "CL"}])
        cups = pd.DataFrame([{"Date": pd.Timestamp("2025-01-10"), "HomeTeam": "A", "AwayTeam": "D", "FTHG": 3,
                              "FTAG": 0, "Result": "H", "league": "FAC"}])
        got = cc.check(league, {"europe": europe, "cups": cups}, log=lambda *_: None)
        assert got["config"] == {"name": "europe_strength", "league_strength": True, "sets": {"europe": "strength"}}
        assert got["use"] == {"europe": True, "cups": False}
        assert got["scores"]["europe_full"]["with"] < got["scores"]["europe_full"]["league_only"]
        assert got["scores"]["cups_full"]["with"] > got["scores"]["cups_full"]["league_only"]
        assert "value_roi_with" in got["scores"]["europe_strength"]
        # Stored verdicts turn into row modes and the model setting
        data = {"check": got}
        assert cc.modes(data) == {c: "strength" for c in cc.EUROPE_CODES} and cc.league_strength(data)
        assert cc.approved(data) == cc.EUROPE_CODES

    def test_nothing_used_when_nothing_helps(self, monkeypatch):
        import backtest
        league = pd.DataFrame([{"Date": pd.Timestamp(f"2025-0{m}-{d:02d}"), "HomeTeam": "A", "AwayTeam": "B",
                                "FTHG": 1, "FTAG": 0, "Result": "H", "league": "PL"}
                               for m in (1, 2, 3) for d in range(1, 29) for _ in range(6)])
        monkeypatch.setattr(backtest, "walk_forward", lambda data, start, end, make_model=None, log=None: [
            {"league": "PL", "date": f"{start}-{i}", "home": "A", "away": "B", "result": "H", "p_home": 0.5,
             "p_draw": 0.25, "p_away": 0.25, "odds_home": 2.0, "odds_draw": 3.5, "odds_away": 4.0,
             "home_goals": 1, "away_goals": 0, "p_over25": 0.5} for i in range(400)])
        got = cc.check(league, {"europe": pd.DataFrame(), "cups": pd.DataFrame()}, log=lambda *_: None)
        assert got["config"] is None and got["use"] == {"europe": False, "cups": False}
        assert got["scores"]["europe_full"] == {"skipped": "no matches"}
        assert not cc.league_strength({"check": got}) and cc.modes({"check": got}) == {}

    def test_old_verdicts_mean_full_rows(self):
        assert cc.modes({"check": {"use": {"europe": True, "cups": False}}}) == {c: "full" for c in cc.EUROPE_CODES}


class TestTraining:
    def test_approved_rows_with_resolved_names(self, monkeypatch):
        import model_store
        data = {**cc.empty(), "check": {"use": {"europe": True}},
                "rows": {"a": {"Date": "2025-10-01", "HomeTeam": "Manchester United", "AwayTeam": "Bayern Munich",
                               "FTHG": 1, "FTAG": 1, "Result": "D", "league": "EL"},
                         "b": {"Date": "2025-10-02", "HomeTeam": "Manchester United", "AwayTeam": "Grimsby Town",
                               "FTHG": 0, "FTAG": 0, "Result": "D", "league": "EFLC"}}}
        monkeypatch.setattr(model_store, "_client", lambda: object())
        monkeypatch.setattr(cc, "load", lambda r: data)
        df = main._club_cup_rows({"Man United", "Bayern Munich"})
        assert list(df["league"]) == ["EL"] and df.iloc[0]["HomeTeam"] == "Man United"
        assert not df.iloc[0]["StrengthOnly"] and main._club_league_strength is False
        data["check"] = {"use": {"europe": True}, "config": {"name": "europe_strength", "league_strength": True,
                                                            "sets": {"europe": "strength"}}}
        df = main._club_cup_rows({"Man United", "Bayern Munich"})
        assert bool(df.iloc[0]["StrengthOnly"]) and main._club_league_strength is True
        assert main._train_new.__module__ == "main"
        assert main._club_cup_rows(set(), {"EFLC"}).iloc[0]["AwayTeam"] == "Grimsby Town"


class TestProbe:
    def test_reports_each_competition(self):
        routes = {("uefa.champions", "20250311"): {"leagues": [{"calendarType": "list", "calendar": ["2025-03-11T07:00Z"]}],
                                                   "events": [ev("Arsenal", "PSV", "2025-03-11")]}}
        got = asyncio.run(cc.probe(Session(routes)))
        assert set(got) == set(cc.PROBE_DAYS)
        cl = got["uefa.champions"]
        assert (cl["status"], cl["events"], cl["finished"], cl["rows"], cl["calendar_len"]) == (200, 1, 1, 1, 1)
        assert got["eng.fa"]["status"] == 400 and got["eng.fa"]["events"] == 0


class TestRoundCalendars:
    """ESPN's calendarType "list": the season's rounds with their days."""
    PAGE = {"leagues": [{"calendarType": "list", "calendar": [{
        "label": "UEFA Champions League", "startDate": "2024-07-01T04:00Z", "endDate": "2025-07-01T03:59Z",
        "entries": [
            {"label": "League Phase", "detail": "Sep 17-Jan 29", "startDate": "2024-08-29T07:00Z", "endDate": "2025-01-31T07:59Z"},
            {"label": "Knockout Round Playoffs", "detail": "Feb 11-19", "startDate": "2025-01-31T08:00Z", "endDate": "2025-02-21T07:59Z"},
            {"label": "Rd of 16", "detail": "Mar 4-12", "startDate": "2025-02-21T08:00Z", "endDate": "2025-03-14T07:59Z"},
            {"label": "Second Round", "detail": "Nov 29-Dec 1", "startDate": "2024-11-28T08:00Z", "endDate": "2025-01-09T07:59Z"},
            {"label": "Qualifying", "detail": "Oct 9", "startDate": "2024-07-01T07:00Z", "endDate": "2024-10-14T06:59Z"},
            {"label": "Odd", "detail": "TBD", "startDate": "2025-05-30T07:00Z", "endDate": "2025-06-01T06:59Z"}]}]}]}

    def test_cup_rounds(self):
        days = cc.calendar_days(self.PAGE, 2024, "cup")
        for d in ("2024-09-17", "2024-10-01", "2025-01-29", "2025-02-11", "2025-02-19", "2025-03-11",
                  "2024-11-29", "2024-12-01", "2024-10-09", "2025-05-30", "2025-06-01"):
            assert d in days, d
        assert "2024-09-16" not in days and "2025-01-30" not in days and "2025-02-10" not in days

    def test_long_european_rounds_keep_midweek(self):
        days = cc.calendar_days(self.PAGE, 2024, "europe")
        assert "2024-09-17" in days and "2024-10-01" in days and "2025-03-11" in days
        assert "2024-09-21" not in days          # a Saturday in the league phase
        assert "2025-03-08" not in days          # Saturday within "Mar 4-12"
        assert "2024-11-30" in days              # a 3-day round keeps all its days

    def test_old_calendars_are_read_again(self):
        data = cc.empty()
        data["calendars"] = {"uefa.champions|2024": ["2024-07-01"]}
        routes = {("uefa.champions", "20241015"): self.PAGE}
        asyncio.run(cc.collect(Session(routes), data, float("inf"), date(2025, 3, 1), pause=0))
        assert data["calendar_version"] == cc.CALENDAR_VERSION
        assert "2024-09-17" in data["calendars"]["uefa.champions|2024"]


def test_league_calendars_read_both_halves_of_a_calendar_year_season():
    import asyncio
    from datetime import date
    import club_cups

    class Session:
        def __init__(self):
            self.urls = []

        async def get(self, url, headers=None, params=None, timeout=None):
            self.urls.append(params["dates"])
            day = params["dates"]
            cal = {"20251015": ["2025-08-10T00:00Z", "2025-10-20T00:00Z"],
                   "20260415": ["2026-03-01T00:00Z", "2026-05-02T00:00Z", "2026-08-01T00:00Z"]}.get(day)

            class R:
                status_code = 200
                text = ""
                def json(s):
                    return {"leagues": [{"calendar": cal or []}], "events": []}
            return R()

    data = club_cups.empty()
    s = Session()
    asyncio.run(club_cups.collect(s, data, 1e18, date(2026, 6, 1), pause=0,
                                  competitions={"irl.1": ("IRL", "Ireland", "league")}, seasons_back=0))
    # Days from both the 2025 and the 2026 calendars, inside July 2025 - June 2026
    assert data["calendars"]["irl.1|2025"] == ["2025-08-10", "2025-10-20", "2026-03-01", "2026-05-02"]
    assert {"20251015", "20260415"} <= set(s.urls)


def test_a_league_without_a_calendar_is_skipped_not_read_daily():
    import asyncio
    from datetime import date
    import club_cups

    class Session:
        calls = 0

        async def get(self, url, headers=None, params=None, timeout=None):
            Session.calls += 1

            class R:
                status_code = 200
                text = ""
                def json(s):
                    return {"leagues": [{"calendar": []}], "events": []}
            return R()

    data = club_cups.empty()
    asyncio.run(club_cups.collect(Session(), data, 1e18, date(2026, 6, 1), pause=0,
                                  competitions={"cze.1": ("CZE", "Czechia", "league")}, seasons_back=0))
    assert data["calendars"]["cze.1|2025"] == [] and Session.calls == 2
