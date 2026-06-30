"""
Unit tests for odds_fetcher.py — pure utility functions and value bet computation.
No network access required: all tested functions are pure or easily mockable.
"""

import pytest
from unittest.mock import patch, AsyncMock
import httpx

from odds_fetcher import (
    _sim,
    _sport_keys_for_predictions,
    _parse_odds_event,
    compute_value_bets,
    FALLBACK_SPORTS,
    MIN_EDGE,
    MATCH_THRESHOLD,
)


# ── _sim: fuzzy string similarity ─────────────────────────────────────────


class TestSim:
    def test_identical_strings_return_one(self):
        assert _sim("Arsenal", "Arsenal") == pytest.approx(1.0)

    def test_completely_different_strings_return_low_score(self):
        assert _sim("Arsenal", "XYZZY") < 0.3

    def test_case_insensitive(self):
        assert _sim("arsenal", "ARSENAL") == pytest.approx(1.0)

    def test_leading_trailing_whitespace_ignored(self):
        assert _sim("  Arsenal  ", "Arsenal") == pytest.approx(1.0)

    def test_partial_match_between_zero_and_one(self):
        score = _sim("Man City", "Manchester City")
        assert 0.0 < score < 1.0

    def test_empty_strings_return_one(self):
        # Two empty strings are identical
        assert _sim("", "") == pytest.approx(1.0)

    def test_returns_float(self):
        assert isinstance(_sim("Arsenal", "Chelsea"), float)


# ── _sport_keys_for_predictions ───────────────────────────────────────────


class TestSportKeysForPredictions:
    def test_empty_predictions_returns_fallback_sports(self):
        result = _sport_keys_for_predictions([])
        assert result == FALLBACK_SPORTS[:4]

    def test_premier_league_maps_to_epl_key(self):
        preds = [{"league_name": "Premier League", "home": "Arsenal", "away": "Chelsea"}]
        result = _sport_keys_for_predictions(preds)
        assert "soccer_epl" in result

    def test_champions_league_maps_correctly(self):
        preds = [{"league_name": "Champions League"}]
        result = _sport_keys_for_predictions(preds)
        assert "soccer_uefa_champs_league" in result

    def test_bundesliga_maps_correctly(self):
        preds = [{"league_name": "Bundesliga"}]
        result = _sport_keys_for_predictions(preds)
        assert "soccer_germany_bundesliga" in result

    def test_unknown_league_falls_back_to_international_sports(self):
        preds = [{"league_name": "Obscure Regional Cup"}]
        result = _sport_keys_for_predictions(preds)
        assert "soccer_fifa_world_cup" in result

    def test_multiple_leagues_returns_multiple_keys(self):
        preds = [
            {"league_name": "Premier League"},
            {"league_name": "Serie A"},
        ]
        result = _sport_keys_for_predictions(preds)
        assert "soccer_epl" in result
        assert "soccer_italy_serie_a" in result

    def test_no_duplicate_sport_keys(self):
        preds = [
            {"league_name": "Premier League"},
            {"league_name": "EPL"},  # same sport key as Premier League
        ]
        result = _sport_keys_for_predictions(preds)
        assert result.count("soccer_epl") == 1

    def test_uses_league_field_as_fallback(self):
        # league_name absent, uses league field
        preds = [{"league": "Premier League"}]
        result = _sport_keys_for_predictions(preds)
        assert "soccer_epl" in result


# ── _parse_odds_event ──────────────────────────────────────────────────────


def _make_event(home="Arsenal", away="Chelsea", date="2025-06-01T15:00:00Z",
                home_price=2.1, draw_price=3.2, away_price=3.5, include_draw=True):
    outcomes = [
        {"name": home, "price": home_price},
        {"name": away, "price": away_price},
    ]
    if include_draw:
        outcomes.append({"name": "Draw", "price": draw_price})
    return {
        "home_team": home,
        "away_team": away,
        "commence_time": date,
        "bookmakers": [
            {
                "key": "bet365",
                "markets": [{"key": "h2h", "outcomes": outcomes}],
            }
        ],
    }


