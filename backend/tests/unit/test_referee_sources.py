"""
Referees from football-data.org (referee_sources.py): upcoming appointments
for the cards forecast, and past matches for the referee ratings.
"""

import asyncio
import gzip
import json
from datetime import date, timedelta

import pandas as pd

import main
import referee_sources as rs


def fd_match(home, away, utc, referee="Michael Oliver", status="FINISHED"):
    refs = [{"id": 1, "name": "Some Assistant", "type": "ASSISTANT_REFEREE_N1"}]
    if referee:
        refs.append({"id": 2, "name": referee, "type": "REFEREE", "nationality": "England"})
    return {"utcDate": utc, "status": status, "homeTeam": {"name": home}, "awayTeam": {"name": away}, "referees": refs}


class TestParse:
    def test_the_referee_not_the_assistants(self):
        got = rs.parse_matches({"matches": [fd_match("Arsenal FC", "Chelsea FC", "2025-03-22T17:30:00Z"),
                                            fd_match("Everton FC", "Fulham FC", "2025-03-22T15:00:00Z", referee=None)]})
        assert got == [{"date": "2025-03-22", "home": "Arsenal FC", "away": "Chelsea FC", "referee": "Michael Oliver",
                        "status": "FINISHED"},
                       {"date": "2025-03-22", "home": "Everton FC", "away": "Fulham FC", "referee": None,
                        "status": "FINISHED"}]
        assert rs.parse_matches(None) == []

    def test_season_start(self):
        assert rs.season_start(date(2026, 9, 25)) == 2026 and rs.season_start(date(2026, 3, 1)) == 2025


class TestPast:
    def test_reads_seasons_once_and_the_current_one_again(self):
        today = date(2026, 9, 25)
        todo = rs.seasons_to_read({}, today)
        assert todo[0] == ("PL", 2026) and len(todo) == len(rs.FD_COMPETITIONS) * (rs.FD_SEASONS_BACK + 1)
        asked, slept = [], []

        async def get_json(url):
            asked.append(url)
            if "competitions/PL/" in url and "season=2025" in url:
                return {"matches": [fd_match("Arsenal FC", "Chelsea FC", "2025-10-04T14:00:00Z")]}
            if "competitions/WC/" in url:
                return None  # e.g. not on the plan
            return {"matches": []}

        async def sleep(s):
            slept.append(s)
        state = {}
        rep = asyncio.run(rs.collect_past(get_json, state, today, sleep, max_requests=1000))
        assert rep["found"] == 1 and state["refs"] == {"2025-10-04|Arsenal FC|Chelsea FC": "Michael Oliver"}
        assert "PL|2025" in state["done"] and "PL|2026" not in state["done"]   # current season: again next time
        assert any(f.startswith("WC") for f in rep["failed"]) and "WC|2025" not in state["done"]
        assert len(slept) == rep["requests"] - 1 and slept[0] == rs.FD_PAUSE
        # Second run: only the current season and the failed ones
        asked.clear()
        asyncio.run(rs.collect_past(get_json, state, today, sleep, max_requests=1000))
        assert all("season=2026" in u or "competitions/WC/" in u for u in asked)

    def test_request_cap(self):
        async def get_json(url):
            return {"matches": []}

        async def sleep(s):
            pass
        rep = asyncio.run(rs.collect_past(get_json, {}, date(2026, 9, 25), sleep, max_requests=3))
        assert rep["requests"] == 3


class TestServer:
    def test_upcoming_referees_for_our_predictions(self, monkeypatch):
        today = date.today()
        day = (today + timedelta(days=1)).isoformat()
        monkeypatch.setattr(main, "API_KEY", "k")

        async def fake_get(self, client, url, _attempt=0):
            assert "/v4/matches?dateFrom=" in url
            return {"matches": [fd_match("Arsenal FC", "Chelsea FC", f"{day}T15:00:00Z", status="TIMED"),
                                fd_match("Leeds United FC", "Burnley FC", f"{day}T15:00:00Z", referee=None)]}
        monkeypatch.setattr(main.FootballDataClient, "_get", fake_get)
        preds = [{"home": "Arsenal FC", "away": "Chelsea FC", "date": day},
                 {"home": "Leeds United FC", "away": "Burnley FC", "date": day}]
        got = asyncio.run(main._football_data_referees(preds, today))
        assert (got["listed"], got["with_referee"], got["found"]) == (2, 1, 1)
        assert got["found_map"] == {f"Arsenal FC|Chelsea FC|{day}": {"name": "Michael Oliver", "career": None,
                                                                    "source": "football-data"}}

    def test_no_key(self, monkeypatch):
        monkeypatch.setattr(main, "API_KEY", "")
        assert "skipped" in asyncio.run(main._football_data_referees([], date.today()))

    def test_past_referees_reach_the_cards_history(self, monkeypatch):
        import international_stats
        import model_store

        class R:
            def __init__(self):
                self.kv = {rs.REFS_KEY: gzip.compress(json.dumps(
                    {"refs": {"2025-10-04|Manchester United FC|Manchester City FC": "Simon Hooper"},
                     "done": ["PL|2025"], "at": "x"}).encode())}
            def get(self, k): return self.kv.get(k)
        monkeypatch.setattr(model_store, "_client", lambda: R())
        monkeypatch.setattr(international_stats, "load", lambda r: {"club_refs": {}})
        history = pd.DataFrame([{"Date": pd.Timestamp("2025-10-04"), "HomeTeam": "Man United", "AwayTeam": "Man City",
                                 "Referee": None, "league": "PL"}])
        out = main._with_club_referees(history)
        assert out.loc[0, "Referee"] == "Simon Hooper"
        assert main._fd_refs_status["refs"] == 1 and main._fd_refs_status["seasons_done"] == 1
