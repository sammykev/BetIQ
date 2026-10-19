"""
Every sport's markets ranked by how often their picks came in, from the
match days stored in Redis (settled pre-match snapshots, not a backtest).

- football: every outcome the model rated ≥ 50% (market_accuracy.settle);
- basketball, tennis, table tennis: our tip (winner), the total line we
  tipped and the best line we showed, the best line grouped by its market.

Where a pick carries the price it was shown at, the flat-stake return is
given too: a high hit rate at short odds can still lose money.

    python market_ranking.py              # last 30 days, markets with ≥ 20 picks
    python market_ranking.py --days 60 --min 50

The server runs report() nightly at midnight (Lagos) for the admin panel
(jobs/reviews.py, betiq:market_ranking).
"""

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

Row = Tuple[str, str, float, bool, Optional[float]]     # sport, market, prob, won, odds

SPORTS = {"basketball": "betiq:bbmd:{}", "tennis": "betiq:tennis:md:{}", "table_tennis": "betiq:table_tennis:md:{}"}
FOOTBALL_KEY = "betiq:md:{}"

_PARTS = (("bb_h1_", "1st half"), ("bb_h2_", "2nd half"), ("bb_q1_", "1st quarter"), ("bb_q2_", "2nd quarter"),
          ("bb_q3_", "3rd quarter"), ("bb_q4_", "4th quarter"), ("rk_s1_", "1st set"), ("rk_s2_", "2nd set"))
_LINES = {"winner": "winner", "1x2": "1X2", "handicap": "handicap", "home_total": "home team points",
          "away_total": "away team points", "set_handicap": "set handicap", "games_handicap": "games handicap",
          "total_games": "total games", "home_games": "home player games", "away_games": "away player games",
          "correct_score": "correct score", "total_sets": "total sets", "odd_even": "odd/even"}


def line_name(sport: str, market: str) -> str:
    """bb_h1_home_total → "1st half home team points"; table tennis plays
    games of points where tennis plays sets of games."""
    part, rest = "", market
    for prefix, label in _PARTS:
        if market.startswith(prefix):
            part, rest = label, market[len(prefix):]
            break
    else:
        rest = market.split("_", 1)[1] if market.startswith(("bb_", "rk_")) else market
    if rest == "total":
        line = "total points" if sport == "basketball" else "total games"
    else:
        line = _LINES.get(rest, rest.replace("_", " "))
    name = f"{part} {line}".strip()
    if sport == "table_tennis":
        name = name.replace("games", "points").replace("set", "game")
    return name[:1].upper() + name[1:]


def _days(r, key: str, dates: List[str]) -> List[Dict]:
    out = []
    for raw in r.mget([key.format(d) for d in dates]):
        try:
            out += list(json.loads(raw).values()) if raw else []
        except Exception:
            pass
    return out


def football_rows(entries: List[Dict]) -> List[Row]:
    import market_accuracy
    import optimizer
    return [("football", optimizer.MARKET_NAMES.get(m, m), p, won, None)
            for e in entries for m, _code, p, won in market_accuracy.settle(e)]


def sport_rows(sport: str, entries: List[Dict]) -> List[Row]:
    import sport_accuracy
    names = sport_accuracy.NAMES.get(sport, sport_accuracy.NAMES["tennis"])
    rows = []
    for e in entries:
        for g, x in (e.get("grades") or {}).items():
            if x.get("verdict") not in ("won", "lost") or not isinstance(x.get("prob"), (int, float)):
                continue
            name = names.get(g, g)
            if g == "best":
                market = ((e.get("pred") or {}).get("best") or {}).get("market") or ""
                name = f"Best line: {line_name(sport, market)}" if market else name
            odds = x.get("odds") if isinstance(x.get("odds"), (int, float)) else None
            rows.append((sport, name, float(x["prob"]), x["verdict"] == "won", odds))
    return rows


def rank(rows: List[Row], min_n: int) -> List[Dict]:
    groups: Dict[Tuple[str, str], List[Row]] = {}
    for r in rows:
        groups.setdefault((r[0], r[1]), []).append(r)
    out = []
    for (sport, market), rs in groups.items():
        if len(rs) < min_n:
            continue
        priced = [r for r in rs if r[4] and r[4] > 1]
        out.append({"sport": sport, "market": market, "n": len(rs),
                    "hit_rate": sum(r[3] for r in rs) / len(rs),
                    "said": sum(r[2] for r in rs) / len(rs),
                    "roi": (sum(r[4] - 1 if r[3] else -1 for r in priced) / len(priced)) if len(priced) >= min_n else None})
    return sorted(out, key=lambda x: -x["hit_rate"])


def summary(rows: List[Row], markets: List[Dict], before: Optional[Dict] = None) -> Dict:
    """The ranked markets and each sport's totals; with the previous report,
    each market's hit rate then (for the panel's change column)."""
    then = {(m["sport"], m["market"]): m["hit_rate"] for m in (before or {}).get("markets") or []}
    by_sport: Dict[str, List[Row]] = {}
    for x in rows:
        by_sport.setdefault(x[0], []).append(x)
    return {"markets": [{**m, "before": then.get((m["sport"], m["market"]))} for m in markets],
            "sports": [{"sport": s, "n": len(rs), "hit_rate": sum(x[3] for x in rs) / len(rs),
                        "said": sum(x[2] for x in rs) / len(rs)} for s, rs in sorted(by_sport.items())]}


def report(r, days: int = 60, min_n: int = 10, before: Optional[Dict] = None) -> Dict:
    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(days, -1, -1)]
    rows = football_rows(_days(r, FOOTBALL_KEY, dates))
    for sport, key in SPORTS.items():
        rows += sport_rows(sport, _days(r, key, dates))
    return {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "days": days, "min": min_n,
            **summary(rows, rank(rows, min_n), before)}


def main() -> None:
    import model_store
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min", type=int, default=20)
    args = ap.parse_args()
    r = model_store._client()
    if r is None:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    rep = report(r, args.days, args.min)
    print(f"Last {args.days} days, markets with at least {args.min} settled picks\n")
    print(f"{'#':>3}  {'Sport':<13}{'Market':<40}{'Picks':>7}{'Hit':>8}{'Said':>8}{'ROI':>8}")
    for i, x in enumerate(rep["markets"], 1):
        roi = f"{x['roi']:+.1%}" if x["roi"] is not None else "–"
        print(f"{i:>3}  {x['sport']:<13}{x['market'][:39]:<40}{x['n']:>7}{x['hit_rate']:>8.1%}{x['said']:>8.1%}{roi:>8}")
    print("\nAll picks by sport")
    for x in rep["sports"]:
        print(f"  {x['sport']:<13}{x['n']:>7} picks  {x['hit_rate']:.1%} hit  (model said {x['said']:.1%})")


if __name__ == "__main__":
    main()
