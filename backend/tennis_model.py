"""
The tennis model.

Level: surface-aware Elo (every ATP/WTA match since 2000 — tour, Challenger,
ITF — tennis_fit.py), blended with SportyBet's match prices where it has
them. Shape: each player's chance of winning a point on serve, from his
serve and return record against the tour's, shifted together so the match
chance matches the level. From those two numbers the exact chances of every
score follow (hold, tiebreak, set, match), and with them every market
SportyBet prices: winner, set betting, first set, set handicap, games
handicap, total games (whole match and a set).

All pure functions; the ratings come from tennis_fit.py via Redis.
"""

from dataclasses import dataclass, field
from functools import lru_cache
from math import log10
from typing import Dict, List, Optional, Tuple

SURFACES = ("Hard", "Clay", "Grass")
# Elo (FiveThirtyEight's tennis Elo): k shrinks with a player's matches
K_BASE, K_OFFSET, K_SHAPE = 250.0, 5.0, 0.4
START = 1500.0
SURFACE_WEIGHT = 0.5          # the surface rating's share in the blend (a player's overall rating the rest)
DEFAULT_SPW = {"ATP": 0.64, "WTA": 0.56}   # a point won on serve, tour average (refit by tennis_fit)


# ── Point → game → set → match ─────────────────────────────────────────────

def p_hold(p: float) -> float:
    """The server wins his game, winning each point with p."""
    q = 1 - p
    deuce = p * p / (p * p + q * q) if p * p + q * q > 0 else 0.5
    return p ** 4 * (1 + 4 * q + 10 * q * q) + 20 * (p * q) ** 3 * deuce


@lru_cache(maxsize=65536)
def _tiebreak(pa: float, pb: float, a: int, b: int, a_serving: bool, served: int) -> float:
    """A wins the tiebreak from a-b; the server changes after the first point, then every two."""
    if a >= 7 and a - b >= 2:
        return 1.0
    if b >= 7 and b - a >= 2:
        return 0.0
    if a >= 6 and b >= 6 and a == b:
        # From level at 6-6 on: win a two-point pair (one serve each)
        w, l = pa * (1 - pb), (1 - pa) * pb
        return w / (w + l) if w + l > 0 else 0.5
    p = pa if a_serving else 1 - pb
    # After this point: first point of the tiebreak switches the server; then every second point
    nxt_served = served + 1
    switch = nxt_served == 1 or (nxt_served > 1 and (nxt_served - 1) % 2 == 0)
    nxt_serving = (not a_serving) if switch else a_serving
    return p * _tiebreak(pa, pb, a + 1, b, nxt_serving, nxt_served) + (1 - p) * _tiebreak(pa, pb, a, b + 1, nxt_serving, nxt_served)


def p_tiebreak(pa: float, pb: float, a_serves_first: bool = True) -> float:
    return _tiebreak(round(pa, 5), round(pb, 5), 0, 0, a_serves_first, 0)


def set_scores(pa: float, pb: float, a_serves_first: bool = True) -> Dict[Tuple[int, int], float]:
    """{(A's games, B's games): chance} over a set's final scores (tiebreak at 6-6)."""
    ha, hb = p_hold(pa), p_hold(pb)
    tb = {True: p_tiebreak(pa, pb, True), False: p_tiebreak(pa, pb, False)}
    out: Dict[Tuple[int, int], float] = {}
    state = {(0, 0): 1.0}
    while state:
        nxt: Dict[Tuple[int, int], float] = {}
        for (a, b), pr in state.items():
            if pr < 1e-12:
                continue
            a_serves = ((a + b) % 2 == 0) == a_serves_first
            if a == 6 and b == 6:
                w = tb[a_serves]
                out[(7, 6)] = out.get((7, 6), 0) + pr * w
                out[(6, 7)] = out.get((6, 7), 0) + pr * (1 - w)
                continue
            win = ha if a_serves else 1 - hb
            for (na, nb), pp in (((a + 1, b), win), ((a, b + 1), 1 - win)):
                done = (na >= 6 or nb >= 6) and abs(na - nb) >= 2 or na == 7 or nb == 7
                if done:
                    out[(na, nb)] = out.get((na, nb), 0) + pr * pp
                else:
                    nxt[(na, nb)] = nxt.get((na, nb), 0) + pr * pp
        state = nxt
    return out


