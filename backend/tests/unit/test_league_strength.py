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


class TestContextRows:
    """Context rows (main._load_extra_leagues): ratings and form for their
    teams, no training rows, and nobody else's prediction changes."""

    def _league(self, n=240):
        import numpy as np
        rng = np.random.default_rng(3)
        teams = ["A", "B", "C", "D", "E", "F"]
        rows, day = [], pd.Timestamp("2023-08-01")
        for i in range(n):
            h, a = rng.choice(teams, 2, replace=False)
            hg, ag = int(rng.integers(0, 4)), int(rng.integers(0, 3))
            rows.append({"Date": day + pd.Timedelta(days=i), "HomeTeam": h, "AwayTeam": a, "FTHG": hg, "FTAG": ag,
                         "Result": "H" if hg > ag else "A" if hg < ag else "D", "league": "PL",
                         "B365H": 2.1, "B365D": 3.3, "B365A": 3.6, "HST": 5, "AST": 3})
        return pd.DataFrame(rows)

    def _context(self):
        rows, day = [], pd.Timestamp("2023-08-02")
        for i in range(80):
            h, a = ("Flamengo", "Santos") if i % 2 else ("Santos", "Flamengo")
            rows.append({"Date": day + pd.Timedelta(days=i * 3), "HomeTeam": h, "AwayTeam": a, "FTHG": 4, "FTAG": 0,
                         "Result": "H", "league": "BSA", "B365H": 1.2, "B365D": 6.0, "B365A": 12.0,
                         "HST": 11, "AST": 1, "Context": True})
        return pd.DataFrame(rows)

    def test_other_predictions_are_identical_and_context_teams_get_ratings(self):
        league = self._league()
        plain, withctx = pr.LeaguePredictor(), pr.LeaguePredictor()
        plain.train(league)
        withctx.train(pd.concat([league, self._context()], ignore_index=True).sort_values("Date", kind="stable"))
        for h, a in (("A", "B"), ("C", "F"), ("E", "D")):
            assert plain.predict_match(h, a) == withctx.predict_match(h, a)
            assert plain.predict_match(h, a, 1.9, 3.4, 4.2) == withctx.predict_match(h, a, 1.9, 3.4, 4.2)
        assert "Flamengo" not in plain.team_stats
        assert len(withctx.team_stats["Flamengo"]["gf"]) == 80
        assert withctx.elo.get("Flamengo") != 1500 and withctx._league_home_goals == plain._league_home_goals
