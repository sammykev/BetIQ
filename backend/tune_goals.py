"""
Per-league Elo K, home advantage and form decay for the football result /
goals model (predictor.LeaguePredictor.league_params), walk-forward: the
model is trained (XGBoost included) on every match before the tested
window, then prices the window's matches in date order, ratings and form
rolling on after each result as they do live. Scored by the Brier score of
the 1X2 and over 2.5 chances, from the model with odds (where the match
had odds) and the one without.

One setting at a time (K, then home advantage, then form decay), each
league moves to the value with the lowest score on the first half of the
window; it keeps its settings only where they beat the defaults on the
second half by MIN_GAIN (0.2%), and only if the kept settings together beat
the defaults over all leagues. Run in GitHub Actions ("Train model"),
before the training: saved to Redis (TUNING_KEY), which main._train_new
reads.

    python tune_goals.py            # needs UPSTASH_REDIS_URL
    python tune_goals.py --local    # the league CSVs in data/football, nothing saved
"""

import argparse
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from predictor import LEAGUE_PARAM_DEFAULTS, LeaguePredictor

TUNING_KEY = "betiq:fb:goals_tuning"
STEPS = (("k_mult", (0.5, 0.75, 1.25, 1.5)),
         ("home_adv", (40.0, 60.0, 100.0, 120.0)),
         ("form_alpha", (0.15, 0.35)))
TEST_DAYS = 365        # the latest season is tested
MIN_GAIN = 0.002       # share of the default's Brier a league's pick must save on the held-out half
MIN_HELD = 150         # held-out matches a league needs to be tuned

Scores = Dict[Tuple[str, int], List[float]]   # (league, half) -> [matches, Brier sum]


def _brier(p: Tuple[float, ...], result: str, goals: float) -> float:
    p_h, p_d, p_a, _, p_o25 = p
    y = {"H": (1, 0, 0), "D": (0, 1, 0), "A": (0, 0, 1)}[result]
    return (p_h - y[0]) ** 2 + (p_d - y[1]) ** 2 + (p_a - y[2]) ** 2 + (p_o25 - (goals >= 3)) ** 2


def _probs(model: LeaguePredictor, feats: List[Dict], odds: bool) -> np.ndarray:
    """(p_home, p_draw, p_away, p_over15, p_over25) per row, in one batch
    (predict_proba's models, without its per-league calibration)."""
    suffix = "" if odds else "_noodds"
    df = pd.DataFrame(feats)
    wp = model.models["win" + suffix].predict_proba(df[model.cols(odds)])
    xg = df[model.cols(odds, target="goals")]
    o15 = model.models["o15" + suffix].predict_proba(xg)[:, 1]
    o25 = model.models["o25" + suffix].predict_proba(xg)[:, 1]
    return np.column_stack([wp[:, 2], wp[:, 1], wp[:, 0], o15, o25])


def score(matches: pd.DataFrame, params: Dict[str, Dict], start: pd.Timestamp, cut: pd.Timestamp,
          make: Callable[[], LeaguePredictor] = LeaguePredictor) -> Scores:
    """Train on the matches before `start` with these league settings, then
    walk the rest: {(league, half): [matches, Brier sum]}."""
    model = make()
    model.league_params = {lg: dict(p) for lg, p in params.items()}
    model.train(matches[matches["Date"] < start])
    feats, meta = [], []
    for _, r in matches[matches["Date"] >= start].iterrows():
        league = str(r.get("league", "") or "")
        day = str(r["Date"].date())
        if r.get("StrengthOnly") in (True,):
            model._strength_update(r["HomeTeam"], r["AwayTeam"], r["Result"], league)
            continue
        if r.get("Context") in (True,):
            model.context_update(r)
            continue
        odds = [float(r.get(c) or 0) for c in ("B365H", "B365D", "B365A")]
        odds = [o if o > 1 else 0.0 for o in odds]
        f = model._feats(r["HomeTeam"], r["AwayTeam"], *odds, match_date=day, league=league)
        feats.append(f)
        meta.append((league, int(r["Date"] >= cut), r["Result"], float(r["FTHG"]) + float(r["FTAG"]), f["_has_odds"]))
        model._update(r["HomeTeam"], r["AwayTeam"], r["Result"], r["FTHG"], r["FTAG"],
                      hyc=r.get("HY"), ayc=r.get("AY"), hrc=r.get("HR"), arc=r.get("AR"),
                      match_date=day, competition=league, hst=r.get("HST"), ast=r.get("AST"))
    out: Scores = defaultdict(lambda: [0, 0.0])
    if not feats:
        return out
    with_odds, without = _probs(model, feats, True), _probs(model, feats, False)
    for (league, half, result, goals, has_odds), po, pn in zip(meta, with_odds, without):
        acc = out[(league, half)]
        acc[0] += 1
        acc[1] += _brier(tuple(po if has_odds else pn), result, goals) + _brier(tuple(pn), result, goals)
    return out


def _per_match(acc: Optional[List[float]]) -> Optional[float]:
    return round(acc[1] / acc[0], 5) if acc and acc[0] else None


