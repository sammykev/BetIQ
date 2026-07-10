"""
Generic competition/league badge lookup via TheSportsDB — same free, no-key
service already used for team logos (see team_logos.py).

Why not hardcode a handful of league badges? The app already spans football
(9+ leagues incl. the World Cup), basketball (NBA/EuroLeague/NCAA/seasonal
competitions like NBA Summer League), tennis, and table tennis — a fixed map
would need constant upkeep and still miss anything new. Searching by name
covers any competition without maintaining a list.

Unlike team search (searchteams.php?t=<name>, verified working), TheSportsDB
has no equivalent single-call "search leagues by name" endpoint — a first
attempt at /searchleagues.php returned a plain 404, confirming it isn't real.
The actual (documented) approach is two calls:
  1. search_all_leagues.php?s=<sport> — list every league for a sport
     (id + name only, no badge)
  2. lookupleague.php?id=<id> — full details for one league, incl. strBadge

So this fetches+caches the per-sport league list once, fuzzy-matches our
competition name against it in-process, then looks up the matched league's
badge. Results are cached (Redis if available, else in-process) since
TheSportsDB is a shared free service and league badges essentially never
change.
"""

import httpx
from difflib import SequenceMatcher
from typing import Dict, List, Optional

SPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json/3"

# In-process fallback caches when Redis isn't configured.
_memory_cache: dict = {}
_leagues_list_cache: Dict[str, List[dict]] = {}  # sport -> [{idLeague, strLeague}, ...]

FUZZY_MATCH_THRESHOLD = 0.55

# Nudges our internal short names toward what's more likely to actually
# appear in TheSportsDB's league list for a fuzzy match, e.g. "World Cup" vs
# their probable "FIFA World Cup". Not required — an unmapped name just gets
# fuzzy-matched against the raw list — but improves match quality for the
# competitions we already know about.
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


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


async def _get_leagues_for_sport(sport: str, client: httpx.AsyncClient) -> List[dict]:
    """Fetch (and cache in-process for this run) the full league list for a sport."""
    if sport in _leagues_list_cache:
        return _leagues_list_cache[sport]
    leagues: List[dict] = []
    try:
        r = await client.get(f"{SPORTSDB_BASE}/search_all_leagues.php", params={"s": sport})
        if r.status_code == 200:
            data = r.json()
            # TheSportsDB's own docs/behavior for this endpoint is inconsistent
            # about the wrapping key across API versions — check both.
            leagues = data.get("countrys") or data.get("leagues") or []
    except Exception as e:
        print(f"[CompetitionLogo] leagues list fetch failed for sport={sport!r}: {e}")
    _leagues_list_cache[sport] = leagues
    return leagues


def find_best_league_match(name: str, leagues: List[dict]) -> Optional[dict]:
    """Fuzzy-match a competition name against a list of {idLeague, strLeague} dicts."""
    best, best_score = None, 0.0
    for lg in leagues:
        candidate_name = lg.get("strLeague") or ""
        if not candidate_name:
            continue
        score = _sim(name, candidate_name)
        if score > best_score:
            best_score, best = score, lg
    return best if best_score >= FUZZY_MATCH_THRESHOLD else None


async def lookup_competition_logo(
    competition_name: str, redis_client=None, sport: str = "Soccer"
) -> Optional[str]:
    """
    Look up a competition's badge/logo URL by name (e.g. "World Cup",
    "Premier League", "EuroLeague") and sport (TheSportsDB's taxonomy, e.g.
    "Soccer", "Basketball" — defaults to "Soccer" since that covers the
    majority of the app's competitions). Returns None on any failure —
    callers should treat this as "no logo available" and fall back to the
    emoji flag already shown, never as an error condition.
    """
    if not competition_name or not competition_name.strip():
        return None

    cache_key = f"betiq:comp_logo:{sport.lower()}:{competition_name.strip().lower()}"

    if redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached is not None:
                return cached or None  # empty string cached = "known no logo"
        except Exception:
            pass
    elif cache_key in _memory_cache:
        return _memory_cache[cache_key] or None

    search_name = _NAME_ALIASES.get(competition_name.strip().lower(), competition_name)

    logo_url = None
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            leagues = await _get_leagues_for_sport(sport, client)
            match = find_best_league_match(search_name, leagues)
            if match and match.get("idLeague"):
                r = await client.get(f"{SPORTSDB_BASE}/lookupleague.php", params={"id": match["idLeague"]})
                if r.status_code == 200:
                    data = r.json()
                    details_list = data.get("leagues") or []
                    if details_list and details_list[0]:
                        details = details_list[0]
                        logo_url = details.get("strBadge") or details.get("strLogo") or None
    except Exception as e:
        print(f"[CompetitionLogo] lookup failed for {competition_name!r} (sport={sport}): {e}")

    if redis_client:
        try:
            redis_client.setex(cache_key, 60 * 60 * 24 * 30, logo_url or "")  # 30 days
        except Exception:
            pass
    else:
        _memory_cache[cache_key] = logo_url or ""

    return logo_url
