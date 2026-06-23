"""
Fetches fixtures and results from football-data.org free API.
Free tier: 10 req/min, covers EPL, La Liga, Bundesliga, Serie A, Ligue 1, UCL, EL.
"""

import asyncio
import httpx
import pandas as pd
from datetime import date, timedelta
from typing import List, Dict, Optional
import os

API_BASE = "https://api.football-data.org/v4"

# Leagues available on football-data.org free tier
LEAGUES: Dict[str, Dict] = {
    "WC":  {"name": "World Cup",        "country": "World",   "flag": "🌍"},
    "EC":  {"name": "Euro Championship","country": "Europe",  "flag": "🇪🇺"},
    "CL":  {"name": "Champions League", "country": "Europe",  "flag": "🏆"},
    "PL":  {"name": "Premier League",   "country": "England", "flag": "🏴󠁧󠁢󠁥󠁮󠁧󠁿"},
    "PD":  {"name": "La Liga",          "country": "Spain",   "flag": "🇪🇸"},
    "BL1": {"name": "Bundesliga",       "country": "Germany", "flag": "🇩🇪"},
    "SA":  {"name": "Serie A",          "country": "Italy",   "flag": "🇮🇹"},
    "FL1": {"name": "Ligue 1",          "country": "France",  "flag": "🇫🇷"},
}


class FootballDataClient:
    def __init__(self, api_key: str):
        self.headers = {"X-Auth-Token": api_key}
        self._semaphore = asyncio.Semaphore(1)  # 1 at a time to respect rate limit

    async def _get(self, client: httpx.AsyncClient, url: str, _attempt: int = 0) -> Optional[Dict]:
        async with self._semaphore:
            try:
                r = await client.get(url, headers=self.headers, timeout=20)
                if r.status_code == 429:
                    wait = 65 if _attempt == 0 else 120
                    print(f"[API] 429 rate-limited — waiting {wait}s (attempt {_attempt+1})")
                    await asyncio.sleep(wait)
                    return await self._get(client, url, _attempt + 1)
                if r.status_code in (500, 502, 503, 504) and _attempt < 2:
                    print(f"[API] {r.status_code} server error — retrying in 10s")
                    await asyncio.sleep(10)
                    return await self._get(client, url, _attempt + 1)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                if _attempt < 2:
                    print(f"[API] Error (attempt {_attempt+1}), retrying: {e}")
                    await asyncio.sleep(10)
                    return await self._get(client, url, _attempt + 1)
                print(f"[API] Failed after 3 attempts: {url} — {e}")
                return None

    async def fetch_upcoming(
        self, league_code: str, days_ahead: int = 7
    ) -> List[Dict]:
        """Return upcoming fixtures for a league within the next N days."""
        today = date.today()
        date_to = today + timedelta(days=days_ahead)
        url = (
            f"{API_BASE}/competitions/{league_code}/matches"
            f"?status=SCHEDULED&dateFrom={today}&dateTo={date_to}"
        )
        async with httpx.AsyncClient() as client:
            data = await self._get(client, url)

        if not data or "matches" not in data:
            return []

        _SKIP = {"tbd", "tba", "to be announced", "", "none"}
        fixtures = []
        for m in data["matches"]:
            home_name = (m["homeTeam"].get("name") or "").strip()
            away_name = (m["awayTeam"].get("name") or "").strip()
            if home_name.lower() in _SKIP or away_name.lower() in _SKIP:
                continue
            fixtures.append({
                "match_id": m["id"],
                "home": home_name,
                "away": away_name,
                "date": m["utcDate"][:10],
                "time": m["utcDate"][11:16],
                "league": league_code,
                "league_name": LEAGUES.get(league_code, {}).get("name", league_code),
                "flag": LEAGUES.get(league_code, {}).get("flag", "⚽"),
            })
        return fixtures

    async def fetch_h2h(self, match_id: int, limit: int = 10) -> Optional[Dict]:
        """Fetch head-to-head record for a specific match ID."""
        url = f"{API_BASE}/matches/{match_id}/head2head?limit={limit}"
        async with httpx.AsyncClient() as client:
            return await self._get(client, url)

    async def fetch_recent_results(
        self, league_code: str, days_back: int = 30
    ) -> pd.DataFrame:
        """Return recent played results (for updating Elo/form on the fly)."""
        today = date.today()
        date_from = today - timedelta(days=days_back)
        url = (
            f"{API_BASE}/competitions/{league_code}/matches"
            f"?status=FINISHED&dateFrom={date_from}&dateTo={today}"
        )
        async with httpx.AsyncClient() as client:
            data = await self._get(client, url)

        if not data or "matches" not in data:
            return pd.DataFrame()

        rows = []
        for m in data["matches"]:
            score = m.get("score", {}).get("fullTime", {})
            home_g = score.get("home")
            away_g = score.get("away")
            if home_g is None or away_g is None:
                continue
            result = "H" if home_g > away_g else ("A" if away_g > home_g else "D")
            rows.append({
                "Date": pd.to_datetime(m["utcDate"][:10]),
                "HomeTeam": m["homeTeam"]["name"],
                "AwayTeam": m["awayTeam"]["name"],
                "Result": result,
                "FTHG": float(home_g),
                "FTAG": float(away_g),
            })

        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)
        return df

    async def fetch_all_upcoming(self, days_ahead: int = 7) -> List[Dict]:
        """Fetch upcoming fixtures for all leagues (with larger delays for rate limit)."""
        all_fixtures = []
        for code in LEAGUES:
            fixtures = await self.fetch_upcoming(code, days_ahead)
            all_fixtures.extend(fixtures)
            await asyncio.sleep(10)  # 10 req/min = 6s, using 10s for safety margin
        return all_fixtures
