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
        "regions": "eu",
        "markets": "h2h,btts,totals",   # 1X2 + BTTS + Over/Under
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
    Extract best available odds from an Odds API event across ALL markets.
    Returns:
    {
      home, away, date,
      odds:   {1, X, 2},           ← 1X2
      btts:   {yes, no},            ← BTTS
      totals: {over_2.5, under_2.5, over_1.5, under_1.5, ...},
      source: "the-odds-api"
    }
    """
    home_team = event.get("home_team", "")
    away_team = event.get("away_team", "")
    commence  = event.get("commence_time", "")

    try:
        dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
        date_str = dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        date_str = ""

    best_h2h: Dict[str, float] = {}
    best_btts: Dict[str, float] = {}
    best_totals: Dict[str, float] = {}

    for bookie in (event.get("bookmakers") or []):
        for market in (bookie.get("markets") or []):
            mk = market.get("key", "")

            if mk == "h2h":
                for o in (market.get("outcomes") or []):
                    n = o.get("name",""); p = float(o.get("price",0) or 0)
                    if p <= 1: continue
                    if n == home_team:               best_h2h["1"] = max(best_h2h.get("1",0), p)
                    elif n == away_team:             best_h2h["2"] = max(best_h2h.get("2",0), p)
                    elif n.lower() in ("draw","tie"):best_h2h["X"] = max(best_h2h.get("X",0), p)

            elif mk == "btts":
                for o in (market.get("outcomes") or []):
                    n = o.get("name","").lower(); p = float(o.get("price",0) or 0)
                    if p <= 1: continue
                    if n == "yes": best_btts["yes"] = max(best_btts.get("yes",0), p)
                    else:          best_btts["no"]  = max(best_btts.get("no",0),  p)

            elif mk == "totals":
                for o in (market.get("outcomes") or []):
                    n  = o.get("name","").lower()
                    pt = o.get("point")
                    p  = float(o.get("price",0) or 0)
                    if p <= 1 or pt is None: continue
                    slot = f"{n}_{pt}"
                    best_totals[slot] = max(best_totals.get(slot,0), p)

    if len(best_h2h) < 3:
        return None

    return {
        "home":   home_team, "away": away_team, "date": date_str,
        "odds":   best_h2h,
        "btts":   best_btts,
        "totals": best_totals,
        "source": "the-odds-api",
    }


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


def _implied(odds_val: float) -> float:
    return 1 / odds_val if odds_val > 1 else 0


def compute_value_bets(predictions: List[Dict], odds_index: Dict[str, Dict]) -> List[Dict]:
    """
    Compare model probabilities against bookmaker odds across three markets:
      1. 1X2  (Home Win / Draw / Away Win)
      2. Over/Under 2.5 goals
      3. BTTS (Both Teams to Score)

    Only uses REAL bookmaker odds — no derived/model-only markets.
    Flags any outcome where model probability exceeds implied probability by ≥ MIN_EDGE.
    """
    value_bets = []

    for pred in predictions:
        key = f"{pred['home']}:{pred['away']}:{pred['date']}"
        ev = odds_index.get(key)
        if not ev:
            continue

        # Support both old flat format {1,X,2} and new nested format {odds:{1,X,2}, btts:{}, totals:{}}
        h2h    = ev.get("odds", ev)
        btts   = ev.get("btts", {})
        totals = ev.get("totals", {})
        source = ev.get("source", "market")

        candidates = []

        # ── 1X2 ──────────────────────────────────────────────────────────
        o1 = float(h2h.get("1") or 0)
        ox = float(h2h.get("X") or 0)
        o2 = float(h2h.get("2") or 0)
        if o1 > 1 and ox > 1 and o2 > 1:
            raw = {"1": _implied(o1), "X": _implied(ox), "2": _implied(o2)}
            over = sum(raw.values())
            imp  = {k: v/over for k, v in raw.items()}
            mod  = {"1": float(pred.get("p_home",0)), "X": float(pred.get("p_draw",0)), "2": float(pred.get("p_away",0))}
            odds_map = {"1": o1, "X": ox, "2": o2}
            labels   = {"1": f"{pred['home']} Win", "X": "Draw", "2": f"{pred['away']} Win"}
            for code in ("1","X","2"):
                edge = mod[code] - imp[code]
                if edge >= MIN_EDGE:
                    candidates.append({"market":"1X2","label":labels[code],"code":code,
                                       "odds":odds_map[code],"model_prob":mod[code],
                                       "implied_prob":imp[code],"edge":edge,
                                       "overround":round((over-1)*100,1)})

        # ── Over/Under 2.5 ───────────────────────────────────────────────
        o_ov = float(totals.get("over_2.5") or 0)
        o_un = float(totals.get("under_2.5") or 0)
        if o_ov > 1 and o_un > 1:
            raw_t = {"over": _implied(o_ov), "under": _implied(o_un)}
            over_t = sum(raw_t.values())
            imp_t  = {k: v/over_t for k,v in raw_t.items()}
            mod_ov = float(pred.get("p_over25", 0.5))
            for code, mod_p, label, odds_val in [
                ("over",  mod_ov,      "Over 2.5 Goals",  o_ov),
                ("under", 1-mod_ov,    "Under 2.5 Goals", o_un),
            ]:
                edge = mod_p - imp_t[code]
                if edge >= MIN_EDGE:
                    candidates.append({"market":"Over/Under 2.5","label":label,"code":f"OU-{code.upper()}",
                                       "odds":odds_val,"model_prob":mod_p,
                                       "implied_prob":imp_t[code],"edge":edge,
                                       "overround":round((over_t-1)*100,1)})

        # ── BTTS ─────────────────────────────────────────────────────────
        b_y = float(btts.get("yes") or 0)
        b_n = float(btts.get("no")  or 0)
        if b_y > 1 and b_n > 1:
            raw_b = {"yes": _implied(b_y), "no": _implied(b_n)}
            over_b = sum(raw_b.values())
            imp_b  = {k: v/over_b for k,v in raw_b.items()}
            mod_btts_y = float(pred.get("p_over15", 0.5))  # proxy: most goals → both likely score
            for code, mod_p, label, odds_val in [
                ("yes", mod_btts_y,   "BTTS Yes", b_y),
                ("no",  1-mod_btts_y, "BTTS No",  b_n),
            ]:
                edge = mod_p - imp_b[code]
                if edge >= MIN_EDGE:
                    candidates.append({"market":"BTTS","label":label,"code":f"BTTS-{code.upper()}",
                                       "odds":odds_val,"model_prob":mod_p,
                                       "implied_prob":imp_b[code],"edge":edge,
                                       "overround":round((over_b-1)*100,1)})

        if not candidates:
            continue

        best = max(candidates, key=lambda c: c["edge"])
        value_bets.append({
            **pred,
            "value_outcome":  best["code"],
            "value_label":    f"{best['market']}: {best['label']}",
            "value_market":   best["market"],
            "value_odds":     round(best["odds"], 2),
            "model_prob":     round(best["model_prob"] * 100, 1),
            "implied_prob":   round(best["implied_prob"] * 100, 1),
            "edge":           round(best["edge"] * 100, 1),
            "overround":      best["overround"],
            "bookie":         source,
            "all_edges":      [{"market":c["market"],"label":c["label"],
                                "edge":round(c["edge"]*100,1),"odds":c["odds"]}
                               for c in sorted(candidates,key=lambda x:-x["edge"])],
        })

    return sorted(value_bets, key=lambda x: x["edge"], reverse=True)
