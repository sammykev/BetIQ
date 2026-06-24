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
    Try every known SportyBet event structure to pull home/draw/away odds.
    Logs structure on first event to help debug.
    """
    # Strategy 1 — markets / betOptions / marketList array
    markets = (
        event.get("markets") or
        event.get("betOptions") or
        event.get("marketList") or
        event.get("odds") or []
    )

    if isinstance(markets, list):
        for m in markets:
            if not isinstance(m, dict):
                continue
            mname = (m.get("name") or m.get("marketName") or m.get("n") or "").lower()
            mid   = str(m.get("id") or m.get("marketId") or m.get("T") or "")
            # Accept 1X2 market by name or by known IDs
            is_1x2 = ("1x2" in mname or "match result" in mname or
                       "full time result" in mname or
                       mid in ("1", "1_18", "18", "2"))
            if not is_1x2:
                continue

            outcomes = (
                m.get("outcomes") or m.get("options") or
                m.get("selections") or m.get("ME") or
                m.get("choiceGroup") or []
            )
            if not isinstance(outcomes, list):
                continue

            odds: Dict[str, float] = {}
            for o in outcomes:
                if not isinstance(o, dict):
                    continue
                name = (o.get("name") or o.get("outcomeName") or
                        o.get("N") or o.get("n") or "").lower().strip()
                val  = (o.get("odds") or o.get("value") or o.get("price") or
                        o.get("C") or o.get("coefficient") or 0)
                try:
                    val = float(val)
                except Exception:
                    val = 0.0
                if val <= 1.0:
                    continue

                if name in ("1", "home", "home win", "w1", "1 (home)"):
                    odds["1"] = val
                elif name in ("x", "draw", "tie", "draw (x)", "x (draw)"):
                    odds["X"] = val
                elif name in ("2", "away", "away win", "w2", "2 (away)"):
                    odds["2"] = val

            if len(odds) == 3:
                return odds

    # Strategy 2 — flat odds directly on event (some endpoints do this)
    # e.g. event = {odds1: 1.85, oddsX: 3.6, odds2: 4.2}
    flat_tries = [
        ("odds1", "oddsX", "odds2"),
        ("home_odds", "draw_odds", "away_odds"),
        ("odd1", "oddX", "odd2"),
        ("p1", "px", "p2"),
    ]
    for k1, kx, k2 in flat_tries:
        v1 = event.get(k1)
        vx = event.get(kx)
        v2 = event.get(k2)
        if v1 and vx and v2:
            try:
                o1, ox, o2 = float(v1), float(vx), float(v2)
                if o1 > 1 and ox > 1 and o2 > 1:
                    return {"1": o1, "X": ox, "2": o2}
            except Exception:
                pass

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
    debug_logged = False

    for d in dates:
        try:
            events = await fetch_events_for_date(d)
            if not events:
                continue

            # Debug: log keys of first event so we can see the structure
            if not debug_logged and events:
                first = events[0]
                top_keys = list(first.keys())[:15]
                markets_sample = first.get("markets") or first.get("betOptions") or []
                mkt_keys = list(markets_sample[0].keys())[:10] if markets_sample else []
                print(f"[Odds] Event keys: {top_keys}")
                print(f"[Odds] Market keys: {mkt_keys}")
                if markets_sample:
                    outs = (markets_sample[0].get("outcomes") or
                            markets_sample[0].get("options") or
                            markets_sample[0].get("selections") or [])
                    print(f"[Odds] Outcome sample: {outs[:2] if outs else 'none'}")
                debug_logged = True

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
