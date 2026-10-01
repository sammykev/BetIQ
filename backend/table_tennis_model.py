"""
The table tennis model.

Level: each player's Elo from SportyBet's own results (table_tennis_fit.py;
the leagues it lists — Setka Cup, TT Elite Series, Czech Liga Pro, WTT… —
where players play several matches a day, so a few months give thousands
of matches), blended with SportyBet's match prices.

Shape: one number, A's chance of winning a rally (serve barely matters in
table tennis, and it rotates every two points). From it the exact chance of
every game score (to 11, win by 2 from 10-10), and from those every match
score, games handicap, total points, points handicap, a game's winner and
its points. The rally chance is found so the match chance equals the level.

Games aren't played at one fixed rally chance: form swings within a match.
SWING spreads each game's rally chance around the match's (a normal
mixture), which makes lopsided games and lopsided game scores likelier
than a single number gives — the walk-forward check measures it.

All pure functions; the ratings come from table_tennis_fit.py via Redis.
"""

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

START = 1500.0
K_BASE, K_OFFSET, K_SHAPE = 250.0, 5.0, 0.4
MAX_DEUCE = 12                      # deuce pairs followed (then the rest split evenly)
SWING = 0.03                        # sd of a game's rally chance around the match's (refit by the check)
SWING_NODES = ((-3 ** 0.5, 1 / 6), (0.0, 2 / 3), (3 ** 0.5, 1 / 6))   # 3-point Gauss–Hermite (standard normal)


@lru_cache(maxsize=4096)
def _game(p: float) -> Tuple[Tuple[Tuple[int, int], float], ...]:
    """A game's final scores (A, B) and their chances, A winning a rally with p."""
    q = 1 - p
    # Chance of reaching (a, b), both on 10 or fewer (none of these has ended the game)
    reach = [[0.0] * 11 for _ in range(11)]
    reach[0][0] = 1.0
    for a in range(11):
        for b in range(11):
            if a or b:
                reach[a][b] = (reach[a - 1][b] * p if a else 0.0) + (reach[a][b - 1] * q if b else 0.0)
    out: Dict[Tuple[int, int], float] = {}
    for k in range(10):                                    # 11-k and k-11
        out[(11, k)] = reach[10][k] * p
        out[(k, 11)] = reach[k][10] * q
    # From 10-10: two points in a row win; split points go round again
    d = reach[10][10]
    for i in range(MAX_DEUCE):
        out[(12 + i, 10 + i)] = d * p * p
        out[(10 + i, 12 + i)] = d * q * q
        d *= 2 * p * q
    # Past MAX_DEUCE: whoever is likelier, at the next score (vanishing)
    w = p * p / (p * p + q * q)
    out[(12 + MAX_DEUCE, 10 + MAX_DEUCE)] = out.get((12 + MAX_DEUCE, 10 + MAX_DEUCE), 0) + d * w
    out[(10 + MAX_DEUCE, 12 + MAX_DEUCE)] = out.get((10 + MAX_DEUCE, 12 + MAX_DEUCE), 0) + d * (1 - w)
    return tuple((k, v) for k, v in out.items() if v > 1e-12)


def game_scores(p: float, swing: float = SWING) -> Dict[Tuple[int, int], float]:
    """A game's scores, the rally chance spread by swing."""
    p = min(max(p, 0.02), 0.98)
    out: Dict[Tuple[int, int], float] = {}
    for z, w in (SWING_NODES if swing > 0 else ((0.0, 1.0),)):
        pp = round(min(max(p + z * swing, 0.02), 0.98), 4)
        for k, v in _game(pp):
            out[k] = out.get(k, 0) + w * v
    return out


def p_game(p: float, swing: float = SWING) -> float:
    return sum(v for (a, b), v in game_scores(p, swing).items() if a > b)


@dataclass
class MatchDist:
    """Everything a match's markets need (tennis_model.MatchDist's names:
    "sets" are games here, "games" are points)."""
    p_win: float
    sets: Dict[Tuple[int, int], float]                 # games won (A, B)
    games_diff: Dict[int, float]                       # A's points minus B's
    total_games: Dict[int, float]                      # points in the match
    first_set: Dict[Tuple[int, int], float]            # a game's score (any game: all alike)
    best_of: int = 5
    p: float = 0.5
    detail: Dict[str, float] = field(default_factory=dict)
    games: Dict[Tuple[int, int], float] = field(default_factory=dict)   # (A's points, B's points) -> chance

    def p_total_over(self, line: float) -> float:
        return sum(v for t, v in self.total_games.items() if t > line)

    def p_handicap(self, line: float) -> float:
        """A's points + line beat B's."""
        return sum(v for d, v in self.games_diff.items() if d + line > 0)

    def p_set_handicap(self, line: float) -> float:
        return sum(v for (a, b), v in self.sets.items() if a - b + line > 0)

    def p_first_set(self) -> float:
        return sum(v for (a, b), v in self.first_set.items() if a > b)

    def p_first_set_total_over(self, line: float) -> float:
        return sum(v for (a, b), v in self.first_set.items() if a + b > line)

    def p_total_sets_over(self, line: float) -> float:
        return sum(v for (a, b), v in self.sets.items() if a + b > line)

    def p_odd_total(self) -> float:
        return sum(v for t, v in self.total_games.items() if t % 2)


