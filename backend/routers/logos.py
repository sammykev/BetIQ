"""
Team crests and competition logos.

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import time
from data_fetcher import API_BASE, LEAGUES, FootballDataClient, not_in_plan
from fastapi import APIRouter, Depends, Response
from typing import Any, Dict, Iterable, Optional, Tuple

import main

router = APIRouter()


@router.get("/api/team-logo")
async def get_team_logo(name: str, response: Response):
    """
    Generic team badge/logo lookup. Tries the football-data.org crest cache
    first — crests for every team in our tracked football competitions
    (World Cup, Premier League, ...) are captured for free from the fixtures
    the pipeline already fetches, no extra API call needed and no fuzzy name
    matching required. Falls back to the TheSportsDB-backed generic lookup
    for anything else (basketball, tennis, a team not yet seen in a fixture
    window). Cached server-side (Redis, 30 days) since badges don't change.
    """
    key = ("team", name)
    hit = main._logo_answer(response, key)
    if hit is not None:
        return hit
    return main._logo_answer(response, key, await main._team_logo(name))


async def _team_logo(name: str) -> Dict[str, Any]:
    from team_logos import lookup_team_logo
    logo = await asyncio.to_thread(main._get_cached_team_crest, name)
    source = "football-data.org" if logo else None
    if not logo:
        logo = await lookup_team_logo(name, redis_client=main._get_redis())
        source = "thesportsdb" if logo else None
    return {"name": name, "logo": logo, "source": source}


@router.get("/api/team-logos")
async def get_team_logos(names: str, response: Response):
    """Several clubs' badges at once (names joined by "|", at most 120):
    {"logos": {name: url or null}} — a page's badges in one request."""
    wanted = list(dict.fromkeys(n.strip() for n in names.split("|") if n.strip()))[:120]
    from team_logos import lookup_team_logo
    out: Dict[str, Optional[str]] = {}
    missing = []
    for n in wanted:
        hit = main._logo_answer(response, ("team", n))
        if hit is None:
            missing.append(n)
        else:
            out[n] = hit.get("logo")
    # Crests in one worker thread for the lot (not a thread each: the pool is
    # shared with every other request), then TheSportsDB for the rest, a few at a time
    found = await asyncio.to_thread(lambda: {n: main._get_cached_team_crest(n) for n in missing}) if missing else {}
    gate = asyncio.Semaphore(4)

    async def outside(n: str) -> Dict[str, Any]:
        async with gate:
            logo = await lookup_team_logo(n, redis_client=main._get_redis())
        return {"name": n, "logo": logo, "source": "thesportsdb" if logo else None}
    rest = [n for n in missing if not found.get(n)]
    more = dict(zip(rest, await asyncio.gather(*(outside(n) for n in rest)))) if rest else {}
    for n in missing:
        got = {"name": n, "logo": found[n], "source": "football-data.org"} if found.get(n) else more[n]
        out[n] = main._logo_answer(response, ("team", n), got).get("logo")
    response.headers["Cache-Control"] = f"public, max-age={main.LOGO_MISS_SECONDS}"
    return {"logos": out}


@router.get("/api/debug/team-logo")
async def debug_team_logo(name: str, _admin: str = Depends(main.require_admin)):
    """
    Diagnose the team badge lookup: whether football-data.org's crest cache
    (populated for free from the fixtures the pipeline fetches — see
    _cache_team_crest in _run_pipeline) has this team, and if not, the raw
    TheSportsDB fallback lookup. A team only appears in the crest cache once
    the pipeline has fetched at least one fixture involving them within the
    90-day fixture window, so a team with no upcoming match (e.g. a
    tournament they didn't qualify for) will always fall through to
    TheSportsDB.
    """
    from team_logos import lookup_team_logo

    out: Dict = {"name": name}
    out["football_data_crest_cached"] = main._get_cached_team_crest(name)

    r = main._get_redis()
    out["thesportsdb_logo"] = await lookup_team_logo(name, redis_client=r)

    out["logo"] = out["football_data_crest_cached"] or out["thesportsdb_logo"]
    out["source"] = (
        "football-data.org" if out["football_data_crest_cached"]
        else ("thesportsdb" if out["thesportsdb_logo"] else None)
    )
    return out


def _get_fd_client() -> Optional[FootballDataClient]:
    if not main.API_KEY:
        return None
    if main._fd_ondemand_client is None:
        main._fd_ondemand_client = FootballDataClient(main.API_KEY)
    return main._fd_ondemand_client


