"""
Basketball: each team's attack and defence in points, per league, and the
chance of any line from them.

Ratings (fit): every game says two things,
    home points = league average + home court + home attack - away defence
    away points = league average + away attack - home defence
solved by ridge least squares, recent games counting more (HALF_LIFE_DAYS).
Ridge pulls teams with few games to the league average, so a new or
promoted team starts average and moves as it plays.

Spread (sigma): how far results land from those expectations, measured on
the league's own games — the margin's, the total's and one team's points,
and, where quarter scores exist, the first half's and a quarter's.

Probabilities (Match.prob): margin and total are taken as normal around the
expectations, on whole points — so a tie at the end of regulation (and
overtime) gets its own chance, and every half-point line (handicap, total,
team total, halves, quarters) has one.

Blending with SportyBet (blend): its winner, main handicap and main total
prices carry the market's expected margin and total. Where we know both
teams, ours and the market's are mixed (MODEL_WEIGHT, from the backtest);
where we don't, the market's are used as they are, still giving a price for
every other line.
"""

from dataclasses import dataclass, field
from datetime import date
from math import exp, log
from statistics import NormalDist
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

HALF_LIFE_DAYS = 90      # a game's weight halves every this many days
RIDGE = 8.0              # pull towards the league average, in games' worth
MIN_GAMES = 8            # a team needs this many (weighted) games to be "known"
SIGMA_FLOOR = {"margin": 9.0, "total": 11.0, "team": 7.5}
# Where a league has no games to measure from: typical of professional leagues
DEFAULT_SIGMA = {"margin": 12.0, "total": 17.0, "team": 10.5,
                 "h1_margin": 8.4, "h1_total": 11.0, "q_margin": 6.0, "q_total": 7.6}
# Regulation ends level more often than a smooth spread says (the side one
# or two behind fouls and shoots to tie; the side ahead plays safe): about
# twice as often in professional leagues. Measured per league (_tie_factor),
# pulled towards this by PRIOR_TIES ties' worth.
TIE_FACTOR = 2.0
PRIOR_TIES = 6.0
OT_HOME = 0.5            # who wins overtime: even (it's short and near random)
OT_SHARE = 0.11          # an overtime's points, as a share of a game's (5 minutes of 40–48)
_N = NormalDist()


@dataclass
class Game:
    date: str                   # YYYY-MM-DD
    home: str
    away: str
    hs: int                     # final score, overtime included
    as_: int
    periods: Optional[List[Tuple[int, int]]] = None   # (home, away) per quarter / half, regulation only
    neutral: bool = False
    ot: bool = False


@dataclass
class League:
    name: str
    avg: float                  # points per team per game
    home_court: float
    attack: Dict[str, float]
    defence: Dict[str, float]
    games: Dict[str, float]     # weighted games per team
    sigma: Dict[str, float]
    h1_share: float = 0.5       # share of the points scored in the first half
    n: int = 0
    as_of: str = ""
    q_shares: Tuple[float, float, float, float] = (0.25, 0.25, 0.25, 0.25)
    # How much of the expected margin shows in the first half and each quarter:
    # measured, not assumed — a favourite's lead grows less once starters rest
    margin_shares: Tuple[float, float, float, float, float] = (0.5, 0.25, 0.25, 0.25, 0.25)
    tie_factor: float = TIE_FACTOR   # regulation ties (overtime) against the spread's own chance of one

    def known(self, team: str) -> bool:
        return self.games.get(team, 0.0) >= MIN_GAMES

    def expect(self, home: str, away: str, neutral: bool = False) -> Tuple[float, float]:
        hc = 0.0 if neutral else self.home_court
        h = self.avg + hc + self.attack.get(home, 0.0) - self.defence.get(away, 0.0)
        a = self.avg + self.attack.get(away, 0.0) - self.defence.get(home, 0.0)
        return h, a

    def to_json(self) -> Dict:
        return {k: getattr(self, k) for k in ("name", "avg", "home_court", "attack", "defence", "games",
                                              "sigma", "h1_share", "n", "as_of", "q_shares", "margin_shares",
                                              "tie_factor")}

    @classmethod
    def from_json(cls, d: Dict) -> "League":
        return cls(**{k: d[k] for k in ("name", "avg", "home_court", "attack", "defence", "games", "sigma")},
                   h1_share=d.get("h1_share", 0.5), n=d.get("n", 0), as_of=d.get("as_of", ""),
                   q_shares=tuple(d.get("q_shares") or (0.25,) * 4),
                   margin_shares=tuple(d.get("margin_shares") or (0.5, 0.25, 0.25, 0.25, 0.25)),
                   tie_factor=float(d.get("tie_factor") or TIE_FACTOR))


