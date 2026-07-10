"""
Unit tests for basketball_data_fetcher.py — the api-basketball (API-SPORTS)
client used to keep basketball Elo ratings fresh. External HTTP calls are
mocked; this integration hasn't been exercised against a live key from this
sandbox (no outbound internet access here) — see the module docstring and
/api/debug/basketball-provider in main.py for the live-verification path.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

import basketball_data_fetcher as bdf


_FAKE_REQUEST = httpx.Request("GET", "https://v1.basketball.api-sports.io/test")


def _resp(status: int, body: dict = None) -> httpx.Response:
    return httpx.Response(status, json=body or {}, request=_FAKE_REQUEST)


def _mock_async_client(response):
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=response)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx, mock_client


class TestGet:
    async def test_returns_none_without_api_key(self):
        with patch.object(bdf, "API_KEY", ""):
            result = await bdf._get("/leagues")
        assert result is None

    async def test_returns_json_on_200(self):
        ctx, mock_client = _mock_async_client(_resp(200, {"response": [{"id": 1}]}))
        with patch.object(bdf, "API_KEY", "test-key"), \
             patch("basketball_data_fetcher.httpx.AsyncClient", return_value=ctx):
            result = await bdf._get("/leagues")
        assert result == {"response": [{"id": 1}]}

    async def test_sends_api_key_header(self):
        ctx, mock_client = _mock_async_client(_resp(200, {"response": []}))
        with patch.object(bdf, "API_KEY", "secret-123"), \
             patch("basketball_data_fetcher.httpx.AsyncClient", return_value=ctx):
            await bdf._get("/leagues")
        _, kwargs = mock_client.get.call_args
        assert kwargs["headers"]["x-apisports-key"] == "secret-123"

    async def test_returns_none_on_non_200(self):
        ctx, _ = _mock_async_client(_resp(403, {"message": "forbidden"}))
        with patch.object(bdf, "API_KEY", "test-key"), \
             patch("basketball_data_fetcher.httpx.AsyncClient", return_value=ctx):
            result = await bdf._get("/leagues")
        assert result is None

    async def test_returns_none_on_exception(self):
        with patch.object(bdf, "API_KEY", "test-key"), \
             patch("basketball_data_fetcher.httpx.AsyncClient", side_effect=Exception("network down")):
            result = await bdf._get("/leagues")
        assert result is None


class TestFetchLeagues:
    async def test_returns_response_list(self):
        with patch.object(bdf, "_get", new_callable=AsyncMock,
                          return_value={"response": [{"id": 12, "name": "NBA"}]}):
            leagues = await bdf.fetch_leagues(search="NBA")
        assert leagues == [{"id": 12, "name": "NBA"}]

    async def test_returns_empty_list_when_request_fails(self):
        with patch.object(bdf, "_get", new_callable=AsyncMock, return_value=None):
            leagues = await bdf.fetch_leagues()
        assert leagues == []

    async def test_passes_search_param_only_when_given(self):
        with patch.object(bdf, "_get", new_callable=AsyncMock, return_value={"response": []}) as mock_get:
            await bdf.fetch_leagues()
        mock_get.assert_called_once_with("/leagues", {})

        with patch.object(bdf, "_get", new_callable=AsyncMock, return_value={"response": []}) as mock_get:
            await bdf.fetch_leagues(search="EuroLeague")
        mock_get.assert_called_once_with("/leagues", {"search": "EuroLeague"})


class TestFetchGames:
    async def test_returns_response_list(self):
        with patch.object(bdf, "_get", new_callable=AsyncMock,
                          return_value={"response": [{"id": 1}, {"id": 2}]}):
            games = await bdf.fetch_games(date="2026-07-10")
        assert games == [{"id": 1}, {"id": 2}]

    async def test_returns_empty_list_when_request_fails(self):
        with patch.object(bdf, "_get", new_callable=AsyncMock, return_value=None):
            games = await bdf.fetch_games()
        assert games == []

    async def test_builds_params_from_given_filters(self):
        with patch.object(bdf, "_get", new_callable=AsyncMock, return_value={"response": []}) as mock_get:
            await bdf.fetch_games(league_id=12, season="2025-2026", date="2026-07-10")
        mock_get.assert_called_once_with(
            "/games", {"league": 12, "season": "2025-2026", "date": "2026-07-10"}
        )

    async def test_omits_unset_filters(self):
        with patch.object(bdf, "_get", new_callable=AsyncMock, return_value={"response": []}) as mock_get:
            await bdf.fetch_games()
        mock_get.assert_called_once_with("/games", {})
