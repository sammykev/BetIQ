"""
Unit tests for competition_logos.py — two-step league badge lookup
(search_all_leagues.php for id+name, lookupleague.php for the badge) with
caching and fuzzy name matching. Network access is mocked throughout.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

import competition_logos
from competition_logos import (
    lookup_competition_logo, find_best_league_match, _sim, _NAME_ALIASES,
)


def _mock_response(status_code=200, json_body=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_body or {})
    resp.text = text
    return resp


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value


@pytest.fixture(autouse=True)
def _clear_caches():
    competition_logos._memory_cache.clear()
    competition_logos._leagues_list_cache.clear()
    yield
    competition_logos._memory_cache.clear()
    competition_logos._leagues_list_cache.clear()


def _mock_client_with_responses(*responses):
    """AsyncClient.get() returns responses in sequence across successive calls."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=list(responses))
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx, mock_client


class TestFindBestLeagueMatch:
    def test_exact_name_match(self):
        leagues = [{"idLeague": "1", "strLeague": "FIFA World Cup"},
                   {"idLeague": "2", "strLeague": "English Premier League"}]
        match = find_best_league_match("FIFA World Cup", leagues)
        assert match["idLeague"] == "1"

    def test_no_good_match_returns_none(self):
        leagues = [{"idLeague": "1", "strLeague": "Completely Unrelated League Name"}]
        assert find_best_league_match("FIFA World Cup", leagues) is None

    def test_empty_list_returns_none(self):
        assert find_best_league_match("FIFA World Cup", []) is None

    def test_skips_entries_without_a_name(self):
        leagues = [{"idLeague": "1", "strLeague": None}, {"idLeague": "2", "strLeague": "FIFA World Cup"}]
        match = find_best_league_match("FIFA World Cup", leagues)
        assert match["idLeague"] == "2"


