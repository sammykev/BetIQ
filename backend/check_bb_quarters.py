"""
Do team trends and previous meetings predict basketball's quarter markets
better than the league's pattern alone? A walk-forward check on every
league with enough quarter-scored games (results plus the long history):
week by week over the last TEST_DAYS days, the quarter model is fitted on
the games before that week only and scored on that week's games.

Compared, by log loss (lower is better) on the same games:
  frequency   how often each quarter was the highest, in the league's past
  league      the simulation from the league's quarter pattern only
  k…/h…       plus team trends (K_TEAM) and meetings (K_H2H) at each weight

Markets scored: highest and lowest scoring quarter (Q1–Q4 or a tie), and the highest
and lowest quarter's points over the league's middle line. The weights that
do best on highest-quarter log loss are saved (CONFIG_KEY) for the server —
only if they beat the league pattern; else trends are switched off.
Per league, how fast old games fade (HL_GRID days against HALF_LIFE_DAYS) is
chosen on the earlier half of its test games and kept only where the later
half agrees; saved with the weights ("half_life").

    python check_bb_quarters.py     (GitHub Actions: "Basketball data", job bb_quarters)
"""

import json
import math
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np

import basketball_model as bm
import basketball_quarters as bq

TEST_DAYS = 150
MIN_LEAGUE = 300
SIMS = 4000
GRID = [(10.0, 8.0), (25.0, 8.0), (60.0, 8.0), (25.0, 3.0), (25.0, 20.0), (25.0, -1.0)]
OFF = {"k_team": 1e9, "k_h2h": -1.0}
# Per league, how fast old games fade (days), with team trends at BASE: the best
# on the earlier half of a league's test games (highest + lowest quarter log loss)
# is kept only where it beats HALF_LIFE_DAYS on the later half by MIN_GAIN
HL_GRID = (90.0, 365.0, 730.0)
BASE = (25.0, 8.0)
MIN_GAIN = 0.002
MIN_HELD = 150


def _ll(p: float) -> float:
    return -math.log(min(0.999, max(0.001, p)))


def _outcome(q: np.ndarray, lowest: bool = False) -> str:
    """Which quarter scored the most (or the least), "EQ" for a tie for it."""
    tot = q.sum(axis=1)
    top = tot.min() if lowest else tot.max()
    return "EQ" if (tot == top).sum() > 1 else f"Q{int(tot.argmin() if lowest else tot.argmax()) + 1}"