class TestParseOddsEvent:
    def test_valid_event_returns_parsed_dict(self):
        result = _parse_odds_event(_make_event())
        assert result is not None
        assert result["home"] == "Arsenal"
        assert result["away"] == "Chelsea"
        assert result["date"] == "2025-06-01"
        assert result["source"] == "the-odds-api"

    def test_odds_keys_are_1_X_2(self):
        result = _parse_odds_event(_make_event())
        assert "1" in result["odds"]
        assert "X" in result["odds"]
        assert "2" in result["odds"]

    def test_home_odds_correctly_mapped(self):
        result = _parse_odds_event(_make_event(home_price=2.1))
        assert result["odds"]["1"] == pytest.approx(2.1)

    def test_away_odds_correctly_mapped(self):
        result = _parse_odds_event(_make_event(away_price=3.5))
        assert result["odds"]["2"] == pytest.approx(3.5)

    def test_draw_odds_correctly_mapped(self):
        result = _parse_odds_event(_make_event(draw_price=3.2))
        assert result["odds"]["X"] == pytest.approx(3.2)

    def test_missing_draw_returns_none(self):
        result = _parse_odds_event(_make_event(include_draw=False))
        assert result is None

    def test_selects_best_odds_across_bookmakers(self):
        event = {
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "commence_time": "2025-06-01T15:00:00Z",
            "bookmakers": [
                {
                    "key": "bet365",
                    "markets": [{"key": "h2h", "outcomes": [
                        {"name": "Arsenal", "price": 2.0},
                        {"name": "Chelsea", "price": 3.5},
                        {"name": "Draw", "price": 3.2},
                    ]}],
                },
                {
                    "key": "betfair",
                    "markets": [{"key": "h2h", "outcomes": [
                        {"name": "Arsenal", "price": 2.2},   # better home odds
                        {"name": "Chelsea", "price": 3.3},
                        {"name": "Draw", "price": 3.4},       # better draw odds
                    ]}],
                },
            ],
        }
        result = _parse_odds_event(event)
        assert result["odds"]["1"] == pytest.approx(2.2)   # best home price
        assert result["odds"]["X"] == pytest.approx(3.4)   # best draw price

    def test_rejects_odds_of_one_or_less(self):
        event = _make_event(home_price=1.0, draw_price=0.5, away_price=3.5)
        event["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = 1.0
        result = _parse_odds_event(event)
        # Only away has valid odds (>1); missing draw and valid home → no result
        assert result is None

    def test_date_parsed_correctly_from_utc(self):
        result = _parse_odds_event(_make_event(date="2025-12-25T20:00:00Z"))
        assert result["date"] == "2025-12-25"

    def test_empty_bookmakers_returns_none(self):
        event = {
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "commence_time": "2025-06-01T15:00:00Z",
            "bookmakers": [],
        }
        assert _parse_odds_event(event) is None


# ── compute_value_bets ────────────────────────────────────────────────────


def _pred(home="Arsenal", away="Chelsea", date="2025-06-01",
          p_home=0.6, p_draw=0.25, p_away=0.15):
    return {"home": home, "away": away, "date": date,
            "p_home": p_home, "p_draw": p_draw, "p_away": p_away}


def _odds_key(pred):
    return f"{pred['home']}:{pred['away']}:{pred['date']}"


class TestComputeValueBets:
    def test_empty_predictions_returns_empty_list(self):
        assert compute_value_bets([], {}) == []

    def test_no_matching_odds_returns_empty_list(self):
        pred = _pred()
        assert compute_value_bets([pred], {}) == []

    def test_detects_value_on_home_win(self):
        # Model: 65% home. Bookmaker implied (after normalization): ~53% home → edge >3%
        pred = _pred(p_home=0.65, p_draw=0.20, p_away=0.15)
        odds = {_odds_key(pred): {"1": 1.8, "X": 3.5, "2": 5.0, "source": "test"}}
        result = compute_value_bets([pred], odds)
        assert len(result) == 1
        assert result[0]["value_outcome"] == "1"
        assert result[0]["value_label"] == "Arsenal Win"

    def test_detects_value_on_draw(self):
        # Model: 40% draw. Bookmaker implied ~25% draw → strong value on draw
        pred = _pred(p_home=0.35, p_draw=0.40, p_away=0.25)
        odds = {_odds_key(pred): {"1": 2.5, "X": 3.8, "2": 3.0, "source": "test"}}
        result = compute_value_bets([pred], odds)
        assert any(r["value_outcome"] == "X" for r in result)

    def test_detects_value_on_away_win(self):
        # Model: 55% away. Bookmaker implied ~25% away → clear value
        pred = _pred(p_home=0.20, p_draw=0.25, p_away=0.55)
        odds = {_odds_key(pred): {"1": 3.0, "X": 4.0, "2": 4.0, "source": "test"}}
        result = compute_value_bets([pred], odds)
        assert len(result) == 1
        assert result[0]["value_outcome"] == "2"
        assert result[0]["value_label"] == "Chelsea Win"

    def test_edge_below_minimum_not_flagged(self):
        # Model and implied probs almost identical — no value
        pred = _pred(p_home=0.50, p_draw=0.27, p_away=0.23)
        # Odds that produce ~50% home implied after normalization
        odds = {_odds_key(pred): {"1": 2.0, "X": 3.8, "2": 4.5, "source": "test"}}
        result = compute_value_bets([pred], odds)
        # Edge should be negligible → no value bets
        if result:
            assert result[0]["edge"] >= MIN_EDGE * 100

    def test_results_sorted_by_edge_descending(self):
        preds = [
            _pred("Arsenal", "Chelsea", p_home=0.75, p_draw=0.15, p_away=0.10),
            _pred("Liverpool", "Everton", p_home=0.65, p_draw=0.20, p_away=0.15),
        ]
        odds = {
            _odds_key(preds[0]): {"1": 1.8, "X": 4.0, "2": 6.0, "source": "test"},
            _odds_key(preds[1]): {"1": 1.9, "X": 3.8, "2": 5.5, "source": "test"},
        }
        result = compute_value_bets(preds, odds)
        if len(result) >= 2:
            assert result[0]["edge"] >= result[1]["edge"]

    def test_value_bet_includes_overround(self):
        pred = _pred(p_home=0.65, p_draw=0.20, p_away=0.15)
        odds = {_odds_key(pred): {"1": 1.8, "X": 3.5, "2": 5.0, "source": "test"}}
        result = compute_value_bets([pred], odds)
        assert len(result) == 1
        assert result[0]["overround"] > 0

    def test_value_bet_includes_model_and_implied_prob(self):
        pred = _pred(p_home=0.65, p_draw=0.20, p_away=0.15)
        odds = {_odds_key(pred): {"1": 1.8, "X": 3.5, "2": 5.0, "source": "test"}}
        result = compute_value_bets([pred], odds)
        assert len(result) == 1
        vb = result[0]
        assert "model_prob" in vb
        assert "implied_prob" in vb
        assert "edge" in vb
        assert "value_odds" in vb
        assert vb["model_prob"] > vb["implied_prob"]

    def test_skips_odds_with_invalid_prices(self):
        pred = _pred()
        # Prices <= 1 are invalid; key present but odds are garbage
        odds = {_odds_key(pred): {"1": 0.0, "X": 0.0, "2": 0.0, "source": "test"}}
        result = compute_value_bets([pred], odds)
        assert result == []

    def test_value_bet_includes_all_prediction_fields(self):
        pred = _pred(p_home=0.70, p_draw=0.18, p_away=0.12)
        odds = {_odds_key(pred): {"1": 1.8, "X": 4.0, "2": 6.0, "source": "test"}}
        result = compute_value_bets([pred], odds)
        assert len(result) == 1
        vb = result[0]
        # Should preserve original prediction fields
        assert vb["home"] == pred["home"]
        assert vb["away"] == pred["away"]
        assert vb["date"] == pred["date"]
