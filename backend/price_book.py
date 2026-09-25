"""
The money test, live: SportyBet's price for every market we model, kept with
each match's pre-match snapshot (matchday.py) and settled at full time.

The backtest can only measure profit against Bet365 in the big leagues'
result and goals markets, where the model can't beat the market. Whether it
beats SportyBet — in corners, bookings, goals lines, double chance — can
only be measured at SportyBet's own prices. So every prediction linked to a
SportyBet event carries, until kick-off, each modelled outcome SportyBet
prices: [market, code, model probability, odds]. The last one before
kick-off is locked with the match; report() settles them (tickets.grade_leg)
and shows the flat-stake profit by market and by the edge the model claimed.
"""

from typing import Any, Dict, Iterable, List, Optional

import optimizer
import tickets

# Expected value (p × odds − 1) buckets the report splits bets into
EV_BUCKETS = [(-1.0, 0.0, "below 0"), (0.0, 0.05, "0–5%"), (0.05, 0.10, "5–10%"), (0.10, 0.20, "10–20%"),
              (0.20, 99.0, "20%+")]
MIN_EV = 0.05   # the "value bets" line in the report


def prices(pred: Dict, event: Optional[Dict]) -> Optional[List[List[Any]]]:
    """[[market, code, prob, odds], …] for the outcomes SportyBet prices, or None."""
    if not event:
        return None
    out = [[o.market, o.code, o.prob, o.odds]
           for o in optimizer.candidates(pred, event, min_prob=0.0) if o.odds_source == "sportybet"]
    return out or None


def settle(entry: Dict) -> List[Dict[str, Any]]:
    """Each priced outcome of a finished match, with its result."""
    res = entry.get("result") or {}
    out = []
    for market, code, prob, odds in (entry.get("pred") or {}).get("prices") or []:
        status = tickets.grade_leg(market, code, res)
        if status in ("won", "lost"):
            out.append({"market": market, "code": code, "prob": prob, "odds": odds, "won": status == "won",
                        "ev": prob * odds - 1})
    return out


def _roi(bets: List[Dict]) -> Dict[str, Any]:
    n = len(bets)
    profit = sum(b["odds"] - 1 if b["won"] else -1.0 for b in bets)
    return {"bets": n, "won": sum(b["won"] for b in bets), "profit": round(profit, 2),
            "roi": round(profit / n, 4) if n else None,
            "avg_prob": round(sum(b["prob"] for b in bets) / n, 3) if n else None,
            "hit_rate": round(sum(b["won"] for b in bets) / n, 3) if n else None}


def report(entries: Iterable[Dict]) -> Dict[str, Any]:
    """Flat 1-unit bets at SportyBet's pre-match price: every priced outcome
    (≈ minus SportyBet's margin), and those the model rated as value, by
    market and by claimed edge. A market is worth betting only if its value
    bets make money over many matches."""
    bets = [b for e in entries for b in settle(e)]
    by_market: Dict[str, List[Dict]] = {}
    for b in bets:
        by_market.setdefault(b["market"], []).append(b)
    return {
        "matches": sum(1 for e in entries if (e.get("pred") or {}).get("prices")),
        "all_priced": _roi(bets),
        "value": _roi([b for b in bets if b["ev"] >= MIN_EV]),
        "min_ev": MIN_EV,
        "by_edge": [{"edge": label, **_roi([b for b in bets if lo <= b["ev"] < hi])} for lo, hi, label in EV_BUCKETS],
        "by_market": sorted(({"market": m, "all": _roi(bs), "value": _roi([b for b in bs if b["ev"] >= MIN_EV])}
                             for m, bs in by_market.items()), key=lambda r: -r["all"]["bets"]),
    }
