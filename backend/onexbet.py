"""
1xBet Nigeria integration — reverse-engineered internal API.
Attempts to create a shareable booking code from BetIQ predictions.

Endpoints are unofficial and may change. All errors are caught and surfaced
as None so the caller can fall through to the copy-card fallback.
"""

import asyncio
import httpx
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Any

BASE = "https://1xbet.ng"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": BASE,
    "Referer": f"{BASE}/en/sport/football",
    "X-Requested-With": "XMLHttpRequest",
}

_REPLACE = [
    (" FC", ""), (" AFC", ""), (" SC", ""), (" CF", ""),
    ("Manchester", "Man"), ("United", "Utd"), ("Borussia", ""),
    ("Internazionale", "Inter"), ("Paris Saint-Germain", "PSG"),
    ("Athletic Club", "Athletic Bilbao"),
]

def _norm(name: str) -> str:
    n = name.strip()
    for old, new in _REPLACE:
        n = n.replace(old, new)
    return n.lower().strip()

def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


async def _get(client: httpx.AsyncClient, url: str) -> Optional[Any]:
    try:
        r = await client.get(url, headers=_HEADERS, timeout=20, follow_redirects=True)
        if r.status_code == 200:
            return r.json()
        print(f"[1xBet] GET {r.status_code}: {url[:120]}")
    except Exception as e:
        print(f"[1xBet] GET error: {e}")
    return None


