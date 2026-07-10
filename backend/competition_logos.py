"""
Generic competition/league badge lookup via TheSportsDB — same free, no-key
service already used for team logos (see team_logos.py).

Why not hardcode a handful of league badges? The app already spans football
(9+ leagues incl. the World Cup), basketball (NBA/EuroLeague/NCAA/seasonal
competitions like NBA Summer League), tennis, and table tennis — a fixed map
would need constant upkeep and still miss anything new. Searching by name
covers any competition without maintaining a list.

Results are cached (Redis if available, else in-process) since TheSportsDB is
a shared free service and league badges essentially never change.
"""

import httpx
from typing import Optional

SPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json/3"

# In-process fallback cache when Redis isn't configured.
_memory_cache: dict = {}

# TheSportsDB indexes most football competitions with a country/confederation
# prefix rather than the short name our app uses internally — nudge the
# handful of leagues we already know about toward names more likely to match,
# while still falling back to a raw search (via lookup_competition_logo) for
# anything not in this map, e.g. newly discovered basketball leagues.
_NAME_ALIASES = {
    "world cup":         "FIFA World Cup",
    "euro championship": "UEFA European Championship",
    "champions league":  "UEFA Champions League",
    "premier league":    "English Premier League",
    "la liga":           "Spanish La Liga",
    "bundesliga":        "German Bundesliga",
    "serie a":           "Italian Serie A",
    "ligue 1":           "French Ligue 1",
    "europa league":     "UEFA Europa League",
    "eredivisie":        "Dutch Eredivisie",
    "primeira liga":     "Portuguese Primeira Liga",
    "brasileirão":       "Brazilian Serie A",
    "brasileirao":       "Brazilian Serie A",
}


async def lookup_competition_logo(competition_name: str, redis_client=None) -> Optional[str]:
    """
    Look up a competition's badge/logo URL by name (e.g. "World Cup",
    "Premier League", "EuroLeague"). Returns None on any failure — callers
    should treat this as "no logo available" and fall back to the emoji flag
    already shown, never as an error condition.
    """
    if not competition_name or not competition_name.strip():
        return None

    cache_key = f"betiq:comp_logo:{competition_name.strip().lower()}"

    if redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached is not None:
                return cached or None  # empty string cached = "known no logo"
        except Exception:
            pass
    elif cache_key in _memory_cache:
        return _memory_cache[cache_key] or None

    alias = _NAME_ALIASES.get(competition_name.strip().lower())
    # Try the alias (more likely to match TheSportsDB's naming) first, then
    # fall back to the raw name we were given — covers both known football
    # leagues and anything (e.g. a newly discovered basketball league) not in
    # the alias map at all.
    candidates = [alias, competition_name] if alias else [competition_name]

    logo_url = None
    for candidate in candidates:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(f"{SPORTSDB_BASE}/searchleagues.php", params={"l": candidate})
                if r.status_code == 200:
                    data = r.json()
                    leagues = data.get("leagues") or []
                    if leagues:
                        entry = leagues[0]
                        logo_url = (
                            entry.get("strBadge") or entry.get("strLogo")
                            or entry.get("strFanart1") or None
                        )
                        if logo_url:
                            break
        except Exception as e:
            print(f"[CompetitionLogo] lookup failed for {candidate!r}: {e}")

    if redis_client:
        try:
            redis_client.setex(cache_key, 60 * 60 * 24 * 30, logo_url or "")  # 30 days
        except Exception:
            pass
    else:
        _memory_cache[cache_key] = logo_url or ""

    return logo_url
