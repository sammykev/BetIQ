"""
Multi-sport predictions using The Odds API.
Covers: Basketball (NBA, EuroLeague, NCAAB, NBL), Lawn Tennis (ATP/WTA), Table Tennis.

Requires: ODDS_API_KEY env var (free tier: 500 req/month).
Uses implied bookmaker odds as model probabilities (very accurate for these sports).
"""

import os
import asyncio
import httpx
from datetime import datetime, timezone
from typing import Dict, List, Optional

ODDS_API_KEY  = os.getenv("ODDS_API_KEY", "")
ODDS_BASE     = "https://api.the-odds-api.com/v4"

# ── Basketball leagues ─────────────────────────────────────────────────────
BASKETBALL_SPORTS = [
    ("basketball_nba",          "NBA",          "🏀"),
    ("basketball_euroleague",   "EuroLeague",   "🏀"),
    ("basketball_ncaab",        "NCAA Basketball","🏀"),
    ("basketball_nbl",          "NBL",          "🏀"),
    ("basketball_wnba",         "WNBA",         "🏀"),
    ("basketball_euroleague_women", "EuroLeague Women", "🏀"),
]

# ── Tennis: we discover active tournaments via /sports endpoint ────────────
TENNIS_KEYWORDS  = ["tennis_atp", "tennis_wtp", "tennis_itf"]
TABLE_TENNIS_KEY = "table_tennis"


async def _get_active_sports() -> List[str]:
    """Fetch all currently active sports from The Odds API."""
    if not ODDS_API_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{ODDS_BASE}/sports/",
                                 params={"apiKey": ODDS_API_KEY, "all": "false"})
            if r.status_code == 200:
                return [s["key"] for s in r.json()]
    except Exception as e:
        print(f"[Sports] active sports fetch error: {e}")
    return []


async def _fetch_odds(sport_key: str, markets: str = "h2h,totals") -> List[Dict]:
    """Fetch odds for a sport from The Odds API."""
    if not ODDS_API_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{ODDS_BASE}/sports/{sport_key}/odds/",
                params={
                    "apiKey":      ODDS_API_KEY,
                    "regions":     "eu",
                    "markets":     markets,
                    "oddsFormat":  "decimal",
                    "dateFormat":  "iso",
                },
            )
            remaining = r.headers.get("x-requests-remaining", "?")
            if r.status_code == 200:
                data = r.json()
                print(f"[Sports] {sport_key}: {len(data)} events (quota remaining={remaining})")
                return data
            elif r.status_code == 422:
                return []   # sport not currently active
            else:
                print(f"[Sports] {sport_key}: HTTP {r.status_code}")
    except Exception as e:
        print(f"[Sports] {sport_key} error: {e}")
    return []


def _best_odds(event: Dict, market_key: str = "h2h") -> Dict[str, float]:
    """Extract best (highest) decimal odds per outcome across all bookmakers."""
    best: Dict[str, float] = {}
    for bookie in (event.get("bookmakers") or []):
        for market in (bookie.get("markets") or []):
            if market.get("key") != market_key:
                continue
            for o in (market.get("outcomes") or []):
                name  = o.get("name", "")
                price = float(o.get("price") or 0)
                if price > 1:
                    best[name] = max(best.get(name, 0), price)
    return best


def _implied_probs(odds: Dict[str, float]) -> Dict[str, float]:
    """Convert decimal odds to overround-adjusted implied probabilities."""
    raw = {k: 1 / v for k, v in odds.items() if v > 1}
    total = sum(raw.values())
    if total <= 0:
        return raw
    return {k: v / total for k, v in raw.items()}


def _event_date(commence_time: str) -> tuple[str, str]:
    """Return (YYYY-MM-DD, HH:MM) in UTC from an ISO timestamp."""
    try:
        dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00")).astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    except Exception:
        return "", ""


# ── Basketball ─────────────────────────────────────────────────────────────

