"""
Unit tests for data_fetcher.py — FootballDataClient.
External HTTP calls are mocked via AsyncMock so no network access is needed.

Two implementation notes about the source code under test:
1. httpx >= 0.28 requires a Request on Response for raise_for_status() to work
   even for 2xx status codes.  All mock responses must include a request.
2. _get() holds self._semaphore per request only, so retries work with the
   real Semaphore(1) (TestNotInPlan checks it; it used to deadlock).
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pandas as pd

import data_fetcher
from data_fetcher import FootballDataClient, LEAGUES


# ── Helpers ────────────────────────────────────────────────────────────────

# A fake request object that satisfies httpx's raise_for_status() requirement
_FAKE_REQUEST = httpx.Request("GET", "https://api.football-data.org/v4/test")


def _make_client() -> FootballDataClient:
    return FootballDataClient(api_key="test-api-key")


def _resp(status: int, body: dict = None) -> httpx.Response:
    """Create a mock httpx.Response with the request field required by httpx>=0.28."""
    return httpx.Response(status, json=body or {}, request=_FAKE_REQUEST)


def _match_payload(home="Arsenal", away="Chelsea", date="2025-06-01T15:00:00Z",
                   match_id=1, status="TIMED"):
    # football-data.org always includes a status; TIMED = kickoff time confirmed
    # (the common case for near-term fixtures, incl. matches today).
    return {
        "id": match_id,
        "homeTeam": {"name": home},
        "awayTeam": {"name": away},
        "utcDate": date,
        "status": status,
    }


# ── _get: response handling ────────────────────────────────────────────────


class TestGetMethod:
    async def test_200_returns_parsed_json(self):
        client = _make_client()
        mock_httpx = MagicMock()
        mock_httpx.get = AsyncMock(return_value=_resp(200, {"matches": []}))

        result = await client._get(mock_httpx, "https://example.com")
        assert result == {"matches": []}

    async def test_sends_auth_header(self):
        client = _make_client()
        captured: dict = {}

        async def capture(url, headers=None, **kwargs):
            captured.update(headers or {})
            return _resp(200, {})

        mock_httpx = MagicMock()
        mock_httpx.get = capture
        await client._get(mock_httpx, "https://example.com")
        assert captured.get("X-Auth-Token") == "test-api-key"

    async def test_500_retries_until_success(self):
        # Override semaphore: _get retries recursively while holding the original
        # asyncio.Semaphore(1), which would deadlock — high count prevents that.
        client = _make_client()
        client._semaphore = asyncio.Semaphore(100)
        mock_httpx = MagicMock()
        mock_httpx.get = AsyncMock(side_effect=[
            _resp(500),
            _resp(500),
            _resp(200, {"ok": True}),
        ])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client._get(mock_httpx, "https://example.com")

        assert result == {"ok": True}
        assert mock_httpx.get.call_count == 3

    async def test_429_sleeps_before_retry(self):
        client = _make_client()
        client._semaphore = asyncio.Semaphore(100)
        mock_sleep = AsyncMock()
        mock_httpx = MagicMock()
        mock_httpx.get = AsyncMock(side_effect=[
            _resp(429),
            _resp(200, {"data": "ok"}),
        ])

        with patch("asyncio.sleep", mock_sleep):
            result = await client._get(mock_httpx, "https://example.com")

        assert result == {"data": "ok"}
        mock_sleep.assert_called_once()
        # First 429 attempt sleeps for 65 s
        assert mock_sleep.call_args[0][0] == 65

    async def test_connect_error_retries_then_succeeds(self):
        client = _make_client()
        client._semaphore = asyncio.Semaphore(100)
        mock_httpx = MagicMock()
        mock_httpx.get = AsyncMock(side_effect=[
            httpx.ConnectError("refused"),
            httpx.ConnectError("refused"),
            _resp(200, {"data": "ok"}),
        ])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client._get(mock_httpx, "https://example.com")

        assert result == {"data": "ok"}

    async def test_exhausted_retries_returns_none(self):
        client = _make_client()
        client._semaphore = asyncio.Semaphore(100)
        mock_httpx = MagicMock()
        mock_httpx.get = AsyncMock(side_effect=httpx.ConnectError("always fails"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await client._get(mock_httpx, "https://example.com")

        assert result is None


# ── fetch_upcoming ─────────────────────────────────────────────────────────


class TestFetchUpcoming:
    async def test_returns_parsed_fixtures(self):
        payload = {"matches": [_match_payload("Arsenal", "Chelsea", "2025-06-01T15:00:00Z")]}
        client = _make_client()

        with patch.object(client, "_get", new_callable=AsyncMock, return_value=payload):
            result = await client.fetch_upcoming("PL", 7)

        assert len(result) == 1
        f = result[0]
        assert f["home"] == "Arsenal"
        assert f["away"] == "Chelsea"
        assert f["date"] == "2025-06-01"
        assert f["time"] == "15:00"
        assert f["league"] == "PL"

    async def test_empty_matches_returns_empty_list(self):
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value={"matches": []}):
            result = await client.fetch_upcoming("PL", 7)
        assert result == []

    async def test_none_response_returns_empty_list(self):
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=None):
            result = await client.fetch_upcoming("PL", 7)
        assert result == []

    async def test_skips_tbd_home_team(self):
        payload = {
            "matches": [
                _match_payload("TBD", "Chelsea", match_id=1),
                _match_payload("Arsenal", "Chelsea", match_id=2),
            ]
        }
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=payload):
            result = await client.fetch_upcoming("PL", 7)
        assert len(result) == 1
        assert result[0]["home"] == "Arsenal"

    async def test_skips_tba_away_team(self):
        payload = {
            "matches": [
                _match_payload("Arsenal", "TBA", match_id=1),
                _match_payload("Arsenal", "Chelsea", match_id=2),
            ]
        }
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=payload):
            result = await client.fetch_upcoming("PL", 7)
        assert len(result) == 1

    async def test_skips_empty_team_name(self):
        payload = {
            "matches": [
                {"id": 1, "homeTeam": {"name": ""}, "awayTeam": {"name": "Chelsea"}, "utcDate": "2025-06-01T15:00:00Z"},
                _match_payload("Arsenal", "Chelsea", match_id=2),
            ]
        }
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=payload):
            result = await client.fetch_upcoming("PL", 7)
        assert len(result) == 1

    async def test_attaches_league_metadata(self):
        payload = {"matches": [_match_payload()]}
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=payload):
            result = await client.fetch_upcoming("CL", 7)
        assert result[0]["league"] == "CL"
        assert result[0]["league_name"] == LEAGUES["CL"]["name"]
        assert result[0]["flag"] == LEAGUES["CL"]["flag"]

    async def test_includes_match_id(self):
        payload = {"matches": [_match_payload(match_id=42)]}
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=payload):
            result = await client.fetch_upcoming("PL", 7)
        assert result[0]["match_id"] == 42

    async def test_keeps_timed_fixtures(self):
        # Matches with a confirmed kickoff (today / near-term) are TIMED, not SCHEDULED.
        payload = {"matches": [_match_payload(status="TIMED")]}
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=payload):
            result = await client.fetch_upcoming("WC", 7)
        assert len(result) == 1

    async def test_keeps_scheduled_fixtures(self):
        payload = {"matches": [_match_payload(status="SCHEDULED")]}
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=payload):
            result = await client.fetch_upcoming("WC", 7)
        assert len(result) == 1

    async def test_excludes_finished_and_in_play_fixtures(self):
        payload = {
            "matches": [
                _match_payload("Arsenal", "Chelsea", match_id=1, status="FINISHED"),
                _match_payload("Spain", "Brazil", match_id=2, status="IN_PLAY"),
                _match_payload("France", "Germany", match_id=3, status="TIMED"),
            ]
        }
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=payload):
            result = await client.fetch_upcoming("WC", 7)
        assert len(result) == 1
        assert result[0]["home"] == "France"

    async def test_does_not_filter_url_by_status(self):
        # Regression: filtering the request on status=SCHEDULED dropped TIMED
        # matches (i.e. everything happening today). The URL must not pin a status.
        captured = {}

        async def _fake_get(_client, url, *a, **k):
            captured["url"] = url
            return {"matches": []}

        client = _make_client()
        with patch.object(client, "_get", side_effect=_fake_get):
            await client.fetch_upcoming("WC", 7)
        assert "status=SCHEDULED" not in captured["url"]


# ── fetch_competition_emblem ────────────────────────────────────────────────


class TestFetchCompetitionEmblem:
    async def test_returns_emblem_from_response(self):
        client = _make_client()
        payload = {"id": 2000, "name": "FIFA World Cup", "emblem": "https://crests.football-data.org/wc.svg"}
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=payload):
            result = await client.fetch_competition_emblem("WC")
        assert result == "https://crests.football-data.org/wc.svg"

    async def test_raises_when_response_missing(self):
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=None):
            with pytest.raises(RuntimeError):
                await client.fetch_competition_emblem("WC")

    async def test_returns_none_when_emblem_field_absent(self):
        client = _make_client()
        payload = {"id": 2000, "name": "FIFA World Cup"}
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=payload):
            result = await client.fetch_competition_emblem("WC")
        assert result is None

    async def test_requests_competition_endpoint_for_given_code(self):
        captured = {}

        async def _fake_get(_client, url, *a, **k):
            captured["url"] = url
            return {"emblem": "https://crests.football-data.org/pl.png"}

        client = _make_client()
        with patch.object(client, "_get", side_effect=_fake_get):
            await client.fetch_competition_emblem("PL")
        assert captured["url"].endswith("/competitions/PL")


# ── fetch_recent_results ───────────────────────────────────────────────────


class TestFetchRecentResults:
    def _result_match(self, home_g, away_g, home="Arsenal", away="Chelsea",
                      date="2025-05-20T18:00:00Z"):
        return {
            "utcDate": date,
            "homeTeam": {"name": home},
            "awayTeam": {"name": away},
            "score": {"fullTime": {"home": home_g, "away": away_g}},
        }

    async def test_returns_non_empty_dataframe(self):
        payload = {"matches": [self._result_match(2, 1)]}
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=payload):
            df = await client.fetch_recent_results("PL", 30)
        assert isinstance(df, pd.DataFrame)
        assert not df.empty

    async def test_expected_columns_present(self):
        payload = {"matches": [self._result_match(1, 0)]}
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=payload):
            df = await client.fetch_recent_results("PL", 30)
        for col in ("HomeTeam", "AwayTeam", "Result", "FTHG", "FTAG"):
            assert col in df.columns

    async def test_home_win_maps_to_H(self):
        payload = {"matches": [self._result_match(3, 0)]}
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=payload):
            df = await client.fetch_recent_results("PL", 30)
        assert df.iloc[0]["Result"] == "H"

    async def test_away_win_maps_to_A(self):
        payload = {"matches": [self._result_match(0, 2)]}
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=payload):
            df = await client.fetch_recent_results("PL", 30)
        assert df.iloc[0]["Result"] == "A"

    async def test_draw_maps_to_D(self):
        payload = {"matches": [self._result_match(1, 1)]}
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=payload):
            df = await client.fetch_recent_results("PL", 30)
        assert df.iloc[0]["Result"] == "D"

    async def test_skips_null_score_matches(self):
        payload = {
            "matches": [
                {
                    "utcDate": "2025-05-20T18:00:00Z",
                    "homeTeam": {"name": "Arsenal"},
                    "awayTeam": {"name": "Chelsea"},
                    "score": {"fullTime": {"home": None, "away": None}},
                },
                self._result_match(1, 0),
            ]
        }
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=payload):
            df = await client.fetch_recent_results("PL", 30)
        assert len(df) == 1

    async def test_none_response_returns_empty_dataframe(self):
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=None):
            df = await client.fetch_recent_results("PL", 30)
        assert df.empty

    async def test_results_sorted_chronologically(self):
        payload = {
            "matches": [
                self._result_match(1, 0, date="2025-05-25T18:00:00Z"),
                self._result_match(2, 1, date="2025-05-10T18:00:00Z"),
                self._result_match(0, 0, date="2025-05-18T18:00:00Z"),
            ]
        }
        client = _make_client()
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=payload):
            df = await client.fetch_recent_results("PL", 30)
        dates = df["Date"].tolist()
        assert dates == sorted(dates)


# ── competitions outside the key's plan, retries under the real lock ───────

class TestNotInPlan:
    @pytest.fixture(autouse=True)
    def _clean(self):
        data_fetcher.NOT_IN_PLAN.clear()
        yield
        data_fetcher.NOT_IN_PLAN.clear()

    async def test_403_is_not_retried_and_the_competition_is_skipped_after(self):
        client = _make_client()
        mock_httpx = MagicMock()
        mock_httpx.get = AsyncMock(return_value=_resp(403))
        url = "https://api.football-data.org/v4/competitions/EL/matches?dateFrom=x"
        with patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
            assert await client._get(mock_httpx, url) is None
            assert await client._get(mock_httpx, "https://api.football-data.org/v4/competitions/EL") is None
        assert mock_httpx.get.call_count == 1
        sleep.assert_not_called()
        assert data_fetcher.NOT_IN_PLAN == {"EL"}

    async def test_retry_with_the_real_single_slot_lock_does_not_hang(self):
        client = _make_client()          # asyncio.Semaphore(1), as in production
        mock_httpx = MagicMock()
        mock_httpx.get = AsyncMock(side_effect=[httpx.ConnectError("refused"), _resp(200, {"ok": 1})])
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await asyncio.wait_for(client._get(mock_httpx, "https://example.com"), timeout=2)
        assert result == {"ok": 1}

    def test_competition_of(self):
        assert data_fetcher.competition_of("https://x/v4/competitions/EL/matches?a=1") == "EL"
        assert data_fetcher.competition_of("https://x/v4/competitions/PL") == "PL"
        assert data_fetcher.competition_of("https://x/v4/matches/1/head2head") is None
