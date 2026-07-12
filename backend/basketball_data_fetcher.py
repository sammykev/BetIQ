"""
Live basketball results via ESPN's unofficial public API
(site.api.espn.com). No API key required — the endpoints are
public and used by ESPN's own web/mobile clients. Covers NBA,
WNBA, NCAA Men's, and NCAA Women's. Used to keep
basketball_predictor's Elo ratings fresh across every league we
track instead of relying on a manually-uploaded Kaggle CSV.

Response shape is stable and well-documented by the open-source
community (e.g. gist.github.com/nntrn/ee26cb2a0716de0947a0a4e9a157bc1b).

/api/debug/basketball-provider in main.py validates the live
response shape from the deployed environment.
"""

import httpx
from typing import Dict, List, Optional

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball"

LEAGUE_SLUGS: Dict[str, str] = {
    "nba": "NBA",
    "wnba": "WNBA",
    "ncaam": "NCAA Men",
    "ncaaw": "NCAA Women",
}


async def _get(url: str, params: Optional[Dict] = None) -> Optional[Dict]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, params=params or {})
            if r.status_code == 200:
                return r.json()
            print(f"[BasketballData] {url}: HTTP {r.status_code} — {r.text[:200]}")
    except Exception as e:
        print(f"[BasketballData] {url} error: {e}")
    return None


async def fetch_scoreboard(league: str = "nba", dates: str = "") -> List[Dict]:
    """
    Raw ESPN event list for a given league and date or date range.

    league: ESPN slug — "nba", "wnba", "ncaam", "ncaaw"
    dates:  YYYYMMDD for a single day, or YYYYMMDD-YYYYMMDD for a
            range (e.g. "20241001-20250615" for a full season).
            Omit for today's scoreboard.
    """
    params: Dict = {"limit": 200}
    if dates:
        params["dates"] = dates
    data = await _get(f"{ESPN_BASE}/{league}/scoreboard", params)
    if not data:
        return []
    return data.get("events", [])


def parse_completed_games(events: List[Dict]) -> List[Dict]:
    """
    Filter a raw ESPN events list to completed games and normalise
    each to the shape the Elo trainer expects:

        {
            "home_team": str,   # e.g. "Los Angeles Lakers"
            "away_team": str,
            "home_score": int,
            "away_score": int,
            "date": str,        # ISO timestamp from ESPN
        }
    """
    results = []
    for event in events:
        comps = event.get("competitions", [])
        if not comps:
            continue
        comp = comps[0]
        if not comp.get("status", {}).get("type", {}).get("completed", False):
            continue
        competitors = comp.get("competitors", [])
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue
        try:
            results.append({
                "home_team": home["team"]["displayName"],
                "away_team": away["team"]["displayName"],
                "home_score": int(home.get("score", 0)),
                "away_score": int(away.get("score", 0)),
                "date": event.get("date", ""),
            })
        except (KeyError, ValueError):
            continue
    return results
