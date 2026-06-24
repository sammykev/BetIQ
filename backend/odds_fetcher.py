"""
Live odds fetcher using bookieskit.
Fetches 1X2 odds from SportyBet + Bet9ja, matches them to our predictions,
and computes value bets where our model's probability beats the implied odds.
"""

import asyncio
from datetime import date
from difflib import SequenceMatcher
from typing import Dict, List, Optional

# ── SportRadar tournament IDs used by SportyBet ────────────────────────────
LEAGUE_TOURNAMENTS = {
    "Premier League":      "sr:tournament:17",
    "Serie A":             "sr:tournament:23",
    "Bundesliga":          "sr:tournament:35",
    "La Liga":             "sr:tournament:8",
    "Ligue 1":             "sr:tournament:34",
    "Champions League":    "sr:tournament:7",
    "Europa League":       "sr:tournament:679",
    "Primeira Liga":       "sr:tournament:238",
    "Eredivisie":          "sr:tournament:37",
    "Liga Nos":            "sr:tournament:238",
}

MIN_EDGE = 0.05          # 5 % edge before a bet is flagged as value
MATCH_THRESHOLD = 0.55   # fuzzy-match threshold for team names


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
            outcomes = m.get("outcomes") or m.get("options") or m.get("selections") or []
            odds = {}
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
            if len(odds) == 3:
                return odds
    return None


def _event_teams(ev: Dict):
    home = (ev.get("homeTeamName") or ev.get("home", {}).get("name") or
            ev.get("O1") or ev.get("team1") or "")
    away = (ev.get("awayTeamName") or ev.get("away", {}).get("name") or
            ev.get("O2") or ev.get("team2") or "")
    return str(home), str(away)


def _event_date(ev: Dict) -> str:
    """Return YYYY-MM-DD for a SportyBet event."""
    ts = ev.get("estimateStartTime") or ev.get("startTime") or ev.get("kickoff") or 0
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return ""


async def _fetch_sportybet_events(tournament_ids: List[str], target_dates: set) -> List[Dict]:
    """Fetch events from SportyBet for given tournament IDs, filtered by date."""
    from bookieskit import SportyBet, extract_participants, extract_kickoff

    collected = []
    try:
        async with SportyBet(country="ng") as sb:
            for tid in tournament_ids:
                try:
                    raw = await sb.get_events(
                        tournament_id=tid,
                        sport_id="sr:sport:1",
                    )
                    # bookieskit wraps in various envelopes — unwrap
                    inner = raw.get("data") or raw
                    events = (
                        inner.get("events") or
                        inner.get("matches") or
                        inner.get("items") or
                        (inner if isinstance(inner, list) else [])
                    )

                    for ev in (events or []):
                        try:
                            # Try bookieskit helpers first
                            parts   = extract_participants(ev)
                            kickoff = extract_kickoff(ev)
                            home = parts.home if parts else None
                            away = parts.away if parts else None
                            ev_date = kickoff.strftime("%Y-%m-%d") if kickoff else _event_date(ev)
                        except Exception:
                            home, away = _event_teams(ev)
                            ev_date = _event_date(ev)

                        if not home or not away or ev_date not in target_dates:
                            continue

                        odds = _extract_1x2(ev)
                        if odds:
                            collected.append({
                                "home": home, "away": away, "date": ev_date,
                                "odds": odds,
                                "event_id": str(ev.get("eventId") or ev.get("id") or ""),
                                "source": "sportybet",
                            })
                    await asyncio.sleep(0.4)
                except Exception as e:
                    print(f"[Odds] tournament {tid} error: {e}")
    except ImportError:
        print("[Odds] bookieskit not installed — skipping live odds")
    except Exception as e:
        print(f"[Odds] SportyBet client error: {e}")

    return collected


def _match_events_to_predictions(events: List[Dict], predictions: List[Dict]) -> Dict[str, Dict]:
    """Fuzzy-match fetched events to our predictions, return index by pred key."""
    index = {}
    for pred in predictions:
        pred_date = pred.get("date", "")
        best, best_score = None, 0.0

        for ev in events:
            if ev["date"] != pred_date:
                continue
            score = (_sim(pred["home"], ev["home"]) + _sim(pred["away"], ev["away"])) / 2
            if score > best_score:
                best_score = score
                best = ev

        if best and best_score >= MATCH_THRESHOLD:
            key = f"{pred['home']}:{pred['away']}:{pred['date']}"
            index[key] = {**best["odds"], "event_id": best.get("event_id", ""), "source": best.get("source", "")}

    return index


async def fetch_odds_for_predictions(predictions: List[Dict]) -> Dict[str, Dict]:
    """
    Public entry point.
    Returns a dict keyed by "home:away:date" → {odds_1, odds_x, odds_2, event_id}.
    """
    if not predictions:
        return {}

    target_dates = {p.get("date", "") for p in predictions}

    # Only fetch tournaments relevant to today's predictions
    tournament_ids = list(LEAGUE_TOURNAMENTS.values())

    events = await _fetch_sportybet_events(tournament_ids, target_dates)
    print(f"[Odds] {len(events)} events with odds fetched from bookieskit")

    return _match_events_to_predictions(events, predictions)


def compute_value_bets(predictions: List[Dict], odds_index: Dict[str, Dict]) -> List[Dict]:
    """
    Compare XGBoost model probabilities against bookmaker implied probabilities.
    Returns matches where the model has ≥ MIN_EDGE (5 %) edge on any 1X2 outcome.
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
        raw_imp = {"1": 1/o1, "X": 1/ox, "2": 1/o2}
        overround = sum(raw_imp.values())
        imp = {k: v / overround for k, v in raw_imp.items()}

        model = {
            "1": float(pred.get("p_home", 0)),
            "X": float(pred.get("p_draw", 0)),
            "2": float(pred.get("p_away", 0)),
        }

        # Find the outcome with the largest positive edge
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
            "value_outcome": best_code,
            "value_label":   label_map[best_code],
            "value_odds":    round(best_odds_val, 2),
            "model_prob":    round(model[best_code] * 100, 1),
            "implied_prob":  round(imp[best_code] * 100, 1),
            "edge":          round(best_edge * 100, 1),
            "overround":     round((overround - 1) * 100, 1),
            "bookie":        odds.get("source", "sportybet"),
        })

    return sorted(value_bets, key=lambda x: x["edge"], reverse=True)
