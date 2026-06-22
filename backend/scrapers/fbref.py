"""
Scrapes team-level corners and cards averages from fbref.com.
Run this script directly to refresh the cached CSVs:
    python -m scrapers.fbref

Outputs:
  backend/data/corners_history.csv
  backend/data/cards_history.csv
"""

import asyncio
import httpx
import pandas as pd
import os
import re
from typing import Optional

# fbref league IDs and names we care about
FBREF_LEAGUES = {
    "PL":  {"id": "9",  "name": "Premier-League"},
    "PD":  {"id": "12", "name": "La-Liga"},
    "BL1": {"id": "20", "name": "Bundesliga"},
    "SA":  {"id": "11", "name": "Serie-A"},
    "FL1": {"id": "13", "name": "Ligue-1"},
    "CL":  {"id": "8",  "name": "Champions-League"},
}

SEASONS = ["2022-2023", "2023-2024", "2024-2025"]

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
CORNERS_CSV = os.path.join(DATA_DIR, "corners_history.csv")
CARDS_CSV   = os.path.join(DATA_DIR, "cards_history.csv")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


async def _fetch(client: httpx.AsyncClient, url: str) -> Optional[str]:
    try:
        r = await client.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
        if r.status_code == 200:
            return r.text
        print(f"[fbref] {r.status_code} for {url}")
        return None
    except Exception as e:
        print(f"[fbref] Error fetching {url}: {e}")
        return None


def _parse_schedule(html: str, league_code: str, season: str) -> list[dict]:
    """Parse the Scores & Fixtures table from fbref HTML."""
    rows = []
    try:
        # Find all table rows with match data
        # Pattern: date, home, score, away, corners_h, corners_a
        # fbref fixture tables use <td data-stat="..."> elements
        from html.parser import HTMLParser

        class FbrefParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_table = False
                self.current_row = {}
                self.current_stat = None
                self.rows = []
                self.depth = 0

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "tr":
                    self.current_row = {}
                if tag == "td":
                    self.current_stat = attrs.get("data-stat")

            def handle_endtag(self, tag):
                if tag == "tr":
                    r = self.current_row
                    # Only keep rows that have both home and away corners
                    if (r.get("date") and r.get("home_team") and r.get("away_team")
                            and r.get("home_goals") is not None
                            and r.get("away_goals") is not None):
                        self.rows.append(dict(r))
                    self.current_row = {}
                if tag == "td":
                    self.current_stat = None

            def handle_data(self, data):
                data = data.strip()
                if not data or not self.current_stat:
                    return
                stat = self.current_stat
                r = self.current_row
                if stat == "date":
                    r["date"] = data
                elif stat == "home_team":
                    r["home_team"] = data
                elif stat == "away_team":
                    r["away_team"] = data
                elif stat == "score":
                    parts = re.findall(r"\d+", data)
                    if len(parts) >= 2:
                        r["home_goals"] = int(parts[0])
                        r["away_goals"] = int(parts[1])
                elif stat == "home_goals":
                    try:
                        r["home_corners"] = int(data)
                    except ValueError:
                        pass
                elif stat == "away_goals":
                    try:
                        r["away_corners"] = int(data)
                    except ValueError:
                        pass
                # Cards from "referee" stats tables aren't in schedule
                # We parse these from team match logs separately

        # Actually fbref schedule page has a different structure.
        # Use pandas read_html which handles the HTML tables well.
        import io
        dfs = pd.read_html(io.StringIO(html), attrs={"id": re.compile(r"sched")})
        if not dfs:
            return []
        df = dfs[0]
        df.columns = [c[1] if isinstance(c, tuple) else c for c in df.columns]
        df = df.dropna(subset=["Home", "Away"])

        corner_cols = [c for c in df.columns if "corner" in c.lower() or "Corner" in c]
        card_cols   = [c for c in df.columns if "card" in c.lower() or "Card" in c or "Yellow" in c or "Red" in c]

        for _, row in df.iterrows():
            try:
                score_str = str(row.get("Score", ""))
                goals = re.findall(r"\d+", score_str)
                if len(goals) < 2:
                    continue
                record = {
                    "date": str(row.get("Date", "")),
                    "league": league_code,
                    "season": season,
                    "home_team": str(row.get("Home", "")).strip(),
                    "away_team": str(row.get("Away", "")).strip(),
                    "home_goals": int(goals[0]),
                    "away_goals": int(goals[1]),
                }
                # Corners
                if len(corner_cols) >= 2:
                    try:
                        record["home_corners"] = float(row[corner_cols[0]])
                        record["away_corners"] = float(row[corner_cols[1]])
                    except Exception:
                        pass
                # Cards (look for yellow card columns)
                yc = [c for c in card_cols if "Yellow" in c or "yellow" in c]
                rc = [c for c in card_cols if "Red" in c or "red" in c]
                if len(yc) >= 2:
                    try:
                        record["home_yellow"] = float(row[yc[0]])
                        record["away_yellow"] = float(row[yc[1]])
                    except Exception:
                        pass
                if len(rc) >= 2:
                    try:
                        record["home_red"] = float(row[rc[0]])
                        record["away_red"] = float(row[rc[1]])
                    except Exception:
                        pass

                rows.append(record)
            except Exception:
                continue

    except Exception as e:
        print(f"[fbref] Parse error: {e}")

    return rows


