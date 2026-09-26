import pandas as pd

import match_facts as mf


def _frame(rows, league="PL"):
    return pd.DataFrame([{"Date": d, "HomeTeam": h, "AwayTeam": a, "FTHG": hg, "FTAG": ag, "league": league}
                         for d, h, a, hg, ag in rows])


def test_last_five_newest_first_before_the_match():
    idx = mf.index([mf.frame(_frame([
        ("2026-08-01", "Arsenal", "Chelsea", 2, 0),
        ("2026-08-08", "Spurs", "Arsenal", 1, 1),
        ("2026-08-15", "Arsenal", "Everton", 0, 1),
        ("2026-08-22", "Leeds", "Arsenal", 0, 3),
        ("2026-08-29", "Arsenal", "Fulham", 4, 2),
        ("2026-09-05", "Wolves", "Arsenal", 2, 2),
        ("2026-09-20", "Arsenal", "Villa", 1, 0),   # the match day itself and later: left out
    ]))])
    rows = mf.last_matches(idx, "Arsenal", "2026-09-20")
    assert [r["date"] for r in rows] == ["2026-09-05", "2026-08-29", "2026-08-22", "2026-08-15", "2026-08-08"]
    assert [r["outcome"] for r in rows] == ["D", "W", "W", "L", "D"]
    assert rows[0]["venue"] == "A" and rows[0]["opponent"] == "Wolves"
    assert rows[1]["comp"] == "Premier League"


def test_meetings_from_the_home_sides_view_and_empty_when_never_met():
    idx = mf.index([mf.frame(_frame([
        ("2025-01-01", "Arsenal", "Chelsea", 2, 0),
        ("2025-05-01", "Chelsea", "Arsenal", 3, 1),
        ("2026-01-01", "Everton", "Fulham", 0, 0),
    ]))])
    h2h = mf.meetings(idx, "Arsenal", "Chelsea", "2026-09-20")
    assert [(r["date"], r["outcome"]) for r in h2h] == [("2025-05-01", "L"), ("2025-01-01", "W")]
    assert mf.meetings(idx, "Arsenal", "Fulham", "2026-09-20") == []
    f = mf.facts(idx, "Arsenal", "Fulham", "2026-09-20")
    assert f["h2h"] == [] and f["summary"]["h2h"] is None
    assert "No previous meetings" in mf.text(f, "Arsenal", "Fulham")


def test_one_match_from_two_sources_a_day_apart_counts_once():
    recent = pd.DataFrame([{"Date": "2026-09-14", "HomeTeam": "Arsenal", "AwayTeam": "Chelsea",
                            "FTHG": 1, "FTAG": 0, "comp": "Premier League"}])
    csv = _frame([("2026-09-13", "Arsenal", "Chelsea", 1, 0), ("2026-01-02", "Arsenal", "Chelsea", 0, 0)])
    idx = mf.index([recent, mf.frame(csv)])
    assert [r["date"] for r in mf.meetings(idx, "Arsenal", "Chelsea", "2026-09-20")] == ["2026-09-14", "2026-01-02"]


def test_cup_and_international_names():
    assert mf.comp_name("CL") == "Champions League"
    assert mf.comp_name("FAC") == "FA Cup"
    assert mf.comp_name("INT") == "International"


def test_summary_counts_goals_for_the_team():
    idx = mf.index([mf.frame(_frame([("2026-08-01", "Arsenal", "Chelsea", 2, 0),
                                     ("2026-08-08", "Spurs", "Arsenal", 3, 1)]))])
    s = mf.summary(mf.last_matches(idx, "Arsenal", "2026-09-01"))
    assert s == {"played": 2, "won": 1, "drawn": 0, "lost": 1, "scored": 3, "conceded": 3}
