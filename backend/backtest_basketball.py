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

    python backtest_basketball.py            # SportyBet's results + the older seasons (bb_history.py)
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


TRAIN_DAYS = 730         # each week's ratings: the two seasons before it (older games weigh ~nothing)
MIN_CONSTANT_GAMES = 200  # out-of-sample games with quarters before a league's constants are used


def league_backtest(name: str, games: List[bm.Game]) -> Optional[Dict]:
    games = sorted(games, key=lambda g: g.date)
    if len(games) < MIN_GAMES:
        return None
    start = date.fromisoformat(games[int(len(games) * WARMUP)].date)
    end = date.fromisoformat(games[-1].date)
    picks: List[tuple] = []
    resid_m, resid_t, fit_sd_m, fit_sd_t = [], [], [], []
    # League constants, measured out of sample: how the expected margin shows
    # in each part, each quarter's share of the points, regulation ties
    mrows: List[tuple] = []
    qsh, h1sh, ties = [], [], []
    week = start
    lo = 0
    while week <= end:
        nxt = week + timedelta(days=7)
        first = (week - timedelta(days=TRAIN_DAYS)).isoformat()
        while lo < len(games) and games[lo].date < first:
            lo += 1
        lg = bm.fit(name, games[lo:], as_of=week)
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
                if g.periods and len(g.periods) == 4:
                    q = g.periods
                    rh, ra = sum(p[0] for p in q), sum(p[1] for p in q)
                    mrows.append((m.margin, [q[0][0] + q[1][0] - q[0][1] - q[1][1]] + [p[0] - p[1] for p in q], 1.0))
                    if rh + ra:
                        qsh.append([(p[0] + p[1]) / (rh + ra) for p in q])
                        h1sh.append((q[0][0] + q[0][1] + q[1][0] + q[1][1]) / (rh + ra))
                    ties.append((m.margin, lg.sigma["margin"], rh == ra))
        week = nxt
    if len(resid_m) < 30:
        return None
    oos_m, oos_t = float(np.std(resid_m)), float(np.std(resid_t))
    scale_m = oos_m / float(np.mean(fit_sd_m))
    out = {"league": name, "games": len(games), "tested": len(resid_m),
           "first": games[0].date, "last": games[-1].date,
           "sigma_scale": {"margin": round(scale_m, 3),
                           "total": round(oos_t / float(np.mean(fit_sd_t)), 3)},
           "calibration": calibration(picks), "bias": {"margin": round(float(np.mean(resid_m)), 2),
                                                         "total": round(float(np.mean(resid_t)), 2)}}
    if len(mrows) >= MIN_CONSTANT_GAMES:
        # Ties against the spread the server will use (the fit's, widened by the measured scale)
        seen = sum(1 for _, _, t in ties if t)
        expected = sum(bm.base_tie(mg, sd * min(1.35, max(0.9, scale_m))) for mg, sd, _ in ties)
        out["constants"] = {
            "n": len(mrows),
            "margin_shares": [round(x, 4) for x in relative_shares(mrows)],
            "q_shares": [round(float(x), 4) for x in np.mean(np.array(qsh), axis=0)],
            "h1_share": round(float(np.mean(h1sh)), 4),
            "tie_factor": round(min(3.0, max(1.0, (seen + bm.PRIOR_TIES * bm.TIE_FACTOR) / (expected + bm.PRIOR_TIES))), 3),
            "ties": [seen, round(expected, 1)],
        }
    return out


def relative_shares(rows: List[tuple]) -> List[float]:
    """Each part's share of the game's margin (first half, quarters 1-4):
    the slope of the part's margin on our expected margin, over the slope of
    the whole game's. Out of sample our margins are shrunk, so the raw slopes
    run over 1; at serve time the margin is the blend with SportyBet's lines,
    and what matters is how it splits."""
    em = np.array([r[0] for r in rows])
    parts = np.array([r[1] for r in rows])
    den = float(np.sum(em * em))
    beta = [float(np.sum(em * parts[:, j])) / den for j in range(5)] if den > 1e-9 else [0.5, 0.25, 0.25, 0.25, 0.25]
    full = sum(beta[1:])
    if full <= 0.2:
        return [0.5, 0.25, 0.25, 0.25, 0.25]
    shares = [b / full for b in beta]
    return [min(0.7, max(0.3, shares[0]))] + [min(0.4, max(0.05, x)) for x in shares[1:]]


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
    import bb_history
    results: List[Dict] = []
    for blob in (r.hgetall(bd.RESULTS_KEY) or {}).values():
        results += bd.decode(blob)
    history = bb_history.load(r)
    print(f"{len(results)} SportyBet results, {len(history)} older games (bb_history.py)")
    results = bb_history.combine(results, history)
    report = run(results, args.min)
    r.set(BACKTEST_KEY, json.dumps(report))
    print(f"Saved: {len(report['leagues'])} leagues")


if __name__ == "__main__":
    main()
