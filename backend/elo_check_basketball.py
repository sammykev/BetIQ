"""
Does a margin-of-victory Elo (basketball_elo.py) help the basketball model?
Walk-forward, week by week as in backtest_basketball.py: before each week,
the attack/defence ratings are fitted on the games before it and the Elo is
run up to it; the week's games are then predicted by

    the current model (attack/defence), Elo alone, and blends of the two
    (the expected margin mixed with weight w on Elo's),

and scored against what happened: winner accuracy, log loss, Brier score
and the expected margin's error. Elo's gap is turned into points by a
per-league scale learned from the games already tested (shrunk towards
FiveThirtyEight's 1 point per 28 Elo points), so nothing is fitted on the
future. Run in GitHub Actions ("Basketball data", job elo_check); the
report goes to Redis (REPORT_KEY) and the log.

    python elo_check_basketball.py
"""

import argparse
import json
import math
from datetime import date, datetime, timedelta, timezone
from statistics import NormalDist
from typing import Dict, List, Optional

import basketball_data as bd
import basketball_elo as be
import basketball_model as bm
from backtest_basketball import MIN_GAMES, TRAIN_DAYS, WARMUP

REPORT_KEY = "betiq:bb:elo_check"
WEIGHTS = (0.0, 0.25, 0.5, 0.75, 1.0)    # 0: the current model, 1: Elo alone
SCALE0 = 1 / 28.0                         # points per Elo point (FiveThirtyEight, NBA)
SCALE_PRIOR = 60 * 200.0 ** 2             # the prior's weight: 60 games at a 200-point gap
_N = NormalDist()


def league_check(name: str, games: List[bm.Game]) -> Optional[Dict]:
    games = sorted(games, key=lambda g: g.date)
    if len(games) < MIN_GAMES:
        return None
    start = date.fromisoformat(games[int(len(games) * WARMUP)].date)
    end = date.fromisoformat(games[-1].date)
    elo: Dict[str, float] = {}
    last: Dict[str, str] = {}
    done = 0                         # games already run through the Elo
    sdm = SCALE0 * SCALE_PRIOR       # the scale's running sums (shrunk to SCALE0)
    sdd = SCALE_PRIOR
    rows: List[tuple] = []           # (model margin, elo gap, scale, sigma, actual margin, ot)
    week, lo = start, 0
    while week <= end:
        nxt = week + timedelta(days=7)
        first = (week - timedelta(days=TRAIN_DAYS)).isoformat()
        while lo < len(games) and games[lo].date < first:
            lo += 1
        # Elo up to this week
        upto = done
        while upto < len(games) and games[upto].date < week.isoformat():
            upto += 1
        be.rate(games[done:upto], out=elo, last=last)
        done = upto
        lg = bm.fit(name, games[lo:], as_of=week)
        scale = sdm / sdd
        week_rows = []
        if lg:
            for g in games[done:]:
                if g.date >= nxt.isoformat():
                    break
                m = bm.expect(lg, g.home, g.away, g.neutral)
                if m is None:
                    continue
                d = be.diff(elo, g.home, g.away, g.neutral)
                week_rows.append((m.margin, d, scale, lg.sigma["margin"], g.hs - g.as_, g.ot))
        rows += week_rows
        # The scale learns from this week's results only after they're predicted
        for _, d, _, _, actual, ot in week_rows:
            if not ot:
                sdm += d * actual
                sdd += d * d
        week = nxt
    if len(rows) < 50:
        return None
    return {"league": name, "tested": len(rows), "scale": round(sdm / sdd, 4), "scores": score(rows)}


def score(rows: List[tuple]) -> Dict[str, Dict]:
    out = {}
    for w in WEIGHTS:
        n = acc = 0
        ll = brier = se = 0.0
        n_m = 0
        for model, d, scale, sigma, actual, ot in rows:
            margin = (1 - w) * model + w * d * scale
            p = min(0.995, max(0.005, _N.cdf(margin / sigma)))
            if actual == 0:
                continue
            won = actual > 0
            n += 1
            acc += (p > 0.5) == won
            ll -= math.log(p if won else 1 - p)
            brier += (p - won) ** 2
            if not ot:
                se += (actual - margin) ** 2
                n_m += 1
        out[f"w{w:g}"] = {"n": n, "accuracy": round(acc / n, 4), "log_loss": round(ll / n, 4),
                          "brier": round(brier / n, 4), "margin_rmse": round(math.sqrt(se / n_m), 2) if n_m else None}
    return out


def run(results: List[Dict]) -> Dict:
    report = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "leagues": {}}
    for name, games in sorted(bd.games_by_league(results).items(), key=lambda kv: -len(kv[1])):
        got = league_check(name, games)
        if not got:
            continue
        report["leagues"][name] = got
        s = got["scores"]
        print(f"{name}: {got['tested']} games, scale {got['scale']} pts/Elo · " + " · ".join(
            f"w{k[1:]}: acc {v['accuracy']:.3f} ll {v['log_loss']:.4f} rmse {v['margin_rmse']}" for k, v in s.items()))
    # Overall: every tested game counted once
    total: Dict[str, Dict[str, float]] = {}
    for lg in report["leagues"].values():
        for k, v in lg["scores"].items():
            t = total.setdefault(k, {"n": 0, "acc": 0.0, "ll": 0.0, "brier": 0.0, "rmse2": 0.0})
            t["n"] += v["n"]
            t["acc"] += v["accuracy"] * v["n"]
            t["ll"] += v["log_loss"] * v["n"]
            t["brier"] += v["brier"] * v["n"]
            t["rmse2"] += (v["margin_rmse"] or 0) ** 2 * v["n"]
    report["overall"] = {k: {"n": int(t["n"]), "accuracy": round(t["acc"] / t["n"], 4),
                             "log_loss": round(t["ll"] / t["n"], 4), "brier": round(t["brier"] / t["n"], 4),
                             "margin_rmse": round(math.sqrt(t["rmse2"] / t["n"]), 2)}
                         for k, t in total.items() if t["n"]}
    print("\nOverall (w = Elo's weight in the margin; w0 = the current model):")
    for k, v in report["overall"].items():
        print(f"  {k}: {json.dumps(v)}")
    return report


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
    r.set(REPORT_KEY, json.dumps(report))


if __name__ == "__main__":
    main()
