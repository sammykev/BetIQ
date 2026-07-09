"""
Generic team logo lookup via TheSportsDB (free, no key required — uses the
public test key "3", the same one used by countless open-source projects).

Why not a hardcoded map like the football crest lookup? Football only needs
~10 leagues' worth of clubs. Basketball spans NBA, WNBA, EuroLeague, NCAA
(hundreds of colleges), NBL, and seasonal competitions like NBA Summer League
that only exist part of the year — a hardcoded list can never keep up and
always leaves teams with no logo. Searching by name against a broad database
covers any team in any league without us maintaining a list at all.

Results are cached (Redis if available, else in-process) since TheSportsDB is
a shared free service and team badges essentially never change.
"""

import os
import httpx
from typing import Optional

SPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json/3"

# In-process fallback cache when Redis isn't configured — keeps repeated
# lookups within a single server run from hitting TheSportsDB every time.
_memory_cache: dict = {}


async def lookup_team_logo(team_name: str, redis_client=None) -> Optional[str]:
    """
    Look up a team's badge/logo URL by name. Returns None on any failure —
    callers should treat this as "no logo available" and fall back to an
    initials avatar, never as an error condition.
    """
    if not team_name or not team_name.strip():
        return None

    cache_key = f"betiq:logo:{team_name.strip().lower()}"

    if redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached is not None:
                return cached or None  # empty string cached = "known no logo"
        except Exception:
            pass
    elif cache_key in _memory_cache:
        return _memory_cache[cache_key] or None

    logo_url = None
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{SPORTSDB_BASE}/searchteams.php", params={"t": team_name})
            if r.status_code == 200:
                data = r.json()
                teams = data.get("teams") or []
                if teams:
                    logo_url = teams[0].get("strTeamBadge") or teams[0].get("strTeamLogo") or None
    except Exception as e:
        print(f"[TeamLogo] lookup failed for {team_name!r}: {e}")

    # Cache the result — including negative results (empty string) — so a
    # team with genuinely no logo doesn't get re-queried on every request.
    if redis_client:
        try:
            redis_client.setex(cache_key, 60 * 60 * 24 * 30, logo_url or "")  # 30 days
        except Exception:
            pass
    else:
        _memory_cache[cache_key] = logo_url or ""

    return logo_url
