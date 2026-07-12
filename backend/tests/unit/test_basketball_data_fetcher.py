"""
Unit tests for basketball_data_fetcher.py — the ESPN unofficial API
client used to keep basketball Elo ratings fresh. No API key is
needed; external HTTP calls are mocked throughout. See the module
docstring and /api/debug/basketball-provider in main.py for the
live-verification path.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

import basketball_data_fetcher as bdf


_FAKE_REQUEST = httpx.Request("GET", "https://site.api.espn.com/test")


def _resp(status: int, body: dict = None) -> httpx.Response:
    return httpx.Response(status, json=body or {}, request=_FAKE_REQUEST)


def _mock_async_client(response):
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=response)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx, mock_client


def _completed_event(home="Los Angeles Lakers", away="Boston Celtics",
                     home_score=112, away_score=98, date="2026-01-15T03:00Z"):
    return {
        "date": date,
        "competitions": [{
            "status": {"type": {"completed": True, "name": "STATUS_FINAL"}},
            "competitors": [
                {
                    "homeAway": "home",
                    "team": {"displayName": home},
                    "score": str(home_score),
                },
                {
                    "homeAway": "away",
                    "team": {"displayName": away},
                    "score": str(away_score),
                },
            ],
        }],
    }


def _live_event(home="Heat", away="Bucks"):
    return {
        "date": "2026-01-15T03:00Z",
        "competitions": [{
            "status": {"type": {"completed": False, "name": "STATUS_IN_PROGRESS"}},
            "competitors": [
                {"homeAway": "home", "team": {"displayName": home}, "score": "60"},
                {"homeAway": "away", "team": {"displayName": away}, "score": "58"},
            ],
        }],
    }


# ── _get ─────────────────────────────────────────────────────────────────────

class TestGet:
    async def test_returns_json_on_200(self):
        body = {"events": [{"id": "1"}]}
        ctx, _ = _mock_async_client(_resp(200, body))
        with patch("basketball_data_fetcher.httpx.AsyncClient", return_value=ctx):
            result = await bdf._get("https://site.api.espn.com/nba/scoreboard")
        assert result == body

    async def test_returns_none_on_non_200(self):
        ctx, _ = _mock_async_client(_resp(404, {}))
        with patch("basketball_data_fetcher.httpx.AsyncClient", return_value=ctx):
            result = await bdf._get("https://site.api.espn.com/nba/scoreboard")
        assert result is None

    async def test_returns_none_on_exception(self):
        with patch("basketball_data_fetcher.httpx.AsyncClient",
                   side_effect=Exception("network down")):
            result = await bdf._get("https://site.api.espn.com/nba/scoreboard")
        assert result is None

    async def test_passes_params_to_get(self):
        ctx, mock_client = _mock_async_client(_resp(200, {}))
        with patch("basketball_data_fetcher.httpx.AsyncClient", return_value=ctx):
            await bdf._get("https://example.com/ep", {"limit": 50})
        _, kwargs = mock_client.get.call_args
        assert kwargs["params"] == {"limit": 50}


# ── fetch_scoreboard ──────────────────────────────────────────────────────────

class TestFetchScoreboard:
    async def test_returns_events_list(self):
        events = [{"id": "1"}, {"id": "2"}]
        with patch.object(bdf, "_get", new_callable=AsyncMock,
                          return_value={"events": events}):
            result = await bdf.fetch_scoreboard("nba")
        assert result == events

    async def test_returns_empty_list_when_request_fails(self):
        with patch.object(bdf, "_get", new_callable=AsyncMock, return_value=None):
            result = await bdf.fetch_scoreboard()
        assert result == []

    async def test_always_passes_limit_200(self):
        with patch.object(bdf, "_get", new_callable=AsyncMock,
                          return_value={"events": []}) as mock_get:
            await bdf.fetch_scoreboard("nba")
        args, _ = mock_get.call_args
        assert args[1]["limit"] == 200

    async def test_passes_dates_param_when_given(self):
        with patch.object(bdf, "_get", new_callable=AsyncMock,
                          return_value={"events": []}) as mock_get:
            await bdf.fetch_scoreboard("nba", dates="20260710")
        args, _ = mock_get.call_args
        assert args[1]["dates"] == "20260710"

    async def test_omits_dates_param_when_empty(self):
        with patch.object(bdf, "_get", new_callable=AsyncMock,
                          return_value={"events": []}) as mock_get:
            await bdf.fetch_scoreboard("wnba")
        args, _ = mock_get.call_args
        assert "dates" not in args[1]

    async def test_builds_correct_url_for_league(self):
        with patch.object(bdf, "_get", new_callable=AsyncMock,
                          return_value={"events": []}) as mock_get:
            await bdf.fetch_scoreboard("ncaam")
        url, _ = mock_get.call_args
        assert "ncaam/scoreboard" in url[0]


# ── parse_completed_games ─────────────────────────────────────────────────────

class TestParseCompletedGames:
    def test_returns_completed_games_only(self):
        events = [_completed_event(), _live_event()]
        result = bdf.parse_completed_games(events)
        assert len(result) == 1

    def test_normalizes_home_and_away(self):
        event = _completed_event("Los Angeles Lakers", "Boston Celtics", 112, 98,
                                 "2026-01-15T03:00Z")
        result = bdf.parse_completed_games([event])
        assert len(result) == 1
        g = result[0]
        assert g["home_team"] == "Los Angeles Lakers"
        assert g["away_team"] == "Boston Celtics"
        assert g["home_score"] == 112
        assert g["away_score"] == 98
        assert g["date"] == "2026-01-15T03:00Z"

    def test_skips_events_with_no_competitions(self):
        event = {"date": "2026-01-15T00:00Z", "competitions": []}
        assert bdf.parse_completed_games([event]) == []

    def test_skips_in_progress_games(self):
        assert bdf.parse_completed_games([_live_event()]) == []

    def test_skips_event_missing_home_competitor(self):
        event = {
            "date": "2026-01-15T00:00Z",
            "competitions": [{
                "status": {"type": {"completed": True}},
                "competitors": [
                    {"homeAway": "away", "team": {"displayName": "Celtics"}, "score": "98"},
                ],
            }],
        }
        assert bdf.parse_completed_games([event]) == []

    def test_skips_event_with_malformed_score(self):
        event = _completed_event()
        event["competitions"][0]["competitors"][0]["score"] = "N/A"
        assert bdf.parse_completed_games([event]) == []

    def test_handles_multiple_completed_games(self):
        events = [
            _completed_event("Lakers", "Celtics", 112, 98),
            _completed_event("Heat", "Bucks", 105, 103),
        ]
        result = bdf.parse_completed_games(events)
        assert len(result) == 2
        teams = {r["home_team"] for r in result}
        assert teams == {"Lakers", "Heat"}
