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

Markets: 1X2, double chance, goals over/under 1.5–3.5, both teams to
score, team goals, clean sheet, win to nil, Asian handicap ±1.5/±2.5,
double chance & goals (from predictor.goal_markets), total corners /
bookings, team corners and most corners (set_pieces.py), and total / team
shots and shots on target (shots.py) — the last two club leagues only.

Prices, best first: SportyBet's own (predictions linked to SportyBet events,
see main._link_sportybet_events), the bookmaker 1X2 odds the prediction
carries (double chance derived from them), else an estimate
from our probability with a typical bookmaker margin, marked "estimated".

Win chance treats the matches as independent and is the model's estimate.
"""

import math
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional

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
    # SportyBet's own ids for the pick (basketball: every line is one SportyBet offers)
    sb: Optional[Dict[str, str]] = None
    sport: str = "football"


def _line(stat: str, line: str, over: bool):
    """Probability of over/under a corners or bookings line (set_pieces.py),
    None when the prediction has none (internationals, unknown teams)."""
    def prob(p: Dict) -> Optional[float]:
        v = ((p.get("set_pieces") or {}).get(stat) or {}).get("over", {}).get(line)
        return None if v is None else (v if over else 1 - v)
    return prob


# (market, market name, code, label template, probability from a prediction — None if unknown)
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
    ("goals_ou", "Total Goals", "O35", "Over 3.5", lambda p: p.get("p_over35")),
    ("goals_ou", "Total Goals", "U35", "Under 3.5",
     lambda p: None if p.get("p_over35") is None else 1 - p["p_over35"]),
    ("btts", "Both Teams to Score", "BTTS-Y", "Both teams score", lambda p: p.get("p_btts")),
    ("btts", "Both Teams to Score", "BTTS-N", "Not both teams score",
     lambda p: None if p.get("p_btts") is None else 1 - p["p_btts"]),
    # No draw no bet: on a draw the leg counts at odds 1, so the slip only
    # reaches its target when the team wins — the straight win's chance at
    # lower odds, which the 1X2 pick always beats.
] + [
    (market, market_name, f"{side}{line.replace('.', '')}",
     f"{'Over' if side == 'O' else 'Under'} {line} {stat}", _line(stat, line, side == "O"))
    for market, market_name, stat, lines in (
        ("corners_ou", "Total Corners", "corners", ("7.5", "8.5", "9.5", "10.5", "11.5")),
        ("cards_ou", "Total Bookings", "bookings", ("2.5", "3.5", "4.5", "5.5", "6.5")))
    for line in lines for side in ("O", "U")
]



def _gm(*path):
    """A probability from the prediction's goal_markets (predictor.goal_markets)."""
    def prob(p: Dict) -> Optional[float]:
        node: Any = p.get("goal_markets")
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        return node if isinstance(node, (int, float)) else None
    return prob


def _sp(stat: str, line: Optional[str] = None, over: bool = True, key: Optional[str] = None):
    """A probability from the prediction's set_pieces (set_pieces.py)."""
    def prob(p: Dict) -> Optional[float]:
        node = (p.get("set_pieces") or {}).get(stat) or {}
        v = node.get(key) if key else (node.get("over") or {}).get(line)
        return None if v is None else (v if over else 1 - v)
    return prob


def _flip(fn):
    return lambda p: None if fn(p) is None else 1 - fn(p)


