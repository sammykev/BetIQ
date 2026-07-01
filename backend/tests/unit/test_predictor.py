"""
Unit tests for predictor.py — EloSystem, _ewm helper, and LeaguePredictor.
These tests exercise pure mathematical logic without external I/O.
"""

import pytest
import numpy as np
import pandas as pd

from predictor import EloSystem, _ewm, LeaguePredictor, FIFA_ELO_SEEDS, FEATURE_COLS


# ── EloSystem ──────────────────────────────────────────────────────────────


class TestEloSystemDefaults:
    def test_unknown_club_defaults_to_1500(self):
        elo = EloSystem()
        assert elo.get("Unknown FC") == 1500.0

    def test_national_team_uses_fifa_seed(self):
        elo = EloSystem()
        assert elo.get("Argentina") == pytest.approx(FIFA_ELO_SEEDS["Argentina"])
        assert elo.get("France") == pytest.approx(FIFA_ELO_SEEDS["France"])

    def test_stored_rating_overrides_seed(self):
        elo = EloSystem()
        elo.ratings["Argentina"] = 1800.0
        assert elo.get("Argentina") == 1800.0

    def test_stored_rating_overrides_default(self):
        elo = EloSystem()
        elo.ratings["Fake FC"] = 1750.0
        assert elo.get("Fake FC") == 1750.0


class TestEloSystemExpected:
    def test_equal_teams_home_advantage_pushes_above_half(self):
        elo = EloSystem()
        elo.ratings["A"] = 1500.0
        elo.ratings["B"] = 1500.0
        assert elo.expected("A", "B") > 0.5

    def test_expected_bounded_between_zero_and_one(self):
        elo = EloSystem()
        elo.ratings["Strong"] = 2100.0
        elo.ratings["Weak"] = 900.0
        e = elo.expected("Strong", "Weak")
        assert 0.0 < e < 1.0

    def test_much_stronger_home_team_has_high_expected(self):
        elo = EloSystem()
        elo.ratings["Strong"] = 2000.0
        elo.ratings["Weak"] = 1000.0
        assert elo.expected("Strong", "Weak") > 0.9

    def test_home_advantage_is_asymmetric(self):
        elo = EloSystem()
        elo.ratings["A"] = 1600.0
        elo.ratings["B"] = 1400.0
        # A is stronger AND at home, so expected(A, B) > expected(B, A)
        assert elo.expected("A", "B") > elo.expected("B", "A")


class TestEloSystemUpdate:
    def _fresh_pair(self):
        elo = EloSystem()
        elo.ratings["Home"] = 1500.0
        elo.ratings["Away"] = 1500.0
        return elo

    def test_home_win_raises_home_lowers_away(self):
        elo = self._fresh_pair()
        elo.update("Home", "Away", "H")
        assert elo.get("Home") > 1500.0
        assert elo.get("Away") < 1500.0

    def test_away_win_raises_away_lowers_home(self):
        elo = self._fresh_pair()
        elo.update("Home", "Away", "A")
        assert elo.get("Away") > 1500.0
        assert elo.get("Home") < 1500.0

    def test_draw_with_equal_teams_penalises_home(self):
        # Home team expected to win (home advantage); draw = underperformance → rating drops
        elo = self._fresh_pair()
        elo.update("Home", "Away", "D")
        assert elo.get("Home") < 1500.0
        assert elo.get("Away") > 1500.0

    def test_update_is_zero_sum(self):
        elo = self._fresh_pair()
        before = elo.get("Home") + elo.get("Away")
        elo.update("Home", "Away", "H")
        after = elo.get("Home") + elo.get("Away")
        assert after == pytest.approx(before, abs=1e-9)

    def test_upset_result_causes_larger_rating_swing(self):
        elo = EloSystem()
        elo.ratings["Weak"] = 1200.0
        elo.ratings["Strong"] = 1800.0
        before_weak = elo.get("Weak")
        # Weak team wins away — huge upset → big delta
        elo.update("Strong", "Weak", "A")
        delta = elo.get("Weak") - before_weak
        assert delta > 10  # significant gain for upset away win


class TestEloSystemSeed:
    def test_seed_populates_all_known_nations(self):
        elo = EloSystem()
        elo.seed_national_teams()
        for team in ("Argentina", "France", "England", "Brazil"):
            assert team in elo.ratings

    def test_seed_does_not_overwrite_existing_ratings(self):
        elo = EloSystem()
        elo.ratings["Argentina"] = 9999.0
        elo.seed_national_teams()
        assert elo.ratings["Argentina"] == 9999.0


