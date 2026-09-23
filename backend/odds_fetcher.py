"""
Live odds fetcher using The Odds API (the-odds-api.com).
Free tier: 500 requests/month. We cache aggressively (4h) and only fetch
sports present in current predictions to stay well within quota.

Set ODDS_API_KEY in environment variables. Without it, value bets are disabled.
"""

import asyncio
import os
import httpx
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Dict, List, Optional

ODDS_API_KEY  = os.getenv("ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

MIN_EDGE        = 0.03   # 3% edge
MATCH_THRESHOLD = 0.50   # fuzzy team name match

# Map our league names → The Odds API sport keys
LEAGUE_TO_SPORT: Dict[str, str] = {
    # Club leagues
    "premier league":       "soccer_epl",
    "epl":                  "soccer_epl",
    "serie a":              "soccer_italy_serie_a",
    "bundesliga":           "soccer_germany_bundesliga",
    "la liga":              "soccer_spain_la_liga",
    "ligue 1":              "soccer_france_ligue_one",
    "champions league":     "soccer_uefa_champs_league",
    "europa league":        "soccer_uefa_europa_league",
    "primeira liga":        "soccer_portugal_primeira_liga",
    "eredivisie":           "soccer_netherlands_eredivisie",
    # International
    "world cup":            "soccer_fifa_world_cup",
    "fifa world cup":       "soccer_fifa_world_cup",
    "afcon":                "soccer_africa_cup_of_nations",
    "africa cup":           "soccer_africa_cup_of_nations",
    "copa america":         "soccer_conmebol_copa_america",
    "nations league":       "soccer_uefa_nations_league",
    "uefa nations league":  "soccer_uefa_nations_league",
    "world cup qualifying": "soccer_world_cup_quali_africa",
    "wcq":                  "soccer_world_cup_quali_africa",
    "international":        "soccer_fifa_world_cup",  # broad fallback
    "friendly":             "soccer_fifa_world_cup",
}

# Fallback sports to try when no league match is found
FALLBACK_SPORTS = [
    "soccer_fifa_world_cup",
    "soccer_africa_cup_of_nations",
    "soccer_conmebol_copa_america",
    "soccer_uefa_nations_league",
    "soccer_epl",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_spain_la_liga",
]


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _sport_keys_for_predictions(predictions: List[Dict]) -> List[str]:
    """
    Return the minimal set of The Odds API sport keys needed to cover
    the predictions we have — avoids wasting API quota on irrelevant sports.
    """
    keys = set()
    for p in predictions:
        # International fixtures say which sport key (if any) prices them
        if "odds_sport" in p:
            if p["odds_sport"]:
                keys.add(p["odds_sport"])
            continue
        league = (p.get("league_name") or p.get("league") or "").lower()
        matched = False
        for keyword, sport_key in LEAGUE_TO_SPORT.items():
            if keyword in league:
                keys.add(sport_key)
                matched = True
                break
        if not matched:
            # Unknown league — add common international sports
            keys.update(["soccer_fifa_world_cup", "soccer_africa_cup_of_nations",
                          "soccer_uefa_nations_league"])
    # If still empty, try all fallbacks
    return list(keys) if keys else FALLBACK_SPORTS[:4]


async def _fetch_odds_for_sport(sport_key: str) -> List[Dict]:
    """Fetch 1X2 (h2h) odds for a sport from The Odds API."""
    if not ODDS_API_KEY:
        return []
    url = f"{ODDS_API_BASE}/sports/{sport_key}/odds/"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",          # European bookmakers = tightest margins
        "markets": "h2h",         # 1X2 / moneyline
        "oddsFormat": "decimal",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, params=params)
            remaining = r.headers.get("x-requests-remaining", "?")
            used      = r.headers.get("x-requests-used", "?")
            if r.status_code == 200:
                data = r.json()
                print(f"[Odds] {sport_key}: {len(data)} events "
                      f"(quota used={used} remaining={remaining})")
                return data
            elif r.status_code == 422:
                print(f"[Odds] {sport_key}: no current events (422)")
                return []
            else:
                print(f"[Odds] {sport_key}: HTTP {r.status_code} — {r.text[:120]}")
    except Exception as e:
        print(f"[Odds] {sport_key} error: {e}")
    return []


def _parse_odds_event(event: Dict) -> Optional[Dict]:
    """
    Extract best available 1X2 odds from an Odds API event.
    Uses the bookmaker with the highest home win price (most generous).
    """
    home_team = event.get("home_team", "")
    away_team = event.get("away_team", "")
    commence  = event.get("commence_time", "")

    try:
        dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
        date_str = dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        date_str = ""

    # Aggregate odds across bookmakers — use best (highest) odds per outcome
    best: Dict[str, float] = {}
    for bookie in (event.get("bookmakers") or []):
        for market in (bookie.get("markets") or []):
            if market.get("key") != "h2h":
                continue
            for outcome in (market.get("outcomes") or []):
                name  = outcome.get("name", "")
                price = float(outcome.get("price") or 0)
                if price <= 1:
                    continue
                if name == home_team:
                    best["1"] = max(best.get("1", 0), price)
                elif name == away_team:
                    best["2"] = max(best.get("2", 0), price)
                elif name.lower() in ("draw", "tie"):
                    best["X"] = max(best.get("X", 0), price)

    if len(best) == 3:
        return {"home": home_team, "away": away_team, "date": date_str,
                "odds": best, "source": "the-odds-api"}
    return None


async def fetch_odds_for_predictions(predictions: List[Dict]) -> Dict[str, Dict]:
    """
    Fetch live 1X2 odds from The Odds API for the sports present in our predictions.
    Returns dict keyed by "home:away:date" → {1, X, 2, source}.
    """
    if not predictions:
        return {}

    if not ODDS_API_KEY:
        print("[Odds] ODDS_API_KEY not set — value bets disabled. "
              "Get a free key at the-odds-api.com and add it to Render env vars.")
        return {}

    sport_keys = _sport_keys_for_predictions(predictions)
    target_dates = {p.get("date", "") for p in predictions if p.get("date")}
    print(f"[Odds] Fetching {sport_keys} for dates {sorted(target_dates)[:5]}")

    all_parsed: List[Dict] = []
    for sport_key in sport_keys:
        events = await _fetch_odds_for_sport(sport_key)
        for ev in events:
            parsed = _parse_odds_event(ev)
            if parsed and parsed["date"] in target_dates:
                all_parsed.append(parsed)
        await asyncio.sleep(0.2)

    print(f"[Odds] {len(all_parsed)} events with odds across {len(sport_keys)} sports")

    # Fuzzy-match to our predictions
    index: Dict[str, Dict] = {}
    for pred in predictions:
        pred_date = pred.get("date", "")
        best, best_score = None, 0.0
        for ev in all_parsed:
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
    """Flag predictions where model probability beats implied probability by ≥ MIN_EDGE."""
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
            "bookie":        "Best available (eu bookmakers)",
        })

    return sorted(value_bets, key=lambda x: x["edge"], reverse=True)
