"""
How often our high-confidence picks really win — by sport, market and
league — and how often a ticket of the N most confident picks of a day
would have landed every leg.

Settled picks come from the graded history (calibration.load_rows, the
last DAYS days). Each pick is judged at the chance we said raw and at the
chance the live calibration maps turn it into (what tickets now use).

For every cell: picks, matches, hit rate, what we said, the gap, z (the
shortfall in standard errors, picks of one match grouped — market_review)
and the Wilson 95% lower bound on the hit rate. A cell is "proven" at a
level when it has at least MIN_PROVEN picks rated there and its lower
bound clears that level: we can say with confidence those picks win at
least that often.

Tickets: for each day, every pick rated ≥ 80% (corrected), one per match
(its most confident), the top N taken; how many days had N such picks and
how many of those landed all N. "proven only" keeps just the picks in
proven market cells — picked from the same history, so read it as an
upper bound until later days confirm it.

    python confidence_check.py     (GitHub Actions: "Confidence check")
"""

import json
import math
from collections import defaultdict
from typing import Callable, Dict, List, Optional

import calibration
import market_review

REPORT_KEY = "betiq:confidence_check"
DAYS = 120
BANDS = ((0.80, 0.85), (0.85, 0.90), (0.90, 0.93), (0.93, 0.95), (0.95, 0.97), (0.97, 1.01))
LEVELS = (0.80, 0.90, 0.95)
MIN_PROVEN = 30
MIN_CELL = 10
TICKET_SIZES = (3, 5, 10, 20, 30)
TICKET_FLOOR = 0.80


def band_of(p: float) -> Optional[str]:
    for lo, hi in BANDS:
        if lo <= p < hi:
            return f"{round(lo * 100)}–{min(round(hi * 100), 100)}%"
    return None


def wilson_low(won: int, n: int, z: float = 1.96) -> Optional[float]:
    if not n:
        return None
    p = won / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round((c - m) / d, 3)


def cell(rows: List[Dict], key: str = "cal") -> Dict:
    """Picks, matches, hit rate, said, gap, z and the Wilson lower bound."""
    ids: Dict[str, int] = {}
    st = market_review.check([(ids.setdefault(r["match"], len(ids)), r[key], r["won"]) for r in rows])
    won = sum(1 for r in rows if r["won"])
    return {**st, "low95": wilson_low(won, len(rows))}


def annotate(rows: List[Dict], maps: Dict) -> List[Dict]:
    """Each row with its corrected chance ("cal")."""
    return [{**r, "cal": calibration.apply(r["sport"], r["market"], r["prob"], maps)} for r in rows]


def _by(rows: List[Dict], how: Callable[[Dict], Optional[str]]) -> Dict[str, List[Dict]]:
    out: Dict[str, List[Dict]] = defaultdict(list)
    for r in rows:
        k = how(r)
        if k is not None:
            out[k].append(r)
    return out


def bands(rows: List[Dict], key: str) -> List[Dict]:
    g = _by(rows, lambda r: band_of(r[key]))
    return [{"band": b, **cell(g[b], key)} for b in (band_of(lo) for lo, _ in BANDS) if g.get(b)]


def table(rows: List[Dict], how: Callable[[Dict], str], key: str = "cal") -> List[Dict]:
    """Cells of picks rated ≥ 80%, split by how(row), with the ≥ 90% and
    ≥ 95% picks of each cell on their own."""
    out = []
    for k, rs in _by([r for r in rows if r[key] >= LEVELS[0]], how).items():
        if len(rs) < MIN_CELL:
            continue
        c = {"cell": k, **cell(rs, key)}
        for lv in LEVELS[1:]:
            top = [r for r in rs if r[key] >= lv]
            c[f"at{round(lv * 100)}"] = cell(top, key) if top else None
        c["proven"] = proven(rs, key)
        out.append(c)
    return sorted(out, key=lambda c: -c["picks"])


def proven(rows: List[Dict], key: str = "cal") -> Optional[float]:
    """The highest level whose picks (≥ MIN_PROVEN of them) win at least
    that often with 95% confidence."""
    best = None
    for lv in LEVELS:
        top = [r for r in rows if r[key] >= lv]
        if len(top) >= MIN_PROVEN and wilson_low(sum(r["won"] for r in top), len(top)) >= lv:
            best = lv
    return best


def market_key(r: Dict) -> str:
    return f"{r['sport']}:{r['market']}"


def league_key(r: Dict) -> str:
    return f"{r['sport']}:{r.get('league') or '?'}"


