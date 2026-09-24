"""
Slip optimizer: from the upcoming predictions, pick at most one selection
per match so the slip's total odds land in a target range (e.g. 5–10x or
4,000–12,000x) with the highest chance that every pick wins.

Each candidate pick has the model's probability p and a price (odds). In
logs the slip is additive — total odds = Σ log(odds), win chance =
Σ log(p) — so choosing picks is a knapsack over log-odds with one item per
match: maximise Σ log(p) with Σ log(odds) inside [log lo, log hi], using at
most `max_games` picks. Solved exactly by dynamic programming over log-odds
in steps of 0.01 (1%), not by a greedy guess.

Prices, best first: SportyBet's own (predictions linked to SportyBet events,
see main._link_sportybet_events), the bookmaker 1X2 odds the prediction
carries (double chance derived from them), else an estimate from our
probability with a typical bookmaker margin, marked "estimated".

Win chance treats the matches as independent and is the model's estimate.
"""

import math
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

import numpy as np

from booking_slip import _event_odds, sportybet_ids

MARGIN = 0.93          # bookmaker payout assumed when estimating a price
STEP = 0.01            # log-odds resolution of the search (1%)
MAX_GAMES = 30         # SportyBet's limit per booking code
MAX_MATCHES = 150      # candidates considered, best first


@dataclass
class Option:
    home: str
    away: str
    date: str
    time: str
    league: str
    market: str
    market_name: str
    code: str
    label: str
    prob: float
    odds: float
    odds_source: str   # "sportybet" | "bookmaker" | "estimated"


# (market, market name, code, label template, probability from a prediction)
_PICKS = [
    ("1x2", "Match Result", "1", "{home} Win", lambda p: p["p_home"]),
    ("1x2", "Match Result", "X", "Draw", lambda p: p["p_draw"]),
    ("1x2", "Match Result", "2", "{away} Win", lambda p: p["p_away"]),
    ("double_chance", "Double Chance", "1X", "Home or Draw", lambda p: p["p_home"] + p["p_draw"]),
    ("double_chance", "Double Chance", "12", "Home or Away", lambda p: p["p_home"] + p["p_away"]),
    ("double_chance", "Double Chance", "X2", "Draw or Away", lambda p: p["p_draw"] + p["p_away"]),
    ("goals_ou", "Total Goals", "O15", "Over 1.5", lambda p: p["p_over15"]),
    ("goals_ou", "Total Goals", "U15", "Under 1.5", lambda p: 1 - p["p_over15"]),
    ("goals_ou", "Total Goals", "O25", "Over 2.5", lambda p: p["p_over25"]),
    ("goals_ou", "Total Goals", "U25", "Under 2.5", lambda p: 1 - p["p_over25"]),
]


def _bookmaker_price(pred: Dict, market: str, code: str) -> Optional[float]:
    """1X2 odds the prediction carries, and double chance derived from them."""
    o = {"1": pred.get("odds_home"), "X": pred.get("odds_draw"), "2": pred.get("odds_away")}
    if not all(isinstance(v, (int, float)) and v > 1 for v in o.values()):
        return None
    if market == "1x2":
        return float(o[code])
    if market == "double_chance":
        a, b = {"1X": ("1", "X"), "12": ("1", "2"), "X2": ("X", "2")}[code]
        return 1 / (1 / o[a] + 1 / o[b])
    return None


def candidates(pred: Dict, sportybet_event: Optional[Dict] = None,
               min_prob: float = 0.6, markets: Optional[set] = None) -> List[Option]:
    """The pickable selections for one prediction, each with its best price."""
    if not all(isinstance(pred.get(k), (int, float))
               for k in ("p_home", "p_draw", "p_away", "p_over15", "p_over25")):
        return []
    out = []
    for market, market_name, code, label, prob_of in _PICKS:
        if markets and market not in markets:
            continue
        prob = float(prob_of(pred))
        if not min_prob <= prob < 0.995:
            continue
        odds, source = None, None
        ids = sportybet_ids(market, code)
        if sportybet_event and ids:
            odds, source = _event_odds(sportybet_event, ids), "sportybet"
        if not odds:
            odds, source = _bookmaker_price(pred, market, code), "bookmaker"
        if not odds:
            odds, source = MARGIN / prob, "estimated"
        if odds <= 1.01:
            continue
        out.append(Option(pred["home"], pred["away"], pred["date"], pred.get("time") or "",
                          pred.get("league_name") or pred.get("league") or "", market, market_name,
                          code, label.format(home=pred["home"], away=pred["away"]),
                          round(prob, 4), round(float(odds), 2), source))
    return out


