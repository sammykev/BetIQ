"""
Per-league half-life and ridge for the basketball ratings (basketball_model.fit),
walk-forward: week by week, each candidate rates the teams on the games
before the week only and prices the week's games. The candidate with the
lowest winner log loss on the first half of the tested weeks is checked on
the second half; a league keeps it only where it beats the defaults there
(log loss down by MIN_GAIN, margin error no worse). Run in GitHub Actions
("Basketball data", job tune); saved to Redis (TUNING_KEY), which the
server, the walk-forward check and the Elo check load into bm.TUNED.

    python tune_basketball.py
"""

import argparse
import json
import math
from datetime import date, datetime, timedelta, timezone
from statistics import NormalDist
from typing import Dict, List, Optional, Tuple

import basketball_data as bd
import basketball_model as bm
from backtest_basketball import MIN_GAMES, TRAIN_DAYS, WARMUP

TUNING_KEY = "betiq:bb:tuning"
HALF_LIVES = (45.0, 90.0, 180.0, 365.0)
RIDGES = (2.0, 4.0, 8.0, 16.0)
TEST_WEEKS = 104          # the latest two seasons' weeks are tested (older leagues: fewer)
MIN_GAIN = 0.002          # log loss a tuned pair must save on the held-out half
MIN_COVER = 0.99          # and the share of the defaults' games it must still price
_N = NormalDist()


def candidates() -> List[Tuple[float, float]]:
    return [(h, r) for h in HALF_LIVES for r in RIDGES]


def league_rows(name: str, games: List[bm.Game]) -> Optional[Dict[Tuple[float, float], List[tuple]]]:
    """Per candidate, (week, our margin, sigma, actual margin, ot, game) for every tested game."""
    games = sorted(games, key=lambda g: g.date)
    if len(games) < MIN_GAMES:
        return None
    end = date.fromisoformat(games[-1].date)
    start = max(date.fromisoformat(games[int(len(games) * WARMUP)].date), end - timedelta(weeks=TEST_WEEKS))
    rest = bm.back_to_back(games)
    out: Dict[Tuple[float, float], List[tuple]] = {c: [] for c in candidates()}
    week, lo, k = start, 0, 0
    while k < len(games) and games[k].date < start.isoformat():
        k += 1
    n_week = 0
    while week <= end:
        nxt = week + timedelta(days=7)
        first = (week - timedelta(days=TRAIN_DAYS)).isoformat()
        while lo < len(games) and games[lo].date < first:
            lo += 1
        hi = k
        while hi < len(games) and games[hi].date < nxt.isoformat():
            hi += 1
        if hi > k:
            for c in out:
                lg = bm.fit(name, games[lo:k], as_of=week, half_life=c[0], ridge=c[1])
                if not lg:
                    continue
                for i in range(k, hi):
                    g = games[i]
                    m = bm.expect(lg, g.home, g.away, g.neutral, rest=rest[i])
                    if m is not None:
                        out[c].append((n_week, m.margin, lg.sigma["margin"], g.hs - g.as_, g.ot, i))
            n_week += 1
        k = hi
        week = nxt
    return out if len(out[(bm.HALF_LIFE_DAYS, bm.RIDGE)]) >= 100 else None


def score(rows: List[tuple]) -> Dict[str, float]:
    n, ll, se, n_m = 0, 0.0, 0.0, 0
    for _, margin, sigma, actual, ot, _ in rows:
        if actual == 0:
            continue
        p = min(0.995, max(0.005, _N.cdf(margin / sigma)))
        n += 1
        ll -= math.log(p if actual > 0 else 1 - p)
        if not ot:
            se += (actual - margin) ** 2
            n_m += 1
    return {"n": n, "log_loss": round(ll / n, 5) if n else None, "margin_rmse": round(math.sqrt(se / n_m), 3) if n_m else None}


def choose(rows: Dict[Tuple[float, float], List[tuple]]) -> Dict:
    """The best candidate on the earlier weeks, judged on the later ones.
    Candidates that leave games unpriced (a short half-life after the
    off-season) are out; the rest are scored on the games all of them price."""
    base = (bm.HALF_LIFE_DAYS, bm.RIDGE)
    n_base = len(rows[base])
    rows = {c: rs for c, rs in rows.items() if len(rs) >= MIN_COVER * n_base}
    common = set.intersection(*({r[5] for r in rs} for rs in rows.values()))
    rows = {c: [r for r in rs if r[5] in common] for c, rs in rows.items()}
    weeks = max(r[0] for r in rows[base]) + 1
    cut = weeks // 2
    tune = {c: score([r for r in rs if r[0] < cut]) for c, rs in rows.items()}
    held = {c: score([r for r in rs if r[0] >= cut]) for c, rs in rows.items()}
    best = min((c for c in tune if tune[c]["log_loss"] is not None), key=lambda c: tune[c]["log_loss"])
    b, d = held[best], held[base]
    keep = (best != base and b["log_loss"] is not None and d["log_loss"] is not None
            and b["log_loss"] <= d["log_loss"] - MIN_GAIN
            and (b["margin_rmse"] or 0) <= (d["margin_rmse"] or 0))
    return {"best": list(best), "keep": keep, "default": d, "tuned": b,
            "tune_default": tune[base], "tune_best": tune[best],
            "grid": {f"{c[0]:g}/{c[1]:g}": held[c] for c in rows}}


def run(results: List[Dict]) -> Dict:
    report = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "leagues": {}, "tuned": {}}
    for name, games in sorted(bd.games_by_league(results).items(), key=lambda kv: -len(kv[1])):
        rows = league_rows(name, games)
        if not rows:
            continue
        got = choose(rows)
        report["leagues"][name] = got
        if got["keep"]:
            report["tuned"][name] = got["best"]
        d, t = got["default"], got["tuned"]
        print(f"{name}: best {got['best'][0]:g}d/{got['best'][1]:g} on the earlier weeks · held out "
              f"{d['n']} games: log loss {d['log_loss']} -> {t['log_loss']}, margin rmse {d['margin_rmse']} -> "
              f"{t['margin_rmse']} · {'KEEP' if got['keep'] else 'defaults'}")
    # Overall on the held-out weeks: every league as it will be fitted
    tot = {"default": [0, 0.0], "used": [0, 0.0]}
    for name, got in report["leagues"].items():
        used = got["tuned"] if got["keep"] else got["default"]
        for k, s in (("default", got["default"]), ("used", used)):
            tot[k][0] += s["n"]
            tot[k][1] += s["log_loss"] * s["n"]
    report["overall"] = {k: {"n": n, "log_loss": round(ll / n, 5) if n else None} for k, (n, ll) in tot.items()}
    print(f"\nHeld-out weeks, all leagues: {json.dumps(report['overall'])}; tuned: {report['tuned']}")
    return report


def load(r) -> Dict[str, Tuple[float, float]]:
    """The tuned leagues saved by the last run ({} if none)."""
    try:
        rep = json.loads(r.get(TUNING_KEY) or "null") if r else None
    except Exception:
        return {}
    return {k: (float(v[0]), float(v[1])) for k, v in ((rep or {}).get("tuned") or {}).items()}


def main() -> None:
    import model_store
    import bb_history
    argparse.ArgumentParser().parse_args()
    r = model_store._client()
    if r is None:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    results: List[Dict] = []
    for blob in (r.hgetall(bd.RESULTS_KEY) or {}).values():
        results += bd.decode(blob)
    results = bb_history.combine(results, bb_history.load(r))
    print(f"{len(results)} games")
    report = run(results)
    r.set(TUNING_KEY, json.dumps(report))


if __name__ == "__main__":
    main()
