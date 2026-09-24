"""
Corners and bookings totals, from the league CSVs' match stats
(football-data.co.uk: HC/AC corners, HY/AY yellows, HR/AR reds).

Each team gets two ratings per stat, relative to its league's average at
that venue: how many it wins/earns ("for") and how many its opponents do
("against"), exponentially weighted so recent matches count most and shrunk
towards the league average while a team has few matches. Expected counts:

    home = league home avg × (for_home × against_away) ** GAMMA
    away = league away avg × (for_away × against_home) ** GAMMA

and the match total is negative binomial (its extra spread over Poisson
fitted from the walk-forward errors), which gives over/under line
probabilities.

Bookings follow Betradar's "Total bookings" rule: yellow = 1, red = 2.

`evaluate` replays the history in date order — every match predicted from
the matches before it only — and scores the lines against a league-average
baseline; `python set_pieces.py` prints that report.
"""

import math
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

STATS = ("corners", "bookings")
# Each team's own corners (home side / away side), with their own spread
TEAM_STATS = ("corners_home", "corners_away")
LINES = {"corners": (7.5, 8.5, 9.5, 10.5, 11.5), "bookings": (2.5, 3.5, 4.5, 5.5, 6.5),
         "corners_home": (2.5, 3.5, 4.5, 5.5, 6.5), "corners_away": (1.5, 2.5, 3.5, 4.5, 5.5)}
DECAY = 0.95        # per match of that team: ~20 matches of memory
PRIOR = 8.0         # matches' worth of league-average shrinkage
GAMMA = 0.6         # damping of the combined team ratings (tuned walk-forward on 2022-24)
TEAM_GAMMA = 1.0    # the same for one team's own corners (tuned walk-forward on 2023-24)
LEAGUE_DECAY = 0.995
MIN_MATCHES = 6     # per team before we price its matches


def _counts(row) -> Optional[Dict[str, Tuple[float, float]]]:
    """{stat: (home, away)} for a CSV row, or None when a stat is missing."""
    try:
        hc, ac = float(row["HC"]), float(row["AC"])
        hy, ay = float(row["HY"]), float(row["AY"])
        hr, ar = float(row["HR"]), float(row["AR"])
    except (KeyError, TypeError, ValueError):
        return None
    if any(math.isnan(v) for v in (hc, ac, hy, ay, hr, ar)):
        return None
    return {"corners": (hc, ac), "bookings": (hy + 2 * hr, ay + 2 * ar)}


def _nb_cdf(k: int, mu: float, r: Optional[float]) -> float:
    """P(X ≤ k) for a negative binomial with mean mu and size r (Poisson if r is None)."""
    total = 0.0
    if r is None:
        for i in range(k + 1):
            total += math.exp(i * math.log(mu) - mu - math.lgamma(i + 1))
        return min(1.0, total)
    p = r / (r + mu)
    for i in range(k + 1):
        total += math.exp(math.lgamma(i + r) - math.lgamma(r) - math.lgamma(i + 1)
                          + r * math.log(p) + i * math.log(1 - p))
    return min(1.0, total)


def p_over(line: float, mu: float, r: Optional[float]) -> float:
    return 1.0 - _nb_cdf(int(math.floor(line)), mu, r)


def _nb_pmf(k: int, mu: float, r: Optional[float]) -> float:
    if r is None:
        return math.exp(k * math.log(mu) - mu - math.lgamma(k + 1))
    p = r / (r + mu)
    return math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(p) + k * math.log(1 - p))


def most_corners(mu_h: float, mu_a: float, r_h: Optional[float], r_a: Optional[float]) -> Dict[str, float]:
    """P(home wins more corners / level / away more), the teams independent."""
    ph = [_nb_pmf(k, mu_h, r_h) for k in range(40)]
    pa = [_nb_pmf(k, mu_a, r_a) for k in range(40)]
    home = sum(ph[i] * pa[j] for i in range(40) for j in range(i))
    draw = sum(ph[i] * pa[i] for i in range(40))
    total = home + draw + sum(ph[i] * pa[j] for i in range(40) for j in range(i + 1, 40))
    return {"home": home / total, "draw": draw / total, "away": 1 - (home + draw) / total}


