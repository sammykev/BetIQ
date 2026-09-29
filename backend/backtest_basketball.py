"""
Basketball walk-forward check (GitHub Actions, "Basketball data"): for each
league with enough results, week by week, rate the teams on the games
before that week only and price the week's games; then compare the chances
we gave with what happened.

Reported per league and overall:
- calibration: of the picks we rated 70–80%, 80–90%, 90%+, how many came in
  (winner, handicap and total lines around our expectation, quarters);
- out-of-sample spread: how far results landed from our expectation,
  against what the fit measured on its own games (sigma scale). The server
  widens (or narrows) each league's spread by it, so the chance it quotes
  for a far line is the chance it has (basketball_data.apply_calibration).

Saved to Redis (BACKTEST_KEY) for the server and the admin page.

    python backtest_basketball.py            # from the stored results (UPSTASH_REDIS_URL)
    python backtest_basketball.py --min 150  # leagues with at least this many games
"""

import argparse
import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

import numpy as np

import basketball_data as bd
import basketball_model as bm

BACKTEST_KEY = "betiq:bb:backtest"
MIN_GAMES = 150
WARMUP = 0.35            # the first share of a league's games only trains
BUCKETS = ((0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0))
# Lines priced for each game, around our own expectation (as SportyBet's alternative lines are)
MARGIN_OFFSETS = (-12.5, -7.5, -2.5, 2.5, 7.5, 12.5)
TOTAL_OFFSETS = (-15.5, -9.5, -4.5, 4.5, 9.5, 15.5)


def _picks(m: bm.Match, g: bm.Game) -> List[tuple]:
    """(market, our chance, came in) for a game's lines — both sides of each."""
    out = []
    reg = (sum(p[0] for p in g.periods), sum(p[1] for p in g.periods)) if g.periods else None
    fh, fa = g.hs, g.as_
    p = m.p_win(True)
    out += [("winner", p, fh > fa), ("winner", 1 - p, fa > fh)]
    for off in MARGIN_OFFSETS:
        line = round(-m.margin + off) + 0.5          # a half-point line for home near our margin
        ph = m.p_handicap_ot(True, line)
        out += [("handicap", ph, fh + line > fa), ("handicap", 1 - ph, fa - line > fh)]
    for off in TOTAL_OFFSETS:
        line = round(m.total + off) + 0.5
        po = m.p_total_ot(line)
        out += [("total", po, fh + fa > line), ("total", 1 - po, fh + fa < line)]
    if g.periods and len(g.periods) >= 4:
        for i, (qh, qa) in enumerate(g.periods[:4]):
            part = m.period(f"q{i + 1}")
            for off in (-5.5, 0.5, 5.5):
                line = round(part.home + part.away + off) + 0.5
                po = part.p_total(line)
                out += [("quarter_total", po, qh + qa > line), ("quarter_total", 1 - po, qh + qa < line)]
    return out


def league_backtest(name: str, games: List[bm.Game]) -> Optional[Dict]:
    games = sorted(games, key=lambda g: g.date)
    if len(games) < MIN_GAMES:
        return None
    start = date.fromisoformat(games[int(len(games) * WARMUP)].date)
    end = date.fromisoformat(games[-1].date)
    picks: List[tuple] = []
    resid_m, resid_t, fit_sd_m, fit_sd_t = [], [], [], []
    week = start
    while week <= end:
        nxt = week + timedelta(days=7)
        lg = bm.fit(name, games, as_of=week)
        if lg:
            for g in games:
                if not (week.isoformat() <= g.date < nxt.isoformat()):
                    continue
                m = bm.expect(lg, g.home, g.away, g.neutral)
                if m is None:
                    continue
                picks += _picks(m, g)
                if not g.ot:
                    resid_m.append((g.hs - g.as_) - m.margin)
                    resid_t.append((g.hs + g.as_) - m.total)
                    fit_sd_m.append(lg.sigma["margin"])
                    fit_sd_t.append(lg.sigma["total"])
        week = nxt
    if len(resid_m) < 30:
        return None
    oos_m, oos_t = float(np.std(resid_m)), float(np.std(resid_t))
    return {"league": name, "games": len(games), "tested": len(resid_m),
            "sigma_scale": {"margin": round(oos_m / float(np.mean(fit_sd_m)), 3),
                            "total": round(oos_t / float(np.mean(fit_sd_t)), 3)},
            "calibration": calibration(picks), "bias": {"margin": round(float(np.mean(resid_m)), 2),
                                                          "total": round(float(np.mean(resid_t)), 2)}}


def calibration(picks: List[tuple]) -> Dict[str, List[Dict]]:
    out: Dict[str, List[Dict]] = {}
    for market in sorted({p[0] for p in picks}) + ["all"]:
        rows = []
        for lo, hi in BUCKETS:
            sel = [(p, won) for mk, p, won in picks if (market == "all" or mk == market) and lo <= p < hi]
            if sel:
                rows.append({"bucket": f"{int(lo * 100)}-{int(hi * 100)}%", "n": len(sel),
                             "said": round(sum(p for p, _ in sel) / len(sel), 3),
                             "came_in": round(sum(1 for _, w in sel if w) / len(sel), 3)})
        out[market] = rows
    return out


def run(results: List[Dict], min_games: int = MIN_GAMES) -> Dict:
    report = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "leagues": {}}
    every: List[tuple] = []
    for name, games in sorted(bd.games_by_league(results).items(), key=lambda kv: -len(kv[1])):
        if len(games) < min_games:
            continue
        got = league_backtest(name, games)
        if got:
            report["leagues"][name] = got
            print(f"{name}: {got['tested']} games tested, sigma x{got['sigma_scale']['margin']} margin, "
                  f"x{got['sigma_scale']['total']} total; 80-90%: "
                  + ", ".join(f"{m} {r['came_in']:.0%} of {r['n']}" for m, rows in got["calibration"].items()
                              for r in rows if r["bucket"] == "80-90%"))
    # Overall, weighting each league by its tested games
    rows: Dict[str, Dict] = {}
    for lg in report["leagues"].values():
        for r in lg["calibration"].get("all", []):
            x = rows.setdefault(r["bucket"], {"bucket": r["bucket"], "n": 0, "said": 0.0, "came_in": 0.0})
            x["n"] += r["n"]
            x["said"] += r["said"] * r["n"]
            x["came_in"] += r["came_in"] * r["n"]
    report["overall"] = [{**x, "said": round(x["said"] / x["n"], 3), "came_in": round(x["came_in"] / x["n"], 3)}
                         for x in rows.values() if x["n"]]
    print("Overall:", json.dumps(report["overall"]))
    return report


def main() -> None:
    import model_store
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=MIN_GAMES)
    args = ap.parse_args()
    r = model_store._client()
    if r is None:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    results: List[Dict] = []
    for blob in (r.hgetall(bd.RESULTS_KEY) or {}).values():
        results += bd.decode(blob)
    print(f"{len(results)} results stored")
    report = run(results, args.min)
    r.set(BACKTEST_KEY, json.dumps(report))
    print(f"Saved: {len(report['leagues'])} leagues")


if __name__ == "__main__":
    main()
