"""
Walk-forward backtest: how the model would have done on matches it had not
seen, judged against the bookmaker's own probabilities.

For each month in the test window the model is retrained on everything
before that month, then predicts the month's matches in date order. Team
state (Elo, form, H2H) rolls forward after each result, the way it does
live. Picks come from predictor.pick_tips, the same rules the app uses.

    python backtest.py                      # last season in the data
    python backtest.py --start 2024-08-01 --end 2025-06-30 --out data/model_metrics.json

The JSON it writes is served by /api/admin/model-metrics and shown on the
admin dashboard (/betiq-hq).
"""

import argparse
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

import pandas as pd

from grading import grade_goals, grade_tip
from predictor import LeaguePredictor, pick_tips

METRICS_PATH = os.path.join(os.path.dirname(__file__), "data", "model_metrics.json")

# Value bet: the model's probability for its 1X2 tip beats the bookmaker's
# implied probability by more than this (same rule as main._build_predictions).
VALUE_EDGE = 0.05

_OUTCOMES = ("H", "D", "A")


# ── Tiers — mirror frontend/lib/picks.ts confidenceTier ──────────────────────

_TIER_THRESHOLDS = {"single": (0.6, 0.45), "double": (0.8, 0.65), "goals": (0.75, 0.6)}


def pick_kind(code: str) -> str:
    return "double" if code in ("1X", "X2", "2X", "12") else "single"


def confidence_tier(prob: float, kind: str) -> str:
    strong, lean = _TIER_THRESHOLDS[kind]
    return "strong" if prob >= strong else "lean" if prob >= lean else "weak"


# ── Walk-forward ─────────────────────────────────────────────────────────────

def _odds(value: Any) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return f if f > 1 and not math.isnan(f) else 0.0


def _month_starts(start: pd.Timestamp, end: pd.Timestamp) -> List[pd.Timestamp]:
    first = start.normalize().replace(day=1)
    return list(pd.date_range(first, end, freq="MS"))


