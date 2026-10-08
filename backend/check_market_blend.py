"""
Does each football market do better leaning on SportyBet's prices? The 1X2
check (check_football_blend.py) for every other market we price: each
settled match's complete sets (market_blend.sets — over/under at a line, a
handicap pair, BTTS, corners 1X2) with our chances and SportyBet's prices as
the match-day store kept them (price_book), blended

    chance = w * ours + (1 - w) * SportyBet's fair chance

and scored on what happened, per market: every weight on all the days, and
the weight chosen on the earlier days scored on the later ones.

A market gets a weight (saved to Redis for the server, market_blend.py) only
when the blend beats ours alone on the later days AND over all the days by
a clear margin (z ≤ MAX_Z), on at least MIN_SETS sets. Every other market
keeps the model's own chances.

    python check_market_blend.py   (GitHub Actions: "Basketball data", job market_blend; nightly)
"""

import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import check_football_blend as cfb
import fair_odds
import market_blend
import tickets

DAYS = 120
MIN_SETS = 60
MAX_Z = -1.0


def sets_of(e: Dict) -> List[Dict]:
    """A settled match's complete priced sets: {market, date, ours, odds, k (the one that won)}."""
    res = e.get("result") or {}
    if res.get("status") != "finished" or res.get("aet"):
        return []
    prices = (e.get("pred") or {}).get("prices") or []
    out = []
    for g, ix in market_blend.sets(prices).items():
        rows = [prices[i] for i in ix]
        try:
            ours = [float(r[2]) for r in rows]
            odds = [float(r[3]) for r in rows]
        except (TypeError, ValueError, IndexError):
            continue
        if not market_blend.complete(ours, odds) or min(ours) <= 0:
            continue
        status = [tickets.grade_leg(r[0], r[1], res) for r in rows]
        if status.count("won") != 1 or status.count("lost") != len(rows) - 1:
            continue     # a push, void or ungradable set
        s = sum(ours)
        out.append({"market": rows[0][0], "set": g, "date": e.get("date") or "",
                    "ours": [x / s for x in ours], "odds": odds, "k": status.index("won")})
    return out


def losses(rows: List[Dict], w: float, method: str) -> Tuple[List[float], float, int]:
    lls, brier, right = [], 0.0, 0
    for r in rows:
        q = market_blend.blend(r["ours"], r["odds"], w, method)
        k = r["k"]
        lls.append(cfb._ll(q[k]))
        brier += sum((q[i] - (i == k)) ** 2 for i in range(len(q)))
        right += max(range(len(q)), key=lambda i: q[i]) == k
    return lls, brier, right


def verdict(got: Dict) -> Optional[float]:
    """The weight to use, or None to keep ours alone."""
    if got.get("n", 0) < MIN_SETS or "holdout" not in got:
        return None
    best = float(got["best"][1:])
    h = got["holdout"]
    full = got["grid"][got["best"]]
    if best >= 1.0 or h["later_blend"]["log_loss"] >= h["later_ours"] or full.get("vs_ours_z", 0) > MAX_Z:
        return None
    return best


def run(rows: List[Dict]) -> Dict:
    by_market: Dict[str, List[Dict]] = defaultdict(list)
    for r in rows:
        by_market[r["market"]].append(r)
    report = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "checks": {}, "weights": {}}
    for market in sorted(by_market, key=lambda m: -len(by_market[m])):
        got = cfb.check(by_market[market], losses)
        report["checks"][market] = got
        w = verdict(got)
        if w is not None:
            report["weights"][market] = w
        line = f"\n{market}: {got.get('n')} sets over {got.get('days', '?')} days"
        if "note" in got:
            print(line + f" · {got['note']}")
            continue
        g = got["grid"]
        print(line + f" · ours {g['w1']['log_loss']} · best {got['best']} {g[got['best']]['log_loss']} "
                     f"(z {g[got['best']].get('vs_ours_z')}) · market alone {g['w0']['log_loss']}")
        if "holdout" in got:
            h = got["holdout"]
            print(f"   chosen on days before {h['cut']}: w{h['chosen_on_earlier']:g} → later days "
                  f"ours {h['later_ours']} vs blend {h['later_blend']['log_loss']}")
        print(f"   → {'blend at w' + format(w, 'g') if w is not None else 'keep ours'}")
    print(f"\nWeights applied: {json.dumps(report['weights']) or '{}'}")
    return report


def load(r, days: int = DAYS, today: Optional[date] = None) -> List[Dict]:
    today = today or datetime.now(timezone.utc).date()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(1, days + 1)]
    rows: List[Dict] = []
    for i in range(0, len(dates), 30):
        for raw in r.mget([f"betiq:md:{d}" for d in dates[i:i + 30]]):
            if not raw:
                continue
            try:
                day = json.loads(raw)
            except ValueError:
                continue
            for e in day.values():
                rows.extend(sets_of(e))
    return rows


def main() -> None:
    import model_store
    r = model_store._client()
    if r is None:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    rows = load(r)
    print(f"{len(rows)} settled priced sets in {len({x['market'] for x in rows})} markets "
          f"(de-vig: {fair_odds.METHOD})")
    report = run(rows)
    r.set(market_blend.REPORT_KEY, json.dumps(report))


if __name__ == "__main__":
    main()
