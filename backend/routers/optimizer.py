"""
The slip optimizer and booking-code conversion.

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import json
import os
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Any, Dict, Iterable, List, Optional, Tuple

import main

router = APIRouter()

# A slip is "high confidence" when it asks each pick for at least this
# chance, or sets a least chance for the whole slip
HIGH_CONF_PROB = 0.80
# High-confidence slips the user builds stop at this many legs: in the
# confidence check's history the day's top 10 picks all won 73.9% of days,
# the top 20 35.0% and the top 30 21% (10 legs: about the most that still
# lands most days). Longer asks get the rest as separate slips of ≤ 10
HIGH_CONF_LEGS = 10
LEG_HISTORY = {10: 0.739, 20: 0.35, 30: 0.21}
WEAK_TTL = 3600
_weak_cache: Dict[str, Any] = {"at": 0.0, "cells": set()}


def _weak_cells() -> set:
    """Market and league cells ("sport:market", "sport:league") the last
    confidence check found falling short (confidence_check.weak_cells), read
    from Redis at most every WEAK_TTL seconds."""
    import confidence_check
    if time.time() - _weak_cache["at"] < WEAK_TTL:
        return _weak_cache["cells"]
    cells: set = set()
    r = main._get_redis()
    if r:
        try:
            raw = r.get(confidence_check.REPORT_KEY)
            cells = confidence_check.weak_cells(json.loads(raw)) if raw else set()
        except Exception as e:
            print(f"[Optimizer] couldn't read the confidence check: {e}")
    _weak_cache.update(at=time.time(), cells=cells)
    return cells


# Slips the user builds take markets whose picks came in at least this often
# in the nightly ranking (market_ranking.py, last RANKING_DAYS days); the
# daily slips ask for more (daily_slips.MIN_HIT_RATE)
MIN_HIT_RATE = 0.70
RANKING_TTL = 600
# When no slip from the markets at MIN_HIT_RATE+ reaches the customer's odds,
# the optimizer steps down the ranking this many points at a time
FALLBACK_STEP = 0.05
LEFT_OUT_KEY = "betiq:optimizer:left_out"
LEFT_OUT_KEEP = 100
_ranking_cache: Dict[str, Any] = {"at": 0.0, "codes": None}
# A match already on this many of the account's open tickets isn't put on
# another one: one bad match shouldn't sink every ticket at once. 0: no cap
MAX_TICKETS_PER_MATCH = int(os.getenv("MAX_TICKETS_PER_MATCH", "2") or 0)
# Each pick's least chance when the request doesn't say. A market's hit rate
# doesn't vouch for its weaker picks: in the Oct 2026 confidence check picks
# rated 80–85% came in 80%, so a 60% floor let long shots onto tickets
MIN_PICK_PROB = 0.85
# Markets left out of every slip: in the Oct 2026 confidence check their
# 80%+ picks came in well under what the model said (basketball total 78% vs
# 82%, Q1 74% vs 85%, Q3 78% vs 87%, first-half total 50% vs 81%, tennis
# set 1 handicap 76% vs 83%). Extra codes (sport:market, comma-separated)
# from HELD_OUT_MARKETS; "none" clears the list
HELD_OUT_MARKETS = (set() if os.getenv("HELD_OUT_MARKETS", "").strip().lower() == "none" else
                    {"basketball:bb_total", "basketball:bb_q1_1x2", "basketball:bb_q3_1x2",
                     "basketball:bb_h1_total", "tennis:rk_s1_handicap"}
                    | {c.strip() for c in os.getenv("HELD_OUT_MARKETS", "").split(",") if ":" in c})


# A pick's chance is pulled down by how far its market's picks came in under
# what the model said in the nightly ranking, weighted by how many settled:
# n / (n + CALIBRATION_PRIOR) of the gap. Markets that came in over aren't raised
CALIBRATION_PRIOR = 30
# A market that came in this far under what the model said, over at least
# OVERCONFIDENT_MIN_N settled picks, is left out of every slip until it recovers
OVERCONFIDENT_GAP = 0.10
OVERCONFIDENT_MIN_N = 20


def _gap(x: Optional[Dict[str, Any]]) -> float:
    """How far the market's picks came in under what the model said (0 if over or unknown)."""
    if not x or not isinstance(x.get("said"), (int, float)) or not isinstance(x.get("hit_rate"), (int, float)):
        return 0.0
    return max(0.0, x["said"] - x["hit_rate"])


def _overconfident(x: Optional[Dict[str, Any]]) -> bool:
    return bool(x) and x.get("n", 0) >= OVERCONFIDENT_MIN_N and _gap(x) >= OVERCONFIDENT_GAP - 1e-9


def _calibrated(o, x: Optional[Dict[str, Any]]):
    """The option with its chance pulled down to its market's record; the model's own kept as model_prob."""
    gap = _gap(x)
    if gap <= 0:
        return o
    n = x.get("n", 0)
    prob = max(0.01, o.prob - gap * n / (n + CALIBRATION_PRIOR))
    return replace(o, prob=round(prob, 4), model_prob=o.prob)


def _crowded_matches(tickets: List[Dict[str, Any]], cap: int) -> List[Tuple[str, str, str]]:
    """The matches (home, away, date) still to be played on `cap` or more of
    these open tickets."""
    if cap <= 0:
        return []
    counts: Dict[Tuple[str, str, str], int] = {}
    for t in tickets:
        if t.get("status") not in ("pending", "open"):
            continue
        for k in {(str(l.get("home")), str(l.get("away")), str(l.get("date"))) for l in t.get("legs") or []
                  if (l.get("status") or "pending") in ("pending", "unknown")}:
            counts[k] = counts.get(k, 0) + 1
    return sorted(k for k, n in counts.items() if n >= cap)


def _account_tickets(uid: Optional[str]) -> List[Dict[str, Any]]:
    r = main._get_redis() if uid else None
    if not r:
        return []
    try:
        raw = r.get(main._ukey(uid, "tickets"))
        items = json.loads(raw) if raw else []
    except Exception:
        return []
    return [t for t in items if isinstance(t, dict)]