class SetPieceModel:
    def __init__(self):
        # league -> stat -> [home avg, away avg]
        self.league: Dict[str, Dict[str, List[float]]] = {}
        # team -> stat -> [weight, for ratio sum, against ratio sum]
        self.team: Dict[str, Dict[str, List[float]]] = {}
        self.matches: Dict[str, int] = {}
        self.size: Dict[str, Optional[float]] = {s: None for s in STATS + TEAM_STATS}  # NB size r
        self.league_of: Dict[str, str] = {}

    # ---------------------------------------------------------------- #
    def _league_avgs(self, league: str, stat: str) -> Optional[List[float]]:
        return self.league.get(league, {}).get(stat)

    def _rating(self, team: str, stat: str) -> Tuple[float, float]:
        w, f, a = self.team.get(team, {}).get(stat, (0.0, 0.0, 0.0))
        return (f + PRIOR) / (w + PRIOR), (a + PRIOR) / (w + PRIOR)

    def expected(self, home: str, away: str, league: Optional[str] = None) -> Optional[Dict[str, Tuple[float, float]]]:
        """{stat: (home mean, away mean)}, or None for teams we know too little about."""
        if self.matches.get(home, 0) < MIN_MATCHES or self.matches.get(away, 0) < MIN_MATCHES:
            return None
        league = league if league in self.league else self.league_of.get(home)
        out = {}
        for stat in STATS:
            avgs = self._league_avgs(league, stat)
            if not avgs:
                return None
            fh, ah = self._rating(home, stat)
            fa, aa = self._rating(away, stat)
            out[stat] = (avgs[0] * (fh * aa) ** GAMMA, avgs[1] * (fa * ah) ** GAMMA)
            if stat == "corners":
                out["corners_team"] = (avgs[0] * (fh * aa) ** TEAM_GAMMA, avgs[1] * (fa * ah) ** TEAM_GAMMA)
        return out

    def update(self, home: str, away: str, league: str, counts: Dict[str, Tuple[float, float]]) -> None:
        for stat, (h, a) in counts.items():
            lg = self.league.setdefault(league, {})
            if stat not in lg:
                lg[stat] = [max(h, 0.5), max(a, 0.5)]
            lh, la = lg[stat]
            for team, gained, conceded, own, opp in ((home, h, a, lh, la), (away, a, h, la, lh)):
                t = self.team.setdefault(team, {}).setdefault(stat, [0.0, 0.0, 0.0])
                t[0] = t[0] * DECAY + 1
                t[1] = t[1] * DECAY + gained / own
                t[2] = t[2] * DECAY + conceded / opp
            lg[stat] = [lh * LEAGUE_DECAY + h * (1 - LEAGUE_DECAY), la * LEAGUE_DECAY + a * (1 - LEAGUE_DECAY)]
        for team in (home, away):
            self.matches[team] = self.matches.get(team, 0) + 1
        self.league_of[home] = self.league_of[away] = league

    # ---------------------------------------------------------------- #
    @classmethod
    def replay(cls, matches: pd.DataFrame, test_from: Optional[pd.Timestamp] = None):
        """Fit on matches in date order. Returns (model, [(date, stat, mean, actual,
        baseline mean)]) — predictions made before each match, from test_from on."""
        model = cls()
        rows = []
        df = matches.sort_values("Date")
        for rec in df.itertuples(index=False):
            row = rec._asdict()
            counts = _counts(row)
            if counts is None:
                continue
            league = row.get("league") or ""
            if test_from is None or row["Date"] >= test_from:
                exp = model.expected(row["HomeTeam"], row["AwayTeam"], league)
                if exp:
                    for stat in STATS:
                        base = sum(model._league_avgs(league, stat))
                        rows.append((row["Date"], stat, sum(exp[stat]), sum(counts[stat]), base))
                    avgs = model._league_avgs(league, "corners")
                    for side, stat in enumerate(TEAM_STATS):
                        rows.append((row["Date"], stat, exp["corners_team"][side], counts["corners"][side], avgs[side]))
            model.update(row["HomeTeam"], row["AwayTeam"], league, counts)
        return model, rows

    @staticmethod
    def fit_size(errors: Iterable[Tuple[float, float]]) -> Optional[float]:
        """NB size r from (mean, actual) pairs: var = mu + mu²/r (None → Poisson)."""
        num = den = 0.0
        for mu, y in errors:
            num += (y - mu) ** 2 - mu
            den += mu * mu
        if den <= 0 or num <= 0:
            return None
        return den / num

    @classmethod
    def fit(cls, matches: pd.DataFrame) -> Optional["SetPieceModel"]:
        """The model after every match with stats, with spreads fitted on its own
        walk-forward errors (the first season is warm-up)."""
        cols = {"Date", "HomeTeam", "AwayTeam", "HC", "AC", "HY", "AY", "HR", "AR"}
        if matches is None or matches.empty or not cols <= set(matches.columns):
            return None
        data = matches.dropna(subset=list(cols))
        if data.empty:
            return None
        warm_up = data["Date"].min() + pd.Timedelta(days=365)
        model, rows = cls.replay(data, test_from=warm_up)
        for stat in STATS + TEAM_STATS:
            model.size[stat] = cls.fit_size((m, y) for _, s, m, y, _ in rows if s == stat)
        return model

    # ---------------------------------------------------------------- #
    def markets(self, home: str, away: str, league: Optional[str] = None) -> Optional[Dict]:
        """{"corners" / "bookings" / "corners_home" / "corners_away": {"mean",
        "over": {line: p}}, "corners_1x2": {"home", "draw", "away"}} for a fixture."""
        exp = self.expected(home, away, league)
        if not exp:
            return None
        out = {}
        means = {**{stat: sum(exp[stat]) for stat in STATS},
                 "corners_home": exp["corners_team"][0], "corners_away": exp["corners_team"][1]}
        for stat, mu in means.items():
            out[stat] = {"mean": round(mu, 2),
                         "over": {f"{line}": round(p_over(line, mu, self.size.get(stat)), 3)
                                  for line in LINES[stat]}}
        race = most_corners(means["corners_home"], means["corners_away"],
                            self.size.get("corners_home"), self.size.get("corners_away"))
        out["corners_1x2"] = {k: round(v, 3) for k, v in race.items()}
        return out