def _weights(games: List[Game], as_of: date) -> np.ndarray:
    return np.array([0.5 ** (max(0, (as_of - date.fromisoformat(g.date)).days) / HALF_LIFE_DAYS) for g in games])


def fit(name: str, games: Iterable[Game], as_of: Optional[date] = None) -> Optional[League]:
    """Ratings from a league's games before `as_of` (all of them if None)."""
    games = sorted((g for g in games if as_of is None or g.date < as_of.isoformat()), key=lambda g: g.date)
    if len(games) < 20:
        return None
    as_of = as_of or date.fromisoformat(games[-1].date)
    teams = sorted({g.home for g in games} | {g.away for g in games})
    idx = {t: i for i, t in enumerate(teams)}
    T = len(teams)
    w = _weights(games, as_of)
    # Columns: [average, home court, attack x T, defence x T]; two rows a game,
    # each with at most 4 non-zeros: the normal equations are built directly
    # (no dense design matrix, so years of a big league stay cheap)
    P = 2 + 2 * T
    n = len(games)
    cols = np.zeros((2 * n, 4), dtype=np.int64)
    vals = np.zeros((2 * n, 4))
    y = np.zeros(2 * n)
    rw = np.repeat(w, 2)
    for k, g in enumerate(games):
        h, a = idx[g.home], idx[g.away]
        cols[2 * k] = (0, 2 + h, 2 + T + a, 1)
        vals[2 * k] = (1.0, 1.0, -1.0, 0.0 if g.neutral else 1.0)
        y[2 * k] = g.hs
        cols[2 * k + 1] = (0, 2 + a, 2 + T + h, 1)
        vals[2 * k + 1] = (1.0, 1.0, -1.0, 0.0)
        y[2 * k + 1] = g.as_
    XtX = np.zeros((P, P))
    np.add.at(XtX, (cols[:, :, None], cols[:, None, :]), rw[:, None, None] * vals[:, :, None] * vals[:, None, :])
    Xty = np.zeros(P)
    np.add.at(Xty, cols, (rw * y)[:, None] * vals)
    # Ridge on the teams only (not on the average or home court)
    pen = np.r_[0.0, 0.0, np.full(2 * T, RIDGE)]
    beta = np.linalg.solve(XtX + np.diag(pen), Xty)
    avg, hc = float(beta[0]), float(beta[1])
    att = {t: float(beta[2 + i]) for t, i in idx.items()}
    dfn = {t: float(beta[2 + T + i]) for t, i in idx.items()}
    played: Dict[str, float] = {}
    for k, g in enumerate(games):
        played[g.home] = played.get(g.home, 0.0) + w[k]
        played[g.away] = played.get(g.away, 0.0) + w[k]
    lg = League(name, avg, hc, att, dfn, played, dict(DEFAULT_SIGMA), n=len(games), as_of=as_of.isoformat())
    lg.sigma, lg.h1_share, lg.q_shares, lg.margin_shares = _spread(lg, games, w)
    lg.tie_factor = _tie_factor(lg, games)
    return lg


def base_tie(margin: float, sd: float) -> float:
    """A regulation tie's chance from the spread alone (the normal's mass at 0)."""
    return _N.cdf((0.5 - margin) / sd) - _N.cdf((-0.5 - margin) / sd)


