"""
Improved sports prediction engine.
Key upgrades over original:
- Elo rating system (dynamic team strength)
- XGBoost (better calibrated probabilities than RandomForest)
- Exponentially weighted form (recent games matter more)
- Goals-against feature (defence strength)
- Attack vs Defence matchup features
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional
import xgboost as xgb
import warnings
import os

warnings.filterwarnings("ignore")


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

    def update(self, home: str, away: str, result: str):
        exp = self.expected(home, away)
        actual = {"H": 1.0, "D": 0.5, "A": 0.0}[result]
        delta = self.K * (actual - exp)
        self.ratings[home] = self.get(home) + delta
        self.ratings[away] = self.get(away) - delta


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
]


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

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _init(self, team: str):
        if team not in self.team_stats:
            self.team_stats[team] = {
                "gf": [], "ga": [], "pts": [],
                "yc": [],           # card weight (yellow + 2*red)
                "home_gf": [],      # goals scored when playing at home
                "home_ga": [],      # goals conceded when playing at home
                "away_gf": [],      # goals scored when playing away
                "away_ga": [],      # goals conceded when playing away
            }

    # League-average implied odds — used when market odds aren't available at prediction time
    # Updated during training based on actual data
    _avg_impl: Dict[str, float] = {"H": 0.46, "D": 0.27, "A": 0.27}

    def _feats(self, home: str, away: str,
               odds_home: float = 0, odds_draw: float = 0, odds_away: float = 0,
               match_date: str = None) -> Dict:
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
        h_elo = self.elo.get(home)
        a_elo = self.elo.get(away)
        h_yc = _ewm(hs["yc"]) if hs["yc"] else 1.5
        a_yc = _ewm(as_["yc"]) if as_["yc"] else 1.5

        # Convert raw odds to overround-adjusted implied probabilities
        if odds_home > 1 and odds_draw > 1 and odds_away > 1:
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
        }

    def _update(
        self, home: str, away: str, result: str,
        fthg: float, ftag: float,
        hyc: float = None, ayc: float = None,
        hrc: float = None, arc: float = None,
        match_date: str = None,
    ):
        self._init(home)
        self._init(away)
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
        self._league_home_goals.append(fthg)
        self._league_away_goals.append(ftag)
        pts = {"H": (3, 0), "D": (1, 1), "A": (0, 3)}[result]
        self.team_stats[home]["pts"].append(pts[0])
        self.team_stats[away]["pts"].append(pts[1])

        # Cards (yellow + 2*red = total card weight)
        h_cards = (hyc or 0) + (hrc or 0) * 2
        a_cards = (ayc or 0) + (arc or 0) * 2
        if hyc is not None:
            self.team_stats[home]["yc"].append(h_cards)
            self.team_stats[away]["yc"].append(a_cards)

        self.elo.update(home, away, result)

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
        self.elo = EloSystem()
        self.team_stats = {}
        self._league_home_goals = []
        self._league_away_goals = []
        self.last_match_date = {}
        self.h2h = {}

        has_odds = all(c in matches.columns for c in ["B365H", "B365D", "B365A"])
        if has_odds:
            print(f"[Predictor] Training WITH bookmaker odds features ({len(matches)} matches)")
        else:
            print(f"[Predictor] Training WITHOUT odds — add football-data.co.uk CSVs for better accuracy")

        # Compute avg implied probs across all training data (for fallback at predict time)
        if has_odds:
            valid_odds = matches[["B365H","B365D","B365A"]].dropna()
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

        rows = []
        for _, r in matches.iterrows():
            oh = float(r.get("B365H") or 0)
            od = float(r.get("B365D") or 0)
            oa = float(r.get("B365A") or 0)
            match_date_str = str(r["Date"].date()) if pd.notna(r.get("Date")) else None
            f = self._feats(r["HomeTeam"], r["AwayTeam"], oh, od, oa, match_date=match_date_str)
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
            )

        df = pd.DataFrame(rows).dropna(subset=FEATURE_COLS)
        X = df[FEATURE_COLS]

        xgb_base = dict(
            n_estimators=400, max_depth=4, learning_rate=0.04,
            subsample=0.8, colsample_bytree=0.8,
            random_state=42, verbosity=0,
        )

        self.models["win"] = xgb.XGBClassifier(
            **xgb_base, num_class=3,
            objective="multi:softprob", eval_metric="mlogloss",
        )
        y_win = df["Result"].map({"A": 0, "D": 1, "H": 2})
        self.models["win"].fit(X, y_win)

        # Calibrate probabilities using isotonic regression (fixes overconfidence).
        # cv="prefit" was removed in sklearn 1.6; use cv=5 for cross-validated calibration.
        try:
            from sklearn.calibration import CalibratedClassifierCV
            if len(X) >= 200:  # need enough data for calibration
                _cal = CalibratedClassifierCV(
                    xgb.XGBClassifier(**xgb_base, num_class=3,
                                      objective="multi:softprob", eval_metric="mlogloss"),
                    method="isotonic", cv=3,
                )
                _cal.fit(X, y_win)
                self.models["win"] = _cal
                print("[Predictor] Win model calibrated with 3-fold isotonic regression")
        except Exception as _e:
            print(f"[Predictor] Calibration failed (using raw XGBoost): {_e}")

        bin_params = {**xgb_base, "objective": "binary:logistic", "eval_metric": "logloss"}
        self.models["o15"] = xgb.XGBClassifier(**bin_params)
        self.models["o15"].fit(X, (df["TotalGoals"] >= 2).astype(int))

        self.models["o25"] = xgb.XGBClassifier(**bin_params)
        self.models["o25"].fit(X, (df["TotalGoals"] >= 3).astype(int))

        self._ready = True

    def predict_match(self, home: str, away: str,
                      odds_home: float = 0, odds_draw: float = 0,
                      odds_away: float = 0,
                      match_date: str = None) -> Optional[Dict]:
        if not self._ready:
            return None
        f = self._feats(home, away, odds_home, odds_draw, odds_away, match_date=match_date)
        X = pd.DataFrame([f])[FEATURE_COLS]

        wp = self.models["win"].predict_proba(X)[0]
        p_a, p_d, p_h = float(wp[0]), float(wp[1]), float(wp[2])
        p_o15 = float(self.models["o15"].predict_proba(X)[0][1])
        p_o25 = float(self.models["o25"].predict_proba(X)[0][1])
        p_u25 = 1.0 - p_o25

        # 1x2 tip
        if p_h > 0.55:
            tip1x2, code = "Home Win", "1"
        elif p_a > 0.55:
            tip1x2, code = "Away Win", "2"
        elif p_d > 0.33:
            tip1x2, code = "Draw", "X"
        elif p_h + p_d > 0.75:
            tip1x2, code = "Home or Draw", "1X"
        elif p_a + p_d > 0.75:
            tip1x2, code = "Away or Draw", "2X"
        else:
            tip1x2, code = "Skip", "?"

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
            "p_home": round(p_h, 3),
            "p_draw": round(p_d, 3),
            "p_away": round(p_a, 3),
            "p_over15": round(p_o15, 3),
            "p_over25": round(p_o25, 3),
            "tip_1x2": tip1x2,
            "tip_code": code,
            "tip_goals": tip_g,
            "goals_type": gtype,
            "goals_confidence": round(gconf, 3),
        }

    def predict_match_full(self, home: str, away: str,
                           odds_home: float = 0, odds_draw: float = 0,
                           odds_away: float = 0) -> Optional[Dict]:
        """Full multi-market analysis using XGBoost + Poisson distribution."""
        if not self._ready:
            return None

        f = self._feats(home, away, odds_home, odds_draw, odds_away)
        X = pd.DataFrame([f])[FEATURE_COLS]

        # XGBoost probabilities
        wp = self.models["win"].predict_proba(X)[0]
        p_a, p_d, p_h = float(wp[0]), float(wp[1]), float(wp[2])
        p_o15 = float(self.models["o15"].predict_proba(X)[0][1])
        p_o25 = float(self.models["o25"].predict_proba(X)[0][1])

        # Dixon-Coles expected goals (venue-adjusted attack vs defense)
        xg_h = f["xG_Home"]
        xg_a = f["xG_Away"]

        # --- Poisson joint probability matrix ---
        MAX = 9
        from math import exp, factorial
        def pmf(k, lam): return (lam**k * exp(-lam)) / factorial(k)

        joint = np.array([[pmf(i, xg_h) * pmf(j, xg_a) for j in range(MAX)] for i in range(MAX)])

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
        elo_h = self.elo.get(home)
        elo_a = self.elo.get(away)
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
    ) -> Dict:
        """
        Predict corners and cards markets using team averages from fbref data.
        Returns two market dicts ready to append to the markets list.
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
        if not cards_df.empty:
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

        return result
