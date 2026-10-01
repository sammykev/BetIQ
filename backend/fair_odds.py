"""
A bookmaker's prices turned into chances (de-vigging). The prices' implied
chances (1/odds) add up to more than 1, the margin; how the margin is taken
back out matters most for clear favourites:

    proportional  each chance divided by the total: takes the margin evenly,
                  so longshots stay overpriced and favourites underpriced
    power         each implied chance raised to the power k that makes them
                  add up to 1: takes more off longshots
    shin          Shin's model (a share z of informed money): the same idea,
                  derived from how bookmakers protect themselves

Bookmakers load more of their margin on longshots (the favourite–longshot
bias), so power and Shin usually price favourites closer to how often they
win. Which one the site uses (METHOD) is chosen by the check on settled
matches (check_fair_odds.py).
"""

import math
from typing import Callable, Dict, List, Sequence

METHOD = "shin"


def _implied(odds: Sequence[float]) -> List[float]:
    return [1.0 / o for o in odds]


def proportional(odds: Sequence[float]) -> List[float]:
    inv = _implied(odds)
    s = sum(inv)
    return [x / s for x in inv]


def _solve(f: Callable[[float], float], lo: float, hi: float, steps: int = 60) -> float:
    """The x in [lo, hi] where the decreasing f(x) crosses 0 (bisection)."""
    for _ in range(steps):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def power(odds: Sequence[float]) -> List[float]:
    inv = _implied(odds)
    if sum(inv) <= 1 or not all(0 < x < 1 for x in inv):
        return proportional(odds)
    k = _solve(lambda k: sum(x ** k for x in inv) - 1, 1.0, 50.0)
    out = [x ** k for x in inv]
    s = sum(out)
    return [x / s for x in out]


def shin(odds: Sequence[float]) -> List[float]:
    inv = _implied(odds)
    s = sum(inv)
    if s <= 1:
        return proportional(odds)

    def probs(z: float) -> List[float]:
        return [(math.sqrt(z * z + 4 * (1 - z) * x * x / s) - z) / (2 * (1 - z)) for x in inv]
    z = _solve(lambda z: sum(probs(z)) - 1, 0.0, 0.99)
    out = probs(z)
    t = sum(out)
    return [x / t for x in out]


METHODS: Dict[str, Callable[[Sequence[float]], List[float]]] = {
    "proportional": proportional, "power": power, "shin": shin}


def fair(odds: Sequence[float], method: str = "") -> List[float]:
    """Chances from prices by `method` (METHOD by default)."""
    return METHODS[method or METHOD](odds)
