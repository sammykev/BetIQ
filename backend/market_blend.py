"""
Blending our chances with SportyBet's in every football market where that
has been shown to help (check_market_blend.py).

A market's outcomes are blended only as a complete set — over/under at one
line, a handicap and its mirror, both teams to score yes/no, corners 1X2 —
when SportyBet prices every outcome in the set, so its margin can be taken
off (fair_odds.py) and the blended chances still add up to 1:

    chance = w * ours + (1 - w) * SportyBet's fair chance

`w` (our share) is per market, from the nightly check. A market without a
weight there is pulled towards SportyBet by how far we disagree (shrink). 1X2 is blended where predictions
are built (main._blend_1x2), so it isn't in here.
"""

import re
from typing import Dict, List, Optional, Sequence

import fair_odds

REPORT_KEY = "betiq:market_blend"
_OU = re.compile(r"^[OU](\d+)$")
_HCP = re.compile(r"^([HA])([+-]\d+(?:\.\d+)?)$")
WHOLE = {"btts", "corners_1x2"}           # the whole market is one set
SKIP = {"1x2", "double_chance", "dnb"}    # 1X2 and its by-products: blended at the source
SUM_TOL = 0.04                            # our chances in a set must add up to 1 within this

# market → our share, loaded from Redis by the server (main._market_blend_load)
weights: Dict[str, float] = {}


def group(market: str, code: str) -> Optional[str]:
    """Which complete set an outcome belongs to (None: not blended)."""
    if market in SKIP:
        return None
    if market in WHOLE:
        return market
    m = _OU.match(code)
    if m and market.endswith("_ou"):
        return f"{market}:{m.group(1)}"
    m = _HCP.match(code) if market == "handicap" else None
    if m:
        line = float(m.group(2))
        return f"handicap:{line if m.group(1) == 'H' else -line:+g}"
    return None


def sets(rows: Sequence) -> Dict[str, List[int]]:
    """Indices of rows (each with .market/.code or [market, code, …]) by complete set."""
    out: Dict[str, List[int]] = {}
    for i, r in enumerate(rows):
        market, code = (r.market, r.code) if hasattr(r, "market") else (r[0], r[1])
        g = group(market, code)
        if g:
            out.setdefault(g, []).append(i)
    return {g: ix for g, ix in out.items() if len(ix) == (3 if g == "corners_1x2" else 2)}


def complete(ours: Sequence[float], odds: Sequence[Optional[float]]) -> bool:
    return (all(isinstance(o, (int, float)) and o > 1.0 for o in odds)
            and abs(sum(ours) - 1.0) <= SUM_TOL)


def blend(ours: Sequence[float], odds: Sequence[float], w: float, method: str = "") -> List[float]:
    s = sum(ours)
    fair = fair_odds.fair(list(odds), method)
    return [w * a / s + (1 - w) * b for a, b in zip(ours, fair)]


# When we disagree with SportyBet: settled outcomes from 45 days (edge_check.py,
# 11 Oct 2026, 1,700 priced) came in about 65% of the way from SportyBet's fair
# chance to ours while the gap was small, and no more than ~10 points above
# SportyBet's however far ours went (ours 72.7% vs its 37.5%: 46.7% came in)
AGREE_SHARE = 0.65
GAP_KNEE = 0.15


def shrink(ours: Sequence[float], odds: Sequence[float]) -> List[float]:
    """A complete set's chances pulled towards SportyBet's fair ones by how
    far they disagree: fair + share × (ours − fair), the share falling past
    GAP_KNEE so the gap counts for at most AGREE_SHARE × GAP_KNEE."""
    s = sum(ours)
    fair = fair_odds.fair(list(odds))
    out = []
    for a, b in zip(ours, fair):
        gap = a / s - b
        share = AGREE_SHARE if abs(gap) <= GAP_KNEE else AGREE_SHARE * GAP_KNEE / abs(gap)
        out.append(b + share * gap)
    t = sum(out)
    return [x / t for x in out]


def apply(market: str, probs: Dict[str, float], odds: Dict[str, Optional[float]],
          w: Optional[Dict[str, float]] = None, shrink_rest: bool = False) -> Dict[str, float]:
    """{code: chance} for one market's outcomes, blended in each complete set
    SportyBet prices fully, if the market has a weight."""
    share = (weights if w is None else w).get(market)
    if (share is None or share >= 1.0) and not shrink_rest:
        return probs
    codes = list(probs)
    out = dict(probs)
    for ix in sets([(market, c) for c in codes]).values():
        cs = [codes[i] for i in ix]
        ours, os_ = [probs[c] for c in cs], [odds.get(c) for c in cs]
        if complete(ours, os_):
            got = blend(ours, os_, share) if share is not None and share < 1.0 else shrink(ours, os_)
            for c, p in zip(cs, got):
                out[c] = p
    return out
