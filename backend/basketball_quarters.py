"""
Basketball quarter markets — highest and lowest scoring quarter (which,
with "Equal" for a tie), and the highest / lowest scoring quarter's points over/under —
priced from trends in previous games and the two teams' previous meetings.

Per league, from every game with quarter scores (recent ones counting more,
HALF_LIFE_DAYS):

  league pattern   each quarter's share of a game's points (in many leagues
                   the 4th runs high: fouls, free throws, the clock)
  team trends      each team's own shares of the points it scores and of the
                   points it concedes, quarter by quarter, as deviations from
                   the league's — pulled back towards it by K_TEAM games'
                   worth (a team with few games looks like its league)
  meetings         the two teams' previous meetings' quarter shares, the
                   same way (K_H2H meetings' worth)
  spread           how quarters' points scatter around their expectation
                   once the game's own pace is known (a 4×4 covariance)

A match's expected quarter points are the home team's expected points split
by its scoring shares (and the away team's conceding ones), plus the away
team's likewise, nudged by the meetings. The four quarters are then
simulated SIMS times on whole points (the game's pace moving all four
together, the spread moving each one), which gives every outcome's chance:
which quarter is highest or lowest, a tie for it, and the highest or lowest quarter's
points against any line.

How much the team trends and meetings count (K_TEAM, K_H2H) is chosen by
the walk-forward check (check_bb_quarters.py) and read from CONFIG_KEY.
"""

import json
import zlib
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

HALF_LIFE_DAYS = 180
K_TEAM = 25.0           # games' worth pulling a team's quarter profile to its league's
K_H2H = 8.0             # meetings' worth pulling the meetings' profile to the match's
H2H_KEEP = 10           # meetings remembered per pairing
SIMS = 20000
MIN_GAMES = 60          # quarter-scored games a league needs for its own model
CONFIG_KEY = "betiq:bb:quarters_config"
MODEL_KEY = "betiq:bb:quarters"
OUTCOMES = ("Q1", "Q2", "Q3", "Q4", "EQ")

# The leagues' models, set by the server after each fit (main._bb_fit_sync)
MODELS: Dict[str, "QuarterModel"] = {}
CONFIG: Dict[str, float] = {"k_team": K_TEAM, "k_h2h": K_H2H}


@dataclass
class QuarterModel:
    league: str
    shares: List[float]                         # league: each quarter's share of the points
    cov: List[List[float]]                      # quarters' scatter around expectation, pace removed
    scoring: Dict[str, List[float]] = field(default_factory=dict)     # team: deviation of its scoring shares
    conceding: Dict[str, List[float]] = field(default_factory=dict)   # team: deviation of its conceding shares
    games: Dict[str, float] = field(default_factory=dict)             # team: weighted quarter-scored games
    meetings: Dict[str, List[List[float]]] = field(default_factory=dict)   # "a|b" (sorted): [[w, d1..d4], …]
    n: int = 0

    def to_json(self) -> Dict:
        return {k: getattr(self, k) for k in ("league", "shares", "cov", "scoring", "conceding", "games",
                                              "meetings", "n")}

    @classmethod
    def from_json(cls, d: Dict) -> "QuarterModel":
        return cls(**{k: d[k] for k in ("league", "shares", "cov", "scoring", "conceding", "games", "meetings", "n")})