_PICKS += [
    # Team goals
    *[(f"{side}_goals_ou", f"{'Home' if side == 'home' else 'Away'} Team Goals", f"{s}{line.replace('.', '')}",
       f"{{{side}}} {'over' if s == 'O' else 'under'} {line} goals",
       _gm("team_totals", side, line) if s == "O" else _flip(_gm("team_totals", side, line)))
      for side in ("home", "away") for line in ("0.5", "1.5", "2.5") for s in ("O", "U")],
    # Clean sheet / win to nil (yes only: "no" is the other team scoring)
    ("clean_sheet", "Clean Sheet", "CS-H", "{home} clean sheet", _gm("clean_sheet", "home")),
    ("clean_sheet", "Clean Sheet", "CS-A", "{away} clean sheet", _gm("clean_sheet", "away")),
    ("win_to_nil", "Win to Nil", "WTN-H", "{home} to win to nil", _gm("win_to_nil", "home")),
    ("win_to_nil", "Win to Nil", "WTN-A", "{away} to win to nil", _gm("win_to_nil", "away")),
    # Asian handicap, half lines (no push)
    *[("handicap", "Handicap", code, label, _gm("handicap", key))
      for line in ("1.5", "2.5")
      for code, label, key in ((f"H-{line}", f"{{home}} -{line}", f"home_-{line}"),
                               (f"A+{line}", f"{{away}} +{line}", f"away_+{line}"),
                               (f"A-{line}", f"{{away}} -{line}", f"away_-{line}"),
                               (f"H+{line}", f"{{home}} +{line}", f"home_+{line}"))],
    # Double chance & goals
    *[("dc_goals", "Double Chance & Goals", f"{dc}&{s}{line.replace('.', '')}",
       f"{name} & {'over' if s == 'O' else 'under'} {line}", _gm("dc_total", dc, f"{s.lower()}{line}"))
      for dc, name in (("1X", "Home or Draw"), ("X2", "Draw or Away"), ("12", "Home or Away"))
      for line in ("1.5", "2.5", "3.5") for s in ("O", "U")],
    # Each team's corners, and who wins more
    *[(f"{side}_corners_ou", f"{'Home' if side == 'home' else 'Away'} Team Corners", f"{s}{line.replace('.', '')}",
       f"{{{side}}} {'over' if s == 'O' else 'under'} {line} corners", _sp(f"corners_{side}", line, s == "O"))
      for side, lines in (("home", ("2.5", "3.5", "4.5", "5.5", "6.5")), ("away", ("1.5", "2.5", "3.5", "4.5", "5.5")))
      for line in lines for s in ("O", "U")],
    ("corners_1x2", "Most Corners", "CR-1", "{home} most corners", _sp("corners_1x2", key="home")),
    ("corners_1x2", "Most Corners", "CR-X", "Level on corners", _sp("corners_1x2", key="draw")),
    ("corners_1x2", "Most Corners", "CR-2", "{away} most corners", _sp("corners_1x2", key="away")),
    # Shots and shots on target: the match's and each team's (shots.py, club leagues)
    *[(market, name, f"{s}{line.replace('.', '')}", label.format(ou="over" if s == "O" else "under", line=line),
       _sp(stat, line, s == "O"))
      for market, name, stat, label, lines in (
          ("shots_ou", "Total Shots", "shots", "{ou} {line} shots", ("20.5", "22.5", "24.5", "26.5", "28.5")),
          ("sot_ou", "Total Shots on Target", "sot", "{ou} {line} shots on target", ("6.5", "7.5", "8.5", "9.5", "10.5")),
          ("home_shots_ou", "Home Team Shots", "shots_home", "{{home}} {ou} {line} shots",
           ("10.5", "11.5", "12.5", "13.5", "14.5", "15.5")),
          ("away_shots_ou", "Away Team Shots", "shots_away", "{{away}} {ou} {line} shots",
           ("8.5", "9.5", "10.5", "11.5", "12.5", "13.5")),
          ("home_sot_ou", "Home Team Shots on Target", "sot_home", "{{home}} {ou} {line} shots on target",
           ("2.5", "3.5", "4.5", "5.5", "6.5")),
          ("away_sot_ou", "Away Team Shots on Target", "sot_away", "{{away}} {ou} {line} shots on target",
           ("1.5", "2.5", "3.5", "4.5", "5.5")))
      for line in lines for s in ("O", "U")],
]

# Bookmakers keep more on corners, bookings and specials than on goals
_MARGINS = {"corners_ou": 0.90, "cards_ou": 0.90, "home_corners_ou": 0.90, "away_corners_ou": 0.90,
            "corners_1x2": 0.90, "clean_sheet": 0.92, "win_to_nil": 0.90, "dc_goals": 0.92,
            **{m: 0.90 for m in ("shots_ou", "sot_ou", "home_shots_ou", "away_shots_ou", "home_sot_ou",
                                  "away_sot_ou")}}


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
               min_prob: float = 0.6, markets: Optional[set] = None,
               allowed: Optional[Callable[[str, str], bool]] = None) -> List[Option]:
    """The pickable selections for one prediction, each with its best price.
    `allowed(market, code)` can veto picks (e.g. markets SportyBet codes can't take yet)."""
    if not all(isinstance(pred.get(k), (int, float))
               for k in ("p_home", "p_draw", "p_away", "p_over15", "p_over25")):
        return []
    out = []
    for market, market_name, code, label, prob_of in _PICKS:
        if markets and market not in markets:
            continue
        if allowed and not allowed(market, code):
            continue
        prob = prob_of(pred)
        if prob is None:
            continue
        prob = float(prob)
        if not min_prob <= prob < 0.995:
            continue
        odds, source = None, None
        ids = sportybet_ids(market, code)
        if sportybet_event and ids:
            odds, source = _event_odds(sportybet_event, ids), "sportybet"
        if not odds:
            odds, source = _bookmaker_price(pred, market, code), "bookmaker"
        if not odds:
            odds, source = _MARGINS.get(market, MARGIN) / prob, "estimated"
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


MARKET_NAMES = {m: n for m, n, *_ in _PICKS}


def why_empty(preds: List[Dict], markets: Optional[set], min_prob: float,
              allowed: Optional[Callable[[str, str], bool]] = None,
              only: Optional[Dict[str, set]] = None) -> List[Dict[str, Any]]:
    """Per market, why no match gave a pick: no data for these matches (e.g.
    corners for internationals), the best probability under the minimum, or
    every pick vetoed by `allowed` (not bookable yet). `only` limits a
    market to some of its options (e.g. goal lines)."""
    usable = [p for p in preds if all(isinstance(p.get(k), (int, float)) for k in ("p_home", "p_over25"))]
    out = []
    for market in sorted(markets or MARKET_NAMES, key=list(MARKET_NAMES).index):
        picks = [(code, fn) for m, _, code, _, fn in _PICKS
                 if m == market and (not only or m not in only or code in only[m])]
        per_match = [[v for _, fn in picks if isinstance(v := fn(p), (int, float))] for p in usable]
        with_data = sum(1 for vals in per_match if vals)
        best = max((v for vals in per_match for v in vals), default=None)
        if allowed and not any(allowed(market, code) for code, _ in picks):
            reason = "not_bookable"
        elif not with_data:
            reason = "no_data"
        elif best is not None and best < min_prob:
            reason = "below_minimum"
        else:
            continue
        out.append({"market": market, "name": MARKET_NAMES[market], "reason": reason,
                    "matches_with_data": with_data, "best": round(best, 3) if best is not None else None})
    return out
