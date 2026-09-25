"""
Referees for upcoming matches (referees.py) and how they reach predictions
(main._set_piece_extras / _apply_referees / _refresh_referees).
"""

import asyncio
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

import main
import referees
import set_pieces


def listing(*events):
    return {"events": list(events)}


def event(eid, home, away, day, status="notstarted"):
    ts = int(datetime.fromisoformat(f"{day}T15:00:00+00:00").timestamp())
    return {"id": eid, "homeTeam": {"name": home}, "awayTeam": {"name": away},
            "startTimestamp": ts, "status": {"type": status}}


def page(name, games=200, yellow=900, red=20):
    return {"event": {"referee": {"name": name, "games": games, "yellowCards": yellow, "redCards": red}}}


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, ""

    def json(self):
        return self._body


class Client:
    """Routes by URL ending; anything else is a 404."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def get(self, url, params=None, headers=None):
        self.calls.append(url)
        for end, reply in self.routes.items():
            if url.endswith(end):
                return Resp(*reply) if isinstance(reply, tuple) else Resp(200, reply)
        return Resp(404, {})


TODAY = date(2025, 3, 22)
PREDS = [{"home": "Man United", "away": "Man City", "date": "2025-03-22"},
         {"home": "Arsenal", "away": "Chelsea", "date": "2025-03-23"},
         {"home": "Everton", "away": "Fulham", "date": "2025-03-24"},
         {"home": "Leeds", "away": "Burnley", "date": "2025-04-20"}]  # beyond the window


class TestFetch:
    def routes(self):
        return {
            "scheduled-events/2025-03-22": listing(event(1, "Manchester United", "Manchester City", "2025-03-22"),
                                                   event(9, "Boca Juniors", "River Plate", "2025-03-22")),
            "scheduled-events/2025-03-23": listing(event(2, "Arsenal", "Chelsea", "2025-03-23"),
                                                   event(3, "Everton", "Fulham", "2025-03-23", status="finished")),
            "scheduled-events/2025-03-24": listing(event(4, "Everton", "Fulham", "2025-03-24")),
            "event/1": page("Anthony Taylor"),
            "event/2": {"event": {}},  # not appointed yet
            "event/4": page("Simon Hooper", games=50),
        }

    def test_finds_referees_for_our_matches(self):
        client = Client(self.routes())
        found, rep = asyncio.run(referees.fetch(client, PREDS, {}, TODAY, pause=0))
        assert found == {
            "Man United|Man City|2025-03-22": {"name": "Anthony Taylor", "event_id": 1,
                                               "career": {"games": 200, "yellow": 900, "red": 20}},
            "Everton|Fulham|2025-03-24": {"name": "Simon Hooper", "event_id": 4,
                                          "career": {"games": 50, "yellow": 900, "red": 20}},
        }
        assert (rep["matched"], rep["pages"], rep["new"]) == (3, 3, 2)
        assert not any(u.endswith("event/9") or u.endswith("event/3") for u in client.calls)

    def test_known_referees_are_not_asked_again_and_survive_a_failed_day(self):
        known = {"Man United|Man City|2025-03-22": {"name": "Anthony Taylor", "career": {}},
                 "Old|Match|2025-03-20": {"name": "Gone", "career": {}}}
        routes = {**self.routes(), "scheduled-events/2025-03-22": (500, {})}
        client = Client(routes)
        found, rep = asyncio.run(referees.fetch(client, PREDS, known, TODAY, pause=0))
        assert found["Man United|Man City|2025-03-22"]["name"] == "Anthony Taylor"  # kept though its day failed
        assert "Old|Match|2025-03-20" not in found                                   # past: dropped
        assert not any(u.endswith("event/1") for u in client.calls)
        assert rep["errors"][0] == "2025-03-22: HTTP 500"

    def test_stops_when_blocked(self):
        client = Client({"scheduled-events/2025-03-22": (403, {})})
        found, rep = asyncio.run(referees.fetch(client, PREDS, {}, TODAY, pause=0))
        assert found == {} and len(client.calls) == 1 and rep["errors"] == ["2025-03-22: HTTP 403"]

    def test_page_budget(self):
        found, rep = asyncio.run(referees.fetch(Client(self.routes()), PREDS, {}, TODAY, pause=0, max_pages=1))
        assert rep["pages"] == 1 and list(found) == ["Man United|Man City|2025-03-22"]  # soonest first


class Canon:
    @staticmethod
    def canon(name):
        return name


def trained_model():
    rows = []
    for week in range(30):
        for home, away in (("A", "B"), ("C", "D"), ("B", "C"), ("D", "A")):
            rows.append({"Date": pd.Timestamp("2024-08-01") + pd.Timedelta(days=7 * week), "HomeTeam": home,
                         "AwayTeam": away, "league": "PL", "HC": 6, "AC": 4, "HY": 2, "AY": 2, "HR": 0, "AR": 0,
                         "Referee": "Strict Ref" if week % 2 else "Calm Ref"})
    return set_pieces.SetPieceModel.fit(pd.DataFrame(rows))


class TestPredictions:
    @pytest.fixture(autouse=True)
    def state(self, monkeypatch):
        monkeypatch.setattr(main, "_referees", {"at": "2025-03-22T00:00:00", "appointments": {}, "report": None,
                                                "trigger": None})
        monkeypatch.setattr(main, "_set_pieces", trained_model())
        monkeypatch.setattr(main, "_predictor", Canon())

    def test_appointed_referee_scales_bookings_and_is_shown(self):
        fx = {"home": "A", "away": "B", "date": "2025-03-22", "league": "PL"}
        plain, none = main._set_piece_extras(fx, Canon())
        assert none is None
        main._referees["appointments"] = {"A|B|2025-03-22": {
            "name": "Anthony Taylor", "career": {"games": 400, "yellow": 2400, "red": 20}}}  # ~6.1 a game
        extras, ref = main._set_piece_extras(fx, Canon())
        assert ref["name"] == "Anthony Taylor" and ref["games"] == 400 and ref["cards_factor"] > 1
        assert extras["bookings"]["mean"] > plain["bookings"]["mean"]
        assert extras["corners"] == plain["corners"]

    def test_apply_updates_cached_predictions(self, monkeypatch):
        monkeypatch.setattr(main, "_predictions_cache", [
            {"home": "A", "away": "B", "date": "2025-03-22", "league": "PL", "set_pieces": {"old": 1}},
            {"home": "C", "away": "D", "date": "2025-03-22", "league": "PL"},
            {"home": "X", "away": "Y", "date": "2025-03-22", "sport": "tennis"}])
        main._referees["appointments"] = {"A|B|2025-03-22": {"name": "Calm Ref", "career": None}}
        assert main._apply_referees() == 1
        a, c, tennis = main._predictions_cache
        assert a["referee"]["name"] == "Calm Ref" and "old" not in a["set_pieces"]
        assert "referee" not in c and "bookings" in c["set_pieces"]
        assert tennis == {"home": "X", "away": "Y", "date": "2025-03-22", "sport": "tennis"}

    def _blocked_sofascore(self, monkeypatch):
        import curl_cffi.requests as cr

        class Down:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return Client({"scheduled-events": (403, {})})
            async def __aexit__(self, *a): return False
        monkeypatch.setattr(cr, "AsyncSession", Down)

    def _api_football(self, monkeypatch, reply):
        import httpx
        calls = []

        class AF:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url, params=None, headers=None):
                calls.append(params["date"])
                return Resp(200, reply(params["date"]))
        monkeypatch.setattr(httpx, "AsyncClient", AF)
        return calls

    def test_refresh_keeps_old_appointments_when_sofascore_is_down(self, monkeypatch):
        day = (date.today() + timedelta(days=1)).isoformat()
        main._referees["appointments"] = {f"A|B|{day}": {"name": "Calm Ref"}}
        monkeypatch.setattr(main, "_predictions_cache", [{"home": "A", "away": "B", "date": day}])
        monkeypatch.delenv("APIFOOTBALL_KEY", raising=False)
        self._blocked_sofascore(monkeypatch)
        rep = asyncio.run(main._refresh_referees("manual"))
        assert rep["errors"] and main._referees["appointments"] == {f"A|B|{day}": {"name": "Calm Ref"}}
        assert "APIFOOTBALL_KEY" in rep["api_football"]["skipped"]
        assert main._referee_status()["count"] == 1

    def test_blocked_sofascore_falls_back_to_api_football(self, monkeypatch):
        today = date.today()
        day = (today + timedelta(days=1)).isoformat()
        monkeypatch.setattr(main, "_predictions_cache", [{"home": "A", "away": "B", "date": day, "league": "PL"}])
        monkeypatch.setenv("APIFOOTBALL_KEY", "k")
        monkeypatch.setattr(main, "_get_redis", lambda: None)
        self._blocked_sofascore(monkeypatch)
        calls = self._api_football(monkeypatch, lambda d: {"response": [
            {"fixture": {"id": 1, "referee": "Michael Oliver, England"},
             "teams": {"home": {"name": "A"}, "away": {"name": "B"}}}] if d == day else []})
        rep = asyncio.run(main._refresh_referees("manual"))
        assert calls == [(today + timedelta(days=o)).isoformat() for o in range(2)]
        assert main._referees["appointments"][f"A|B|{day}"] == {"name": "Michael Oliver", "career": None,
                                                                "source": "api-football"}
        assert rep["api_football"]["found"] == 1 and main._predictions_cache[0]["referee"]["name"] == "Michael Oliver"
        assert main._referee_status()["api_football_calls_today"] == 2

        # Within the daily cap only
        main._referees["_af_calls"] = {today.isoformat(): referees.AF_DAILY_CAP}
        rep = asyncio.run(main._refresh_referees("manual"))
        assert "used today" in rep["api_football"]["skipped"] and len(calls) == 2


class TestApiFootball:
    def test_bad_key_stops_early(self):
        class C:
            n = 0
            async def get(self, url, params=None, headers=None):
                C.n += 1
                return Resp(200, {"errors": {"token": "Error/Missing application key."}, "response": []})
        found, rep = asyncio.run(referees.fetch_api_football(C(), PREDS, TODAY, "bad"))
        assert found == {} and C.n == 1 and "token" in rep["errors"][0]


class TestGitHubJob:
    def test_stores_what_sofascore_found(self, monkeypatch):
        import json
        import collect_referees
        import curl_cffi.requests as cr

        class R:
            def __init__(self):
                self.data = {"betiq:predictions": json.dumps({"predictions": PREDS}).encode()}
            def get(self, k): return self.data.get(k)
            def set(self, k, v, ex=None): self.data[k] = v
        r = R()
        monkeypatch.setattr(collect_referees.model_store, "_client", lambda: r)
        routes = TestFetch().routes()

        class Up:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return Client(routes)
            async def __aexit__(self, *a): return False
        monkeypatch.setattr(cr, "AsyncSession", Up)
        monkeypatch.setattr(referees, "PAUSE", 0)
        real = referees.fetch
        monkeypatch.setattr(referees, "fetch", lambda c, p, k, t, **kw: real(c, p, k, TODAY, pause=0))
        assert collect_referees.main() == 0
        stored = referees.load(r)
        assert stored["trigger"] == "github" and stored["appointments"]["Man United|Man City|2025-03-22"]["name"] == "Anthony Taylor"

    def test_blocked_leaves_stored_referees_alone(self, monkeypatch):
        import json
        import collect_referees
        import curl_cffi.requests as cr

        class R:
            def __init__(self):
                self.data = {"betiq:predictions": json.dumps({"predictions": PREDS}).encode(),
                             referees.APPOINTED_KEY: json.dumps({"at": "x", "appointments": {"k|l|2099-01-01": {"name": "Kept"}}})}
            def get(self, k): return self.data.get(k)
            def set(self, k, v, ex=None): raise AssertionError("must not write")
        monkeypatch.setattr(collect_referees.model_store, "_client", lambda: R())
        today = datetime.now(timezone.utc).date().isoformat()

        class Down:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return Client({f"scheduled-events/{today}": (403, {})})
            async def __aexit__(self, *a): return False
        monkeypatch.setattr(cr, "AsyncSession", Down)
        assert collect_referees.main() == 0