def _build_basketball_prediction(event: Dict, league_name: str, flag: str) -> Optional[Dict]:
    """Convert an Odds API basketball event into a BetIQ prediction dict."""
    home = event.get("home_team", "")
    away = event.get("away_team", "")
    if not home or not away:
        return None

    date_str, time_str = _event_date(event.get("commence_time", ""))

    h2h_odds = _best_odds(event, "h2h")
    if len(h2h_odds) < 2:
        return None

    probs = _implied_probs(h2h_odds)
    p_home = probs.get(home, 0)
    p_away = probs.get(away, 0)

    # Best tip
    if p_home >= p_away:
        tip, tip_code = f"{home} Win", "1"
        confidence = p_home
    else:
        tip, tip_code = f"{away} Win", "2"
        confidence = p_away

    # Over/Under line
    totals = _best_odds(event, "totals")
    total_line = None
    tip_total  = None
    if totals:
        # Find the line (key looks like "Over 218.5" — extract the number)
        for k in totals:
            if k.startswith("Over"):
                try:
                    total_line = float(k.split()[-1])
                    over_odds  = totals.get(k, 0)
                    under_odds = totals.get(f"Under {total_line}", 0)
                    if over_odds and under_odds:
                        tip_total = "Over" if over_odds < under_odds else "Under"
                        tip_total = f"{tip_total} {total_line}"
                except Exception:
                    pass
                break

    return {
        "home":            home,
        "away":            away,
        "date":            date_str,
        "time":            time_str,
        "sport":           "basketball",
        "league":          league_name,
        "league_name":     league_name,
        "flag":            flag,
        "tip_1x2":         tip,
        "tip_code":        tip_code,
        "tip_goals":       tip_total or "",
        "goals_type":      "value" if confidence > 0.65 else "normal",
        "goals_confidence": round(confidence, 3),
        "p_home":          round(p_home, 3),
        "p_draw":          0,
        "p_away":          round(p_away, 3),
        "p_over15":        0,
        "p_over25":        0,
        "total_line":      total_line,
        "odds_home":       round(h2h_odds.get(home, 0), 2),
        "odds_away":       round(h2h_odds.get(away, 0), 2),
    }


async def fetch_basketball_predictions() -> List[Dict]:
    results = []
    for sport_key, league_name, flag in BASKETBALL_SPORTS:
        events = await _fetch_odds(sport_key, markets="h2h,totals")
        for ev in events:
            p = _build_basketball_prediction(ev, league_name, flag)
            if p:
                results.append(p)
        await asyncio.sleep(0.2)
    print(f"[Sports] Basketball: {len(results)} predictions")
    return sorted(results, key=lambda x: x["date"] + x["time"])


# ── Tennis ─────────────────────────────────────────────────────────────────

def _build_tennis_prediction(event: Dict, league_name: str, flag: str,
                              sport: str = "tennis") -> Optional[Dict]:
    """Convert a tennis/table-tennis event into a BetIQ prediction dict."""
    p1 = event.get("home_team", "")  # In tennis API, home_team = player 1
    p2 = event.get("away_team", "")
    if not p1 or not p2:
        return None

    date_str, time_str = _event_date(event.get("commence_time", ""))

    odds = _best_odds(event, "h2h")
    if len(odds) < 2:
        return None

    probs    = _implied_probs(odds)
    p_home   = probs.get(p1, 0)
    p_away   = probs.get(p2, 0)

    if p_home >= p_away:
        tip, tip_code = f"{p1} Win", "1"
        confidence = p_home
    else:
        tip, tip_code = f"{p2} Win", "2"
        confidence = p_away

    # Detect surface from league name (Wimbledon=Grass, RG=Clay, US/AO=Hard)
    surface = "Hard"
    ln = league_name.lower()
    if any(x in ln for x in ["wimbledon", "halle", "queens", "grass"]):
        surface = "Grass"
    elif any(x in ln for x in ["roland", "french", "clay", "barcelona", "monte"]):
        surface = "Clay"

    return {
        "home":            p1,
        "away":            p2,
        "date":            date_str,
        "time":            time_str,
        "sport":           sport,
        "league":          league_name,
        "league_name":     league_name,
        "flag":            flag,
        "tip_1x2":         tip,
        "tip_code":        tip_code,
        "tip_goals":       "",
        "goals_type":      "value" if confidence > 0.65 else "normal",
        "goals_confidence": round(confidence, 3),
        "p_home":          round(p_home, 3),
        "p_draw":          0,
        "p_away":          round(p_away, 3),
        "p_over15":        0,
        "p_over25":        0,
        "surface":         surface,
        "odds_home":       round(odds.get(p1, 0), 2),
        "odds_away":       round(odds.get(p2, 0), 2),
    }