async def fetch_events_for_date(date_str: str) -> List[Dict]:
    """Fetch football events from 1xBet for a given date."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    start_ts = int(dt.replace(hour=0, minute=0, second=0).timestamp())
    end_ts   = int(dt.replace(hour=23, minute=59, second=59).timestamp())

    endpoints = [
        # LineFeed — most common internal API
        f"{BASE}/LineFeed/Get1x2_ByInterval?sportId=1&startDate={start_ts}&stopDate={end_ts}&count=200&lng=en&tf=10800&tz=0",
        # Newer API style
        f"{BASE}/en/api/getFutureMatchEvents?sport=1&dateFrom={date_str}&dateTo={date_str}&count=200",
        # Alternative linefeed endpoint
        f"{BASE}/LineFeed/GetChampEvents?sportId=1&count=200&tf={(end_ts - start_ts)}&tz=0&mode=4&country=1&getEmpty=true&gr=48",
    ]

    async with httpx.AsyncClient() as client:
        for url in endpoints:
            data = await _get(client, url)
            if not data:
                continue
            # 1xBet LineFeed wraps events in "Value" key
            events = (
                data.get("Value") or
                data.get("value") or
                data.get("events") or
                data.get("data") or
                (data if isinstance(data, list) else [])
            )
            if isinstance(events, list) and events:
                print(f"[1xBet] {len(events)} events for {date_str}")
                return events

    print(f"[1xBet] No events found for {date_str}")
    return []


def _event_teams(ev: Dict):
    """Extract home/away team names from a 1xBet event dict."""
    # LineFeed format uses O1/O2 for team names
    home = (
        ev.get("O1") or ev.get("homeTeam") or
        ev.get("home", {}).get("name") or
        ev.get("team1") or ""
    )
    away = (
        ev.get("O2") or ev.get("awayTeam") or
        ev.get("away", {}).get("name") or
        ev.get("team2") or ""
    )
    return str(home), str(away)


def find_event(home: str, away: str, events: List[Dict]) -> Optional[Dict]:
    best, best_score = None, 0.0
    for ev in events:
        h, a = _event_teams(ev)
        if not h or not a:
            continue
        score = (_sim(home, h) + _sim(away, a)) / 2
        if score > best_score:
            best_score = score
            best = ev
    return best if best_score >= 0.52 else None


def _extract_1x2_selection(event: Dict, tip_code: str) -> Optional[Dict]:
    """
    Pull the 1X2 market from a 1xBet LineFeed event and build a selection.
    LineFeed events nest odds inside event["E"] list; type 1 = 1X2.
    """
    try:
        event_id = str(event.get("I") or event.get("id") or event.get("eventId") or "")

        # LineFeed: E = list of market groups, each has T (type) and ME (outcomes)
        markets = event.get("E") or event.get("markets") or event.get("betOptions") or []

        market_1x2 = None
        for m in markets:
            mtype = str(m.get("T") or m.get("type") or m.get("marketType") or "")
            mname = (m.get("N") or m.get("name") or "").lower()
            if mtype == "1" or "1x2" in mname or "match result" in mname or "match winner" in mname:
                market_1x2 = m
                break

        if not market_1x2 and markets:
            market_1x2 = markets[0]

        if not market_1x2:
            return None

        market_id = str(market_1x2.get("T") or market_1x2.get("id") or "1")
        outcomes = (
            market_1x2.get("ME") or market_1x2.get("outcomes") or
            market_1x2.get("options") or []
        )

        # In 1xBet LineFeed, outcomes are: index 0=home, 1=draw, 2=away
        # Each outcome has C (coefficient/odds) and T (type: 1=home,2=draw,3=away)
        target_type = {"1": "1", "X": "2", "2": "3"}[tip_code]
        name_map = {"1": ["1", "home", "win", "w1"], "X": ["x", "draw", "2", "tie"], "2": ["2", "away", "win", "w2"]}

        outcome = None
        for o in outcomes:
            otype = str(o.get("T") or o.get("type") or o.get("id") or "")
            oname = (o.get("N") or o.get("name") or "").lower()
            if otype == target_type or any(t == oname for t in name_map[tip_code]):
                outcome = o
                break

        if not outcome and outcomes:
            idx = {"1": 0, "X": 1, "2": 2}.get(tip_code, 0)
            outcome = outcomes[min(idx, len(outcomes) - 1)]

        if not outcome:
            return None

        odds = str(outcome.get("C") or outcome.get("coefficient") or outcome.get("odds") or "1.00")
        outcome_id = str(outcome.get("I") or outcome.get("id") or outcome.get("T") or "")

        h, a = _event_teams(event)
        return {
            "eventId": event_id,
            "marketId": market_id,
            "outcomeId": outcome_id,
            "homeTeam": h,
            "awayTeam": a,
            "odds": odds,
            "tip": tip_code,
        }
    except Exception as e:
        print(f"[1xBet] extract error: {e}")
        return None


async def post_booking(selections: List[Dict]) -> Optional[str]:
    """POST selections to 1xBet and return a booking/coupon code."""
    # Multiple endpoint patterns to try
    payloads_and_urls = [
        (
            f"{BASE}/en/api/bets/booking",
            {"bets": [{"gameId": s["eventId"], "coefficient": float(s["odds"]), "gameType": 1} for s in selections]},
        ),
        (
            f"{BASE}/en/api/betslip/share",
            {"selections": selections},
        ),
        (
            f"{BASE}/LineFeed/Coupon",
            {"Coupon": [{"GameId": s["eventId"], "Coef": s["odds"], "GroupId": s["marketId"], "TypeId": s["outcomeId"]} for s in selections]},
        ),
    ]

    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        for url, payload in payloads_and_urls:
            try:
                r = await client.post(url, json=payload, headers={
                    **_HEADERS, "Content-Type": "application/json",
                })
                print(f"[1xBet] POST {url.split('/')[-1]} → {r.status_code}: {r.text[:200]}")
                if r.status_code == 200:
                    data = r.json()
                    code = (
                        data.get("bookingCode") or data.get("couponCode") or
                        data.get("code") or data.get("Value") or
                        (data.get("data") or {}).get("code")
                    )
                    if code:
                        return str(code)
            except Exception as e:
                print(f"[1xBet] POST error {url}: {e}")
                continue

    return None


async def generate_booking_code(predictions: List[Dict]) -> Dict:
    """Try to generate a 1xBet booking code from BetIQ predictions."""
    matched_games, unmatched_games, selections = [], [], []
    total_odds = 1.0

    dates = list({p["date"] for p in predictions})
    events_by_date: Dict[str, List] = {}
    for d in dates:
        events_by_date[d] = await fetch_events_for_date(d)
        await asyncio.sleep(0.3)

    for pred in predictions:
        tip_code = pred.get("tip_code", "?")
        if tip_code not in ("1", "X", "2"):
            unmatched_games.append(f"{pred['home']} vs {pred['away']} (non-1X2 tip)")
            continue

        events = events_by_date.get(pred["date"], [])
        event = find_event(pred["home"], pred["away"], events)
        if not event:
            unmatched_games.append(f"{pred['home']} vs {pred['away']} (not found on 1xBet)")
            continue

        sel = _extract_1x2_selection(event, tip_code)
        if not sel:
            unmatched_games.append(f"{pred['home']} vs {pred['away']} (market extraction failed)")
            continue

        selections.append(sel)
        matched_games.append({"game": f"{sel['homeTeam']} vs {sel['awayTeam']}", "tip": tip_code, "odds": sel["odds"]})
        try:
            total_odds *= float(sel["odds"])
        except Exception:
            pass

    if not selections:
        return {"code": None, "bookie": "1xbet", "matched": matched_games, "unmatched": unmatched_games, "total_odds": None}

    code = await post_booking(selections)
    return {
        "code": code,
        "bookie": "1xbet",
        "matched": matched_games,
        "unmatched": unmatched_games,
        "total_odds": round(total_odds, 2) if matched_games else None,
    }