def walk_forward(matches: pd.DataFrame, start: str, end: str,
                 min_train: int = 1000,
                 make_model: Callable[[], LeaguePredictor] = LeaguePredictor,
                 log: Callable[[str], None] = print) -> List[Dict]:
    """One record per test match: the model's probabilities, its picks, the
    bookmaker's odds and the real score."""
    matches = matches.dropna(subset=["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "Result"])
    matches = matches.sort_values("Date", kind="stable").reset_index(drop=True)
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    records: List[Dict] = []

    for month in _month_starts(start_ts, end_ts):
        fold_from = max(month, start_ts)
        fold_to = min(month + pd.offsets.MonthBegin(1), end_ts + pd.Timedelta(days=1))
        train = matches[matches["Date"] < fold_from]
        test = matches[(matches["Date"] >= fold_from) & (matches["Date"] < fold_to)]
        if test.empty or len(train) < min_train:
            continue
        log(f"[Backtest] {fold_from:%Y-%m}: train {len(train)} → test {len(test)}")

        model = make_model()
        model.train(train)
        for _, r in test.iterrows():
            day = str(r["Date"].date())
            league = str(r.get("league", "") or "")
            oh, od, oa = _odds(r.get("B365H")), _odds(r.get("B365D")), _odds(r.get("B365A"))
            feats = model._feats(r["HomeTeam"], r["AwayTeam"], oh, od, oa, match_date=day, league=league)
            p_h, p_d, p_a, p_o15, p_o25 = model.predict_proba(feats)
            records.append({
                "date": day, "league": league,
                "home": r["HomeTeam"], "away": r["AwayTeam"],
                "p_home": p_h, "p_draw": p_d, "p_away": p_a, "p_over15": p_o15, "p_over25": p_o25,
                **pick_tips(p_h, p_d, p_a, p_o15, p_o25),
                "odds_home": oh, "odds_draw": od, "odds_away": oa,
                "odds_over25": _odds(r.get("B365>2.5")), "odds_under25": _odds(r.get("B365<2.5")),
                "home_goals": int(r["FTHG"]), "away_goals": int(r["FTAG"]), "result": r["Result"],
            })
            model._update(
                r["HomeTeam"], r["AwayTeam"], r["Result"], r["FTHG"], r["FTAG"],
                hyc=r.get("HY"), ayc=r.get("AY"), hrc=r.get("HR"), arc=r.get("AR"),
                match_date=day, competition=league,
            )
    return records


# ── Metrics ──────────────────────────────────────────────────────────────────

_EPS = 1e-12


def _market_1x2(r: Dict) -> Optional[List[float]]:
    """Bookmaker probabilities with the margin removed (proportionally)."""
    if not (r["odds_home"] and r["odds_draw"] and r["odds_away"]):
        return None
    raw = [1 / r["odds_home"], 1 / r["odds_draw"], 1 / r["odds_away"]]
    total = sum(raw)
    return [x / total for x in raw]


def _market_over25(r: Dict) -> Optional[float]:
    if not (r["odds_over25"] and r["odds_under25"]):
        return None
    over, under = 1 / r["odds_over25"], 1 / r["odds_under25"]
    return over / (over + under)


def _scores_1x2(probs: Iterable[List[float]], results: Iterable[str]) -> Dict[str, float]:
    ll = brier = hits = n = 0.0
    for p, res in zip(probs, results):
        k = _OUTCOMES.index(res)
        ll -= math.log(max(p[k], _EPS))
        brier += sum((p[i] - (1.0 if i == k else 0.0)) ** 2 for i in range(3))
        hits += 1.0 if max(range(3), key=lambda i: p[i]) == k else 0.0
        n += 1
    if not n:
        return {"log_loss": None, "brier": None, "accuracy": None}
    return {"log_loss": round(ll / n, 4), "brier": round(brier / n, 4), "accuracy": round(hits / n, 4)}


def _scores_binary(probs: Iterable[float], outcomes: Iterable[bool]) -> Dict[str, float]:
    ll = brier = n = 0.0
    for p, y in zip(probs, outcomes):
        ll -= math.log(max(p if y else 1 - p, _EPS))
        brier += (p - (1.0 if y else 0.0)) ** 2
        n += 1
    if not n:
        return {"log_loss": None, "brier": None}
    return {"log_loss": round(ll / n, 4), "brier": round(brier / n, 4)}


def _model_1x2(r: Dict) -> List[float]:
    return [r["p_home"], r["p_draw"], r["p_away"]]


def _rate(won: float, total: float) -> Optional[float]:
    return round(won / total, 4) if total else None


def calibration(records: List[Dict], buckets: int = 10) -> List[Dict]:
    """Predicted vs actual frequency, pooling all three 1X2 outcomes."""
    acc = [[0, 0.0, 0] for _ in range(buckets)]  # n, sum predicted, hits
    for r in records:
        for p, outcome in zip(_model_1x2(r), _OUTCOMES):
            b = min(int(p * buckets), buckets - 1)
            acc[b][0] += 1
            acc[b][1] += p
            acc[b][2] += 1 if r["result"] == outcome else 0
    return [
        {"from": round(i / buckets, 2), "to": round((i + 1) / buckets, 2), "n": n,
         "predicted": round(s / n, 4), "actual": round(h / n, 4)}
        for i, (n, s, h) in enumerate(acc) if n
    ]


def _settle_at_odds(r: Dict, code: str) -> Optional[float]:
    """Profit on a 1-unit stake at the bookmaker's odds for a 1/X/2 tip."""
    odds = {"1": r["odds_home"], "X": r["odds_draw"], "2": r["odds_away"]}.get(code)
    if not odds:
        return None
    return odds - 1 if grade_tip(code, r["result"]) == "won" else -1.0


def _roi(profits: List[float]) -> Dict[str, Any]:
    n = len(profits)
    total = sum(profits)
    return {
        "bets": n, "won": sum(1 for p in profits if p > 0),
        "profit": round(total, 2), "roi": round(total / n, 4) if n else None,
    }


def summarize(records: List[Dict]) -> Dict[str, Any]:
    """Headline metrics from walk_forward() records."""
    with_odds = [r for r in records if _market_1x2(r)]
    with_ou = [r for r in records if _market_over25(r) is not None]

    # Picks by type and tier
    pick_rows: Dict[tuple, List[float]] = defaultdict(lambda: [0, 0, 0.0])  # n, won, sum prob
    for r in records:
        code = r["tip_code"]
        if code not in ("1", "X", "2", "1X", "2X", "X2", "12"):
            continue
        kind = pick_kind(code)
        tier = confidence_tier(r["tip_confidence"], kind)
        won = grade_tip(code, r["result"]) == "won"
        for key in ((kind, tier), (kind, "all"), ("code", code)):
            row = pick_rows[key]
            row[0] += 1
            row[1] += won
            row[2] += r["tip_confidence"]

    def _pick_row(key, **label):
        n, won, s = pick_rows.get(key, (0, 0, 0.0))
        return {**label, "n": n, "won": won, "hit_rate": _rate(won, n), "avg_prob": _rate(s, n)}

    by_tier = [_pick_row((kind, tier), kind=kind, tier=tier)
               for kind in ("single", "double") for tier in ("strong", "lean", "weak", "all")]
    by_code = [_pick_row(("code", code), code=code)
               for code in ("1", "X", "2", "1X", "2X") if ("code", code) in pick_rows]

    # Goals tips, settled like a bookmaker would (Asian pushes refund)
    goals: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in records:
        tip = r["tip_goals"]
        verdict = grade_goals(tip, r["home_goals"], r["away_goals"])
        if verdict is None:
            continue
        tier = confidence_tier(r["goals_confidence"], "goals")
        for key in (tip, f"tier:{tier}", "all"):
            g = goals[key]
            g["n"] += 1
            g["prob"] += r["goals_confidence"]
            g[verdict] += 1

    def _goals_row(key, **label):
        g = goals.get(key, {})
        won = g.get("won", 0) + 0.5 * g.get("half_won", 0)
        lost = g.get("lost", 0) + 0.5 * g.get("half_lost", 0)
        n = int(g.get("n", 0))
        return {**label, "n": n, "won": int(g.get("won", 0)), "lost": int(g.get("lost", 0)),
                "push": int(g.get("push", 0)), "hit_rate": _rate(won, won + lost),
                "avg_prob": _rate(g.get("prob", 0), n)}

    goals_by_tip = [_goals_row(k, tip=k) for k in sorted(goals) if not k.startswith("tier:") and k != "all"]
    goals_by_tier = [_goals_row(f"tier:{t}", tier=t) for t in ("strong", "lean", "weak") if f"tier:{t}" in goals]

    # Betting: every straight tip, and value bets only, flat 1 unit at B365 odds
    straight, value = [], []
    for r in records:
        code = r["tip_code"]
        if code not in ("1", "X", "2"):
            continue
        profit = _settle_at_odds(r, code)
        if profit is None:
            continue
        straight.append(profit)
        odds = {"1": r["odds_home"], "X": r["odds_draw"], "2": r["odds_away"]}[code]
        if r["tip_confidence"] - 1 / odds > VALUE_EDGE:
            value.append(profit)

    # Per league: does the model beat the market there?
    by_league = []
    for lg in sorted({r["league"] for r in with_odds}):
        rows = [r for r in with_odds if r["league"] == lg]
        results = [r["result"] for r in rows]
        by_league.append({
            "league": lg, "n": len(rows),
            "model": _scores_1x2([_model_1x2(r) for r in rows], results),
            "market": _scores_1x2([_market_1x2(r) for r in rows], results),
        })

    dates = sorted(r["date"] for r in records)
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "period": {"from": dates[0], "to": dates[-1]} if dates else None,
        "matches": len(records),
        "leagues": sorted({r["league"] for r in records}),
        "method": "Walk-forward: retrained monthly on all earlier matches, "
                  "predicting each month unseen. Bookmaker = Bet365 odds with the margin removed.",
        "match_result": {
            "n": len(with_odds),
            "model": _scores_1x2([_model_1x2(r) for r in with_odds], [r["result"] for r in with_odds]),
            "market": _scores_1x2([_market_1x2(r) for r in with_odds], [r["result"] for r in with_odds]),
        },
        "over25": {
            "n": len(with_ou),
            "model": _scores_binary([r["p_over25"] for r in with_ou],
                                    [r["home_goals"] + r["away_goals"] > 2 for r in with_ou]),
            "market": _scores_binary([_market_over25(r) for r in with_ou],
                                     [r["home_goals"] + r["away_goals"] > 2 for r in with_ou]),
        },
        "calibration": calibration(records),
        "picks": {"by_tier": by_tier, "by_code": by_code},
        "goals_tips": {"by_tip": goals_by_tip, "by_tier": goals_by_tier, "all": _goals_row("all")},
        "betting": {"all_straight_tips": _roi(straight), "value_bets": _roi(value), "edge_threshold": VALUE_EDGE},
        "by_league": by_league,
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def _default_window(matches: pd.DataFrame) -> tuple:
    """The most recent season in the data: from the last 1 July (or August) on."""
    last = matches["Date"].max()
    season_start = pd.Timestamp(year=last.year if last.month >= 7 else last.year - 1, month=7, day=1)
    return str(season_start.date()), str(last.date())


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", help="first test date (default: start of the latest season)")
    ap.add_argument("--end", help="last test date (default: latest match)")
    ap.add_argument("--out", default=METRICS_PATH)
    args = ap.parse_args(argv)

    from main import _load_football_data_csvs
    matches = _load_football_data_csvs()
    if matches.empty:
        raise SystemExit("No football-data CSVs found in data/football/")
    start, end = _default_window(matches)
    start, end = args.start or start, args.end or end

    records = walk_forward(matches, start, end)
    metrics = summarize(records)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(metrics, f, indent=1)
    mr = metrics["match_result"]
    print(f"[Backtest] {metrics['matches']} matches {start} → {end}. "
          f"1X2 log loss: model {mr['model']['log_loss']} vs bookmaker {mr['market']['log_loss']}. "
          f"Wrote {args.out}")


if __name__ == "__main__":
    main()
