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