def _ranked_codes() -> Optional[Dict[str, Dict[str, Any]]]:
    """The nightly ranking's hit rate per "sport:market" ({n, hit_rate}),
    read from Redis at most every RANKING_TTL seconds; None before the
    first ranking (no gate then)."""
    if time.time() - _ranking_cache["at"] < RANKING_TTL:
        return _ranking_cache["codes"]
    codes = None
    r = main._get_redis()
    if r:
        try:
            raw = r.get(main.RANKING_KEY)
            codes = (json.loads(raw) or {}).get("codes") if raw else None
        except Exception as e:
            print(f"[Optimizer] couldn't read the market ranking: {e}")
    _ranking_cache.update(at=time.time(), codes=codes)
    return codes


def _hit_floors(rates: List[float], min_hit: float) -> List[float]:
    """The hit-rate floors to try in turn: min_hit, then FALLBACK_STEP lower
    each time down the ranking, skipping a step that lets in no new market."""
    floors, f = [min_hit], min_hit
    below = sorted((r for r in rates if r < min_hit), reverse=True)
    while below:
        f = max(0.0, round(f - FALLBACK_STEP, 4))
        if below[0] >= f:
            floors.append(f)
            below = [r for r in below if r < f]
    return floors


def _market_hit(pick: Dict[str, Any], ranked: Dict[str, Dict[str, Any]], min_hit: float) -> None:
    """The pick's market in the nightly ranking: {n, hit_rate, said, roi},
    flagged when it came in under min_hit or lost money at its odds."""
    x = ranked.get(f"{pick.get('sport') or 'football'}:{pick.get('market')}")
    if not x:
        return
    pick["market_hit"] = {"n": x["n"], "hit_rate": x["hit_rate"], "said": x.get("said"), "roi": x.get("roi"),
                          "below_min": x["hit_rate"] < min_hit,
                          "negative_roi": x.get("roi") is not None and x["roi"] < 0}


def _log_left_out(entry: Dict[str, Any]) -> None:
    """Keep the last LEFT_OUT_KEEP gated runs (what the hit-rate gate left out) for the admin panel."""
    r = main._get_redis()
    if not r:
        return
    try:
        runs = json.loads(r.get(LEFT_OUT_KEY) or "[]")
        r.set(LEFT_OUT_KEY, json.dumps(([entry] + runs)[:LEFT_OUT_KEEP], separators=(",", ":")))
    except Exception as e:
        print(f"[Optimizer] couldn't log the left-out markets: {e}")


@router.get("/api/admin/optimizer/left-out")
async def admin_left_out(_admin: str = Depends(main.require_admin)):
    """The last gated optimizer and daily runs: the markets the hit-rate gate
    left out, and the ones under it a slip had to fall back on."""
    r = main._get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="The database isn't connected")
    try:
        runs = json.loads(r.get(LEFT_OUT_KEY) or "[]")
    except Exception:
        runs = []
    return {"runs": runs, "min_hit_rate": MIN_HIT_RATE, "fallback_step": FALLBACK_STEP}


def _leg_note(asked: int, cap: int) -> str:
    rates = ", ".join(f"{n} legs {round(r * 100)}%" for n, r in LEG_HISTORY.items())
    return (f"High-confidence slips stop at {cap} legs: the longer a slip, the likelier one leg lets it down "
            f"(how often the day's most confident picks all won: {rates}). "
            f"You asked for up to {asked}, so the other matches are offered as separate slips of up to {cap}.")


def _bookable_markets():
    """(market, code) → whether a SportyBet code can take the pick now:
    trusted markets always, VERIFIED ones once SportyBet's own labels
    confirmed them. Reads the confirmed markets once."""
    import booking_slip
    confirmed = {k for k, v in main._sb_market_map().items() if (v or {}).get("ok")}

    def ok(market: str, code: str) -> bool:
        if not booking_slip.sportybet_ids(market, code):
            return False
        kind = booking_slip.verified_kind(market, code)
        return not kind or kind in confirmed
    return ok


def _explain_empty(reasons: List[Dict], matches: int, days: int, min_prob: float) -> str:
    """One sentence per market saying why it gave no picks."""
    span = f"the {matches} match{'es' if matches != 1 else ''} in the next {days} day{'s' if days != 1 else ''}"
    parts = []
    for r in reasons:
        if r["reason"] == "no_data":
            extra = (" Each team's corners come from club-league match stats, so internationals have none."
                     if r["market"] in ("home_corners_ou", "away_corners_ou", "corners_1x2") else
                     " Shots come from club-league match stats (the nine leagues with shot counts), so "
                     "internationals and other clubs have none."
                     if r["market"].endswith(("shots_ou", "sot_ou")) else
                     " These come from club-league stats, or for other matches from SportyBet's own line once "
                     "SportyBet lists the match with that market (tick \"Only matches SportyBet lists\")."
                     if r["market"] in ("corners_ou", "cards_ou") else "")
            parts.append(f"{r['name']}: no predictions for {span}.{extra}")
        elif r["reason"] == "below_minimum":
            parts.append(f"{r['name']}: the likeliest pick is {round(r['best'] * 100)}%, under the "
                         f"{round(min_prob * 100)}% minimum.")
        else:
            parts.append(f"{r['name']}: SportyBet hasn't confirmed this market yet, so codes can't include it. "
                         "Untick \"Only matches SportyBet lists\" to build the slip anyway.")
    return " ".join(parts) or (f"None of {span} has a pick at {round(min_prob * 100)}% or more." if min_prob
                                else f"None of {span} has a pick in the markets you chose.")


def _optimizer_sports(body: Dict[str, Any]) -> List[str]:
    """The sports an optimizer request picks from: `sports` (a list, any of
    OPT_SPORTS or "all") or else `sport` (one of them or "all"; football by default)."""
    raw = body.get("sports")
    if isinstance(raw, list) and raw:
        asked = {str(x) for x in raw}
    elif raw is not None and not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="sports must be a list")
    else:
        asked = {str(body.get("sport") or "football")}
    if "all" in asked:
        asked = set(main.OPT_SPORTS)
    bad = asked - set(main.OPT_SPORTS)
    if bad:
        raise HTTPException(status_code=400, detail=f"Unknown sport: {', '.join(sorted(bad))} "
                                                    "(football, basketball, tennis, table_tennis or all)")
    return [x for x in main.OPT_SPORTS if x in asked]