def evaluate(matches: pd.DataFrame, test_from: str) -> Dict:
    """Walk-forward check: fit spreads on errors before test_from, then score
    every line on matches from test_from on (model vs league-average
    baseline) with Brier score and calibration bins."""
    cutoff = pd.Timestamp(test_from)
    data = matches.dropna(subset=["HC", "AC", "HY", "AY", "HR", "AR"])
    warm_up = data["Date"].min() + pd.Timedelta(days=365)
    _, rows = SetPieceModel.replay(data, test_from=warm_up)
    report: Dict = {}
    for stat in STATS + TEAM_STATS:
        train = [(m, y, b) for d, s, m, y, b in rows if s == stat and d < cutoff]
        test = [(m, y, b) for d, s, m, y, b in rows if s == stat and d >= cutoff]
        size = SetPieceModel.fit_size((m, y) for m, y, _ in train)
        base_size = SetPieceModel.fit_size((b, y) for _, y, b in train)
        lines = {}
        for line in LINES[stat]:
            model_b = base_b = 0.0
            bins: Dict[int, List[float]] = {}
            for m, y, b in test:
                hit = 1.0 if y > line else 0.0
                p = p_over(line, m, size)
                model_b += (p - hit) ** 2
                base_b += (p_over(line, b, base_size) - hit) ** 2
                slot = bins.setdefault(min(int(p * 10), 9), [0, 0.0, 0.0])
                slot[0] += 1
                slot[1] += p
                slot[2] += hit
            n = len(test) or 1
            lines[f"{line}"] = {
                "brier": round(model_b / n, 4), "baseline_brier": round(base_b / n, 4),
                "over_rate": round(sum(y > line for _, y, _ in test) / n, 3),
                "calibration": [{"said": round(s[1] / s[0], 3), "happened": round(s[2] / s[0], 3), "n": s[0]}
                                for _, s in sorted(bins.items()) if s[0] >= 20],
            }
        mae = sum(abs(m - y) for m, y, _ in test) / (len(test) or 1)
        base_mae = sum(abs(b - y) for _, y, b in test) / (len(test) or 1)
        report[stat] = {"matches": len(test), "size": size and round(size, 1),
                        "mae": round(mae, 3), "baseline_mae": round(base_mae, 3), "lines": lines}
    return report


if __name__ == "__main__":
    import json
    from main import _load_football_data_csvs
    print(json.dumps(evaluate(_load_football_data_csvs(), "2024-08-01"), indent=1))