def _tie_factor(lg: League, games: List[Game]) -> float:
    """How many more regulation ties the league's games had than the spread
    alone expects (games with quarter scores: those tell overtime for sure)."""
    seen, expected = 0, 0.0
    for g in games:
        if not g.periods or len(g.periods) != 4:
            continue
        eh, ea = lg.expect(g.home, g.away, g.neutral)
        expected += base_tie(eh - ea, lg.sigma["margin"])
        seen += sum(p[0] for p in g.periods) == sum(p[1] for p in g.periods)
    return min(3.0, max(1.0, (seen + PRIOR_TIES * TIE_FACTOR) / (expected + PRIOR_TIES)))


def _wstd(x: List[float], w: List[float]) -> float:
    x_, w_ = np.array(x), np.array(w)
    return float(np.sqrt(np.sum(w_ * x_ ** 2) / np.sum(w_)))


def _margin_shares(rows: List[Tuple[float, List[float], float]]) -> Tuple[float, float, float, float, float]:
    """Per part (first half, quarters 1–4), how much of the expected margin
    showed: the slope of the part's margin on the game's expected margin
    (through the origin, recency-weighted)."""
    if len(rows) < 20:
        return (0.5, 0.25, 0.25, 0.25, 0.25)
    em = np.array([r[0] for r in rows])
    parts = np.array([r[1] for r in rows])            # h1, q1, q2, q3, q4 margins
    wt = np.array([r[2] for r in rows])
    den = float(np.sum(wt * em * em))
    if den <= 1e-9:
        return (0.5, 0.25, 0.25, 0.25, 0.25)
    beta = [float(np.sum(wt * em * parts[:, j])) / den for j in range(5)]
    return (min(0.7, max(0.3, beta[0])),) + tuple(min(0.4, max(0.05, b)) for b in beta[1:])


def _spread(lg: League, games: List[Game], w: np.ndarray) -> Tuple[Dict[str, float], float, Tuple, Tuple]:
    """How far the league's results land from our expectations (in-sample,
    so widened a little: out of sample they land further)."""
    dm, dt, dteam, ww = [], [], [], []
    h1m, h1t, qm, qt, qw, shares, qsh = [], [], [], [], [], [], []
    mrows: List[Tuple[float, List[float], float]] = []
    quarter_games = []
    for k, g in enumerate(games):
        eh, ea = lg.expect(g.home, g.away, g.neutral)
        # Regulation only: overtime adds points a normal game doesn't have
        rh, ra = (sum(p[0] for p in g.periods), sum(p[1] for p in g.periods)) if g.periods else (g.hs, g.as_)
        if g.ot and not g.periods:
            continue
        dm.append((rh - ra) - (eh - ea))
        dt.append((rh + ra) - (eh + ea))
        dteam += [rh - eh, ra - ea]
        ww.append(w[k])
        if g.periods and len(g.periods) == 4:
            first = g.periods[0][0] + g.periods[1][0], g.periods[0][1] + g.periods[1][1]
            tot = rh + ra
            if tot:
                shares.append(sum(first) / tot)
                qsh.append([(ph + pa) / tot for ph, pa in g.periods])
            mrows.append((eh - ea, [first[0] - first[1]] + [ph - pa for ph, pa in g.periods], w[k]))
            quarter_games.append((eh, ea, first, g.periods, w[k]))
    s = dict(DEFAULT_SIGMA)
    if len(dm) >= 20:
        widen = 1.04
        s["margin"] = max(SIGMA_FLOOR["margin"], widen * _wstd(dm, ww))
        s["total"] = max(SIGMA_FLOOR["total"], widen * _wstd(dt, ww))
        s["team"] = max(SIGMA_FLOOR["team"], widen * _wstd(dteam, [x for x in ww for _ in (0, 1)]))
        # Halves and quarters scale from the whole game where there are no quarter scores
        s["h1_margin"], s["h1_total"] = s["margin"] * 0.70, s["total"] * 0.66
        s["q_margin"], s["q_total"] = s["margin"] * 0.50, s["total"] * 0.45
    share = float(np.mean(shares)) if len(shares) >= 20 else 0.5
    qs = tuple(float(x) for x in np.mean(np.array(qsh), axis=0)) if len(qsh) >= 20 else (0.25,) * 4
    ms = _margin_shares(mrows)
    # Parts' spreads around what each part should show (its measured margin share, its point share)
    for eh, ea, first, periods, wk in quarter_games:
        h1m.append((first[0] - first[1]) - ms[0] * (eh - ea))
        h1t.append(sum(first) - (qs[0] + qs[1]) * (eh + ea))
        for i, (ph, pa) in enumerate(periods):
            qm.append((ph - pa) - ms[1 + i] * (eh - ea))
            qt.append((ph + pa) - qs[i] * (eh + ea))
        qw.append(wk)
    if len(h1m) >= 20:
        s["h1_margin"], s["h1_total"] = 1.04 * _wstd(h1m, qw), 1.04 * _wstd(h1t, qw)
        s["q_margin"] = 1.04 * _wstd(qm, [x for x in qw for _ in range(4)])
        s["q_total"] = 1.04 * _wstd(qt, [x for x in qw for _ in range(4)])
    return s, share, qs, ms