def tickets(rows: List[Dict], sizes=TICKET_SIZES, keep: Optional[Callable[[Dict], bool]] = None) -> List[Dict]:
    """For each day, the top-N most confident picks (one per match, ≥ TICKET_FLOOR):
    days with N picks, days all N won, and the chance we said on average."""
    days: Dict[str, Dict[str, Dict]] = defaultdict(dict)
    for r in rows:
        if r["cal"] < TICKET_FLOOR or (keep and not keep(r)):
            continue
        best = days[r["date"]].get(r["match"])
        if best is None or r["cal"] > best["cal"]:
            days[r["date"]][r["match"]] = r
    out = []
    for n in sizes:
        played = landed = 0
        said = 0.0
        legs_won = legs = 0
        for picks in days.values():
            top = sorted(picks.values(), key=lambda r: -r["cal"])[:n]
            if len(top) < n:
                continue
            played += 1
            landed += all(r["won"] for r in top)
            said += math.prod(r["cal"] for r in top)
            legs += n
            legs_won += sum(r["won"] for r in top)
        out.append({"legs": n, "days": played, "landed": landed,
                    "landed_rate": round(landed / played, 3) if played else None,
                    "said": round(said / played, 3) if played else None,
                    "leg_hit": round(legs_won / legs, 3) if legs else None})
    return out


def report(rows: List[Dict], maps: Dict) -> Dict:
    rows = annotate(rows, maps)
    names = {league_key(r): r.get("league_name") or r.get("league") or "?" for r in rows}
    dates = sorted(r["date"] for r in rows)
    rep = {"picks": len(rows), "days": DAYS, "first_day": dates[0] if dates else None,
           "last_day": dates[-1] if dates else None, "corrected_markets": len(maps),
           "overall": {"raw": bands(rows, "prob"), "corrected": bands(rows, "cal")},
           "sports": {}, "markets": table(rows, market_key), "leagues": table(rows, league_key),
           "league_names": names}
    for s in calibration.SPORTS:
        rs = [r for r in rows if r["sport"] == s]
        if rs:
            rep["sports"][s] = {"raw": bands(rs, "prob"), "corrected": bands(rs, "cal")}
    good = {c["cell"] for c in rep["markets"] if c["proven"] and c["proven"] >= 0.9}
    rep["proven_markets"] = sorted(good)
    rep["tickets"] = {"all": tickets(rows),
                      "proven_only": tickets(rows, keep=lambda r: market_key(r) in good) if good else []}
    return rep


def _pct(x) -> str:
    return calibration._pct(x)


def _line(c: Dict) -> str:
    gap = "—" if c["gap"] is None else f"{100 * c['gap']:+.1f}"
    return (f"n {c['picks']:>5} ({c['matches']:>4} m) hit {_pct(c['hit_rate']):>6} said {_pct(c['model_said']):>6} "
            f"gap {gap:>6} z {c['z']:>6} low95 {_pct(c['low95']):>6}")


def print_report(rep: Dict) -> None:
    print(f"{rep['picks']} settled picks ({rep['first_day']} → {rep['last_day']}), "
          f"{rep['corrected_markets']} markets with a live correction\n")
    for title, block in [("ALL SPORTS", rep["overall"])] + [(s.upper(), v) for s, v in rep["sports"].items()]:
        print(f"== {title}: by the chance we said ==")
        for key in ("raw", "corrected"):
            print(f"  {key}")
            for b in block[key]:
                print(f"    {b['band']:<9} {_line(b)}")
        print()
    for title, cells in (("MARKETS", rep["markets"]), ("LEAGUES", rep["leagues"])):
        print(f"== {title}: picks rated ≥ 80% (corrected), and of those ≥ 90% / ≥ 95% ==")
        for c in cells:
            label = c["cell"] if title == "MARKETS" else f"{c['cell']} {rep['league_names'].get(c['cell'], '')}"
            print(f"  {label[:44]:<44} {_line(c)} proven {_pct(c['proven'])}")
            for lv in ("at90", "at95"):
                if c.get(lv):
                    print(f"      {lv.replace('at', '≥') + '%':<6} {_line(c[lv])}")
        print()
    print("== PROVEN at ≥ 90% (Wilson lower bound clears 90% on ≥ "
          f"{MIN_PROVEN} picks) ==\n  " + (", ".join(rep["proven_markets"]) or "none"))
    for name, rows in rep["tickets"].items():
        print(f"\n== TICKETS ({name}): the day's top-N picks, one per match ==")
        for t in rows:
            print(f"  {t['legs']:>2} legs: {t['days']:>3} days, all won {t['landed']:>3} "
                  f"({_pct(t['landed_rate'])}), we said {_pct(t['said'])}, legs won {_pct(t['leg_hit'])}")


def main() -> None:
    import model_store
    r = model_store._client()
    if r is None:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    rep = report(calibration.load_rows(r, days=DAYS), calibration.load(r))
    print_report(rep)
    r.set(REPORT_KEY, json.dumps(rep))


if __name__ == "__main__":
    main()