def _pair(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


def _quarters(g) -> Optional[np.ndarray]:
    """(home, away) points per quarter as a 4×2 array, regulation only."""
    p = getattr(g, "periods", None)
    if not p or len(p) != 4:
        return None
    a = np.array(p, dtype=float)
    return a if a.shape == (4, 2) and a.sum() > 0 and (a.sum(axis=0) > 0).all() else None


def fit(league: str, games: Iterable, as_of: Optional[date] = None,
        k_team: Optional[float] = None) -> Optional[QuarterModel]:
    """A league's quarter model from its quarter-scored games before `as_of`."""
    k_team = CONFIG["k_team"] if k_team is None else k_team
    rows = []
    for g in games:
        if as_of is not None and g.date >= as_of.isoformat():
            continue
        q = _quarters(g)
        if q is not None:
            rows.append((g, q))
    if len(rows) < MIN_GAMES:
        return None
    last = as_of or max(date.fromisoformat(g.date) for g, _ in rows)
    w = np.array([0.5 ** (max(0, (last - date.fromisoformat(g.date)).days) / HALF_LIFE_DAYS) for g, _ in rows])
    tot = np.array([q.sum(axis=1) for _, q in rows])                    # n × 4 quarter totals
    T = tot.sum(axis=1)
    shares = (w[:, None] * tot / T[:, None]).sum(axis=0) / w.sum()
    shares = shares / shares.sum()
    resid = tot - T[:, None] * shares[None, :]
    cov = (w[:, None, None] * resid[:, :, None] * resid[:, None, :]).sum(axis=0) / w.sum()

    sums: Dict[str, np.ndarray] = {}
    cons: Dict[str, np.ndarray] = {}
    wt: Dict[str, float] = {}
    meet: Dict[str, List[List[float]]] = {}
    for (g, q), wk in zip(rows, w):
        for team, side in ((g.home, 0), (g.away, 1)):
            scored, conceded = q[:, side], q[:, 1 - side]
            sums[team] = sums.get(team, np.zeros(4)) + wk * (scored / scored.sum() - shares)
            cons[team] = cons.get(team, np.zeros(4)) + wk * (conceded / conceded.sum() - shares)
            wt[team] = wt.get(team, 0.0) + wk
        meet.setdefault(_pair(g.home, g.away), []).append(
            [float(wk)] + [round(float(x), 4) for x in (q.sum(axis=1) / q.sum() - shares)])
    shrink = lambda team: wt[team] / (wt[team] + k_team) if k_team > 0 else 1.0
    return QuarterModel(
        league=league, shares=[round(float(x), 5) for x in shares], cov=[[round(float(x), 3) for x in r] for r in cov],
        scoring={t: [round(float(x), 5) for x in sums[t] / wt[t] * shrink(t)] for t in wt},
        conceding={t: [round(float(x), 5) for x in cons[t] / wt[t] * shrink(t)] for t in wt},
        games={t: round(v, 2) for t, v in wt.items()},
        meetings={k: v[-H2H_KEEP:] for k, v in meet.items()}, n=len(rows))


def expected(m: QuarterModel, home: str, away: str, home_pts: float, away_pts: float,
             k_h2h: Optional[float] = None, use_teams: bool = True) -> np.ndarray:
    """Expected points in each quarter (regulation) for this match."""
    k_h2h = CONFIG["k_h2h"] if k_h2h is None else k_h2h
    L = np.array(m.shares)
    z = np.zeros(4)
    hs, as_ = L.copy(), L.copy()
    if use_teams:
        hs = L + np.array(m.scoring.get(home, z)) + np.array(m.conceding.get(away, z))
        as_ = L + np.array(m.scoring.get(away, z)) + np.array(m.conceding.get(home, z))
    hs, as_ = np.clip(hs, 0.05, None), np.clip(as_, 0.05, None)
    mu = hs / hs.sum() * home_pts + as_ / as_.sum() * away_pts
    past = m.meetings.get(_pair(home, away)) or []
    if past and k_h2h is not None and k_h2h >= 0 and use_teams:
        wsum = sum(r[0] for r in past)
        dev = np.array([sum(r[0] * r[i] for r in past) for i in range(1, 5)]) / wsum if wsum > 0 else z
        weight = len(past) / (len(past) + k_h2h) if k_h2h > 0 else 1.0
        mu = mu + weight * dev * (home_pts + away_pts)
    mu = np.clip(mu, 1.0, None)
    return mu / mu.sum() * (home_pts + away_pts)


def simulate(mu: Sequence[float], cov: Sequence[Sequence[float]], total_sd: float,
             sims: int = SIMS, seed: int = 7) -> np.ndarray:
    """sims × 4 quarter totals, whole points: the game's pace (total_sd) moves
    all four in proportion, the covariance moves each."""
    rng = np.random.default_rng(seed)
    mu = np.asarray(mu, dtype=float)
    pace = rng.normal(0.0, total_sd, size=(sims, 1)) * (mu / mu.sum())[None, :]
    c = np.asarray(cov, dtype=float)
    # The four deviations sum to (about) zero, so the covariance is singular:
    # drawn through its eigenvectors, tiny negative eigenvalues clipped
    vals, vecs = np.linalg.eigh((c + c.T) / 2)
    spread = rng.standard_normal((sims, 4)) @ (vecs * np.sqrt(np.clip(vals, 0.0, None))).T
    return np.clip(np.rint(mu[None, :] + pace + spread), 0, None)


@dataclass
class QuarterOdds:
    """Every quarter outcome's chance for one match, from its simulated quarters."""
    sims: np.ndarray
    expected: List[float]

    def highest(self) -> Dict[str, float]:
        return self._extreme(self.sims.max(axis=1, keepdims=True))

    def lowest(self) -> Dict[str, float]:
        return self._extreme(self.sims.min(axis=1, keepdims=True))

    def _extreme(self, top: np.ndarray) -> Dict[str, float]:
        """Each quarter's chance of being the only one at `top` (each draw's
        highest or lowest), and "EQ" for two or more sharing it."""
        q = self.sims
        ties = (q == top).sum(axis=1)
        out = {f"Q{i + 1}": float(((q[:, i] == top[:, 0]) & (ties == 1)).mean()) for i in range(4)}
        out["EQ"] = float((ties > 1).mean())
        return out

    def p_high_over(self, line: float) -> float:
        return float((self.sims.max(axis=1) > line).mean())

    def p_low_over(self, line: float) -> float:
        return float((self.sims.min(axis=1) > line).mean())


def odds_for(league: str, home: str, away: str, home_pts: float, away_pts: float, total_sd: float,
             fallback_shares: Sequence[float] = (0.25,) * 4, fallback_q_sd: float = 7.6) -> QuarterOdds:
    """A match's quarter chances: its league's model where there is one, else a
    plain one from the league's quarter shares (equal scatter per quarter)."""
    m = MODELS.get(league)
    if m is None:
        s = np.array(fallback_shares, dtype=float)
        sd2 = fallback_q_sd ** 2
        cov = (np.eye(4) - 0.25) * sd2        # each quarter scatters, the four sum to the game's points
        m = QuarterModel(league, list(s / s.sum()), cov.tolist())
    mu = expected(m, home, away, home_pts, away_pts)
    seed = zlib.crc32(f"{home}|{away}".encode())       # the same draws for the same match, every run
    return QuarterOdds(simulate(mu, m.cov, total_sd, seed=seed), [round(float(x), 1) for x in mu])


# ── storage ──────────────────────────────────────────────────────────────

def save(r, models: Dict[str, QuarterModel]) -> None:
    import basketball_data as bd
    r.set(MODEL_KEY, bd.encode([m.to_json() for m in models.values()]))


def load(r) -> Dict[str, QuarterModel]:
    import basketball_data as bd
    try:
        raw = r.get(MODEL_KEY)
        return {d["league"]: QuarterModel.from_json(d) for d in (bd.decode(raw) if raw else [])}
    except Exception:
        return {}


def load_config(r) -> Dict[str, float]:
    try:
        raw = r.get(CONFIG_KEY)
        got = json.loads(raw) if raw else {}
        return {"k_team": float(got.get("k_team", K_TEAM)), "k_h2h": float(got.get("k_h2h", K_H2H))}
    except Exception:
        return {"k_team": K_TEAM, "k_h2h": K_H2H}
