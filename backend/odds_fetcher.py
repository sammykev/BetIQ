"""
Live odds fetcher for value bet detection.
Uses SportyBet date-based events (same source as booking codes) to pull 1X2 odds,
then compares against XGBoost model probabilities to find mispriced markets.
"""

import asyncio
import json
from difflib import SequenceMatcher
from typing import Dict, List, Optional

MIN_EDGE = 0.03        # 3% edge minimum (lowered from 5% for more results)
MATCH_THRESHOLD = 0.50 # fuzzy team name threshold


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _extract_1x2(event: Dict) -> Optional[Dict]:
    """
    Extract 1X2 odds from a SportyBet event dict.
    Confirmed structure from network capture:
      market.id == "1", market.desc == "1X2"
      outcome.desc == "Home" / "Draw" / "Away"
      outcome.odds == "1.85" (string)
    """
    markets = event.get("markets") or event.get("betOptions") or []
    for m in markets:
        if not isinstance(m, dict):
            continue
        mid   = str(m.get("id") or "")
        mdesc = (m.get("desc") or m.get("name") or "").lower()
        if mid != "1" and "1x2" not in mdesc:
            continue

        outcomes = m.get("outcomes") or m.get("options") or []
        odds: Dict[str, float] = {}
        for o in outcomes:
            if not isinstance(o, dict):
                continue
            # SportyBet uses "desc" for outcome label ("Home"/"Draw"/"Away")
            label = (o.get("desc") or o.get("name") or "").lower().strip()
            try:
                val = float(o.get("odds") or o.get("value") or 0)
            except Exception:
                val = 0.0
            if val <= 1.0 or not o.get("isActive", 1):
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
    home = (ev.get("homeTeamName") or ev.get("home", {}).get("name") or
            ev.get("O1") or ev.get("team1") or ev.get("homeName") or "")
    away = (ev.get("awayTeamName") or ev.get("away", {}).get("name") or
            ev.get("O2") or ev.get("team2") or ev.get("awayName") or "")
    return str(home).strip(), str(away).strip()


async def fetch_odds_for_predictions(predictions: List[Dict]) -> Dict[str, Dict]:
    """
    Fetch live 1X2 odds from SportyBet for each unique date in predictions.
    Returns dict keyed by "home:away:date" → {1, X, 2, event_id, source}.
    """
    if not predictions:
        return {}

    from sportybet import fetch_events_for_date

    dates = list({p.get("date", "") for p in predictions if p.get("date")})
    all_events: List[Dict] = []
    for d in dates:
        try:
            events = await fetch_events_for_date(d)
            if not events:
                continue

            matched_odds = 0
            for ev in events:
                home, away = _event_teams(ev)
                if not home or not away:
                    continue
                odds = _extract_1x2(ev)
                if odds:
                    matched_odds += 1
                    all_events.append({
                        "home": home, "away": away, "date": d,
                        "odds": odds,
                        "event_id": str(ev.get("eventId") or ev.get("id") or ""),
                        "source": "sportybet",
                    })
            print(f"[Odds] {d}: {len(events)} events, {matched_odds} with 1X2 odds")
            await asyncio.sleep(0.3)
        except Exception as e:
            print(f"[Odds] {d} error: {e}")

    print(f"[Odds] Total: {len(all_events)} events with valid 1X2 odds across {len(dates)} dates")

    # Fuzzy-match events to our predictions
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
            index[key] = {
                **best["odds"],
                "event_id": best.get("event_id", ""),
                "source": best.get("source", "sportybet"),
            }

    print(f"[Odds] Matched {len(index)}/{len(predictions)} predictions to live odds")
    return index


def compute_value_bets(predictions: List[Dict], odds_index: Dict[str, Dict]) -> List[Dict]:
    """
    Compare model probabilities against bookmaker implied probabilities.
    Returns predictions where the model has ≥ MIN_EDGE on any 1X2 outcome.
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

        # Overround-adjusted implied probabilities
        raw_imp = {"1": 1 / o1, "X": 1 / ox, "2": 1 / o2}
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

        if best_edge < MIN_EDGE or best_code is None:
            continue

        label_map = {
            "1": f"{pred['home']} Win",
            "X": "Draw",
            "2": f"{pred['away']} Win",
        }
        value_bets.append({
            **pred,
            "value_outcome":  best_code,
            "value_label":    label_map[best_code],
            "value_odds":     round(best_odds_val, 2),
            "model_prob":     round(model[best_code] * 100, 1),
            "implied_prob":   round(imp[best_code] * 100, 1),
            "edge":           round(best_edge * 100, 1),
            "overround":      round((overround - 1) * 100, 1),
            "bookie":         odds.get("source", "sportybet"),
        })

    return sorted(value_bets, key=lambda x: x["edge"], reverse=True)
