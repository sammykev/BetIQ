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
"""

import argparse
import json
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

Row = Tuple[str, str, float, bool, Optional[float]]     # sport, market, prob, won, odds

SPORTS = {"basketball": "betiq:bbmd:{}", "tennis": "betiq:tennis:md:{}", "table_tennis": "betiq:table_tennis:md:{}"}
FOOTBALL_KEY = "betiq:md:{}"


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
                name = f"Best line: {market}" if market else name
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


def main() -> None:
    import model_store
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min", type=int, default=20)
    args = ap.parse_args()
    r = model_store._client()
    if r is None:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(args.days, -1, -1)]
    rows = football_rows(_days(r, FOOTBALL_KEY, dates))
    for sport, key in SPORTS.items():
        rows += sport_rows(sport, _days(r, key, dates))
    print(f"Last {args.days} days, markets with at least {args.min} settled picks\n")
    print(f"{'#':>3}  {'Sport':<13}{'Market':<40}{'Picks':>7}{'Hit':>8}{'Said':>8}{'ROI':>8}")
    for i, x in enumerate(rank(rows, args.min), 1):
        roi = f"{x['roi']:+.1%}" if x["roi"] is not None else "–"
        print(f"{i:>3}  {x['sport']:<13}{x['market'][:39]:<40}{x['n']:>7}{x['hit_rate']:>8.1%}{x['said']:>8.1%}{roi:>8}")
    by_sport: Dict[str, List[Row]] = {}
    for x in rows:
        by_sport.setdefault(x[0], []).append(x)
    print("\nAll picks by sport")
    for sport, rs in sorted(by_sport.items()):
        print(f"  {sport:<13}{len(rs):>7} picks  {sum(x[3] for x in rs) / len(rs):.1%} hit  "
              f"(model said {sum(x[2] for x in rs) / len(rs):.1%})")


if __name__ == "__main__":
    main()