# ── Probabilities for one match ────────────────────────────────────────────

def _over(mean: float, sd: float, line: float) -> float:
    """P(value > line) for a whole-number value around `mean` (continuity-corrected)."""
    return 1.0 - _N.cdf((line + 0.5 - mean) / sd) if line == int(line) else 1.0 - _N.cdf((line - mean) / sd)


@dataclass
class Match:
    """Expected points for each team (regulation) and the spreads around them."""
    home_pts: float
    away_pts: float
    sigma: Dict[str, float]
    h1_share: float = 0.5
    source: str = "model"             # model | market | blend
    detail: Dict[str, float] = field(default_factory=dict)
    q_shares: Tuple[float, float, float, float] = (0.25, 0.25, 0.25, 0.25)
    margin_shares: Tuple[float, float, float, float, float] = (0.5, 0.25, 0.25, 0.25, 0.25)
    tie_factor: float = TIE_FACTOR

    @property
    def margin(self) -> float:
        return self.home_pts - self.away_pts

    @property
    def total(self) -> float:
        return self.home_pts + self.away_pts

    def p_tie(self) -> float:
        """Level at the end of regulation (so overtime): the spread's own
        chance, times the league's tie factor."""
        return min(0.3, self.tie_factor * base_tie(self.margin, self.sigma["margin"]))

    def _close(self, m: float) -> Tuple[float, float, float]:
        """The extra ties (over the spread's own) and where they come from:
        one-to-three-point finishes, (extra, share won by 1–3, share lost by 1–3)
        from the side whose margin is m."""
        sd = self.sigma["margin"]
        extra = self.p_tie() - base_tie(m, sd)
        up = _N.cdf((3.5 - m) / sd) - _N.cdf((0.5 - m) / sd)
        down = _N.cdf((-0.5 - m) / sd) - _N.cdf((-3.5 - m) / sd)
        return extra, up / (up + down or 1.0), down / (up + down or 1.0)

    def _reg_win(self, home: bool) -> float:
        """The side wins in regulation."""
        m = self.margin if home else -self.margin
        extra, w_up, _ = self._close(m)
        return max(0.0, 1.0 - _N.cdf((0.5 - m) / self.sigma["margin"]) - extra * w_up)

    def p_win(self, home: bool = True) -> float:
        """Winner, overtime included (SportyBet's basketball "Winner")."""
        p_home = self._reg_win(True) + self.p_tie() * OT_HOME
        return p_home if home else 1.0 - p_home

    def p_3way(self, side: str) -> float:
        """Regulation result: "1", "X" or "2"."""
        if side == "X":
            return self.p_tie()
        return self._reg_win(side == "1")

    def p_handicap(self, home: bool, line: float) -> float:
        """The team's points + line beat the other's (a half-point line: no push).
        Overtime counts, as on SportyBet: it barely moves a margin."""
        m = self.margin if home else -self.margin
        return _over(m, self.sigma["margin"], -line)

    def p_total(self, line: float, over: bool = True) -> float:
        p = _over(self.total, self.sigma["total"], line)
        return p if over else 1.0 - p

    def p_team_total(self, home: bool, line: float, over: bool = True) -> float:
        p = _over(self.home_pts if home else self.away_pts, self.sigma["team"], line)
        return p if over else 1.0 - p

    def p_half(self, kind: str, line: float = 0.0, home: bool = True, over: bool = True) -> float:
        """First half: "handicap" (line for the side) or "total"."""
        if kind == "total":
            p = _over(self.h1_share * self.total, self.sigma["h1_total"], line)
            return p if over else 1.0 - p
        m = self.margin_shares[0] * self.margin * (1 if home else -1)
        return _over(m, self.sigma["h1_margin"], -line)

    def p_half_3way(self, side: str) -> float:
        return self.period("h1").p_3way(side)

    # ── Any part of the game: "full" (regulation), "h1", "h2", "q1".."q4" ──

    def period(self, part: str) -> "Part":
        if part == "full":
            return Part(self.home_pts, self.away_pts, self.sigma["margin"], self.sigma["total"], self.sigma["team"])
        ms = self.margin_shares
        if part in ("h1", "h2"):
            share = self.h1_share if part == "h1" else 1.0 - self.h1_share
            mshare = ms[0] if part == "h1" else ms[3] + ms[4]
            sm, st = self.sigma["h1_margin"], self.sigma["h1_total"]
        else:
            q = int(part[1])
            share, mshare = self.q_shares[q - 1], ms[q]
            sm, st = self.sigma["q_margin"], self.sigma["q_total"]
        # The part's points (its share of the total) split by its share of the margin
        tot, mar = self.total * share, self.margin * mshare
        # One team's points in a part: spread from the part's total, as for the game
        team = self.sigma["team"] * st / self.sigma["total"]
        return Part((tot + mar) / 2, (tot - mar) / 2, sm, st, team)

    # ── Overtime included (SportyBet's full-game winner, handicap and totals) ──

    def _ot_margin_sd(self) -> float:
        return self.sigma["margin"] * OT_SHARE ** 0.5

    def p_handicap_ot(self, home: bool, line: float) -> float:
        """Handicap with overtime counted: a regulation tie is decided by the
        overtime margin (even, with its own small spread)."""
        m = self.margin if home else -self.margin
        sd = self.sigma["margin"]
        p = _over(m, sd, -line)              # regulation margin + line > 0 (by the spread alone)
        if line > 0:                         # a tie counted as a win here: overtime decides it
            p -= base_tie(m, sd)
        # The close finishes that were ties after all, where this line counted them a win
        extra, w_up, w_down = self._close(m)
        up = _N.cdf((3.5 - m) / sd) - _N.cdf((0.5 - m) / sd)
        down = _N.cdf((-0.5 - m) / sd) - _N.cdf((-3.5 - m) / sd)
        lo_up, lo_down = max(0.5, -line), max(-3.5, -line)
        if lo_up < 3.5 and up > 0:
            p -= extra * w_up * (_N.cdf((3.5 - m) / sd) - _N.cdf((lo_up - m) / sd)) / up
        if lo_down < -0.5 and down > 0:
            p -= extra * w_down * (_N.cdf((-0.5 - m) / sd) - _N.cdf((lo_down - m) / sd)) / down
        p += self.p_tie() * (1.0 - _N.cdf(-line / self._ot_margin_sd()))
        return min(1.0, max(0.0, p))

    def _mixture(self, mean: float, sd: float, line: float, extra: float) -> float:
        tie = self.p_tie()
        return (1 - tie) * _over(mean, sd, line) + tie * _over(mean + extra, sd, line)

    def p_total_ot(self, line: float, over: bool = True) -> float:
        p = self._mixture(self.total, self.sigma["total"], line, OT_SHARE * self.total)
        return p if over else 1.0 - p

    def p_team_total_ot(self, home: bool, line: float, over: bool = True) -> float:
        pts = self.home_pts if home else self.away_pts
        p = self._mixture(pts, self.sigma["team"], line, OT_SHARE * self.total / 2)
        return p if over else 1.0 - p


