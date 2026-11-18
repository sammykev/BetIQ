"""
SportyBet's multi-bet bonus (its betslip's "promotion/v2/bonus/plans/valid",
found by probe_jackpot.bonus): a share of the stake × the qualifying legs'
odds added to a winning ticket, by how many legs qualify (priced at least
MIN_ODDS). The share sits in a range per count — the betslip shows the top
of it (× the sports' bonus factor: football 0.6) — and depends on the legs,
so tickets count the bottom of the range (sure) and show the top.

The plan is refreshed from SportyBet every REFRESH_HOURS (refresh); until the
first refresh, the plan as SportyBet gave it on 18 Nov 2026.

    share(picks)      -> the bottom of the range, as a share of the ticket's odds
    share_top(picks)  -> the top of it
"""

import math
import time
from typing import Any, Dict, List, Optional, Tuple

PLAN_PATH = "/promotion/v2/bonus/plans/valid"
REFRESH_HOURS = 6
MIN_ODDS = 1.2
# qualifying legs → (bottom, top) share, 18 Nov 2026
RANGES: Dict[int, Tuple[float, float]] = {
    2: (0, .03), 3: (0, .05), 4: (0, .08), 5: (.05, .12), 6: (.07, .16), 7: (.07, .20), 8: (.10, .25),
    9: (.10, .30), 10: (.10, .33), 11: (.15, .35), 12: (.15, .37), 13: (.15, .40), 14: (.15, .42), 15: (.15, .45),
    16: (.25, .50), 17: (.25, .55), 18: (.30, .60), 19: (.30, .65), 20: (.30, .70), 21: (.35, .75), 22: (.35, .80),
    23: (.35, .85), 24: (.40, .90), 25: (.40, .95), 26: (.40, 1.0), 27: (.45, 1.1), 28: (.45, 1.2), 29: (.50, 1.3),
    30: (.50, 1.6), 31: (.50, 1.8), 32: (.55, 1.9), 33: (.55, 2.0), 34: (.60, 2.1), 35: (.60, 2.2), 36: (.60, 2.3),
    37: (.65, 2.4), 38: (.65, 2.5), 39: (.70, 2.6), 40: (.70, 2.7), 41: (.70, 2.8), 42: (.75, 2.9), 43: (.75, 3.0),
    44: (.80, 3.0), 45: (.80, 3.0), 46: (.85, 3.0), 47: (.85, 3.0), 48: (.90, 3.0), 49: (.90, 3.0), 50: (1.0, 3.0)}
# the top of the range × this per sport (the lowest among a ticket's legs)
FACTORS: Dict[str, float] = {"football": 0.6, "basketball": 1.0, "tennis": 1.0, "table_tennis": 1.0}
_SPORT_IDS = {"sr:sport:1": "football", "sr:sport:2": "basketball", "sr:sport:5": "tennis", "sr:sport:20": "table_tennis"}
_state = {"at": 0.0}


def parse(data: Dict[str, Any]) -> Optional[Tuple[float, Dict[int, Tuple[float, float]], Dict[str, float]]]:
    """(least odds, ranges, factors) from the plan endpoint's data, or None."""
    try:
        plan = (data.get("entityList") or [])[0]
        ranges = {int(r["qualifyingSelections"]): (float(r.get("min") or 0) / 1e4, float(r.get("max") or 0) / 1e4)
                  for r in plan.get("bonusRatios") or []}
        factors = {_SPORT_IDS[f["sportId"]]: float(f["bonusFactor"]) / 1e4
                   for f in data.get("bonusFactorVOList") or []
                   if f.get("sportId") in _SPORT_IDS and f.get("isEnabled", True) and not f.get("tournamentId")}
        least = float(plan.get("qualifyingOddsLimit") or 12000) / 1e4
    except (IndexError, KeyError, TypeError, ValueError):
        return None
    return (least, ranges, factors) if ranges else None


async def refresh(session=None) -> bool:
    """The plan from SportyBet, at most every REFRESH_HOURS. True if updated."""
    global MIN_ODDS
    if time.time() - _state["at"] < REFRESH_HOURS * 3600:
        return False
    _state["at"] = time.time()
    try:
        import jackpot
        import sportybet
        session = session or sportybet.shared_session()
        r = await session.get(sportybet.BASE + PLAN_PATH, headers=jackpot._headers())
        body = r.json()
        got = parse(body.get("data") or {}) if body.get("bizCode") == sportybet.OK else None
    except Exception as e:
        print(f"[Bonus] couldn't read SportyBet's bonus plan: {e}")
        return False
    if not got:
        return False
    MIN_ODDS, ranges, factors = got
    RANGES.clear()
    RANGES.update(ranges)
    FACTORS.update(factors)
    return True


def _qualifying(picks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [p for p in picks if (p.get("odds") or 0) >= MIN_ODDS - 1e-9]


def _range(n: int) -> Tuple[float, float]:
    if n < min(RANGES, default=99):
        return 0.0, 0.0
    return RANGES.get(n) or RANGES[max(RANGES)]


def _of_odds(picks: List[Dict[str, Any]], rate: float) -> float:
    """A bonus rate (on the qualifying legs' odds) as a share of the ticket's odds."""
    q = _qualifying(picks)
    total = math.prod(p.get("odds") or 1 for p in picks)
    return rate * math.prod(p["odds"] for p in q) / total if q and total else 0.0


def share(picks: List[Dict[str, Any]]) -> float:
    return round(_of_odds(picks, _range(len(_qualifying(picks)))[0]), 4)


def share_top(picks: List[Dict[str, Any]]) -> float:
    q = _qualifying(picks)
    factor = min((FACTORS.get(p.get("sport") or "football", 1.0) for p in q), default=1.0)
    return round(_of_odds(picks, _range(len(q))[1] * factor), 4)
