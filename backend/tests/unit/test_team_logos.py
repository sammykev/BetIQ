"""
Unit tests for team_logos.py — generic team badge lookup with caching.
Network access is mocked; no real calls to TheSportsDB are made.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

import team_logos
from team_logos import lookup_team_logo


def _mock_response(status_code=200, json_body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_body or {})
    return resp


def _mock_async_client(response=None, raises=None):
    """Build a mock that supports `async with httpx.AsyncClient() as client:`."""
    mock_client = AsyncMock()
    if raises:
        mock_client.get = AsyncMock(side_effect=raises)
    else:
        mock_client.get = AsyncMock(return_value=response)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


class FakeRedis:
    """Minimal in-memory stand-in for the subset of redis-py used here."""
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value


@pytest.fixture(autouse=True)
def _clear_memory_cache():
    team_logos._memory_cache.clear()
    yield
    team_logos._memory_cache.clear()


class TestLookupTeamLogo:
    async def test_empty_name_returns_none(self):
        assert await lookup_team_logo("") is None
        assert await lookup_team_logo("   ") is None

    async def test_successful_lookup_returns_badge(self):
        payload = {"teams": [{"strTeamBadge": "https://example.com/badge.png"}]}
        with patch("team_logos.httpx.AsyncClient", return_value=_mock_async_client(_mock_response(200, payload))):
            result = await lookup_team_logo("Boston Celtics")
        assert result == "https://example.com/badge.png"

    async def test_falls_back_to_team_logo_field(self):
        payload = {"teams": [{"strTeamBadge": None, "strTeamLogo": "https://example.com/logo.png"}]}
        with patch("team_logos.httpx.AsyncClient", return_value=_mock_async_client(_mock_response(200, payload))):
            result = await lookup_team_logo("Some Team")
        assert result == "https://example.com/logo.png"

    async def test_no_teams_found_returns_none(self):
        payload = {"teams": None}
        with patch("team_logos.httpx.AsyncClient", return_value=_mock_async_client(_mock_response(200, payload))):
            result = await lookup_team_logo("Nonexistent Team FC")
        assert result is None

    async def test_non_200_response_returns_none(self):
        with patch("team_logos.httpx.AsyncClient", return_value=_mock_async_client(_mock_response(500, {}))):
            result = await lookup_team_logo("Some Team")
        assert result is None

    async def test_network_exception_returns_none_not_raises(self):
        with patch("team_logos.httpx.AsyncClient", return_value=_mock_async_client(raises=Exception("boom"))):
            result = await lookup_team_logo("Some Team")
        assert result is None

    async def test_uses_in_memory_cache_when_no_redis(self):
        payload = {"teams": [{"strTeamBadge": "https://example.com/badge.png"}]}
        mock_ctx = _mock_async_client(_mock_response(200, payload))
        with patch("team_logos.httpx.AsyncClient", return_value=mock_ctx) as MockClient:
            first = await lookup_team_logo("Boston Celtics")
            second = await lookup_team_logo("Boston Celtics")
        assert first == second == "https://example.com/badge.png"
        # Only one network call — second lookup served from the in-memory cache
        assert MockClient.call_count == 1

    async def test_redis_cache_hit_skips_network(self):
        redis = FakeRedis()
        redis.store["betiq:logo:boston celtics"] = "https://cached.example.com/badge.png"
        with patch("team_logos.httpx.AsyncClient") as MockClient:
            result = await lookup_team_logo("Boston Celtics", redis_client=redis)
        assert result == "https://cached.example.com/badge.png"
        MockClient.assert_not_called()

    async def test_redis_cache_written_on_lookup(self):
        payload = {"teams": [{"strTeamBadge": "https://example.com/badge.png"}]}
        redis = FakeRedis()
        with patch("team_logos.httpx.AsyncClient", return_value=_mock_async_client(_mock_response(200, payload))):
            await lookup_team_logo("Boston Celtics", redis_client=redis)
        assert redis.store["betiq:logo:boston celtics"] == "https://example.com/badge.png"

    async def test_negative_result_cached_as_empty_string(self):
        redis = FakeRedis()
        with patch("team_logos.httpx.AsyncClient", return_value=_mock_async_client(_mock_response(200, {"teams": None}))):
            result = await lookup_team_logo("Unknown Team", redis_client=redis)
        assert result is None
        assert redis.store["betiq:logo:unknown team"] == ""

    async def test_cached_negative_result_returns_none_not_empty_string(self):
        redis = FakeRedis()
        redis.store["betiq:logo:unknown team"] = ""
        with patch("team_logos.httpx.AsyncClient") as MockClient:
            result = await lookup_team_logo("Unknown Team", redis_client=redis)
        assert result is None
        MockClient.assert_not_called()
