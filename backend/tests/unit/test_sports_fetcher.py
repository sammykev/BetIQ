"""
Unit tests for sports_fetcher.py — started-event filtering and dynamic
basketball league discovery. Network access is mocked throughout.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock, MagicMock

import sports_fetcher
from sports_fetcher import (
    _basketball_league_name,
    _discover_basketball_leagues,
    fetch_basketball_predictions,
    _build_basketball_prediction,
    _classify_pick,
    _best_market_with_points,
    BASKETBALL_SPORTS,
)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _event(commence_time: str, home="Team A", away="Team B"):
    return {
        "home_team": home,
        "away_team": away,
        "commence_time": commence_time,
        "bookmakers": [
            {"markets": [{"key": "h2h", "outcomes": [
                {"name": home, "price": 1.8},
                {"name": away, "price": 2.0},
            ]}]}
        ],
    }


def _mock_response(status_code=200, json_body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_body if json_body is not None else [])
    resp.headers = {}
    return resp


def _mock_async_client(response):
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=response)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


# ── _fetch_odds: filters out already-started events ────────────────────────


class TestFetchOddsFiltersStartedEvents:
    async def test_future_event_kept(self):
        future = _iso(datetime.now(timezone.utc) + timedelta(hours=2))
        payload = [_event(future)]
        with patch.object(sports_fetcher, "ODDS_API_KEY", "fake-key"), \
             patch("sports_fetcher.httpx.AsyncClient", return_value=_mock_async_client(_mock_response(200, payload))):
            result = await sports_fetcher._fetch_odds("basketball_nba")
        assert len(result) == 1

    async def test_past_event_dropped(self):
        past = _iso(datetime.now(timezone.utc) - timedelta(hours=1))
        payload = [_event(past)]
        with patch.object(sports_fetcher, "ODDS_API_KEY", "fake-key"), \
             patch("sports_fetcher.httpx.AsyncClient", return_value=_mock_async_client(_mock_response(200, payload))):
            result = await sports_fetcher._fetch_odds("basketball_nba")
        assert result == []

    async def test_mixed_past_and_future_events(self):
        past = _iso(datetime.now(timezone.utc) - timedelta(minutes=30))
        future = _iso(datetime.now(timezone.utc) + timedelta(hours=3))
        payload = [_event(past, "Old A", "Old B"), _event(future, "New A", "New B")]
        with patch.object(sports_fetcher, "ODDS_API_KEY", "fake-key"), \
             patch("sports_fetcher.httpx.AsyncClient", return_value=_mock_async_client(_mock_response(200, payload))):
            result = await sports_fetcher._fetch_odds("basketball_nba")
        assert len(result) == 1
        assert result[0]["home_team"] == "New A"

    async def test_unparsed_commence_time_dropped(self):
        payload = [_event("not-a-date")]
        with patch.object(sports_fetcher, "ODDS_API_KEY", "fake-key"), \
             patch("sports_fetcher.httpx.AsyncClient", return_value=_mock_async_client(_mock_response(200, payload))):
            result = await sports_fetcher._fetch_odds("basketball_nba")
        assert result == []


# ── Dynamic basketball league discovery ─────────────────────────────────────


class TestBasketballLeagueName:
    def test_prefers_api_title(self):
        assert _basketball_league_name("basketball_nba_summer_league", "NBA Summer League") == "NBA Summer League"

    def test_falls_back_to_key_when_no_title(self):
        assert _basketball_league_name("basketball_nbl") == "Nbl"


class TestDiscoverBasketballLeagues:
    async def test_discovers_active_basketball_sports_only(self):
        active = [
            {"key": "basketball_nba", "title": "NBA"},
            {"key": "basketball_nba_summer_league", "title": "NBA Summer League"},
            {"key": "tennis_atp_wimbledon", "title": "Wimbledon"},  # not basketball — excluded
        ]
        with patch("sports_fetcher._get_active_sports_full", new_callable=AsyncMock, return_value=active):
            leagues = await _discover_basketball_leagues()
        keys = {l[0] for l in leagues}
        assert keys == {"basketball_nba", "basketball_nba_summer_league"}

    async def test_summer_league_included_when_active(self):
        active = [{"key": "basketball_nba_summer_league", "title": "NBA Summer League"}]
        with patch("sports_fetcher._get_active_sports_full", new_callable=AsyncMock, return_value=active):
            leagues = await _discover_basketball_leagues()
        names = {l[1] for l in leagues}
        assert "NBA Summer League" in names

    async def test_falls_back_to_static_list_when_discovery_empty(self):
        with patch("sports_fetcher._get_active_sports_full", new_callable=AsyncMock, return_value=[]):
            leagues = await _discover_basketball_leagues()
        assert leagues == BASKETBALL_SPORTS

    async def test_falls_back_to_static_list_on_exception(self):
        with patch("sports_fetcher._get_active_sports_full", new_callable=AsyncMock, side_effect=Exception("boom")):
            leagues = await _discover_basketball_leagues()
        assert leagues == BASKETBALL_SPORTS


class TestFetchBasketballPredictionsUsesDiscovery:
    async def test_uses_discovered_leagues_not_hardcoded_list(self):
        discovered = [("basketball_nba_summer_league", "NBA Summer League", "🏀")]
        with patch("sports_fetcher._discover_basketball_leagues", new_callable=AsyncMock, return_value=discovered), \
             patch("sports_fetcher._fetch_odds", new_callable=AsyncMock, return_value=[]) as mock_fetch:
            await fetch_basketball_predictions()
        mock_fetch.assert_called_once()
        assert mock_fetch.call_args[0][0] == "basketball_nba_summer_league"


# ── _classify_pick: safe / upset categorization ─────────────────────────────

class TestClassifyPick:
    def test_safe_when_model_agrees_with_favorite_and_confident(self):
        result = _classify_pick("1", p_home=0.75, p_away=0.25, market_p_home=0.7, market_p_away=0.3)
        assert result == "safe"

    def test_none_when_model_agrees_with_favorite_but_not_confident(self):
        result = _classify_pick("1", p_home=0.55, p_away=0.45, market_p_home=0.6, market_p_away=0.4)
        assert result is None

    def test_upset_when_model_picks_clear_market_underdog(self):
        result = _classify_pick("2", p_home=0.4, p_away=0.6, market_p_home=0.75, market_p_away=0.25)
        assert result == "upset"

    def test_none_when_market_gap_too_small_for_a_real_upset(self):
        # model picks the away side, but the market barely favors home —
        # not a genuine underdog pick
        result = _classify_pick("2", p_home=0.49, p_away=0.51, market_p_home=0.55, market_p_away=0.45)
        assert result is None

    def test_away_favorite_safe_pick(self):
        # symmetry check: market favors away, model confidently agrees
        result = _classify_pick("2", p_home=0.2, p_away=0.8, market_p_home=0.3, market_p_away=0.7)
        assert result == "safe"


# ── _best_market_with_points ────────────────────────────────────────────────

class TestBestMarketWithPoints:
    def test_extracts_price_and_point(self):
        event = {
            "bookmakers": [
                {"markets": [{"key": "spreads", "outcomes": [
                    {"name": "Lakers", "price": 1.91, "point": -4.5},
                    {"name": "Celtics", "price": 1.91, "point": 4.5},
                ]}]}
            ]
        }
        result = _best_market_with_points(event, "spreads")
        assert result["Lakers"] == {"price": 1.91, "point": -4.5}
        assert result["Celtics"] == {"price": 1.91, "point": 4.5}

    def test_keeps_best_price_across_bookmakers(self):
        event = {
            "bookmakers": [
                {"markets": [{"key": "spreads", "outcomes": [{"name": "Lakers", "price": 1.85, "point": -4.5}]}]},
                {"markets": [{"key": "spreads", "outcomes": [{"name": "Lakers", "price": 1.95, "point": -4.5}]}]},
            ]
        }
        result = _best_market_with_points(event, "spreads")
        assert result["Lakers"]["price"] == 1.95

    def test_ignores_other_markets(self):
        event = {
            "bookmakers": [{"markets": [{"key": "h2h", "outcomes": [{"name": "Lakers", "price": 1.5}]}]}]
        }
        result = _best_market_with_points(event, "spreads")
        assert result == {}


# ── _build_basketball_prediction: spreads + pick_type wiring ───────────────

class TestBuildBasketballPredictionSpreads:
    def _event_with_spreads(self):
        return {
            "home_team": "Lakers",
            "away_team": "Celtics",
            "commence_time": "2026-08-01T20:00:00Z",
            "bookmakers": [{"markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "Lakers", "price": 1.8},
                    {"name": "Celtics", "price": 2.0},
                ]},
                {"key": "spreads", "outcomes": [
                    {"name": "Lakers", "price": 1.91, "point": -4.5},
                    {"name": "Celtics", "price": 1.91, "point": 4.5},
                ]},
            ]}],
        }

    def test_spread_fields_populated(self):
        with patch("sports_fetcher._apply_elo_blend",
                   return_value={"p_home": 0.55, "p_away": 0.45, "elo_home": None, "elo_away": None}):
            pred = _build_basketball_prediction(self._event_with_spreads(), "NBA", "🏀")
        assert pred is not None
        assert pred["spread_home"] == {"point": -4.5, "odds": 1.91}
        assert pred["spread_away"] == {"point": 4.5, "odds": 1.91}

    def test_spread_fields_none_when_market_absent(self):
        event = {
            "home_team": "Lakers", "away_team": "Celtics",
            "commence_time": "2026-08-01T20:00:00Z",
            "bookmakers": [{"markets": [{"key": "h2h", "outcomes": [
                {"name": "Lakers", "price": 1.8}, {"name": "Celtics", "price": 2.0},
            ]}]}],
        }
        with patch("sports_fetcher._apply_elo_blend",
                   return_value={"p_home": 0.55, "p_away": 0.45, "elo_home": None, "elo_away": None}):
            pred = _build_basketball_prediction(event, "NBA", "🏀")
        assert pred["spread_home"] is None
        assert pred["spread_away"] is None

    def test_pick_type_included_for_confident_favorite(self):
        with patch("sports_fetcher._apply_elo_blend",
                   return_value={"p_home": 0.75, "p_away": 0.25, "elo_home": None, "elo_away": None}):
            pred = _build_basketball_prediction(self._event_with_spreads(), "NBA", "🏀")
        assert pred["pick_type"] == "safe"

    def test_pick_type_upset_when_model_favors_underdog(self):
        # Market strongly favors Lakers (1.3 vs 3.5 -> ~73%/27% implied), but
        # the model picks Celtics to win outright — a genuine upset call.
        event = {
            "home_team": "Lakers",
            "away_team": "Celtics",
            "commence_time": "2026-08-01T20:00:00Z",
            "bookmakers": [{"markets": [{"key": "h2h", "outcomes": [
                {"name": "Lakers", "price": 1.3},
                {"name": "Celtics", "price": 3.5},
            ]}]}],
        }
        with patch("sports_fetcher._apply_elo_blend",
                   return_value={"p_home": 0.35, "p_away": 0.65, "elo_home": None, "elo_away": None}):
            pred = _build_basketball_prediction(event, "NBA", "🏀")
        assert pred["pick_type"] == "upset"
