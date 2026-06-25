"""
Basketball Elo predictor.
Trained on Kaggle NBA game data (or any CSV with game results).
Blended with The Odds API implied odds for final predictions.

Expected CSV columns (flexible — will auto-detect):
  - game_date / date
  - team_id_home / home_team / home
  - team_id_away / away_team / away
  - wl_home (W/L) OR pts_home + pts_away
  - pts_home, pts_away (optional, for over/under calibration)
"""

import os
import json
import math
import pandas as pd
from datetime import datetime, date
from difflib import SequenceMatcher
from typing import Dict, Optional, Tuple

DATA_DIR    = os.path.join(os.path.dirname(__file__), "data")
BBALL_CSV   = os.path.join(DATA_DIR, "basketball_games.csv")
ELO_FILE    = os.path.join(DATA_DIR, "basketball_elo.json")

HOME_ADVANTAGE = 50   # Elo points home court gives
K_FACTOR       = 20   # Elo update step
BLEND_WEIGHT   = 0.35 # How much Elo blends into implied odds (0=pure market, 1=pure Elo)


class BasketballElo:
    def __init__(self):
        self.ratings: Dict[str, float] = {}
        self.games_played: Dict[str, int] = {}
        self.avg_pts: Dict[str, float] = {}  # for over/under calibration

    def _rating(self, team: str) -> float:
        return self.ratings.get(team, 1500.0)

    def _expected(self, ra: float, rb: float) -> float:
        return 1 / (1 + 10 ** ((rb - ra) / 400))

    def _update(self, home: str, away: str, home_won: bool,
                home_pts: float = 0, away_pts: float = 0):
        rh = self._rating(home) + HOME_ADVANTAGE
        ra = self._rating(away)
        eh = self._expected(rh, ra)

        # Margin of victory multiplier (like NBA Elo)
        if home_pts and away_pts:
            mov = abs(home_pts - away_pts)
            mov_mult = math.log(max(mov, 1) + 1) * (2.2 / ((abs(rh - ra) * 0.001 + 2.2)))
        else:
            mov_mult = 1.0

        score = 1.0 if home_won else 0.0
        k = K_FACTOR * mov_mult

        self.ratings[home] = self._rating(home) + k * (score - eh)
        self.ratings[away] = self._rating(away) + k * ((1 - score) - (1 - eh))
        self.games_played[home] = self.games_played.get(home, 0) + 1
        self.games_played[away] = self.games_played.get(away, 0) + 1

        if home_pts and away_pts:
            total = home_pts + away_pts
            self.avg_pts[home] = (self.avg_pts.get(home, 0) * (self.games_played[home] - 1) + home_pts) / self.games_played[home]
            self.avg_pts[away] = (self.avg_pts.get(away, 0) * (self.games_played.get(away, 1) - 1) + away_pts) / max(self.games_played.get(away, 1), 1)

    def predict(self, home: str, away: str) -> Optional[Dict]:
        """
        Predict home/away win probability using Elo ratings.
        Returns None if we have no data for either team.
        """
        rh = self._rating(home) + HOME_ADVANTAGE
        ra = self._rating(away)
        p_home = self._expected(rh, ra)
        p_away = 1 - p_home

        avg_total = (self.avg_pts.get(home, 0) + self.avg_pts.get(away, 0)) * 0.9
        total_line = round(avg_total / 5) * 5 if avg_total > 0 else None

        return {
            "p_home": round(p_home, 3),
            "p_away": round(p_away, 3),
            "elo_home": round(self.ratings.get(home, 1500)),
            "elo_away": round(self.ratings.get(away, 1500)),
            "total_line": total_line,
            "games_home": self.games_played.get(home, 0),
            "games_away": self.games_played.get(away, 0),
        }

    def save(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(ELO_FILE, "w") as f:
            json.dump({
                "ratings": self.ratings,
                "games_played": self.games_played,
                "avg_pts": self.avg_pts,
            }, f)

    def load(self) -> bool:
        if not os.path.exists(ELO_FILE):
            return False
        try:
            with open(ELO_FILE) as f:
                d = json.load(f)
            self.ratings      = d.get("ratings", {})
            self.games_played = d.get("games_played", {})
            self.avg_pts      = d.get("avg_pts", {})
            print(f"[Basketball] Loaded Elo for {len(self.ratings)} teams")
            return True
        except Exception as e:
            print(f"[Basketball] Elo load error: {e}")
            return False


def _detect_columns(df: pd.DataFrame) -> Dict[str, str]:
    """Auto-detect column names regardless of CSV format."""
    cols = {c.lower().strip(): c for c in df.columns}

    def pick(*candidates):
        for c in candidates:
            if c in cols:
                return cols[c]
        return None

    return {
        "date":      pick("game_date", "date", "game_datetime", "gamedate"),
        "home":      pick("team_name_home", "home_team", "home", "team_id_home", "hometeam"),
        "away":      pick("team_name_away", "away_team", "away", "team_id_away", "awayteam"),
        "wl_home":   pick("wl_home", "result_home", "home_result"),
        "pts_home":  pick("pts_home", "home_pts", "home_score", "pts_home", "score_home"),
        "pts_away":  pick("pts_away", "away_pts", "away_score", "pts_away", "score_away"),
    }


def train_from_csv(csv_path: str = BBALL_CSV) -> Optional[BasketballElo]:
    """Load a basketball games CSV and train Elo ratings from scratch."""
    if not os.path.exists(csv_path):
        print(f"[Basketball] CSV not found: {csv_path}")
        return None

    try:
        df = pd.read_csv(csv_path, low_memory=False)
        mapping = _detect_columns(df)
        print(f"[Basketball] CSV columns detected: {mapping}")

        if not mapping["home"] or not mapping["away"]:
            print("[Basketball] Could not detect home/away team columns")
            return None

        # Sort by date if available
        if mapping["date"]:
            try:
                df[mapping["date"]] = pd.to_datetime(df[mapping["date"]], errors="coerce")
                df = df.dropna(subset=[mapping["date"]]).sort_values(mapping["date"])
            except Exception:
                pass

        elo = BasketballElo()
        trained = 0

        for _, row in df.iterrows():
            home = str(row[mapping["home"]]).strip()
            away = str(row[mapping["away"]]).strip()
            if not home or not away or home == away:
                continue

            # Determine winner
            home_won = None
            if mapping["wl_home"]:
                wl = str(row[mapping["wl_home"]]).strip().upper()
                if wl in ("W", "WIN", "1"):
                    home_won = True
                elif wl in ("L", "LOSS", "0"):
                    home_won = False
            elif mapping["pts_home"] and mapping["pts_away"]:
                try:
                    ph = float(row[mapping["pts_home"]])
                    pa = float(row[mapping["pts_away"]])
                    home_won = ph > pa
                except Exception:
                    pass

            if home_won is None:
                continue

            home_pts, away_pts = 0.0, 0.0
            if mapping["pts_home"] and mapping["pts_away"]:
                try:
                    home_pts = float(row[mapping["pts_home"]])
                    away_pts = float(row[mapping["pts_away"]])
                except Exception:
                    pass

            elo._update(home, away, home_won, home_pts, away_pts)
            trained += 1

        print(f"[Basketball] Trained Elo on {trained} games, {len(elo.ratings)} teams")
        elo.save()
        return elo

    except Exception as e:
        print(f"[Basketball] Training error: {e}")
        import traceback; traceback.print_exc()
        return None


def blend_with_market(elo_pred: Dict, market_p_home: float, market_p_away: float) -> Dict:
    """
    Blend Elo model probabilities with bookmaker implied odds.
    Weight: BLEND_WEIGHT for Elo, (1-BLEND_WEIGHT) for market.
    Only blends if we have enough games for both teams.
    """
    min_games = min(elo_pred.get("games_home", 0), elo_pred.get("games_away", 0))
    if min_games < 10:
        # Not enough data — trust market more
        w = BLEND_WEIGHT * (min_games / 10)
    else:
        w = BLEND_WEIGHT

    blended_home = w * elo_pred["p_home"] + (1 - w) * market_p_home
    blended_away = w * elo_pred["p_away"] + (1 - w) * market_p_away
    total = blended_home + blended_away
    return {
        "p_home": round(blended_home / total, 3),
        "p_away": round(blended_away / total, 3),
        "elo_home": elo_pred["elo_home"],
        "elo_away": elo_pred["elo_away"],
        "blend_weight": round(w, 2),
        "games_home": elo_pred.get("games_home", 0),
        "games_away": elo_pred.get("games_away", 0),
    }


# Singleton
_bball_elo: Optional[BasketballElo] = None

def get_basketball_elo() -> Optional[BasketballElo]:
    global _bball_elo
    if _bball_elo is None:
        elo = BasketballElo()
        if not elo.load():
            elo = train_from_csv()
        _bball_elo = elo
    return _bball_elo