def _miss(total: float, lo: float, hi: float) -> float:
    """How far (in log-odds) a total is from the range; 0 inside it."""
    return max(0.0, math.log(lo) - math.log(total), math.log(total) - math.log(hi))


def _solve(groups: List[List[Option]], L: int, W: int, max_games: int) -> Optional[List[Option]]:
    """Exact group knapsack: ≤ one option per group, ≤ max_games picks,
    rounded log-odds in [L, W], lowest Σ -log(p)."""
    inf = np.inf
    dp = np.full((max_games + 1, W + 1), inf)
    dp[0, 0] = 0.0
    weights, choices = [], []
    for g in groups:
        w = [max(1, int(round(math.log(o.odds) / STEP))) for o in g]
        c = [-math.log(o.prob) for o in g]
        new = dp.copy()
        ch = np.full(dp.shape, -1, dtype=np.int8)
        for j, (wj, cj) in enumerate(zip(w, c)):
            if wj > W:
                continue
            cand = np.full(dp.shape, inf)
            cand[1:, wj:] = dp[:-1, :W + 1 - wj] + cj
            better = cand < new
            new[better] = cand[better]
            ch[better] = j
        dp = new
        weights.append(w)
        choices.append(ch)

    window = dp[1:, L:W + 1]
    if not np.isfinite(window).any():
        return None
    gi, wi = np.unravel_index(np.argmin(window), window.shape)
    n, w = int(gi) + 1, int(wi) + L
    picks: List[Option] = []
    for i in range(len(groups) - 1, -1, -1):
        j = int(choices[i][n, w])
        if j >= 0:
            picks.append(groups[i][j])
            n, w = n - 1, w - weights[i][j]
    return picks


def optimize(groups: List[List[Option]], lo: float, hi: float,
             max_games: int = MAX_GAMES) -> Optional[Dict]:
    """The slip (≤ one option per group) with total odds in [lo, hi] and the
    highest win chance, or None if no combination reaches the range."""
    if lo < 1 or hi < lo:
        raise ValueError("target odds need 1 ≤ min ≤ max")
    max_games = max(1, min(int(max_games), MAX_GAMES))
    groups = [g for g in groups if g]
    # Best matches first when there are too many: highest-probability pick
    groups = sorted(groups, key=lambda g: -max(o.prob for o in g))[:MAX_MATCHES]

    # The search works on prices rounded to STEP, so its total can land just
    # outside the range; re-aim at the real total until it's inside.
    L = max(1, int(math.ceil(math.log(lo) / STEP)))
    W = int(math.floor(math.log(hi) / STEP))
    best: Optional[List[Option]] = None
    for _ in range(6):
        if L > W:
            break
        picks = _solve(groups, L, W, max_games)
        if picks is None:
            break
        total = math.prod(o.odds for o in picks)
        if best is None or _miss(total, lo, hi) < _miss(math.prod(o.odds for o in best), lo, hi):
            best = picks
        if lo <= total <= hi:
            break
        if total < lo:
            L += int(math.ceil((math.log(lo) - math.log(total)) / STEP)) + 1
        else:
            W -= int(math.ceil((math.log(total) - math.log(hi)) / STEP)) + 1
    if best is None:
        return None
    picks = sorted(best, key=lambda o: (o.date, o.time, o.home))

    total = math.prod(o.odds for o in picks)
    return {
        "picks": [asdict(o) for o in picks],
        "games": len(picks),
        "total_odds": round(total, 2),
        "win_chance": round(math.prod(o.prob for o in picks), 4),
        "within_target": lo <= total <= hi,
        "estimated_prices": sum(o.odds_source == "estimated" for o in picks),
    }
