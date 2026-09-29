"""
Tennis form and head-to-head (tennis_facts.py): SportyBet's results read
into sets and games, each player's last 5, their meetings, and his numbers
over recent matches, as football's and basketball's match views have them.
"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

import main
import tennis_facts as tf


def ev(i, h, a, sets, games, status="Ended", t="ATP · Shanghai, China"):
    ko = int(datetime(2026, 9, 1 + i, 10, tzinfo=timezone.utc).timestamp() * 1000)
    return {"eventId": f"sr:match:{i}", "homeTeamName": h, "awayTeamName": a, "estimateStartTime": ko,
            "matchStatus": status, "setScore": sets, "gameScore": games, "_tournament": t}


class TestParse:
    def test_sets_games_and_tiebreaks(self):
        m = tf.parse_result(ev(1, "Sinner, Jannik", "Alcaraz, Carlos", "2:1", ["7:6(7:4)", "3:6", "6:4"]))
        assert m["sets"] == [2, 1] and m["games"] == [[7, 6], [3, 6], [6, 4]] and not m["ret"]

    def test_left_out(self):
        assert tf.parse_result(ev(1, "A / B", "C / D", "2:0", ["6:1", "6:1"])) is None          # doubles
        assert tf.parse_result(ev(1, "A", "B", "0:0", [])) is None                                # walkover
        assert tf.parse_result(ev(1, "A", "B", "1:0", ["6:3"], status="Not started")) is None
        assert tf.parse_result(ev(1, "A", "B", "1:0", ["6:3", "2:1"], status="Retired"))["ret"]


RESULTS = [tf.parse_result(e) for e in (
    ev(1, "Sinner, Jannik", "Alcaraz, Carlos", "2:1", ["7:6(7:4)", "3:6", "6:4"]),
    ev(2, "Zverev, Alexander", "Sinner, Jannik", "0:2", ["4:6", "3:6"]),
    ev(3, "Sinner, Jannik", "Medvedev, Daniil", "1:2", ["6:4", "6:7", "4:6"]),
    ev(4, "Alcaraz, Carlos", "Sinner, Jannik", "2:0", ["6:3", "6:2"], t="ATP · Roland Garros, France"),
    ev(5, "Sinner, Jannik", "Rune, Holger", "2:0", ["6:1", "6:2"]),
    ev(6, "Alcaraz, Carlos", "Fritz, Taylor", "2:0", ["6:4", "7:5"]),
)]
PRED = {"home": "Sinner, Jannik", "away": "Alcaraz, Carlos", "date": "2026-09-20", "time": "12:00",
        "league": "ATP · Shanghai, China"}


def test_form_h2h_and_averages():
    idx = tf.build_index(RESULTS)
    f = tf.facts(idx, PRED)
    assert [r["outcome"] for r in f["home"]] == ["W", "L", "L", "W", "W"]
    assert f["home"][1] == {"date": "2026-09-05", "opponent": "Alcaraz, Carlos", "outcome": "L", "sets": [0, 2],
                            "score": "3-6 2-6", "retired": False, "tournament": "Roland Garros, France", "surface": "Clay"}
    assert [(m["home"], m["sets"]) for m in f["h2h"]] == [("Alcaraz, Carlos", [2, 0]), ("Sinner, Jannik", [2, 1])]
    assert f["summary"]["h2h"] == {"won": 1, "lost": 1}
    a = f["averages"]["home"]
    assert a["played"] == 5 and a["won"] == 0.6 and a["straight_sets_wins"] == 0.4
    assert a["deciding_set"] == {"rate": 0.4, "won": 0.5}
    assert a["tiebreak_matches"] == 0.4                     # 7-6 in two of five
    assert a["games"]["total"] == round((32 + 19 + 33 + 17 + 15) / 5, 1)
    assert f["surface"] == "Hard" and a["surface"]["played"] == 4 and a["surface"]["won"] == 0.75
    assert tf.form_string(idx, "Alcaraz, Carlos") == "LWW"


def test_endpoint_and_list_form(monkeypatch):
    async def open_to_all(request, sport):
        return None
    monkeypatch.setattr(main, "_check_sport_access", open_to_all)
    monkeypatch.setattr(main, "_tennis_results", lambda r: RESULTS)
    main._tennis_players.clear()
    c = TestClient(main.app)
    got = c.get("/api/tennis/facts", params={"home": PRED["home"], "away": PRED["away"], "date": PRED["date"],
                                              "time": "12:00", "league": PRED["league"]}).json()
    assert len(got["home"]) == 5 and got["summary"]["h2h"]["won"] == 1
    assert c.get("/api/tennis/facts", params={"home": "a", "away": "b", "date": "bad"}).status_code == 400
    listed = main._with_tennis_form([{"home": "Sinner, Jannik", "away": "Nobody"}])
    assert listed[0]["home_form"] == "WWLLW" and listed[0]["away_form"] == ""
    main._tennis_players.clear()
