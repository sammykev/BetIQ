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
# Referees (bookings only): each one's bookings relative to what the teams
# alone predicted, shrunk towards 1 by REF_PRIOR matches' worth. Walk-forward
# on the Premier League + Championship (the leagues whose data names the
# referee): bookings Brier 0.9616 → 0.9549 on 2023-24 and 0.9469 → 0.9466 on
# 2024-25 with these cautious settings; bolder ones helped 2023-24 more but
# hurt 2024-25. A referee new to our data starts from their career record
# (career_factor) when a source gives one.
USE_REFEREES = True
REF_PRIOR = 80.0
REF_DECAY = 0.98
REF_GAMMA = 1.0
CAREER_PRIOR = 20.0   # games' worth of weight on the league-wide average
AVERAGE_BOOKINGS = 4.4


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


def career_factor(career: Optional[Dict]) -> float:
    """A referee's career bookings a game vs the average, shrunk by games:
    {"games", "yellow", "red"} (SofaScore's referee record); 1 without one."""
    try:
        games = float(career["games"])
        per_game = (float(career.get("yellow") or 0) + 2 * float(career.get("red") or 0)) / games
    except (TypeError, KeyError, ValueError, ZeroDivisionError):
        return 1.0
    if games <= 0:
        return 1.0
    ratio = min(2.0, max(0.5, per_game / AVERAGE_BOOKINGS))
    return (games * ratio + CAREER_PRIOR) / (games + CAREER_PRIOR)


def referee_key(name: Optional[str]) -> Optional[str]:
    """One key per referee across sources: surname + first initial
    ("Anthony Taylor", "A Taylor" → "taylor a")."""
    import unicodedata
    if not isinstance(name, str) or not name.strip():
        return None
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    parts = [p for p in plain.replace(".", " ").replace(",", " ").split() if p]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return f"{parts[-1]} {parts[0][0]}"


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


# Spreads used when a match has no fitted model (the fitted values sit near these)
DEFAULT_SIZE = {"corners": 75.0, "bookings": 16.0}
_PRICE_MARKETS = {"corners": "166", "bookings": "139"}


def from_prices(event: Optional[Dict], sizes: Optional[Dict[str, Optional[float]]] = None) -> Optional[Dict]:
    """Corners / bookings lines for a match we have no stats for (internationals),
    implied by SportyBet's own over/under price: the margin taken out, a
    negative binomial fitted to that one line, the other lines read off it.
    Market-implied, not a model edge — marked "source": "sportybet"."""
    from booking_slip import label_ok
    if not event:
        return None
    sizes = sizes or {}
    out: Dict[str, Dict] = {}
    for stat, market_id in _PRICE_MARKETS.items():
        best = None
        for m in event.get("markets") or []:
            spec = m.get("specifier") or ""
            if str(m.get("id")) != market_id or not spec.startswith("total=") or not label_ok(market_id, m.get("desc")):
                continue
            prices = {}
            for o in m.get("outcomes") or []:
                try:
                    if o.get("isActive", 1):
                        prices[str(o.get("id"))] = float(o.get("odds"))
                except (TypeError, ValueError):
                    pass
            if not {"12", "13"} <= set(prices) or min(prices["12"], prices["13"]) <= 1:
                continue
            p = (1 / prices["12"]) / (1 / prices["12"] + 1 / prices["13"])
            try:
                line = float(spec.split("=", 1)[1])
            except ValueError:
                continue
            if best is None or abs(p - 0.5) < abs(best[1] - 0.5):  # the line nearest a coin flip says most
                best = (line, p)
        if not best:
            continue
        line, p = best
        r = sizes.get(stat) or DEFAULT_SIZE[stat]
        lo, hi = 0.3, 40.0
        for _ in range(50):  # P(over line) rises with the mean
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if p_over(line, mid, r) < p else (lo, mid)
        mu = (lo + hi) / 2
        out[stat] = {"mean": round(mu, 2), "source": "sportybet",
                     "over": {f"{l}": round(p_over(l, mu, r), 3) for l in LINES[stat]}}
    return out or None


