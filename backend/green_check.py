"""
How many greens we got when the model said FROM (75%) or more:

1. Every settled pick in the graded history (calibration.load_rows, the
   last DAYS days, one row per match, market and line), judged at the
   chance we said and at the live corrected chance: overall, by sport, by
   band, by market, by week, and the last 7 days against the rest.
2. The tickets people booked: their settled legs rated FROM+, and the
   tickets whose every leg was FROM+ (how many landed).

    python green_check.py [--from 0.75]     (GitHub Actions: "Green check")
"""

import argparse
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List

import calibration
import confidence_check

DAYS = 120
BANDS = ((0.75, 0.80), (0.80, 0.85), (0.85, 0.90), (0.90, 0.95), (0.95, 1.01))
MIN_ROW = 20


def line(rows: List[Dict], how: Callable[[Dict], float]) -> str:
    n = len(rows)
    if not n:
        return "n     0"
    won = sum(1 for r in rows if r["won"])
    said = sum(how(r) for r in rows) / n
    low = confidence_check.wilson_low(won, n)
    return (f"n {n:>6}  green {won:>6}  red {n - won:>5}  hit {100 * won / n:5.1f}%  said {100 * said:5.1f}%  "
            f"gap {100 * (won / n - said):+5.1f}  low95 {100 * (low or 0):5.1f}%")


def picks_report(rows: List[Dict], start: float) -> None:
    maps = calibration.load(_r)
    raw = lambda r: r["prob"]
    cal = lambda r: calibration.apply(r["sport"], r["market"], r["prob"], maps)
    for name, how in (("as we said it (raw)", raw), ("corrected (what tickets use)", cal)):
        top = [r for r in rows if how(r) >= start]
        days = sorted({r["date"] for r in top})
        print(f"\n== Picks rated {start:.0%}+ {name}: {days[0] if days else '-'} → {days[-1] if days else '-'} ==")
        print(f"  all            {line(top, how)}")
        for s in calibration.SPORTS:
            rs = [r for r in top if r["sport"] == s]
            if rs:
                print(f"  {s:<14} {line(rs, how)}")
        print("  by what we said:")
        for lo, hi in BANDS:
            rs = [r for r in top if lo <= how(r) < hi]
            if rs:
                print(f"    {round(lo * 100)}–{min(round(hi * 100), 100)}%{'':<6} {line(rs, how)}")
        cut = (date.today() - timedelta(days=7)).isoformat()
        print("  recent:")
        print(f"    last 7 days    {line([r for r in top if r['date'] >= cut], how)}")
        print(f"    before         {line([r for r in top if r['date'] < cut], how)}")
        print("  by week (Monday):")
        weeks: Dict[str, List[Dict]] = defaultdict(list)
        for r in top:
            d = date.fromisoformat(r["date"][:10])
            weeks[(d - timedelta(days=d.weekday())).isoformat()].append(r)
        for w in sorted(weeks):
            print(f"    {w}     {line(weeks[w], how)}")
        if how is cal:
            print(f"  by market (≥ {MIN_ROW} picks), worst hit rate against what we said first:")
            by: Dict[str, List[Dict]] = defaultdict(list)
            for r in top:
                by[f"{r['sport']}:{r['market']}"].append(r)
            cells = [(k, v) for k, v in by.items() if len(v) >= MIN_ROW]
            gap = lambda v: sum(1 for r in v if r["won"]) / len(v) - sum(how(r) for r in v) / len(v)
            for k, v in sorted(cells, key=lambda kv: gap(kv[1])):
                print(f"    {k[:34]:<34} {line(v, how)}")