# ── _ewm ──────────────────────────────────────────────────────────────────


class TestEwm:
    def test_empty_list_returns_zero(self):
        assert _ewm([]) == 0.0

    def test_single_value_returns_that_value(self):
        assert _ewm([7.0]) == pytest.approx(7.0)

    def test_constant_series_returns_constant(self):
        assert _ewm([3.0, 3.0, 3.0, 3.0]) == pytest.approx(3.0, abs=0.01)

    def test_recent_values_weighted_more_heavily(self):
        # Recent spike should pull ewm above the simple mean
        values = [1.0, 1.0, 1.0, 1.0, 5.0]
        assert _ewm(values) > sum(values) / len(values)

    def test_uses_only_last_12_values(self):
        long = list(range(20))
        assert _ewm(long) == pytest.approx(_ewm(long[-12:]))

    def test_returns_float(self):
        assert isinstance(_ewm([1.0, 2.0, 3.0]), float)

    def test_all_zeros_returns_zero(self):
        assert _ewm([0.0, 0.0, 0.0]) == pytest.approx(0.0)


# ── LeaguePredictor (untrained) ───────────────────────────────────────────


class TestLeaguePredictorUntrained:
    def test_predict_match_returns_none_before_training(self):
        p = LeaguePredictor()
        assert p.predict_match("Arsenal", "Chelsea") is None

    def test_predict_match_full_returns_none_before_training(self):
        p = LeaguePredictor()
        assert p.predict_match_full("Arsenal", "Chelsea") is None

    def test_ready_flag_is_false_before_training(self):
        p = LeaguePredictor()
        assert p._ready is False


# ── LeaguePredictor (trained) ─────────────────────────────────────────────


class TestLeaguePredictorTrained:
    def test_ready_flag_set_after_training(self, trained_predictor):
        assert trained_predictor._ready is True

    def test_elo_ratings_populated_after_training(self, trained_predictor):
        assert "Arsenal" in trained_predictor.elo.ratings
        assert "Chelsea" in trained_predictor.elo.ratings

    def test_team_stats_populated_after_training(self, trained_predictor):
        assert "Arsenal" in trained_predictor.team_stats
        assert len(trained_predictor.team_stats["Arsenal"]["gf"]) > 0

    def test_predict_match_returns_dict(self, trained_predictor):
        result = trained_predictor.predict_match("Arsenal", "Chelsea")
        assert isinstance(result, dict)

    def test_predict_match_has_all_required_keys(self, trained_predictor):
        result = trained_predictor.predict_match("Arsenal", "Chelsea")
        expected_keys = {
            "p_home", "p_draw", "p_away",
            "p_over15", "p_over25",
            "tip_1x2", "tip_code",
            "tip_goals", "goals_type", "goals_confidence",
        }
        assert expected_keys.issubset(result.keys())

    def test_probabilities_sum_to_one(self, trained_predictor):
        result = trained_predictor.predict_match("Arsenal", "Chelsea")
        total = result["p_home"] + result["p_draw"] + result["p_away"]
        assert total == pytest.approx(1.0, abs=0.01)

    def test_all_probabilities_in_valid_range(self, trained_predictor):
        result = trained_predictor.predict_match("Arsenal", "Chelsea")
        for key in ("p_home", "p_draw", "p_away", "p_over15", "p_over25"):
            val = result[key]
            assert 0.0 <= val <= 1.0, f"{key}={val} is outside [0, 1]"

    def test_tip_1x2_is_a_valid_label(self, trained_predictor):
        result = trained_predictor.predict_match("Arsenal", "Chelsea")
        valid = {"Home Win", "Away Win", "Draw", "Home or Draw", "Away or Draw", "Skip"}
        assert result["tip_1x2"] in valid

    def test_tip_code_is_a_valid_code(self, trained_predictor):
        result = trained_predictor.predict_match("Arsenal", "Chelsea")
        valid = {"1", "2", "X", "1X", "2X", "?"}
        assert result["tip_code"] in valid

    def test_tip_goals_is_a_valid_label(self, trained_predictor):
        result = trained_predictor.predict_match("Arsenal", "Chelsea")
        valid = {
            "Over 1.5 Goals", "Over 2.0 (Asian)", "Over 1.0 (Asian)",
            "Under 3.0 (Asian)", "Skip",
        }
        assert result["tip_goals"] in valid

    def test_predict_with_odds_returns_valid_result(self, trained_predictor):
        result = trained_predictor.predict_match(
            "Arsenal", "Chelsea", odds_home=2.5, odds_draw=3.2, odds_away=2.8
        )
        assert result is not None
        assert result["p_home"] + result["p_draw"] + result["p_away"] == pytest.approx(1.0, abs=0.01)

    def test_unknown_teams_use_default_stats(self, trained_predictor):
        result = trained_predictor.predict_match("Unknown FC", "Mystery United")
        assert result is not None
        assert "p_home" in result
        assert 0.0 <= result["p_home"] <= 1.0

    def test_goals_confidence_in_range(self, trained_predictor):
        result = trained_predictor.predict_match("Arsenal", "Chelsea")
        assert 0.0 <= result["goals_confidence"] <= 1.0


