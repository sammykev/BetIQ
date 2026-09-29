"""
Player props, for any sport: a player's expected count of a stat in one
game, and the chance of any line.

Expected count = expected minutes x the player's rate per minute x the game
(how many of the stat his team should make tonight, against its usual).

- Minutes: the player's recent games, recent ones counting more (HALF_LIFE
  games), with games he didn't play left out: props are void when a player
  doesn't play, so the question is only how long he plays when he does.
- Rate per minute: his recent rate, pulled towards a prior (his position's,
  or the league's) by PRIOR_MINUTES worth of play — a player with a few
  good games isn't taken at his hottest.
- Distribution: counts spread wider than Poisson (a player's night swings
  with fouls, blowouts, how the ball moves), so a negative binomial whose
  extra spread (dispersion) is measured per stat on real games; minutes'
  own spread widens it further.

A line's chance is P(count > line) under that distribution; the walk-forward
check (backtest in props collectors) compares those chances with what came
in, per stat, and sets the dispersion.
"""

from dataclasses import dataclass, field
from math import exp, lgamma, log
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

HALF_LIFE = 10          # games: a game's weight halves every 10 games back
PRIOR_MINUTES = 150.0   # a rate's pull to its prior, in minutes of play
MIN_GAMES = 5           # games played before a player is priced
MIN_MINUTES = 8.0       # a game shorter than this says little about the rate


@dataclass
class Game:
    """One game of one player: minutes and his counts ({"pts": 18, "reb": 7, …})."""
    date: str
    minutes: float
    stats: Dict[str, float]
    started: bool = True
    team_for: Optional[float] = None       # his team's total of the stat's base (points, shots…) that game


@dataclass
class Projection:
    stat: str
    mean: float
    minutes: float
    rate: float
    games: int
    dispersion: float                      # negative binomial r: smaller = wider
    detail: Dict[str, float] = field(default_factory=dict)

    def p_over(self, line: float) -> float:
        """P(count > line) for a half line (x.5); a whole line counts over as > line."""
        k = int(line // 1) if line != int(line) else int(line)
        return 1.0 - nb_cdf(k, self.mean, self.dispersion)

    def p_at_least(self, n: int) -> float:
        """P(count >= n): SportyBet's "10+" style lines."""
        return 1.0 - nb_cdf(n - 1, self.mean, self.dispersion) if n > 0 else 1.0


def nb_pmf(k: int, mean: float, r: float) -> float:
    """Negative binomial with mean `mean` and dispersion r (variance mean + mean²/r)."""
    if mean <= 0:
        return 1.0 if k == 0 else 0.0
    if r > 1e6:   # Poisson
        return exp(k * log(mean) - mean - lgamma(k + 1))
    p = r / (r + mean)
    return exp(lgamma(k + r) - lgamma(r) - lgamma(k + 1) + r * log(p) + k * log(1 - p))


def nb_cdf(k: int, mean: float, r: float) -> float:
    if k < 0:
        return 0.0
    return min(1.0, sum(nb_pmf(i, mean, r) for i in range(k + 1)))


def _weights(n: int) -> List[float]:
    """Newest game first: 1, 0.93, 0.87, …"""
    return [0.5 ** (i / HALF_LIFE) for i in range(n)]


def expected_minutes(games: Sequence[Game]) -> Tuple[float, float]:
    """(mean, spread) of his minutes when he plays, newest first."""
    played = [g for g in games if g.minutes > 0]
    if not played:
        return 0.0, 0.0
    w = _weights(len(played))
    m = sum(wi * g.minutes for wi, g in zip(w, played)) / sum(w)
    var = sum(wi * (g.minutes - m) ** 2 for wi, g in zip(w, played)) / sum(w)
    return m, var ** 0.5


def rate(games: Sequence[Game], stat: str, prior: float) -> Tuple[float, float]:
    """(per-minute rate, minutes behind it), pulled towards `prior`."""
    used = [g for g in games if g.minutes >= MIN_MINUTES and stat in g.stats]
    w = _weights(len(used))
    mins = sum(wi * g.minutes for wi, g in zip(w, used))
    count = sum(wi * g.stats[stat] for wi, g in zip(w, used))
    return (count + prior * PRIOR_MINUTES) / (mins + PRIOR_MINUTES), mins


def project(games: Sequence[Game], stat: str, prior: float, dispersion: float,
            game_factor: float = 1.0, minutes: Optional[float] = None) -> Optional[Projection]:
    """His expected count tonight. `games` newest first; `game_factor`: tonight's
    game against his team's usual (e.g. our expected team points / its average);
    `minutes` to use instead of his recent average (known to play less, say)."""
    played = [g for g in games if g.minutes > 0]
    if len(played) < MIN_GAMES:
        return None
    m, m_sd = expected_minutes(played)
    if minutes is not None:
        m = minutes
    r, behind = rate(played, stat, prior)
    mean = max(0.01, m * r * game_factor)
    # Minutes that swing widen the count's spread: var += (rate x minutes' sd)²
    extra = (r * game_factor * m_sd) ** 2
    base_var = mean + mean ** 2 / dispersion
    total_var = base_var + extra
    r_eff = mean ** 2 / max(1e-9, total_var - mean) if total_var > mean else 1e7
    return Projection(stat, mean, m, r, len(played), r_eff,
                      {"minutes_sd": round(m_sd, 1), "minutes_behind_rate": round(behind), "game_factor": round(game_factor, 3)})


def fit_dispersion(pairs: Iterable[Tuple[float, float]]) -> float:
    """Dispersion r from (expected, actual) pairs: method of moments on the
    excess of the squared misses over the mean (Poisson's variance)."""
    pairs = [(m, y) for m, y in pairs if m > 0]
    if len(pairs) < 50:
        return 8.0
    excess = sum((y - m) ** 2 - m for m, y in pairs)
    sq = sum(m * m for m, _ in pairs)
    if excess <= 0:
        return 1e7
    return max(0.5, sq / excess)


def calibration(rows: Iterable[Tuple[float, bool]],
                buckets=((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0))) -> List[Dict]:
    rows = list(rows)
    out = []
    for lo, hi in buckets:
        sel = [(p, w) for p, w in rows if lo <= p < hi]
        if sel:
            out.append({"bucket": f"{int(lo * 100)}-{int(hi * 100)}%", "n": len(sel),
                        "said": round(sum(p for p, _ in sel) / len(sel), 3),
                        "came_in": round(sum(1 for _, w in sel if w) / len(sel), 3)})
    return out


def name_key(name: str) -> str:
    """A player's name in a form both SportyBet ("Shengelia, Tornike") and box
    scores ("Tornike Shengelia") reduce to: the words, lower case, sorted."""
    import unicodedata
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    words = [w for w in "".join(c if c.isalnum() else " " for c in s).split() if w not in ("jr", "sr", "ii", "iii")]
    return " ".join(sorted(words))