def league_check(name: str, games: List[bm.Game]) -> Optional[Dict]:
    games = sorted((g for g in games if bq._quarters(g) is not None), key=lambda g: g.date)
    if len(games) < MIN_LEAGUE:
        return None
    end = date.fromisoformat(games[-1].date)
    start = end - timedelta(days=TEST_DAYS)
    losses: Dict[str, List[float]] = {}
    week = start
    while week <= end:
        nxt = week + timedelta(days=7)
        test = [g for g in games if week.isoformat() <= g.date < nxt.isoformat()]
        train = [g for g in games if g.date < week.isoformat()]
        week = nxt
        if not test or len(train) < bq.MIN_GAMES:
            continue
        lg = bm.fit(name, train[-3000:], as_of=date.fromisoformat(test[0].date))
        if lg is None:
            continue
        base = bq.fit(name, train, as_of=date.fromisoformat(test[0].date), k_team=1e9)
        if base is None:
            continue
        outs = [_outcome(bq._quarters(g)) for g in train[-2000:]]
        freq = {o: (outs.count(o) + 1) / (len(outs) + 5) for o in bq.OUTCOMES}
        outs_lo = [_outcome(bq._quarters(g), lowest=True) for g in train[-2000:]]
        freq_lo = {o: (outs_lo.count(o) + 1) / (len(outs_lo) + 5) for o in bq.OUTCOMES}
        maxes = [bq._quarters(g).sum(axis=1).max() for g in train[-2000:]]
        mins = [bq._quarters(g).sum(axis=1).min() for g in train[-2000:]]
        hi_line, lo_line = float(np.median(maxes)) + 0.5, float(np.median(mins)) + 0.5
        f_hi = float(np.mean([m > hi_line for m in maxes]))
        f_lo = float(np.mean([m > lo_line for m in mins]))
        models = {"league": (base, None, False)}
        for kt, kh in GRID:
            m = bq.fit(name, train, as_of=date.fromisoformat(test[0].date), k_team=kt)
            if m:
                models[f"k{kt:g}/h{kh:g}"] = (m, kh, True)
        for hl in HL_GRID:
            m = bq.fit(name, train, as_of=date.fromisoformat(test[0].date), k_team=BASE[0], half_life=hl)
            if m:
                models[f"hl{hl:g}"] = (m, BASE[1], True)
        for g in test:
            q = bq._quarters(g)
            tot = q.sum(axis=1)
            got, got_lo = _outcome(q), _outcome(q, lowest=True)
            hp, ap = lg.expect(g.home, g.away, g.neutral)
            losses.setdefault("frequency|hsq", []).append(_ll(freq[got]))
            losses.setdefault("frequency|lsq", []).append(_ll(freq_lo[got_lo]))
            losses.setdefault("frequency|hi", []).append(_ll(f_hi if tot.max() > hi_line else 1 - f_hi))
            losses.setdefault("frequency|lo", []).append(_ll(f_lo if tot.min() > lo_line else 1 - f_lo))
            for label, (m, kh, teams) in models.items():
                mu = bq.expected(m, g.home, g.away, hp, ap, k_h2h=kh if kh is not None else -1.0, use_teams=teams)
                sims = bq.simulate(mu, m.cov, lg.sigma.get("total", 17.0), sims=SIMS, seed=len(g.home) * 31 + len(g.away))
                o = bq.QuarterOdds(sims, list(mu))
                losses.setdefault(f"{label}|hsq", []).append(_ll(o.highest()[got]))
                losses.setdefault(f"{label}|lsq", []).append(_ll(o.lowest()[got_lo]))
                ph, pl = o.p_high_over(hi_line), o.p_low_over(lo_line)
                losses.setdefault(f"{label}|hi", []).append(_ll(ph if tot.max() > hi_line else 1 - ph))
                losses.setdefault(f"{label}|lo", []).append(_ll(pl if tot.min() > lo_line else 1 - pl))
    if not losses:
        return None
    return {"games": len(losses.get("league|hsq", [])), "losses": losses}


def _z(a: List[float], b: List[float]) -> float:
    d = [x - y for x, y in zip(a, b)]
    if len(d) < 2:
        return 0.0
    mean = sum(d) / len(d)
    sd = math.sqrt(sum((x - mean) ** 2 for x in d) / (len(d) - 1))
    return round(mean / (sd / math.sqrt(len(d))), 2) if sd > 0 else 0.0


def half_life(losses: Dict[str, List[float]]) -> Dict:
    """A league's half life: candidates scored on highest + lowest quarter log
    loss, chosen on the earlier half of its test games, kept where the later
    half agrees (beats HALF_LIFE_DAYS by MIN_GAIN)."""
    base = f"k{BASE[0]:g}/h{BASE[1]:g}"
    cands = {float(bq.HALF_LIFE_DAYS): base, **{hl: f"hl{hl:g}" for hl in HL_GRID}}
    score = {hl: [a + b for a, b in zip(losses[f"{lab}|hsq"], losses[f"{lab}|lsq"])]
             for hl, lab in cands.items() if f"{lab}|hsq" in losses}
    n = min((len(v) for v in score.values()), default=0)
    if float(bq.HALF_LIFE_DAYS) not in score or n < 2:
        return {"keep": False}
    half = n // 2
    early = {hl: sum(v[:half]) / half for hl, v in score.items() if len(v) == n}
    late = {hl: sum(v[half:]) / (n - half) for hl, v in score.items() if len(v) == n}
    dflt = float(bq.HALF_LIFE_DAYS)
    best = min(early, key=early.get)
    keep = best != dflt and n - half >= MIN_HELD and late[best] <= late[dflt] * (1 - MIN_GAIN)
    return {"best": best, "keep": keep, "held": n - half, "default": round(late[dflt], 5),
            "tuned": round(late[best], 5), "early": {f"{k:g}": round(v, 5) for k, v in early.items()},
            "late": {f"{k:g}": round(v, 5) for k, v in late.items()}}


