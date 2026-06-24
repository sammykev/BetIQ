"""
Live odds fetcher for value bet detection.
Uses SportyBet's date-based getScheduled endpoint which returns ALL football
events for a given date (leagues + internationals), not filtered by tournament.
"""

import asyncio
import os
import httpx
from difflib import SequenceMatcher
from datetime import datetime, timezone
from typing import Dict, List, Optional

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

MIN_EDGE = 0.03
MATCH_THRESHOLD = 0.50


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _extract_1x2(event: Dict) -> Optional[Dict]:
    """
    Extract 1X2 odds from a SportyBet event.
    Confirmed structure: market.id=="1", outcome.desc=="Home"/"Draw"/"Away"
    """
    for m in (event.get("markets") or []):
        if not isinstance(m, dict):
            continue
        if str(m.get("id", "")) != "1":
            continue
        odds: Dict[str, float] = {}
        for o in (m.get("outcomes") or []):
            if not isinstance(o, dict) or not o.get("isActive", 1):
                continue
            label = (o.get("desc") or "").lower().strip()
            try:
                val = float(o.get("odds") or 0)
            except Exception:
                val = 0.0
            if val <= 1.0:
                continue
            if label == "home":
                odds["1"] = val
            elif label == "draw":
                odds["X"] = val
            elif label == "away":
                odds["2"] = val
        if len(odds) == 3:
            return odds
    return None


def _event_teams(ev: Dict):
    return (
        str(ev.get("homeTeamName") or "").strip(),
        str(ev.get("awayTeamName") or "").strip(),
    )


