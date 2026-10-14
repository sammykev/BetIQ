"""SportyBet league history for the clubs other sources don't cover (collect_sb_leagues.py)."""

from datetime import datetime, timezone

import collect_sb_leagues as sbl


def ev(eid, home, away, score, tournament, day=13):
    ts = int(datetime(2026, 10, day, 18, tzinfo=timezone.utc).timestamp() * 1000)
    return {"eventId": eid, "homeTeamName": home, "awayTeamName": away, "estimateStartTime": ts, "status": 4,
            "setScore": score, "matchStatus": "Ended", "_tournament": tournament}


def test_keeps_league_rows_and_uefa_clubs():
    data = sbl.empty()
    n = sbl.take([
        ev("1", "Hajduk Split", "Dinamo Zagreb", "2:1", "Croatia · HNL"),
        ev("2", "Rudes", "Osijek", "0:0", "Croatia · HNL"),
        ev("3", "Hajduk Split", "Ajax", "1:1", "International Clubs · UEFA Conference League"),
        ev("4", "Dinamo Zagreb", "Anderlecht", "2:0", "International Clubs · UEFA Europa League"),
        ev("5", "Hajduk Split II", "X", "1:0", "Croatia · HNL"),                  # reserves
        ev("6", "Hajduk Split", "Rijeka", "3:0", "Croatia · Croatian Cup"),          # a cup
        ev("7", "Varazdin", "Sesvete", "1:1", "Croatia · Prva NL"),                  # second division
        ev("8", "Arsenal", "Chelsea", "1:0", "England · Premier League"),           # covered elsewhere
        ev("9", "Hajduk Split U19", "Ajax U19", "1:0", "International Youth · UEFA Youth League"),
    ], data)
    assert n == 3 and set(data["uefa"]) == {"Hajduk Split", "Ajax", "Dinamo Zagreb", "Anderlecht"}
    assert sbl.selected(data) == {"SB-CRO": "HNL"}                                 # the top flight, not Prva NL
    df = sbl.rows_frame(data)
    assert len(df) == 2 and set(df["league"]) == {"SB-CRO"} and list(df["Result"]) == ["H", "D"]


def test_a_cup_stored_earlier_is_never_the_top_flight():
    import collect_sb_leagues as c
    data = c.empty()
    data["uefa"] = {"Riga FC": "2025-08-01", "RFS": "2025-08-01"}
    row = lambda t, d: {"Date": d, "HomeTeam": "Riga FC", "AwayTeam": "RFS", "FTHG": 1, "FTAG": 0,
                        "Result": "H", "league": "SB-LVA", "tournament": t}
    data["rows"] = {"1": row("Latvijas Kauss", "2025-05-01"), "2": row("Virsliga", "2025-06-01")}
    assert c.selected(data) == {"SB-LVA": "Virsliga"}
