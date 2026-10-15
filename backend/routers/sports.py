"""
Other sports' listings: leagues and predictions per sport, event detail.

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import json
import re
from fastapi import APIRouter, HTTPException, Request
from typing import Optional

import main

router = APIRouter()


def _sportybet_event_id(sport: str, home: str, away: str, day: str) -> Optional[str]:
    """The SportyBet event behind a tennis / table-tennis prediction, from the cached list."""
    key = "table-tennis" if sport.startswith("table") else sport
    cached = None
    r = main._get_redis()
    if r:
        try:
            cached = json.loads(r.get(f"betiq:sports:{key}") or "null")
        except Exception:
            cached = None
    if cached is None:
        cached = (main._sports_memory_cache.get(key) or (None,))[0]
    for p in cached or []:
        if p.get("home") == home and p.get("away") == away and p.get("date") == day and p.get("sportybet_event_id"):
            return str(p["sportybet_event_id"])
    return None


def _check_sport(sport: str) -> None:
    # Sport names go into cache keys and outside API calls: known ones only
    if sport not in main.SPORTS:
        raise HTTPException(status_code=404, detail="Unknown sport")


async def _check_sport_access(request: Request, sport: str) -> None:
    """Sports other than football can be switched off or kept for testers."""
    import access
    if sport in access.SPORT_FEATURES:
        await main.check_feature(request, access.SPORT_FEATURES[sport])


@router.get("/api/sports/{sport}/leagues")
async def get_sport_leagues(sport: str, request: Request):
    """Return distinct leagues/tournaments being predicted for a sport."""
    main._check_sport(sport)
    await main._check_sport_access(request, sport)
    import json as _json
    cache_key = f"betiq:sports:{sport}"
    r = main._get_redis()
    preds = []
    if r:
        try:
            cached = r.get(cache_key)
            if cached:
                preds = _json.loads(cached)
        except Exception:
            pass
    leagues = sorted({p.get("league_name","") or p.get("league","") for p in preds if p.get("league_name") or p.get("league")})
    return {"sport": sport, "leagues": leagues, "total_predictions": len(preds)}


@router.get("/api/sports/{sport}")
async def get_sport_predictions(sport: str, request: Request):
    """
    Multi-sport predictions endpoint.
    sport: basketball | tennis | table-tennis
    Requires ODDS_API_KEY env var.
    """
    main._check_sport(sport)
    await main._check_sport_access(request, sport)
    import time as _time
    from sports_fetcher import (
        fetch_basketball_predictions,
        fetch_tennis_predictions,
        fetch_table_tennis_predictions,
    )
    from event_filters import drop_started_events
    import json as _json

    CACHE_TTL = 3600  # 1 hour — conserves Odds API quota
    cache_key = f"betiq:sports:{sport}"
    r = main._get_redis()

    if r:
        try:
            cached = await asyncio.to_thread(r.get, cache_key)
            if cached:
                out = drop_started_events(_json.loads(cached))
                return main._with_racket_form(out, "table_tennis" if sport == "table-tennis" else sport) if sport in ("tennis", "table-tennis") else out
        except Exception:
            pass
    else:
        # No Redis (or it's briefly down) — fall back to an in-process cache
        # so this endpoint still doesn't hit The Odds API on every request.
        # Without this, any Redis outage turns every page view into a fresh
        # API call, burning the free 500/month quota within hours.
        cached_entry = main._sports_memory_cache.get(sport)
        if cached_entry and (_time.monotonic() - cached_entry[1]) < CACHE_TTL:
            out = drop_started_events(cached_entry[0])
            return main._with_racket_form(out, "table_tennis" if sport == "table-tennis" else sport) if sport in ("tennis", "table-tennis") else out

    if sport == "basketball":
        data = [main._bb_slim(p) for p in (main._bb_upcoming() or await main._bb_refresh())]
        if not data:
            data = await fetch_basketball_predictions()
    elif sport in ("tennis", "table-tennis"):
        rk = "table_tennis" if sport == "table-tennis" else "tennis"
        # Every SportyBet line priced (racket_predictions); the old path if the listing is down
        data = [main._rk_slim(p) for p in (main._rk_upcoming(rk) or await main._rk_refresh(rk))]
        if not data:
            data = await (fetch_tennis_predictions() if rk == "tennis" else fetch_table_tennis_predictions())
    else:
        raise HTTPException(status_code=400, detail=f"Unknown sport: {sport}")

    if r:
        try:
            # An empty list (a source down) is retried in 5 minutes, not an hour
            r.setex(cache_key, CACHE_TTL if data else 300, _json.dumps(data))
        except Exception:
            pass
    elif data:
        main._sports_memory_cache[sport] = (data, _time.monotonic())

    out = drop_started_events(data)
    return main._with_racket_form(out, "table_tennis" if sport == "table-tennis" else sport) if sport in ("tennis", "table-tennis") else out


@router.get("/api/sports/{sport}/event")
async def get_sport_event_detail(sport: str, home: str, away: str, date: str, request: Request):
    """
    Full market detail for a specific basketball/tennis/table-tennis match.
    Used by the sport analysis modal.
    """
    main._check_sport(sport)
    await main._check_sport_access(request, sport)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or len(home) > 80 or len(away) > 80:
        raise HTTPException(status_code=400, detail="Invalid match")
    from sports_fetcher import fetch_event_detail
    import json as _json

    cache_key = f"betiq:sport_event:{sport}:{home}:{away}:{date}"
    r = main._get_redis()
    if r:
        try:
            cached = r.get(cache_key)
            if cached:
                return _json.loads(cached)
        except Exception:
            pass

    detail = None
    # Matches taken from SportyBet's listing: its own match page has every market
    event_id = main._sportybet_event_id(sport, home, away, date)
    if event_id:
        try:
            import sportybet
            from sports_fetcher import structure_sportybet_detail
            ev = await sportybet.event_page(event_id)
            if ev:
                detail = structure_sportybet_detail(ev, "table_tennis" if sport.startswith("table") else sport)
        except Exception as e:
            print(f"[Sports Detail] SportyBet {event_id}: {e}")
    if not detail:
        detail = await fetch_event_detail(sport, home, away, date)
    if not detail:
        raise HTTPException(status_code=404, detail="Event not found")

    if r:
        try:
            r.setex(cache_key, 3600, _json.dumps(detail))
        except Exception:
            pass
    return detail