class TestLookupCompetitionLogo:
    async def test_empty_name_returns_none(self):
        assert await lookup_competition_logo("") is None
        assert await lookup_competition_logo("   ") is None

    async def test_successful_two_step_lookup(self):
        list_resp = _mock_response(200, {"countrys": [
            {"idLeague": "4429", "strLeague": "FIFA World Cup"},
        ]})
        detail_resp = _mock_response(200, {"leagues": [
            {"strBadge": "https://example.com/wc-badge.png"},
        ]})
        mock_ctx, mock_client = _mock_client_with_responses(list_resp, detail_resp)
        with patch("competition_logos.httpx.AsyncClient", return_value=mock_ctx):
            result = await lookup_competition_logo("World Cup")
        assert result == "https://example.com/wc-badge.png"
        assert mock_client.get.call_count == 2

    async def test_uses_leagues_key_if_countrys_key_absent(self):
        list_resp = _mock_response(200, {"leagues": [
            {"idLeague": "1", "strLeague": "English Premier League"},
        ]})
        detail_resp = _mock_response(200, {"leagues": [{"strBadge": "https://example.com/badge.png"}]})
        mock_ctx, _ = _mock_client_with_responses(list_resp, detail_resp)
        with patch("competition_logos.httpx.AsyncClient", return_value=mock_ctx):
            result = await lookup_competition_logo("Premier League")
        assert result == "https://example.com/badge.png"

    async def test_falls_back_to_logo_field(self):
        list_resp = _mock_response(200, {"countrys": [{"idLeague": "1", "strLeague": "Eredivisie"}]})
        detail_resp = _mock_response(200, {"leagues": [{"strBadge": None, "strLogo": "https://example.com/logo.png"}]})
        mock_ctx, _ = _mock_client_with_responses(list_resp, detail_resp)
        with patch("competition_logos.httpx.AsyncClient", return_value=mock_ctx):
            result = await lookup_competition_logo("Eredivisie")
        assert result == "https://example.com/logo.png"

    async def test_no_match_in_league_list_returns_none(self):
        list_resp = _mock_response(200, {"countrys": [{"idLeague": "1", "strLeague": "Totally Unrelated"}]})
        mock_ctx, mock_client = _mock_client_with_responses(list_resp)
        with patch("competition_logos.httpx.AsyncClient", return_value=mock_ctx):
            result = await lookup_competition_logo("World Cup")
        assert result is None
        # Only the list call should have happened — no point looking up a badge for no match
        assert mock_client.get.call_count == 1

    async def test_empty_league_list_returns_none(self):
        list_resp = _mock_response(200, {"countrys": []})
        mock_ctx, _ = _mock_client_with_responses(list_resp)
        with patch("competition_logos.httpx.AsyncClient", return_value=mock_ctx):
            result = await lookup_competition_logo("World Cup")
        assert result is None

    async def test_list_endpoint_non_200_returns_none(self):
        list_resp = _mock_response(404, {}, text="Not Found")
        mock_ctx, _ = _mock_client_with_responses(list_resp)
        with patch("competition_logos.httpx.AsyncClient", return_value=mock_ctx):
            result = await lookup_competition_logo("World Cup")
        assert result is None

    async def test_lookup_endpoint_non_200_returns_none(self):
        list_resp = _mock_response(200, {"countrys": [{"idLeague": "1", "strLeague": "FIFA World Cup"}]})
        detail_resp = _mock_response(500, {})
        mock_ctx, _ = _mock_client_with_responses(list_resp, detail_resp)
        with patch("competition_logos.httpx.AsyncClient", return_value=mock_ctx):
            result = await lookup_competition_logo("World Cup")
        assert result is None

    async def test_network_exception_returns_none_not_raises(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("boom"))
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("competition_logos.httpx.AsyncClient", return_value=mock_ctx):
            result = await lookup_competition_logo("World Cup")
        assert result is None

    async def test_leagues_list_cached_across_lookups_same_sport(self):
        list_resp = _mock_response(200, {"countrys": [
            {"idLeague": "1", "strLeague": "FIFA World Cup"},
            {"idLeague": "2", "strLeague": "English Premier League"},
        ]})
        detail_resp_1 = _mock_response(200, {"leagues": [{"strBadge": "https://example.com/wc.png"}]})
        detail_resp_2 = _mock_response(200, {"leagues": [{"strBadge": "https://example.com/pl.png"}]})
        mock_ctx, mock_client = _mock_client_with_responses(list_resp, detail_resp_1, detail_resp_2)
        with patch("competition_logos.httpx.AsyncClient", return_value=mock_ctx):
            r1 = await lookup_competition_logo("World Cup")
            r2 = await lookup_competition_logo("Premier League")
        assert r1 == "https://example.com/wc.png"
        assert r2 == "https://example.com/pl.png"
        # 1 list call (shared/cached) + 2 lookup calls = 3, not 4
        assert mock_client.get.call_count == 3

    async def test_uses_in_memory_cache_when_no_redis(self):
        list_resp = _mock_response(200, {"countrys": [{"idLeague": "1", "strLeague": "Eredivisie"}]})
        detail_resp = _mock_response(200, {"leagues": [{"strBadge": "https://example.com/badge.png"}]})
        mock_ctx, mock_client = _mock_client_with_responses(list_resp, detail_resp)
        with patch("competition_logos.httpx.AsyncClient", return_value=mock_ctx):
            first = await lookup_competition_logo("Eredivisie")
            second = await lookup_competition_logo("Eredivisie")
        assert first == second == "https://example.com/badge.png"
        assert mock_client.get.call_count == 2  # not repeated for the second call

    async def test_redis_cache_hit_skips_network(self):
        redis = FakeRedis()
        redis.store["betiq:comp_logo:soccer:eredivisie"] = "https://cached.example.com/badge.png"
        with patch("competition_logos.httpx.AsyncClient") as MockClient:
            result = await lookup_competition_logo("Eredivisie", redis_client=redis)
        assert result == "https://cached.example.com/badge.png"
        MockClient.assert_not_called()

    async def test_redis_cache_written_on_lookup(self):
        list_resp = _mock_response(200, {"countrys": [{"idLeague": "1", "strLeague": "Eredivisie"}]})
        detail_resp = _mock_response(200, {"leagues": [{"strBadge": "https://example.com/badge.png"}]})
        mock_ctx, _ = _mock_client_with_responses(list_resp, detail_resp)
        redis = FakeRedis()
        with patch("competition_logos.httpx.AsyncClient", return_value=mock_ctx):
            await lookup_competition_logo("Eredivisie", redis_client=redis)
        assert redis.store["betiq:comp_logo:soccer:eredivisie"] == "https://example.com/badge.png"

    async def test_negative_result_cached_as_empty_string(self):
        list_resp = _mock_response(200, {"countrys": []})
        mock_ctx, _ = _mock_client_with_responses(list_resp)
        redis = FakeRedis()
        with patch("competition_logos.httpx.AsyncClient", return_value=mock_ctx):
            result = await lookup_competition_logo("Unknown League", redis_client=redis)
        assert result is None
        assert redis.store["betiq:comp_logo:soccer:unknown league"] == ""

    async def test_cached_negative_result_returns_none_not_empty_string(self):
        redis = FakeRedis()
        redis.store["betiq:comp_logo:soccer:unknown league"] = ""
        with patch("competition_logos.httpx.AsyncClient") as MockClient:
            result = await lookup_competition_logo("Unknown League", redis_client=redis)
        assert result is None
        MockClient.assert_not_called()

    async def test_different_sports_cached_separately(self):
        redis = FakeRedis()
        redis.store["betiq:comp_logo:soccer:summer league"] = "https://example.com/soccer.png"
        list_resp = _mock_response(200, {"countrys": [{"idLeague": "1", "strLeague": "NBA Summer League"}]})
        detail_resp = _mock_response(200, {"leagues": [{"strBadge": "https://example.com/basketball.png"}]})
        mock_ctx, _ = _mock_client_with_responses(list_resp, detail_resp)
        with patch("competition_logos.httpx.AsyncClient", return_value=mock_ctx):
            result = await lookup_competition_logo("Summer League", redis_client=redis, sport="Basketball")
        assert result == "https://example.com/basketball.png"
        assert redis.store["betiq:comp_logo:basketball:summer league"] == "https://example.com/basketball.png"


class TestNameAliasFallback:
    def test_world_cup_has_an_alias(self):
        assert _NAME_ALIASES.get("world cup") == "FIFA World Cup"

    async def test_alias_used_as_fuzzy_match_seed(self):
        list_resp = _mock_response(200, {"countrys": [
            {"idLeague": "1", "strLeague": "FIFA World Cup"},
            {"idLeague": "2", "strLeague": "World Cup Qualification"},
        ]})
        detail_resp = _mock_response(200, {"leagues": [{"strBadge": "https://example.com/wc.png"}]})
        mock_ctx, _ = _mock_client_with_responses(list_resp, detail_resp)
        with patch("competition_logos.httpx.AsyncClient", return_value=mock_ctx):
            result = await lookup_competition_logo("World Cup")
        # Alias "FIFA World Cup" should match the exact entry, not the qualifiers one
        assert result == "https://example.com/wc.png"


class TestSim:
    def test_identical_strings(self):
        assert _sim("Premier League", "Premier League") == pytest.approx(1.0)

    def test_case_insensitive(self):
        assert _sim("premier league", "PREMIER LEAGUE") == pytest.approx(1.0)
