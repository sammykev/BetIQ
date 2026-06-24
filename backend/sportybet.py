"""
SportyBet integration — reverse-engineered internal web API.
Creates shareable booking codes from a list of predicted fixtures.

Endpoint base: https://www.sportybet.com/api/ng/
All requests are unauthenticated (booking codes are public/shareable by design).
"""

import asyncio
import httpx
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Any

BASE = "https://www.sportybet.com/api/ng"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.sportybet.com",
    "Referer": "https://www.sportybet.com/ng/sport/football",
}

MARKET_1X2 = "1_18"

# Normalise team name before fuzzy comparison
_REPLACE = [
    (" FC", ""), (" AFC", ""), (" SC", ""), (" CF", ""),
    ("Manchester", "Man"), ("United", "Utd"), ("Borussia", ""),
    ("Internazionale", "Inter"), ("Paris Saint-Germain", "PSG"),
    ("Atletico", "Atlético"), (" City", " City"),
]

def _norm(name: str) -> str:
    n = name.strip()
    for old, new in _REPLACE:
        n = n.replace(old, new)
    return n.lower().strip()

def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


# ------------------------------------------------------------------ #
# Fetch SportyBet matches for a calendar date
# ------------------------------------------------------------------ #

async def _get(client: httpx.AsyncClient, url: str) -> Optional[Any]:
    try:
        r = await client.get(url, headers=_HEADERS, timeout=20, follow_redirects=True)
        body = r.text.strip()
        print(f"[SportyBet] GET {r.status_code} len={len(body)} body={body[:200]!r}")
        if r.status_code in (200, 202) and body:
            try:
                return r.json()
            except Exception as je:
                print(f"[SportyBet] JSON parse error: {je} — body={body[:200]!r}")
    except Exception as e:
        print(f"[SportyBet] GET error: {e}")
    return None


TOURNAMENT_IDS = [
    # Club leagues
    "sr:tournament:17",   # Premier League
    "sr:tournament:23",   # Serie A
    "sr:tournament:35",   # Bundesliga
    "sr:tournament:8",    # La Liga
    "sr:tournament:34",   # Ligue 1
    "sr:tournament:7",    # Champions League
    "sr:tournament:679",  # Europa League
    "sr:tournament:238",  # Primeira Liga
    "sr:tournament:37",   # Eredivisie
    "sr:tournament:44",   # Bundesliga 2
    # International competitions
    "sr:tournament:1091", # UEFA Nations League A
    "sr:tournament:1090", # UEFA Nations League B
    "sr:tournament:133",  # Copa America
    "sr:tournament:1049", # AFCON
    "sr:tournament:42",   # FIFA World Cup Qualifiers Africa
    "sr:tournament:143",  # FIFA World Cup Qualifiers Europe
    "sr:tournament:203",  # Algerian Ligue Pro
    "sr:tournament:68",   # Africa Cup of Nations Qualifiers
    "sr:tournament:191",  # International Friendlies
]