@router.get("/api/optimizer/leagues")
async def optimizer_leagues(request: Request, days: int = 3, sports: str = "football"):
    """The leagues with matches the optimizer can pick from: in the next
    `days` days, for the sports asked (comma-separated), with how many
    matches each has, busiest first. The optimizer's `leagues` takes their ids."""
    days = min(main.PREDICTION_DAYS, max(1, int(days)))
    asked = main._optimizer_sports({"sports": [s for s in sports.split(",") if s]})
    now = datetime.now(timezone.utc)
    today, last = now.date().isoformat(), (now.date() + timedelta(days=days - 1)).isoformat()
    kicked_off = now.strftime("%H:%M")
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def add(sport: str, preds: Iterable[Dict]) -> None:
        for p in preds:
            d = p.get("date") or ""
            if not today <= d <= last or (d == today and (p.get("time") or "99:99") <= kicked_off):
                continue
            lg = str(p.get("league") or "")
            if not lg:
                continue
            row = out.setdefault((sport, lg), {"sport": sport, "id": lg, "name": p.get("league_name") or lg,
                                               "flag": p.get("flag") or "", "matches": 0})
            row["matches"] += 1
    for sp in asked:
        if sp != "football":
            try:
                await main._check_sport_access(request, "basketball" if sp == "basketball" else main._rk_url(sp))
            except HTTPException:
                continue
        if sp == "football":
            add(sp, (p for p in main._predictions_cache if p.get("sport") in (None, "football")))
        elif sp == "basketball":
            add(sp, main._bb_upcoming())
        else:
            add(sp, main._rk_upcoming(sp))
    return {"leagues": sorted(out.values(), key=lambda x: (-x["matches"], x["name"]))}


@router.post("/api/optimizer")
async def optimize_slip(request: Request, body: Dict[str, Any], _access=Depends(main.require_feature("optimizer"))):
    """
    Build the slip with the best win chance whose total odds land in a target
    range (optimizer.py). Body: {min_odds, max_odds, max_games?, min_prob?,
    days?, leagues?: [codes], markets?: [ids], codes?: {market: [option
    codes]} (e.g. only some goal lines), bookable_only?, sports?: [football,
    basketball, tennis, table_tennis: any of them] (or sport?: one of them or
    all), bb_markets?: [basketball families], tn_markets? / tt_markets?:
    [tennis / table tennis families]}.
    """
    body = dict(body or {})
    sports = main._optimizer_sports(body)
    if len(sports) == 1:
        if sports[0] != "football":
            await main._check_sport_access(request, "basketball" if sports[0] == "basketball" else main._rk_url(sports[0]))
    else:
        # Sports switched off (or for testers only) are left out of a mixed slip
        off, refused = [], None
        for sp in sports:
            if sp == "football":
                continue
            try:
                await main._check_sport_access(request, "basketball" if sp == "basketball" else main._rk_url(sp))
            except HTTPException as e:
                off.append(sp)
                refused = e
        if refused and len(off) == len(sports):
            raise refused
        body["_off"] = off
    # The account's open tickets: a match already on MAX_TICKETS_PER_MATCH of them is left out
    crowded: List[Tuple[str, str, str]] = []
    if MAX_TICKETS_PER_MATCH and body.get("spread_matches", True) is not False:
        import auth
        uid = await auth.optional_user(request)
        if not uid and auth.unverified_uid_allowed():
            uid = str(body.get("uid") or "")[:64] or None
        crowded = _crowded_matches(await asyncio.to_thread(_account_tickets, uid), MAX_TICKETS_PER_MATCH)
        if crowded:
            body["exclude_matches"] = list(body.get("exclude_matches") or []) + [list(k) for k in crowded]
    result = await main._optimize_request(body)
    if crowded and isinstance(result, dict):
        result["crowded_matches"] = {"left_out": len(crowded), "cap": MAX_TICKETS_PER_MATCH}
    return result


def asdict_option(o) -> Dict[str, Any]:
    return {"home": o.home, "away": o.away, "date": o.date, "market": o.market, "code": o.code}


def _option_dict(o) -> Dict[str, Any]:
    """An option as a pick for the live check (with its own SportyBet ids)."""
    return {**asdict_option(o), "sb": o.sb, "odds": o.odds}


def _pick_key(x: Dict[str, Any]) -> Tuple:
    return (x.get("home"), x.get("away"), x.get("date"), x.get("market"), x.get("code"))


async def _live_pages_for(event_ids: Iterable[str]) -> Dict[str, Optional[Dict]]:
    """Each event's SportyBet match page as it is now (every market and
    outcome, with their status): None when it couldn't be read in time."""
    import sportybet
    now = time.time()
    out: Dict[str, Optional[Dict]] = {}
    todo = []
    for eid in set(event_ids):
        hit = main._live_pages.get(eid)
        if hit and now - hit[0] < main.LIVE_PAGE_SECONDS:
            out[eid] = hit[1]
        else:
            todo.append(eid)
    gate = asyncio.Semaphore(main.LIVE_PAGES_AT_ONCE)

    async def read(eid: str):
        async with gate:
            try:
                return eid, await asyncio.wait_for(sportybet.event_page(eid), main.LIVE_PAGE_TIMEOUT)
            except Exception:
                return eid, None
    for eid, page in await asyncio.gather(*(read(e) for e in todo)):
        out[eid] = page
        if page is not None:
            main._live_pages[eid] = (time.time(), page)
    if len(main._live_pages) > 2000:
        for k, (at, _) in list(main._live_pages.items()):
            if now - at > main.LIVE_PAGE_SECONDS:
                main._live_pages.pop(k, None)
    return out


