"""
Check a SportyBet booking code against the model, then improve it.

Each leg (sportybet.parse_share) is matched to our prediction for that match
(the caller finds it) and to the model's own pick for that exact outcome, so
it gets our probability beside SportyBet's price. Then:

- a verdict per leg: strong (≥75%), fair (≥55%), risky, or not modelled;
- a better pick on the same match when the model rates one clearly higher;
- "same odds": the slip with the best chance of winning whose total stays
  near the code's (optimizer.optimize over the code's matches only);
- "safest": each match's likeliest pick at odds ≥ 1.15;
- three tickets to book (the "Refine" button on the site):
  safe = the safest slip; conservative = each match's likeliest pick paying
  at least 1.40; risky = the best chance at the code's own total or twice
  the conservative total, whichever is higher.

Legs we can't model (a market or match we don't predict) are kept as they
are in both slips, and booked by their SportyBet ids.
"""

import math
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import booking_slip
from optimizer import _PICKS, Option, candidates, optimize

STRONG, FAIR = 0.75, 0.55
BETTER_BY = 0.08          # a suggestion must beat the leg by this much
SAFE_MIN_ODDS = 1.15
CONSERVATIVE_MIN_ODDS = 1.40
RISKY_OVER_CONSERVATIVE = 2.0   # the risky ticket pays at least this much more


def _when(ms: Any) -> tuple:
    try:
        dt = datetime.fromtimestamp(int(ms) / 1000, timezone.utc)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    except (TypeError, ValueError):
        return "", ""