def tickets_report(r, start: float) -> None:
    legs = {"won": 0, "lost": 0}
    tickets = {"won": 0, "lost": 0}
    by_sport: Dict[str, Dict[str, int]] = defaultdict(lambda: {"won": 0, "lost": 0})
    since = (date.today() - timedelta(days=DAYS)).isoformat()
    for k in r.scan_iter(match="betiq:user:*:tickets", count=500):
        try:
            booked = json.loads(r.get(k) or "[]")
        except ValueError:
            continue
        for t in booked:
            if str(t.get("created_at") or "")[:10] < since:
                continue
            ls = [l for l in t.get("legs") or [] if isinstance(l.get("prob"), (int, float))]
            for l in ls:
                if l["prob"] >= start and l.get("status") in ("won", "lost"):
                    legs[l["status"]] += 1
                    m = str(l.get("market") or "")
                    sport = l.get("sport") or ("basketball" if m.startswith("bb_") else "tennis" if m.startswith("rk_") else "football")
                    by_sport[sport][l["status"]] += 1
            if ls and len(ls) == len(t.get("legs") or []) and all(l["prob"] >= start for l in ls) \
                    and t.get("status") in ("won", "lost"):
                tickets[t["status"]] += 1
    pct = lambda d: f"{100 * d['won'] / (d['won'] + d['lost']):.1f}%" if d["won"] + d["lost"] else "—"
    print(f"\n== Booked tickets (created in the last {DAYS} days) ==")
    print(f"  legs rated {start:.0%}+: green {legs['won']}  red {legs['lost']}  hit {pct(legs)}")
    for s, d in sorted(by_sport.items()):
        print(f"    {s:<12} green {d['won']:>5}  red {d['lost']:>5}  hit {pct(d)}")
    print(f"  tickets with every leg {start:.0%}+: landed {tickets['won']}  lost {tickets['lost']}  ({pct(tickets)})")


def daily_report(r) -> None:
    """The daily slips per target: how many landed against how many their
    combined chances (win_chance) said would, and by how sure the slip was."""
    rows: Dict[str, List] = defaultdict(list)
    bands: Dict[str, List] = defaultdict(list)
    legs: Dict[str, List] = defaultdict(list)
    priced: Dict[str, List] = defaultdict(list)
    for i in range(DAYS):
        d = (date.today() - timedelta(days=i)).isoformat()
        try:
            doc = json.loads(r.get(f"betiq:daily:{d}") or "null")
        except ValueError:
            continue
        for sl in ((doc or {}).get("slips") or []) + ((doc or {}).get("cut") or []):
            wc = sl.get("win_chance")
            if sl.get("status") not in ("won", "lost") or not isinstance(wc, (int, float)):
                continue
            won = sl["status"] == "won"
            for pk in sl.get("picks") or []:
                if pk.get("status") in ("won", "lost") and isinstance(pk.get("prob"), (int, float)):
                    m = str(pk.get("market") or "")
                    sport = "basketball" if m.startswith("bb_") else "tennis" if m.startswith("rk_") else "football"
                    for key in ("all", sport, f"{sport}:{m}", f"month {d[:7]}"):
                        legs[key].append((pk["prob"], pk["status"] == "won"))
                    o = pk.get("odds")
                    if isinstance(o, (int, float)) and o > 1:
                        # what the price says (less a ~5% margin), and how far we disagreed
                        imp = min(0.99, 1.05 / o)
                        edge = pk["prob"] - imp
                        band = "edge <0" if edge < 0 else "edge 0-5" if edge < .05 else "edge 5-10" if edge < .1 else "edge 10+"
                        for key in (f"{sport} {band}", f"{sport} all priced"):
                            priced[key].append((pk["prob"], imp, pk["status"] == "won"))
            rows[str(int(sl.get("target") or 0))].append((wc, won))
            band = "40%+" if wc >= 0.4 else "30-40%" if wc >= 0.3 else "20-30%" if wc >= 0.2 else "10-20%" if wc >= 0.1 else "<10%"
            bands[band].append((wc, won))
    print(f"\n== Daily slips (last {DAYS} days): landed vs what their chances said ==")
    show = lambda k, v: print(f"  {k:<8} n {len(v):>4}  landed {sum(w for _, w in v):>3}  "
                              f"said {sum(c for c, _ in v):6.1f}  ({100 * sum(w for _, w in v) / len(v):.0f}% vs "
                              f"{100 * sum(c for c, _ in v) / len(v):.0f}%)")
    for k in sorted(rows, key=lambda k: int(k)):
        show(f"{k}x", rows[k])
    print("  by the slip's own chance:")
    for k in ("<10%", "10-20%", "20-30%", "30-40%", "40%+"):
        if bands.get(k):
            show(k, bands[k])
    print("  the slips' legs (hit vs said):")
    leg = lambda k, v: print(f"    {k[:30]:<30} n {len(v):>4}  hit {100 * sum(w for _, w in v) / len(v):5.1f}%  "
                             f"said {100 * sum(c for c, _ in v) / len(v):5.1f}%")
    for k in ["all", "football", "basketball", "tennis"] + sorted(k for k in legs if k.startswith("month")):
        if legs.get(k):
            leg(k, legs[k])
    print("  ours vs the price's chance, by how far we disagreed (edge = ours - price, points):")
    for k in sorted(priced):
        v = priced[k]
        print(f"    {k:<30} n {len(v):>4}  hit {100 * sum(w for *_, w in v) / len(v):5.1f}%  "
              f"ours {100 * sum(a for a, *_ in v) / len(v):5.1f}%  price {100 * sum(b for _, b, _ in v) / len(v):5.1f}%")
    for k, v in sorted(((k, v) for k, v in legs.items() if ":" in k and len(v) >= 15),
                       key=lambda kv: sum(w for _, w in kv[1]) / len(kv[1]) - sum(c for c, _ in kv[1]) / len(kv[1])):
        leg(k, v)