async def _live_states(picks: List[Dict[str, Any]], events: Dict[Tuple, Optional[Dict]],
                       market_map: Dict[str, Dict[str, Any]]) -> Dict[Tuple, Tuple[str, Optional[float]]]:
    """{pick key: (state, SportyBet's price now)} for the picks a code can
    take: "open" / "suspended" / "missing" / "started", or "unchecked" when
    the match page couldn't be read (those stay, at the price we had)."""
    import booking_slip
    ids_of: Dict[Tuple, Dict[str, str]] = {}
    for x in picks:
        ev = events.get((x["home"], x["away"], x.get("date")))
        ids = booking_slip.pick_ids(x, str((ev or {}).get("eventId") or "") or None, market_map)
        if ids:
            ids_of[main._pick_key(x)] = ids
    pages = await main._live_pages_for(ids["eventId"] for ids in ids_of.values())
    now = time.time()
    out: Dict[Tuple, Tuple[str, Optional[float]]] = {}
    for k, ids in ids_of.items():
        page = pages.get(ids["eventId"])
        if page is None:
            out[k] = ("unchecked", None)
            continue
        start = booking_slip._start_seconds(page)
        if start is not None and start <= now:
            out[k] = ("started", None)
            continue
        out[k] = booking_slip.live_state(page, ids)
    return out


async def _solve(groups, lo: float, hi: float, target: float, max_games: int,
                 min_chance: Optional[float] = None) -> Optional[Dict]:
    """The best slip in [lo, hi]; failing that, the nearest within ±25% of the
    target, flagged as off target (within_target is False). With `min_chance`:
    the highest odds up to `hi` whose win chance is at least that."""
    import optimizer
    if min_chance:
        return await asyncio.to_thread(optimizer.optimize_safe, groups, min_chance, hi, max_games)
    res = await asyncio.to_thread(optimizer.optimize, groups, lo, hi, max_games)
    if res is None and lo <= target <= hi:
        for spread in (0.1, 0.25):
            near = await asyncio.to_thread(optimizer.optimize, groups, max(1.01, target * (1 - spread)),
                                           target * (1 + spread), max_games)
            if near is not None:
                return {**near, "within_target": False}
    return res


async def _solve_checked(groups, lo: float, hi: float, target: float, max_games: int,
                         events: Dict[Tuple, Optional[Dict]],
                         market_map: Dict[str, Dict[str, Any]],
                         min_chance: Optional[float] = None) -> Tuple[Optional[Dict], Dict[str, Any]]:
    """The slip, with every pick checked on SportyBet's match page as it is
    now: suspended, withdrawn or started ones are dropped and the slip solved
    again without them; the rest take SportyBet's current price (solved again
    when one moved), so the code books at the odds shown. Up to
    LIVE_CHECK_ROUNDS rounds, then a last check: a slip still holding a
    suspended pick comes back None. Returns (slip or None, what the check did)."""
    import booking_slip
    check: Dict[str, Any] = {"checked": 0, "removed": [], "repriced": 0, "unchecked": 0}
    result = await main._solve(groups, lo, hi, target, max_games, min_chance)
    for round_ in range(main.LIVE_CHECK_ROUNDS + 1):
        if not result or not result.get("picks"):
            break
        picked = {main._pick_key(x) for x in result["picks"]}
        # Every line on the picked matches, not just the picked one: a match
        # page shows all its suspended lines at once (basketball suspends
        # many together), so the next solve can't land on another of them
        matches = {k[:3] for k in picked}
        others = [_option_dict(o) for g in groups for o in g
                  if (o.home, o.away, o.date) in matches and main._pick_key(main.asdict_option(o)) not in picked]
        states = await main._live_states(list(result["picks"]) + others, events, market_map)
        mine = {k: st for k, st in states.items() if k in picked}
        check["checked"] = sum(1 for st in mine.values() if st[0] != "unchecked")
        check["unchecked"] = sum(1 for st in mine.values() if st[0] == "unchecked")
        by_key = {main._pick_key(x): x for x in result["picks"]}
        gone = {k for k, (st, _) in states.items() if st in (booking_slip.SUSPENDED, booking_slip.MISSING, "started")}
        moved = {k: o for k, (st, o) in mine.items()
                 if st == booking_slip.OPEN and o and abs(o - by_key[k]["odds"]) >= 0.005}
        if not gone & picked and not moved:
            break
        if round_ == main.LIVE_CHECK_ROUNDS:
            # Out of rounds: a slip still holding a suspended pick isn't given out
            if gone & picked:
                result = None
            break
        for k in gone & picked:
            x = by_key[k]
            check["removed"].append({"home": x["home"], "away": x["away"], "label": x["label"],
                                     "reason": {"started": "Already started",
                                                booking_slip.MISSING: "No longer offered on SportyBet"}
                                     .get(states[k][0], "Suspended on SportyBet")})
        check["repriced"] += len(moved)
        groups = [[o for o in g if main._pick_key(main.asdict_option(o)) not in gone] for g in groups]
        for g in groups:
            for o in g:
                k = main._pick_key(main.asdict_option(o))
                if k in moved:
                    o.odds, o.odds_source = round(moved[k], 2), "sportybet"
        result = await main._solve(groups, lo, hi, target, max_games, min_chance)
    return result, check


