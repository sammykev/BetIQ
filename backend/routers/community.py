"""
Value bets, leaderboard, tracking, referrals and the SportyBet event lookup.

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import json
import re
from auth import optional_user, require_user
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request
from typing import Any, Dict, List, Tuple

import main

router = APIRouter()


@router.get("/api/value-bets")
async def get_value_bets():
    """
    Returns predictions where BetIQ's model probability beats the market's
    implied probability by >= 3%. Results cached in Redis for 30 min.

    Odds are read from the same throttled odds cache the pipeline maintains
    (_get_live_odds_throttled) instead of calling The Odds API directly —
    this used to only cache non-empty results, so once the API's free quota
    was exhausted, EVERY page view retried the dead API with no backoff,
    which kept the quota from ever recovering. Now this endpoint never calls
    The Odds API on its own; it just reads whatever the pipeline last fetched.
    """
    from odds_fetcher import compute_value_bets
    import json as _json

    CACHE_KEY = "betiq:value_bets"
    r = main._get_redis()

    if r:
        try:
            cached = r.get(CACHE_KEY)
            if cached is not None:
                return _json.loads(cached)
        except Exception:
            pass

    preds = main._predictions_cache
    print(f"[ValueBets] {len(preds)} predictions in cache")
    if not preds:
        return []

    # Log sample dates so we know what we're working with
    dates = sorted({p.get("date", "") for p in preds if p.get("date")})
    print(f"[ValueBets] Prediction dates: {dates[:5]}")

    try:
        import value_bets as vb
        now = datetime.now(timezone.utc)
        today, clock = now.date().isoformat(), now.strftime("%H:%M")
        upcoming = [p for p in preds if p.get("sport") in (None, "football")
                    and (p.get("date", "") > today or (p.get("date") == today and (p.get("time") or "99:99") > clock))
                    and main._within_window(p.get("date", ""))]
        # SportyBet's own prices for linked matches (bookable), then The Odds
        # API's cached 1X2 odds for matches SportyBet doesn't list
        found: Dict[str, Dict] = {}
        for p in upcoming:
            v = vb.best_value(p, main._linked_event(p))
            if v:
                found[main._sb_key(p["home"], p["away"], p["date"])] = v
        odds_index, _ = main._load_cached_live_odds()
        for v in compute_value_bets(upcoming, odds_index):
            found.setdefault(main._sb_key(v["home"], v["away"], v["date"]), v)
        value_bets = sorted(found.values(), key=lambda x: -x["edge"])
        print(f"[ValueBets] Found {len(value_bets)} value bets ({sum(v.get('bookie') == 'SportyBet' for v in value_bets)} "
              f"priced by SportyBet, {len(odds_index)} cached Odds API entries)")

        if r:
            try:
                # Cache even empty results (briefly) — the odds source is
                # already throttled upstream, this just avoids recomputing
                # compute_value_bets() on every request during quiet periods.
                ttl = 1800 if value_bets else 300
                r.setex(CACHE_KEY, ttl, _json.dumps(value_bets))
            except Exception:
                pass

        return value_bets
    except Exception as e:
        print(f"[ValueBets] Error: {e}")
        return []


def _leaderboard(r) -> List[Tuple[str, int]]:
    """Accounts by winning tickets: codes booked here and settled by the
    server from the results, so nobody can report their own wins."""
    wins: Dict[str, int] = {}
    for t in main._all_tickets(r):
        if t.get("status") == "won" and t["uid"] != main.ANON_UID:
            wins[t["uid"]] = wins.get(t["uid"], 0) + 1
    return sorted(wins.items(), key=lambda kv: (-kv[1], kv[0]))[:main.LEADERBOARD_SIZE]


@router.get("/api/leaderboard")
async def get_leaderboard(request: Request, uid: str = ""):
    """Top predictors. Public, so it never returns user ids (those are what the
    user endpoints key on); `you` marks the caller's own row when signed in."""
    r = main._get_redis()
    if not r: return []
    # The verified user; the client's uid only in local development
    import auth
    me = (uid or None) if auth.unverified_uid_allowed() else await optional_user(request)
    try:
        rows = await asyncio.to_thread(main._leaderboard, r)
    except Exception:
        return []
    return [{"name": f"#{u[-6:]}", "wins": n, "you": u == me} for u, n in rows]


@router.post("/api/track/match")
async def track_match(body: Dict[str, Any]):
    home = str(body.get("home", ""))
    away = str(body.get("away", ""))
    if not home or not away: return {"ok": True}
    # Only real fixtures, so junk can't grow the stats without bound
    if not any(p.get("home") == home and p.get("away") == away for p in main._predictions_cache):
        return {"ok": True}
    r = main._get_redis()
    if r:
        try: r.zincrby("betiq:stats:matches", 1, f"{home} vs {away}")
        except Exception: pass
    return {"ok": True}


