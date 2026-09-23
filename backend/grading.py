"""
Settle predictions against real results. Pure functions — no I/O.

A history entry carries two independent verdicts:
  outcome        — the 1X2 / double-chance tip (tip_code):
                   "won" | "lost" | "pending" | "void" (result known, no such tip)
  goals_outcome  — the goals tip (tip_goals, e.g. "Over 2.0 (Asian)"):
                   "won" | "lost" | "push" | "half_won" | "half_lost" | None

Asian lines settle the way bookmakers do: a whole line (2.0) refunds the
stake on an exact hit ("push"); a quarter line (2.25) is half on 2.0 and half
on 2.5, so it can be half won or half lost.
"""

import math
import re
from typing import Any, Dict, Optional

# Which full-time results (H/D/A) win each tip code
_TIP_WINS_ON = {
    "1": {"H"}, "X": {"D"}, "2": {"A"},
    "1X": {"H", "D"}, "X2": {"D", "A"}, "2X": {"D", "A"}, "12": {"H", "A"},
}

_LINE_RE = re.compile(r"^\s*(over|under)\s+(\d+(?:\.\d+)?)", re.IGNORECASE)
_BTTS_RE = re.compile(r"^\s*btts\s+(yes|no)\b", re.IGNORECASE)
_SCORE_RE = re.compile(r"^\s*(\d+)\s*[-:]\s*(\d+)\s*$")


def result_from_score(home_goals: int, away_goals: int) -> str:
    return "H" if home_goals > away_goals else "A" if home_goals < away_goals else "D"


def parse_score(score: Any) -> Optional[tuple]:
    """"2-1" → (2, 1); anything else → None."""
    m = _SCORE_RE.match(str(score or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def grade_tip(tip_code: str, result: str) -> str:
    """Settle the 1X2 / double-chance tip. "void" when there's no such tip."""
    wins_on = _TIP_WINS_ON.get(tip_code or "")
    if wins_on is None:
        return "void"
    return "won" if result in wins_on else "lost"


def _settle_line(side: str, line: float, total: float) -> str:
    margin = total - line if side == "over" else line - total
    return "won" if margin > 0 else "lost" if margin < 0 else "push"


def grade_goals(tip_goals: str, home_goals: int, away_goals: int) -> Optional[str]:
    """Settle the goals tip against the final score. None when there's no goals tip."""
    tip = tip_goals or ""
    m = _BTTS_RE.match(tip)
    if m:
        both = home_goals > 0 and away_goals > 0
        return "won" if both == (m.group(1).lower() == "yes") else "lost"

    m = _LINE_RE.match(tip)
    if not m:
        return None
    side, line = m.group(1).lower(), float(m.group(2))
    total = home_goals + away_goals

    if (line * 4) % 2 == 1:  # quarter line: .25 / .75
        a = _settle_line(side, line - 0.25, total)
        b = _settle_line(side, line + 0.25, total)
        if a == b:
            return a
        if "won" in (a, b):
            return "half_won"   # other half pushed
        return "half_lost"      # one half lost, the other pushed
    return _settle_line(side, line, total)


def grade_prediction(pred: Dict[str, Any], result: Optional[str] = None,
                     home_goals: Optional[int] = None,
                     away_goals: Optional[int] = None) -> Dict[str, Any]:
    """
    Return a copy of `pred` settled against a result. Pass the score when you
    have it (it grades the goals tip too), or just H/D/A when you don't.
    With neither, the entry is pending.
    """
    out = dict(pred)
    if home_goals is not None and away_goals is not None:
        home_goals, away_goals = int(home_goals), int(away_goals)
        result = result_from_score(home_goals, away_goals)
        out["score"] = f"{home_goals}-{away_goals}"
        out["goals_outcome"] = grade_goals(pred.get("tip_goals", ""), home_goals, away_goals)
    else:
        out.setdefault("goals_outcome", None)

    if result in ("H", "D", "A"):
        out["actual_result"] = result
        out["outcome"] = grade_tip(pred.get("tip_code", ""), result)
    else:
        out["actual_result"] = None
        out["outcome"] = "pending"
        out["goals_outcome"] = None
    return out


def regrade(pred: Dict[str, Any]) -> Dict[str, Any]:
    """
    Re-settle a stored entry from its own actual_result / score. Entries
    archived before double chance and goals tips were graded have 1X/2X
    marked lost regardless of the result, and no goals verdict.
    """
    score = parse_score(pred.get("score"))
    if score:
        return grade_prediction(pred, home_goals=score[0], away_goals=score[1])
    if pred.get("actual_result") in ("H", "D", "A"):
        return grade_prediction(pred, result=pred["actual_result"])
    return pred


def to_goals(value: Any) -> Optional[int]:
    """A goal count from a CSV/JSON cell, or None for blanks and NaN."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else int(f)