class TestLeaguePredictorFullMarkets:
    def test_predict_match_full_returns_markets(self, trained_predictor):
        result = trained_predictor.predict_match_full("Arsenal", "Chelsea")
        assert result is not None
        assert "markets" in result
        assert len(result["markets"]) > 0

    def test_predict_match_full_has_xg(self, trained_predictor):
        result = trained_predictor.predict_match_full("Arsenal", "Chelsea")
        assert "xg_home" in result
        assert "xg_away" in result
        assert result["xg_home"] > 0
        assert result["xg_away"] > 0

    def test_predict_match_full_has_elo_context(self, trained_predictor):
        result = trained_predictor.predict_match_full("Arsenal", "Chelsea")
        elo = result["elo"]
        for key in ("home", "away", "gap", "label", "description", "implied_win_prob"):
            assert key in elo

    def test_elo_implied_win_prob_in_range(self, trained_predictor):
        result = trained_predictor.predict_match_full("Arsenal", "Chelsea")
        p = result["elo"]["implied_win_prob"]
        assert 0.0 < p < 1.0

    def test_all_market_option_probs_in_range(self, trained_predictor):
        result = trained_predictor.predict_match_full("Arsenal", "Chelsea")
        for market in result["markets"]:
            for opt in market["options"]:
                assert 0.0 <= opt["prob"] <= 1.0, (
                    f"Market '{market['id']}' option '{opt['label']}' prob={opt['prob']}"
                )

    def test_recommended_pick_exists(self, trained_predictor):
        result = trained_predictor.predict_match_full("Arsenal", "Chelsea")
        assert result["recommended"] is not None
        assert "prob" in result["recommended"]
        assert "label" in result["recommended"]

    def test_market_ids_include_expected_types(self, trained_predictor):
        result = trained_predictor.predict_match_full("Arsenal", "Chelsea")
        market_ids = {m["id"] for m in result["markets"]}
        for expected_id in ("1x2", "btts", "goals_ou", "asian_handicap"):
            assert expected_id in market_ids


class TestLeaguePredictorImpliedOdds:
    def test_implied_probs_sum_to_one_with_valid_odds(self, trained_predictor):
        feats = trained_predictor._feats(
            "Arsenal", "Chelsea", odds_home=2.0, odds_draw=3.5, odds_away=4.0
        )
        impl_sum = feats["Impl_Home"] + feats["Impl_Draw"] + feats["Impl_Away"]
        assert impl_sum == pytest.approx(1.0, abs=0.001)

    def test_falls_back_to_league_avg_when_no_odds(self, trained_predictor):
        feats_no_odds = trained_predictor._feats("Arsenal", "Chelsea")
        feats_zero_odds = trained_predictor._feats("Arsenal", "Chelsea", 0, 0, 0)
        assert feats_no_odds["Impl_Home"] == pytest.approx(feats_zero_odds["Impl_Home"])

    def test_overround_is_removed(self, trained_predictor):
        # Odds with 10% overround — after normalization, implied probs should sum to 1.0
        feats = trained_predictor._feats(
            "Arsenal", "Chelsea", odds_home=1.9, odds_draw=3.3, odds_away=4.2
        )
        impl_sum = feats["Impl_Home"] + feats["Impl_Draw"] + feats["Impl_Away"]
        assert impl_sum == pytest.approx(1.0, abs=0.001)

    def test_favourite_has_highest_implied_prob(self, trained_predictor):
        # Odds: home 1.5 (favourite), draw 4.0, away 7.0
        feats = trained_predictor._feats(
            "Arsenal", "Chelsea", odds_home=1.5, odds_draw=4.0, odds_away=7.0
        )
        assert feats["Impl_Home"] > feats["Impl_Draw"]
        assert feats["Impl_Home"] > feats["Impl_Away"]