@dataclass
class Part:
    """Expected points in one part of a game (regulation, a half, a quarter)."""
    home: float
    away: float
    sd_margin: float
    sd_total: float
    sd_team: float

    def p_3way(self, side: str) -> float:
        m, sd = self.home - self.away, self.sd_margin
        tie = _N.cdf((0.5 - m) / sd) - _N.cdf((-0.5 - m) / sd)
        if side == "X":
            return tie
        p1 = 1.0 - _N.cdf((0.5 - m) / sd)
        return p1 if side == "1" else max(0.0, 1.0 - p1 - tie)

    def p_handicap(self, home: bool, line: float) -> float:
        m = (self.home - self.away) * (1 if home else -1)
        return _over(m, self.sd_margin, -line)

    def p_total(self, line: float, over: bool = True) -> float:
        p = _over(self.home + self.away, self.sd_total, line)
        return p if over else 1.0 - p

    def p_team_total(self, home: bool, line: float, over: bool = True) -> float:
        p = _over(self.home if home else self.away, self.sd_team, line)
        return p if over else 1.0 - p


def expect(lg: League, home: str, away: str, neutral: bool = False) -> Optional[Match]:
    """Our expectation for a match, or None when either team is unknown."""
    if not (lg.known(home) and lg.known(away)):
        return None
    h, a = lg.expect(home, away, neutral)
    return Match(h, a, dict(lg.sigma), lg.h1_share, "model",
                 {"games_home": round(lg.games.get(home, 0), 1), "games_away": round(lg.games.get(away, 0), 1)},
                 tuple(lg.q_shares), tuple(lg.margin_shares), lg.tie_factor)