def ranking_report(r) -> None:
    """The nightly market ranking: each market's hit rate, what we said, its
    return at the odds, and which a 'under what we said AND losing money'
    rule would leave out of slips."""
    import math
    try:
        codes = (json.loads(r.get("betiq:market_ranking") or "null") or {}).get("codes") or {}
    except ValueError:
        codes = {}
    rows = []
    for k, x in codes.items():
        n, h, said, roi = x.get("n") or 0, x.get("hit_rate"), x.get("said"), x.get("roi")
        if not n or h is None or said is None:
            continue
        se = math.sqrt(max(h * (1 - h), 1e-6) / n)
        z = (said - h) / se
        rows.append((k, n, h, said, roi, z))
    print(f"\n== Market ranking ({len(rows)} markets): under what we said (z) and the return at the odds ==")
    out = [x for x in rows if x[1] >= 100 and x[5] >= 2 and x[4] is not None and x[4] < 0]
    total = sum(x[1] for x in rows)
    print(f"  the rule (100+ picks, clearly under what we said, losing money) leaves out {len(out)} of "
          f"{len(rows)} markets, {sum(x[1] for x in out)} of {total} picks")
    for k, n, h, said, roi, z in sorted(rows, key=lambda x: -x[1])[:80]:
        flag = "OUT" if (k, n, h, said, roi, z) in out else ""
        roi_s = f"{100 * roi:+5.1f}%" if roi is not None else "  —  "
        print(f"    {k[:32]:<32} n {n:>6}  hit {100 * h:5.1f}%  said {100 * said:5.1f}%  z {z:5.1f}  return {roi_s}  {flag}")


