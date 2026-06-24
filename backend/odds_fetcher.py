"""
Live odds fetcher for value bet detection.
Calls SportyBet's pcEvents endpoint directly (confirmed working from network capture).
Response structure: data = [{id: tournament_id, events: [{...markets...}]}]
Outcome label field is "desc" (not "name"), market id "1" = 1X2.
"""

import asyncio
from difflib import SequenceMatcher
from typing import Dict, List, Optional
import httpx

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

# Tournament IDs to fetch — covers all our prediction leagues
TOURNAMENTS = [
    "sr:tournament:17",   # Premier League
    "sr:tournament:23",   # Serie A
    "sr:tournament:35",   # Bundesliga
    "sr:tournament:8",    # La Liga
    "sr:tournament:34",   # Ligue 1
    "sr:tournament:7",    # Champions League
    "sr:tournament:679",  # Europa League
    "sr:tournament:238",  # Primeira Liga
    "sr:tournament:37",   # Eredivisie
    "sr:tournament:203",  # Algeria Ligue Pro
]

MIN_EDGE = 0.03
MATCH_THRESHOLD = 0.50


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _extract_1x2(event: Dict) -> Optional[Dict]:
    """
    Extract 1X2 odds from a SportyBet pcEvents event.
    Confirmed from network capture:
      market.id == "1", market.desc == "1X2"
      outcome.desc == "Home"/"Draw"/"Away", outcome.odds == "1.85" (string)
      outcome.isActive == 1
    """
    for m in (event.get("markets") or []):
        if not isinstance(m, dict):
            continue
        if str(m.get("id", "")) != "1":
            continue  # only 1X2 market

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


def _event_date(ev: Dict) -> str:
    ts = ev.get("estimateStartTime") or ev.get("startTime") or 0
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _event_teams(ev: Dict):
    return (
        str(ev.get("homeTeamName") or "").strip(),
        str(ev.get("awayTeamName") or "").strip(),
    )


async def _fetch_pcevents(tournament_id: str) -> List[Dict]:
    """
    Call SportyBet's pcEvents endpoint for one tournament.
    Tries POST body first, then GET query params.
    Returns flat list of events with markets embedded.
    """
    ts = int(__import__("time").time() * 1000)

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        # Try 1: POST with JSON body (what bookieskit uses)
        for payload in [
            {"tournamentId": tournament_id, "sportId": "sr:sport:1", "marketId": "1", "_t": ts},
            {"tournamentIds": [tournament_id], "sportId": "sr:sport:1", "marketId": "1"},
        ]:
            try:
                r = await client.post(
                    f"{BASE}/factsCenter/pcEvents",
                    json=payload,
                    headers={**_HEADERS, "Content-Type": "application/json"},
                )
                if r.status_code == 200 and r.text.strip():
                    d = r.json()
                    if d.get("bizCode") == 10000:
                        return _flatten(d.get("data") or [])
            except Exception:
                pass

        # Try 2: GET with query params
        for params in [
            {"tournamentId": tournament_id, "sportId": "sr:sport:1", "marketId": "1", "_t": ts},
            {"id": tournament_id, "sportId": "sr:sport:1", "marketId": "1", "_t": ts},
        ]:
            try:
                r = await client.get(
                    f"{BASE}/factsCenter/pcEvents",
                    params=params,
                    headers=_HEADERS,
                )
                if r.status_code == 200 and r.text.strip():
                    d = r.json()
                    if d.get("bizCode") == 10000:
                        return _flatten(d.get("data") or [])
            except Exception:
                pass

    return []


def _flatten(data) -> List[Dict]:
    """pcEvents data = [{id: tournament, events: [...]}, ...] — flatten all events."""
    events = []
    if isinstance(data, list):
        for t in data:
            if isinstance(t, dict):
                events.extend(t.get("events") or [])
    elif isinstance(data, dict):
        events.extend(data.get("events") or [])
    return events


async def fetch_odds_for_predictions(predictions: List[Dict]) -> Dict[str, Dict]:
    """
    Fetch live 1X2 odds from SportyBet pcEvents for all our prediction leagues.
    Returns dict keyed by "home:away:date" → {1, X, 2, source}.
    """
    if not predictions:
        return {}

    target_dates = {p.get("date", "") for p in predictions if p.get("date")}
    all_events: List[Dict] = []

    for tid in TOURNAMENTS:
        try:
            evs = await _fetch_pcevents(tid)
            before = len(all_events)
            for ev in evs:
                home, away = _event_teams(ev)
                ev_date = _event_date(ev)
                if not home or not away or ev_date not in target_dates:
                    continue
                odds = _extract_1x2(ev)
                if odds:
                    all_events.append({
                        "home": home, "away": away, "date": ev_date,
                        "odds": odds, "source": "sportybet",
                    })
            added = len(all_events) - before
            if added:
                print(f"[Odds] {tid}: +{added} events with 1X2 odds")
        except Exception as e:
            print(f"[Odds] {tid} error: {e}")
        await asyncio.sleep(0.3)

    print(f"[Odds] Total: {len(all_events)} events matched to target dates {target_dates}")

    # Fuzzy-match to our predictions
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
    Flag predictions where the model probability beats implied probability by ≥ MIN_EDGE.
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