async def scrape_league_season(
    client: httpx.AsyncClient, league_code: str, season: str
) -> list[dict]:
    info = FBREF_LEAGUES.get(league_code)
    if not info:
        return []

    url = (
        f"https://fbref.com/en/comps/{info['id']}/{season}/schedule/"
        f"{season}-{info['name']}-Scores-and-Fixtures"
    )
    html = await _fetch(client, url)
    if not html:
        return []

    rows = _parse_schedule(html, league_code, season)
    print(f"[fbref] {league_code} {season}: {len(rows)} matches")
    await asyncio.sleep(4)  # be respectful
    return rows


async def scrape_all() -> pd.DataFrame:
    os.makedirs(DATA_DIR, exist_ok=True)
    all_rows = []

    async with httpx.AsyncClient() as client:
        for league_code in FBREF_LEAGUES:
            for season in SEASONS:
                rows = await scrape_league_season(client, league_code, season)
                all_rows.extend(rows)
                await asyncio.sleep(3)

    return pd.DataFrame(all_rows)


def build_team_averages(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    From raw match rows, compute per-team averages for corners and cards.
    Returns (corners_df, cards_df) with columns:
      team, league, home_corners_avg, away_corners_avg, ...
    """
    corners_rows = []
    cards_rows = []

    has_corners = "home_corners" in df.columns and "away_corners" in df.columns
    has_cards   = "home_yellow" in df.columns and "away_yellow" in df.columns

    for team in set(df["home_team"].dropna()) | set(df["away_team"].dropna()):
        home_matches = df[df["home_team"] == team]
        away_matches = df[df["away_team"] == team]

        if has_corners:
            hc = home_matches["home_corners"].dropna()
            ac = away_matches["away_corners"].dropna()
            hca = home_matches["away_corners"].dropna()  # corners conceded at home
            aca = away_matches["home_corners"].dropna()  # corners conceded away
            if len(hc) + len(ac) > 0:
                corners_rows.append({
                    "team": team,
                    "home_corners_for":     round(hc.mean(), 2) if len(hc) > 0 else 5.0,
                    "home_corners_against": round(hca.mean(), 2) if len(hca) > 0 else 5.0,
                    "away_corners_for":     round(ac.mean(), 2) if len(ac) > 0 else 4.5,
                    "away_corners_against": round(aca.mean(), 2) if len(aca) > 0 else 4.5,
                    "matches": len(hc) + len(ac),
                })

        if has_cards:
            hy = home_matches["home_yellow"].fillna(0)
            ay = away_matches["away_yellow"].fillna(0)
            hr = home_matches.get("home_red", pd.Series(dtype=float)).fillna(0)
            ar = away_matches.get("away_red", pd.Series(dtype=float)).fillna(0)
            ahyc = home_matches["away_yellow"].fillna(0)  # opponent cards at home
            aayc = away_matches["home_yellow"].fillna(0)  # opponent cards away
            if len(hy) + len(ay) > 0:
                cards_rows.append({
                    "team": team,
                    "home_cards_for":     round((hy + hr * 2).mean(), 2) if len(hy) > 0 else 1.5,
                    "away_cards_for":     round((ay + ar * 2).mean(), 2) if len(ay) > 0 else 1.8,
                    "home_cards_against": round(ahyc.mean(), 2) if len(ahyc) > 0 else 1.5,
                    "away_cards_against": round(aayc.mean(), 2) if len(aayc) > 0 else 1.8,
                    "matches": len(hy) + len(ay),
                })

    return pd.DataFrame(corners_rows), pd.DataFrame(cards_rows)


def save_csvs(corners_df: pd.DataFrame, cards_df: pd.DataFrame):
    os.makedirs(DATA_DIR, exist_ok=True)
    if not corners_df.empty:
        corners_df.to_csv(CORNERS_CSV, index=False)
        print(f"[fbref] Saved corners data for {len(corners_df)} teams → {CORNERS_CSV}")
    if not cards_df.empty:
        cards_df.to_csv(CARDS_CSV, index=False)
        print(f"[fbref] Saved cards data for {len(cards_df)} teams → {CARDS_CSV}")


def load_corners() -> pd.DataFrame:
    if os.path.exists(CORNERS_CSV):
        return pd.read_csv(CORNERS_CSV)
    return pd.DataFrame()


def load_cards() -> pd.DataFrame:
    if os.path.exists(CARDS_CSV):
        return pd.read_csv(CARDS_CSV)
    return pd.DataFrame()


async def refresh():
    print("[fbref] Starting scrape...")
    raw = await scrape_all()
    if raw.empty:
        print("[fbref] No data scraped.")
        return
    corners_df, cards_df = build_team_averages(raw)
    save_csvs(corners_df, cards_df)
    print(f"[fbref] Done. Corners: {len(corners_df)} teams, Cards: {len(cards_df)} teams")


if __name__ == "__main__":
    asyncio.run(refresh())