# ── The market's expectation, from SportyBet prices ───────────────────────

def _fair(odds: List[float]) -> List[float]:
    inv = [1 / o for o in odds]
    return [x / sum(inv) for x in inv]


def market_margin(sigma: float, winner: Optional[Tuple[float, float]] = None,
                  handicap: Optional[Tuple[float, float, float]] = None) -> Optional[float]:
    """The margin the market expects: from the main handicap (line for the
    home side, home odds, away odds) if there is one, else the winner prices."""
    if handicap:
        line, oh, oa = handicap
        p = min(0.98, max(0.02, _fair([oh, oa])[0]))
        # P(margin + line > 0) = p  =>  margin mean = -line + sigma * z(p)
        return -line + sigma * _N.inv_cdf(p)
    if winner:
        p = min(0.98, max(0.02, _fair(list(winner))[0]))
        return sigma * _N.inv_cdf(p)
    return None


def market_total(sigma: float, total: Optional[Tuple[float, float, float]]) -> Optional[float]:
    """The total the market expects, from the main total (line, over odds, under odds)."""
    if not total:
        return None
    line, oo, ou = total
    p = min(0.98, max(0.02, _fair([oo, ou])[0]))
    return line + sigma * _N.inv_cdf(p)


def blend(ours: Optional[Match], sigma: Dict[str, float], margin: Optional[float], total: Optional[float],
          model_weight: float, h1_share: float = 0.5,
          q_shares: Tuple[float, float, float, float] = (0.25,) * 4,
          margin_shares: Tuple[float, float, float, float, float] = (0.5, 0.25, 0.25, 0.25, 0.25),
          tie_factor: float = TIE_FACTOR) -> Optional[Match]:
    """Our expectation mixed with the market's (weight on ours), or the
    market's alone where we have none, or ours alone where it has none."""
    if margin is None and total is None:
        return ours
    if ours is None:
        if margin is None or total is None:
            return None
        return Match((total + margin) / 2, (total - margin) / 2, dict(sigma), h1_share, "market",
                     {"market_margin": round(margin, 2), "market_total": round(total, 2)}, tuple(q_shares),
                     tuple(margin_shares), tie_factor)
    w = model_weight
    m = ours.margin if margin is None else w * ours.margin + (1 - w) * margin
    t = ours.total if total is None else w * ours.total + (1 - w) * total
    return Match((t + m) / 2, (t - m) / 2, ours.sigma, ours.h1_share, "blend",
                 {**ours.detail, "model_margin": round(ours.margin, 2), "model_total": round(ours.total, 2),
                  **({"market_margin": round(margin, 2)} if margin is not None else {}),
                  **({"market_total": round(total, 2)} if total is not None else {}), "model_weight": w},
                 ours.q_shares, ours.margin_shares, ours.tie_factor)


def log_loss(p: float, hit: bool) -> float:
    p = min(1 - 1e-6, max(1e-6, p))
    return -log(p if hit else 1 - p)
