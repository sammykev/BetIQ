"""
Live odds fetcher for value bet detection.
Uses our existing SportyBet date-based fetcher (which works) to pull 1X2 odds,
then compares them against XGBoost model probabilities to find mispriced markets.
"""

import asyncio
from difflib import SequenceMatcher
from typing import Dict, List, Optional

MIN_EDGE = 0.05        # 5% edge minimum to flag as value
MATCH_THRESHOLD = 0.52 # fuzzy team name threshold


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _extract_1x2(event: Dict) -> Optional[Dict]:
    """Pull home/draw/away decimal odds from a raw SportyBet event dict."""
    markets = (
        event.get("markets") or
        event.get("betOptions") or
        event.get("marketList") or []
    )
    for m in markets:
        mname = (m.get("name") or m.get("marketName") or "").lower()
        mid   = str(m.get("id") or m.get("marketId") or "")
        if "1x2" in mname or "match result" in mname or mid in ("1", "1_18", "18"):
            outcomes = (
                m.get("outcomes") or m.get("options") or
                m.get("selections") or []
            )
            odds: Dict[str, float] = {}
            for o in outcomes:
                name = (o.get("name") or o.get("outcomeName") or "").lower().strip()
                val  = o.get("odds") or o.get("value") or o.get("price") or 0
                try:
                    val = float(val)
                except Exception:
                    val = 0.0
                if name in ("1", "home", "home win", "w1"):
                    odds["1"] = val
                elif name in ("x", "draw", "tie"):
                    odds["X"] = val
                elif name in ("2", "away", "away win", "w2"):
                    odds["2"] = val
            if len(odds) == 3 and all(v > 1 for v in odds.values()):
                return odds
    return None


def _event_teams(ev: Dict):
    home = (ev.get("homeTeamName") or ev.get("home", {}).get("name") or
            ev.get("O1") or ev.get("team1") or "")
    away = (ev.get("awayTeamName") or ev.get("away", {}).get("name") or
            ev.get("O2") or ev.get("team2") or "")
    return str(home), str(away)


async def fetch_odds_for_predictions(predictions: List[Dict]) -> Dict[str, Dict]:
    """
    Public entry point. Fetches live 1X2 odds from SportyBet for each unique
    date in the predictions list, then fuzzy-matches them to our predictions.

    Returns dict keyed by "home:away:date" → {1, X, 2, event_id, source}.
    """
    if not predictions:
        return {}

    from sportybet import fetch_events_for_date

    # Collect events for each unique date
    dates = list({p.get("date", "") for p in predictions if p.get("date")})
    all_events: List[Dict] = []

    for d in dates:
        try:
            events = await fetch_events_for_date(d)
            for ev in events:
                home, away = _event_teams(ev)
                if not home or not away:
                    continue
                odds = _extract_1x2(ev)
                if odds:
                    all_events.append({
                        "home": home,
                        "away": away,
                        "date": d,
                        "odds": odds,
                        "event_id": str(ev.get("eventId") or ev.get("id") or ""),
                        "source": "sportybet",
                    })
            await asyncio.sleep(0.3)
        except Exception as e:
            print(f"[Odds] {d} fetch error: {e}")

    print(f"[Odds] {len(all_events)} events with 1X2 odds from SportyBet")

    # Fuzzy-match to predictions
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
    Returns predictions where the model has ≥ MIN_EDGE on any 1X2 outcome,
    sorted by edge descending.
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
        overround = sum(raw_imp.values())          # e.g. 1.06 = 6% margin
        imp = {k: v / overround for k, v in raw_imp.items()}

        model = {
            "1": float(pred.get("p_home", 0)),
            "X": float(pred.get("p_draw", 0)),
            "2": float(pred.get("p_away", 0)),
        }

        # Pick the outcome with the largest positive edge
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