def _competition_emblem_cache_key(code: str) -> str:
    return f"betiq:fd_emblem:{code}"


def _cache_competition_emblem(code: str, emblem: Optional[str]) -> None:
    r = main._get_redis()
    if not r:
        return
    try:
        r.setex(main._competition_emblem_cache_key(code), 60 * 60 * 24 * 30, emblem or "")  # 30 days
    except Exception:
        pass


def _has_cached_competition_emblem(code: str) -> bool:
    """
    True only for a confirmed, truthy cached emblem. A missing key or a
    cached empty string are both treated as "not yet known" so a stale
    negative — e.g. from a request that failed under 429 rate-limiting
    during a burst of concurrent lookups — gets retried rather than trusted
    for the full 30-day TTL.
    """
    r = main._get_redis()
    if not r:
        return False
    try:
        return bool(r.get(main._competition_emblem_cache_key(code)))
    except Exception:
        return False


async def _get_football_competition_emblem(name: str) -> Optional[str]:
    """
    football-data.org emblem for one of our own football leagues — a real,
    already-paid-for API key rather than TheSportsDB's rate/data-limited free
    "3" test key, and our league codes ("WC", "PL", ...) map exactly to
    football-data.org's own competition codes, so no fuzzy name matching is
    needed at all. Returns None for anything not in our LEAGUES dict (e.g. a
    basketball competition) or if the API key isn't configured.

    Normally this is pre-populated by the pipeline (see _run_pipeline), which
    fetches each league's emblem once (paced alongside its fixture fetches)
    and caches it for 30 days — this on-demand path only fires as a fallback
    for a request that lands before the first pipeline run, or for a league
    whose cache is still empty/stale.
    """
    code = main._LEAGUE_NAME_TO_CODE.get(name.strip().lower())
    if not code or not main.API_KEY:
        return None

    def cached() -> Optional[str]:
        r = main._get_redis()
        try:
            return (r.get(main._competition_emblem_cache_key(code)) or None) if r else None
        except Exception:
            return None
    hit = await asyncio.to_thread(cached)
    if hit:
        return hit

    try:
        emblem = await main._get_fd_client().fetch_competition_emblem(code)
    except Exception as e:
        # Do NOT cache failures — an outage or a transient 429 shouldn't be
        # remembered as "this competition has no emblem" for a month.
        print(f"[CompetitionLogo] football-data.org emblem fetch failed for {code}: {e}")
        return None

    await asyncio.to_thread(main._cache_competition_emblem, code, emblem)
    return emblem


def _team_crest_cache_key(team_name: str) -> str:
    return f"betiq:team_crest:{team_name.strip().lower()}"


def _cache_team_crest(team_name: str, crest_url: Optional[str]) -> None:
    """
    Persist a team's football-data.org crest, captured for free from a
    fixtures response (no extra API call). Only writes truthy values — a
    fixture missing a crest for one team shouldn't overwrite/block a crest
    learned from a different fixture, or block the TheSportsDB fallback.
    """
    if not crest_url or not team_name or not team_name.strip():
        return
    r = main._get_redis()
    if not r:
        return
    try:
        r.setex(main._team_crest_cache_key(team_name), 60 * 60 * 24 * 30, crest_url)  # 30 days
    except Exception:
        pass


def _cache_team_crests(fixtures: Iterable[Dict]) -> None:
    """Every fixture's crests in one Redis round trip (one write per team took
    a network round trip each, hundreds a pipeline run)."""
    r = main._get_redis()
    if not r:
        return
    pairs = {(fx.get(side) or "").strip(): fx.get(f"{side}_crest")
             for fx in fixtures for side in ("home", "away")}
    pairs = {team: url for team, url in pairs.items() if team and url}
    if not pairs:
        return
    try:
        import crests
        crests.save(r, {t: u for t, u in pairs.items() if "football-data.org" in u})
    except Exception:
        pass
    try:
        pipe = r.pipeline(transaction=False)
        for team, url in pairs.items():
            pipe.setex(main._team_crest_cache_key(team), 60 * 60 * 24 * 30, url)
        pipe.execute()
    except Exception:
        for team, url in pairs.items():
            main._cache_team_crest(team, url)