async def _fetch_by_date(date_str: str) -> List[Dict]:
    """
    Fetch ALL football events from SportyBet for a date using the
    getScheduled date-range endpoint — covers leagues + internationals.
    Falls back to the pcEvents per-popular-tournament approach.
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    start_ms = int(dt.replace(hour=0, minute=0, second=0).timestamp() * 1000)
    end_ms   = int(dt.replace(hour=23, minute=59, second=59).timestamp() * 1000)
    ts = int(datetime.now().timestamp() * 1000)

    urls = [
        # getScheduled with market 1 (1X2)
        f"{BASE}/factsCenter/getScheduled?sportId=sr%3Asport%3A1&startTime={start_ms}&endTime={end_ms}&marketId=1&page=1&pageSize=500&_t={ts}",
        # getScheduled with market 1_18 (legacy ID)
        f"{BASE}/factsCenter/getScheduled?sportId=sr%3Asport%3A1&startTime={start_ms}&endTime={end_ms}&marketId=1_18&page=1&pageSize=500&_t={ts}",
        # getAllScheduled — broader endpoint some regions expose
        f"{BASE}/factsCenter/getAllScheduled?sportId=sr%3Asport%3A1&startTime={start_ms}&endTime={end_ms}&marketId=1&pageSize=500&_t={ts}",
    ]

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        for url in urls:
            try:
                r = await client.get(url, headers=_HEADERS)
                if r.status_code not in (200, 202) or not r.text.strip():
                    print(f"[Odds] getScheduled {r.status_code} for {date_str}")
                    continue
                data = r.json()
                if data.get("bizCode") != 10000:
                    continue
                inner = data.get("data") or {}
                events = (
                    inner.get("events") or inner.get("matches") or
                    inner.get("items") or
                    (inner if isinstance(inner, list) else [])
                )
                if events:
                    print(f"[Odds] getScheduled: {len(events)} events for {date_str}")
                    return events
            except Exception as e:
                print(f"[Odds] getScheduled error for {date_str}: {e}")

    # ── Fallback: pcEvents POST for popular/active tournaments ──────────────
    # Useful in off-season when only international tournaments are active
    INTL_TOURNAMENTS = [
        "sr:tournament:42",    # World Cup 2026
        "sr:tournament:133",   # AFCON
        "sr:tournament:29",    # Copa America
        "sr:tournament:1091",  # UEFA Nations League A
        "sr:tournament:1092",  # UEFA Nations League B
        "sr:tournament:3",     # International Friendlies
        "sr:tournament:203",   # Algeria Ligue Pro
        "sr:tournament:205",   # Algerian Cup
        "sr:tournament:271",   # World Cup Qualifying - Africa
        "sr:tournament:272",   # World Cup Qualifying - Europe
        "sr:tournament:273",   # World Cup Qualifying - S. America
        "sr:tournament:17",    # EPL (keep for when in season)
        "sr:tournament:23",    # Serie A
        "sr:tournament:35",    # Bundesliga
        "sr:tournament:8",     # La Liga
        "sr:tournament:34",    # Ligue 1
        "sr:tournament:7",     # Champions League
    ]

    collected = []
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for tid in INTL_TOURNAMENTS[:8]:  # limit to avoid rate limiting
            try:
                r = await client.post(
                    f"{BASE}/factsCenter/pcEvents",
                    json={"tournamentId": tid, "sportId": "sr:sport:1", "marketId": "1",
                          "startTime": start_ms, "endTime": end_ms},
                    headers={**_HEADERS, "Content-Type": "application/json"},
                )
                if r.status_code == 200 and r.text.strip():
                    d = r.json()
                    if d.get("bizCode") == 10000:
                        for t in (d.get("data") or []):
                            collected.extend(t.get("events") or [])
            except Exception:
                pass
            await asyncio.sleep(0.15)

    if collected:
        print(f"[Odds] pcEvents fallback: {len(collected)} events for {date_str}")
    else:
        print(f"[Odds] No events found for {date_str} — SportyBet may be blocking server requests")

    return collected


async def fetch_odds_for_predictions(predictions: List[Dict]) -> Dict[str, Dict]:
    """
    Fetch live 1X2 odds from SportyBet for each unique prediction date.
    Returns dict keyed by "home:away:date" → {1, X, 2, source}.
    """
    if not predictions:
        return {}

    target_dates = {p.get("date", "") for p in predictions if p.get("date")}
    print(f"[Odds] Fetching odds for {len(target_dates)} dates: {sorted(target_dates)[:5]}")

    all_events: List[Dict] = []
    for d in sorted(target_dates):
        evs = await _fetch_by_date(d)
        matched = 0
        for ev in evs:
            home, away = _event_teams(ev)
            if not home or not away:
                continue
            odds = _extract_1x2(ev)
            if odds:
                all_events.append({
                    "home": home, "away": away, "date": d,
                    "odds": odds, "source": "sportybet",
                })
                matched += 1
        print(f"[Odds] {d}: {matched}/{len(evs)} events have 1X2 odds")
        await asyncio.sleep(0.5)

    print(f"[Odds] Total: {len(all_events)} events with 1X2 odds across {len(target_dates)} dates")

    # Fuzzy match to our predictions
    index: Dict[str, Dict] = {}
    for pred in predictions:
        pred_date = pred.get("date", "")
        best, best_score = None, 0.0
        for ev in all_events:
            if ev["date"] != pred_date:
                continue
            score = (_sim(pred["home"], ev["home"]) + _sim(pred["away"], ev["away"])) / 2
            if score > best_score:
                best_score = score
                best = ev
        if best and best_score >= MATCH_THRESHOLD:
            key = f"{pred['home']}:{pred['away']}:{pred['date']}"
            index[key] = {**best["odds"], "source": best["source"]}

    print(f"[Odds] Matched {len(index)}/{len(predictions)} predictions to live odds")
    return index


def compute_value_bets(predictions: List[Dict], odds_index: Dict[str, Dict]) -> List[Dict]:
    """
    Flag predictions where model probability beats implied probability by ≥ MIN_EDGE.
    """
    value_bets = []
    for pred in predictions:
        key = f"{pred['home']}:{pred['away']}:{pred['date']}"
        odds = odds_index.get(key)
        if not odds:
            continue

        o1 = float(odds.get("1") or 0)
        ox = float(odds.get("X") or 0)
        o2 = float(odds.get("2") or 0)
        if not (o1 > 1 and ox > 1 and o2 > 1):
            continue

        raw_imp = {"1": 1/o1, "X": 1/ox, "2": 1/o2}
        overround = sum(raw_imp.values())
        imp = {k: v / overround for k, v in raw_imp.items()}

        model = {
            "1": float(pred.get("p_home", 0)),
            "X": float(pred.get("p_draw", 0)),
            "2": float(pred.get("p_away", 0)),
        }

        best_code, best_edge, best_odds_val = None, 0.0, 0.0
        for code in ("1", "X", "2"):
            edge = model[code] - imp[code]
            if edge > best_edge:
                best_edge = edge
                best_code = code
                best_odds_val = {"1": o1, "X": ox, "2": o2}[code]

        if best_edge < MIN_EDGE or not best_code:
            continue

        label = {"1": f"{pred['home']} Win", "X": "Draw", "2": f"{pred['away']} Win"}[best_code]
        value_bets.append({
            **pred,
            "value_outcome": best_code,
            "value_label":   label,
            "value_odds":    round(best_odds_val, 2),
            "model_prob":    round(model[best_code] * 100, 1),
            "implied_prob":  round(imp[best_code] * 100, 1),
            "edge":          round(best_edge * 100, 1),
            "overround":     round((overround - 1) * 100, 1),
            "bookie":        odds.get("source", "sportybet"),
        })

    return sorted(value_bets, key=lambda x: x["edge"], reverse=True)