async def fetch_tennis_predictions() -> List[Dict]:
    active = await _get_active_sports()
    tennis_keys = [k for k in active
                   if any(k.startswith(kw) for kw in TENNIS_KEYWORDS)]
    if not tennis_keys:
        tennis_keys = []  # nothing active

    results = []
    for sport_key in tennis_keys:
        label = sport_key.replace("tennis_atp_", "ATP ").replace("tennis_wtp_", "WTA ").replace("_", " ").title()
        events = await _fetch_odds(sport_key, markets="h2h")
        for ev in events:
            p = _build_tennis_prediction(ev, label, "🎾", sport="tennis")
            if p:
                results.append(p)
        await asyncio.sleep(0.2)

    print(f"[Sports] Tennis: {len(results)} predictions from {len(tennis_keys)} tournaments")
    return sorted(results, key=lambda x: x["date"] + x["time"])


async def fetch_table_tennis_predictions() -> List[Dict]:
    events = await _fetch_odds(TABLE_TENNIS_KEY, markets="h2h")
    results = []
    for ev in events:
        p = _build_tennis_prediction(ev, "Table Tennis", "🏓", sport="table_tennis")
        if p:
            results.append(p)
    print(f"[Sports] Table Tennis: {len(results)} predictions")
    return sorted(results, key=lambda x: x["date"] + x["time"])


# ── Sport-specific market configs ─────────────────────────────────────────

SPORT_MARKETS = {
    "basketball": {
        "markets":    "h2h,spreads,totals",
        "market_defs": {
            "h2h":     {"name": "Moneyline",        "icon": "🏀"},
            "spreads":  {"name": "Point Spread",     "icon": "⚖️"},
            "totals":   {"name": "Total Points",     "icon": "📊"},
        },
    },
    "tennis": {
        "markets":    "h2h,alternate_spreads",
        "market_defs": {
            "h2h":              {"name": "Match Winner",   "icon": "🎾"},
            "alternate_spreads":{"name": "Game Handicap",  "icon": "⚖️"},
            "totals":           {"name": "Total Games",    "icon": "📊"},
        },
    },
    "table_tennis": {
        "markets":    "h2h,totals",
        "market_defs": {
            "h2h":   {"name": "Match Winner",  "icon": "🏓"},
            "totals":{"name": "Total Games",   "icon": "📊"},
        },
    },
}


async def fetch_event_detail(sport: str, home: str, away: str, date: str) -> Optional[Dict]:
    """
    Fetch full market detail for a specific match from The Odds API.
    Returns structured market data for the sport modal.
    """
    if not ODDS_API_KEY:
        return None

    from difflib import SequenceMatcher

    def sim(a: str, b: str) -> float:
        return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()

    # Determine which sport keys to search
    if sport == "basketball":
        sport_keys = [k for k, _, _ in BASKETBALL_SPORTS]
        market_str = "h2h,spreads,totals"
    elif sport == "tennis":
        active = await _get_active_sports()
        sport_keys = [k for k in active if any(k.startswith(kw) for kw in TENNIS_KEYWORDS)]
        market_str = "h2h,totals,alternate_spreads"
    elif sport == "table_tennis":
        sport_keys = [TABLE_TENNIS_KEY]
        market_str = "h2h,totals"
    else:
        return None

    for sport_key in sport_keys:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"{ODDS_BASE}/sports/{sport_key}/odds/",
                    params={
                        "apiKey": ODDS_API_KEY,
                        "regions": "eu",
                        "markets": market_str,
                        "oddsFormat": "decimal",
                    },
                )
                if r.status_code != 200:
                    continue

                events = r.json()
                # Find matching event by team names + date
                best, best_score = None, 0.0
                for ev in events:
                    h = ev.get("home_team", "")
                    a = ev.get("away_team", "")
                    ev_date, _ = _event_date(ev.get("commence_time", ""))
                    if ev_date != date:
                        continue
                    score = (sim(home, h) + sim(away, a)) / 2
                    if score > best_score:
                        best_score = score
                        best = ev

                if best and best_score >= 0.45:
                    return _structure_event_detail(best, sport, sport_key)

            await asyncio.sleep(0.2)
        except Exception as e:
            print(f"[Sports Detail] {sport_key} error: {e}")

    return None


