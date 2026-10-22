"""
SofaScore xG for club matches (club_xg.py): parsing, collection, matching
to the league rows, the backtest verdict (xg_check.py) and the model's xG
features.
"""

import asyncio
from datetime import date, datetime, timezone

import pandas as pd

import club_xg as cx
import xg_check
from predictor import XG_COLS, LeaguePredictor


def ts(day):
    return int(datetime.fromisoformat(day).replace(hour=15, tzinfo=timezone.utc).timestamp())


def event(eid, home, away, day, status="finished", has_xg=True):
    return {"id": eid, "homeTeam": {"name": home}, "awayTeam": {"name": away},
            "startTimestamp": ts(day), "status": {"type": status}, "hasXg": has_xg}


def stats(h, a):
    return {"statistics": [{"period": "ALL", "groups": [{"statisticsItems": [
        {"name": "Ball possession", "key": "ballPossession", "homeValue": 55, "awayValue": 45},
        {"name": "Expected goals", "key": "expectedGoals", "homeValue": h, "awayValue": a}]}]}]}


def test_parsers():
    assert cx.parse_seasons({"seasons": [{"id": 5, "year": "25/26"}, {"id": 4, "year": "24/25"}, {"id": 3}]}, keep=2) \
        == [{"id": 5, "name": "25/26"}, {"id": 4, "name": "24/25"}]
    found, more = cx.parse_events({"events": [event(1, "Arsenal", "Chelsea", "2025-09-01"),
                                              event(2, "Spurs", "Fulham", "2025-09-02", status="notstarted")],
                                   "hasNextPage": True}, "PL")
    assert found == {"1": ["PL", "2025-09-01", "Arsenal", "Chelsea", None, None, True]} and more
    assert cx.parse_stats_xg(stats(1.234567, 0.5)) == (1.235, 0.5)
    assert cx.parse_stats_xg({"statistics": []}) is None
    assert cx.parse_shotmap_xg({"shotmap": [{"isHome": True, "xg": 0.3}, {"isHome": False, "xg": 0.1},
                                            {"isHome": True, "xg": 0.2}]}) == (0.5, 0.1)
    assert cx.parse_shotmap_xg({"shotmap": [{"isHome": True}]}) is None


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, ""

    def json(self):
        return self._body


class Session:
    """Answers by URL suffix; anything unknown is a 404."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(url)
        for suffix, body in self.routes.items():
            if url.endswith(suffix):
                return body if isinstance(body, Resp) else Resp(200, body)
        return Resp(404, None)


def test_run_lists_and_fetches_xg(monkeypatch):
    monkeypatch.setattr(cx, "CLUB_TOURNAMENTS", {17: "PL"})
    monkeypatch.setattr(cx, "PAUSE", 0)
    s = Session({
        "/unique-tournament/17/seasons": {"seasons": [{"id": 9, "year": "25/26"}]},
        "/season/9/events/last/0": {"events": [event(1, "Arsenal", "Chelsea", "2025-09-01"),
                                               event(2, "Spurs", "Fulham", "2025-09-02")], "hasNextPage": True},
        "/season/9/events/last/1": {"events": [event(3, "Leeds", "Burnley", "2025-08-20", has_xg=False)],
                                    "hasNextPage": False},
        "/event/1/statistics": stats(2.1, 0.4),
        "/event/2/shotmap": {"shotmap": [{"isHome": True, "xg": 0.7}, {"isHome": False, "xg": 1.2}]},
    })
    report = asyncio.run(cx.run(None, 5, today=date(2025, 10, 1), session=s))
    assert report["listed"] == 3 and report["xg"]["stats"] == 1 and report["xg"]["shotmap"] == 1
    assert not any("/event/3/" in u for u in s.calls)   # hasXg False: not asked


def test_fetch_xg_retries_recent_and_gives_up_on_old(monkeypatch):
    data = cx.empty()
    data["matches"] = {"1": ["PL", "2025-09-29", "A", "B", None, None, True],
                       "2": ["PL", "2025-08-01", "C", "D", None, None, None]}
    errors = []
    asyncio.run(cx.fetch_xg(Session({}), data, float("inf"), date(2025, 10, 1), errors, pause=0))
    assert "1" not in data["no_xg"] and "2" in data["no_xg"]


def test_blocked_saves_what_was_found(monkeypatch):
    monkeypatch.setattr(cx, "CLUB_TOURNAMENTS", {17: "PL"})
    monkeypatch.setattr(cx, "PAUSE", 0)
    report = asyncio.run(cx.run(None, 5, session=Session({"/seasons": Resp(403, None)})))
    assert "403" in report["blocked"]


def test_attach_matches_names_and_a_day_either_way():
    data = cx.empty()
    data["matches"] = {"1": ["PL", "2025-09-01", "Arsenal", "Chelsea", 2.1, 0.4, True],
                       "2": ["PL", "2025-09-03", "Tottenham Hotspur", "Fulham", 0.7, 1.2, True],
                       "3": ["PL", "2025-09-05", "Leeds", "Burnley", None, None, False]}
    rows = pd.DataFrame({"league": ["PL", "PL", "PL", "SA"],
                         "Date": pd.to_datetime(["2025-09-01", "2025-09-02", "2025-09-05", "2025-09-01"]),
                         "HomeTeam": ["Arsenal", "Tottenham", "Leeds", "Arsenal"],
                         "AwayTeam": ["Chelsea", "Fulham", "Burnley", "Chelsea"]})
    out = cx.attach(rows, data)
    assert list(out["HXG"].iloc[:2]) == [2.1, 0.7] and list(out["AXG"].iloc[:2]) == [0.4, 1.2]
    assert out["HXG"].iloc[2:].isna().all()


def test_decide():
    def res(x1, ou):
        return {"n": 100, "1x2": {"log_loss": x1}, "over25": {"log_loss": ou}}
    base = res(0.98, 0.68)
    assert xg_check.decide({False: base, "goals": res(0.98, 0.678), True: res(0.97, 0.677)}) is True
    assert xg_check.decide({False: base, "goals": res(0.98, 0.678), True: res(0.985, 0.677)}) == "goals"
    assert xg_check.decide({False: base, "goals": res(0.981, 0.670), True: res(0.97, 0.67)}) is False
    assert xg_check.decide({False: base, "goals": res(0.98, 0.6795), True: res(0.97, 0.67)}) is False


def test_predictor_xg_features():
    p = LeaguePredictor()
    assert not set(XG_COLS) & set(p.cols(target="goals"))
    p.use_xg = "goals"
    assert set(XG_COLS) <= set(p.cols(target="goals")) and not set(XG_COLS) & set(p.cols())
    p.use_xg = True
    assert set(XG_COLS) <= set(p.cols())
    p._update("A", "B", 2, 1, "PL", hxg=1.8, axg=0.6)
    p._update("A", "B", 2, 1, "PL", hxg=float("nan"), axg=0.6)
    assert p.team_stats["A"]["xgf"] == [1.8] and p.team_stats["B"]["xga"] == [1.8]