def _set_dist(pa: float, pb: float) -> Dict[Tuple[int, int], float]:
    """A set's scores with the first server unknown (either, equally)."""
    x, y = set_scores(pa, pb, True), set_scores(pa, pb, False)
    return {k: 0.5 * x.get(k, 0) + 0.5 * y.get(k, 0) for k in set(x) | set(y)}


@dataclass
class MatchDist:
    """Everything a match's markets need."""
    p_win: float                                   # A wins the match
    sets: Dict[Tuple[int, int], float]             # final set score (A, B) -> chance
    games_diff: Dict[int, float]                   # A's games minus B's -> chance
    total_games: Dict[int, float]                  # games in the match -> chance
    first_set: Dict[Tuple[int, int], float]        # the first set's score
    pa: float = 0.0
    pb: float = 0.0
    detail: Dict[str, float] = field(default_factory=dict)
    games: Dict[Tuple[int, int], float] = field(default_factory=dict)   # (A's games, B's games) -> chance

    def p_total_over(self, line: float) -> float:
        return sum(p for g, p in self.total_games.items() if g > line)

    def p_handicap(self, line: float) -> float:
        """A's games + line beat B's."""
        return sum(p for d, p in self.games_diff.items() if d + line > 0)

    def p_set_handicap(self, line: float) -> float:
        return sum(pr for (a, b), pr in self.sets.items() if a - b + line > 0)

    def p_first_set(self) -> float:
        return sum(p for (a, b), p in self.first_set.items() if a > b)

    def p_first_set_total_over(self, line: float) -> float:
        return sum(pr for (a, b), pr in self.first_set.items() if a + b > line)