def run(by_league: Dict[str, List[bm.Game]]) -> Dict:
    pooled: Dict[str, List[float]] = {}
    per: Dict[str, Dict] = {}
    half_lives: Dict[str, float] = {}
    for name, games in sorted(by_league.items(), key=lambda kv: -len(kv[1])):
        got = league_check(name, games)
        if not got:
            continue
        per[name] = {"games": got["games"], **{k: round(sum(v) / len(v), 4) for k, v in got["losses"].items()}}
        for k, v in got["losses"].items():
            pooled.setdefault(k, []).extend(v)
        print(f"{name}: {got['games']} test games · highest quarter log loss: "
              + ", ".join(f"{k.split('|')[0]} {per[name][k]}" for k in per[name] if k.endswith("|hsq")))
        hl = half_life(got["losses"])
        per[name]["half_life"] = hl
        if "best" in hl:
            print(f"  half life: best {hl['best']:g} days on the earlier half · held out {hl['held']} games: "
                  f"log loss {hl['default']} -> {hl['tuned']} · {'KEEP' if hl['keep'] else 'defaults'}")
        if hl.get("keep"):
            half_lives[name] = hl["best"]
    if not pooled:
        return {"note": "no league with enough quarter-scored games"}
    labels = sorted({k.split("|")[0] for k in pooled})
    table = {lab: {mk: round(sum(pooled[f"{lab}|{mk}"]) / len(pooled[f"{lab}|{mk}"]), 5)
                   for mk in ("hsq", "lsq", "hi", "lo") if f"{lab}|{mk}" in pooled} for lab in labels}
    grid = [lab for lab in labels if lab.startswith("k")]     # the half-life candidates ("hl…") aren't weights
    best = min(grid, key=lambda lab: table[lab]["hsq"]) if grid else None
    z = _z(pooled[f"{best}|hsq"], pooled["league|hsq"]) if best else 0.0
    use = best is not None and table[best]["hsq"] < table["league"]["hsq"]
    if use:
        kt, kh = best[1:].split("/h")
        config = {"k_team": float(kt), "k_h2h": float(kh)}
    else:
        config = dict(OFF)
    print(f"\nAll leagues, {len(pooled['league|hsq'])} test games (log loss; lower is better):")
    for lab in labels:
        print(f"  {lab:<12} {json.dumps(table[lab])}")
    print(f"best weights: {best} · vs league pattern z {z} (below −2 is clear) → "
          f"{'team trends and meetings ON ' + json.dumps(config) if use else 'trends OFF (league pattern only)'}")
    print(f"half lives kept: {json.dumps(half_lives) if half_lives else 'none (every league on ' + str(bq.HALF_LIFE_DAYS) + ' days)'}")
    return {"test_games": len(pooled["league|hsq"]), "table": table, "best": best, "z_vs_league": z,
            "config": {**config, "half_life": half_lives}, "leagues": per}


def main() -> None:
    import model_store
    import basketball_data as bd
    import bb_history
    r = model_store._client()
    if r is None:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    results = []
    for raw in (r.hgetall(bd.RESULTS_KEY) or {}).values():
        results += bd.decode(raw)
    games = bd.games_by_league(bb_history.combine(results, bb_history.load(r)))
    rep = run(games)
    if "config" in rep:
        r.set(bq.CONFIG_KEY, json.dumps({**rep["config"], "report": {k: rep[k] for k in ("test_games", "table", "best",
                                                                                          "z_vs_league")}}))


if __name__ == "__main__":
    main()
