"""
Per-league settings for the football corners / bookings model
(set_pieces.SetPieceModel) and the shots model (shots.ShotModel),
walk-forward: each candidate (rating decay, prior, damping) replays every
match in date order, pricing each from the matches before it. The
candidate with the lowest Brier score (summed over the lines of every
stat) on the first half of the tested window is checked on the second
half; a league keeps it only where it beats the defaults there by
MIN_GAIN (0.2%). Run in GitHub Actions ("Train model"); saved to Redis
(TUNING_KEY), which the pipeline passes to both models' fits (load()).

    python tune_football.py            # needs UPSTASH_REDIS_URL
    python tune_football.py --local    # the league CSVs in data/football, nothing saved
"""

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

import set_pieces
import shots

TUNING_KEY = "betiq:fb:sp_tuning"
DECAYS = (0.90, 0.93, 0.95, 0.97)
PRIORS = (4.0, 8.0, 16.0)
DAMPING = (0.75, 1.0, 1.25)   # × the default gamma and team_gamma
TEST_DAYS = 730               # the latest two seasons are tested
MIN_GAIN = 0.002              # share of the default's Brier a league's pick must save on the held-out half
MIN_HELD = 150                # held-out matches a league needs to be tuned

MODELS = {
    "set_pieces": (set_pieces.SetPieceModel, dict(set_pieces.CLUB_GUARDS),
                   {"decay": set_pieces.DECAY, "prior": set_pieces.PRIOR,
                    "gamma": set_pieces.GAMMA, "team_gamma": set_pieces.TEAM_GAMMA}),
    "shots": (shots.ShotModel, {}, dict(shots.PARAMS)),
}


def candidates(default: Dict) -> List[Dict]:
    return [{"decay": d, "prior": p, "gamma": round(default["gamma"] * g, 3),
             "team_gamma": round(default["team_gamma"] * g, 3)}
            for d in DECAYS for p in PRIORS for g in DAMPING]


def _key(c: Dict) -> str:
    return f"{c['decay']:g}/{c['prior']:g}/{c['gamma']:g}"


def _cdfs(mu: np.ndarray, size: Optional[float], kmax: int) -> np.ndarray:
    """P(X <= k) for k = 0..kmax, one row a mean (set_pieces._nb_cdf, vectorised)."""
    mu = np.maximum(mu, 1e-9)
    if size is None:
        pmf, step = np.exp(-mu), lambda i: mu / (i + 1)
    else:
        p = size / (size + mu)
        pmf, step = p ** size, lambda i: (i + size) / (i + 1) * (1 - p)
    out = np.empty((len(mu), kmax + 1))
    acc = np.zeros(len(mu))
    for i in range(kmax + 1):
        acc = acc + pmf
        out[:, i] = acc
        pmf = pmf * step(i)
    return np.minimum(out, 1.0)


def _brier(model_cls, rows: List[tuple], start: pd.Timestamp, cut: pd.Timestamp) -> Dict[Tuple[str, int], List[float]]:
    """{(league, half): [matches, Brier sum]} over the tested rows, with the
    spreads (and scales) fitted on the rows before `start`."""
    out: Dict[Tuple[str, int], List[float]] = defaultdict(lambda: [0, 0.0])
    first = model_cls.STATS[0]
    for stat in model_cls.all_stats():
        before = [(m, y) for d, s, m, y, _, _ in rows if s == stat and d < start]
        size = set_pieces.SetPieceModel.fit_size(before) or model_cls.DEFAULT_SIZE.get(stat.split("_")[0])
        scale = set_pieces.fit_scale(before, model_cls.LINES[stat], size) if model_cls.SCALE else None
        tested = [(m, y, league, int(d >= cut)) for d, s, m, y, _, league in rows if s == stat and d >= start]
        if not tested:
            continue
        mu = np.array([t[0] for t in tested], dtype=float)
        y = np.array([t[1] for t in tested], dtype=float)
        lines = model_cls.LINES[stat]
        cdf = _cdfs(mu, size, int(max(lines)))
        b = np.zeros(len(tested))
        for line in lines:
            p = 1.0 - cdf[:, int(np.floor(line))]
            if scale and scale != 1.0:
                p = np.clip(p, 1e-6, 1 - 1e-6)
                p = 1.0 / (1.0 + np.exp(-scale * np.log(p / (1 - p))))
            b += (p - (y > line)) ** 2
        for (_, _, league, half), v in zip(tested, b):
            acc = out[(league, half)]
            acc[1] += float(v)
            if stat == first:
                acc[0] += 1
    return out