class SetPieceModel:
    """params (all optional, else the module defaults above, read at use):
    decay, prior, gamma, team_gamma, league_decay, min_matches, and
    seed_leagues — a competition seen for the first time starts from the
    average over all competitions rather than its first match (for
    internationals, where many competitions have few matches)."""

    def __init__(self, params: Optional[Dict] = None):
        self.params = dict(params or {})
        self.overall: Dict[str, List[float]] = {}  # stat -> [home avg, away avg] over everything
        self.ref: Dict[str, List[float]] = {}      # referee key -> [weight, bookings ratio sum]
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

    def _p(self, name: str):
        return self.params.get(name, globals()[name.upper()])

    def _rating(self, team: str, stat: str) -> Tuple[float, float]:
        w, f, a = self.team.get(team, {}).get(stat, (0.0, 0.0, 0.0))
        prior = self._p("prior")
        return (f + prior) / (w + prior), (a + prior) / (w + prior)

    def referee_factor(self, referee: Optional[str], career: Optional[Dict] = None) -> float:
        """How many more (>1) or fewer bookings than usual this referee gives:
        their matches in our data, starting from their career record."""
        key = referee_key(referee)
        if not key or not self._p("use_referees"):
            return 1.0
        start = career_factor(career)
        w, total = self.ref.get(key, (0.0, 0.0))
        prior = self._p("ref_prior")
        return ((total + prior * start) / (w + prior)) ** self._p("ref_gamma")

    def expected(self, home: str, away: str, league: Optional[str] = None,
                 referee: Optional[str] = None, career: Optional[Dict] = None) -> Optional[Dict[str, Tuple[float, float]]]:
        """{stat: (home mean, away mean)}, or None for teams we know too little about.
        A known referee scales the bookings (when use_referees is on)."""
        need = self._p("min_matches")
        if self.matches.get(home, 0) < need or self.matches.get(away, 0) < need:
            return None
        gamma, team_gamma = self._p("gamma"), self._p("team_gamma")
        league = league if league in self.league else self.league_of.get(home)
        out = {}
        for stat in STATS:
            avgs = self._league_avgs(league, stat)
            if not avgs:
                return None
            fh, ah = self._rating(home, stat)
            fa, aa = self._rating(away, stat)
            out[stat] = (avgs[0] * (fh * aa) ** gamma, avgs[1] * (fa * ah) ** gamma)
            if stat == "corners":
                out["corners_team"] = (avgs[0] * (fh * aa) ** team_gamma, avgs[1] * (fa * ah) ** team_gamma)
        factor = self.referee_factor(referee, career)
        if factor != 1.0:
            out["bookings"] = (out["bookings"][0] * factor, out["bookings"][1] * factor)
        return out

    def update(self, home: str, away: str, league: str, counts: Dict[str, Tuple[float, float]],
               referee: Optional[str] = None) -> None:
        key = referee_key(referee)
        if key and "bookings" in counts:
            # The referee's share: bookings given vs what the teams alone predicted
            teams_only = self.expected(home, away, league)
            base = sum(teams_only["bookings"]) if teams_only else sum(self._league_avgs(league, "bookings") or (0, 0))
            if base > 0:
                decay_r = self._p("ref_decay")
                r = self.ref.setdefault(key, [0.0, 0.0])
                r[0] = r[0] * decay_r + 1
                r[1] = r[1] * decay_r + sum(counts["bookings"]) / base
        decay, league_decay = self._p("decay"), self._p("league_decay")
        for stat, (h, a) in counts.items():
            lg = self.league.setdefault(league, {})
            if stat not in lg:
                seed = self.overall.get(stat) if self.params.get("seed_leagues") else None
                lg[stat] = list(seed) if seed else [max(h, 0.5), max(a, 0.5)]
            lh, la = lg[stat]
            for team, gained, conceded, own, opp in ((home, h, a, lh, la), (away, a, h, la, lh)):
                t = self.team.setdefault(team, {}).setdefault(stat, [0.0, 0.0, 0.0])
                t[0] = t[0] * decay + 1
                t[1] = t[1] * decay + gained / own
                t[2] = t[2] * decay + conceded / opp
            lg[stat] = [lh * league_decay + h * (1 - league_decay), la * league_decay + a * (1 - league_decay)]
            ov = self.overall.setdefault(stat, [max(h, 0.5), max(a, 0.5)])
            self.overall[stat] = [ov[0] * 0.998 + h * 0.002, ov[1] * 0.998 + a * 0.002]
        for team in (home, away):
            self.matches[team] = self.matches.get(team, 0) + 1
        self.league_of[home] = self.league_of[away] = league

    # ---------------------------------------------------------------- #
    @classmethod
    def replay(cls, matches: pd.DataFrame, test_from: Optional[pd.Timestamp] = None,
               params: Optional[Dict] = None):
        """Fit on matches in date order. Returns (model, [(date, stat, mean, actual,
        baseline mean)]) — predictions made before each match, from test_from on."""
        model = cls(params)
        rows = []
        df = matches.sort_values("Date")
        for rec in df.itertuples(index=False):
            row = rec._asdict()
            counts = _counts(row)
            if counts is None:
                continue
            league = row.get("league") or ""
            referee = row.get("Referee") if isinstance(row.get("Referee"), str) else None
            if test_from is None or row["Date"] >= test_from:
                exp = model.expected(row["HomeTeam"], row["AwayTeam"], league, referee)
                if exp:
                    for stat in STATS:
                        base = sum(model._league_avgs(league, stat))
                        rows.append((row["Date"], stat, sum(exp[stat]), sum(counts[stat]), base))
                    avgs = model._league_avgs(league, "corners")
                    for side, stat in enumerate(TEAM_STATS):
                        rows.append((row["Date"], stat, exp["corners_team"][side], counts["corners"][side], avgs[side]))
            model.update(row["HomeTeam"], row["AwayTeam"], league, counts, referee)
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
    def fit(cls, matches: pd.DataFrame, params: Optional[Dict] = None) -> Optional["SetPieceModel"]:
        """The model after every match with stats, with spreads fitted on its own
        walk-forward errors (the first season is warm-up)."""
        cols = {"Date", "HomeTeam", "AwayTeam", "HC", "AC", "HY", "AY", "HR", "AR"}
        if matches is None or matches.empty or not cols <= set(matches.columns):
            return None
        data = matches.dropna(subset=list(cols))
        if data.empty:
            return None
        warm_up = data["Date"].min() + pd.Timedelta(days=365)
        model, rows = cls.replay(data, test_from=warm_up, params=params)
        for stat in STATS + TEAM_STATS:
            model.size[stat] = cls.fit_size((m, y) for _, s, m, y, _ in rows if s == stat)
        return model

    # ---------------------------------------------------------------- #
    def markets(self, home: str, away: str, league: Optional[str] = None,
                referee: Optional[str] = None, career: Optional[Dict] = None) -> Optional[Dict]:
        """{"corners" / "bookings" / "corners_home" / "corners_away": {"mean",
        "over": {line: p}}, "corners_1x2": {"home", "draw", "away"}} for a fixture."""
        exp = self.expected(home, away, league, referee, career)
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


