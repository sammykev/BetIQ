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


def _stats_frame(rows):
    """(date, home, away, hg, ag, HC, AC, HY, AY, HR, AR, HS, AS, HST, AST)"""
    keys = ("Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "HC", "AC", "HY", "AY", "HR", "AR", "HS", "AS", "HST", "AST")
    return pd.DataFrame([{**dict(zip(keys, r)), "league": "PL"} for r in rows])


class TestAverages:
    def idx(self):
        return mf.index([mf.frame(_stats_frame([
            ("2026-09-01", "Arsenal", "Chelsea", 2, 1, 7, 3, 2, 1, 0, 1, 15, 8, 6, 3),
            ("2026-09-08", "Spurs", "Arsenal", 0, 0, 5, 5, 1, 2, 0, 0, 10, 12, 3, 4),
            ("2026-09-15", "Arsenal", "Leeds", 3, 2, 9, 2, 0, 3, 0, 0, 18, 7, 8, 2),
        ]))])

    def test_every_market_from_the_teams_view(self):
        a = mf.averages(self.idx(), "Arsenal", "2026-09-20")
        assert a["played"] == 3
        assert a["goals"] == {"for": round(5 / 3, 2), "against": 1.0, "total": round(8 / 3, 2)}
        assert a["over"] == {"0.5": round(2 / 3, 3), "1.5": round(2 / 3, 3), "2.5": round(2 / 3, 3), "3.5": round(1 / 3, 3)}
        assert (a["btts"], a["clean_sheet"], a["failed_to_score"]) == (round(2 / 3, 3), round(1 / 3, 3), round(1 / 3, 3))
        assert a["results"] == {"won": round(2 / 3, 3), "drawn": round(1 / 3, 3), "lost": 0.0}
        # Away at Spurs, their stats are the second column
        assert a["corners"] == {"for": round((7 + 5 + 9) / 3, 2), "against": round((3 + 5 + 2) / 3, 2),
                                "total": round((10 + 10 + 11) / 3, 2), "matches": 3}
        assert a["bookings"]["for"] == round((2 + 2 + 0) / 3, 2)             # yellows + 2 × reds
        assert a["bookings"]["against"] == round((3 + 1 + 3) / 3, 2)
        assert a["sot"]["for"] == 6.0

    def test_stats_are_optional_and_can_come_from_elsewhere(self):
        idx = mf.index([mf.frame(_frame([("2026-09-01", "Ghana", "Mali", 1, 0)], league="INT"))])
        a = mf.averages(idx, "Ghana", "2026-09-20")
        assert a["goals"]["for"] == 1.0 and a["corners"] is None
        filled = mf.averages(idx, "Ghana", "2026-09-20",
                             stats_for=lambda d, h, aw: {"HC": 6, "AC": 2} if (d, h, aw) == ("2026-09-01", "Ghana", "Mali") else None)
        assert filled["corners"] == {"for": 6.0, "against": 2.0, "total": 8.0, "matches": 1}

    def test_a_copy_with_stats_fills_one_without(self):
        bare = _frame([("2026-09-01", "Arsenal", "Chelsea", 2, 1)])
        stats = _stats_frame([("2026-09-01", "Arsenal", "Chelsea", 2, 1, 7, 3, 2, 1, 0, 0, 15, 8, 6, 3)])
        idx = mf.index([mf.frame(bare), mf.frame(stats)])
        assert len(idx) == 1 and mf.averages(idx, "Arsenal", "2026-09-20")["corners"]["for"] == 7.0

    def test_in_the_facts(self):
        f = mf.facts(self.idx(), "Arsenal", "Chelsea", "2026-09-20")
        assert f["averages"]["n"] == mf.AVG_N and f["averages"]["home"]["played"] == 3
        assert f["averages"]["away"]["played"] == 1
        assert mf.averages(self.idx(), "Nobody", "2026-09-20") is None
