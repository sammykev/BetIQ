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
    def test_uses_a_set_only_if_it_lowers_log_loss(self, monkeypatch):
        import backtest
        league = pd.DataFrame([{"Date": pd.Timestamp(f"2025-0{m}-{d:02d}"), "HomeTeam": "A", "AwayTeam": "B",
                                "FTHG": 1, "FTAG": 0, "Result": "H", "league": "PL"}
                               for m in (1, 2, 3) for d in range(1, 29) for _ in range(6)])

        def walk_forward(data, start, end, log=None):
            # 400 league test matches; the Europe set makes the model surer of the right answer
            p = 0.7 if (data["league"] == "CL").any() else 0.5
            return [{"league": "PL", "date": f"{start}-{i}", "home": "A", "away": "B", "result": "H",
                     "p_home": p, "p_draw": (1 - p) / 2, "p_away": (1 - p) / 2} for i in range(400)]
        monkeypatch.setattr(backtest, "walk_forward", walk_forward)
        europe = pd.DataFrame([{"Date": pd.Timestamp("2025-01-10"), "HomeTeam": "A", "AwayTeam": "C", "FTHG": 1,
                                "FTAG": 1, "Result": "D", "league": "CL"}])
        cups = pd.DataFrame([{"Date": pd.Timestamp("2025-01-10"), "HomeTeam": "A", "AwayTeam": "D", "FTHG": 3,
                              "FTAG": 0, "Result": "H", "league": "FAC"}])
        got = cc.check(league, {"europe": europe, "cups": cups, "none": pd.DataFrame()}, log=lambda *_: None)
        assert got["use"] == {"europe": True, "cups": False}
        assert got["scores"]["europe"]["with"] < got["scores"]["europe"]["league_only"]
        assert got["scores"]["none"] == {"skipped": "no matches"}


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
        assert main._club_cup_rows(set(), {"EFLC"}).iloc[0]["AwayTeam"] == "Grimsby Town"