@router.post("/api/track/league")
async def track_league(body: Dict[str, Any]):
    league = str(body.get("league", ""))
    if not re.fullmatch(r"[A-Za-z0-9-]{1,16}", league): return {"ok": True}
    r = main._get_redis()
    if r:
        try: r.zincrby("betiq:stats:leagues", 1, league)
        except Exception: pass
    return {"ok": True}


@router.get("/api/admin/popular")
async def get_popular(_admin: str = Depends(main.require_admin)):
    r = main._get_redis()
    if not r: return {"matches": [], "leagues": []}
    try:
        matches = r.zrevrange("betiq:stats:matches", 0, 9, withscores=True)
        leagues = r.zrevrange("betiq:stats:leagues", 0, 9, withscores=True)
        return {
            "matches": [{"name": n, "clicks": int(s)} for n, s in matches],
            "leagues": [{"code": c, "clicks": int(s)} for c, s in leagues],
        }
    except Exception:
        return {"matches": [], "leagues": []}


@router.get("/api/referral/stats")
async def get_referral_stats(request: Request, uid: str = ""):
    uid = await require_user(request, uid)
    r = main._get_redis()
    if not r: return {"count": 0}
    code = f"ref_{uid[-8:]}"
    count = int(r.get(f"betiq:referral:{code}:count") or 0)
    return {"code": code, "count": count, "link": f"https://predict-withbetiq.vercel.app?ref={code}"}


@router.post("/api/referral/use")
async def use_referral(request: Request, body: Dict[str, Any]):
    """Called when a new user signs up with a referral code: counts once per
    signed-in user, never for their own code."""
    uid = await require_user(request, body.get("uid", ""))
    code = str(body.get("code", "")).strip()
    if not re.fullmatch(r"ref_[A-Za-z0-9]{1,8}", code) or code == f"ref_{uid[-8:]}":
        return {"ok": False}
    r = main._get_redis()
    if r:
        try:
            if r.set(f"betiq:referral:used:{uid}", code, nx=True):
                r.incr(f"betiq:referral:{code}:count")
        except Exception:
            pass
    return {"ok": True}


@router.get("/api/debug/odds-sample")
async def debug_odds_sample(_admin: str = Depends(main.require_admin)):
    """
    Returns raw SportyBet events for the first prediction date we have cached,
    so we can verify the odds extraction is working.
    """
    from sportybet import fetch_events_for_date
    from datetime import date

    # Use first prediction date if available, otherwise today
    dates = sorted({p.get("date","") for p in (main._predictions_cache or []) if p.get("date")})
    target = dates[0] if dates else date.today().isoformat()

    results = {}
    for d in (dates[:5] if dates else [target]):
        evs = await fetch_events_for_date(d)
        results[d] = len(evs)

    # Also clear value bets cache so next request recomputes
    r = main._get_redis()
    if r:
        try: r.delete("betiq:value_bets")
        except: pass

    return {
        "all_prediction_dates": dates[:10],
        "events_per_date": results,
        "note": "Value bets Redis cache cleared — refresh /api/value-bets now",
    }


@router.get("/api/sportybet-event")
async def get_sportybet_event(home: str, away: str, date: str):
    """
    Fetch the live SportyBet event for a specific match and return its full
    market/outcome structure with real IDs — used by the in-modal bet picker.
    """
    from sportybet import fetch_events_for_date, find_event

    CACHE_KEY = f"betiq:sb_event:{home}:{away}:{date}"
    r = main._get_redis()
    if r:
        try:
            cached = r.get(CACHE_KEY)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

    try:
        events = await fetch_events_for_date(date)
        event = find_event(home, away, events)
        if not event:
            if r:   # SportyBet may list it later: look again in 10 minutes
                try: r.setex(CACHE_KEY, 600, json.dumps({"found": False, "markets": []}))
                except Exception: pass
            return {"found": False, "markets": []}

        # Build a clean market list from the raw event
        clean_markets = []
        for m in (event.get("markets") or []):
            mid = str(m.get("id") or "")
            mname = m.get("name") or m.get("desc") or ""
            specifier = m.get("specifier") or ""
            outcomes = []
            for o in (m.get("outcomes") or []):
                if not o.get("isActive", 1):
                    continue
                outcomes.append({
                    "id":    str(o.get("id") or ""),
                    "desc":  o.get("desc") or o.get("name") or "",
                    "odds":  str(o.get("odds") or o.get("value") or ""),
                })
            if outcomes:
                clean_markets.append({
                    "id":        mid,
                    "name":      mname,
                    "specifier": specifier,
                    "outcomes":  outcomes,
                })

        result = {
            "found":      True,
            "eventId":    str(event.get("eventId") or event.get("id") or ""),
            "gameId":     str(event.get("gameId") or ""),
            "homeTeam":   event.get("homeTeamName") or home,
            "awayTeam":   event.get("awayTeamName") or away,
            "markets":    clean_markets,
        }

        if r and result["found"]:
            try:
                r.setex(CACHE_KEY, 1800, json.dumps(result))
            except Exception:
                pass

        return result
    except Exception as e:
        print(f"[SportyBet Event] {home} vs {away}: {e}")
        return {"found": False, "markets": [], "error": str(e)}
