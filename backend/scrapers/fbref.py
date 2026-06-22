"""
Downloads historical match data WITH corners and cards from football-data.co.uk.
Free, no scraping — plain CSV downloads updated each season.

Run to refresh:
    python -m scrapers.fbref

Outputs:
  backend/data/corners_history.csv  — per-team rolling averages
  backend/data/cards_history.csv    — per-team rolling averages
  backend/data/matches_extras.csv   — raw match-level data for model training
"""

import asyncio
import httpx
import pandas as pd
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
CORNERS_CSV = os.path.join(DATA_DIR, "corners_history.csv")
CARDS_CSV   = os.path.join(DATA_DIR, "cards_history.csv")
EXTRAS_CSV  = os.path.join(DATA_DIR, "matches_extras.csv")

# football-data.co.uk league codes and seasons to fetch
# Format: mmz4281/{season_code}/{league_code}.csv
LEAGUES = {
    "E0":  "PL",   # Premier League
    "SP1": "PD",   # La Liga
    "D1":  "BL1",  # Bundesliga
    "I1":  "SA",   # Serie A
    "F1":  "FL1",  # Ligue 1
}

SEASON_CODES = ["2223", "2324", "2425"]  # 2022-23, 2023-24, 2024-25

BASE_URL = "https://www.football-data.co.uk/mmz4281"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; BetIQ/1.0)"
}

# Column name mappings from football-data.co.uk format
COL_MAP = {
    "HomeTeam": "home_team",
    "AwayTeam":  "away_team",
    "FTHG":      "home_goals",
    "FTAG":      "away_goals",
    "FTR":       "result",
    "HC":        "home_corners",
    "AC":        "away_corners",
    "HY":        "home_yellow",
    "AY":        "away_yellow",
    "HR":        "home_red",
    "AR":        "away_red",
}


async def _fetch_csv(client: httpx.AsyncClient, url: str) -> pd.DataFrame:
    try:
        r = await client.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
        if r.status_code != 200:
            print(f"[fetcher] {r.status_code} → {url}")
            return pd.DataFrame()
        from io import StringIO
        df = pd.read_csv(StringIO(r.text), on_bad_lines="skip")
        return df
    except Exception as e:
        print(f"[fetcher] Error {url}: {e}")
        return pd.DataFrame()


async def download_all() -> pd.DataFrame:
    all_rows = []

    async with httpx.AsyncClient(verify=False) as client:
        for season in SEASON_CODES:
            for fd_code, our_code in LEAGUES.items():
                url = f"{BASE_URL}/{season}/{fd_code}.csv"
                raw = await _fetch_csv(client, url)
                if raw.empty:
                    continue

                # Normalise columns
                present = {k: v for k, v in COL_MAP.items() if k in raw.columns}
                df = raw.rename(columns=present)

                # Parse date
                if "Date" in raw.columns:
                    df["date"] = pd.to_datetime(raw["Date"], dayfirst=True, errors="coerce")
                else:
                    df["date"] = pd.NaT

                df["league"] = our_code
                df["season"] = season

                keep = ["date", "league", "season"] + list(present.values())
                keep = [c for c in keep if c in df.columns]
                df = df[keep].dropna(subset=["home_team", "away_team", "home_goals", "away_goals"])

                all_rows.append(df)
                print(f"[fetcher] {our_code} {season}: {len(df)} matches")
                await asyncio.sleep(1)

    if not all_rows:
        return pd.DataFrame()

    combined = pd.concat(all_rows, ignore_index=True)
    combined = combined.sort_values("date").reset_index(drop=True)
    return combined


