"""
Shots and shots-on-target totals — the same team-rating model as corners
(set_pieces.SetPieceModel), on the league CSVs' HS/AS (shots) and HST/AST
(shots on target), and ESPN's for European/cup matches.

Markets: total shots and total shots on target for the match, and each
team's own. Like corners, a stat is only offered where the walk-forward
check (evaluate / check below) shows the model beating the league average
on matches it hadn't seen.

    python shots.py      # the walk-forward report on the 2024-25 season
"""

import math
import re
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

import set_pieces

STATS = ("shots", "sot")
TEAM_STATS = {"shots": ("shots_home", "shots_away"), "sot": ("sot_home", "sot_away")}
LINES = {"shots": (20.5, 22.5, 24.5, 26.5, 28.5), "sot": (6.5, 7.5, 8.5, 9.5, 10.5),
         "shots_home": (10.5, 11.5, 12.5, 13.5, 14.5, 15.5), "shots_away": (8.5, 9.5, 10.5, 11.5, 12.5, 13.5),
         "sot_home": (2.5, 3.5, 4.5, 5.5, 6.5), "sot_away": (1.5, 2.5, 3.5, 4.5, 5.5)}
# Tuned walk-forward on 2023-24 (tune()), tested on 2024-25 (check())
PARAMS = {"gamma": 0.8, "team_gamma": 1.1, "prior": 8.0, "decay": 0.95}


def _counts(row) -> Optional[Dict[str, Tuple[float, float]]]:
    try:
        hs, as_, hst, ast = (float(row[c]) for c in ("HS", "AS", "HST", "AST"))
    except (KeyError, TypeError, ValueError):
        return None
    if any(math.isnan(v) for v in (hs, as_, hst, ast)) or hst > hs or ast > as_:
        return None
    return {"shots": (hs, as_), "sot": (hst, ast)}


class ShotModel(set_pieces.SetPieceModel):
    STATS = STATS
    TEAM_OF = TEAM_STATS
    REQUIRED = ("HS", "AS", "HST", "AST")
    LINES = LINES
    DEFAULT_SIZE = {"shots": 30.0, "sot": 25.0}
    RACE = None
    SCALE = True

    def __init__(self, params: Optional[Dict] = None):
        super().__init__({**PARAMS, **(params or {}), "use_referees": False})

    @staticmethod
    def counts(row) -> Optional[Dict[str, Tuple[float, float]]]:
        return _counts(row)


def evaluate(matches: pd.DataFrame, test_from: str = "2024-08-01", params: Optional[Dict] = None) -> Dict:
    return set_pieces.evaluate(matches, test_from, model_cls=ShotModel, params=params)


def check(matches: pd.DataFrame, test_from: str, test_to: Optional[str] = None,
          params: Optional[Dict] = None) -> Dict[str, Dict]:
    """Per stat: the model's Brier score vs the league average's on the
    test window, and whether to offer it ("use")."""
    sc = set_pieces.score(matches, params, pd.Timestamp(test_from), test_to and pd.Timestamp(test_to),
                          model_cls=ShotModel)
    return {stat: {**v, "use": v["matches"] >= 300 and v["model"] < v["baseline"]} for stat, v in sc.items()}


def tune(matches: pd.DataFrame, start: str, end: str) -> Tuple[Dict, float]:
    """The settings with the lowest total Brier on [start, end)."""
    best, best_total = None, None
    for gamma in (0.6, 0.8, 1.0):
        for prior in (4.0, 8.0, 16.0):
            for decay in (0.92, 0.95, 0.97):
                params = {"gamma": gamma, "team_gamma": gamma, "prior": prior, "decay": decay}
                sc = set_pieces.score(matches, params, pd.Timestamp(start), pd.Timestamp(end), model_cls=ShotModel)
                total = sum(v["model"] for v in sc.values())
                if best_total is None or total < best_total:
                    best, best_total = params, total
    return best, best_total


if __name__ == "__main__":
    import json
    from main import _load_football_data_csvs
    print(json.dumps(evaluate(_load_football_data_csvs()), indent=1))


