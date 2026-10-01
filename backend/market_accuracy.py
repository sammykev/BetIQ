"""
How often each market's picks come in: every outcome the model rated at
least 50% likely before kick-off, settled at full time.

Each match's pre-match snapshot (matchday.py) keeps those picks compactly as
{market: {code: prob}} (`picks`, from optimizer.candidates). Snapshots from
before that field existed still give the markets their headline numbers
cover — result, double chance, goal lines, both teams to score, corners
9.5 and bookings 4.5.

report() groups the settled picks by market and by confidence band: how
many, how many won, and what the model said on average. A well-calibrated
market's hit rate sits close to what the model said.
"""

from typing import Any, Dict, Iterable, List, Optional, Tuple

import optimizer
import tickets

MIN_PROB = 0.5
BANDS = [(0.5, 0.6, "50–60%"), (0.6, 0.7, "60–70%"), (0.7, 0.8, "70–80%"), (0.8, 1.01, "80%+")]
# Snapshot fields → the markets they cover (older snapshots)
_SET_PIECE_FIELDS = {"corners_over": ("corners_ou", "95"), "bookings_over": ("cards_ou", "45")}


def picks_of(pred: Dict) -> Dict[str, Dict[str, float]]:
    """{market: {code: prob}} for every option the model rates ≥ MIN_PROB."""
    out: Dict[str, Dict[str, float]] = {}
    for o in optimizer.candidates(pred, None, min_prob=MIN_PROB, calibrate=False):
        out.setdefault(o.market, {})[o.code] = round(o.prob, 3)
    return out


def _entry_picks(entry: Dict) -> Dict[str, Dict[str, float]]:
    snap = entry.get("pred") or {}
    if snap.get("picks"):
        return snap["picks"]
    picks = picks_of({**snap, "home": entry.get("home", ""), "away": entry.get("away", ""),
                      "date": entry.get("date", "")})
    for field, (market, line) in _SET_PIECE_FIELDS.items():
        p = snap.get(field)
        if isinstance(p, (int, float)):
            code, prob = (f"O{line}", p) if p >= 0.5 else (f"U{line}", 1 - p)
            picks.setdefault(market, {})[code] = round(prob, 3)
    return picks


def settle(entry: Dict) -> List[Tuple[str, str, float, bool]]:
    """(market, code, prob, won) for a finished match's picks."""
    res = entry.get("result") or {}
    out = []
    for market, codes in _entry_picks(entry).items():
        for code, prob in codes.items():
            status = tickets.grade_leg(market, code, res)
            if status in ("won", "lost"):
                out.append((market, code, prob, status == "won"))
    return out


def _row(rows: List[Tuple[str, str, float, bool]]) -> Dict[str, Any]:
    n = len(rows)
    won = sum(1 for r in rows if r[3])
    said = sum(r[2] for r in rows) / n if n else None
    return {"picks": n, "won": won, "hit_rate": round(won / n, 3) if n else None,
            "model_said": round(said, 3) if said is not None else None,
            "gap": round(won / n - said, 3) if n else None}


def report(entries: Iterable[Dict]) -> Dict[str, Any]:
    rows = []
    matches = 0
    for e in entries:
        s = settle(e)
        if s:
            matches += 1
            rows += s
    by_market: Dict[str, List] = {}
    for r in rows:
        by_market.setdefault(r[0], []).append(r)
    order = list(optimizer.MARKET_NAMES)
    return {
        "matches": matches, "all": _row(rows),
        "by_band": [{"band": label, **_row([r for r in rows if lo <= r[2] < hi])} for lo, hi, label in BANDS],
        "by_market": [{"market": m, "name": optimizer.MARKET_NAMES.get(m, m), **_row(rs),
                       "bands": [{"band": label, **_row([r for r in rs if lo <= r[2] < hi])} for lo, hi, label in BANDS]}
                      for m, rs in sorted(by_market.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99)],
    }