def match_dist(p: float, best_of: int = 5, swing: float = SWING) -> MatchDist:
    need = best_of // 2 + 1
    gd = game_scores(p, swing)
    # Coarsen the game's scores to (A won?, points diff, points total) for the match DP
    per: Dict[Tuple[bool, int, int], float] = {}
    for (a, b), v in gd.items():
        k = (a > b, a - b, a + b)
        per[k] = per.get(k, 0) + v
    state = {(0, 0, 0, 0): 1.0}
    done: Dict[Tuple[int, int, int, int], float] = {}
    while state:
        nxt: Dict[Tuple[int, int, int, int], float] = {}
        for (ga, gb, d, t), pr in state.items():
            for (won, dd, tt), v in per.items():
                k = (ga + won, gb + (not won), d + dd, t + tt)
                target = done if k[0] == need or k[1] == need else nxt
                target[k] = target.get(k, 0) + pr * v
        state = nxt
    sets: Dict[Tuple[int, int], float] = {}
    diff: Dict[int, float] = {}
    total: Dict[int, float] = {}
    points: Dict[Tuple[int, int], float] = {}
    for (ga, gb, d, t), pr in done.items():
        sets[(ga, gb)] = sets.get((ga, gb), 0) + pr
        diff[d] = diff.get(d, 0) + pr
        total[t] = total.get(t, 0) + pr
        k = ((t + d) // 2, (t - d) // 2)
        points[k] = points.get(k, 0) + pr
    p_win = sum(v for (a, b), v in sets.items() if a > b)
    return MatchDist(p_win, sets, diff, total, gd, best_of, p, games=points)


def p_match(p: float, best_of: int = 5, swing: float = SWING) -> float:
    g = p_game(p, swing)
    need = best_of // 2 + 1
    # A wins `need` games before B does: sum over B's games k < need
    from math import comb
    return sum(comb(need - 1 + k, k) * g ** need * (1 - g) ** k for k in range(need))


def solve_rally(p_win: float, best_of: int = 5, swing: float = SWING) -> float:
    """The rally chance that makes A's match chance p_win."""
    p_win = min(max(p_win, 0.005), 0.995)
    lo, hi = 0.2, 0.8
    for _ in range(40):
        mid = (lo + hi) / 2
        if p_match(mid, best_of, swing) < p_win:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ── Ratings ────────────────────────────────────────────────────────────────

def elo_p(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def shrink(p: float, s: float) -> float:
    """p pulled toward even on the logit scale (s < 1), as the check fits."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    return 1.0 / (1.0 + ((1 - p) / p) ** s)


def k_factor(n: int) -> float:
    return K_BASE / (n + K_OFFSET) ** K_SHAPE


@dataclass
class Player:
    name: str
    elo: float = START
    n: int = 0
    last: int = 0                     # last match (unix seconds)

    def to_json(self) -> list:
        return [self.name, round(self.elo, 1), self.n, self.last]

    @classmethod
    def from_json(cls, j: list) -> "Player":
        return cls(j[0], float(j[1]), int(j[2]), int(j[3]) if len(j) > 3 else 0)


def best_of(tournament: str) -> int:
    t = (tournament or "").lower()
    return 7 if any(x in t for x in ("olympic", "world championship", "wtt champions", "wtt star", "grand smash")) else 5


def predict(players: Dict[str, Player], p1: str, p2: str, market_p1: Optional[float] = None,
            model_weight: float = 0.5, bo: int = 5, swing: float = SWING, min_matches: int = 10,
            shrink_s: float = 1.0) -> Optional[MatchDist]:
    """A match's distribution: Elo level blended with the market's. None when
    either player is unrated and there's no market price."""
    a, b = players.get(p1), players.get(p2)
    known = a is not None and b is not None and a.n >= min_matches and b.n >= min_matches
    p_elo = shrink(elo_p(a.elo, b.elo), shrink_s) if known else None
    if known:
        level = p_elo if market_p1 is None else model_weight * p_elo + (1 - model_weight) * market_p1
    elif market_p1 is not None:
        level = market_p1
    else:
        return None
    md = match_dist(solve_rally(level, bo, swing), bo, swing)
    md.detail = {"level": round(level, 4), "elo": round(p_elo, 4) if p_elo is not None else None,
                 "market": round(market_p1, 4) if market_p1 is not None else None, "known": known,
                 "ratings": {side: {"elo": round(p.elo), "matches": p.n} if p is not None else None
                             for side, p in (("home", a), ("away", b))}}
    return md