# ── SportyBet's own shot lines ───────────────────────────────────────────────
# Each shot market's SportyBet id (booking_slip._LINE_MARKETS; names checked
# by booking_slip.label_ok before a price is trusted)
PRICE_MARKETS = {"shots": "900394", "sot": "900393", "shots_home": "900552", "shots_away": "900553",
                 "sot_home": "900546", "sot_away": "900547"}
# Our market names (optimizer / tickets) → the stat they're on
MARKET_STATS = {"shots_ou": "shots", "sot_ou": "sot", "home_shots_ou": "shots_home", "away_shots_ou": "shots_away",
                "home_sot_ou": "sot_home", "away_sot_ou": "sot_away"}
BLEND_WEIGHTS = (0.0, 0.25, 0.5, 0.75, 1.0)
MIN_BLEND_LINES = 80    # settled lines before a mix is trusted over the model alone
_CODE = re.compile(r"^([OU])(\d+)5$")


def from_prices(event: Optional[Dict], sizes: Optional[Dict[str, Optional[float]]] = None) -> Optional[Dict]:
    """Every shot line SportyBet's price implies (margin out), per stat."""
    return set_pieces.from_prices(event, sizes, PRICE_MARKETS, LINES, ShotModel.DEFAULT_SIZE)


def blend(model_stat: Dict, market_stat: Dict, weight: float) -> Dict:
    """Our line probabilities mixed with SportyBet's: (1-w)·ours + w·theirs."""
    over = {}
    for line, p in (model_stat.get("over") or {}).items():
        q = (market_stat.get("over") or {}).get(line)
        over[line] = round((1 - weight) * p + weight * q, 3) if isinstance(q, (int, float)) else p
    return {"mean": round((1 - weight) * model_stat["mean"] + weight * market_stat["mean"], 2),
            "over": over, "blend": weight}


def blend_report(entries: Iterable[Dict]) -> Dict[str, Dict]:
    """On settled matches, for each shot line both we and SportyBet priced
    before kick-off: the Brier score of our probability, SportyBet's (margin
    out) and each mix, for internationals and clubs. Only our model's own
    numbers count (not ones already taken from or mixed with SportyBet)."""
    import tickets
    pairs: Dict[str, List[Tuple[float, float, float]]] = {"international": [], "club": []}
    for e in entries:
        res = e.get("result") or {}
        pred = e.get("pred") or {}
        if res.get("status") != "finished":
            continue
        not_model = pred.get("not_model") or {}
        sides: Dict[Tuple[str, str], Dict[str, Tuple[float, float]]] = {}
        for market, code, prob, odds in pred.get("prices") or []:
            m = _CODE.match(str(code))
            if market in MARKET_STATS and m and MARKET_STATS[market] not in not_model:
                sides.setdefault((market, m.group(2)), {})[m.group(1)] = (prob, odds)
        group = "international" if str(e.get("league") or "").startswith("INT") else "club"
        for (market, line), s in sides.items():
            if "O" not in s or "U" not in s or min(s["O"][1], s["U"][1]) <= 1:
                continue
            verdict = tickets.grade_leg(market, f"O{line}5", res)
            if verdict not in ("won", "lost"):
                continue
            p_sb = (1 / s["O"][1]) / (1 / s["O"][1] + 1 / s["U"][1])
            pairs[group].append((s["O"][0], p_sb, 1.0 if verdict == "won" else 0.0))
    out = {}
    for group, rows in pairs.items():
        n = len(rows)
        brier = {str(w): round(sum(((1 - w) * p + w * q - y) ** 2 for p, q, y in rows) / n, 4) if n else None
                 for w in BLEND_WEIGHTS}
        best = min(BLEND_WEIGHTS, key=lambda w: brier[str(w)]) if n else 0.0
        chosen = best if n >= MIN_BLEND_LINES and brier[str(best)] < brier["0.0"] else 0.0
        out[group] = {"lines": n, "brier": brier, "chosen": chosen,
                      "reason": None if n >= MIN_BLEND_LINES else f"needs {MIN_BLEND_LINES} settled lines ({n} so far)"}
    return out