def build_team_averages(df: pd.DataFrame):
    """
    Compute per-team season averages for corners and cards.
    Returns (corners_df, cards_df).
    """
    has_corners = "home_corners" in df.columns and "away_corners" in df.columns
    has_cards   = "home_yellow" in df.columns and "away_yellow" in df.columns

    corners_rows, cards_rows = [], []
    teams = set(df["home_team"].dropna()) | set(df["away_team"].dropna())

    for team in teams:
        hm = df[df["home_team"] == team]
        aw = df[df["away_team"] == team]

        if has_corners:
            hcf = hm["home_corners"].dropna()
            hca = hm["away_corners"].dropna()
            acf = aw["away_corners"].dropna()
            aca = aw["home_corners"].dropna()
            n = len(hcf) + len(acf)
            if n > 0:
                corners_rows.append({
                    "team":                 team,
                    "home_corners_for":     round(hcf.mean(), 2) if len(hcf) else 5.0,
                    "home_corners_against": round(hca.mean(), 2) if len(hca) else 4.5,
                    "away_corners_for":     round(acf.mean(), 2) if len(acf) else 4.5,
                    "away_corners_against": round(aca.mean(), 2) if len(aca) else 5.0,
                    "matches": n,
                })

        if has_cards:
            hy = hm["home_yellow"].fillna(0)
            hr = hm.get("home_red", pd.Series(dtype=float)).fillna(0)
            ay = aw["away_yellow"].fillna(0)
            ar = aw.get("away_red", pd.Series(dtype=float)).fillna(0)
            ohy = hm["away_yellow"].fillna(0)
            oay = aw["home_yellow"].fillna(0)
            n = len(hy) + len(ay)
            if n > 0:
                cards_rows.append({
                    "team":               team,
                    "home_cards_for":     round((hy + hr * 2).mean(), 2) if len(hy) else 1.5,
                    "away_cards_for":     round((ay + ar * 2).mean(), 2) if len(ay) else 1.8,
                    "home_cards_against": round(ohy.mean(), 2) if len(ohy) else 1.5,
                    "away_cards_against": round(oay.mean(), 2) if len(oay) else 1.8,
                    "matches": n,
                })

    return pd.DataFrame(corners_rows), pd.DataFrame(cards_rows)


def load_corners() -> pd.DataFrame:
    if os.path.exists(CORNERS_CSV):
        return pd.read_csv(CORNERS_CSV)
    return pd.DataFrame()


def load_cards() -> pd.DataFrame:
    if os.path.exists(CARDS_CSV):
        return pd.read_csv(CARDS_CSV)
    return pd.DataFrame()


def load_extras() -> pd.DataFrame:
    """Load raw match extras (corners + cards per match) for model feature building."""
    if os.path.exists(EXTRAS_CSV):
        df = pd.read_csv(EXTRAS_CSV, parse_dates=["date"])
        return df
    return pd.DataFrame()


def build_from_epl_csv(epl_csv_path: str) -> pd.DataFrame:
    """
    Build extras DataFrame from the existing epl-final.csv which already has
    corners and cards columns. No downloading needed.
    """
    df = pd.read_csv(epl_csv_path)
    df["date"] = pd.to_datetime(df["MatchDate"], errors="coerce")
    df["league"] = "PL"
    df["season"] = df["Season"].astype(str)

    col_map = {
        "HomeTeam": "home_team",
        "AwayTeam": "away_team",
        "FullTimeHomeGoals": "home_goals",
        "FullTimeAwayGoals": "away_goals",
        "FullTimeResult": "result",
        "HomeCorners": "home_corners",
        "AwayCorners": "away_corners",
        "HomeYellowCards": "home_yellow",
        "AwayYellowCards": "away_yellow",
        "HomeRedCards": "home_red",
        "AwayRedCards": "away_red",
    }
    df = df.rename(columns=col_map)
    keep = ["date", "league", "season"] + [v for v in col_map.values() if v in df.columns]
    return df[[c for c in keep if c in df.columns]].dropna(subset=["home_team", "away_team"])


async def refresh(epl_csv_path: str = None):
    os.makedirs(DATA_DIR, exist_ok=True)

    # Primary: use existing EPL CSV (already has corners + cards)
    if epl_csv_path and os.path.exists(epl_csv_path):
        print("[fetcher] Building extras from existing EPL CSV...")
        raw = build_from_epl_csv(epl_csv_path)
        print(f"[fetcher] Loaded {len(raw)} EPL matches with corners/cards")
    else:
        # Fallback: try downloading from football-data.co.uk
        print("[fetcher] Downloading match data from football-data.co.uk...")
        raw = await download_all()

    if raw.empty:
        print("[fetcher] No data available.")
        return

    raw.to_csv(EXTRAS_CSV, index=False)
    print(f"[fetcher] Saved {len(raw)} matches → {EXTRAS_CSV}")

    corners_df, cards_df = build_team_averages(raw)
    if not corners_df.empty:
        corners_df.to_csv(CORNERS_CSV, index=False)
        print(f"[fetcher] Corners: {len(corners_df)} teams → {CORNERS_CSV}")
    if not cards_df.empty:
        cards_df.to_csv(CARDS_CSV, index=False)
        print(f"[fetcher] Cards: {len(cards_df)} teams → {CARDS_CSV}")


if __name__ == "__main__":
    import sys
    epl_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), "..", "..", "epl-final.csv"
    )
    asyncio.run(refresh(epl_csv_path=epl_path))