def match_dist(pa: float, pb: float, best_of: int = 3) -> MatchDist:
    need = best_of // 2 + 1
    sd = _set_dist(pa, pb)
    # State: (sets A, sets B, games diff, total games) -> chance
    state = {(0, 0, 0, 0): 1.0}
    done: Dict[Tuple[int, int, int, int], float] = {}
    while state:
        nxt: Dict[Tuple[int, int, int, int], float] = {}
        for (sa, sb, d, t), pr in state.items():
            for (ga, gb), ps in sd.items():
                k = (sa + (ga > gb), sb + (gb > ga), d + ga - gb, t + ga + gb)
                target = done if k[0] == need or k[1] == need else nxt
                target[k] = target.get(k, 0) + pr * ps
        state = nxt
    sets: Dict[Tuple[int, int], float] = {}
    diff: Dict[int, float] = {}
    total: Dict[int, float] = {}
    games: Dict[Tuple[int, int], float] = {}
    for (sa, sb, d, t), pr in done.items():
        sets[(sa, sb)] = sets.get((sa, sb), 0) + pr
        diff[d] = diff.get(d, 0) + pr
        total[t] = total.get(t, 0) + pr
        g = ((t + d) // 2, (t - d) // 2)
        games[g] = games.get(g, 0) + pr
    p_win = sum(p for (a, b), p in sets.items() if a > b)
    return MatchDist(p_win, sets, diff, total, sd, pa, pb, games=games)


def p_match(pa: float, pb: float, best_of: int = 3) -> float:
    sd = _set_dist(pa, pb)
    s = sum(p for (a, b), p in sd.items() if a > b)
    if best_of == 5:
        return s ** 3 * (1 + 3 * (1 - s) + 6 * (1 - s) ** 2)
    return s * s * (1 + 2 * (1 - s))


def solve_serve(p_win: float, pa: float, pb: float, best_of: int = 3) -> Tuple[float, float]:
    """Shift both serve chances (A's up, B's down, or the reverse) until A's
    chance of winning a best-of-3 match is p_win: the level from the
    ratings (and market), the shape from the serve/return record."""
    lo, hi = -0.25, 0.25
    p_win = min(0.995, max(0.005, p_win))
    for _ in range(40):
        mid = (lo + hi) / 2
        a, b = min(0.97, max(0.2, pa + mid)), min(0.97, max(0.2, pb - mid))
        if p_match(a, b, 3) < p_win:
            lo = mid
        else:
            hi = mid
    mid = (lo + hi) / 2
    return min(0.97, max(0.2, pa + mid)), min(0.97, max(0.2, pb - mid))


# ── Ratings ────────────────────────────────────────────────────────────────

def elo_p(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def k_factor(n: int, mult: float = 1.0) -> float:
    return mult * K_BASE / (n + K_OFFSET) ** K_SHAPE


def shrink(p: float, s: float) -> float:
    """p pulled toward even on the logit scale (s < 1): Elo's chances run
    overconfident (ratings are estimates); tennis_fit fits s."""
    p = min(max(p, 1e-6), 1 - 1e-6)
    return 1.0 / (1.0 + ((1 - p) / p) ** s)


@dataclass
class Player:
    name: str
    tour: str
    elo: float = START
    n: int = 0
    surf: Dict[str, float] = field(default_factory=lambda: {s: START for s in SURFACES})
    surf_n: Dict[str, int] = field(default_factory=lambda: {s: 0 for s in SURFACES})
    last: str = ""
    # Serve and return points (recency-weighted): [played, won]
    sv: List[float] = field(default_factory=lambda: [0.0, 0.0])
    rt: List[float] = field(default_factory=lambda: [0.0, 0.0])

    def rating(self, surface: str, weight: Optional[float] = None) -> float:
        s = surface if surface in SURFACES else "Hard"
        w = SURFACE_WEIGHT if weight is None else weight
        return (1 - w) * self.elo + w * self.surf[s]

    def to_json(self) -> list:
        return [self.name, self.tour, round(self.elo, 1), self.n, [round(self.surf[s], 1) for s in SURFACES],
                [self.surf_n[s] for s in SURFACES], self.last, [round(x, 1) for x in self.sv], [round(x, 1) for x in self.rt]]

    @classmethod
    def from_json(cls, j: list) -> "Player":
        return cls(j[0], j[1], j[2], j[3], dict(zip(SURFACES, j[4])), dict(zip(SURFACES, j[5])), j[6], list(j[7]), list(j[8]))


def serve_chances(a: Player, b: Player, avg: Dict[str, float], prior_pts: float = 300.0) -> Tuple[float, float]:
    """A's and B's chance of winning a point on serve against each other,
    from their serve and return records (shrunk to the tour's by prior_pts)."""
    t = a.tour
    base = avg.get(t, DEFAULT_SPW.get(t, 0.6))

    def rate(pts: List[float], mean: float) -> float:
        return (pts[1] + prior_pts * mean) / (pts[0] + prior_pts)
    a_sv, b_sv = rate(a.sv, base), rate(b.sv, base)
    a_rt, b_rt = rate(a.rt, 1 - base), rate(b.rt, 1 - base)
    pa = base + (a_sv - base) - (b_rt - (1 - base))
    pb = base + (b_sv - base) - (a_rt - (1 - base))
    return min(0.9, max(0.35, pa)), min(0.9, max(0.35, pb))


def name_key(name: str) -> str:
    """ "Sinner, Jannik" and "Jannik Sinner" -> "jannik sinner" (as player_props)."""
    import player_props as pp
    return pp.name_key(name)


def tour_of(tournament: str) -> str:
    t = (tournament or "").lower()
    return "WTA" if ("wta" in t or "women" in t or "ladies" in t or "fed cup" in t or "bjk" in t) else "ATP"


def predict(players: Dict[str, Player], avg: Dict[str, float], p1: str, p2: str, surface: str,
            best_of: int = 3, market_p1: Optional[float] = None, model_weight: float = 0.5,
            tour: str = "ATP", shrink_s: float = 1.0, surface_weight: Optional[float] = None) -> Optional[MatchDist]:
    """A match's distribution: Elo level blended with the market's, shaped
    by serve and return. None when neither player is known and there's no
    market price."""
    def find(name: str) -> Optional[Player]:
        k = name_key(name)
        return players.get(f"{tour}|{k}") or players.get(f"{'WTA' if tour == 'ATP' else 'ATP'}|{k}")
    a, b = find(p1), find(p2)
    known = a is not None and b is not None and a.n >= 5 and b.n >= 5
    if known:
        p_elo = shrink(elo_p(a.rating(surface, surface_weight), b.rating(surface, surface_weight)), shrink_s)
        level = p_elo if market_p1 is None else model_weight * p_elo + (1 - model_weight) * market_p1
    elif market_p1 is not None:
        level = market_p1
    else:
        return None
    a = a or Player(p1, tour)
    b = b or Player(p2, tour)
    pa0, pb0 = serve_chances(a, b, avg)
    pa, pb = solve_serve(level, pa0, pb0)
    md = match_dist(pa, pb, best_of)
    md.detail = {"level": round(level, 4), "elo": round(p_elo, 4) if known else None,
                 "market": round(market_p1, 4) if market_p1 is not None else None, "known": known}
    return md
