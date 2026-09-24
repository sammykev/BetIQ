"""
Value bets priced by SportyBet: for a prediction linked to a SportyBet event
(main._link_sportybet_events), compare the model's probability for each
outcome with SportyBet's odds for it.

Implied probability = 1/odds, scaled so the market's outcomes sum to 1
(the bookmaker's margin taken out). An outcome is value when the model
rates it at least MIN_EDGE more likely than that AND the bet pays more
than it costs at the model's probability (prob × odds > 1).

Draws are left out: the model overrates them (backtest: said 34%,
happened 18%), so a "value draw" would mostly be the model's own error.
"""

from typing import Dict, List, Optional, Tuple

MIN_EDGE = 0.03
MIN_PROB = 0.25   # long shots: small probabilities, big noise


def _outcome_odds(event: Dict, market_id: str, specifier: str = "") -> Dict[str, float]:
    for m in event.get("markets") or []:
        if str(m.get("id")) == market_id and (m.get("specifier") or "") == specifier:
            out = {}
            for o in m.get("outcomes") or []:
                try:
                    odds = float(o.get("odds"))
                except (TypeError, ValueError):
                    continue
                if odds > 1 and o.get("isActive", 1):
                    out[str(o.get("id"))] = odds
            return out
    return {}


def _markets(pred: Dict) -> List[Tuple[str, str, str, Dict[str, Tuple[str, Optional[float], str]], int]]:
    """(SportyBet market id, specifier, name, {outcome id: (code, model prob, label)}, outcomes that sum to 1).
    Double chance outcomes sum to 2 (each result is in two of them)."""
    h, d, a = pred.get("p_home"), pred.get("p_draw"), pred.get("p_away")
    home, away = pred.get("home", "Home"), pred.get("away", "Away")
    out = []
    if all(isinstance(x, (int, float)) for x in (h, d, a)):
        out.append(("1", "", "Match Result", {"1": ("1", h, f"{home} Win"), "3": ("2", a, f"{away} Win"),
                                               "2": ("X", None, "Draw")}, 1))
        out.append(("10", "", "Double Chance", {"9": ("1X", h + d, "Home or Draw"), "10": ("12", h + a, "Home or Away"),
                                                 "11": ("X2", d + a, "Draw or Away")}, 2))
    for line, key in (("1.5", "p_over15"), ("2.5", "p_over25"), ("3.5", "p_over35")):
        p = pred.get(key)
        if isinstance(p, (int, float)):
            out.append(("18", f"total={line}", "Total Goals",
                        {"12": (f"O{line.replace('.', '')}", p, f"Over {line}"),
                         "13": (f"U{line.replace('.', '')}", 1 - p, f"Under {line}")}, 1))
    p = pred.get("p_btts")
    if isinstance(p, (int, float)):
        out.append(("29", "", "Both Teams to Score", {"74": ("BTTS-Y", p, "Both teams score"),
                                                      "76": ("BTTS-N", 1 - p, "Not both teams score")}, 1))
    return out


_SLIP_MARKET = {"1": "1x2", "10": "double_chance", "18": "goals_ou", "29": "btts"}


def best_value(pred: Dict, event: Optional[Dict]) -> Optional[Dict]:
    """The prediction's best value outcome at SportyBet's prices, or None."""
    if not event:
        return None
    best = None
    for market_id, spec, name, outcomes, total in _markets(pred):
        odds = _outcome_odds(event, market_id, spec)
        if len(odds) < len(outcomes):
            continue  # need the whole market to take the margin out
        booked = sum(1 / odds[o] for o in outcomes)
        for oid, (code, prob, label) in outcomes.items():
            if prob is None or prob < MIN_PROB:
                continue
            implied = (1 / odds[oid]) / booked * total
            edge = prob - implied
            if edge >= MIN_EDGE and prob * odds[oid] > 1 and (best is None or edge > best["_edge"]):
                best = {"_edge": edge, "market": _SLIP_MARKET[market_id], "market_name": name, "code": code,
                        "label": label, "odds": odds[oid], "prob": prob, "implied": implied,
                        "overround": booked / total - 1}
    if not best:
        return None
    return {
        **pred,
        "value_outcome": best["code"], "value_label": best["label"], "value_market": best["market"],
        "value_market_name": best["market_name"], "value_odds": round(best["odds"], 2),
        "model_prob": round(best["prob"] * 100, 1), "implied_prob": round(best["implied"] * 100, 1),
        "edge": round(best["_edge"] * 100, 1), "overround": round(best["overround"] * 100, 1),
        "expected_return": round((best["prob"] * best["odds"] - 1) * 100, 1),
        "bookie": "SportyBet",
    }