def _get_cached_team_crest(team_name: str) -> Optional[str]:
    """The club's crest: its own fixture's, else football-data.org's team
    lists by name (crests.py: whole words, never a guess from a city)."""
    import crests
    if not team_name or not team_name.strip():
        return None
    r = main._get_redis()
    if not r:
        return None
    try:
        return r.get(main._team_crest_cache_key(team_name)) or crests.lookup(r, team_name)
    except Exception:
        return None


async def _crest_index_refresh() -> int:
    """Every tracked competition's clubs and crests from football-data.org
    (one request a competition, spaced for the rate limit) into crests.KEY."""
    import crests
    if not main.API_KEY:
        return 0
    if main._is_training:
        # The prediction pipeline is fetching fixtures: football-data.org allows
        # 10 requests a minute between us, and its fixtures come first
        print("[Crests] pipeline running — trying again later")
        return 0
    client = FootballDataClient(main.API_KEY)
    found: Dict[str, str] = {}
    for code in LEAGUES:
        if not_in_plan(code):
            continue
        if main._is_training:
            break
        try:
            found.update(crests.entries(await client.fetch_competition_teams(code)))
        except Exception as e:
            print(f"[Crests] {code}: {e}")
        await asyncio.sleep(main.CREST_GAP_S)
    n = await asyncio.to_thread(crests.save, main._get_redis(), found)
    main._logo_memo.clear()
    print(f"[Crests] {n} club names with crests from football-data.org")
    return n


