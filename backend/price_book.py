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
           for o in optimizer.candidates(pred, event, min_prob=0.0, calibrate=False) if o.odds_source == "sportybet"]
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


# ── closing-line value ───────────────────────────────────────────────────
# A pick's real test is the price it beat: if SportyBet's odds for it have
# dropped by kick-off, the market moved towards the model. Over hundreds of
# picks, beating the closing price is the best early sign of an edge (results
# take far longer to settle the question). Each outcome is noted at the first
# price it became a pick at (model ≥ 50%, or priced above the model's fair
# odds); the locked snapshot's prices are the close.
CONF_BANDS = [(0.0, 0.5, "below 50%"), (0.5, 0.6, "50–60%"), (0.6, 0.7, "60–70%"), (0.7, 0.8, "70–80%"),
              (0.8, 1.01, "80%+")]


def note_open(entry: Dict, prices: Optional[List[List[Any]]], at: str) -> bool:
    """Keep in entry["open"] the first price each outcome became a pick at.
    True if anything was added."""
    if not prices:
        return False
    opened = entry.setdefault("open", {})
    added = False
    for market, code, prob, odds in prices:
        k = f"{market}|{code}"
        if k in opened or not prob or not odds or odds <= 1:
            continue
        if prob >= 0.5 or prob * odds - 1 >= 0:
            opened[k] = [odds, round(prob, 4), at]
            added = True
    if not opened:
        entry.pop("open")
    return added


def _clv_stats(rows: List[Dict]) -> Dict[str, Any]:
    n = len(rows)
    if not n:
        return {"picks": 0}
    clv = sorted(r["clv"] for r in rows)
    return {"picks": n, "avg_clv": round(sum(clv) / n, 4), "median_clv": round(clv[n // 2], 4),
            "beat": round(sum(r["clv"] > 0 for r in rows) / n, 3),
            "worse": round(sum(r["clv"] < 0 for r in rows) / n, 3)}


def clv_rows(entry: Dict) -> List[Dict[str, Any]]:
    """Each noted pick of a locked match with its opening and closing odds."""
    if not entry.get("locked") or not entry.get("open"):
        return []
    close = {f"{m}|{c}": o for m, c, _, o in (entry.get("pred") or {}).get("prices") or []}
    out = []
    last = (entry.get("snap_at") or "")[:16]
    for k, (odds, prob, at) in entry["open"].items():
        shut = close.get(k)
        if not shut or shut <= 1:
            continue  # pulled before kick-off: no close to compare with
        if at >= last:
            continue  # first a pick in the closing snapshot itself: nothing moved in between
        out.append({"market": k.split("|", 1)[0], "league": entry.get("league") or "?",
                    "prob": prob, "open": odds, "close": shut, "clv": odds / shut - 1,
                    "value": prob * odds - 1 >= MIN_EV})
    return out


def clv_report(entries: Iterable[Dict]) -> Dict[str, Any]:
    """Closing-line value of the model's picks: overall, for value picks, and
    by market, league, confidence and claimed edge."""
    rows = [r for e in entries for r in clv_rows(e)]

    def by(key) -> List[Dict[str, Any]]:
        groups: Dict[str, List[Dict]] = {}
        for r in rows:
            groups.setdefault(key(r), []).append(r)
        return sorted(({"name": g, **_clv_stats(rs)} for g, rs in groups.items()), key=lambda x: -x["picks"])

    def band(r):
        return next(label for lo, hi, label in CONF_BANDS if lo <= r["prob"] < hi)

    def edge(r):
        ev = r["prob"] * r["open"] - 1
        return next(label for lo, hi, label in EV_BUCKETS if lo <= ev < hi)

    return {
        "sport": "football",
        "all": _clv_stats(rows),
        "value": _clv_stats([r for r in rows if r["value"]]),
        "min_ev": MIN_EV,
        "by_market": by(lambda r: r["market"]),
        "by_league": [g for g in by(lambda r: r["league"]) if g["picks"] >= 20][:25],
        "by_confidence": sorted(by(band), key=lambda g: [b[2] for b in CONF_BANDS].index(g["name"])),
        "by_edge": sorted(by(edge), key=lambda g: [b[2] for b in EV_BUCKETS].index(g["name"])),
    }