def corners_report(r) -> None:
    """Why corners legs miss: the corner counts behind them. Finished matches'
    corner totals (too many zeros would mean a source writing 0 for 'no
    data'), and every corners leg on the daily slips with its match's count."""
    import tickets as tk
    totals: Dict[str, int] = defaultdict(int)
    by_source: Dict[str, List[int]] = defaultdict(list)
    results: Dict[tuple, Dict] = {}
    for i in range(DAYS):
        d = (date.today() - timedelta(days=i)).isoformat()
        try:
            day = json.loads(r.get(f"betiq:md:{d}") or "null") or {}
        except ValueError:
            continue
        for e in day.values():
            res = (e or {}).get("result") or {}
            if res.get("status") != "finished":
                continue
            results[(e.get("home"), e.get("away"), d)] = res
            c = res.get("corners")
            if not isinstance(c, list):
                totals["no count"] += 1
                continue
            t = sum(c)
            totals["0" if t == 0 else "1-3" if t <= 3 else "4-7" if t <= 7 else "8-11" if t <= 11 else "12+"] += 1
            by_source[str(res.get("source") or "?")].append(t)
    print(f"\n== Corners: finished matches' corner totals (last {DAYS} days) ==")
    print("  " + "  ".join(f"{k}: {totals[k]}" for k in ("no count", "0", "1-3", "4-7", "8-11", "12+")))
    for src, ts in sorted(by_source.items()):
        zero = sum(1 for t in ts if t == 0)
        print(f"    source {src:<12} {len(ts):>5} counted  average {sum(ts) / len(ts):5.1f}  zero {zero}")
    print("  corners legs on the daily slips: code, status, the match's corners, what grading gives now")
    shown = 0
    for i in range(DAYS):
        d = (date.today() - timedelta(days=i)).isoformat()
        try:
            doc = json.loads(r.get(f"betiq:daily:{d}") or "null") or {}
        except ValueError:
            continue
        for sl in (doc.get("slips") or []) + (doc.get("cut") or []):
            for pk in sl.get("picks") or []:
                if pk.get("market") != "corners_ou" or shown >= 60:
                    continue
                res = results.get((pk.get("home"), pk.get("away"), pk.get("date")))
                now = tk.grade_leg("corners_ou", pk.get("code", ""), res) if res else "no result found"
                print(f"    {pk.get('date')} {pk.get('home')} v {pk.get('away')}  {pk.get('code')} @ {pk.get('odds')}  "
                      f"stored {pk.get('status')}  corners {res.get('corners') if res else None} "
                      f"({res.get('source') if res else '-'})  grades {now}")
                shown += 1


def basketball_price_report(r) -> None:
    """Settled basketball lines kept with SportyBet's odds (from Nov 18 2026):
    per market, how well ours, the price's chance (PRICE_MARGIN / odds) and a
    half-and-half mix predicted them (log loss, lower is better)."""
    import math
    import basketball_matchday as bmd
    import basketball_markets as bmk
    per: Dict[str, List] = defaultdict(list)
    for i in range(1, 45):
        d = (date.today() - timedelta(days=i)).isoformat()
        try:
            day = json.loads(r.get(bmd.KEY.format(d)) or "null") or {}
        except ValueError:
            continue
        for e in day.values():
            res, p = (e or {}).get("result") or {}, (e or {}).get("pred") or {}
            if res.get("status") != "finished":
                continue
            st = bmd._settle_result(res)
            for x in p.get("lines") or []:
                if not isinstance(x, list) or len(x) < 4 or not isinstance(x[3], (int, float)) or x[3] <= 1:
                    continue
                v = bmk.settle(x[0], x[1], st)
                if v in ("won", "lost") and isinstance(x[2], (int, float)):
                    per[x[0]].append((x[2], min(0.99, 1.05 / x[3]), v == "won"))
    ll = lambda q, y: -math.log(max(1e-6, q if y else 1 - q))
    print("\n== Basketball: ours vs SportyBet's price, per market (log loss; lower is better) ==")
    if not per:
        print("  none yet: lines are kept with their odds from Nov 18 2026")
    for m, v in sorted(per.items(), key=lambda kv: -len(kv[1])):
        if len(v) < 50:
            continue
        n = len(v)
        a = sum(ll(o, y) for o, _, y in v) / n
        b = sum(ll(q, y) for _, q, y in v) / n
        c = sum(ll(0.5 * o + 0.5 * q, y) for o, q, y in v) / n
        best = min((a, "ours"), (b, "price"), (c, "half-half"))[1]
        print(f"    {m[:28]:<28} n {n:>6}  ours {a:.4f}  price {b:.4f}  half-half {c:.4f}  best: {best}")


_r = None


def main() -> None:
    global _r
    import model_store
    p = argparse.ArgumentParser()
    p.add_argument("--from", dest="start", type=float, default=0.75)
    a = p.parse_args()
    _r = model_store._client()
    if _r is None:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    rows = [x for x in calibration.load_rows(_r, days=DAYS) if isinstance(x.get("prob"), (int, float))]
    print(f"{len(rows)} settled picks over the last {DAYS} days "
          f"(as of {datetime.now(timezone.utc).isoformat(timespec='minutes')})")
    picks_report(rows, a.start)
    tickets_report(_r, a.start)
    daily_report(_r)
    ranking_report(_r)
    corners_report(_r)
    basketball_price_report(_r)


if __name__ == "__main__":
    main()
