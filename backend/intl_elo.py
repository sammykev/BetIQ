"""
Elo ratings for national teams, as they stood on any date, from the
international results history (martj42's results.csv, synced to
data/international_results.csv).

Used by the shots model (set_pieces.STRENGTH): a national team with only a
handful of matches with shot stats starts from what its strength suggests
rather than from the average, so the stronger side is expected to create
more. Ratings "before" a date use only earlier results, so a walk-forward
check never sees the future.

The formula is World Football Elo's: K by the match's weight, times a
goal-difference multiplier; 100 points of home advantage unless neutral.
"""

import bisect
import csv
import io
import math
from typing import Dict, Iterable, List, Optional

import international_fixtures as intl

BASE = 1500.0
HOME_ADVANTAGE = 100.0
MIN_MATCHES = 10          # rated matches before a team's rating is trusted
SCALE = 400.0             # strength = (rating - BASE) / SCALE


def k_factor(tournament: str) -> float:
    t = (tournament or "").lower()
    if "friendly" in t:
        return 20.0
    if "qualification" in t or "qualifier" in t:
        return 40.0
    if t == "fifa world cup":
        return 60.0
    if any(x in t for x in ("euro", "copa américa", "copa america", "african cup of nations", "africa cup",
                            "afc asian cup", "gold cup", "nations league", "confederations cup")):
        return 50.0
    return 30.0


def goal_multiplier(margin: int) -> float:
    margin = abs(margin)
    if margin <= 1:
        return 1.0
    if margin == 2:
        return 1.5
    return (11 + margin) / 8


class EloTimeline:
    """Each team's rating after each of its matches, looked up by date."""

    def __init__(self) -> None:
        self.dates: Dict[str, List[str]] = {}
        self.values: Dict[str, List[float]] = {}

    @classmethod
    def from_rows(cls, rows: Iterable[Dict[str, str]]) -> "EloTimeline":
        """rows: results.csv records (date, home_team, away_team, home_score,
        away_score, tournament, neutral), in any order."""
        tl = cls()
        rating: Dict[str, float] = {}
        parsed = []
        for r in rows:
            try:
                hs, as_ = int(r["home_score"]), int(r["away_score"])
            except (KeyError, TypeError, ValueError):
                continue   # unplayed fixtures are listed with NA scores
            day = (r.get("date") or "")[:10]
            if len(day) != 10:
                continue
            parsed.append((day, intl.team_key(r["home_team"]), intl.team_key(r["away_team"]), hs, as_,
                           r.get("tournament") or "", str(r.get("neutral", "")).upper() == "TRUE"))
        parsed.sort(key=lambda x: x[0])
        for day, home, away, hs, as_, tournament, neutral in parsed:
            rh, ra = rating.get(home, BASE), rating.get(away, BASE)
            diff = rh - ra + (0.0 if neutral else HOME_ADVANTAGE)
            expected = 1 / (1 + 10 ** (-diff / 400))
            actual = 1.0 if hs > as_ else 0.5 if hs == as_ else 0.0
            change = k_factor(tournament) * goal_multiplier(hs - as_) * (actual - expected)
            rating[home], rating[away] = rh + change, ra - change
            for team in (home, away):
                tl.dates.setdefault(team, []).append(day)
                tl.values.setdefault(team, []).append(rating[team])
        return tl

    @classmethod
    def from_csv_text(cls, text: str) -> "EloTimeline":
        return cls.from_rows(csv.DictReader(io.StringIO(text)))

    @classmethod
    def from_file(cls, path: str) -> Optional["EloTimeline"]:
        try:
            with open(path, encoding="utf-8") as f:
                return cls.from_csv_text(f.read())
        except OSError:
            return None

    def rating(self, team: str, before: Optional[str] = None) -> Optional[float]:
        """The team's rating before `before` (YYYY-MM-DD; None = now), or
        None for a team with too few rated matches by then."""
        key = intl.team_key(team)
        dates = self.dates.get(key)
        if not dates:
            return None
        i = len(dates) if before is None else bisect.bisect_left(dates, before[:10])
        if i < MIN_MATCHES:
            return None
        return self.values[key][i - 1]

    def strength(self, team: str, when=None) -> Optional[float]:
        """(rating - 1500) / 400 before `when` (a date, Timestamp or string;
        None = now): about ±1 for the strongest and weakest sides."""
        before = None if when is None else str(when)[:10]
        r = self.rating(team, before)
        return None if r is None else (r - BASE) / SCALE

    def teams(self) -> int:
        return len(self.dates)


def strength_prior(z: Optional[float], weight: float):
    """(shots-for, shots-against) multipliers a team's ratings start from:
    exp(±weight·z), i.e. 1 (the average) without a rating or weight."""
    if z is None or not weight:
        return 1.0, 1.0
    return math.exp(weight * z), math.exp(-weight * z)
