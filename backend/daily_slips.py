"""
Daily odds: three slips a day at about 10x, 15x and 20x, built by the
optimizer only from picks the model rates 85% or more.

Every pick being 85%+ doesn't make the slip 85%: the chances multiply, so a
10x slip of 85-90% picks lands a few times in twenty. Each slip carries that
honest combined chance (the optimizer's win_chance), and every slip is kept
and graded, so the record shows how they really do.
"""

from typing import Any, Callable, Dict, List, Optional

import tickets

TARGETS = (10, 15, 20)
MIN_PROB = 0.85
SPREAD = 0.07          # total odds within ±7% of the target
KEEP_DAYS = 120

# Tried in order until one gives a slip: today's matches that SportyBet lists
# first (so the slip can be booked), then more days, then unlisted matches
ATTEMPTS = ({"days": 1, "bookable_only": True}, {"days": 2, "bookable_only": True},
            {"days": 1, "bookable_only": False}, {"days": 2, "bookable_only": False})

PICK_FIELDS = ("home", "away", "date", "time", "league", "market", "market_name", "code", "label",
               "prob", "odds", "odds_source", "bookable")


def request(target: float, attempt: Dict[str, Any]) -> Dict[str, Any]:
    """The optimizer request for one target."""
    return {"target_odds": target, "min_odds": round(target * (1 - SPREAD), 2),
            "max_odds": round(target * (1 + SPREAD), 2), "min_prob": MIN_PROB,
            "max_games": 30, **attempt}


def slip(target: float, result: Dict[str, Any], attempt: Dict[str, Any]) -> Dict[str, Any]:
    """A stored slip from the optimizer's answer (or why there isn't one)."""
    if result.get("error") or not result.get("picks"):
        return {"target": target, "error": result.get("error") or "No slip today.", "picks": [], "status": "none"}
    picks = [{**{k: p.get(k) for k in PICK_FIELDS}, "status": "pending"} for p in result["picks"]]
    return {"target": target, "total_odds": result["total_odds"], "win_chance": result["win_chance"],
            "games": len(picks), "within_target": result.get("within_target", True),
            "days": attempt["days"], "bookable": all(p.get("bookable") for p in picks),
            "picks": picks, "status": "pending"}


def status(picks: List[Dict]) -> str:
    states = [p.get("status", "pending") for p in picks]
    if "lost" in states:
        return "lost"
    if states and all(s in ("won", "void") for s in states):
        return "void" if all(s == "void" for s in states) else "won"
    return "pending"


def grade(s: Dict[str, Any], result_for: Callable[[Dict], Optional[Dict]]) -> bool:
    """Settle a slip's picks from the results; True if anything changed."""
    if s.get("status") not in ("pending",):
        return False
    changed = False
    for p in s["picks"]:
        if p.get("status") != "pending":
            continue
        verdict = tickets.grade_leg(p.get("market", ""), p.get("code", ""), result_for(p))
        if verdict != "pending":
            p["status"] = verdict
            changed = True
    new = status(s["picks"])
    if new != s["status"]:
        s["status"] = new
        changed = True
    return changed
