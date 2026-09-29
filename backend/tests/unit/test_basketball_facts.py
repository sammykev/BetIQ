"""
Basketball match facts (basketball_facts.py): each team's last 5, their
head-to-head, and averages over the last 10, from SportyBet's results, as
the football match page shows them.
"""

import asyncio
from datetime import datetime, timezone

from fastapi.testclient import TestClient

import basketball_facts as bf
import main


def game(i, h, a, hs, as_, q=None, ot=False, t="Spain · Liga ACB"):
    ko = int(datetime(2026, 3, 1 + i, 18, tzinfo=timezone.utc).timestamp())
    return {"id": f"sr:match:{i}", "t": t, "h": h, "a": a, "ko": ko, "hs": hs, "as": as_, "q": q, "ot": ot}


RESULTS = [
    game(1, "Real Madrid", "Barcelona", 85, 80, [[20, 20], [22, 20], [21, 20], [22, 20]]),
    game(2, "Valencia", "Real Madrid", 90, 88),
    game(3, "Real Madrid", "Unicaja", 95, 70, [[25, 15], [25, 20], [20, 15], [25, 20]]),
    game(4, "Barcelona", "Real Madrid", 99, 92, ot=True),
    game(5, "Barcelona", "Valencia", 80, 75),
    game(6, "Real Madrid", "Baskonia", 81, 79),
    game(7, "Real Madrid", "Joventut", 100, 60),
    game(8, "Real Madrid", "Barcelona", 70, 75, t="Spain · Copa del Rey"),
]
PRED = {"home": "Real Madrid", "away": "Barcelona", "date": "2026-03-20", "time": "19:00",
        "total_line": 160.5, "handicap_line": -4.5, "sportybet_event_id": "sr:match:99"}


def test_form_h2h_and_averages():
    idx = bf.build_index(RESULTS)
    f = bf.facts(idx, PRED)
    # Newest first, from each team's side
    assert [r["outcome"] for r in f["home"]] == ["L", "W", "W", "L", "W"]
    assert f["home"][0] == {"date": "2026-03-09", "opponent": "Barcelona", "venue": "H", "for": 70, "against": 75,
                            "outcome": "L", "ot": False, "comp": "Copa del Rey"}
    assert f["home"][1]["opponent"] == "Joventut"
    # Meetings at either venue, summary from the home side
    assert [(m["home"], m["hs"], m["as"]) for m in f["h2h"]] == [
        ("Real Madrid", 70, 75), ("Barcelona", 99, 92), ("Real Madrid", 85, 80)]
    assert f["summary"]["h2h"]["won"] == 1 and f["summary"]["h2h"]["lost"] == 2
    a = f["averages"]["home"]
    assert a["played"] == 7 and a["points"]["for"] == round(611 / 7, 1)
    assert a["results"]["won"] == round(4 / 7, 3) and a["overtime"] == round(1 / 7, 3)
    assert a["quarters"]["matches"] == 2 and a["quarters"]["for"][0] == 22.5
    assert a["over_line"] == {"line": 160.5, "rate": round(4 / 7, 3)} and a["cover"]["line"] == -4.5
    assert f["averages"]["away"]["cover"]["line"] == 4.5
    assert bf.form_string(idx, "Barcelona") == "LWWW"          # oldest to newest


def test_only_games_before_the_match():
    idx = bf.build_index(RESULTS)
    early = {**PRED, "date": "2026-03-04", "time": "12:00"}
    f = bf.facts(idx, early)
    assert [r["date"] for r in f["home"]] == ["2026-03-03", "2026-03-02"]
    assert len(f["h2h"]) == 1


def test_endpoint(monkeypatch):
    async def open_to_all(request, sport):
        return None
    monkeypatch.setattr(main, "_check_sport_access", open_to_all)
    monkeypatch.setattr(main, "_bb_predictions", [PRED])
    main._bb_team_games.clear()
    monkeypatch.setattr(main, "_bb_results", lambda r, days=None: RESULTS)
    monkeypatch.setattr(main, "_get_redis", lambda: object())
    c = TestClient(main.app)
    got = c.get("/api/basketball/facts?event=sr:match:99").json()
    assert len(got["home"]) == 5 and got["summary"]["h2h"]["lost"] == 2
    assert c.get("/api/basketball/facts?event=nope").status_code == 404
    slim = main._bb_slim({**PRED, "bb_markets": []})
    assert slim["home_form"] == "WLWWL" and slim["away_form"] == "LWWW"
    main._bb_team_games.clear()