def score(matches: pd.DataFrame, params: Optional[Dict], start: pd.Timestamp,
          end: Optional[pd.Timestamp] = None) -> Dict[str, Dict[str, float]]:
    """Walk-forward Brier score, summed over each stat's lines, for matches in
    [start, end): {stat: {"model", "baseline", "matches"}} — spreads fitted on
    the errors before `start`, baseline = the competition average."""
    data = matches.dropna(subset=["HC", "AC", "HY", "AY", "HR", "AR"])
    warm_up = data["Date"].min() + pd.Timedelta(days=365)
    _, rows = SetPieceModel.replay(data, test_from=warm_up, params=params)
    out = {}
    for stat in STATS + TEAM_STATS:
        before = [(m, y, b) for d, s, m, y, b in rows if s == stat and d < start]
        test = [(m, y, b) for d, s, m, y, b in rows if s == stat and d >= start and (end is None or d < end)]
        if not test:
            continue
        size = SetPieceModel.fit_size((m, y) for m, y, _ in before) or DEFAULT_SIZE.get(stat.split("_")[0])
        base_size = SetPieceModel.fit_size((b, y) for _, y, b in before) or size
        model_b = base_b = 0.0
        for m, y, b in test:
            for line in LINES[stat]:
                hit = 1.0 if y > line else 0.0
                model_b += (p_over(line, m, size) - hit) ** 2
                base_b += (p_over(line, b, base_size) - hit) ** 2
        out[stat] = {"model": round(model_b / len(test), 4), "baseline": round(base_b / len(test), 4),
                     "matches": len(test)}
    return out


# National teams: few matches each, many small competitions
INTERNATIONAL_GRID = [
    {"prior": prior, "gamma": gamma, "decay": decay, "min_matches": 4, "seed_leagues": True, "league_decay": 0.98}
    for prior in (6.0, 12.0, 20.0) for gamma in (0.4, 0.6, 0.8) for decay in (0.85, 0.92)
]
MIN_HOLDOUT = 200


def tune_international(matches: pd.DataFrame, today: Optional[pd.Timestamp] = None) -> Dict:
    """Pick settings on everything but the last 12 months, then test on those
    months against the competition average. The model is used ("use": True,
    per stat) only where it beats that baseline on matches it never saw."""
    today = today or pd.Timestamp.now().normalize()
    holdout = today - pd.Timedelta(days=365)
    report: Dict = {"matches": int(len(matches)), "use": {}, "params": None, "holdout": {}}
    if matches.empty or len(matches) < 600:
        report["reason"] = f"not enough matches yet ({len(matches)}; need 600)"
        return report
    first = matches["Date"].min()
    tune_start = first + pd.Timedelta(days=730)
    if tune_start >= holdout:
        report["reason"] = "not enough history yet (need three years)"
        return report
    best, best_score = None, None
    for params in INTERNATIONAL_GRID:
        sc = score(matches[matches["Date"] < holdout], params, tune_start)
        total = sum(v["model"] for k, v in sc.items() if k in STATS)
        if best_score is None or total < best_score:
            best, best_score = params, total
    # Referees on or off (where the data names them), whichever scored better
    if "Referee" in matches.columns and matches["Referee"].notna().any():
        with_refs = {**best, "use_referees": True}
        without = {**best, "use_referees": False}
        pre = matches[matches["Date"] < holdout]
        b_with = score(pre, with_refs, tune_start).get("bookings", {}).get("model")
        b_without = score(pre, without, tune_start).get("bookings", {}).get("model")
        best = with_refs if b_with is not None and b_without is not None and b_with < b_without else without
    else:
        best = {**best, "use_referees": False}
    report["params"] = best
    report["holdout"] = score(matches, best, holdout)
    for stat, v in report["holdout"].items():
        report["use"][stat] = v["matches"] >= MIN_HOLDOUT and v["model"] < v["baseline"]
    if not any(report["use"].values()):
        report["reason"] = "didn't beat the competition average on the last 12 months"
    return report


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
