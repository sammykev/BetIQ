"""
Improved sports prediction engine.
Key upgrades over original:
- Elo rating system (dynamic team strength)
- XGBoost (better calibrated probabilities than RandomForest)
- Exponentially weighted form (recent games matter more)
- Goals-against feature (defence strength)
- Attack vs Defence matchup features
"""

import io
import math
from functools import lru_cache

import numpy as np
import pandas as pd
from typing import Dict, List, Optional
import xgboost as xgb
import warnings
import os
import joblib

from team_names import TeamResolver

warnings.filterwarnings("ignore")

MODEL_CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "model_cache.joblib")
MODEL_CACHE_VERSION = 7  # bump when FEATURE_COLS or saved fields change


# ── FIFA ranking-calibrated starting Elo for national teams ───────────────
# Prevents unknown national teams from defaulting to 1500 and looking equal
# to top sides. Source: FIFA World Rankings (June 2025), converted to Elo scale.
# Club teams not listed here start at 1500 (correct for unknown clubs).
FIFA_ELO_SEEDS: Dict[str, float] = {
    # Top 10
    "Argentina": 2050, "France": 1990, "England": 1970, "Brazil": 1950,
    "Portugal": 1930, "Spain": 1920, "Belgium": 1900, "Germany": 1880,
    "Netherlands": 1870, "Croatia": 1855,
    # 11-25
    "Italy": 1845, "Morocco": 1835, "Colombia": 1820, "Uruguay": 1815,
    "United States": 1810, "USA": 1810, "Mexico": 1805, "Switzerland": 1800,
    "Japan": 1795, "Senegal": 1785, "Denmark": 1775, "Austria": 1760,
    "Ukraine": 1755, "Poland": 1745, "South Korea": 1740,
    # 26-50
    "Ecuador": 1730, "Hungary": 1725, "Chile": 1718, "Turkey": 1715,
    "Australia": 1710, "Czech Republic": 1705, "Serbia": 1700,
    "Norway": 1695, "Paraguay": 1688, "Sweden": 1685, "Venezuela": 1680,
    "Iran": 1675, "Wales": 1670, "Romania": 1665, "Slovakia": 1660,
    "Peru": 1655, "Scotland": 1650, "Egypt": 1645, "Ghana": 1640,
    "Ivory Coast": 1635, "Nigeria": 1630, "Algeria": 1625,
    "Cameroon": 1618, "South Africa": 1610, "Tunisia": 1605,
    "Senegal": 1785, "Mali": 1598, "Morocco": 1835, "Qatar": 1570,
    # Others
    "Uzbekistan": 1590, "Bosnia-Herzegovina": 1585, "Slovenia": 1582,
    "Albania": 1575, "Finland": 1568, "Greece": 1562, "Israel": 1558,
    "Canada": 1555, "Costa Rica": 1545, "Panama": 1538, "Jamaica": 1528,
    "Bolivia": 1520, "Honduras": 1515, "El Salvador": 1510, "Haiti": 1505,
    "New Zealand": 1515, "India": 1498, "Saudi Arabia": 1545,
    "Iraq": 1535, "Oman": 1520, "UAE": 1512, "Bahrain": 1505,
    "Libya": 1498, "Zimbabwe": 1492, "Rwanda": 1488,
}

# Competition importance multiplier for Elo K factor.
# Higher-stakes competitions should update ratings more aggressively.
COMPETITION_K: Dict[str, float] = {
    "CL": 1.5, "WC": 1.5, "EC": 1.4,   # Champions League, World Cup, Euros
    "EL": 1.3, "CA": 1.3,               # Europa League, Copa América
    "PL": 1.2, "PD": 1.2, "SA": 1.2,   # Premier League, La Liga, Serie A
    "BL1": 1.2, "FL1": 1.2,             # Bundesliga, Ligue 1
    "DED": 1.1, "PPL": 1.1,             # Eredivisie, Primeira Liga
    "ELC": 1.0,                          # Championship
}


# ── League strength ──────────────────────────────────────────────────────
# Team Elo is earned inside a league, so a 1600 in the Eredivisie and a 1600
# in the Premier League aren't the same team. With LEAGUE_STRENGTH on, each
# domestic league gets an Elo offset — started from the priors below and
# learned from matches between leagues (European competitions and domestic
# cups, fed as "strength only" rows: they move ratings, never form or the
# training set) — and a team changing league (promotion, relegation) keeps
# its real strength: Elo + old offset - new offset. On only when the
# walk-forward check says it helps (see club_cups.check).
LEAGUE_STRENGTH = False
DOMESTIC_LEAGUES = {"PL", "ELC", "EL1", "EL2", "PD", "SD", "SA", "SB", "BL1", "BL2", "FL1", "FL2", "DED", "JPL",
                    "PPL", "SPL", "D1", "GSL", "BSA", "TSL", "AUT", "SUI", "DEN", "NOR", "SWE", "POL", "ROU"}
LEAGUE_OFFSET_PRIOR = {"PL": 0.0, "PD": -15.0, "SA": -25.0, "BL1": -25.0, "FL1": -60.0, "PPL": -90.0,
                       "DED": -100.0, "ELC": -170.0, "BL2": -190.0, "SD": -190.0, "SB": -200.0, "FL2": -230.0,
                       "EL1": -280.0, "EL2": -360.0, "JPL": -110.0, "SPL": -150.0, "GSL": -150.0, "BSA": -80.0,
                       # Rough starting points (UEFA coefficients); European results move them
                       "TSL": -140.0, "AUT": -150.0, "SUI": -160.0, "DEN": -170.0, "NOR": -180.0,
                       "POL": -200.0, "SWE": -210.0, "ROU": -230.0}
LEAGUE_K = 6.0          # league offset learning rate per cross-league match
STRENGTH_TEAM_K = 0.5   # share of the normal Elo K a strength-only match moves the teams by


def _dc_tau(i: int, j: int, mu_h: float, mu_a: float, rho: float) -> float:
    """Dixon-Coles correction factor for low-scoring scorelines (0-0, 1-0, 0-1, 1-1)."""
    if i == 0 and j == 0:
        return 1.0 - mu_h * mu_a * rho
    if i == 0 and j == 1:
        return 1.0 + mu_h * rho
    if i == 1 and j == 0:
        return 1.0 + mu_a * rho
    if i == 1 and j == 1:
        return 1.0 - rho
    return 1.0


class EloSystem:
    K = 32
    HOME_ADV = 80  # Elo points added for home advantage

    def __init__(self):
        self.ratings: Dict[str, float] = {}

    def get(self, team: str) -> float:
        # Check direct rating first (updated by match results)
        if team in self.ratings:
            return self.ratings[team]
        # Fall back to FIFA seed for national teams, else 1500 for clubs
        return FIFA_ELO_SEEDS.get(team, 1500.0)

    def seed_national_teams(self):
        """Pre-seed national team Elo from FIFA rankings if no match data yet."""
        for team, seed in FIFA_ELO_SEEDS.items():
            if team not in self.ratings:
                self.ratings[team] = seed

    def expected(self, home: str, away: str) -> float:
        diff = self.get(home) - self.get(away) + self.HOME_ADV
        return 1.0 / (1.0 + 10 ** (-diff / 400))

    def update(self, home: str, away: str, result: str, competition: str = ""):
        exp = self.expected(home, away)
        actual = {"H": 1.0, "D": 0.5, "A": 0.0}[result]
        k = self.K * COMPETITION_K.get(competition, 1.0)
        delta = k * (actual - exp)
        self.ratings[home] = self.get(home) + delta
        self.ratings[away] = self.get(away) - delta


def _known(x) -> bool:
    """A real number, not None/NaN."""
    try:
        return x is not None and not math.isnan(float(x))
    except (TypeError, ValueError):
        return False


def _ewm(values: List[float], alpha: float = 0.25) -> float:
    """Exponentially weighted mean — more recent = higher weight."""
    if not values:
        return 0.0
    arr = np.array(values[-12:], dtype=float)
    weights = (1 - alpha) ** np.arange(len(arr) - 1, -1, -1)
    return float(np.average(arr, weights=weights))


