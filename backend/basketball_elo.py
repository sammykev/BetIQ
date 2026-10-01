"""
Basketball Elo with margin of victory (FiveThirtyEight's NBA Elo): a team's
rating moves after every game by K times how surprising the result was,
scaled up for bigger wins, and scaled down when a strong favourite wins
big (so blowouts by the better team don't inflate it). Between seasons
each rating is pulled a quarter of the way back to the average.

    rate(games)  -> {team: rating} from a league's games in date order
    diff(r, home, away, neutral) -> the Elo gap with home court added

The walk-forward check (elo_check_basketball.py) measures whether it adds
anything to the attack/defence ratings (basketball_model.py).
"""

from datetime import date
from typing import Dict, Iterable, Optional, Tuple

# Elo's weight in a league's expected margin (basketball_model.expect),
# from the walk-forward check (elo_check_basketball.py): the league's best
# weight there, pulled towards DEFAULT_WEIGHT by SHRINK_GAMES' worth (a league
# with few tested games keeps close to it). On 71,539 games the check found
# Elo much better in NCAA (thousands of teams, few meetings), level in the
# NBA, and mixed in the smaller leagues.
DEFAULT_WEIGHT = 0.25
SHRINK_GAMES = 800
SCALE0 = 1 / 28.0         # points per Elo point where a league has no measured scale

K = 20.0
HOME = 100.0              # home court, in Elo points (FiveThirtyEight: 100 for the NBA)
START = 1500.0
CARRY = 0.75              # share of a rating kept over an off-season
OFFSEASON_DAYS = 60       # a team's gap between games that counts as one


def expected(d: float) -> float:
    """The home side's chance from an Elo gap (home court included)."""
    return 1.0 / (1.0 + 10 ** (-d / 400.0))


def _mov(margin: int, winner_gap: float) -> float:
    """Margin-of-victory multiplier: bigger wins count more, less so for the favourite."""
    return ((abs(margin) + 3) ** 0.8) / (7.5 + 0.006 * winner_gap)


def diff(r: Dict[str, float], home: str, away: str, neutral: bool = False) -> float:
    return r.get(home, START) + (0.0 if neutral else HOME) - r.get(away, START)


def rate(games: Iterable, until: Optional[str] = None, out: Optional[Dict[str, float]] = None,
         last: Optional[Dict[str, str]] = None) -> Dict[str, float]:
    """Ratings after `games` (each with date, home, away, hs, as_, neutral),
    taken in date order, up to (not including) `until` (YYYY-MM-DD).
    `out` and `last` carry state between calls (games after the last call's)."""
    r = out if out is not None else {}
    seen = last if last is not None else {}
    for g in sorted(games, key=lambda g: g.date):
        if until is not None and g.date >= until:
            break
        for t in (g.home, g.away):
            if t in seen and (date.fromisoformat(g.date) - date.fromisoformat(seen[t])).days > OFFSEASON_DAYS:
                r[t] = START + CARRY * (r.get(t, START) - START)
            seen[t] = g.date
        d = diff(r, g.home, g.away, g.neutral)
        margin = g.hs - g.as_
        if margin == 0:
            continue
        won = 1.0 if margin > 0 else 0.0
        winner_gap = d if margin > 0 else -d
        shift = K * _mov(margin, winner_gap) * (won - expected(d))
        r[g.home] = r.get(g.home, START) + shift
        r[g.away] = r.get(g.away, START) - shift
    return r


def league_weights(report: Optional[Dict]) -> Dict[str, Tuple[float, float]]:
    """{league: (Elo's weight, points per Elo point)} from the check's report."""
    out = {}
    for name, lg in ((report or {}).get("leagues") or {}).items():
        scores = lg.get("scores") or {}
        if not scores:
            continue
        best = min(scores, key=lambda k: scores[k].get("log_loss", 9))
        n = float(lg.get("tested") or 0)
        w_best = float(best[1:])
        w = DEFAULT_WEIGHT + (w_best - DEFAULT_WEIGHT) * n / (n + SHRINK_GAMES)
        out[name] = (round(w, 3), float(lg.get("scale") or SCALE0))
    return out
