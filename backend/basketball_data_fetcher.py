"""
Live basketball results via api-basketball (the API-SPORTS family — same
vendor pattern as football-data.org-style APIs already trusted elsewhere in
this app). Used to keep basketball_predictor's Elo ratings fresh across
every league we track (NBA, EuroLeague, NCAA, WNBA, NBL), instead of relying
on a manually-uploaded, NBA-only Kaggle CSV.

Free tier: sign up at https://dashboard.api-football.com (the same account
works across every API-SPORTS sport, including basketball), copy the API
key from the dashboard, and set API_BASKETBALL_KEY.

NOTE ON VERIFICATION: this module is built against API-SPORTS' publicly
documented request/response shape, but has not been exercised against a
live key — the sandbox this was written in has no outbound internet access.
/api/debug/basketball-provider in main.py exists specifically to validate
the real response shape once a key is configured; expect to adjust field
names here based on that live output, the same way the football-data.org
competition-emblem and team-crest integrations were verified earlier this
project.
"""

import os
import httpx
from typing import Dict, List, Optional

API_KEY  = os.getenv("API_BASKETBALL_KEY", "")
API_BASE = "https://v1.basketball.api-sports.io"


async def _get(path: str, params: Optional[Dict] = None) -> Optional[Dict]:
    if not API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{API_BASE}{path}",
                headers={"x-apisports-key": API_KEY},
                params=params or {},
            )
            if r.status_code == 200:
                return r.json()
            print(f"[BasketballData] {path}: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        print(f"[BasketballData] {path} error: {e}")
    return None


async def fetch_leagues(search: str = "") -> List[Dict]:
    """Raw leagues list — used to discover league IDs (e.g. NBA, EuroLeague)."""
    params = {"search": search} if search else {}
    data = await _get("/leagues", params)
    if not data:
        return []
    return data.get("response", [])


async def fetch_games(league_id: Optional[int] = None, season: str = "", date: str = "") -> List[Dict]:
    """Raw games list — finished games are what feed the Elo trainer."""
    params: Dict = {}
    if league_id is not None:
        params["league"] = league_id
    if season:
        params["season"] = season
    if date:
        params["date"] = date
    data = await _get("/games", params)
    if not data:
        return []
    return data.get("response", [])