def _logo_answer(response: Response, key: Tuple, found: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The remembered answer for `key` (None when there isn't a fresh one);
    with `found`, remember it. Badges don't change: browsers keep them a day."""
    now = time.time()
    if found is not None:
        main._logo_memo[key] = (now, found)
        if len(main._logo_memo) > 5000:
            main._logo_memo.clear()
        hit = found
    else:
        memo = main._logo_memo.get(key)
        if not memo or now - memo[0] > (main.LOGO_SECONDS if memo[1].get("logo") else main.LOGO_MISS_SECONDS):
            return None
        hit = memo[1]
    ttl = main.LOGO_SECONDS if hit.get("logo") else main.LOGO_MISS_SECONDS
    response.headers["Cache-Control"] = f"public, max-age={ttl}, stale-while-revalidate={main.LOGO_SECONDS}"
    return hit


@router.get("/api/competition-logo")
async def get_competition_logo(name: str, response: Response, sport: str = "Soccer"):
    """
    Competition/league badge lookup (World Cup, Premier League, EuroLeague,
    etc.). For football competitions we already know (our own LEAGUES dict),
    tries football-data.org's emblem first — a real API key with exact code
    matching, no fuzzy search needed. Falls back to the generic
    TheSportsDB-backed lookup (used for basketball etc., or if the football
    league isn't one of ours). Cached server-side (Redis, 30 days).
    """
    from competition_logos import lookup_competition_logo
    key = ("competition", name, sport)
    hit = main._logo_answer(response, key)
    if hit is not None:
        return hit

    # International competitions: the badge their fixture source supplied
    logo = main._international_competition_logo(name)
    if logo:
        return main._logo_answer(response, key, {"name": name, "sport": sport, "logo": logo, "source": "fixture-source"})

    logo = await main._get_football_competition_emblem(name)
    source = "football-data.org" if logo else None

    if not logo:
        r = main._get_redis()
        logo = await lookup_competition_logo(name, redis_client=r, sport=sport)
        source = "thesportsdb" if logo else None

    found = {"name": name, "sport": sport, "logo": logo, "source": source}
    import europe_fixtures
    if name in {display for _slug, display, *_r in europe_fixtures.COMPETITIONS.values()}:
        # ESPN's badge arrives with the first Europe fixtures read after a
        # restart: until then the fallback is answered, not remembered
        response.headers["Cache-Control"] = "public, max-age=300"
        return found
    return main._logo_answer(response, key, found)


@router.get("/api/debug/competition-logo")
async def debug_competition_logo(name: str, sport: str = "Soccer", _admin: str = Depends(main.require_admin)):
    """
    Diagnose the competition logo lookup end-to-end.

    Primary path (football only): football-data.org's /competitions/{code}
    emblem field, using our own LEAGUES dict to map `name` -> code exactly
    (no fuzzy matching needed, and it's a real paid key rather than
    TheSportsDB's rate/data-limited free "3" test key).

    Fallback path (any sport, or if the football-data.org lookup found
    nothing): TheSportsDB's all_leagues.php (the one endpoint that takes NO
    query params, so its shape can't be guessed wrong) dumps every league
    across every sport; we filter to `sport` and fuzzy-match `name` against
    it, then lookupleague.php?id=X for the badge. TheSportsDB's free test key
    is known to return only a handful of domestic leagues (no World Cup), so
    this is mainly relevant for basketball etc.

    Example: /api/debug/competition-logo?name=World%20Cup
    (URL-encode the space — "World Cup" unencoded gets truncated to "World"
    by some HTTP clients/shells.)
    """
    from competition_logos import (
        lookup_competition_logo, _NAME_ALIASES, _get_all_leagues,
        find_best_league_match, _sim, SPORTSDB_BASE,
    )
    import httpx as _httpx

    out: Dict = {"name": name, "sport": sport}

    # --- football-data.org path ---
    fd_code = main._LEAGUE_NAME_TO_CODE.get(name.strip().lower())
    r = main._get_redis()
    cached_raw = None
    if fd_code and r:
        try:
            cached_raw = r.get(main._competition_emblem_cache_key(fd_code))
        except Exception:
            pass
    out["football_data"] = {
        "matched_league_code": fd_code,
        "api_key_set": bool(main.API_KEY),
        "redis_cached_value": cached_raw,
        "redis_cache_treated_as_confirmed": bool(cached_raw) if fd_code else None,
    }
    if fd_code and main.API_KEY:
        try:
            url = f"{API_BASE}/competitions/{fd_code}"
            async with _httpx.AsyncClient() as client:
                resp = await client.get(url, headers={"X-Auth-Token": main.API_KEY}, timeout=20)
            out["football_data"]["status"] = resp.status_code
            if resp.status_code == 200:
                data = resp.json()
                out["football_data"]["emblem"] = data.get("emblem")
            else:
                out["football_data"]["raw_response_sample"] = resp.text[:500]
        except Exception as e:
            out["football_data"]["error"] = str(e)

    fd_emblem = out["football_data"].get("emblem")
    if fd_emblem:
        # Heal the shared cache immediately rather than waiting for the next
        # pipeline run — useful right after a fix like this one, where the
        # cache may hold a stale negative result from before the fix.
        main._cache_competition_emblem(fd_code, fd_emblem)
        out["football_data"]["cache_healed"] = True
        out["logo"] = fd_emblem
        out["source"] = "football-data.org"
        return out

    # --- TheSportsDB fallback path ---
    alias = _NAME_ALIASES.get(name.strip().lower())
    search_name = alias or name
    out["alias_used"] = alias
    out["search_name"] = search_name

    async with _httpx.AsyncClient(timeout=20) as client:
        list_resp = await client.get(f"{SPORTSDB_BASE}/all_leagues.php")
        out["all_leagues_status"] = list_resp.status_code
        if list_resp.status_code != 200:
            out["all_leagues_raw_sample"] = list_resp.text[:500]
            r = main._get_redis()
            out["logo"] = await lookup_competition_logo(name, redis_client=r, sport=sport)
            return out

        leagues = await _get_all_leagues(client)
        out["total_leagues_all_sports"] = len(leagues)
        out["distinct_sports_seen"] = sorted({lg.get("strSport") for lg in leagues if lg.get("strSport")})[:20]
        sport_leagues = [lg for lg in leagues if (lg.get("strSport") or "").lower() == sport.lower()]
        out["leagues_found_for_sport"] = len(sport_leagues)
        out["sample_league_names_for_sport"] = [lg.get("strLeague") for lg in sport_leagues[:8]]

        match = find_best_league_match(search_name, leagues, sport=sport)
        out["best_match"] = {
            "name": match.get("strLeague"),
            "id": match.get("idLeague"),
            "sport": match.get("strSport"),
            "score": round(_sim(search_name, match.get("strLeague") or ""), 3),
        } if match else None

        if match and match.get("idLeague"):
            resp = await client.get(f"{SPORTSDB_BASE}/lookupleague.php", params={"id": match["idLeague"]})
            out["lookupleague_status"] = resp.status_code
            out["lookupleague_response_sample"] = resp.text[:800]

    r = main._get_redis()
    out["logo"] = await lookup_competition_logo(name, redis_client=r, sport=sport)
    out["source"] = "thesportsdb" if out["logo"] else None

    return out
