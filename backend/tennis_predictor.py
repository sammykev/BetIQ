"""
Tennis Elo predictor — surface-specific ratings.
Trained on Jeff Sackmann's ATP/WTA match data (free on GitHub).

Data source:
  ATP: https://github.com/JeffSackmann/tennis_atp
  WTA: https://github.com/JeffSackmann/tennis_wta

Download these CSV files and place them in backend/data/tennis/:
  atp_matches_2015.csv ... atp_matches_2025.csv
  wta_matches_2015.csv ... wta_matches_2025.csv

Column format (auto-detected):
  tourney_date, surface, winner_name, loser_name, winner_rank, loser_rank, score
"""

import os, json, math, glob
import pandas as pd
from typing import Dict, Optional, Tuple

DATA_DIR    = os.path.join(os.path.dirname(__file__), "data", "tennis")
ELO_FILE    = os.path.join(os.path.dirname(__file__), "data", "tennis_elo.json")

SURFACES    = ["Hard", "Clay", "Grass", "Carpet"]
K_BASE      = 32       # Elo K factor
K_SLAM      = 40       # Grand Slam matches count more
BLEND       = 0.30     # 30% Elo, 70% market odds (increases as data grows)


class TennisElo:
    def __init__(self):
        # Per-surface ratings: {player_name: {surface: elo}}
        self.ratings:  Dict[str, Dict[str, float]] = {}
        self.matches:  Dict[str, int] = {}   # total matches per player

    def _r(self, player: str, surface: str) -> float:
        return self.ratings.get(player, {}).get(surface, 1500.0)

    def _expected(self, ra: float, rb: float) -> float:
        return 1 / (1 + 10 ** ((rb - ra) / 400))

    def _update(self, winner: str, loser: str, surface: str, is_slam: bool = False):
        rw = self._r(winner, surface)
        rl = self._r(loser, surface)
        ew = self._expected(rw, rl)
        k  = K_SLAM if is_slam else K_BASE

        if winner not in self.ratings:
            self.ratings[winner] = {}
        if loser not in self.ratings:
            self.ratings[loser] = {}

        self.ratings[winner][surface] = rw + k * (1 - ew)
        self.ratings[loser][surface]  = rl + k * (0 - (1 - ew))
        self.matches[winner] = self.matches.get(winner, 0) + 1
        self.matches[loser]  = self.matches.get(loser, 0) + 1

    def predict(self, p1: str, p2: str, surface: str = "Hard") -> Optional[Dict]:
        surf = surface if surface in SURFACES else "Hard"
        r1 = self._r(p1, surf)
        r2 = self._r(p2, surf)
        p1_win = self._expected(r1, r2)

        m1 = self.matches.get(p1, 0)
        m2 = self.matches.get(p2, 0)

        return {
            "p1_win":    round(p1_win, 3),
            "p2_win":    round(1 - p1_win, 3),
            "elo_p1":    round(r1),
            "elo_p2":    round(r2),
            "surface":   surf,
            "matches_p1": m1,
            "matches_p2": m2,
        }

    def save(self):
        os.makedirs(os.path.dirname(ELO_FILE), exist_ok=True)
        with open(ELO_FILE, "w") as f:
            json.dump({"ratings": self.ratings, "matches": self.matches}, f)
        print(f"[Tennis] Saved Elo for {len(self.ratings)} players")

    def load(self) -> bool:
        if not os.path.exists(ELO_FILE):
            return False
        try:
            with open(ELO_FILE) as f:
                d = json.load(f)
            self.ratings = d.get("ratings", {})
            self.matches = d.get("matches", {})
            print(f"[Tennis] Loaded Elo for {len(self.ratings)} players")
            return True
        except Exception as e:
            print(f"[Tennis] Load error: {e}")
            return False


SLAM_NAMES = {"australian open", "roland garros", "french open",
              "wimbledon", "us open"}

def train_tennis_elo(data_dir: str = DATA_DIR) -> Optional[TennisElo]:
    """
    Train surface-specific Elo from Jeff Sackmann CSV files.
    Expects CSVs named atp_matches_YYYY.csv / wta_matches_YYYY.csv in data_dir.
    """
    csvs = sorted(glob.glob(os.path.join(data_dir, "*.csv")))
    if not csvs:
        print(f"[Tennis] No CSVs found in {data_dir}")
        print("[Tennis] Download from:")
        print("  ATP: https://github.com/JeffSackmann/tennis_atp")
        print("  WTA: https://github.com/JeffSackmann/tennis_wta")
        print(f"  Save files to: {data_dir}")
        return None

    elo = TennisElo()
    total = 0

    for csv_path in csvs:
        try:
            df = pd.read_csv(csv_path, low_memory=False)
            cols = {c.lower(): c for c in df.columns}

            # Map column names
            winner_col  = cols.get("winner_name")
            loser_col   = cols.get("loser_name")
            surface_col = cols.get("surface")
            tourney_col = cols.get("tourney_name")
            date_col    = cols.get("tourney_date")

            if not winner_col or not loser_col:
                print(f"[Tennis] Skipping {csv_path} — missing winner/loser columns")
                continue

            # Sort by date if available
            if date_col:
                try:
                    df[date_col] = pd.to_datetime(df[date_col].astype(str), format="%Y%m%d", errors="coerce")
                    df = df.dropna(subset=[date_col]).sort_values(date_col)
                except Exception:
                    pass

            for _, row in df.iterrows():
                winner  = str(row[winner_col]).strip()
                loser   = str(row[loser_col]).strip()
                surface = str(row[surface_col]).strip().title() if surface_col else "Hard"
                is_slam = (tourney_col and
                           str(row[tourney_col]).lower().strip() in SLAM_NAMES)

                if not winner or not loser or winner == loser or winner == "nan":
                    continue

                if surface not in SURFACES:
                    surface = "Hard"

                elo._update(winner, loser, surface, is_slam)
                total += 1

        except Exception as e:
            print(f"[Tennis] Error reading {csv_path}: {e}")

    if total == 0:
        return None

    print(f"[Tennis] Trained on {total} matches, {len(elo.ratings)} players")
    elo.save()
    return elo


def blend_with_market(elo_pred: Dict, market_p1: float, market_p2: float) -> Dict:
    """Blend Elo with market odds. More Elo weight when player has many matches."""
    min_m = min(elo_pred.get("matches_p1", 0), elo_pred.get("matches_p2", 0))
    w = BLEND * min(min_m / 50, 1.0)   # full blend after 50 matches each

    blended_p1 = w * elo_pred["p1_win"] + (1 - w) * market_p1
    blended_p2 = w * elo_pred["p2_win"] + (1 - w) * market_p2
    total = blended_p1 + blended_p2

    return {
        "p1_win":     round(blended_p1 / total, 3),
        "p2_win":     round(blended_p2 / total, 3),
        "elo_p1":     elo_pred.get("elo_p1"),
        "elo_p2":     elo_pred.get("elo_p2"),
        "blend_weight": round(w, 2),
    }


# ── Singleton ──────────────────────────────────────────────────────────────
_tennis_elo: Optional[TennisElo] = None

def get_tennis_elo() -> Optional[TennisElo]:
    global _tennis_elo
    if _tennis_elo is None:
        elo = TennisElo()
        if not elo.load():
            elo = train_tennis_elo()
        _tennis_elo = elo
    return _tennis_elo
