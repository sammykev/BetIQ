"""
Fetches team xG averages from understat.com for the top 5 European leagues.
Covers: EPL, La Liga, Bundesliga, Serie A, Ligue 1
Data is cached in Redis with a 24h TTL.
"""

import httpx
import asyncio
import json
import re
import os
from typing import Optional, Dict

# Understat league name mapping (football-data.org code → understat URL slug)
UNDERSTAT_LEAGUES = {
    "PL":  "EPL",
    "PD":  "La_liga",
    "BL1": "Bundesliga",
    "SA":  "Serie_A",
    "FL1": "Ligue_1",
}

# Current season year
SEASON = 2025  # 2025-26 season

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://understat.com/",
}

_CACHE_PREFIX = "betiq:understat_xg:"
_CACHE_TTL = 60 * 60 * 24  # 24 hours


def _extract_json_var(html: str, var_name: str):
    """Extract a JSON-encoded var from Understat's JavaScript-embedded data."""
    pattern = rf"var {var_name}\s*=\s*JSON\.parse\('(.+?)'\)"
    m = re.search(pattern, html)
    if not m:
        return None
    raw = m.group(1).encode("utf-8").decode("unicode_escape")
    return json.loads(raw)


async def fetch_league_xg(league_code: str) -> Dict[str, Dict]:
    """
    Fetch season xG stats for all teams in a league from understat.com.
    Returns dict: team_name -> {xg_for: float, xg_against: float, games: int}
    Returns empty dict on failure.
    """
    slug = UNDERSTAT_LEAGUES.get(league_code)
    if not slug:
        return {}

    url = f"https://understat.com/league/{slug}/{SEASON}"
    try:
        async with httpx.AsyncClient(timeout=20, headers=_HEADERS, follow_redirects=True) as client:
            r = await client.get(url)
        if r.status_code != 200:
            return {}

        teams_data = _extract_json_var(r.text, "teamsData")
        if not teams_data:
            return {}

        result = {}
        for team_id, team_info in teams_data.items():
            name = team_info.get("title", "")
            history = team_info.get("history", [])
            if not history:
                continue
            recent = history[-10:]  # last 10 matches
            xg_for     = sum(float(m.get("xG",  0)) for m in recent) / len(recent)
            xg_against = sum(float(m.get("xGA", 0)) for m in recent) / len(recent)
            result[name] = {
                "xg_for":     round(xg_for,     2),
                "xg_against": round(xg_against, 2),
                "games":      len(recent),
                "source":     "understat",
            }
        return result

    except Exception as e:
        print(f"[Understat] Failed to fetch {league_code}: {e}")
        return {}


async def fetch_all_leagues_xg() -> Dict[str, Dict]:
    """Fetch xG for all supported leagues (with delays to be polite)."""
    all_xg = {}
    for code in UNDERSTAT_LEAGUES:
        data = await fetch_league_xg(code)
        all_xg.update(data)
        await asyncio.sleep(3)
    print(f"[Understat] Fetched xG for {len(all_xg)} teams")
    return all_xg


def cache_xg(redis_client, xg_data: Dict[str, Dict]):
    """Store per-team xG in Redis."""
    for team, stats in xg_data.items():
        key = _CACHE_PREFIX + team.lower().replace(" ", "_")
        redis_client.setex(key, _CACHE_TTL, json.dumps(stats))


def get_cached_xg(redis_client, team: str) -> Optional[Dict]:
    """Retrieve cached xG for a team. Returns None if not cached."""
    if not redis_client:
        return None
    key = _CACHE_PREFIX + team.lower().replace(" ", "_")
    try:
        raw = redis_client.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None
