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


class EloSystem:
    K = 32
    HOME_ADV = 80  # Elo points added for home advantage

    def __init__(self):
        self.ratings: Dict[str, float] = {}

    def get(self, team: str) -> float:
        return self.ratings.get(team, 1500.0)

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
    "Atk_vs_Def",   # home attack - away defence
    "Def_vs_Atk",   # away attack - home defence
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

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _init(self, team: str):
        if team not in self.team_stats:
            self.team_stats[team] = {"gf": [], "ga": [], "pts": []}

    def _feats(self, home: str, away: str) -> Dict:
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

        return {
            "HomeElo": h_elo, "AwayElo": a_elo, "EloDiff": h_elo - a_elo,
            "Home_G_Avg": h_gf, "Away_G_Avg": a_gf,
            "Home_GA_Avg": h_ga, "Away_GA_Avg": a_ga,
            "Home_Form": h_form, "Away_Form": a_form,
            "Home_G_Var": h_var, "Away_G_Var": a_var,
            "Atk_vs_Def": h_gf - a_ga,
            "Def_vs_Atk": a_gf - h_ga,
        }

    def _update(self, home: str, away: str, result: str, fthg: float, ftag: float):
        self._init(home)
        self._init(away)
        self.team_stats[home]["gf"].append(fthg)
        self.team_stats[home]["ga"].append(ftag)
        self.team_stats[away]["gf"].append(ftag)
        self.team_stats[away]["ga"].append(fthg)
        pts = {"H": (3, 0), "D": (1, 1), "A": (0, 3)}[result]
        self.team_stats[home]["pts"].append(pts[0])
        self.team_stats[away]["pts"].append(pts[1])
        self.elo.update(home, away, result)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def train(self, matches: pd.DataFrame):
        """
        Build features + train XGBoost models.
        matches columns: HomeTeam, AwayTeam, Result (H/D/A), FTHG, FTAG — sorted ascending by date.
        """
        self.elo = EloSystem()
        self.team_stats = {}

        rows = []
        for _, r in matches.iterrows():
            f = self._feats(r["HomeTeam"], r["AwayTeam"])
            f["Result"] = r["Result"]
            f["TotalGoals"] = r["FTHG"] + r["FTAG"]
            rows.append(f)
            self._update(r["HomeTeam"], r["AwayTeam"], r["Result"], r["FTHG"], r["FTAG"])

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

        bin_params = {**xgb_base, "objective": "binary:logistic", "eval_metric": "logloss"}
        self.models["o15"] = xgb.XGBClassifier(**bin_params)
        self.models["o15"].fit(X, (df["TotalGoals"] >= 2).astype(int))

        self.models["o25"] = xgb.XGBClassifier(**bin_params)
        self.models["o25"].fit(X, (df["TotalGoals"] >= 3).astype(int))

        self._ready = True

    def predict_match(self, home: str, away: str) -> Optional[Dict]:
        if not self._ready:
            return None
        f = self._feats(home, away)
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

    def predict_match_full(self, home: str, away: str) -> Optional[Dict]:
        """Full multi-market analysis using XGBoost + Poisson distribution."""
        if not self._ready:
            return None

        f = self._feats(home, away)
        X = pd.DataFrame([f])[FEATURE_COLS]

        # XGBoost probabilities
        wp = self.models["win"].predict_proba(X)[0]
        p_a, p_d, p_h = float(wp[0]), float(wp[1]), float(wp[2])
        p_o15 = float(self.models["o15"].predict_proba(X)[0][1])
        p_o25 = float(self.models["o25"].predict_proba(X)[0][1])

        # Expected goals from features (used for Poisson)
        xg_h = max(0.1, f["Home_G_Avg"])
        xg_a = max(0.1, f["Away_G_Avg"])

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

    def predict_corners_cards(
        self,
        home: str,
        away: str,
        corners_df: pd.DataFrame,
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

        # ── Corners ────────────────────────────────────────────────────────
        if not corners_df.empty:
            def get_corners(team, is_home):
                row = corners_df[corners_df["team"].str.lower() == team.lower()]
                if row.empty:
                    return (5.0, 4.5)  # league avg defaults
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

            h_cf, h_ca = get_corners(home, is_home=True)
            a_cf, a_ca = get_corners(away, is_home=False)

            # Expected total corners: blend team for/against
            exp_h = (h_cf + a_ca) / 2
            exp_a = (a_cf + h_ca) / 2
            exp_total = exp_h + exp_a

            corners_market = {
                "id": "corners",
                "name": "Total Corners",
                "options": [
                    {"label": "Over 7.5",  "code": "CRN-O75",  "prob": p_over_poisson(exp_total, 7)},
                    {"label": "Over 8.5",  "code": "CRN-O85",  "prob": p_over_poisson(exp_total, 8)},
                    {"label": "Over 9.5",  "code": "CRN-O95",  "prob": p_over_poisson(exp_total, 9)},
                    {"label": "Over 10.5", "code": "CRN-O105", "prob": p_over_poisson(exp_total, 10)},
                    {"label": "Under 8.5", "code": "CRN-U85",  "prob": round(1 - p_over_poisson(exp_total, 8), 3)},
                    {"label": "Under 9.5", "code": "CRN-U95",  "prob": round(1 - p_over_poisson(exp_total, 9), 3)},
                ],
            }
            result["corners"] = corners_market

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
