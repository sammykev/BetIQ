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
from typing import Dict, Optional, Tuple

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
