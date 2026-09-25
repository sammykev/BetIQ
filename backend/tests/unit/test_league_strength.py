"""
League strength (predictor.LEAGUE_STRENGTH): one rating scale across
leagues, learned from European / cup matches fed as ratings-only rows.
"""

import pandas as pd

import predictor as pr


def model(on=True):
    m = pr.LeaguePredictor()
    m.use_league_strength = on
    return m


class TestStrength:
    def test_off_by_default_and_then_plain_elo(self):
        m = pr.LeaguePredictor()
        assert m.use_league_strength is pr.LEAGUE_STRENGTH
        m.use_league_strength = False
        m._note_league("Burnley", "ELC")
        assert m.strength("Burnley") == m.elo.get("Burnley")

    def test_league_offset_prior(self):
        m = model()
        m._note_league("Arsenal", "PL")
        m._note_league("Burnley", "ELC")
        assert m.strength("Arsenal") - m.strength("Burnley") == -pr.LEAGUE_OFFSET_PRIOR["ELC"]

    def test_promotion_keeps_real_strength(self):
        m = model()
        m._note_league("Burnley", "ELC")
        m.elo.ratings["Burnley"] = 1700.0     # top of the Championship
        before = m.strength("Burnley")
        m._note_league("Burnley", "PL")
        assert m.team_league["Burnley"] == "PL"
        assert abs(m.strength("Burnley") - before) < 1e-9          # same team, same strength
        assert m.elo.ratings["Burnley"] < 1700.0                    # but a lower PL rating

    def test_cross_league_results_move_the_offsets(self):
        m = model()
        m._note_league("Ajax", "DED")
        m._note_league("Arsenal", "PL")
        start = m._offset("Ajax")
        m._strength_update("Ajax", "Arsenal", "H", "CL")            # the weaker league's team wins
        assert m._offset("Ajax") > start and m._offset("Arsenal") < 0
        assert m.elo.get("Ajax") > 1500

    def test_same_league_cup_ties_leave_offsets(self):
        m = model()
        for t in ("Arsenal", "Chelsea"):
            m._note_league(t, "PL")
        m._strength_update("Arsenal", "Chelsea", "A", "FAC")
        assert m.league_offset == {}


class TestTraining:
    def test_ratings_only_rows_skip_the_training_set(self):
        rows = []
        day = pd.Timestamp("2023-08-01")
        teams = ["A", "B", "C", "D"]
        for i in range(160):
            h, a = teams[i % 4], teams[(i + 1 + i // 4) % 4]
            if h == a:
                continue
            rows.append({"Date": day + pd.Timedelta(days=i), "HomeTeam": h, "AwayTeam": a, "FTHG": i % 3,
                         "FTAG": (i + 1) % 2, "Result": "H" if i % 3 > (i + 1) % 2 else "A" if i % 3 < (i + 1) % 2 else "D",
                         "league": "PL"})
        league = pd.DataFrame(rows)
        cup = pd.DataFrame([{"Date": day + pd.Timedelta(days=5), "HomeTeam": "A", "AwayTeam": "Z", "FTHG": 5,
                             "FTAG": 0, "Result": "H", "league": "FAC", "StrengthOnly": True}])
        m = model()
        m.train(pd.concat([league, cup], ignore_index=True))
        assert "Z" not in m.team_stats                    # no form for a ratings-only team
        assert "Z" in m.elo.ratings and m.elo.ratings["Z"] < 1500
