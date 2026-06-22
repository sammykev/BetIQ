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

    async def _get(self, client: httpx.AsyncClient, url: str) -> Optional[Dict]:
        async with self._semaphore:
            try:
                r = await client.get(url, headers=self.headers, timeout=15)
                if r.status_code == 429:
                    await asyncio.sleep(61)
                    r = await client.get(url, headers=self.headers, timeout=15)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                print(f"[API] Error fetching {url}: {e}")
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

        fixtures = []
        for m in data["matches"]:
            fixtures.append({
                "match_id": m["id"],          # needed for H2H endpoint
                "home": m["homeTeam"]["name"],
                "away": m["awayTeam"]["name"],
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

        df = pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)
        return df

    async def fetch_all_upcoming(self, days_ahead: int = 7) -> List[Dict]:
        """Fetch upcoming fixtures for all leagues (with small delays for rate limit)."""
        all_fixtures = []
        for code in LEAGUES:
            fixtures = await self.fetch_upcoming(code, days_ahead)
            all_fixtures.extend(fixtures)
            await asyncio.sleep(6)  # 10 req/min = 6s between calls
        return all_fixtures