FEATURE_COLS = [
    "HomeElo", "AwayElo", "EloDiff",
    "Home_G_Avg", "Away_G_Avg",
    "Home_GA_Avg", "Away_GA_Avg",
    "Home_Form", "Away_Form",
    "Home_G_Var", "Away_G_Var",
    "Atk_vs_Def",
    "Def_vs_Atk",
    "Home_Cards_Avg", "Away_Cards_Avg",
    # Market odds features — overround-adjusted implied probabilities
    # These are the single most predictive features available.
    # At training time: from football-data.co.uk (B365H/D/A columns)
    # At prediction time: from The Odds API for upcoming fixtures
    "Impl_Home", "Impl_Draw", "Impl_Away",
    # Dixon-Coles home/away split features
    "Home_Attack",   # home team attack strength at home vs league avg (>1 = above avg)
    "Away_Attack",   # away team attack strength away vs league avg
    "Home_Defense",  # home team defensive weakness at home (>1 = leaks more than avg)
    "Away_Defense",  # away team defensive weakness away
    "xG_Home",       # Dixon-Coles expected goals for home team
    "xG_Away",       # Dixon-Coles expected goals for away team
    # Rest days and head-to-head features
    "Days_Rest_Home", "Days_Rest_Away",
    "H2H_Home_Rate", "H2H_Draw_Rate",
    # League context features
    "League_Avg_Goals", "League_Home_WinRate",
]

# For fixtures without bookmaker odds. The main models learn from Impl_* and
# never saw the league-average stand-in during training, so a separate set
# trained without them predicts those fixtures from team strength alone.
NO_ODDS_COLS = [c for c in FEATURE_COLS if not c.startswith("Impl_")]

# Shots on target (league CSVs' HST/AST): a steadier read of a team's
# strength than goals, which a couple of lucky finishes can swing. Rolling
# averages for and against; teams with no shot data (national teams, old UCL
# rows) get the league average, so their matches still train. On only when
# the walk-forward backtest says it helps (USE_SHOTS): True for every
# model, "goals" for the over/under goals models only, False for none.
SHOT_COLS = ["Home_SOT_For", "Away_SOT_For", "Home_SOT_Against", "Away_SOT_Against",
             "SOT_Atk_vs_Def", "SOT_Def_vs_Atk"]
# Walk-forward Feb-May 2025 (1,347 matches): in every model, 1X2 log loss
# 0.9820 → 0.9842 (worse); in the goals models only, 1X2 unchanged and
# over 2.5 log loss 0.6871 → 0.6823, Brier 0.2467 → 0.2447.
USE_SHOTS = "goals"
DEFAULT_SOT = 4.3  # shots on target per team per match, top European leagues


def _rounded_probs(p_h: float, p_d: float, p_a: float, p_o15: float, p_o25: float) -> Dict:
    return {
        "p_home": round(p_h, 3), "p_draw": round(p_d, 3), "p_away": round(p_a, 3),
        "p_over15": round(p_o15, 3), "p_over25": round(p_o25, 3),
    }


# BTTS = sigmoid(a + b·logit(grid BTTS) + c·logit(p_over25)), fitted walk-forward
# on 2023-24; on 2024-25 Brier 0.2476 vs 0.2483 for the base rate — BTTS is
# close to a coin flip for every model.
BTTS_CALIBRATION = (0.159, 0.646, 0.264)

# Heavy favourites to win by 2+ / 3+: the grid overrates them at the top
# end (2024-25: said 75%, happened 48%, 44 matches), so those are capped
HANDICAP_CAPS = {1.5: 0.6, 2.5: 0.45}

_K = np.arange(11)
_FACT = np.array([math.factorial(k) for k in _K], dtype=float)
_TOTALS = np.add.outer(_K, _K)
_DIFF = np.subtract.outer(_K, _K)


def _grid(mh: float, ma: float, rho: float) -> np.ndarray:
    mh, ma = max(mh, 0.05), max(ma, 0.05)
    g = np.outer(mh ** _K * np.exp(-mh) / _FACT, ma ** _K * np.exp(-ma) / _FACT)
    g[0, 0] *= _dc_tau(0, 0, mh, ma, rho)
    g[0, 1] *= _dc_tau(0, 1, mh, ma, rho)
    g[1, 0] *= _dc_tau(1, 0, mh, ma, rho)
    g[1, 1] *= _dc_tau(1, 1, mh, ma, rho)
    g = np.clip(g, 0, None)
    return g / g.sum()


@lru_cache(maxsize=4096)
def score_grid(xg_h: float, xg_a: float, p_o25: float, p_h: float, p_a: float, rho: float) -> np.ndarray:
    """P(home goals = i, away goals = j): Dixon-Coles from the expected goals,
    with the home share of goals fitted so home wins' share of decided
    matches equals the classifier's p_h/(p_h+p_a), and the total so over 2.5
    equals its p_o25 — every market from the grid then agrees with the
    tested 1X2 and over/under models. Inputs rounded by the caller (cache)."""
    total = max(xg_h + xg_a, 0.2)
    share = xg_h / total
    target = p_h / (p_h + p_a) if p_h + p_a > 0 else 0.5
    for _ in range(3):
        lo, hi = 0.02, 0.98
        for _ in range(20):
            mid = (lo + hi) / 2
            g = _grid(total * mid, total * (1 - mid), rho)
            h, a = g[_DIFF > 0].sum(), g[_DIFF < 0].sum()
            lo, hi = (mid, hi) if h / (h + a) < target else (lo, mid)
        share = (lo + hi) / 2
        lo, hi = 0.2, 10.0
        for _ in range(20):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if _grid(mid * share, mid * (1 - share), rho)[_TOTALS > 2.5].sum() < p_o25 else (lo, mid)
        total = (lo + hi) / 2
    g = _grid(total * share, total * (1 - share), rho)
    g.setflags(write=False)
    return g


def goal_markets(xg_h: float, xg_a: float, p_o25: float, p_h: float, p_a: float,
                 rho: float = -0.13) -> Dict:
    """Every goals market derived from the score grid (score_grid): BTTS,
    over 3.5, draw no bet, team totals, clean sheets, win to nil, handicaps
    and double chance & total combos.

    Walk-forward on 2024-25 (3,222 league matches, predicted from earlier
    matches only): each beats last season's base rate and is calibrated,
    except BTTS (the raw grid is overconfident, so BTTS_CALIBRATION) and
    heavy-favourite handicaps (HANDICAP_CAPS)."""
    g = score_grid(round(xg_h, 3), round(xg_a, 3), round(p_o25, 3), round(p_h, 3), round(p_a, 3), round(rho, 3))
    r3 = lambda x: round(float(x), 3)
    logit = lambda p: math.log(min(max(p, 1e-4), 1 - 1e-4) / (1 - min(max(p, 1e-4), 1 - 1e-4)))
    a, b_grid, b_o25 = BTTS_CALIBRATION
    z = a + b_grid * logit(float(g[1:, 1:].sum())) + b_o25 * logit(p_o25)
    dnb = p_h / (p_h + p_a) if p_h + p_a > 0 else 0.5

    handicap = {}
    for line, cap in HANDICAP_CAPS.items():
        home = min(float(g[_DIFF > line].sum()), cap)    # home -line
        away = min(float(g[_DIFF < -line].sum()), cap)   # away -line
        handicap[f"home_-{line}"], handicap[f"away_+{line}"] = r3(home), r3(1 - home)
        handicap[f"away_-{line}"], handicap[f"home_+{line}"] = r3(away), r3(1 - away)

    dc_sides = {"1X": _DIFF >= 0, "X2": _DIFF <= 0, "12": _DIFF != 0}
    dc_total = {dc: {f"{side}{line}": r3(g[mask & ((_TOTALS > line) if side == "o" else (_TOTALS < line))].sum())
                     for line in (1.5, 2.5, 3.5) for side in ("o", "u")}
                for dc, mask in dc_sides.items()}
    return {
        "p_btts": round(1 / (1 + math.exp(-z)), 3),
        "p_over35": r3(g[_TOTALS > 3.5].sum()),
        "p_dnb_home": round(dnb, 3),
        "goal_markets": {
            "team_totals": {"home": {f"{l}": r3(g[_K > l, :].sum()) for l in (0.5, 1.5, 2.5)},
                            "away": {f"{l}": r3(g[:, _K > l].sum()) for l in (0.5, 1.5, 2.5)}},
            "clean_sheet": {"home": r3(g[:, 0].sum()), "away": r3(g[0, :].sum())},
            "win_to_nil": {"home": r3(g[1:, 0].sum()), "away": r3(g[0, 1:].sum())},
            "handicap": handicap,
            "dc_total": dc_total,
        },
    }


def calibrate(p, cal: Dict) -> tuple:
    """1X2 probabilities adjusted for one competition (europe_model's fit):
    sharpened or softened by the temperature `t`, then blended with the
    competition's own result rates `base` by weight `w`. A model that
    overrates what it knows about the clubs gets pulled toward the rates."""
    t, w, base = float(cal.get("t", 1.0)), float(cal.get("w", 1.0)), cal.get("base") or (1 / 3, 1 / 3, 1 / 3)
    q = [max(float(x), 1e-9) ** t for x in p]
    total = sum(q)
    return tuple(w * x / total + (1 - w) * float(b) for x, b in zip(q, base))