async def fetch_events_for_date(date_str: str) -> List[Dict]:
    """
    Fetch all football events from SportyBet for a given date (YYYY-MM-DD).
    Uses pcEvents (POST) which is what their website actually calls, then falls
    back to getScheduled GET endpoints.
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    start_ms = int(dt.replace(hour=0, minute=0, second=0).timestamp() * 1000)
    end_ms   = int(dt.replace(hour=23, minute=59, second=59).timestamp() * 1000)
    ts = start_ms

    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:

        # ── Strategy 1: getScheduled GET — date-based, covers ALL tournaments ──
        # This is the best approach for value bets since it's not tournament-specific
        for url in [
            f"{BASE}/factsCenter/getScheduled?sportId=sr%3Asport%3A1&startTime={start_ms}&endTime={end_ms}&marketId=1&page=1&pageSize=500&_t={ts}",
            f"{BASE}/factsCenter/getScheduled?sportId=sr%3Asport%3A1&startTime={start_ms}&endTime={end_ms}&marketId={MARKET_1X2}&page=1&pageSize=500&_t={ts}",
            f"{BASE}/factsCenter/getScheduled?sportId=sr%3Asport%3A1&startTime={start_ms}&endTime={end_ms}&page=1&pageSize=500&_t={ts}",
        ]:
            data = await _get(client, url)
            if not data:
                continue
            inner = data.get("data") or data
            events = (
                inner.get("events") or inner.get("matches") or
                inner.get("items") or
                (inner if isinstance(inner, list) else [])
            )
            if events:
                print(f"[SportyBet] getScheduled: {len(events)} events for {date_str}")
                return events

        # ── Strategy 2: pcEvents POST per tournament — server ignores date params,
        # so collect ALL events then filter client-side by estimateStartTime
        all_evs: List[Dict] = []
        for tid in TOURNAMENT_IDS:
            try:
                r = await client.post(f"{BASE}/factsCenter/pcEvents", json={
                    "tournamentId": tid,
                    "sportId": "sr:sport:1",
                    "marketId": "1",
                }, headers={**_HEADERS, "Content-Type": "application/json"})
                if r.status_code == 200:
                    data = r.json()
                    if data.get("bizCode") == 10000:
                        for t in (data.get("data") or []):
                            for ev in (t.get("events") or []):
                                ev_ts = ev.get("estimateStartTime", 0)
                                if start_ms <= int(ev_ts) <= end_ms:
                                    all_evs.append(ev)
            except Exception as e:
                print(f"[SportyBet] pcEvents {tid}: {e}")
            await asyncio.sleep(0.1)

        if all_evs:
            print(f"[SportyBet] pcEvents: {len(all_evs)} events for {date_str}")
            return all_evs

    print(f"[SportyBet] No events found for {date_str}")
    return []


# ------------------------------------------------------------------ #
# Match our fixture names to a SportyBet event
# ------------------------------------------------------------------ #

def _event_teams(ev: Dict):
    home = (
        ev.get("homeTeamName") or
        ev.get("home", {}).get("name") or
        ev.get("teams", {}).get("home", {}).get("name") or ""
    )
    away = (
        ev.get("awayTeamName") or
        ev.get("away", {}).get("name") or
        ev.get("teams", {}).get("away", {}).get("name") or ""
    )
    return home, away


def find_event(home: str, away: str, events: List[Dict]) -> Optional[Dict]:
    best, best_score = None, 0.0
    for ev in events:
        sb_home, sb_away = _event_teams(ev)
        if not sb_home or not sb_away:
            continue
        score = (_sim(home, sb_home) + _sim(away, sb_away)) / 2
        if score > best_score:
            best_score = score
            best = ev
    return best if best_score >= 0.55 else None


# ------------------------------------------------------------------ #
# Build a selection dict from a matched event
# ------------------------------------------------------------------ #

def build_selection(event: Dict, tip_code: str) -> Optional[Dict]:
    """
    Extract market/outcome IDs from a SportyBet event dict and build a
    selection payload item compatible with their /orders/share endpoint.
    tip_code: "1" (home), "X" (draw), "2" (away)
    """
    try:
        match_id = (
            event.get("eventId") or event.get("matchId") or
            event.get("id") or event.get("matchInfo", {}).get("id")
        )

        # Find the 1X2 market in the event's market list
        markets = (
            event.get("markets") or event.get("betOptions") or
            event.get("marketList") or []
        )
        market = None
        for m in markets:
            mid = str(m.get("id", "") or m.get("marketId", ""))
            mname = (m.get("name") or m.get("marketName") or "").lower()
            if mid == MARKET_1X2 or "1x2" in mname or "match result" in mname:
                market = m
                break

        if not market and markets:
            market = markets[0]     # take first market as fallback

        if not market:
            print(f"[SportyBet] No 1X2 market found for event {match_id}")
            return None

        market_id = str(market.get("id") or market.get("marketId") or MARKET_1X2)
        outcomes = market.get("outcomes") or market.get("options") or market.get("selections") or []

        # Map tip_code → expected outcome name
        name_map = {"1": ["home", "1", "win"], "X": ["draw", "x", "tie"], "2": ["away", "2"]}
        target_names = name_map.get(tip_code, [])

        outcome = None
        for o in outcomes:
            oname = (o.get("name") or o.get("outcomeName") or "").lower().strip()
            if any(t in oname for t in target_names) or oname == tip_code.lower():
                outcome = o
                break

        if not outcome and outcomes:
            idx = {"1": 0, "X": 1, "2": 2}.get(tip_code, 0)
            outcome = outcomes[min(idx, len(outcomes) - 1)]

        if not outcome:
            return None

        outcome_id = str(outcome.get("id") or outcome.get("outcomeId") or "")
        odds = str(outcome.get("odds") or outcome.get("value") or outcome.get("price") or "1.00")

        sb_home, sb_away = _event_teams(event)

        return {
            "matchId":     str(match_id),
            "marketId":    market_id,
            "outcomeId":   outcome_id,
            "specifiers":  market.get("specifiers") or "",
            "marketName":  market.get("name") or "1X2",
            "outcomeName": outcome.get("name") or tip_code,
            "homeTeamName": sb_home,
            "awayTeamName": sb_away,
            "odds":         odds,
            "status":       0,
        }
    except Exception as e:
        print(f"[SportyBet] build_selection error: {e}")
        return None


# ------------------------------------------------------------------ #
# POST booking code
# ------------------------------------------------------------------ #

async def post_booking(selections: List[Dict]) -> Optional[str]:
    """POST selections to SportyBet and return the booking code string."""
    url = f"{BASE}/orders/share"
    payload = {"betType": "1", "betList": selections}

    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            r = await client.post(url, json=payload, headers={
                **_HEADERS,
                "Content-Type": "application/json",
            })
            print(f"[SportyBet] POST /orders/share → {r.status_code}: {r.text[:300]}")
            if r.status_code == 200:
                data = r.json()
                inner = data.get("data") or data
                return (
                    inner.get("bookingCode") or
                    inner.get("code") or
                    inner.get("shareCode") or
                    inner.get("betCode")
                )
    except Exception as e:
        print(f"[SportyBet] post_booking error: {e}")
    return None


# ------------------------------------------------------------------ #
# Public entry point
# ------------------------------------------------------------------ #

async def generate_booking_code(predictions: List[Dict]) -> Dict:
    """
    Given a list of BetIQ prediction dicts, match them to SportyBet events
    and generate a booking code.

    Returns:
        {
            code: str | None,
            matched: [{game, tip, odds}],
            unmatched: [str],
            total_odds: float | None,
            error: str | None,
        }
    """
    matched_games = []
    unmatched_games = []
    selections = []
    total_odds = 1.0

    # Fetch SportyBet events per unique date (batched)
    dates = list({p["date"] for p in predictions})
    sb_by_date: Dict[str, List] = {}

    async with httpx.AsyncClient() as _:       # warm connection pool context
        for d in dates:
            sb_by_date[d] = await fetch_events_for_date(d)
            await asyncio.sleep(0.4)

    for pred in predictions:
        tip_code = pred.get("tip_code", "?")

        # Only book clean 1X2 tips
        if tip_code not in ("1", "X", "2"):
            unmatched_games.append(
                f"{pred['home']} vs {pred['away']} (tip '{tip_code}' not bookable as 1X2)"
            )
            continue

        events = sb_by_date.get(pred["date"], [])
        event = find_event(pred["home"], pred["away"], events)

        if not event:
            unmatched_games.append(f"{pred['home']} vs {pred['away']} (not found on SportyBet)")
            continue

        sel = build_selection(event, tip_code)
        if not sel:
            unmatched_games.append(f"{pred['home']} vs {pred['away']} (market extraction failed)")
            continue

        selections.append(sel)
        matched_games.append({
            "game": f"{sel['homeTeamName']} vs {sel['awayTeamName']}",
            "tip":  sel["outcomeName"],
            "odds": sel["odds"],
        })
        try:
            total_odds *= float(sel["odds"])
        except Exception:
            pass

    if not selections:
        return {
            "code": None,
            "matched": matched_games,
            "unmatched": unmatched_games,
            "total_odds": None,
            "error": "None of the selected games were found on SportyBet for today.",
        }

    code = await post_booking(selections)
    return {
        "code": code,
        "matched": matched_games,
        "unmatched": unmatched_games,
        "total_odds": round(total_odds, 2) if matched_games else None,
        "error": None if code else "SportyBet returned no booking code — their API may have changed.",
    }
