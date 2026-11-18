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
- three tickets to book (the "Refine" button on the site), each the
  highest odds whose chance of every leg winning is at least:
      safe 50%, conservative 25%, risky 12.5%
  digging into every market we price on each game, and keeping as many of
  the code's games as that chance allows (the rest are listed as left out).

Every leg gets a chance: our model's where we price the match (football,
basketball, tennis, table tennis: matched by SportyBet's event), else
SportyBet's own odds less their margin (ODDS_MARGIN / odds). A model chance
counts at no more than its price's (routers.optimizer.price_checked: where
we rated legs well over their price they came in far under). Every pick is
booked by SportyBet's own ids.
"""

import json
import math
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import booking_slip
from optimizer import _PICKS, Option, candidates, optimize_safe

STRONG, FAIR = 0.75, 0.55
BETTER_BY = 0.08          # a suggestion must beat the leg by this much
# Each ticket's least chance of every leg winning (Nov 2026: halved each step)
TICKETS = (("safe", 0.50), ("conservative", 0.25), ("risky", 0.125))
MIN_OPTION_PROB = 0.30    # options looked at on each game (a risky ticket takes longer prices)
ODDS_MARGIN = 0.95        # a leg we don't price: chance ≈ ODDS_MARGIN / its odds
PRICE_MARGIN = 1.05       # a model chance counts at no more than PRICE_MARGIN / its odds
MAX_TICKET_ODDS = 1e6


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


def odds_chance(odds: Optional[float]) -> Optional[float]:
    """A leg's chance from SportyBet's price alone, less its margin."""
    return min(0.97, ODDS_MARGIN / odds) if odds and odds > 1 else None


def _capped(prob: float, odds: Optional[float]) -> float:
    """A model chance counted at no more than its price says."""
    return min(prob, PRICE_MARGIN / odds) if odds and odds > 1 else prob


def _ids(sel: Dict) -> Dict[str, str]:
    return {k: sel[k] for k in ("eventId", "marketId", "specifier", "outcomeId")}


def _same_outcome(o: Option, sel: Dict) -> bool:
    sb = o.sb or {}
    return (str(sb.get("marketId")), str(sb.get("specifier") or ""), str(sb.get("outcomeId"))) == \
        (str(sel["marketId"]), str(sel.get("specifier") or ""), str(sel["outcomeId"]))


def _slip(picks: List[Dict[str, Any]]) -> Dict[str, Any]:
    known = [p["prob"] for p in picks if p.get("prob") is not None]
    odds = [p["odds"] for p in picks if p.get("odds")]
    return {"picks": picks, "games": len(picks), "total_odds": round(math.prod(odds), 2) if odds else None,
            "win_chance": round(math.prod(known), 4) if known else None,
            "from_odds": sum(1 for p in picks if p.get("odds_source") == "odds_only"),
            # older clients read these
            "unmodelled": sum(1 for p in picks if p.get("odds_source") == "odds_only"),
            "estimated_prices": sum(1 for p in picks if p.get("odds_source") == "estimated")}


def analyse(selections: List[Dict], find_pred: Callable[[Dict], Optional[Dict]],
            event_for: Callable[[Dict], Optional[Dict]], confirmed: Dict[str, str],
            allowed: Optional[Callable[[str, str], bool]] = None,
            find_other: Optional[Callable[[Dict], Optional[Dict]]] = None,
            adjust: Optional[Callable[[Option], Optional[Option]]] = None) -> Dict[str, Any]:
    """Legs with verdicts and suggestions, the code as it stands, and the
    three tickets. `find_pred(sel)` → our football prediction for its match;
    `find_other(sel)` → our basketball / tennis / table tennis prediction for
    its SportyBet event; `event_for(pred)` → a football match's linked
    SportyBet event (prices); `adjust(option)` → the option as the optimizer
    would count it, or None to leave it out (held-out markets, the price check)."""
    legs, groups, chances = [], [], []
    for sel in selections:
        date, time = _when(sel.get("start"))
        odds = sel.get("odds")
        pred = find_pred(sel)
        other = find_other(sel) if find_other and not pred else None
        prob: Optional[float] = None
        options: List[Option] = []
        own_market, own_code, sport = "sportybet", str(sel["outcomeId"]), "football"
        if pred and pred.get("sport") in (None, "football"):
            pick = model_pick(pred, sel, confirmed)
            options = candidates(pred, event_for(pred), MIN_OPTION_PROB, None, allowed)
            if pick:
                own_market, own_code = pick["market"], pick["code"]
                same = next((o for o in options if (o.market, o.code) == (own_market, own_code)), None)
                prob = same.prob if same else pick["prob"]       # through calibration where the optimizer has it
                options = [o for o in options if (o.market, o.code) != (own_market, own_code)]
        elif pred or other:
            import basketball_predictions
            import racket_predictions
            p = pred or other
            sport = p.get("sport") or "basketball"
            mod = basketball_predictions if sport == "basketball" else racket_predictions
            options = mod.options(p, MIN_OPTION_PROB) if (p.get("bb_markets") or p.get("rk_markets")) else []
            same = next((o for o in options if _same_outcome(o, sel)), None)
            if same:
                prob, own_market, own_code = same.prob, same.market, same.code
            elif not options:
                pick = model_pick(p, sel, confirmed)              # tennis winner from a bare prediction
                if pick:
                    prob, own_market, own_code = pick["prob"], pick["market"], pick["code"]
            options = [o for o in options if not _same_outcome(o, sel)]
            pred = p
        if adjust:
            options = [c for o in options for c in [adjust(o)] if c is not None]
        suggestable = options
        # On a ticket: SportyBet's price only (bookable), under the code's own names and kick-off
        options = [replace(o, home=sel["home"], away=sel["away"], date=date or o.date, time=time or o.time)
                   for o in options if o.odds_source == "sportybet"]
        chance = _capped(prob, odds) if prob is not None else odds_chance(odds)
        leg = {"home": sel["home"], "away": sel["away"], "date": date, "time": time,
               "league": (pred or {}).get("league_name") or sel.get("tournament", ""),
               "market": sel["market"], "pick": sel["outcome"], "odds": odds, "active": sel.get("active", True),
               "our_prob": round(prob, 3) if prob is not None else None,
               "chance": round(chance, 3) if chance is not None else None,
               "chance_from": "model" if prob is not None else "odds",
               "implied": round(1 / odds, 3) if odds else None,
               "value": round(prob * odds - 1, 3) if prob is not None and odds else None,
               "verdict": _verdict(prob), "matched": pred is not None, "suggestion": None, "sb": _ids(sel)}
        if not pred:
            leg["note"] = "We don't predict this match: its chance is from SportyBet's odds"
        elif prob is None:
            leg["note"] = "We don't price this market: its chance is from SportyBet's odds"
        better = max((o for o in suggestable if o.odds >= 1.2 and o.prob >= (prob or chance or 0) + BETTER_BY),
                     key=lambda o: (o.prob, o.odds), default=None)
        if better:
            leg["suggestion"] = {"market_name": better.market_name, "label": better.label,
                                 "prob": better.prob, "odds": better.odds, "odds_source": better.odds_source}
        legs.append(leg)
        chances.append(chance)
        if chance is None or not odds or odds <= 1.01 or not leg["active"]:
            continue                       # suspended or unpriced: can't go on a ticket
        own = Option(sel["home"], sel["away"], date, time, leg["league"], own_market, sel["market"], own_code,
                     sel["outcome"], round(chance, 4), round(float(odds), 2),
                     "sportybet" if prob is not None else "odds_only", sb=_ids(sel), sport=sport)
        groups.append([own] + options)

    known = [c for c in chances if c is not None]
    odds_all = [l["odds"] for l in legs if l["odds"]]
    report = {"legs": legs,
              "original": {"games": len(legs), "total_odds": round(math.prod(odds_all), 2) if odds_all else None,
                           "win_chance": round(math.prod(known), 4) if known else None,
                           "from_odds": sum(1 for l in legs if l["chance_from"] == "odds"),
                           "unmodelled": sum(1 for l in legs if l["chance_from"] == "odds")},
              "same_odds": None, "safest": None, "tickets": _tickets(groups) if groups else None}
    return report


def _tickets(groups: List[List[Option]]) -> Dict[str, Optional[Dict[str, Any]]]:
    """Each ticket: the highest odds at its least chance, on as many of the
    code's games as that chance allows (None when not even one leg is that
    likely)."""
    out: Dict[str, Optional[Dict[str, Any]]] = {}
    for name, least in TICKETS:
        res = optimize_safe(groups, least, MAX_TICKET_ODDS, len(groups), min_edge=None, most_games=True)
        if not res:
            out[name] = None
            continue
        on = {(p["home"], p["away"]) for p in res["picks"]}
        left = [f"{g[0].home} v {g[0].away}" for g in groups if (g[0].home, g[0].away) not in on]
        out[name] = {**_slip(res["picks"]), "target_chance": least, "left_out": left}
    return out


# ── how the refined tickets do ────────────────────────────────────────────
# Every ticket the checker refines is kept (RECORD_KEY, apart from the
# accounts' tickets so the market ranking and calibration don't count them)
# and settled by the ticket job (settle_record), so each tier's record shows
# whether its chance is honest (safe aims at 50%: does it land half the time?).
RECORD_KEY = "betiq:codecheck:tickets"
RECORD_MAX = 3000


def record_tickets(r, code: str, report: Dict[str, Any], now: str) -> int:
    """Keep the report's three tickets (a code checked again replaces its
    tickets from before). Returns how many were kept."""
    import tickets as tk
    if r is None or not report.get("tickets"):
        return 0
    key = RECORD_KEY
    try:
        items = json.loads(r.get(key) or "[]")
    except ValueError:
        items = []
    items = [t for t in items if t.get("checked_code") != code or t.get("status") != "pending"]
    kept = 0
    for tier, least in TICKETS:
        t = report["tickets"].get(tier)
        if not t or not t.get("picks"):
            continue
        sels = [{**p, "marketName": p.get("market_name")} for p in t["picks"]]
        ticket = tk.new_ticket(f"{code}:{tier}", sels, [{"status": "booked", "odds": p.get("odds")} for p in t["picks"]],
                               "code_check", None, t.get("total_odds"), now)
        ticket.update(tier=tier, target_chance=least, said=t.get("win_chance"), checked_code=code)
        items.insert(0, ticket)
        kept += 1
    r.set(key, json.dumps(items[:RECORD_MAX], separators=(",", ":")), ex=365 * 86400)
    return kept


def settle_record(r, result_for: Callable[[Dict], Optional[Dict]]) -> int:
    """Settle the kept tickets' legs from the results (the ticket job's
    result_for). Returns how many tickets were settled."""
    import tickets as tk
    if r is None:
        return 0
    try:
        items = json.loads(r.get(RECORD_KEY) or "[]")
    except ValueError:
        return 0
    settled, changed = 0, False
    for t in items:
        if t.get("status") not in ("pending", "open"):
            continue
        was = t.get("status")
        if tk.settle(t, result_for):
            changed = True
            settled += t.get("status") != was and t.get("status") in ("won", "lost", "void")
    if changed:
        r.set(RECORD_KEY, json.dumps(items, separators=(",", ":")), ex=365 * 86400)
    return settled


def record(r) -> Dict[str, Dict[str, Any]]:
    """Per tier: tickets landed and lost, against the chance they were given."""
    out = {tier: {"aim": least, "landed": 0, "lost": 0, "open": 0, "said": 0.0} for tier, least in TICKETS}
    try:
        items = json.loads(r.get(RECORD_KEY) or "[]") if r is not None else []
    except ValueError:
        items = []
    for t in items:
        x = out.get(t.get("tier"))
        if not x:
            continue
        if t.get("status") in ("won", "lost"):
            x["landed" if t["status"] == "won" else "lost"] += 1
            x["said"] += float(t.get("said") or x["aim"])
        else:
            x["open"] += 1
    for x in out.values():
        n = x["landed"] + x["lost"]
        x["said"] = round(x["said"] / n, 4) if n else None
        x["rate"] = round(x["landed"] / n, 4) if n else None
    return out