async def _optimize_request(body: Dict[str, Any],
                             window: Optional[Tuple[datetime, datetime]] = None) -> Dict[str, Any]:
    """The optimizer endpoint's work, for the daily slips too. `window` (UTC
    start, end; server-side only): matches kicking off in it, from now on,
    in place of `days` from today."""
    import booking_slip
    import optimizer
    try:
        lo, hi = float(body.get("min_odds", 2)), float(body.get("max_odds", 5))
        target = float(body.get("target_odds") or (lo * hi) ** 0.5)
        max_games = int(body.get("max_games", optimizer.MAX_GAMES))
        # Each pick's least chance; 0 or null: off (any pick, e.g. a ~30% highest scoring quarter)
        raw = body.get("min_prob", MIN_PICK_PROB)
        min_prob = 0.0 if raw in (None, 0, "off") else min(0.95, max(0.5, float(raw)))
        # The whole slip's least win chance (e.g. 0.9): the highest odds that keep it
        min_chance = float(body["min_slip_chance"]) if body.get("min_slip_chance") else None
        if min_chance is not None:
            min_chance = min(0.99, max(0.5, min_chance))
        # A floor on the target-odds slip's win chance (e.g. 0.1 for a 10x slip:
        # break-even); a slip below it isn't offered. The daily slips use 1/target
        min_win = float(body["min_win_chance"]) if body.get("min_win_chance") else None
        if min_win is not None:
            min_win = min(0.5, max(0.0001, min_win))
        # Optional cap on each pick's price (the daily slips use it): picks at or above it are left out
        max_leg = float(body["max_leg_odds"]) if body.get("max_leg_odds") else None
        # Markets whose ranked hit rate is below this are left out; 0 or "off": no gate
        raw_hit = body.get("min_hit_rate", MIN_HIT_RATE)
        min_hit = 0.0 if raw_hit in (None, 0, "off") else min(1.0, max(0.0, float(raw_hit)))
        # Without a slip at the customer's odds from those markets, step down
        # the ranking to the ones under min_hit only when asked (True): those
        # picks were the weakest legs on the tickets
        hit_fallback = body.get("hit_fallback") is True
        days = min(main.PREDICTION_DAYS, max(1, int(body.get("days", 3))))
        high = min_chance is not None or min_prob >= HIGH_CONF_PROB
        # Markets and leagues the confidence check found falling short stay
        # out of high-confidence slips ("healthy_only" overrides either way)
        healthy = bool(body["healthy_only"]) if body.get("healthy_only") is not None else high
        # A slip the user builds (not the daily slips: `window`) is cut to
        # HIGH_CONF_LEGS when it's high confidence ("allow_long" keeps the ask)
        asked = max_games
        if high and window is None and not body.get("allow_long") and max_games > HIGH_CONF_LEGS:
            max_games = HIGH_CONF_LEGS
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid optimizer settings")
    if not (1.01 <= lo <= hi <= 1_000_000):
        raise HTTPException(status_code=400, detail="Target odds need 1.01 ≤ min ≤ max ≤ 1,000,000")
    leagues = set(body.get("leagues") or [])
    markets = set(body.get("markets") or []) or None
    bookable_only = bool(body.get("bookable_only"))
    codes = body.get("codes") or {}
    if not isinstance(codes, dict):
        raise HTTPException(status_code=400, detail="codes must be {market: [codes]}")
    only = {str(m): {str(c) for c in (cs or [])} for m, cs in codes.items() if isinstance(cs, list) and cs}
    # Any of football (default), basketball, tennis, table tennis: the other
    # sports' picks are the lines SportyBet offers on its listed matches, all bookable
    on = set(main._optimizer_sports(body)) - set(body.get("_off") or body.get("_rk_off") or [])
    if body.get("no_racket") and len(on) > 1:
        on -= set(main.RK_SPORTS)
    if not on:
        raise HTTPException(status_code=403, detail="None of the sports you picked is open to your account")
    # One sport alone gets its own "nothing found" messages
    sport = next(iter(on)) if len(on) == 1 else "mixed"
    bb_families = {str(m) for m in body.get("bb_markets") or []} or None
    rk_families = {"tennis": {str(m) for m in body.get("tn_markets") or []} or None,
                   "table_tennis": {str(m) for m in body.get("tt_markets") or []} or None}
    rk_on = [rk for rk in main.RK_SPORTS if rk in on]

    now = datetime.now(timezone.utc)
    today, last = now.date().isoformat(), (now.date() + timedelta(days=days - 1)).isoformat()
    kicked_off = now.strftime("%H:%M")
    def in_window(p: Dict) -> bool:
        try:
            k = datetime.fromisoformat(f"{p['date']}T{p.get('time') or '12:00'}:00+00:00")
        except (KeyError, TypeError, ValueError):
            return False
        return max(window[0], now) < k < window[1]

    upcoming = [p for p in (main._predictions_cache if "football" in on else [])
                if p.get("sport") in (None, "football")
                and (in_window(p) if window else
                     today <= p.get("date", "") <= last
                     and not (p.get("date") == today and (p.get("time") or "99:99") <= kicked_off))
                and (not leagues or p.get("league") in leagues)]
    # A club the model has (next to) no matches for: its chances are mostly
    # guesswork, so the match stays out (the page lists what was left out)
    # A league whose check hasn't passed (league_unproven) is left out the same way
    # So is a league the weekly drift check paused (or the admin switched off)
    paused_lg = main._paused_leagues()
    out_lg = lambda p: str(p.get("league") or "") in paused_lg
    thin = [{**{k: p.get(k) for k in ("home", "away", "date", "time", "league", "league_name", "flag")},
             **({"league_unproven": True} if p.get("league_unproven") and not p.get("thin_history") else {}),
             **({"league_paused": True} if out_lg(p) else {})}
            for p in upcoming if p.get("thin_history") or p.get("league_unproven") or out_lg(p)]
    upcoming = [p for p in upcoming if not (p.get("thin_history") or p.get("league_unproven") or out_lg(p))]
    # Bookable = linked to a SportyBet event. Asked of the stored links, not
    # the prediction's "sportybet" flag: a predictions rebuild replaces the
    # predictions (flags and all) minutes before the next linking run.
    linked = {id(p): main._linked_event(p) for p in upcoming}
    preds = [p for p in upcoming if not bookable_only or linked[id(p)]]
    bb_preds = [] if "basketball" not in on else [
        p for p in main._bb_upcoming()
        if (in_window(p) if window else today <= p["date"] <= last) and (not leagues or p.get("league") in leagues)]
    if bookable_only and upcoming and not preds and sport == "football":
        return {"error": (f"SportyBet hasn't listed any of the {len(upcoming)} matches in the next {days} "
                          f"day{'s' if days != 1 else ''} yet (or we haven't linked them since the last restart). "
                          "Untick \"Only matches SportyBet lists\", or pick more days."),
                "matches_considered": 0, "target": [lo, hi], "target_odds": target, "thin_history": thin}
    # Bookable-only slips skip markets SportyBet hasn't confirmed yet (a code
    # couldn't take those picks)
    bookable = main._bookable_markets() if bookable_only else None
    # Markets the weekly accuracy review paused (or the admin switched off)
    paused = main._paused_markets()
    if markets and markets <= paused and sport == "football":
        import market_review
        return {"error": (f"{market_review.names(sorted(markets))} {'is' if len(markets) == 1 else 'are'} paused: "
                          "recent picks came in less often than we said, so we're not using "
                          f"{'it' if len(markets) == 1 else 'them'} until the accuracy check clears. Add other markets."),
                "paused": sorted(markets), "matches_considered": 0, "target": [lo, hi], "target_odds": target, "thin_history": thin}

    def allowed(market: str, code: str) -> bool:
        if market in paused:
            return False
        if market in only and code not in only[market]:
            return False  # a line the user left out
        return bookable is None or bookable(market, code)
    # Matches without corner/card stats (internationals) get SportyBet-implied lines
    pairs = [(main._with_priced_set_pieces(p, linked[id(p)]), linked[id(p)]) for p in preds]
    preds = [p for p, _ in pairs]
    groups = [optimizer.candidates(p, ev, min_prob, markets, allowed) for p, ev in pairs]
    if (not markets or "anytime_scorer" in markets) and not body.get("no_props"):
        # Anytime goalscorers priced on the match's SportyBet page (_fb_props_refresh)
        for g, (p, ev) in zip(groups, pairs):
            for x in main._props_fb.get(str((ev or {}).get("eventId") or ""), []):
                if min_prob <= x["prob"] < 0.995 and x["odds"] > 1.01 and "anytime_scorer" not in paused:
                    g.append(optimizer.Option(p["home"], p["away"], p["date"], p.get("time") or "",
                                              p.get("league_name") or "", "anytime_scorer", x["market_name"],
                                              x["code"], x["label"], x["prob"], x["odds"], "sportybet", sb=x["sb"],
                                              league_id=str(p.get("league") or "")))
    if bb_preds:
        import basketball_predictions
        # A market or its whole family switched off (the admin switches basketball off by family)
        bb_allowed = lambda m, c: m not in paused and basketball_predictions.family(m) not in paused
        groups += [basketball_predictions.options(p, min_prob, bb_families, bb_allowed) for p in bb_preds]
    rk_preds: List[Dict] = []
    if rk_on:
        import racket_predictions
        for rk in rk_on:
            ps = [p for p in main._rk_upcoming(rk)
                  if (in_window(p) if window else today <= p["date"] <= last)
                  and (not leagues or p.get("league") in leagues)]
            rk_preds += ps
            groups += [racket_predictions.options(p, min_prob, rk_families[rk], lambda m, c: m not in paused)
                       for p in ps]
    if max_leg:
        groups = [[o for o in g if o.odds < max_leg] for g in groups]
    # Each pick's chance pulled down to its market's real record, and markets
    # far under what the model said left out ("calibrate": False keeps the model's)
    codes = main._ranked_codes() if body.get("calibrate", True) is not False else None
    over_out: List[str] = []
    held_out = sorted({k for g in groups for o in g for k in [f"{o.sport}:{o.market}"] if k in HELD_OUT_MARKETS})
    if held_out:
        groups = [[o for o in g if f"{o.sport}:{o.market}" not in HELD_OUT_MARKETS] for g in groups]
    if codes:
        over_out = sorted({k for g in groups for o in g for k in [f"{o.sport}:{o.market}"]
                           if _overconfident(codes.get(k))})
        groups = [[c for o in g if f"{o.sport}:{o.market}" not in over_out
                   for c in [_calibrated(o, codes.get(f"{o.sport}:{o.market}"))] if c.prob >= min_prob]
                  for g in groups]
    # Markets that came in at least min_hit of the time in the nightly ranking
    # first; the ones under it only as a fallback (below). A market without
    # enough settled picks to be ranked is left out
    ranked = main._ranked_codes() if min_hit else None
    hit_out: List[str] = []

    def rate(o) -> float:
        return ranked[f"{o.sport}:{o.market}"]["hit_rate"]
    if ranked is not None:
        def low(o) -> bool:
            x = ranked.get(f"{o.sport}:{o.market}")
            return not x or x["hit_rate"] < min_hit
        had = sum(1 for g in groups if g)
        hit_out = sorted({f"{o.sport}:{o.market}" for g in groups for o in g if low(o)})
        groups = [[o for o in g if f"{o.sport}:{o.market}" in ranked and (hit_fallback or not low(o))]
                  for g in groups]
        if had and not any(groups):
            _log_left_out({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "source": body.get("_source") or "optimizer", "min_hit": min_hit,
                           "considered": 0, "left_out": hit_out, "floor": min_hit, "below_min": []})
            return {"error": (f"None of the picks on {had} matches is in a market that came in "
                              f"{min_hit:.0%} of the time or more over the last {main.RANKING_DAYS} days. "
                              "Try more days or sports." if not hit_fallback else
                              f"None of the picks on {had} matches is in a market with enough settled picks "
                              f"over the last {main.RANKING_DAYS} days to be ranked. Try more days or sports."),
                    "hit_rate_left_out": hit_out, "min_hit_rate": min_hit,
                    "matches_considered": 0, "target": [lo, hi], "target_odds": target, "thin_history": thin}
    weak_out: List[str] = []
    if healthy:
        weak = main._weak_cells()
        if weak:
            def bad(o) -> Optional[str]:
                return next((k for k in (f"{o.sport}:{o.market}", f"{o.sport}:{o.league_id}") if k in weak), None)
            weak_out = sorted({k for g in groups for o in g for k in [bad(o)] if k})
            groups = [[o for o in g if not bad(o)] for g in groups]
    # Matches already on another slip (the daily slips: no match in two categories)
    taken = {tuple(str(x) for x in m[:3]) for m in body.get("exclude_matches") or []
             if isinstance(m, (list, tuple)) and len(m) >= 3}
    if taken:
        groups = [[o for o in g if (o.home, o.away, o.date) not in taken] for g in groups]
    if bookable_only:
        # Shots are on some matches and lines only: bookable when SportyBet priced that very line
        groups = [[o for o in g if o.market not in booking_slip.LISTED_ONLY or o.odds_source == "sportybet"]
                  for g in groups]
    considered = sum(1 for g in groups if g)
    if considered == 0 and sport == "basketball":
        return {"error": (f"None of the {len(bb_preds)} basketball matches in the next {days} day{'s' if days != 1 else ''} "
                          + (f"has a line our model rates {min_prob:.0%} or more" if min_prob else "has a priced line")
                          + (" in the markets you chose" if bb_families else "")
                          + ". Add markets or pick more days."
                          if bb_preds else "No basketball matches listed on SportyBet in those days yet."),
                "matches_considered": 0, "target": [lo, hi], "target_odds": target, "thin_history": thin}
    if considered == 0 and sport in main.RK_SPORTS:
        name = "tennis" if sport == "tennis" else "table tennis"
        return {"error": (f"None of the {len(rk_preds)} {name} matches in the next {days} day{'s' if days != 1 else ''} "
                          + (f"has a line our model rates {min_prob:.0%} or more" if min_prob else "has a priced line")
                          + (" in the markets you chose" if rk_families[sport] else "")
                          + ". Add markets or pick more days."
                          if rk_preds else f"No {name} matches listed on SportyBet in those days yet."),
                "matches_considered": 0, "target": [lo, hi], "target_odds": target, "thin_history": thin}
    if considered == 0:
        reasons = optimizer.why_empty(preds, markets, min_prob, bookable, only)
        return {"error": main._explain_empty(reasons, len(preds), days, min_prob), "reasons": reasons,
                "matches_considered": 0, "target": [lo, hi], "target_odds": target, "thin_history": thin}
    # Every pick checked on SportyBet's current match page (_solve_checked)
    events = {(p["home"], p["away"], p.get("date")): ev for p, ev in pairs}
    # Markets at min_hit+ first. Only when no slip from them reaches the
    # customer's odds does the next one down the ranking come in, a step at a time
    floors = _hit_floors([rate(o) for g in groups for o in g], min_hit) if ranked is not None else [0.0]
    result, live_check, floor, pool_used = None, {}, floors[0], groups
    for f in floors:
        pool = [[o for o in g if ranked is None or rate(o) >= f] for g in groups]
        if not any(pool):
            continue
        if body.get("no_live_check"):
            got = await main._solve(pool, lo, hi, target, max_games, min_chance)
            checked: Dict[str, Any] = {"checked": 0, "removed": [], "repriced": 0, "unchecked": 0}
        else:
            got, checked = await main._solve_checked(pool, lo, hi, target, max_games, events,
                                                     main._sb_market_map(), min_chance)
        if result is None or got is not None:
            live_check = checked
        if got is not None and (result is None or got.get("within_target", True)):
            result, floor, pool_used = got, f, pool
        if result is not None and (min_chance or result.get("within_target", True)):
            break
    groups = pool_used
    if not live_check:
        live_check = {"checked": 0, "removed": [], "repriced": 0, "unchecked": 0}
    below_min = sorted({f"{p.get('sport') or 'football'}:{p.get('market')}" for p in (result or {}).get("picks") or []
                        if ranked is not None and (ranked.get(f"{p.get('sport') or 'football'}:{p.get('market')}")
                                                   or {}).get("hit_rate", 1) < min_hit})
    if ranked is not None:
        _log_left_out({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "source": body.get("_source") or "optimizer", "min_hit": min_hit, "considered": considered,
                       "left_out": hit_out, "floor": floor, "below_min": below_min,
                       **({"overconfident": over_out} if over_out else {})})
    if result is None and live_check["removed"]:
        return {"error": (f"{len(live_check['removed'])} of the picks were suspended on SportyBet and "
                          "no slip without them reaches the target. Try again in a few minutes, or widen the target."),
                "live_check": live_check, "matches_considered": considered, "target": [lo, hi], "target_odds": target, "thin_history": thin}
    if result is None and min_chance:
        return {"error": (f"No pick among {considered} matches is {min_chance:.0%} likely on its own, so no slip "
                          f"can be. Add markets, sports or days, or lower the slip's chance."),
                "matches_considered": considered, "target": [lo, hi], "target_odds": target, "thin_history": thin}
    if result is not None and min_win and not min_chance and (result.get("win_chance") or 0) < min_win:
        return {"error": (f"No {target:,.0f}x slip from {considered} matches is at least {min_win:.0%} likely "
                          f"(break-even for its odds): the best lands {result.get('win_chance', 0):.1%} of the time. "
                          "Try a lower target, or more days, markets or sports."),
                "best_win_chance": result.get("win_chance"), "min_win_chance": min_win,
                "matches_considered": considered, "target": [lo, hi], "target_odds": target, "thin_history": thin}
    if result is None:
        capped = (f" High-confidence slips stop at {max_games} legs (you asked for {asked}): lower the target, "
                  "or untick the cap." if max_games < asked else "")
        return {"error": (f"No slip from {considered} matches gets near {target:,.2f}x. "
                          "Allow more games or days, or add markets." + capped),
                "matches_considered": considered, "target": [lo, hi], "target_odds": target, "thin_history": thin}
    # Which picks a SportyBet code can take: the match is linked to a SportyBet
    # event and SportyBet confirmed the market (shots, for one, it doesn't offer)
    ok = bookable or main._bookable_markets()
    result["live_check"] = live_check
    for pick in result.get("picks") or []:
        if pick.get("sb"):   # basketball: a line SportyBet offers, with its ids
            pick["bookable"] = True
            continue
        pick["bookable"] = (bool(events.get((pick["home"], pick["away"], pick["date"]))) and ok(pick["market"], pick["code"])
                            and (pick["market"] not in booking_slip.LISTED_ONLY or pick["odds_source"] == "sportybet"))
    result["bookable_picks"] = sum(1 for pick in result.get("picks") or [] if pick["bookable"])
    if weak_out:
        result["weak_left_out"] = weak_out
    if over_out:
        result["overconfident_left_out"] = over_out
    if held_out:
        result["held_out"] = held_out
    if ranked is not None:
        result["min_hit_rate"] = min_hit
        result["hit_rate_left_out"] = hit_out
        result["fallback_used"] = bool(below_min)
        result["fallback_floor"] = floor
        result["below_min"] = below_min
        for pick in result.get("picks") or []:
            _market_hit(pick, ranked, min_hit)
    if max_games < asked:
        # The rest of a long ask: more slips of ≤ HIGH_CONF_LEGS from the matches left
        result["leg_cap"] = {"cap": max_games, "asked": asked, "note": main._leg_note(asked, max_games)}
        used = {(p["home"], p["away"], p["date"]) for p in result.get("picks") or []}
        extra: List[Dict[str, Any]] = []
        for _ in range(-(-asked // max_games) - 1):
            rest = [[o for o in g if (o.home, o.away, o.date) not in used] for g in groups]
            more = await main._solve(rest, lo, hi, target, max_games, min_chance) if any(rest) else None
            if not more or not more.get("picks"):
                break
            for pick in more["picks"]:
                if ranked is not None:
                    _market_hit(pick, ranked, min_hit)
                pick["bookable"] = (pick.get("sb") is not None or bool(events.get((pick["home"], pick["away"], pick["date"])))
                                    and ok(pick["market"], pick["code"])
                                    and (pick["market"] not in booking_slip.LISTED_ONLY or pick["odds_source"] == "sportybet"))
            used |= {(p["home"], p["away"], p["date"]) for p in more["picks"]}
            extra.append(more)
        result["extra_slips"] = extra
    return {**result, "target": [lo, hi], "target_odds": round(target, 2), "matches_considered": considered, "thin_history": thin}


def _prediction_for_leg(sel: Dict[str, Any], by_event: Dict[str, Dict]) -> Optional[Dict]:
    """Our prediction for a booking-code leg: by the SportyBet event we
    linked it to, else by team names within a day of kick-off; tennis and
    table tennis from their cached lists."""
    import sportybet
    if sel["eventId"] in by_event:
        return by_event[sel["eventId"]]
    for sport in ("tennis", "table-tennis"):
        for p in main._cached_sport_predictions(sport):
            if str(p.get("sportybet_event_id")) == sel["eventId"]:
                return p
    try:
        day = datetime.fromtimestamp(int(sel["start"]) / 1000, timezone.utc).date()
    except (TypeError, ValueError, KeyError):
        return None
    near = {(day + timedelta(days=o)).isoformat() for o in (-1, 0, 1)}
    best, score = None, 0.0
    for p in main._predictions_cache:
        if p.get("date") in near and p.get("sport") in (None, "football"):
            s = min(sportybet.team_similarity(p["home"], sel["home"]), sportybet.team_similarity(p["away"], sel["away"]))
            if s >= 0.8 and s > score:
                best, score = p, s
    return best


def _cached_sport_predictions(sport: str) -> List[Dict]:
    r = main._get_redis()
    if r:
        try:
            return json.loads(r.get(f"betiq:sports:{sport}") or "[]")
        except Exception:
            return []
    return (main._sports_memory_cache.get(sport) or ([],))[0]


@router.post("/api/optimizer/code")
async def optimize_code(body: Dict[str, Any], _access=Depends(main.require_feature("code_check"))):
    """
    Check a SportyBet booking code with the model: each leg's chance, a
    better pick where there is one, and two improved slips (code_check.py).
    Body: {"code": "ABC123"}.
    """
    import code_check
    import sportybet
    code = str(body.get("code") or "").strip()
    try:
        selections = await sportybet.load_share_code(code)
    except sportybet.SportyBetError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        print(f"[CodeCheck] {code}: {e}")
        raise HTTPException(status_code=502, detail="Couldn't reach SportyBet to load that code. Try again in a minute.")
    if not main._sb_links:
        main._linked_event({})
    by_key = {main._sb_key(p.get("home", ""), p.get("away", ""), p.get("date", "")): p for p in main._predictions_cache}
    by_event = {str(ev.get("eventId")): by_key[k] for k, ev in main._sb_links.items() if k in by_key}
    confirmed = {k: str(v["id"]) for k, v in main._sb_market_map().items() if (v or {}).get("ok")}
    def find(sel: Dict) -> Optional[Dict]:
        p = main._prediction_for_leg(sel, by_event)
        return main._with_priced_set_pieces(p, main._linked_event(p)) if p else None

    bookable, paused = main._bookable_markets(), main._paused_markets()
    report = await asyncio.to_thread(
        code_check.analyse, selections, find,
        main._linked_event, confirmed, lambda m, c: m not in paused and bookable(m, c))
    return {"code": code.upper(), **report}


@router.post("/api/booking/convert")
async def convert_slip(request: Request, body: Dict[str, Any]):
    """
    Turn the bet slip into a booking code on the chosen platform.
    Body: {"platform": "sportybet" | "football_com", "selections": [{home, away, date, market, code, label?}]}
    """
    import booking_slip
    import sportybet

    platform = body.get("platform", "sportybet")
    if platform not in sportybet.PLATFORMS:
        raise HTTPException(status_code=400, detail="Booking codes are made on SportyBet or football.com")
    try:
        selections = booking_slip.validate(body.get("selections"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # football.com is SportyBet's platform: the same events and ids, its own code
    result = booking_slip.name_platform(await booking_slip.to_sportybet(
        selections, sportybet.fetch_events_for_date, sportybet.find_event, sportybet.share_on(platform),
        linked=main._linked_event, market_map=main._sb_market_map(), platform=platform))
    # Every code a signed-in account makes becomes a ticket, settled leg by
    # leg as the results come in (Dashboard → Tickets)
    if result.get("code"):
        import auth
        import tickets
        uid = await auth.optional_user(request)
        if not uid and auth.unverified_uid_allowed():
            uid = str(body.get("uid") or "")[:64] or None
        # Signed out: kept too, so the admin sees every code the site makes
        source = body.get("source") if body.get("source") in main.TICKET_SOURCES else "other"
        try:
            ticket = tickets.new_ticket(
                result["code"], selections, result.get("picks") or [], source, result.get("share_url"),
                result.get("total_odds"), datetime.now(timezone.utc).isoformat(timespec="seconds"))
            ticket["platform"] = platform
            main._record_ticket(uid or main.ANON_UID, ticket)
            if uid:
                result["tracked"] = True
        except Exception as e:
            print(f"[Tickets] Couldn't record {result['code']}: {e}")
    return result