def _window(matches: pd.DataFrame) -> Tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    end = matches["Date"].max()
    start = max(matches["Date"].min() + pd.Timedelta(days=730), end - pd.Timedelta(days=TEST_DAYS))
    return start, start + (end - start) / 2, end


def tune(matches: pd.DataFrame, make: Callable[[], LeaguePredictor] = LeaguePredictor,
         steps=STEPS, log: Callable[[str], None] = print) -> Dict:
    matches = matches.dropna(subset=["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "Result"])
    matches = matches.sort_values("Date", kind="stable").reset_index(drop=True)
    start, cut, end = _window(matches)
    t0 = time.time()
    default = score(matches, {}, start, cut, make)
    log(f"[goals] defaults scored in {time.time() - t0:.0f}s; window {start.date()} / {cut.date()} / {end.date()}")
    leagues = sorted({lg for lg, _ in default if lg}, key=lambda lg: -default.get((lg, 1), [0])[0])
    chosen: Dict[str, Dict] = {lg: dict(LEAGUE_PARAM_DEFAULTS) for lg in leagues}
    best: Dict[str, Tuple[float, Optional[float]]] = {
        lg: (_per_match(default.get((lg, 0))) or float("inf"), _per_match(default.get((lg, 1)))) for lg in leagues}
    tried = []
    for key, values in steps:
        for v in values:
            params = {lg: {**chosen[lg], key: v} for lg in leagues}
            s = score(matches, params, start, cut, make)
            tried.append(f"{key}={v:g}")
            for lg in leagues:
                t = _per_match(s.get((lg, 0)))
                if t is not None and t < best[lg][0]:
                    best[lg] = (t, _per_match(s.get((lg, 1))))
                    chosen[lg] = params[lg]
            log(f"[goals] {key}={v:g} scored ({time.time() - t0:.0f}s so far)")
    report = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "default": LEAGUE_PARAM_DEFAULTS,
              "window": [str(start.date()), str(cut.date()), str(end.date())], "tried": tried,
              "leagues": {}, "tuned": {}}
    for lg in leagues:
        n_held = default.get((lg, 1), [0])[0]
        held_default, held_best = _per_match(default.get((lg, 1))), best[lg][1]
        if held_default is None:
            continue
        changed = chosen[lg] != LEAGUE_PARAM_DEFAULTS
        keep = bool(changed and n_held >= MIN_HELD and held_best is not None
                    and held_best <= held_default * (1 - MIN_GAIN))
        report["leagues"][lg] = {"best": chosen[lg], "keep": keep, "held_matches": n_held,
                                 "default": held_default, "tuned": held_best,
                                 "tune_default": _per_match(default.get((lg, 0))), "tune_best": best[lg][0]}
        if keep:
            report["tuned"][lg] = chosen[lg]
        log(f"[goals] {lg}: best {json.dumps(chosen[lg])} on the earlier half · held out {n_held} matches: "
            f"Brier {held_default} -> {held_best} · {'KEEP' if keep else 'defaults'}")
    # The kept settings together, against the defaults, on the held-out half
    used = score(matches, report["tuned"], start, cut, make) if report["tuned"] else default
    report["overall"] = {}
    for name, s in (("default", default), ("used", used)):
        n = sum(v[0] for (lg, half), v in s.items() if half == 1)
        b = sum(v[1] for (lg, half), v in s.items() if half == 1)
        report["overall"][name] = {"matches": n, "brier": _per_match([n, b])}
    # Leagues share the Elo pool and the XGBoost models, so the kept settings
    # must also win together, or every league goes back to the defaults
    if report["tuned"] and not report["overall"]["used"]["brier"] < report["overall"]["default"]["brier"]:
        log(f"[goals] the kept settings together {report['overall']['used']['brier']} don't beat the defaults "
            f"{report['overall']['default']['brier']}: back to defaults")
        for lg in report["tuned"]:
            report["leagues"][lg]["keep"] = False
        report["tuned"] = {}
    log(f"[goals] held-out half, all leagues: {json.dumps(report['overall'])}; tuned: {sorted(report['tuned'])}")
    return report


def load(r) -> Dict[str, Dict]:
    """{league: settings} saved by the last run ({} without one)."""
    try:
        rep = json.loads(r.get(TUNING_KEY) or "null") if r else None
    except Exception:
        rep = None
    tuned = (rep or {}).get("tuned") or {}
    return {lg: {k: float(v) for k, v in p.items() if k in LEAGUE_PARAM_DEFAULTS}
            for lg, p in tuned.items() if isinstance(p, dict)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true", help="the league CSVs in data/football; nothing saved")
    args = ap.parse_args()
    import main as app
    if args.local:
        matches, r = app._load_football_data_csvs(), None
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
        matches = assembled[1]

    def make() -> LeaguePredictor:   # as main._train_new, without the saved settings
        p = LeaguePredictor()
        p.use_league_strength = app._club_league_strength
        p.elo.seed_national_teams()
        return p
    print(f"{len(matches)} matches")
    report = tune(matches, make)
    if r is not None:
        r.set(TUNING_KEY, json.dumps(report))


if __name__ == "__main__":
    main()