def _per_match(acc: List[float]) -> Optional[float]:
    return round(acc[1] / acc[0], 5) if acc and acc[0] else None


def tune(name: str, matches: pd.DataFrame) -> Dict:
    model_cls, base, default = MODELS[name]
    data = matches.dropna(subset=list(model_cls.REQUIRED))
    if data.empty:
        return {"skipped": "no matches with these stats"}
    warm_up = data["Date"].min() + pd.Timedelta(days=365)
    end = data["Date"].max()
    start = max(warm_up, end - pd.Timedelta(days=TEST_DAYS))
    cut = start + (end - start) / 2
    grid = candidates(default)
    if default not in grid:
        grid.append(dict(default))
    scores = {}
    for c in grid:
        _, rows = model_cls.replay(data, test_from=warm_up, params={**base, **c}, tag=True)
        scores[_key(c)] = _brier(model_cls, rows, start, cut)
    by_key = {_key(c): c for c in grid}
    dk = _key(default)
    report = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "default": default,
              "window": [str(start.date()), str(cut.date()), str(end.date())], "leagues": {}, "tuned": {}}
    for league in sorted({lg for lg, _ in scores[dk]}, key=lambda lg: -scores[dk][(lg, 1)][0]):
        tune_s = {k: _per_match(s.get((league, 0))) for k, s in scores.items()}
        held_s = {k: _per_match(s.get((league, 1))) for k, s in scores.items()}
        n_held = scores[dk].get((league, 1), [0])[0]
        if tune_s[dk] is None or held_s[dk] is None:
            continue
        best = min((k for k in tune_s if tune_s[k] is not None), key=lambda k: tune_s[k])
        keep = (best != dk and n_held >= MIN_HELD and held_s[best] is not None
                and held_s[best] <= held_s[dk] * (1 - MIN_GAIN))
        report["leagues"][league] = {"best": by_key[best], "keep": keep, "held_matches": n_held,
                                     "default": held_s[dk], "tuned": held_s[best],
                                     "tune_default": tune_s[dk], "tune_best": tune_s[best]}
        if keep:
            report["tuned"][league] = by_key[best]
        print(f"[{name}] {league}: best {best} on the earlier half · held out {n_held} matches: "
              f"Brier {held_s[dk]} -> {held_s[best]} · {'KEEP' if keep else 'defaults'}")
    # The chosen settings together, in one replay (a team's matches in
    # other leagues use those leagues' settings), against the defaults
    _, rows = model_cls.replay(data, test_from=warm_up, params={**base, **default, "by_league": report["tuned"]},
                               tag=True)
    used = _brier(model_cls, rows, start, cut)
    tot = {"default": [0, 0.0], "used": [0, 0.0]}
    for k, s in (("default", scores[dk]), ("used", used)):
        for (_, half), (n, b) in s.items():
            if half == 1:
                tot[k][0] += n
                tot[k][1] += b
    report["overall"] = {k: {"matches": n, "brier": _per_match([n, b])} for k, (n, b) in tot.items()}
    print(f"[{name}] held-out half, all leagues: {json.dumps(report['overall'])}; "
          f"tuned: {sorted(report['tuned'])}\n")
    return report


def run(matches: pd.DataFrame) -> Dict:
    return {name: tune(name, matches) for name in MODELS}


def load(r) -> Dict[str, Dict[str, Dict]]:
    """{"set_pieces" / "shots": {league: settings}} saved by the last run
    ({} for each without)."""
    try:
        rep = json.loads(r.get(TUNING_KEY) or "null") if r else None
    except Exception:
        rep = None
    return {name: dict(((rep or {}).get(name) or {}).get("tuned") or {}) for name in MODELS}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true", help="the league CSVs in data/football; nothing saved")
    args = ap.parse_args()
    import main as app
    if args.local:
        history = app._load_football_data_csvs()
        r = None
    else:
        import asyncio
        import model_store
        r = model_store._client()
        if r is None:
            raise SystemExit("UPSTASH_REDIS_URL isn't set")
        asyncio.run(app._sync_football_data(force=True))
        assembled = app._assemble_training_data()
        if assembled is None:
            raise SystemExit("no training data")
        history = app._with_club_referees(assembled[0])
    print(f"{len(history)} matches, {history['league'].nunique()} leagues")
    report = run(history)
    if r is not None:
        r.set(TUNING_KEY, json.dumps(report))


if __name__ == "__main__":
    main()
