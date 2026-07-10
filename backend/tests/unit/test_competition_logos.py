"""
Unit tests for competition_logos.py — generic league/competition badge lookup
with caching and name-alias fallback. Network access is mocked throughout.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

import competition_logos
from competition_logos import lookup_competition_logo, _NAME_ALIASES


def _mock_response(status_code=200, json_body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_body or {})
    resp.text = ""
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
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value


@pytest.fixture(autouse=True)
def _clear_memory_cache():
    competition_logos._memory_cache.clear()
    yield
    competition_logos._memory_cache.clear()


class TestLookupCompetitionLogo:
    async def test_empty_name_returns_none(self):
        assert await lookup_competition_logo("") is None
        assert await lookup_competition_logo("   ") is None

    async def test_successful_lookup_returns_badge(self):
        payload = {"leagues": [{"strBadge": "https://example.com/badge.png"}]}
        with patch("competition_logos.httpx.AsyncClient", return_value=_mock_async_client(_mock_response(200, payload))):
            result = await lookup_competition_logo("Eredivisie")  # not in alias map
        assert result == "https://example.com/badge.png"

    async def test_falls_back_to_logo_field(self):
        payload = {"leagues": [{"strBadge": None, "strLogo": "https://example.com/logo.png"}]}
        with patch("competition_logos.httpx.AsyncClient", return_value=_mock_async_client(_mock_response(200, payload))):
            result = await lookup_competition_logo("Some League")
        assert result == "https://example.com/logo.png"

    async def test_no_leagues_found_returns_none(self):
        payload = {"leagues": None}
        with patch("competition_logos.httpx.AsyncClient", return_value=_mock_async_client(_mock_response(200, payload))):
            result = await lookup_competition_logo("Nonexistent League")
        assert result is None

    async def test_non_200_response_returns_none(self):
        with patch("competition_logos.httpx.AsyncClient", return_value=_mock_async_client(_mock_response(500, {}))):
            result = await lookup_competition_logo("Some League")
        assert result is None

    async def test_network_exception_returns_none_not_raises(self):
        with patch("competition_logos.httpx.AsyncClient", return_value=_mock_async_client(raises=Exception("boom"))):
            result = await lookup_competition_logo("Some League")
        assert result is None

    async def test_uses_in_memory_cache_when_no_redis(self):
        payload = {"leagues": [{"strBadge": "https://example.com/badge.png"}]}
        mock_ctx = _mock_async_client(_mock_response(200, payload))
        with patch("competition_logos.httpx.AsyncClient", return_value=mock_ctx) as MockClient:
            first = await lookup_competition_logo("Eredivisie")
            second = await lookup_competition_logo("Eredivisie")
        assert first == second == "https://example.com/badge.png"
        assert MockClient.call_count == 1

    async def test_redis_cache_hit_skips_network(self):
        redis = FakeRedis()
        redis.store["betiq:comp_logo:eredivisie"] = "https://cached.example.com/badge.png"
        with patch("competition_logos.httpx.AsyncClient") as MockClient:
            result = await lookup_competition_logo("Eredivisie", redis_client=redis)
        assert result == "https://cached.example.com/badge.png"
        MockClient.assert_not_called()

    async def test_redis_cache_written_on_lookup(self):
        payload = {"leagues": [{"strBadge": "https://example.com/badge.png"}]}
        redis = FakeRedis()
        with patch("competition_logos.httpx.AsyncClient", return_value=_mock_async_client(_mock_response(200, payload))):
            await lookup_competition_logo("Eredivisie", redis_client=redis)
        assert redis.store["betiq:comp_logo:eredivisie"] == "https://example.com/badge.png"

    async def test_negative_result_cached_as_empty_string(self):
        redis = FakeRedis()
        with patch("competition_logos.httpx.AsyncClient", return_value=_mock_async_client(_mock_response(200, {"leagues": None}))):
            result = await lookup_competition_logo("Unknown League", redis_client=redis)
        assert result is None
        assert redis.store["betiq:comp_logo:unknown league"] == ""

    async def test_cached_negative_result_returns_none_not_empty_string(self):
        redis = FakeRedis()
        redis.store["betiq:comp_logo:unknown league"] = ""
        with patch("competition_logos.httpx.AsyncClient") as MockClient:
            result = await lookup_competition_logo("Unknown League", redis_client=redis)
        assert result is None
        MockClient.assert_not_called()


class TestNameAliasFallback:
    def test_world_cup_has_an_alias(self):
        assert _NAME_ALIASES.get("world cup") == "FIFA World Cup"

    async def test_alias_is_tried_before_raw_name(self):
        payload = {"leagues": [{"strBadge": "https://example.com/wc-badge.png"}]}
        mock_ctx = _mock_async_client(_mock_response(200, payload))
        with patch("competition_logos.httpx.AsyncClient", return_value=mock_ctx) as MockClient:
            result = await lookup_competition_logo("World Cup")
        assert result == "https://example.com/wc-badge.png"
        called_params = MockClient.return_value.__aenter__.return_value.get.call_args
        # First (only, since it succeeded) call should use the alias, not "World Cup" verbatim
        assert called_params.kwargs["params"]["l"] == "FIFA World Cup"

    async def test_falls_back_to_raw_name_when_alias_finds_nothing(self):
        empty = _mock_response(200, {"leagues": None})
        found = _mock_response(200, {"leagues": [{"strBadge": "https://example.com/raw-badge.png"}]})
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[empty, found])
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("competition_logos.httpx.AsyncClient", return_value=mock_ctx):
            result = await lookup_competition_logo("World Cup")

        assert result == "https://example.com/raw-badge.png"
        assert mock_client.get.call_count == 2
        first_call_params = mock_client.get.call_args_list[0].kwargs["params"]
        second_call_params = mock_client.get.call_args_list[1].kwargs["params"]
        assert first_call_params["l"] == "FIFA World Cup"
        assert second_call_params["l"] == "World Cup"

    async def test_no_alias_for_unknown_competition_searches_raw_name_only(self):
        payload = {"leagues": [{"strBadge": "https://example.com/badge.png"}]}
        mock_ctx = _mock_async_client(_mock_response(200, payload))
        with patch("competition_logos.httpx.AsyncClient", return_value=mock_ctx) as MockClient:
            await lookup_competition_logo("NBA Summer League")
        called_params = MockClient.return_value.__aenter__.return_value.get.call_args
        assert called_params.kwargs["params"]["l"] == "NBA Summer League"
