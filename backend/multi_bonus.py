"""
SportyBet's multi-bet bonus: a share added to a winning ticket's winnings by
how many selections it has, counting only selections at or above a least
price. Filled in from SportyBet's own rules (probe_jackpot.bonus); empty
until then, so tickets show their return without it.

    share(picks) -> 0.05 for 5%
"""

from typing import Any, Dict, List

MIN_ODDS = 1.2                   # the least price a selection needs to count
TABLE: Dict[int, float] = {}     # selections that count → share of the winnings


def share(picks: List[Dict[str, Any]]) -> float:
    if not TABLE:
        return 0.0
    n = sum(1 for p in picks if (p.get("odds") or 0) >= MIN_ODDS)
    best = [v for k, v in TABLE.items() if n >= k]
    return max(best) if best else 0.0