def pick_tips(p_h: float, p_d: float, p_a: float, p_o15: float, p_o25: float) -> Dict:
    """
    The 1X2 / double-chance tip and the goals tip for a set of probabilities.
    Shared by live predictions and the backtest so both judge the same picks.
    """
    p_u25 = 1.0 - p_o25

    # 1x2 tip
    if p_h > 0.55:
        tip1x2, code = "Home Win", "1"
    elif p_a > 0.55:
        tip1x2, code = "Away Win", "2"
    # No straight draw tip: above ~33% the model overrates draws (backtest:
    # said 34%, happened 18%), so those matches go to double chance instead.
    elif p_h + p_d > 0.75:
        tip1x2, code = "Home or Draw", "1X"
    elif p_a + p_d > 0.75:
        tip1x2, code = "Away or Draw", "2X"
    else:
        tip1x2, code = "Skip", "?"

    # Probability of the 1X2 / double-chance tip itself. goals_confidence
    # below is the goals tip's probability — a different market.
    tip_conf = {
        "1": p_h, "X": p_d, "2": p_a,
        "1X": p_h + p_d, "2X": p_a + p_d,
    }.get(code)

    # Goals tip
    if p_o15 > 0.82:
        tip_g, gtype, gconf = "Over 1.5 Goals", "Banker", p_o15
    elif p_o25 > 0.58:
        tip_g, gtype, gconf = "Over 2.0 (Asian)", "Asian", p_o25
    elif p_o15 > 0.68:
        tip_g, gtype, gconf = "Over 1.0 (Asian)", "Asian", p_o15
    elif p_u25 > 0.62:
        tip_g, gtype, gconf = "Under 3.0 (Asian)", "Asian", p_u25
    elif p_h > 0.60:
        tip_g, gtype, gconf = "Over 1.0 (Asian)", "Asian", p_o15
    else:
        tip_g, gtype, gconf = "Skip", "Skip", 0.0

    return {
        "tip_1x2": tip1x2,
        "tip_code": code,
        "tip_confidence": round(tip_conf, 3) if tip_conf is not None else None,
        "tip_goals": tip_g,
        "goals_type": gtype,
        "goals_confidence": round(gconf, 3),
    }


_XGB_BASE = dict(
    n_estimators=400, max_depth=4, learning_rate=0.04,
    subsample=0.8, colsample_bytree=0.8,
    random_state=42, verbosity=0,
)


def _fit_calibrated(X: pd.DataFrame, y: pd.Series, multiclass: bool = False):
    """XGBoost with 3-fold isotonic calibration (plain XGBoost when data is too
    thin to calibrate). The goals models used to skip calibration and ran
    overconfident — Over 1.5 said 86% and hit 83% in the 2024-25 backtest."""
    params = ({**_XGB_BASE, "num_class": 3, "objective": "multi:softprob", "eval_metric": "mlogloss"}
              if multiclass else {**_XGB_BASE, "objective": "binary:logistic", "eval_metric": "logloss"})
    if len(X) >= 200:
        try:
            from sklearn.calibration import CalibratedClassifierCV
            model = CalibratedClassifierCV(xgb.XGBClassifier(**params), method="isotonic", cv=3)
            model.fit(X, y)
            return model
        except Exception as e:
            print(f"[Predictor] Calibration failed (using raw XGBoost): {e}")
    return xgb.XGBClassifier(**params).fit(X, y)


