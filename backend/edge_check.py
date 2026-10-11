"""
How do picks do when we disagree with SportyBet? Every priced outcome kept
with a settled match (price_book), grouped by how far our chance sits above
SportyBet's fair chance (its price with the margin taken out, fair_odds.py):
picks, how often they came in, what we said and what SportyBet said. Shows
whether a big claimed edge is real or a model error. Also prints the shots
expectations behind any listed matches (WATCH). Reads only.

    python edge_check.py     (GitHub Actions: "Basketball data", job edge_check)
"""

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import fair_odds
import market_blend
import tickets

DAYS = 45
GAPS = [(-1, 0.0, "below SportyBet"), (0.0, 0.05, "0–5 pts above"), (0.05, 0.10, "5–10"), (0.10, 0.15, "10–15"),
        (0.15, 0.20, "15–20"), (0.20, 0.30, "20–30"), (0.30, 1.0, "30+ pts above")]
WATCH = ("PEC Zwolle", "Telstar")


def rows(entry):
    """(market, gap, ours, fair, won, odds) for each settled priced outcome with a full priced set."""
    res = entry.get("result") or {}
    prices = (entry.get("pred") or {}).get("prices") or []
    out = []
    for g, ix in market_blend.sets(prices).items():
        rs = [prices[i] for i in ix]
        try:
            odds = [float(r[3]) for r in rs]
        except (TypeError, ValueError):
            continue
        if not all(o > 1 for o in odds):
            continue
        fair = fair_odds.fair(odds)
        for r, f, o in zip(rs, fair, odds):
            st = tickets.grade_leg(r[0], r[1], res)
            if st in ("won", "lost") and isinstance(r[2], (int, float)):
                out.append((r[0], float(r[2]) - f, float(r[2]), f, st == "won", o))
    return out


def main():
    import redis
    r = redis.from_url(os.environ["UPSTASH_REDIS_URL"], decode_responses=True)
    today = datetime.now(timezone.utc).date()
    all_rows = []
    for i in range(1, DAYS + 1):
        raw = r.get(f"betiq:md:{(today - timedelta(days=i)).isoformat()}")
        for e in (json.loads(raw).values() if raw else []):
            if (e.get("result") or {}).get("status") == "finished":
                all_rows += rows(e)
    print(f"{len(all_rows)} settled outcomes with SportyBet's full priced set, last {DAYS} days\n")

    def show(title, groups):
        print(title)
        print(f"  {'':22} {'picks':>6} {'came in':>8} {'we said':>8} {'SportyBet':>9} {'ROI':>7}")
        for k, rs in groups:
            if not rs:
                continue
            n = len(rs)
            roi = sum((o - 1) if w else -1 for *_, w, o in rs) / n
            print(f"  {k:22} {n:>6} {sum(x[4] for x in rs) / n:>8.1%} {sum(x[2] for x in rs) / n:>8.1%} "
                  f"{sum(x[3] for x in rs) / n:>9.1%} {roi:>7.1%}")
    show("Our chance minus SportyBet's fair chance (all markets):",
         [(lab, [x for x in all_rows if lo <= x[1] < hi]) for lo, hi, lab in GAPS])
    by = defaultdict(list)
    for x in all_rows:
        if x[1] >= 0.15:
            by[x[0]].append(x)
    show("\nPicks 15+ points above SportyBet, by market:", sorted(by.items(), key=lambda kv: -len(kv[1])))

    raw = r.get("betiq:predictions")
    preds = (json.loads(raw) or {}).get("predictions", []) if raw else []
    print("\nShots expectations for watched matches:")
    for p in preds:
        if any(w in (p.get("home") or "") for w in WATCH):
            sp = p.get("set_pieces") or {}
            print(f"  {p.get('home')} v {p.get('away')} [{p.get('league')}] shots: "
                  f"{json.dumps({k: (sp.get(k) or {}).get('mean') for k in ('shots', 'shots_home', 'shots_away', 'sot')})} "
                  f"source: {json.dumps({k: (sp.get(k) or {}).get('source') for k in ('shots',)})}")
            print(f"    shots over: {json.dumps((sp.get('shots') or {}).get('over'))}")


if __name__ == "__main__":
    main()