def _structure_event_detail(event: Dict, sport: str, sport_key: str) -> Dict:
    """Convert a raw Odds API event into structured market data for the modal."""
    home = event.get("home_team", "")
    away = event.get("away_team", "")
    date_str, time_str = _event_date(event.get("commence_time", ""))

    market_defs = SPORT_MARKETS.get(sport, {}).get("market_defs", {})

    # Aggregate markets across bookmakers — best odds per outcome per market
    market_best: Dict[str, Dict[str, Dict]] = {}  # market_key → {outcome_name → {price, line}}

    for bookie in (event.get("bookmakers") or []):
        for market in (bookie.get("markets") or []):
            mk = market.get("key", "")
            if mk not in market_best:
                market_best[mk] = {}
            for o in (market.get("outcomes") or []):
                name  = o.get("name", "")
                price = float(o.get("price") or 0)
                point = o.get("point")  # spread/totals line
                if price > 1:
                    existing = market_best[mk].get(name, {})
                    if price > existing.get("price", 0):
                        market_best[mk][name] = {"price": price, "point": point}

    # Build structured markets for the modal
    structured_markets = []
    for mk, outcomes_raw in market_best.items():
        defn = market_defs.get(mk, {"name": mk.replace("_", " ").title(), "icon": "📌"})
        outcomes = []
        total_implied = sum(1/v["price"] for v in outcomes_raw.values() if v["price"] > 1)

        for name, data in outcomes_raw.items():
            price = data["price"]
            point = data.get("point")
            implied = (1 / price) / total_implied if total_implied > 0 else 0
            label = name
            if point is not None:
                sign = "+" if point > 0 else ""
                label = f"{name} ({sign}{point})"
            outcomes.append({
                "name":    name,
                "label":   label,
                "odds":    round(price, 2),
                "implied": round(implied, 3),
                "point":   point,
            })

        # Sort: for h2h home first; for totals Over first; for spreads by name
        if mk == "h2h":
            outcomes.sort(key=lambda o: (0 if o["name"] == home else 1))
        elif mk == "totals":
            outcomes.sort(key=lambda o: (0 if "over" in o["name"].lower() else 1))
        else:
            outcomes.sort(key=lambda o: o["name"])

        if outcomes:
            structured_markets.append({
                "id":       mk,
                "name":     defn["name"],
                "icon":     defn["icon"],
                "outcomes": outcomes,
            })

    # Sort markets: h2h first, then totals, then spreads
    order = {"h2h": 0, "totals": 1, "spreads": 2}
    structured_markets.sort(key=lambda m: order.get(m["id"], 9))

    # Compute best pick from h2h
    h2h_outs = next((m["outcomes"] for m in structured_markets if m["id"] == "h2h"), [])
    best_pick = None
    if h2h_outs:
        best = max(h2h_outs, key=lambda o: o["implied"])
        best_pick = {"label": best["label"], "odds": best["odds"], "confidence": best["implied"]}

    return {
        "home":       home,
        "away":       away,
        "date":       date_str,
        "time":       time_str,
        "sport":      sport,
        "sport_key":  sport_key,
        "markets":    structured_markets,
        "best_pick":  best_pick,
    }