class LeaguePredictor:
    """
    Per-league predictor. Call train() once, then predict_match() for each fixture.
    The Elo + rolling stats state is maintained in-memory between calls.
    """

    def __init__(self):
        self.elo = EloSystem()
        self.team_stats: Dict[str, dict] = {}
        self.models: Dict[str, xgb.XGBClassifier] = {}
        self._ready = False
        self._league_home_goals: List[float] = []
        self._league_away_goals: List[float] = []
        self.last_match_date: Dict[str, str] = {}   # team -> "YYYY-MM-DD"
        self.h2h: Dict[str, Dict] = {}              # "teamA:teamB" -> {a_wins,draws,b_wins,total_goals,n}
        self.dc_rho: float = -0.13                  # Dixon-Coles correlation (estimated in train())
        self.league_stats: Dict[str, dict] = {}     # league_code -> {avg_goals, home_win_rate}
        self.use_shots = USE_SHOTS                   # False / True / "goals" (see SHOT_COLS)
        self.use_league_strength = LEAGUE_STRENGTH   # see LEAGUE_STRENGTH
        self.team_league: Dict[str, str] = {}        # team -> its latest domestic league
        self.league_offset: Dict[str, float] = {}    # league -> Elo offset (see LEAGUE_STRENGTH)
        self._league_sot: List[float] = []           # shots on target per team-match, for the default

    # ── league strength ──────────────────────────────────────────────────
    def _offset(self, team: str) -> float:
        if not getattr(self, "use_league_strength", False):
            return 0.0
        league = getattr(self, "team_league", {}).get(team)
        if league is None:
            return 0.0
        offsets = self.league_offset
        return offsets.get(league, LEAGUE_OFFSET_PRIOR.get(league, 0.0))

    def strength(self, team: str) -> float:
        """Elo on one scale across leagues: the team's Elo plus its league's offset."""
        return self.elo.get(team) + self._offset(team)

    def _note_league(self, team: str, league: str) -> None:
        """Record a team's domestic league; on a change, carry its strength over."""
        if not league or league not in DOMESTIC_LEAGUES:
            return
        old = self.team_league.get(team)
        if old == league:
            return
        if old is not None and getattr(self, "use_league_strength", False) and team in self.elo.ratings:
            before = self._offset(team)
            self.team_league[team] = league
            self.elo.ratings[team] += before - self._offset(team)
            return
        self.team_league[team] = league

    def _strength_update(self, home: str, away: str, result: str, competition: str = "") -> None:
        """A European / cup match: moves the teams' Elo (by a share of the usual
        K) and, between two leagues, the leagues' offsets. Nothing else."""
        if self._ready:
            home, away = self.canon(home), self.canon(away)
        if result not in ("H", "D", "A"):
            return
        diff = self.strength(home) - self.strength(away) + EloSystem.HOME_ADV
        exp = 1.0 / (1.0 + 10 ** (-diff / 400))
        delta = {"H": 1.0, "D": 0.5, "A": 0.0}[result] - exp
        k = EloSystem.K * COMPETITION_K.get(competition, 1.0) * STRENGTH_TEAM_K
        self.elo.ratings[home] = self.elo.get(home) + k * delta
        self.elo.ratings[away] = self.elo.get(away) - k * delta
        lh, la = self.team_league.get(home), self.team_league.get(away)
        if getattr(self, "use_league_strength", False) and lh and la and lh != la:
            for lg, sign in ((lh, 1.0), (la, -1.0)):
                self.league_offset[lg] = self.league_offset.get(lg, LEAGUE_OFFSET_PRIOR.get(lg, 0.0)) + sign * LEAGUE_K * delta

    def context_update(self, r) -> None:
        """A context row (a league CSV row): ratings and form, no training
        row, no baselines."""
        day = str(r["Date"].date()) if pd.notna(r.get("Date")) else None
        self._update(r["HomeTeam"], r["AwayTeam"], r["Result"], r["FTHG"], r["FTAG"],
                     hyc=r.get("HY"), ayc=r.get("AY"), hrc=r.get("HR"), arc=r.get("AR"),
                     match_date=day, competition=str(r.get("league", "") or ""),
                     hst=r.get("HST"), ast=r.get("AST"), baseline=False)

    def cols(self, odds: bool = True, target: str = "win") -> List[str]:
        """The feature columns a model trains and predicts on: target "win"
        (the result model) or "goals" (the over/under models)."""
        base = FEATURE_COLS if odds else NO_ODDS_COLS
        shots = getattr(self, "use_shots", False)
        use = shots is True or (shots == "goals" and target == "goals")
        return base + SHOT_COLS if use else list(base)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def canon(self, team: str) -> str:
        """
        The name this model knows a live team by ("Manchester United FC" →
        "Man United"). Training uses the CSV names as-is; everything after
        training (fixtures, live results, lookups) goes through here.
        """
        resolver = getattr(self, "_resolver", None)
        if resolver is None:
            resolver = self._resolver = TeamResolver(self.team_stats.keys())
        return resolver.resolve(team)

    def _init(self, team: str):
        if team not in self.team_stats:
            resolver = getattr(self, "_resolver", None)
            if resolver is not None:
                resolver.add(team)
            self.team_stats[team] = {
                "gf": [], "ga": [], "pts": [],
                "yc": [],           # card weight (yellow + 2*red)
                "home_gf": [],      # goals scored when playing at home
                "home_ga": [],      # goals conceded when playing at home
                "away_gf": [],      # goals scored when playing away
                "away_ga": [],      # goals conceded when playing away
                "sotf": [],         # shots on target for
                "sota": [],         # shots on target against
            }

    # League-average implied odds — used when market odds aren't available at prediction time
    # Updated during training based on actual data
    _avg_impl: Dict[str, float] = {"H": 0.46, "D": 0.27, "A": 0.27}

    def _feats(self, home: str, away: str,
               odds_home: float = 0, odds_draw: float = 0, odds_away: float = 0,
               match_date: str = None, league: str = "") -> Dict:
        if self._ready:
            home, away = self.canon(home), self.canon(away)
        self._init(home)
        self._init(away)
        hs, as_ = self.team_stats[home], self.team_stats[away]

        h_gf = _ewm(hs["gf"]) if hs["gf"] else 1.5
        a_gf = _ewm(as_["gf"]) if as_["gf"] else 1.5
        h_ga = _ewm(hs["ga"]) if hs["ga"] else 1.2
        a_ga = _ewm(as_["ga"]) if as_["ga"] else 1.2
        h_form = _ewm(hs["pts"]) if hs["pts"] else 1.0
        a_form = _ewm(as_["pts"]) if as_["pts"] else 1.0
        h_var = float(np.std(hs["gf"][-6:])) if len(hs["gf"]) >= 3 else 0.8
        a_var = float(np.std(as_["gf"][-6:])) if len(as_["gf"]) >= 3 else 0.8
        h_elo = self.strength(home)
        a_elo = self.strength(away)
        h_yc = _ewm(hs["yc"]) if hs["yc"] else 1.5
        a_yc = _ewm(as_["yc"]) if as_["yc"] else 1.5

        # Convert raw odds to overround-adjusted implied probabilities
        has_odds = odds_home > 1 and odds_draw > 1 and odds_away > 1
        if has_odds:
            raw = {"H": 1/odds_home, "D": 1/odds_draw, "A": 1/odds_away}
            overround = sum(raw.values())
            impl_h = raw["H"] / overround
            impl_d = raw["D"] / overround
            impl_a = raw["A"] / overround
        else:
            # Fall back to league-average when odds unavailable
            impl_h = self._avg_impl["H"]
            impl_d = self._avg_impl["D"]
            impl_a = self._avg_impl["A"]

        # --- Dixon-Coles home/away attack/defense ratings ---
        # League baseline: rolling average of home/away goals across all training matches
        lg_home = float(np.mean(self._league_home_goals[-2000:])) if len(self._league_home_goals) >= 20 else 1.50
        lg_away = float(np.mean(self._league_away_goals[-2000:])) if len(self._league_away_goals) >= 20 else 1.20

        # Use venue-specific stats when >= 3 games available, fall back to overall avg
        h_home_scored   = _ewm(hs["home_gf"]) if len(hs.get("home_gf", [])) >= 3 else h_gf
        h_home_conceded = _ewm(hs["home_ga"]) if len(hs.get("home_ga", [])) >= 3 else h_ga
        a_away_scored   = _ewm(as_["away_gf"]) if len(as_.get("away_gf", [])) >= 3 else a_gf
        a_away_conceded = _ewm(as_["away_ga"]) if len(as_.get("away_ga", [])) >= 3 else a_ga

        # Relative strength vs league baseline (1.0 = exactly average)
        h_attack  = h_home_scored   / max(lg_home, 0.01)   # home scoring vs avg home scorer
        a_defense = a_away_conceded / max(lg_home, 0.01)   # away defensive weakness vs home teams
        a_attack  = a_away_scored   / max(lg_away, 0.01)   # away scoring vs avg away scorer
        h_defense = h_home_conceded / max(lg_away, 0.01)   # home defensive weakness vs away teams

        # Dixon-Coles λ: attack × opponent_defense_weakness × league_baseline
        xg_h = max(0.1, h_attack * a_defense * lg_home)
        xg_a = max(0.1, a_attack * h_defense * lg_away)

        # ── Rest days ────────────────────────────────────────────────────────
        from datetime import date as _date
        _today = match_date or str(_date.today())
        def _days_since(team: str) -> float:
            last = self.last_match_date.get(team)
            if not last:
                return 7.0  # default: assume 7 days rest
            try:
                delta = (_date.fromisoformat(_today) - _date.fromisoformat(last)).days
                return max(1.0, min(float(delta), 21.0))  # clamp 1-21
            except Exception:
                return 7.0

        days_rest_home = _days_since(home)
        days_rest_away = _days_since(away)

        # ── H2H feature ──────────────────────────────────────────────────────
        _a, _b = sorted([home.lower(), away.lower()])
        _h2h = self.h2h.get(f"{_a}:{_b}", {})
        _h2h_n = _h2h.get("n", 0)
        _home_is_a = home.lower() == _a

        if _h2h_n >= 3:
            _a_rate = _h2h.get("a_wins", 0) / _h2h_n
            _draw_rate = _h2h.get("draws", 0) / _h2h_n
            # Rotate perspective so "H2H_Home_Rate" is always from home team's view
            h2h_home_rate = _a_rate if _home_is_a else (1 - _a_rate - _draw_rate)
            h2h_draw_rate = _draw_rate
        else:
            h2h_home_rate = self._avg_impl.get("H", 0.46)
            h2h_draw_rate = self._avg_impl.get("D", 0.27)

        # ── Shots on target ──────────────────────────────────────────────────
        league_sot = getattr(self, "_league_sot", [])
        sot_default = float(np.mean(league_sot[-4000:])) if len(league_sot) >= 20 else DEFAULT_SOT
        def _sot(team_stats: dict, key: str) -> float:
            vals = team_stats.get(key) or []
            return _ewm(vals) if len(vals) >= 3 else sot_default
        h_sotf, h_sota = _sot(hs, "sotf"), _sot(hs, "sota")
        a_sotf, a_sota = _sot(as_, "sotf"), _sot(as_, "sota")

        # ── League context features ───────────────────────────────────────────
        lg_stats = self.league_stats.get(league, {})
        league_avg_goals   = lg_stats.get("avg_goals",    xg_h + xg_a)
        league_home_wr     = lg_stats.get("home_win_rate", self._avg_impl.get("H", 0.46))

        return {
            "HomeElo": h_elo, "AwayElo": a_elo, "EloDiff": h_elo - a_elo,
            "Home_G_Avg": h_gf, "Away_G_Avg": a_gf,
            "Home_GA_Avg": h_ga, "Away_GA_Avg": a_ga,
            "Home_Form": h_form, "Away_Form": a_form,
            "Home_G_Var": h_var, "Away_G_Var": a_var,
            "Atk_vs_Def": h_gf - a_ga,
            "Def_vs_Atk": a_gf - h_ga,
            "Home_Cards_Avg": h_yc,
            "Away_Cards_Avg": a_yc,
            "Impl_Home": impl_h,
            "Impl_Draw": impl_d,
            "Impl_Away": impl_a,
            "Home_Attack":  round(h_attack, 4),
            "Away_Attack":  round(a_attack, 4),
            "Home_Defense": round(h_defense, 4),
            "Away_Defense": round(a_defense, 4),
            "xG_Home":      round(xg_h, 4),
            "xG_Away":      round(xg_a, 4),
            "Days_Rest_Home": days_rest_home,
            "Days_Rest_Away": days_rest_away,
            "H2H_Home_Rate":  round(h2h_home_rate, 4),
            "H2H_Draw_Rate":  round(h2h_draw_rate, 4),
            "League_Avg_Goals":   round(league_avg_goals, 4),
            "League_Home_WinRate": round(league_home_wr, 4),
            "Home_SOT_For": round(h_sotf, 4), "Away_SOT_For": round(a_sotf, 4),
            "Home_SOT_Against": round(h_sota, 4), "Away_SOT_Against": round(a_sota, 4),
            "SOT_Atk_vs_Def": round(h_sotf - a_sota, 4),
            "SOT_Def_vs_Atk": round(a_sotf - h_sota, 4),
            "_has_odds": has_odds,  # routes predict_proba; not a model feature
            "_league": league,      # picks a calibration (predict_proba); not a feature
        }

    def _update(
        self, home: str, away: str, result: str,
        fthg: float, ftag: float,
        hyc: float = None, ayc: float = None,
        hrc: float = None, arc: float = None,
        match_date: str = None,
        competition: str = "",
        hst: float = None, ast: float = None,
        baseline: bool = True,
    ):
        """One played match into the ratings and rolling stats. baseline=False
        (a context row, see train) leaves the all-matches averages alone —
        the goals and shots baselines every team's ratings are measured
        against."""
        if self._ready:
            home, away = self.canon(home), self.canon(away)
        self._init(home)
        self._init(away)
        if competition in DOMESTIC_LEAGUES:
            self._note_league(home, competition)
            self._note_league(away, competition)
        # Shots on target, where the source has them (the league CSVs, ESPN)
        if _known(hst) and _known(ast):
            hst, ast = float(hst), float(ast)
            for team, f, a in ((home, hst, ast), (away, ast, hst)):
                self.team_stats[team].setdefault("sotf", []).append(f)
                self.team_stats[team].setdefault("sota", []).append(a)
            if not hasattr(self, "_league_sot"):
                self._league_sot = []
            if baseline:
                self._league_sot += [hst, ast]
        # Reject NaN goals — can come from CSV rows with missing scores
        try:
            fthg, ftag = float(fthg), float(ftag)
            if fthg != fthg or ftag != ftag:  # NaN check (NaN != NaN)
                self.elo.update(home, away, result)
                return
        except (TypeError, ValueError):
            return
        # Overall rolling stats (kept for fallback)
        self.team_stats[home]["gf"].append(fthg)
        self.team_stats[home]["ga"].append(ftag)
        self.team_stats[away]["gf"].append(ftag)
        self.team_stats[away]["ga"].append(fthg)
        # Venue-specific stats for Dixon-Coles
        self.team_stats[home]["home_gf"].append(fthg)
        self.team_stats[home]["home_ga"].append(ftag)
        self.team_stats[away]["away_gf"].append(ftag)
        self.team_stats[away]["away_ga"].append(fthg)
        # League-wide baseline (used to normalise attack/defense ratings)
        if baseline:
            self._league_home_goals.append(fthg)
            self._league_away_goals.append(ftag)
        pts = {"H": (3, 0), "D": (1, 1), "A": (0, 3)}[result]
        self.team_stats[home]["pts"].append(pts[0])
        self.team_stats[away]["pts"].append(pts[1])

        # Cards (yellow + 2*red = total card weight). Sources without card
        # data (international and UCL results) pass NaN: skip rather than
        # record it — a NaN in the window made the feature NaN for the next
        # 12 games, and training drops rows with any NaN feature, so those
        # matches never trained the model.
        if not _known(hyc) or not _known(ayc):
            hyc = ayc = None
        h_cards = hyc + (hrc if _known(hrc) else 0) * 2 if hyc is not None else 0
        a_cards = ayc + (arc if _known(arc) else 0) * 2 if ayc is not None else 0
        if hyc is not None:
            self.team_stats[home]["yc"].append(h_cards)
            self.team_stats[away]["yc"].append(a_cards)

        self.elo.update(home, away, result, competition=competition)

        # Track last match date for rest-days feature
        if match_date:
            self.last_match_date[home] = match_date
            self.last_match_date[away] = match_date

        # Track head-to-head record (team names sorted for consistent key)
        a, b = sorted([home.lower(), away.lower()])
        h2h_key = f"{a}:{b}"
        if h2h_key not in self.h2h:
            self.h2h[h2h_key] = {"a_wins": 0, "draws": 0, "b_wins": 0, "total_goals": 0, "n": 0}
        rec = self.h2h[h2h_key]
        rec["n"] += 1
        rec["total_goals"] += fthg + ftag
        if result == "D":
            rec["draws"] += 1
        elif (result == "H" and home.lower() == a) or (result == "A" and away.lower() == a):
            rec["a_wins"] += 1
        else:
            rec["b_wins"] += 1

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def train(self, matches: pd.DataFrame):
        """
        Build features + train XGBoost models.
        Accepts both legacy CSV format and football-data.co.uk format (with B365 odds).
        Required: HomeTeam, AwayTeam, Result (H/D/A), FTHG, FTAG
        Optional: B365H, B365D, B365A (odds — massively improve accuracy)
        """
        self._ready = False       # names are taken as-is while training
        self._resolver = None
        self.elo = EloSystem()
        self.team_stats = {}
        self._league_home_goals = []
        self._league_away_goals = []
        self.last_match_date = {}
        self.h2h = {}
        self.league_stats = {}
        self.dc_rho = -0.13
        self._league_sot = []
        self.team_league, self.league_offset = {}, {}
        if not hasattr(self, "use_shots"):
            self.use_shots = USE_SHOTS
        if not hasattr(self, "use_league_strength"):
            self.use_league_strength = LEAGUE_STRENGTH

        # Context rows (leagues the model learns ratings from but doesn't train
        # on, e.g. main._load_extra_leagues): only their teams' ratings and
        # form change, so the predictions for every other team stay the same
        context = matches["Context"].fillna(False).astype(bool) if "Context" in matches.columns \
            else pd.Series(False, index=matches.index)
        trained = matches[~context]

        has_odds = all(c in matches.columns for c in ["B365H", "B365D", "B365A"])
        if has_odds:
            print(f"[Predictor] Training WITH bookmaker odds features ({len(matches)} matches)")
        else:
            print(f"[Predictor] Training WITHOUT odds — add football-data.co.uk CSVs for better accuracy")

        # Compute avg implied probs across all training data (for fallback at predict time)
        if has_odds:
            valid_odds = trained[["B365H","B365D","B365A"]].dropna()
            valid_odds = valid_odds[(valid_odds > 1).all(axis=1)]
            if len(valid_odds):
                inv = valid_odds.apply(lambda x: 1/x)
                overrounds = inv.sum(axis=1)
                impl = inv.div(overrounds, axis=0)
                self._avg_impl = {
                    "H": float(impl["B365H"].mean()),
                    "D": float(impl["B365D"].mean()),
                    "A": float(impl["B365A"].mean()),
                }

        # Pre-compute per-league stats for league context features.
        # Uses a separate pass over data so league_stats is ready before the training loop.
        if "league" in matches.columns:
            for lg_code, grp in matches.groupby("league"):
                total_goals = grp["FTHG"].fillna(0) + grp["FTAG"].fillna(0)
                home_wins   = (grp["Result"] == "H").sum()
                lg_n        = max(len(grp), 1)
                self.league_stats[str(lg_code)] = {
                    "avg_goals":    float(total_goals.mean()),
                    "home_win_rate": float(home_wins / lg_n),
                }

        # Estimate Dixon-Coles rho from training data using MLE approximation.
        # rho < 0 means 0-0 and 1-1 are more common than independent Poisson predicts.
        try:
            goals_h = trained["FTHG"].dropna()
            goals_a = trained["FTAG"].dropna()
            if len(goals_h) >= 100:
                mu_h = float(goals_h.mean())
                mu_a = float(goals_a.mean())
                n00 = int(((goals_h == 0) & (goals_a == 0)).sum())
                n11 = int(((goals_h == 1) & (goals_a == 1)).sum())
                n_total = len(goals_h)
                # Expected counts under independence
                e00 = n_total * np.exp(-mu_h) * np.exp(-mu_a)
                e11 = n_total * mu_h * np.exp(-mu_h) * mu_a * np.exp(-mu_a)
                # rho estimated from 0-0 excess; clamp to valid range
                if e00 > 0:
                    self.dc_rho = float(np.clip((n00 - e00) / (e00 * mu_h * mu_a), -0.5, 0.0))
                    print(f"[Predictor] DC rho estimated: {self.dc_rho:.4f}")
        except Exception as _e:
            print(f"[Predictor] DC rho estimation failed, using default: {_e}")

        rows = []
        strength_only = matches["StrengthOnly"].fillna(False).astype(bool) if "StrengthOnly" in matches.columns \
            else pd.Series(False, index=matches.index)
        for idx, r in matches.iterrows():
            if strength_only[idx]:
                self._strength_update(r["HomeTeam"], r["AwayTeam"], r["Result"], str(r.get("league", "") or ""))
                continue
            if context[idx]:
                self.context_update(r)
                continue
            oh = float(r.get("B365H") or 0)
            od = float(r.get("B365D") or 0)
            oa = float(r.get("B365A") or 0)
            match_date_str = str(r["Date"].date()) if pd.notna(r.get("Date")) else None
            lg = str(r.get("league", "")) if "league" in r.index else ""
            f = self._feats(r["HomeTeam"], r["AwayTeam"], oh, od, oa,
                            match_date=match_date_str, league=lg)
            f["Result"] = r["Result"]
            f["TotalGoals"] = r["FTHG"] + r["FTAG"]
            rows.append(f)
            self._update(
                r["HomeTeam"], r["AwayTeam"], r["Result"], r["FTHG"], r["FTAG"],
                hyc=r.get("HomeYellowCards") or r.get("HY"),
                ayc=r.get("AwayYellowCards") or r.get("AY"),
                hrc=r.get("HomeRedCards") or r.get("HR"),
                arc=r.get("AwayRedCards") or r.get("AR"),
                match_date=match_date_str,
                competition=lg,
                hst=r.get("HST"), ast=r.get("AST"),
            )

        df = pd.DataFrame(rows).dropna(subset=self.cols(target="goals"))

        y_win = df["Result"].map({"A": 0, "D": 1, "H": 2})
        y_o15 = (df["TotalGoals"] >= 2).astype(int)
        y_o25 = (df["TotalGoals"] >= 3).astype(int)
        for suffix, odds in (("", True), ("_noodds", False)):
            X_win, X_goals = df[self.cols(odds)], df[self.cols(odds, target="goals")]
            self.models["win" + suffix] = _fit_calibrated(X_win, y_win, multiclass=True)
            self.models["o15" + suffix] = _fit_calibrated(X_goals, y_o15)
            self.models["o25" + suffix] = _fit_calibrated(X_goals, y_o25)
        print("[Predictor] Trained calibrated result + goals models, with and without odds")

        self._ready = True

    def _payload(self, data_mtime: float) -> Dict:
        return {
            "version": MODEL_CACHE_VERSION,
            "models": self.models,
            "elo": self.elo,
            "team_stats": self.team_stats,
            "_avg_impl": self._avg_impl,
            "_league_home_goals": self._league_home_goals,
            "_league_away_goals": self._league_away_goals,
            "last_match_date": self.last_match_date,
            "h2h": self.h2h,
            "dc_rho": self.dc_rho,
            "league_stats": self.league_stats,
            "use_shots": getattr(self, "use_shots", False),
            "use_league_strength": getattr(self, "use_league_strength", False),
            "team_league": getattr(self, "team_league", {}),
            "league_offset": getattr(self, "league_offset", {}),
            "_league_sot": getattr(self, "_league_sot", []),
            "data_mtime": data_mtime,
        }

    @classmethod
    def _from_payload(cls, payload: Dict) -> Optional["LeaguePredictor"]:
        if payload.get("version", 1) != MODEL_CACHE_VERSION:
            print("[Cache] Cache version mismatch — retraining.")
            return None
        inst = cls.__new__(cls)
        inst.models               = payload["models"]
        inst.elo                  = payload["elo"]
        inst.team_stats           = payload["team_stats"]
        inst._avg_impl            = payload["_avg_impl"]
        inst._league_home_goals   = payload.get("_league_home_goals", [])
        inst._league_away_goals   = payload.get("_league_away_goals", [])
        inst.last_match_date      = payload.get("last_match_date", {})
        inst.h2h                  = payload.get("h2h", {})
        inst.dc_rho               = payload.get("dc_rho", -0.13)
        inst.league_stats         = payload.get("league_stats", {})
        inst.use_shots            = payload.get("use_shots", False)
        inst.use_league_strength  = payload.get("use_league_strength", False)
        inst.team_league          = payload.get("team_league", {})
        inst.league_offset        = payload.get("league_offset", {})
        inst._league_sot          = payload.get("_league_sot", [])
        inst._ready               = True
        return inst

    def save_cache(self, data_mtime: float):
        """Persist trained model + state to disk so cold restarts skip retraining."""
        try:
            os.makedirs(os.path.dirname(MODEL_CACHE_PATH), exist_ok=True)
            joblib.dump(self._payload(data_mtime), MODEL_CACHE_PATH, compress=3)
            print(f"[Cache] Model saved → {MODEL_CACHE_PATH}")
        except Exception as e:
            print(f"[Cache] Save failed: {e}")

    @classmethod
    def load_cache(cls, data_mtime: float) -> Optional["LeaguePredictor"]:
        """Load cached model if it's newer than training data. Returns None if stale/missing."""
        if not os.path.exists(MODEL_CACHE_PATH):
            return None
        try:
            payload = joblib.load(MODEL_CACHE_PATH)
            if payload.get("data_mtime", 0) < data_mtime:
                print("[Cache] Training data is newer than cache — retraining.")
                return None
            inst = cls._from_payload(payload)
            if inst is not None:
                print("[Cache] Model loaded from disk — skipping training.")
            return inst
        except Exception as e:
            print(f"[Cache] Load failed: {e}")
            return None

    def to_bytes(self, data_mtime: float = 0.0) -> bytes:
        """The trained model as one blob (model_store shares it between servers)."""
        buf = io.BytesIO()
        joblib.dump(self._payload(data_mtime), buf, compress=3)
        return buf.getvalue()

    @classmethod
    def from_bytes(cls, blob: bytes) -> Optional["LeaguePredictor"]:
        return cls._from_payload(joblib.load(io.BytesIO(blob)))

    def predict_match(self, home: str, away: str,
                      odds_home: float = 0, odds_draw: float = 0,
                      odds_away: float = 0,
                      match_date: str = None, league: str = "") -> Optional[Dict]:
        if not self._ready:
            return None
        f = self._feats(home, away, odds_home, odds_draw, odds_away,
                        match_date=match_date, league=league)
        probs = self.predict_proba(f)
        p_h, _, p_a, _, p_o25 = probs
        return {**_rounded_probs(*probs), **pick_tips(*probs),
                **goal_markets(f["xG_Home"], f["xG_Away"], p_o25, p_h, p_a, getattr(self, "dc_rho", -0.13))}

    def predict_proba(self, feats: Dict) -> tuple:
        """(p_home, p_draw, p_away, p_over15, p_over25) for one feature row.
        Fixtures without odds use the models trained without odds features."""
        no_odds = not feats.get("_has_odds", True) and "win_noodds" in self.models
        suffix, odds = ("_noodds", False) if no_odds else ("", True)
        row = pd.DataFrame([feats])
        X, X_goals = row[self.cols(odds)], row[self.cols(odds, target="goals")]
        wp = self.models["win" + suffix].predict_proba(X)[0]
        p_a, p_d, p_h = float(wp[0]), float(wp[1]), float(wp[2])
        cal = (getattr(self, "calibration", None) or {}).get(feats.get("_league") or "")
        if cal:
            p_h, p_d, p_a = calibrate((p_h, p_d, p_a), cal)
        p_o15 = float(self.models["o15" + suffix].predict_proba(X_goals)[0][1])
        p_o25 = float(self.models["o25" + suffix].predict_proba(X_goals)[0][1])
        return p_h, p_d, p_a, p_o15, p_o25

    def predict_match_full(self, home: str, away: str,
                           odds_home: float = 0, odds_draw: float = 0,
                           odds_away: float = 0, league: str = "") -> Optional[Dict]:
        """Full multi-market analysis using XGBoost + Poisson distribution."""
        if not self._ready:
            return None

        f = self._feats(home, away, odds_home, odds_draw, odds_away, league=league)
        p_h, p_d, p_a, p_o15, p_o25 = self.predict_proba(f)

        # Dixon-Coles expected goals (venue-adjusted attack vs defense)
        xg_h = f["xG_Home"]
        xg_a = f["xG_Away"]

        # --- Poisson joint probability matrix with Dixon-Coles tau correction ---
        MAX = 9
        from math import exp, factorial
        def pmf(k, lam): return (lam**k * exp(-lam)) / factorial(k)

        rho = getattr(self, "dc_rho", -0.13)
        joint = np.array([
            [pmf(i, xg_h) * pmf(j, xg_a) * _dc_tau(i, j, xg_h, xg_a, rho)
             for j in range(MAX)]
            for i in range(MAX)
        ])
        # Normalise so probabilities sum to 1 after tau adjustment
        joint = joint / joint.sum()

        # Over/Under markets (Poisson-based)
        def p_over(n):
            return float(1 - sum(joint[i][j] for i in range(MAX) for j in range(MAX) if i + j <= n))

        ou = {
            "over_05":  round(p_over(0), 3),
            "over_15":  round(p_over(1), 3),
            "over_25":  round(p_over(2), 3),
            "over_35":  round(p_over(3), 3),
            "over_45":  round(p_over(4), 3),
        }
        ou["under_05"] = round(1 - ou["over_05"], 3)
        ou["under_15"] = round(1 - ou["over_15"], 3)
        ou["under_25"] = round(1 - ou["over_25"], 3)
        ou["under_35"] = round(1 - ou["over_35"], 3)
        ou["under_45"] = round(1 - ou["over_45"], 3)

        # Odd/Even total goals — common bet-builder leg, derived from the same joint matrix
        p_goals_odd = float(sum(joint[i][j] for i in range(MAX) for j in range(MAX) if (i + j) % 2 == 1))
        p_goals_even = round(1 - p_goals_odd, 3)
        p_goals_odd = round(p_goals_odd, 3)

        # Goal-range buckets — 0-1, 2-3, 4+ goals
        p_range_01 = round(float(sum(joint[i][j] for i in range(MAX) for j in range(MAX) if i + j <= 1)), 3)
        p_range_23 = round(float(sum(joint[i][j] for i in range(MAX) for j in range(MAX) if 2 <= i + j <= 3)), 3)
        p_range_4p = round(max(0.0, 1 - p_range_01 - p_range_23), 3)

        # BTTS
        p_home_scores = float(1 - pmf(0, xg_h))
        p_away_scores = float(1 - pmf(0, xg_a))
        p_btts_yes = round(p_home_scores * p_away_scores, 3)
        p_btts_no  = round(1 - p_btts_yes, 3)

        # Asian Handicap (Poisson)
        def p_ah(handicap):
            total = 0.0
            for i in range(MAX):
                for j in range(MAX):
                    margin = (i + handicap) - j
                    if margin > 0:
                        total += joint[i][j]
                    elif margin == 0:
                        total += joint[i][j] * 0.5  # push/refund
            return round(float(total), 3)

        ah = {
            "home_-05": p_ah(-0.5),
            "away_+05": round(1 - p_ah(-0.5), 3),
            "home_+05": p_ah(0.5),
            "away_-05": round(1 - p_ah(0.5), 3),
            "home_-15": p_ah(-1.5),
            "away_+15": round(1 - p_ah(-1.5), 3),
            "home_+15": p_ah(1.5),
            "away_-15": round(1 - p_ah(1.5), 3),
        }

        # Correct score (top 8 most likely)
        scores = []
        for i in range(MAX):
            for j in range(MAX):
                scores.append({"score": f"{i}-{j}", "prob": round(float(joint[i][j]), 4)})
        top_scores = sorted(scores, key=lambda x: -x["prob"])[:8]

        # Half-time result (approx: scale λ by 0.45 for first half)
        xg_h_ht = xg_h * 0.45
        xg_a_ht = xg_a * 0.45
        joint_ht = np.array([[pmf(i, xg_h_ht) * pmf(j, xg_a_ht) for j in range(5)] for i in range(5)])
        p_ht_h = float(sum(joint_ht[i][j] for i in range(5) for j in range(5) if i > j))
        p_ht_d = float(sum(joint_ht[i][j] for i in range(5) for j in range(5) if i == j))
        p_ht_a = float(sum(joint_ht[i][j] for i in range(5) for j in range(5) if i < j))

        # Double Chance
        dc_1x = round(p_h + p_d, 3)
        dc_x2 = round(p_d + p_a, 3)
        dc_12 = round(p_h + p_a, 3)

        # Win to Nil (Poisson: win AND opponent scores 0)
        p_home_0 = float(pmf(0, xg_h))   # P(home scores 0)
        p_away_0 = float(pmf(0, xg_a))   # P(away scores 0)

        # P(home win to nil) = P(home > away AND away = 0)
        p_wtn_home = round(float(sum(
            joint[i][0] for i in range(1, MAX)
        )), 3)
        p_wtn_away = round(float(sum(
            joint[0][j] for j in range(1, MAX)
        )), 3)
        p_wtn_no = round(1 - p_wtn_home - p_wtn_away, 3)

        # Clean Sheet (Poisson: team concedes 0)
        p_cs_home = round(p_away_0, 3)   # home keeps clean sheet = away scores 0
        p_cs_away = round(p_home_0, 3)   # away keeps clean sheet = home scores 0
        p_no_cs_home = round(1 - p_cs_home, 3)
        p_no_cs_away = round(1 - p_cs_away, 3)

        # Result + BTTS (6 combined outcomes)
        p_h_btts  = round(float(sum(joint[i][j] for i in range(1,MAX) for j in range(1,MAX) if i>j)), 3)
        p_d_btts  = round(float(sum(joint[i][j] for i in range(1,MAX) for j in range(1,MAX) if i==j)), 3)
        p_a_btts  = round(float(sum(joint[i][j] for i in range(1,MAX) for j in range(1,MAX) if i<j)), 3)
        p_h_nbtts = round(max(0, p_h - p_h_btts), 3)
        p_d_nbtts = round(max(0, p_d - p_d_btts), 3)
        p_a_nbtts = round(max(0, p_a - p_a_btts), 3)

        # Draw No Bet
        dnb_home = round(p_h / max(p_h + p_a, 0.01), 3)
        dnb_away = round(p_a / max(p_h + p_a, 0.01), 3)

        # --- Build all markets ---
        markets = [
            {
                "id": "1x2",
                "name": "Match Result (1X2)",
                "options": [
                    {"label": "Home Win", "code": "1", "prob": round(p_h, 3)},
                    {"label": "Draw",     "code": "X", "prob": round(p_d, 3)},
                    {"label": "Away Win", "code": "2", "prob": round(p_a, 3)},
                ],
            },
            {
                "id": "double_chance",
                "name": "Double Chance",
                "options": [
                    {"label": "Home or Draw", "code": "1X", "prob": dc_1x},
                    {"label": "Draw or Away", "code": "X2", "prob": dc_x2},
                    {"label": "Home or Away", "code": "12", "prob": dc_12},
                ],
            },
            {
                "id": "btts",
                "name": "Both Teams to Score",
                "options": [
                    {"label": "Yes", "code": "BTTS-Y", "prob": p_btts_yes},
                    {"label": "No",  "code": "BTTS-N", "prob": p_btts_no},
                ],
            },
            {
                "id": "goals_ou",
                "name": "Total Goals Over/Under",
                "options": [
                    {"label": "Over 0.5",  "code": "O05",  "prob": ou["over_05"]},
                    {"label": "Over 1.5",  "code": "O15",  "prob": ou["over_15"]},
                    {"label": "Over 2.5",  "code": "O25",  "prob": ou["over_25"]},
                    {"label": "Over 3.5",  "code": "O35",  "prob": ou["over_35"]},
                    {"label": "Over 4.5",  "code": "O45",  "prob": ou["over_45"]},
                    {"label": "Under 1.5", "code": "U15",  "prob": ou["under_15"]},
                    {"label": "Under 2.5", "code": "U25",  "prob": ou["under_25"]},
                    {"label": "Under 3.5", "code": "U35",  "prob": ou["under_35"]},
                ],
            },
            {
                "id": "goals_odd_even",
                "name": "Total Goals — Odd/Even",
                "options": [
                    {"label": "Odd",  "code": "GOE-ODD",  "prob": p_goals_odd},
                    {"label": "Even", "code": "GOE-EVEN", "prob": p_goals_even},
                ],
            },
            {
                "id": "goal_range",
                "name": "Total Goals — Range",
                "options": [
                    {"label": "0-1 Goals", "code": "GR-01", "prob": p_range_01},
                    {"label": "2-3 Goals", "code": "GR-23", "prob": p_range_23},
                    {"label": "4+ Goals",  "code": "GR-4P", "prob": p_range_4p},
                ],
            },
            {
                "id": "asian_handicap",
                "name": "Asian Handicap",
                "options": [
                    {"label": f"{home} -0.5", "code": "AH-H05", "prob": ah["home_-05"]},
                    {"label": f"{away} +0.5", "code": "AH-A05", "prob": ah["away_+05"]},
                    {"label": f"{home} -1.5", "code": "AH-H15", "prob": ah["home_-15"]},
                    {"label": f"{away} +1.5", "code": "AH-A15", "prob": ah["away_+15"]},
                    {"label": f"{home} +0.5", "code": "AH-H+05","prob": ah["home_+05"]},
                    {"label": f"{away} -0.5", "code": "AH-A-05","prob": ah["away_-05"]},
                ],
            },
            {
                "id": "half_time",
                "name": "Half Time Result",
                "options": [
                    {"label": "Home Win at HT", "code": "HT1", "prob": round(p_ht_h, 3)},
                    {"label": "Draw at HT",     "code": "HTX", "prob": round(p_ht_d, 3)},
                    {"label": "Away Win at HT", "code": "HT2", "prob": round(p_ht_a, 3)},
                ],
            },
            {
                "id": "correct_score",
                "name": "Correct Score",
                "options": [{"label": s["score"], "code": f"CS-{s['score']}", "prob": s["prob"]} for s in top_scores],
            },
            {
                "id": "win_to_nil",
                "name": "Win to Nil",
                "options": [
                    {"label": f"{home} Win to Nil", "code": "WTN-H", "prob": p_wtn_home},
                    {"label": f"{away} Win to Nil", "code": "WTN-A", "prob": p_wtn_away},
                    {"label": "No Win to Nil",      "code": "WTN-N", "prob": p_wtn_no},
                ],
            },
            {
                "id": "clean_sheet",
                "name": "Clean Sheet",
                "options": [
                    {"label": f"{home} Clean Sheet",    "code": "CS-H-Y", "prob": p_cs_home},
                    {"label": f"{home} No Clean Sheet", "code": "CS-H-N", "prob": p_no_cs_home},
                    {"label": f"{away} Clean Sheet",    "code": "CS-A-Y", "prob": p_cs_away},
                    {"label": f"{away} No Clean Sheet", "code": "CS-A-N", "prob": p_no_cs_away},
                ],
            },
            {
                "id": "result_btts",
                "name": "Result & Both Teams Score",
                "options": [
                    {"label": f"{home} Win & Yes", "code": "RB-H-Y", "prob": p_h_btts},
                    {"label": "Draw & Yes",         "code": "RB-D-Y", "prob": p_d_btts},
                    {"label": f"{away} Win & Yes", "code": "RB-A-Y", "prob": p_a_btts},
                    {"label": f"{home} Win & No",  "code": "RB-H-N", "prob": p_h_nbtts},
                    {"label": "Draw & No",          "code": "RB-D-N", "prob": p_d_nbtts},
                    {"label": f"{away} Win & No",  "code": "RB-A-N", "prob": p_a_nbtts},
                ],
            },
            {
                "id": "draw_no_bet",
                "name": "Draw No Bet",
                "options": [
                    {"label": f"{home} Win", "code": "DNB-H", "prob": dnb_home},
                    {"label": f"{away} Win", "code": "DNB-A", "prob": dnb_away},
                ],
            },
        ]

        # Find overall best pick across all markets (excluding correct score — too specific)
        best_pick = None
        best_prob = 0.0
        for m in markets:
            if m["id"] == "correct_score":
                continue
            for opt in m["options"]:
                if opt["prob"] > best_prob:
                    best_prob = opt["prob"]
                    best_pick = {"market": m["name"], "market_id": m["id"], **opt}

        # --- Elo context ---
        elo_h = self.strength(home)
        elo_a = self.strength(away)
        elo_gap = elo_h - elo_a  # positive = home stronger
        # Implied win prob from Elo alone (includes home advantage)
        elo_win_prob = round(1 / (1 + 10 ** (-(elo_gap + EloSystem.HOME_ADV) / 400)), 3)

        abs_gap = abs(elo_gap)
        if abs_gap < 30:
            elo_label = "Evenly Matched"
            elo_desc  = "Both teams are virtually equal in quality. Form and tactics decide this one."
        elif abs_gap < 80:
            elo_label = "Slight Edge"
            elo_desc  = f"{'Home' if elo_gap > 0 else 'Away'} team holds a small but meaningful quality advantage."
        elif abs_gap < 150:
            elo_label = "Clear Advantage"
            elo_desc  = f"{'Home' if elo_gap > 0 else 'Away'} team is noticeably stronger — expect them to control the match."
        elif abs_gap < 250:
            elo_label = "Strong Favourite"
            elo_desc  = f"{'Home' if elo_gap > 0 else 'Away'} team has significantly outperformed their opponent over recent history."
        else:
            elo_label = "Heavy Favourite"
            elo_desc  = f"{'Home' if elo_gap > 0 else 'Away'} team is vastly superior based on historical performance ratings."

        # Elo is scored on a 0–2000 scale in practice; show gap out of 400
        # (400 pts = ~90% win probability in pure Elo)
        elo_context = {
            "home": round(elo_h, 0),
            "away": round(elo_a, 0),
            "gap": round(elo_gap, 0),
            "gap_out_of": 400,
            "leading": home if elo_gap >= 0 else away,
            "label": elo_label,
            "description": elo_desc,
            "implied_win_prob": elo_win_prob,
        }

        return {
            "xg_home": round(xg_h, 2),
            "xg_away": round(xg_a, 2),
            "markets": markets,
            "recommended": best_pick,
            "elo": elo_context,
        }

    def predict_cards(
        self,
        home: str,
        away: str,
        cards_df: pd.DataFrame,
        corners_df: Optional[pd.DataFrame] = None,
    ) -> Dict:
        """
        Predict corners and cards markets using team averages from fbref data.
        Returns market dicts ready to append to the markets list.
        """
        from math import exp, factorial

        def pmf(k, lam):
            try:
                return (lam ** k * exp(-lam)) / factorial(k)
            except Exception:
                return 0.0

        def p_over_poisson(lam, threshold):
            total = sum(pmf(k, lam) for k in range(int(threshold) + 1))
            # handle .5 lines
            return round(max(0.0, min(1.0, 1.0 - total)), 3)

        result = {}

        # ── Cards ──────────────────────────────────────────────────────────
        if cards_df is not None and not cards_df.empty:
            def get_cards(team, is_home):
                row = cards_df[cards_df["team"].str.lower() == team.lower()]
                if row.empty:
                    return (1.5, 1.5)
                r = row.iloc[0]
                if is_home:
                    return (
                        float(r.get("home_cards_for", 1.5)),
                        float(r.get("home_cards_against", 1.5)),
                    )
                return (
                    float(r.get("away_cards_for", 1.8)),
                    float(r.get("away_cards_against", 1.8)),
                )

            h_cf, h_ca = get_cards(home, is_home=True)
            a_cf, a_ca = get_cards(away, is_home=False)

            exp_h = (h_cf + a_ca) / 2
            exp_a = (a_cf + h_ca) / 2
            exp_total = exp_h + exp_a

            cards_market = {
                "id": "cards",
                "name": "Total Cards",
                "options": [
                    {"label": "Over 2.5 Cards", "code": "CRD-O25", "prob": p_over_poisson(exp_total, 2)},
                    {"label": "Over 3.5 Cards", "code": "CRD-O35", "prob": p_over_poisson(exp_total, 3)},
                    {"label": "Over 4.5 Cards", "code": "CRD-O45", "prob": p_over_poisson(exp_total, 4)},
                    {"label": "Under 3.5 Cards", "code": "CRD-U35", "prob": round(1 - p_over_poisson(exp_total, 3), 3)},
                    {"label": "Under 4.5 Cards", "code": "CRD-U45", "prob": round(1 - p_over_poisson(exp_total, 4), 3)},
                ],
            }
            result["cards"] = cards_market

        # ── Corners ────────────────────────────────────────────────────────
        if corners_df is not None and not corners_df.empty:
            def get_corners(team, is_home):
                row = corners_df[corners_df["team"].str.lower() == team.lower()]
                if row.empty:
                    return (5.0, 4.5) if is_home else (4.5, 5.0)
                r = row.iloc[0]
                if is_home:
                    return (
                        float(r.get("home_corners_for", 5.0)),
                        float(r.get("home_corners_against", 4.5)),
                    )
                return (
                    float(r.get("away_corners_for", 4.5)),
                    float(r.get("away_corners_against", 5.0)),
                )

            h_cnf, h_cna = get_corners(home, is_home=True)
            a_cnf, a_cna = get_corners(away, is_home=False)

            exp_h_corners = (h_cnf + a_cna) / 2
            exp_a_corners = (a_cnf + h_cna) / 2
            exp_total_corners = exp_h_corners + exp_a_corners

            corners_market = {
                "id": "corners",
                "name": "Total Corners",
                "options": [
                    {"label": "Over 8.5 Corners",  "code": "CNR-O85",  "prob": p_over_poisson(exp_total_corners, 8)},
                    {"label": "Over 9.5 Corners",  "code": "CNR-O95",  "prob": p_over_poisson(exp_total_corners, 9)},
                    {"label": "Over 10.5 Corners", "code": "CNR-O105", "prob": p_over_poisson(exp_total_corners, 10)},
                    {"label": "Under 9.5 Corners", "code": "CNR-U95",  "prob": round(1 - p_over_poisson(exp_total_corners, 9), 3)},
                    {"label": "Under 10.5 Corners","code": "CNR-U105", "prob": round(1 - p_over_poisson(exp_total_corners, 10), 3)},
                ],
            }
            result["corners"] = corners_market

            # Team-to-have-most-corners is a common bet-builder leg
            p_home_more = 0.0
            MAX_C = 20
            for i in range(MAX_C):
                for j in range(MAX_C):
                    if i > j:
                        p_home_more += pmf(i, exp_h_corners) * pmf(j, exp_a_corners)
            p_away_more = 0.0
            for i in range(MAX_C):
                for j in range(MAX_C):
                    if j > i:
                        p_away_more += pmf(i, exp_h_corners) * pmf(j, exp_a_corners)
            p_tie_c = max(0.0, 1 - p_home_more - p_away_more)

            result["corners_race"] = {
                "id": "corners_race",
                "name": "Corners — Most Wins",
                "options": [
                    {"label": f"{home} — Most Corners", "code": "CNR-H", "prob": round(p_home_more, 3)},
                    {"label": "Tie",                     "code": "CNR-T", "prob": round(p_tie_c, 3)},
                    {"label": f"{away} — Most Corners", "code": "CNR-A", "prob": round(p_away_more, 3)},
                ],
            }

        return result