def model_pick(pred: Dict, sel: Dict, confirmed: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """The model's pick for exactly this SportyBet outcome, with its probability.
    `confirmed` = {booking_slip.VERIFIED kind: SportyBet market id} for markets
    SportyBet has confirmed; unconfirmed ones aren't matched (their guessed id
    could be another market)."""
    if pred.get("sport") in ("tennis", "table_tennis"):
        if sel["marketId"] == "186" and sel["outcomeId"] in ("4", "5"):
            home = sel["outcomeId"] == "4"
            prob = pred.get("p_home" if home else "p_away")
            if isinstance(prob, (int, float)):
                return {"market": "winner", "market_name": "Winner", "code": "1" if home else "2",
                        "label": f"{pred['home' if home else 'away']} to win", "prob": float(prob)}
        return None
    for market, market_name, code, label, prob_of in _PICKS:
        ids = booking_slip.sportybet_ids(market, code)
        if not ids:
            continue
        kind = booking_slip.verified_kind(market, code)
        if kind and kind not in confirmed:
            continue
        market_id = confirmed[kind] if kind else ids["marketId"]
        if (market_id, ids["specifier"], ids["outcomeId"]) != (sel["marketId"], sel["specifier"], sel["outcomeId"]):
            continue
        prob = prob_of(pred) if all(isinstance(pred.get(k), (int, float)) for k in ("p_home", "p_over25")) else None
        if not isinstance(prob, (int, float)):
            return None
        return {"market": market, "market_name": market_name, "code": code,
                "label": label.format(home=pred["home"], away=pred["away"]), "prob": float(prob)}
    return None


def _verdict(prob: Optional[float]) -> str:
    if prob is None:
        return "not_modelled"
    return "strong" if prob >= STRONG else "fair" if prob >= FAIR else "risky"


def _kept_option(sel: Dict, date: str, time: str) -> Dict[str, Any]:
    """A leg we don't model, carried into the improved slips unchanged."""
    return {"home": sel["home"], "away": sel["away"], "date": date, "time": time, "league": sel.get("tournament", ""),
            "market": "sportybet", "market_name": sel["market"], "code": sel["outcomeId"],
            "label": sel["outcome"], "prob": None, "odds": sel["odds"], "odds_source": "sportybet",
            "sb": {k: sel[k] for k in ("eventId", "marketId", "specifier", "outcomeId")}}


def _slip(picks: List[Dict[str, Any]]) -> Dict[str, Any]:
    known = [p["prob"] for p in picks if p.get("prob") is not None]
    odds = [p["odds"] for p in picks if p.get("odds")]
    return {"picks": picks, "games": len(picks), "total_odds": round(math.prod(odds), 2) if odds else None,
            "win_chance": round(math.prod(known), 4) if known else None,
            "unmodelled": sum(1 for p in picks if p.get("prob") is None),
            "estimated_prices": sum(1 for p in picks if p.get("odds_source") == "estimated")}


def analyse(selections: List[Dict], find_pred: Callable[[Dict], Optional[Dict]],
            event_for: Callable[[Dict], Optional[Dict]], confirmed: Dict[str, str],
            allowed: Optional[Callable[[str, str], bool]] = None) -> Dict[str, Any]:
    """Legs with verdicts and suggestions, the code as it stands, and the two
    improved slips. `find_pred(sel)` → our prediction for its match or None;
    `event_for(pred)` → its linked SportyBet event (prices) or None."""
    legs, groups, kept, original_odds = [], [], [], []
    for sel in selections:
        date, time = _when(sel.get("start"))
        pred = find_pred(sel)
        pick = model_pick(pred, sel, confirmed) if pred else None
        prob = pick["prob"] if pick else None
        odds = sel.get("odds")
        leg = {"home": sel["home"], "away": sel["away"], "date": date, "time": time,
               "league": (pred or {}).get("league_name") or sel.get("tournament", ""),
               "market": sel["market"], "pick": sel["outcome"], "odds": odds, "active": sel.get("active", True),
               "our_prob": round(prob, 3) if prob is not None else None,
               "implied": round(1 / odds, 3) if odds else None,
               "value": round(prob * odds - 1, 3) if prob is not None and odds else None,
               "verdict": _verdict(prob), "matched": pred is not None, "suggestion": None,
               "sb": {k: sel[k] for k in ("eventId", "marketId", "specifier", "outcomeId")}}
        if not pred:
            leg["note"] = "We don't predict this match"
        elif prob is None:
            leg["note"] = "We don't model this market"
        options: List[Option] = []
        if pred and pred.get("sport") in (None, "football"):
            options = candidates(pred, event_for(pred), 0.5, None, allowed)
            if pick and odds and odds > 1.01:
                # The code's own pick stays an option (priced as SportyBet has it)
                options = [o for o in options if (o.market, o.code) != (pick["market"], pick["code"])] + [
                    Option(pred["home"], pred["away"], pred["date"], pred.get("time") or "",
                           pred.get("league_name") or "", pick["market"], pick["market_name"], pick["code"],
                           pick["label"], round(prob, 4), round(float(odds), 2), "sportybet",
                           league_id=str(pred.get("league") or ""))]
            better = max((o for o in options if o.odds >= 1.2 and o.prob >= (prob or 0) + BETTER_BY),
                         key=lambda o: (o.prob, o.odds), default=None)
            if better and (prob is None or better.code != pick["code"] or better.market != pick["market"]):
                leg["suggestion"] = {"market_name": better.market_name, "label": better.label,
                                     "prob": better.prob, "odds": better.odds, "odds_source": better.odds_source}
        legs.append(leg)
        if options and pick is not None and odds:
            groups.append(options)
            original_odds.append(float(odds))
        else:
            kept.append(_kept_option(sel, date, time))

    original = [{"prob": l["our_prob"], "odds": l["odds"]} for l in legs]
    report = {"legs": legs, "original": {k: v for k, v in _slip(original).items() if k != "picks"},
              "same_odds": None, "safest": None, "tickets": None}
    if groups:
        target = math.prod(original_odds)  # the modelled legs' total as booked
        best = None
        for spread in (0.1, 0.25):
            best = optimize(groups, max(1.01, target * (1 - spread)), target * (1 + spread), len(groups))
            if best:
                break
        if best:
            kept_odds = math.prod(k["odds"] for k in kept if k.get("odds"))
            report["same_odds"] = {**_slip(best["picks"] + kept), "target_odds": round(target * kept_odds, 2),
                                   "within_target": best["within_target"]}
        safest = [asdict(max([o for o in g if o.odds >= SAFE_MIN_ODDS] or g, key=lambda o: (o.prob, o.odds)))
                  for g in groups]
        report["safest"] = _slip(safest + kept)
        report["tickets"] = _tickets(groups, kept, safest, target)
    return report


def _likeliest(group: List[Option], min_odds: float) -> Optional[Option]:
    pool = [o for o in group if o.odds >= min_odds]
    return max(pool, key=lambda o: (o.prob, o.odds)) if pool else None


def _tickets(groups: List[List[Option]], kept: List[Dict], safest: List[Dict],
             booked_total: float) -> Dict[str, Dict[str, Any]]:
    """Safe, conservative and risky versions of the code, on its own
    matches (legs we can't model kept in all three)."""
    conservative = [_likeliest(g, CONSERVATIVE_MIN_ODDS) or _likeliest(g, SAFE_MIN_ODDS) or max(g, key=lambda o: o.prob)
                    for g in groups]
    target = max(booked_total, math.prod(o.odds for o in conservative) * RISKY_OVER_CONSERVATIVE)
    risky = None
    for spread in (0.1, 0.25, 0.5):
        best = optimize(groups, max(1.01, target * (1 - spread)), target * (1 + spread), len(groups))
        if best and len(best["picks"]) == len(groups):
            risky = best["picks"]
            break
    if risky is None:
        # Too few matches or markets to reach it: each match's likeliest long price
        risky = [asdict(_likeliest(g, 2.0) or c) for g, c in zip(groups, conservative)]
    kept_odds = math.prod(k["odds"] for k in kept if k.get("odds"))
    return {
        "safe": _slip(safest + kept),
        "conservative": _slip([asdict(o) for o in conservative] + kept),
        "risky": {**_slip(risky + kept), "target_odds": round(target * kept_odds, 2)},
    }
