"""
FastAPI backend for Sport Bet Predictions.
- Trains on historical EPL + UCL CSV data (for model accuracy)
- Fetches ALL upcoming fixtures live from football-data.org API
- Serves predictions via REST API
- Refreshes every 6 hours via APScheduler
"""

import asyncio
import os
import glob
import json
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from predictor import LeaguePredictor
from data_fetcher import FootballDataClient, LEAGUES
from scrapers.fbref import load_cards, refresh as scrape_fbref, CORNERS_CSV, CARDS_CSV

load_dotenv()

API_KEY = os.getenv("FOOTBALL_DATA_API_KEY", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
EPL_HISTORY = os.getenv("EPL_HISTORY_CSV", "../epl-final.csv")
UCL_CSV_PATTERN = os.getenv("UCL_CSV", "../champions-league-*.csv")
REDIS_URL = os.getenv("UPSTASH_REDIS_URL", "")
H2H_CACHE_FILE = "h2h_cache.json"
H2H_TTL_DAYS = 7
PREDICTIONS_CACHE_FILE = os.path.join("data", "predictions_cache.json")
RESULTS_CSV = os.path.join("data", "recent_results.csv")

# Redis client — only active when UPSTASH_REDIS_URL is set
_redis = None
def _get_redis():
    global _redis
    if _redis is not None:
        return _redis
    if not REDIS_URL:
        return None
    try:
        import redis as redis_lib
        _redis = redis_lib.from_url(REDIS_URL, decode_responses=True)
        _redis.ping()
        print("[Redis] Connected to Upstash Redis.")
        return _redis
    except Exception as e:
        print(f"[Redis] Could not connect: {e}")
        return None

app = FastAPI(title="Sport Bet Predictions API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:3000", "*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# --- Global state ---
_predictor: Optional[LeaguePredictor] = None
_predictions_cache: List[Dict] = []
_last_updated: Optional[str] = None
_is_training = False
_history_df: Optional[pd.DataFrame] = None
_cards_df: pd.DataFrame = pd.DataFrame()

# --- H2H cache (in-memory + file-backed) ---
_h2h_cache: Dict[str, Dict] = {}

def _load_h2h_cache():
    global _h2h_cache
    if os.path.exists(H2H_CACHE_FILE):
        try:
            with open(H2H_CACHE_FILE, "r") as f:
                _h2h_cache = json.load(f)
            print(f"[H2H] Loaded {len(_h2h_cache)} cached records from disk.")
        except Exception:
            _h2h_cache = {}

def _save_h2h_cache():
    try:
        with open(H2H_CACHE_FILE, "w") as f:
            json.dump(_h2h_cache, f)
    except Exception as e:
        print(f"[H2H] Cache save error: {e}")

def _h2h_cache_key(home: str, away: str) -> str:
    return f"{home.lower().strip()}__vs__{away.lower().strip()}"


_WEB_FORM_REDIS_PREFIX = "betiq:web_form:"
_WEB_FORM_TTL = 60 * 60 * 24  # 24 hours


def _redis_team_key(team: str) -> str:
    return _WEB_FORM_REDIS_PREFIX + team.lower().replace(" ", "_")


def _get_web_form_cache(team: str) -> Optional[Dict]:
    """Synchronous Redis lookup for pre-fetched web form data."""
    r = _get_redis()
    if not r:
        return None
    try:
        raw = r.get(_redis_team_key(team))
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _set_web_form_cache(team: str, form: Dict):
    r = _get_redis()
    if not r:
        return
    try:
        r.setex(_redis_team_key(team), _WEB_FORM_TTL, json.dumps(form))
    except Exception:
        pass


def _predictor_form_summary(team: str) -> Dict:
    """
    Return the current rolling form for a team.
    Primary source: predictor.team_stats (built from CSVs + API).
    Fallback: Redis-cached web form (fetched async in pipeline for sparse teams).
    """
    if _predictor is None:
        return {}

    stats = _predictor.team_stats.get(team, {})
    elo   = round(_predictor.elo.get(team))

    pts = stats.get("pts", [])[-10:]
    # Filter NaN values that can creep in from CSV rows with missing scores
    gf  = [v for v in stats.get("gf", [])[-10:] if v == v and v is not None]
    ga  = [v for v in stats.get("ga", [])[-10:] if v == v and v is not None]
    n   = len(pts)

    # ── Web form fallback (used when local data is sparse) ──────────────
    web = _get_web_form_cache(team) if n < 5 else None

    if n == 0:
        if web and web.get("matches"):
            # Build form from web search results
            matches  = web["matches"]
            wm       = [m for m in matches if m.get("result") == "W"]
            dm       = [m for m in matches if m.get("result") == "D"]
            lm       = [m for m in matches if m.get("result") == "L"]
            form_str = "".join(
                "W" if m.get("result") == "W" else ("D" if m.get("result") == "D" else "L")
                for m in matches[-5:]
            )
            return {
                "available":           True,
                "games":               len(matches),
                "form":                form_str,
                "wins":                len(wm),
                "draws":               len(dm),
                "losses":              len(lm),
                "goals_scored":        _safe_round(web.get("avg_scored")),
                "goals_conceded":      _safe_round(web.get("avg_conceded")),
                "home_goals_scored":   None,
                "home_goals_conceded": None,
                "away_goals_scored":   None,
                "away_goals_conceded": None,
                "xg_for":              _safe_round(web.get("avg_xg_for"), 2),
                "xg_against":          _safe_round(web.get("avg_xg_against"), 2),
                "elo":                 elo,
                "data_source":         "Live web search (last 10 matches)",
            }
        # No local OR web data — show Elo only
        return {
            "available":      True,
            "games":          0,
            "form":           "",
            "wins":           0,
            "draws":          0,
            "losses":         0,
            "goals_scored":   None,
            "goals_conceded": None,
            "xg_for":         None,
            "xg_against":     None,
            "elo":            elo,
            "data_source":    "Elo rating only — match stats loading",
        }

    wins   = sum(1 for p in pts if p == 3)
    draws  = sum(1 for p in pts if p == 1)
    losses = sum(1 for p in pts if p == 0)
    form_str = "".join("W" if p == 3 else ("D" if p == 1 else "L") for p in pts[-5:])

    # Venue-specific goal averages from Dixon-Coles tracking
    home_gf = [v for v in stats.get("home_gf", [])[-10:] if v == v and v is not None]
    home_ga = [v for v in stats.get("home_ga", [])[-10:] if v == v and v is not None]
    away_gf = [v for v in stats.get("away_gf", [])[-10:] if v == v and v is not None]
    away_ga = [v for v in stats.get("away_ga", [])[-10:] if v == v and v is not None]

    # Supplement with web xG if local data is sparse and web has it
    xg_for     = _safe_round(web.get("avg_xg_for"), 2) if web else None
    xg_against = _safe_round(web.get("avg_xg_against"), 2) if web else None

    # Supplement with Understat xG if web xG not available (European clubs)
    if xg_for is None:
        try:
            from understat_fetcher import get_cached_xg
            ustat = get_cached_xg(_get_redis(), team)
            if ustat:
                xg_for     = ustat.get("xg_for")
                xg_against = ustat.get("xg_against")
        except Exception:
            pass

    return {
        "available":           True,
        "games":               n,
        "form":                form_str,
        "wins":                wins,
        "draws":               draws,
        "losses":              losses,
        "goals_scored":        round(sum(gf) / n, 1) if gf else None,
        "goals_conceded":      round(sum(ga) / n, 1) if ga else None,
        "home_goals_scored":   round(sum(home_gf) / len(home_gf), 1) if home_gf else None,
        "home_goals_conceded": round(sum(home_ga) / len(home_ga), 1) if home_ga else None,
        "away_goals_scored":   round(sum(away_gf) / len(away_gf), 1) if away_gf else None,
        "away_goals_conceded": round(sum(away_ga) / len(away_ga), 1) if away_ga else None,
        "xg_for":              xg_for,
        "xg_against":          xg_against,
        "elo":                 elo,
        "data_source":         "football-data.org + CSVs" + (" + web xG" if xg_for else ""),
    }


def _safe_round(v, decimals: int = 1):
    """Round v if it's a valid finite number, else return None."""
    try:
        f = float(v)
        return round(f, decimals) if f == f else None  # NaN guard
    except (TypeError, ValueError):
        return None

def _load_predictions_cache():
    global _predictions_cache, _last_updated
    # 1. Try Redis (survives Render deploys)
    r = _get_redis()
    if r:
        try:
            raw = r.get("betiq:predictions")
            if raw:
                saved = json.loads(raw)
                _predictions_cache = saved.get("predictions", [])
                _last_updated = saved.get("last_updated")
                print(f"[Cache] Restored {len(_predictions_cache)} predictions from Redis.")
                _backfill_history_from_cache(r)
                return
        except Exception as e:
            print(f"[Cache] Redis load error: {e}")
    # 2. Fall back to local disk (works on localhost)
    if os.path.exists(PREDICTIONS_CACHE_FILE):
        try:
            with open(PREDICTIONS_CACHE_FILE) as f:
                saved = json.load(f)
            _predictions_cache = saved.get("predictions", [])
            _last_updated = saved.get("last_updated")
            print(f"[Cache] Restored {len(_predictions_cache)} predictions from disk.")
        except Exception as e:
            print(f"[Cache] Disk load error: {e}")


def _backfill_history_from_cache(r):
    """Write each date's predictions to betiq:history:{date} if missing — fills gaps from before the feature was deployed."""
    if not _predictions_cache:
        return
    from collections import defaultdict
    by_date: dict = defaultdict(list)
    for p in _predictions_cache:
        d = p.get("date", "")
        if d:
            by_date[d].append({**p, "outcome": "pending", "actual_result": None})
    filled = 0
    for d, preds in by_date.items():
        try:
            if not r.exists(f"betiq:history:{d}"):
                r.set(f"betiq:history:{d}", json.dumps(preds), ex=90 * 86400)
                filled += 1
        except Exception:
            pass
    if filled:
        print(f"[Cache] Backfilled {filled} date(s) into prediction history.")

def _save_predictions_cache():
    payload = json.dumps({"predictions": _predictions_cache, "last_updated": _last_updated})
    r = _get_redis()

    # 1. Save main predictions cache (TTL: 8 hours)
    if r:
        try:
            r.set("betiq:predictions", payload, ex=8 * 3600)
            print(f"[Cache] Saved {len(_predictions_cache)} predictions to Redis.")
        except Exception as e:
            print(f"[Cache] Redis save error: {e}")

    # 2. Persist each date's predictions to history keys NOW (as pending).
    #    This ensures history survives even if the match rolls off the upcoming cache.
    #    _archive_past_predictions() will later upgrade pending → won/lost via results CSV.
    if r and _predictions_cache:
        from collections import defaultdict
        by_date: dict = defaultdict(list)
        for p in _predictions_cache:
            d = p.get("date", "")
            if d:
                by_date[d].append({**p, "outcome": "pending", "actual_result": None})
        saved_dates = 0
        for d, preds in by_date.items():
            key = f"betiq:history:{d}"
            try:
                # Only write if no settled outcomes exist yet for this date
                existing = r.get(key)
                if not existing:
                    r.set(key, json.dumps(preds), ex=90 * 86400)  # 90-day TTL
                    saved_dates += 1
            except Exception:
                pass
        if saved_dates:
            print(f"[Cache] Persisted {saved_dates} new date(s) to prediction history.")

    # 3. Disk fallback
    try:
        os.makedirs("data", exist_ok=True)
        with open(PREDICTIONS_CACHE_FILE, "w") as f:
            f.write(payload)
    except Exception as e:
        print(f"[Cache] Disk save error: {e}")

def _h2h_is_fresh(entry: Dict) -> bool:
    try:
        fetched = datetime.fromisoformat(entry["fetched_at"])
        return (datetime.utcnow() - fetched).days < H2H_TTL_DAYS
    except Exception:
        return False


# ------------------------------------------------------------------ #
# CSV loaders (existing files)
# ------------------------------------------------------------------ #

FOOTBALL_DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "football")

def _load_football_data_csvs() -> pd.DataFrame:
    """
    Load football-data.co.uk CSVs (with Bet365 odds) from data/football/*.csv.
    These files have columns: Date, HomeTeam, AwayTeam, FTHG, FTAG, FTR, B365H, B365D, B365A, HY, AY, HR, AR
    """
    # Map filename prefix → league code (football-data.co.uk naming convention)
    _LEAGUE_CODES = {
        "E0": "PL",  "E1": "ELC", "E2": "EL1", "E3": "EL2",
        "SP1": "PD", "SP2": "SD",
        "I1": "SA",  "I2": "SB",
        "D1": "BL1", "D2": "BL2",
        "F1": "FL1", "F2": "FL2",
        "N1": "DED",
        "B1": "JPL",
        "P1": "PPL",
        "SC0": "SPL", "SC1": "D1",
        "G1": "GSL",
        "T1": "TSL",
    }

    csvs = sorted(glob.glob(os.path.join(FOOTBALL_DATA_DIR, "*.csv")))
    if not csvs:
        return pd.DataFrame()

    dfs = []
    for path in csvs:
        try:
            df = pd.read_csv(path, low_memory=False)
            df.columns = [c.strip() for c in df.columns]

            # Detect league from filename prefix (e.g. "E0_2324.csv" → "PL")
            basename = os.path.splitext(os.path.basename(path))[0]
            prefix = basename.split("_")[0] if "_" in basename else basename[:2]
            league_code = _LEAGUE_CODES.get(prefix, prefix)

            # Standardise column names
            renames = {"FTR": "Result"}
            df = df.rename(columns=renames)

            # Must have basic match columns
            if not all(c in df.columns for c in ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "Result"]):
                continue

            df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
            df = df.dropna(subset=["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "Result"])
            df = df[df["Result"].isin(["H", "D", "A"])]
            df["FTHG"] = pd.to_numeric(df["FTHG"], errors="coerce")
            df["FTAG"] = pd.to_numeric(df["FTAG"], errors="coerce")

            # Parse odds columns
            for col in ["B365H", "B365D", "B365A"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            df["league"] = league_code

            keep = ["Date", "HomeTeam", "AwayTeam", "Result", "FTHG", "FTAG",
                    "B365H", "B365D", "B365A", "HY", "AY", "HR", "AR", "league"]
            df = df[[c for c in keep if c in df.columns]]
            dfs.append(df)
        except Exception as e:
            print(f"[Data] {os.path.basename(path)}: {e}")

    if not dfs:
        return pd.DataFrame()

    combined = pd.concat(dfs, ignore_index=True).drop_duplicates(
        subset=["Date", "HomeTeam", "AwayTeam"]
    ).sort_values("Date").reset_index(drop=True)
    print(f"[Data] football-data.co.uk: {len(combined)} matches with odds from {len(csvs)} CSVs")
    return combined


def _load_epl_csv() -> pd.DataFrame:
    rows = []

    if os.path.exists(EPL_HISTORY):
        h = pd.read_csv(EPL_HISTORY)
        h["Date"] = pd.to_datetime(h["MatchDate"], errors="coerce")
        h = h.dropna(subset=["Date", "FullTimeResult", "FullTimeHomeGoals", "FullTimeAwayGoals"])
        h = h[h["FullTimeResult"].isin(["H", "D", "A"])]
        extra = {}
        for src, dst in [
            ("HomeCorners", "HomeCorners"), ("AwayCorners", "AwayCorners"),
            ("HomeYellowCards", "HomeYellowCards"), ("AwayYellowCards", "AwayYellowCards"),
            ("HomeRedCards", "HomeRedCards"), ("AwayRedCards", "AwayRedCards"),
        ]:
            if src in h.columns:
                extra[dst] = pd.to_numeric(h[src], errors="coerce")

        rows.append(pd.DataFrame({
            "Date": h["Date"],
            "HomeTeam": h["HomeTeam"],
            "AwayTeam": h["AwayTeam"],
            "Result": h["FullTimeResult"],
            "FTHG": h["FullTimeHomeGoals"].astype(float),
            "FTAG": h["FullTimeAwayGoals"].astype(float),
            **extra,
        }))

    if not rows:
        return pd.DataFrame()

    df = pd.concat(rows, ignore_index=True).sort_values("Date").reset_index(drop=True)
    return df


def _load_ucl_csv() -> pd.DataFrame:
    files = sorted(glob.glob(UCL_CSV_PATTERN))
    rows = []

    name_map = {
        "Man. City": "Manchester City", "Man. United": "Manchester United",
        "Bayern München": "Bayern Munich", "B. Dortmund": "Borussia Dortmund",
        "Atlético": "Atletico Madrid", "Paris Saint-Germain": "PSG",
        "Internazionale": "Inter Milan", "Tottenham Hotspur": "Tottenham",
        "RB Leipzig": "RB Leipzig",
    }

    for f in files:
        try:
            df = pd.read_csv(f)
            df.columns = [c.strip() for c in df.columns]
            df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")

            def parse(s):
                if pd.isna(s):
                    return np.nan, np.nan
                try:
                    p = str(s).strip().split("-")
                    return float(p[0]), float(p[1])
                except Exception:
                    return np.nan, np.nan

            scores = df["Result"].apply(parse)
            df["FTHG"] = [x[0] for x in scores]
            df["FTAG"] = [x[1] for x in scores]
            df = df.dropna(subset=["FTHG", "FTAG", "Date"])

            import numpy as _np
            cond = [df["FTHG"] > df["FTAG"], df["FTHG"] < df["FTAG"], df["FTHG"] == df["FTAG"]]
            df["Result"] = _np.select(cond, ["H", "A", "D"], default="X")
            df = df[df["Result"] != "X"]

            df["HomeTeam"] = df["Home Team"].map(lambda t: name_map.get(str(t).strip(), str(t).strip()))
            df["AwayTeam"] = df["Away Team"].map(lambda t: name_map.get(str(t).strip(), str(t).strip()))

            rows.append(pd.DataFrame({
                "Date": df["Date"], "HomeTeam": df["HomeTeam"],
                "AwayTeam": df["AwayTeam"], "Result": df["Result"],
                "FTHG": df["FTHG"], "FTAG": df["FTAG"],
            }))
        except Exception as e:
            print(f"[CSV] Skipping {f}: {e}")

    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True).sort_values("Date").reset_index(drop=True)


DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
INTERNATIONAL_CSV = os.path.join(DATA_DIR, "international_results.csv")

def _load_international_csv() -> pd.DataFrame:
    """
    Load international football match results (all countries, all competitions).
    Best source: Kaggle 'International football results from 1872'
      https://www.kaggle.com/datasets/martj42/international-football-results-from-1872-to-2017
    Download results.csv and place it at backend/data/international_results.csv
    Columns: date, home_team, away_team, home_score, away_score, tournament, ...
    """
    if not os.path.exists(INTERNATIONAL_CSV):
        return pd.DataFrame()
    try:
        df = pd.read_csv(INTERNATIONAL_CSV, parse_dates=["date"], low_memory=False)
        # Only use post-2010 matches — older data less relevant for current form
        df = df[df["date"] >= "2010-01-01"].copy()

        # Drop rows with missing scores BEFORE computing Result — rows with NA
        # scores (e.g. future WC fixtures already listed in the CSV) would otherwise
        # produce NaN goals and corrupt team_stats with NaN values.
        df = df.dropna(subset=["home_score", "away_score"])
        df["home_score"] = pd.to_numeric(df["home_score"], errors="coerce")
        df["away_score"] = pd.to_numeric(df["away_score"], errors="coerce")
        df = df.dropna(subset=["home_score", "away_score"])

        df["Result"] = np.where(df["home_score"] > df["away_score"], "H",
                       np.where(df["home_score"] < df["away_score"], "A", "D"))
        result = pd.DataFrame({
            "Date":     df["date"],
            "HomeTeam": df["home_team"],
            "AwayTeam": df["away_team"],
            "Result":   df["Result"],
            "FTHG":     df["home_score"].astype(float),
            "FTAG":     df["away_score"].astype(float),
        }).dropna(subset=["Date", "HomeTeam", "AwayTeam", "Result", "FTHG", "FTAG"])

        print(f"[CSV] International results: {len(result)} matches (post-2010)")
        return result.sort_values("Date").reset_index(drop=True)
    except Exception as e:
        print(f"[CSV] International results load error: {e}")
        return pd.DataFrame()


# ------------------------------------------------------------------ #
# Train + predict pipeline
# ------------------------------------------------------------------ #

async def _prefetch_web_forms(predictor, fixtures: list):
    """
    Background task: for each team in upcoming fixtures that has fewer than
    5 local matches in team_stats, fetch their last 10 results + xG via
    compound-beta web search and cache in Redis for 24 hours.
    """
    from llm_service import fetch_team_form_web, GROQ_API_KEY
    if not GROQ_API_KEY:
        return

    seen = set()
    sparse_teams = []
    for fx in fixtures:
        for team in (fx["home"], fx["away"]):
            if team in seen:
                continue
            seen.add(team)
            local_pts = len(predictor.team_stats.get(team, {}).get("pts", []))
            # Already have enough local data AND a cached web form → skip
            if local_pts >= 5 and _get_web_form_cache(team):
                continue
            sparse_teams.append(team)

    if not sparse_teams:
        return

    print(f"[WebForm] Fetching form for {len(sparse_teams)} teams with sparse data...")
    for team in sparse_teams[:25]:  # cap at 25 to respect Groq quota
        try:
            cached = _get_web_form_cache(team)
            if cached:
                continue  # already have it
            form = await fetch_team_form_web(team)
            if form and form.get("matches"):
                _set_web_form_cache(team, form)
                # Also feed confirmed match results into team_stats
                for m in form.get("matches", []):
                    try:
                        scored   = float(m["scored"])
                        conceded = float(m["conceded"])
                        result   = m.get("result", "")
                        if result not in ("W", "D", "L"):
                            continue
                        # Translate from team's perspective to H/D/A for _update
                        if m.get("home"):
                            predictor._update(team, "__web__", {"W":"H","D":"D","L":"A"}[result], scored, conceded)
                        else:
                            predictor._update("__web__", team, {"W":"A","D":"D","L":"H"}[result], conceded, scored)
                    except Exception:
                        pass
        except Exception as e:
            print(f"[WebForm] Failed for {team}: {e}")
        await asyncio.sleep(7)  # stay within Groq rate limit

    print("[WebForm] Pre-fetch complete.")


# ------------------------------------------------------------------ #

async def _run_pipeline():
    global _predictor, _predictions_cache, _last_updated, _is_training

    if _is_training:
        return
    _is_training = True

    try:
        print("[Pipeline] Loading CSV data...")
        # Primary: football-data.co.uk CSVs (include Bet365 odds — best for accuracy)
        fd_df = _load_football_data_csvs()
        # Legacy: our existing EPL + UCL CSVs (no odds but more historical depth)
        epl_df = _load_epl_csv()
        ucl_df = _load_ucl_csv()
        # International match history (from Kaggle — fixes national team calibration)
        intl_df = _load_international_csv()

        parts = [df for df in [fd_df, epl_df, ucl_df, intl_df] if not df.empty]
        if not parts:
            print("[Pipeline] No training data found!")
            return

        combined = pd.concat(parts, ignore_index=True)
        combined = combined.drop_duplicates(
            subset=["Date", "HomeTeam", "AwayTeam"]
        ).sort_values("Date").reset_index(drop=True)
        if combined.empty:
            print("[Pipeline] No training data after dedup!")
            return

        global _history_df
        _history_df = combined  # keep for H2H lookups

        # Augment training set with API results saved between runs
        if os.path.exists(RESULTS_CSV):
            try:
                saved_results = pd.read_csv(RESULTS_CSV, parse_dates=["Date"])
                saved_results = saved_results[["Date", "HomeTeam", "AwayTeam", "Result", "FTHG", "FTAG"]].dropna()
                combined = pd.concat([combined, saved_results], ignore_index=True)
                combined = combined.drop_duplicates(subset=["Date", "HomeTeam", "AwayTeam"])
                combined = combined.sort_values("Date").reset_index(drop=True)
                print(f"[Pipeline] +{len(saved_results)} saved API results → {len(combined)} total training rows.")
            except Exception as e:
                print(f"[Pipeline] Saved results load error: {e}")

        print(f"[Pipeline] Training on {len(combined)} matches...")

        # Compute the latest mtime across all data sources so we know when to invalidate
        def _mtime(path):
            try: return os.path.getmtime(path)
            except: return 0.0
        data_mtime = max(
            _mtime(INTERNATIONAL_CSV),
            _mtime(RESULTS_CSV) if os.path.exists(RESULTS_CSV) else 0,
            *[_mtime(os.path.join(DATA_DIR, f)) for f in os.listdir(DATA_DIR) if f.endswith(".csv")]
        )

        predictor = LeaguePredictor.load_cache(data_mtime)
        if predictor is None:
            predictor = LeaguePredictor()
            # Seed national team Elo from FIFA rankings BEFORE training
            # This prevents unknown national teams (Ecuador, Algeria etc.) from
            # starting at 1500 and looking equal to Germany/France/Brazil
            predictor.elo.seed_national_teams()
            predictor.train(combined)
            predictor.save_cache(data_mtime)

        # Make predictor available immediately so card analysis works during API calibration
        global _predictor
        _predictor = predictor
        print("[Pipeline] Predictor ready — card analysis now available.")

        # Fetch recent results from API to update Elo with current season data
        predictions = []
        fixtures: list = []  # pre-init so the block below is safe when API_KEY is unset
        if API_KEY:
            client = FootballDataClient(API_KEY)

            print("[Pipeline] Fetching recent API results to calibrate Elo...")
            for code in list(LEAGUES.keys()):
                try:
                    recent = await client.fetch_recent_results(code, days_back=60)
                    if recent.empty or "HomeTeam" not in recent.columns:
                        await asyncio.sleep(6)  # still pace requests even on empty results
                        continue
                    for _, r in recent.iterrows():
                        predictor._update(r["HomeTeam"], r["AwayTeam"], r["Result"], r["FTHG"], r["FTAG"],
                                          competition=code)
                    await asyncio.sleep(10)
                except Exception as e:
                    print(f"[Pipeline] Recent results error for {code}: {e}")

            print("[Pipeline] Fetching upcoming fixtures + live odds...")
            fixtures = await client.fetch_all_upcoming(days_ahead=90)

            # Fetch live odds from The Odds API for upcoming fixtures
            # Inject into model as features (massively improves accuracy)
            live_odds: dict = {}
            try:
                from odds_fetcher import fetch_odds_for_predictions
                # Build minimal prediction stubs just for odds lookup
                stubs = [{"home": fx["home"], "away": fx["away"],
                          "date": fx.get("date",""), "league_name": fx.get("league_name","")}
                         for fx in fixtures]
                live_odds = await fetch_odds_for_predictions(stubs)
                print(f"[Pipeline] Got live odds for {len(live_odds)}/{len(fixtures)} fixtures")
            except Exception as e:
                print(f"[Pipeline] Live odds fetch error (non-fatal): {e}")

            for fx in fixtures:
                try:
                    key = f"{fx['home']}:{fx['away']}:{fx.get('date','')}"
                    odds = live_odds.get(key, {})
                    tip = predictor.predict_match(
                        fx["home"], fx["away"],
                        odds_home=float(odds.get("1") or 0),
                        odds_draw=float(odds.get("X") or 0),
                        odds_away=float(odds.get("2") or 0),
                        match_date=fx.get("date"),
                        league=fx.get("competition_code", ""),
                    )
                    if tip:
                        # Value bet detection: model prob vs bookmaker implied prob
                        tip_code = tip.get("tip_code", "?")
                        if tip_code == "1" and odds.get("1") and float(odds.get("1", 0)) > 1:
                            implied = 1.0 / float(odds["1"])
                            model_p  = tip.get("p_home", 0)
                        elif tip_code == "X" and odds.get("X") and float(odds.get("X", 0)) > 1:
                            implied = 1.0 / float(odds["X"])
                            model_p  = tip.get("p_draw", 0)
                        elif tip_code == "2" and odds.get("2") and float(odds.get("2", 0)) > 1:
                            implied = 1.0 / float(odds["2"])
                            model_p  = tip.get("p_away", 0)
                        else:
                            implied = None
                            model_p  = None

                        value_edge = round(model_p - implied, 3) if (model_p is not None and implied is not None) else None

                        predictions.append({
                            **fx, **tip,
                            "odds_home": round(float(odds.get("1") or 0), 2) or None,
                            "odds_draw": round(float(odds.get("X") or 0), 2) or None,
                            "odds_away": round(float(odds.get("2") or 0), 2) or None,
                            "value_edge": value_edge,
                            "is_value_bet": value_edge is not None and value_edge > 0.05,
                        })
                except Exception:
                    pass
        else:
            print("[Pipeline] WARNING: No FOOTBALL_DATA_API_KEY set. Add your key to .env to get live fixtures.")

        # Pre-fetch web form for teams that have sparse local data
        # Runs as a background task so it doesn't block the pipeline
        if fixtures:
            asyncio.create_task(_prefetch_web_forms(predictor, fixtures))

        # Archive past predictions from the OLD cache BEFORE replacing it —
        # new `predictions` only has upcoming fixtures so yesterday is already gone.
        _archive_past_predictions()

        # Fetch Understat xG in background (updates Redis cache for European clubs)
        asyncio.create_task(_refresh_understat_xg())

        _predictor = predictor
        _predictions_cache = predictions
        _last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        _save_predictions_cache()
        print(f"[Pipeline] Done — {len(predictions)} predictions cached.")

        # Send push notifications for high-value picks
        value_picks = [p for p in predictions if p.get("is_value_bet") and p.get("value_edge", 0) > 0.08]
        if value_picks:
            asyncio.create_task(_send_push_notifications(value_picks))

    except Exception as e:
        print(f"[Pipeline] Fatal error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        _is_training = False


# ------------------------------------------------------------------ #
# Result fetcher — keeps model calibrated with real match outcomes
# ------------------------------------------------------------------ #

async def _fetch_and_save_results():
    """
    Fetch finished match results from football-data.org for the past 30 days,
    append to results CSV, and apply to the live model's Elo/form state so
    predictions stay fresh between full retrains.
    """
    if not API_KEY:
        return

    print("[Results] Fetching recent finished results...")
    client = FootballDataClient(API_KEY)
    all_rows: List[pd.DataFrame] = []

    for code in LEAGUES:
        try:
            df = await client.fetch_recent_results(code, days_back=30)
            if not df.empty:
                df["league"] = code
                all_rows.append(df)
            await asyncio.sleep(10)
        except Exception as e:
            print(f"[Results] Error fetching {code}: {e}")

    if not all_rows:
        print("[Results] No results returned.")
        return

    new_df = pd.concat(all_rows, ignore_index=True)
    os.makedirs("data", exist_ok=True)

    if os.path.exists(RESULTS_CSV):
        existing = pd.read_csv(RESULTS_CSV, parse_dates=["Date"])
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["Date", "HomeTeam", "AwayTeam"])
        combined = combined.sort_values("Date").reset_index(drop=True)
    else:
        combined = new_df

    combined.to_csv(RESULTS_CSV, index=False)
    print(f"[Results] {len(combined)} results saved to {RESULTS_CSV}")

    # Push new results into the live predictor without a full retrain
    if _predictor is not None:
        updated = 0
        for _, r in new_df.iterrows():
            try:
                _predictor._update(
                    r["HomeTeam"], r["AwayTeam"], r["Result"],
                    float(r["FTHG"]), float(r["FTAG"]),
                )
                updated += 1
            except Exception:
                pass
        print(f"[Results] Applied {updated} results to live model.")

    # Web search fallback: find results for past predictions still marked "pending"
    # that the API didn't return (e.g. international friendlies, cup games)
    await _web_search_missing_results()


async def _web_search_missing_results():
    """
    For past predictions still marked 'pending' in Redis history,
    use web search (compound-beta) to find the actual result,
    then update the history and feed into the live model.
    """
    from llm_service import GROQ_API_KEY, fetch_missing_results
    if not GROQ_API_KEY:
        return
    from datetime import date as _date, timedelta

    r = _get_redis()
    if not r:
        return

    today = _date.today()
    searched = 0

    # Check the last 7 days of history for pending predictions
    for days_ago in range(1, 8):
        d = (today - timedelta(days=days_ago)).isoformat()
        try:
            raw = r.get(f"betiq:history:{d}")
            if not raw:
                continue
            preds = json.loads(raw)
            still_pending = [p for p in preds if p.get("outcome") == "pending"]
            if not still_pending:
                continue

            changed = False
            for pred in still_pending:
                if searched >= 5:  # cap to save Groq quota
                    break
                try:
                    res = await fetch_missing_results(pred["home"], pred["away"], d)
                    if res.get("found"):
                        pred["actual_result"] = res["result"]
                        pred["score"] = f"{res['home_goals']}-{res['away_goals']}"
                        tip_map = {"1": "H", "X": "D", "2": "A"}
                        expected = tip_map.get(pred.get("tip_code",""), "")
                        pred["outcome"] = "won" if res["result"] == expected else "lost"
                        pred["source"] = "web_search"
                        changed = True
                        searched += 1

                        # Feed into live model
                        if _predictor:
                            _predictor._update(
                                pred["home"], pred["away"], res["result"],
                                res["home_goals"], res["away_goals"]
                            )
                        print(f"[WebResults] Found via web: {pred['home']} {pred['score']} {pred['away']} → {pred['outcome']}")
                    await asyncio.sleep(1)
                except Exception:
                    pass

            if changed:
                r.set(f"betiq:history:{d}", json.dumps(preds), ex=90 * 86400)
        except Exception:
            pass

    if searched:
        print(f"[WebResults] Found and applied {searched} missing results via web search")


# ------------------------------------------------------------------ #
# Routes
# ------------------------------------------------------------------ #

@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health():
    return {
        "status": "ok",
        "last_updated": _last_updated,
        "predictions": len(_predictions_cache),
        "model_ready": _predictor is not None,
    }


@app.get("/api/leagues")
async def get_leagues():
    return [
        {"code": code, **info}
        for code, info in LEAGUES.items()
    ]


@app.get("/api/predictions")
async def get_predictions(
    league: Optional[str] = None,
    date_str: Optional[str] = None,
    min_confidence: float = 0.0,
    limit: int = 500,
):
    data = _predictions_cache

    if league and league.upper() != "ALL":
        data = [p for p in data if p.get("league", "").upper() == league.upper()]

    if date_str:
        data = [p for p in data if p.get("date") == date_str]

    if min_confidence > 0:
        data = [p for p in data if p.get("goals_confidence", 0) >= min_confidence]

    # Sort by confidence desc, then date
    data = sorted(data, key=lambda x: (-x.get("goals_confidence", 0), x.get("date", "")))

    return {
        "predictions": data[:limit],
        "total": len(data),
        "last_updated": _last_updated,
    }


def _parse_api_h2h(data: Dict, home: str, away: str) -> Dict:
    """Convert football-data.org H2H response into our standard format."""
    agg = data.get("aggregates", {})
    home_agg = agg.get("homeTeam", {})
    away_agg = agg.get("awayTeam", {})
    total = agg.get("numberOfMatches", 0)
    total_goals = agg.get("totalGoals", 0)

    # The API returns stats from the perspective of the fixture's home team.
    # We normalise to our requested home team.
    api_home_name = home_agg.get("name", "")
    our_home_is_api_home = api_home_name.lower() == home.lower()

    if our_home_is_api_home:
        home_w = home_agg.get("wins", 0)
        away_w = away_agg.get("wins", 0)
    else:
        home_w = away_agg.get("wins", 0)
        away_w = home_agg.get("wins", 0)

    draws = total - home_w - away_w

    rows = []
    for m in data.get("matches", []):
        sc = m.get("score", {}).get("fullTime", {})
        hg = sc.get("home")
        ag = sc.get("away")
        if hg is None or ag is None:
            continue
        result = "H" if hg > ag else ("A" if ag > hg else "D")
        w = m.get("winner")
        rows.append({
            "date": m["utcDate"][:10],
            "home_team": m["homeTeam"]["name"],
            "away_team": m["awayTeam"]["name"],
            "score": f"{hg}-{ag}",
            "result": result,
            "winner": m["homeTeam"]["name"] if w == "HOME_TEAM" else (
                      m["awayTeam"]["name"] if w == "AWAY_TEAM" else None),
            "competition": m.get("competition", {}).get("name", ""),
        })

    home_gf = sum(
        int(r["score"].split("-")[0]) if r["home_team"].lower() == home.lower()
        else int(r["score"].split("-")[1])
        for r in rows
    )
    home_ga = sum(
        int(r["score"].split("-")[1]) if r["home_team"].lower() == home.lower()
        else int(r["score"].split("-")[0])
        for r in rows
    )
    n = len(rows)
    btts = sum(1 for r in rows if int(r["score"].split("-")[0]) > 0 and int(r["score"].split("-")[1]) > 0)

    return {
        "meetings": rows,
        "summary": {
            "total": total,
            "home_wins": home_w,
            "draws": draws,
            "away_wins": away_w,
            "home_goals": home_gf,
            "away_goals": home_ga,
            "avg_goals": round(total_goals / total, 1) if total else 0,
            "btts_count": btts,
        },
        "source": "api",
        "fetched_at": datetime.utcnow().isoformat(),
    }


def _parse_csv_h2h(home: str, away: str, limit: int = 10) -> Dict:
    """Fall back to CSV history when API data unavailable."""
    if _history_df is None:
        return {"meetings": [], "summary": None, "source": "none"}

    df = _history_df
    mask = (
        ((df["HomeTeam"] == home) & (df["AwayTeam"] == away)) |
        ((df["HomeTeam"] == away) & (df["AwayTeam"] == home))
    )
    meetings = df[mask].sort_values("Date", ascending=False).head(limit)
    if meetings.empty:
        return {"meetings": [], "summary": None, "source": "csv"}

    rows, home_w, draw, away_w, home_gf, home_ga = [], 0, 0, 0, 0, 0
    for _, r in meetings.iterrows():
        is_h = r["HomeTeam"] == home
        gf = int(r["FTHG"]) if is_h else int(r["FTAG"])
        ga = int(r["FTAG"]) if is_h else int(r["FTHG"])
        res_for_home = r["Result"] if is_h else {"H": "A", "A": "H", "D": "D"}.get(r["Result"], r["Result"])
        if res_for_home == "H": home_w += 1
        elif res_for_home == "A": away_w += 1
        else: draw += 1
        home_gf += gf; home_ga += ga
        rows.append({
            "date": r["Date"].strftime("%d %b %Y"),
            "home_team": r["HomeTeam"], "away_team": r["AwayTeam"],
            "score": f"{int(r['FTHG'])}-{int(r['FTAG'])}",
            "result": r["Result"],
            "winner": r["HomeTeam"] if r["Result"] == "H" else (r["AwayTeam"] if r["Result"] == "A" else None),
            "competition": "Historical",
        })

    n = len(rows)
    btts = sum(1 for r in rows if int(r["score"].split("-")[0]) > 0 and int(r["score"].split("-")[1]) > 0)
    return {
        "meetings": rows,
        "summary": {
            "total": n, "home_wins": home_w, "draws": draw, "away_wins": away_w,
            "home_goals": home_gf, "away_goals": home_ga,
            "avg_goals": round((home_gf + home_ga) / n, 1) if n else 0,
            "btts_count": btts,
        },
        "source": "csv",
        "fetched_at": datetime.utcnow().isoformat(),
    }


async def _fetch_live_odds(home: str, away: str, date_str: str = "") -> Dict:
    """
    Fetch truly live odds from The Odds API at request time.
    Called when a match modal opens — not from cache.
    Returns {home_odds, draw_odds, away_odds, btts_yes, btts_no, bookie}.
    """
    api_key = os.getenv("ODDS_API_KEY", "")
    if not api_key:
        return {}

    from difflib import SequenceMatcher
    def sim(a, b): return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    SPORT_KEYS = [
        "soccer_epl", "soccer_italy_serie_a", "soccer_germany_bundesliga",
        "soccer_spain_la_liga", "soccer_france_ligue_one",
        "soccer_uefa_champs_league", "soccer_uefa_europa_league",
        "soccer_portugal_primeira_liga", "soccer_netherlands_eredivisie",
        "soccer_england_efl_champ", "soccer_germany_bundesliga2",
        "soccer_fifa_world_cup", "soccer_africa_cup_of_nations",
        "soccer_uefa_nations_league", "soccer_conmebol_copa_america",
    ]

    try:
        import httpx as _hx
        async with _hx.AsyncClient(timeout=10) as client:
            for sport_key in SPORT_KEYS:
                r = await client.get(
                    f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/",
                    params={"apiKey": api_key, "regions": "eu",
                            "markets": "h2h,btts", "oddsFormat": "decimal"},
                )
                if r.status_code != 200:
                    continue
                for ev in r.json():
                    h = ev.get("home_team","")
                    a = ev.get("away_team","")
                    if (sim(home, h) + sim(away, a)) / 2 < 0.55:
                        continue
                    # Found the match — extract best odds
                    best: Dict[str, float] = {}
                    btts: Dict[str, float] = {}
                    for bk in (ev.get("bookmakers") or []):
                        for mkt in (bk.get("markets") or []):
                            if mkt["key"] == "h2h":
                                for o in mkt.get("outcomes",[]):
                                    n = o["name"]; p = float(o.get("price",0))
                                    if n == h:    best["1"] = max(best.get("1",0), p)
                                    elif n == a:  best["2"] = max(best.get("2",0), p)
                                    else:         best["X"] = max(best.get("X",0), p)
                            elif mkt["key"] == "btts":
                                for o in mkt.get("outcomes",[]):
                                    if o["name"].lower() == "yes":
                                        btts["yes"] = max(btts.get("yes",0), float(o.get("price",0)))
                                    else:
                                        btts["no"]  = max(btts.get("no",0),  float(o.get("price",0)))
                    if best:
                        print(f"[LiveOdds] {home} vs {away}: 1={best.get('1')} X={best.get('X')} 2={best.get('2')}")
                        return {**best, **btts, "bookie": "market (live)"}
    except Exception as e:
        print(f"[LiveOdds] fetch error: {e}")
    return {}


def _sanitize(obj):
    """Recursively replace NaN/Inf with None so FastAPI can JSON-serialize the response."""
    import math
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


@app.get("/api/analysis")
async def get_match_analysis(home: str, away: str):
    if _predictor is None:
        raise HTTPException(status_code=503, detail="Model not ready yet")

    # Find this fixture in our predictions cache to get date + any cached odds
    cached_fx = next(
        (p for p in _predictions_cache
         if p.get("home","").lower() == home.lower()
         and p.get("away","").lower() == away.lower()),
        {}
    )
    fx_date = cached_fx.get("date", "")

    # Fetch LIVE odds right now from The Odds API (not from cache)
    live_odds = await _fetch_live_odds(home, away, fx_date)

    # Fetch live web news and extract structured model adjustments in parallel
    from llm_service import extract_model_adjustments
    from llm_service import _fetch_news as _web_news
    news_text, news_sources = await _web_news(home, away)
    adjustments = await extract_model_adjustments(home, away, news_text) if news_text else {}

    # Apply web-search adjustments to xG before running the model
    # This makes injury news actually move the prediction numbers
    adj_xg_h = 1.0 + adjustments.get("home_attack_modifier", 0.0)
    adj_xg_a = 1.0 + adjustments.get("away_attack_modifier", 0.0)
    adj_def_h = 1.0 + adjustments.get("home_defense_modifier", 0.0)
    adj_def_a = 1.0 + adjustments.get("away_defense_modifier", 0.0)
    if adjustments:
        _predictor._init(home)
        _predictor._init(away)
        # Temporarily scale goal lists so Dixon-Coles xG reflects the news adjustment.
        # We scale both overall gf and the venue-specific home_gf/away_gf lists.
        orig_home_gf      = _predictor.team_stats[home].get("gf", [])
        orig_home_gf_home = _predictor.team_stats[home].get("home_gf", [])
        orig_away_gf      = _predictor.team_stats[away].get("gf", [])
        orig_away_gf_away = _predictor.team_stats[away].get("away_gf", [])
        if adj_xg_h != 1.0:
            if orig_home_gf:
                _predictor.team_stats[home]["gf"]      = [v * adj_xg_h for v in orig_home_gf]
            if orig_home_gf_home:
                _predictor.team_stats[home]["home_gf"] = [v * adj_xg_h for v in orig_home_gf_home]
        if adj_xg_a != 1.0:
            if orig_away_gf:
                _predictor.team_stats[away]["gf"]      = [v * adj_xg_a for v in orig_away_gf]
            if orig_away_gf_away:
                _predictor.team_stats[away]["away_gf"] = [v * adj_xg_a for v in orig_away_gf_away]

    # If live odds available, re-run prediction with them for better accuracy
    if live_odds:
        result = _predictor.predict_match_full(
            home, away,
            odds_home=live_odds.get("1", 0),
            odds_draw=live_odds.get("X", 0),
            odds_away=live_odds.get("2", 0),
        )
    else:
        result = _predictor.predict_match_full(home, away)

    # Restore original stats after prediction (don't permanently alter training data)
    if adjustments:
        if adj_xg_h != 1.0:
            _predictor.team_stats[home]["gf"]      = orig_home_gf
            _predictor.team_stats[home]["home_gf"] = orig_home_gf_home
        if adj_xg_a != 1.0:
            _predictor.team_stats[away]["gf"]      = orig_away_gf
            _predictor.team_stats[away]["away_gf"] = orig_away_gf_away

    # Apply confidence modifier from web search
    conf_mod = adjustments.get("confidence_modifier", 0.0)
    if conf_mod and result:
        result["web_confidence_modifier"] = conf_mod
        result["web_adjustment_flags"] = adjustments.get("flags", [])
        result["web_adjustment_reason"] = adjustments.get("reasoning", "")
        result["web_news_sources"] = news_sources

    if result is None:
        raise HTTPException(status_code=404, detail="Could not generate analysis")

    # Tag live odds onto 1X2 market options
    if live_odds:
        for mkt in result["markets"]:
            if mkt["id"] == "1x2":
                for opt in mkt["options"]:
                    if opt["code"] == "1" and live_odds.get("1"):
                        opt["odds"] = str(live_odds["1"]); opt["bookie"] = live_odds.get("bookie","")
                    elif opt["code"] == "X" and live_odds.get("X"):
                        opt["odds"] = str(live_odds["X"]); opt["bookie"] = live_odds.get("bookie","")
                    elif opt["code"] == "2" and live_odds.get("2"):
                        opt["odds"] = str(live_odds["2"]); opt["bookie"] = live_odds.get("bookie","")
            elif mkt["id"] == "btts":
                for opt in mkt["options"]:
                    if opt["code"] == "BTTS-Y" and live_odds.get("yes"):
                        opt["odds"] = str(live_odds["yes"]); opt["bookie"] = live_odds.get("bookie","")
                    elif opt["code"] == "BTTS-N" and live_odds.get("no"):
                        opt["odds"] = str(live_odds["no"]); opt["bookie"] = live_odds.get("bookie","")
        result["live_odds_fetched"] = True
        result["odds_bookie"] = live_odds.get("bookie", "")

    # Show team form stats in the response
    result["team_form"] = {
        "home": _predictor_form_summary(home),
        "away": _predictor_form_summary(away),
    }

    # Inject cards market if data is available
    extra = _predictor.predict_cards(home, away, _cards_df)
    if "cards" in extra:
        result["markets"].append(extra["cards"])

    # Blend H2H win rates into model probabilities if cached data exists
    key = _h2h_cache_key(home, away)
    cached = _h2h_cache.get(key)
    if cached and cached.get("summary") and cached["summary"]["total"] >= 3:
        s = cached["summary"]
        n = s["total"]
        h2h_ph = s["home_wins"] / n
        h2h_pd = s["draws"] / n
        h2h_pa = s["away_wins"] / n
        W = 0.15  # 15% weight on H2H, 85% on XGBoost
        for mkt in result["markets"]:
            if mkt["id"] == "1x2":
                for opt in mkt["options"]:
                    if opt["code"] == "1":
                        opt["prob"] = round((1 - W) * opt["prob"] + W * h2h_ph, 3)
                    elif opt["code"] == "X":
                        opt["prob"] = round((1 - W) * opt["prob"] + W * h2h_pd, 3)
                    elif opt["code"] == "2":
                        opt["prob"] = round((1 - W) * opt["prob"] + W * h2h_pa, 3)
        result["h2h_blended"] = True
        result["h2h_matches_used"] = n

    # Inject live SportyBet odds into each market (best-effort)
    # SportyBet market IDs confirmed from network capture:
    #   1=1X2, 10=Double Chance, 11=Draw No Bet, 18=O/U Goals,
    #   29=GG/NG(BTTS), 36=Result+BTTS combo
    CODE_TO_SB: dict = {
        # 1X2
        "1":       ("1",  "home"),           "X":       ("1",  "draw"),
        "2":       ("1",  "away"),
        # Double Chance
        "1X":      ("10", "home or draw"),   "X2":      ("10", "draw or away"),
        "12":      ("10", "home or away"),
        # Draw No Bet
        "DNB-H":   ("11", "home"),           "DNB-A":   ("11", "away"),
        # BTTS
        "BTTS-Y":  ("29", "yes"),            "BTTS-N":  ("29", "no"),
        # Result + BTTS (market 36, specifier total=2.5 based on network capture)
        "RB-H-Y":  ("36", "home win & yes"), "RB-D-Y":  ("36", "draw & yes"),
        "RB-A-Y":  ("36", "away win & yes"),
        "RB-H-N":  ("36", "home win & no"),  "RB-D-N":  ("36", "draw & no"),
        "RB-A-N":  ("36", "away win & no"),
    }

    try:
        from sportybet import fetch_events_for_date, find_event
        from datetime import date as _date
        today = _date.today().isoformat()
        sb_events = await fetch_events_for_date(today)
        sb_event  = find_event(home, away, sb_events) if sb_events else None
        if sb_event:
            odds_lookup: dict = {}
            for m in (sb_event.get("markets") or []):
                mid = str(m.get("id",""))
                spec = str(m.get("specifier","") or "")
                for o in (m.get("outcomes") or []):
                    desc = (o.get("desc") or "").lower().strip()
                    # Key with and without specifier
                    odds_lookup[f"{mid}:{desc}"] = str(o.get("odds",""))
                    if spec:
                        odds_lookup[f"{mid}:{spec}:{desc}"] = str(o.get("odds",""))

            for mkt in result["markets"]:
                for opt in mkt.get("options", []):
                    code = opt.get("code","")
                    sb = CODE_TO_SB.get(code)
                    if sb:
                        odds_val = odds_lookup.get(f"{sb[0]}:{sb[1]}")
                        if odds_val and float(odds_val) > 1:
                            opt["odds"] = odds_val
                            opt["bookie"] = "SportyBet"
    except Exception:
        pass

    # Also try The Odds API for markets SportyBet doesn't cover (Win to Nil, Clean Sheet)
    try:
        if os.getenv("ODDS_API_KEY"):
            import httpx as _hx
            # Find the prediction with odds already fetched for this match
            cached_odds = next(
                (p for p in _predictions_cache
                 if p.get("home","").lower() == home.lower()
                 and p.get("away","").lower() == away.lower()),
                {}
            )
            # Inject pre-fetched odds from prediction cache onto 1x2 options
            o1 = cached_odds.get("odds_home")
            ox = cached_odds.get("odds_draw")
            o2 = cached_odds.get("odds_away")
            if o1 and ox and o2:
                for mkt in result["markets"]:
                    if mkt["id"] == "1x2":
                        for opt in mkt["options"]:
                            if opt["code"] == "1" and not opt.get("odds"):
                                opt["odds"] = str(o1); opt["bookie"] = "market"
                            elif opt["code"] == "X" and not opt.get("odds"):
                                opt["odds"] = str(ox); opt["bookie"] = "market"
                            elif opt["code"] == "2" and not opt.get("odds"):
                                opt["odds"] = str(o2); opt["bookie"] = "market"
    except Exception:
        pass

    return _sanitize(result)


@app.get("/api/h2h")
async def get_h2h(home: str, away: str):
    key = _h2h_cache_key(home, away)

    # Return fresh cache if available
    if key in _h2h_cache and _h2h_is_fresh(_h2h_cache[key]):
        return _h2h_cache[key]

    # Try live API using match_id from predictions cache
    match_id = next(
        (p.get("match_id") for p in _predictions_cache
         if p.get("home", "").lower() == home.lower()
         and p.get("away", "").lower() == away.lower()),
        None
    )

    if match_id and API_KEY:
        try:
            client = FootballDataClient(API_KEY)
            raw = await client.fetch_h2h(match_id, limit=20)
            print(f"[H2H] API response for match {match_id}: keys={list(raw.keys()) if raw else None}, matches={len(raw.get('matches', [])) if raw else 0}")
            if raw is not None:
                parsed = _parse_api_h2h(raw, home, away)
                _h2h_cache[key] = parsed
                _save_h2h_cache()
                print(f"[H2H] Stored {parsed['summary']['total']} meetings for {home} vs {away} (source=api)")
                # If API gave us data (even 0 meetings), return it — don't fall to CSV
                if parsed["summary"]["total"] > 0 or len(raw.get("matches", [])) == 0:
                    return parsed
        except Exception as e:
            print(f"[H2H] API fetch failed for match {match_id}: {e}")
    else:
        print(f"[H2H] No match_id found for {home} vs {away} (cache size={len(_predictions_cache)})")

    # Fall back to CSV history
    csv_result = _parse_csv_h2h(home, away)
    if csv_result["meetings"]:
        _h2h_cache[key] = csv_result
        _save_h2h_cache()

    return csv_result


def _sim_name(a: str, b: str) -> bool:
    from difflib import SequenceMatcher
    def n(s): return s.lower().replace(" fc","").replace(" united"," utd").strip()
    return SequenceMatcher(None, n(a), n(b)).ratio() >= 0.6


def _archive_past_predictions():
    """
    Compare past predictions against real results and store outcomes in Redis.
    Called at the end of each pipeline run.
    """
    if not _predictions_cache:
        return
    today = date.today().isoformat()
    past = [p for p in _predictions_cache if p.get("date", "") < today]
    if not past:
        return

    results_df = pd.DataFrame()
    if os.path.exists(RESULTS_CSV):
        try:
            results_df = pd.read_csv(RESULTS_CSV, parse_dates=["Date"])
        except Exception:
            pass

    TIP_TO_RESULT = {"1": "H", "X": "D", "2": "A"}
    by_date: Dict[str, List] = {}

    for pred in past:
        d = pred.get("date", "")
        outcome, actual_result = "pending", None
        tip_code = pred.get("tip_code", "")

        if not results_df.empty and tip_code in TIP_TO_RESULT:
            day = results_df[results_df["Date"].dt.date.astype(str) == d]
            for _, res in day.iterrows():
                if (_sim_name(pred.get("home",""), str(res.get("HomeTeam",""))) and
                        _sim_name(pred.get("away",""), str(res.get("AwayTeam","")))):
                    actual_result = str(res.get("Result",""))
                    expected = TIP_TO_RESULT[tip_code]
                    outcome = "won" if actual_result == expected else "lost"
                    break

        entry = {**pred, "outcome": outcome, "actual_result": actual_result}
        by_date.setdefault(d, []).append(entry)

    r = _get_redis()
    for d, preds in by_date.items():
        if r:
            try:
                has_settled = any(p.get("outcome") in ("won", "lost") for p in preds)
                existing_raw = r.get(f"betiq:history:{d}")

                if not existing_raw:
                    # First time — write regardless
                    r.set(f"betiq:history:{d}", json.dumps(preds), ex=90 * 86400)
                elif has_settled:
                    # We have real results — always overwrite (upgrades pending → won/lost)
                    existing = json.loads(existing_raw)
                    # Merge: keep any manually submitted results not in our archive
                    result_map = {f"{p['home']}:{p['away']}": p for p in preds}
                    for ex_p in existing:
                        key2 = f"{ex_p['home']}:{ex_p['away']}"
                        if key2 not in result_map:
                            result_map[key2] = ex_p
                    r.set(f"betiq:history:{d}", json.dumps(list(result_map.values())), ex=90 * 86400)
            except Exception as e:
                print(f"[History] Redis error for {d}: {e}")
    settled_count = sum(1 for preds in by_date.values() if any(p.get("outcome") in ("won","lost") for p in preds))
    print(f"[History] Archived {len(past)} predictions across {len(by_date)} dates ({settled_count} dates with results).")


@app.get("/api/history")
async def get_history(date: str):
    """Return predictions for a specific date with outcomes (won/lost/pending)."""
    r = _get_redis()
    if r:
        try:
            raw = r.get(f"betiq:history:{date}")
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    # Fall back to current predictions cache (works for today + upcoming)
    from_cache = [
        {**p, "outcome": "pending", "actual_result": None}
        for p in _predictions_cache
        if p.get("date") == date
    ]
    if from_cache:
        return from_cache
    return []


@app.get("/api/calendar")
async def get_calendar(month: str = ""):
    """Return per-date prediction summary for a given month (YYYY-MM)."""
    import calendar as cal_lib
    if not month:
        month = datetime.utcnow().strftime("%Y-%m")
    try:
        year, m = map(int, month.split("-"))
    except ValueError:
        raise HTTPException(status_code=400, detail="month must be YYYY-MM")

    days_in_month = cal_lib.monthrange(year, m)[1]
    summary: Dict[str, Any] = {}
    r = _get_redis()

    # Debug: log what dates exist in cache
    cache_dates = sorted({p.get("date","") for p in _predictions_cache if p.get("date","").startswith(month)})
    print(f"[Calendar] {month}: {len(_predictions_cache)} in cache, dates in month: {cache_dates}")

    for day in range(1, days_in_month + 1):
        d = f"{month}-{day:02d}"
        data: List[Dict] = []

        if r:
            try:
                raw = r.get(f"betiq:history:{d}")
                if raw:
                    data = json.loads(raw)
            except Exception:
                pass

        if not data:
            # Fall back to live predictions cache
            current = [p for p in _predictions_cache
                       if (p.get("date") or p.get("Date",""))[:10] == d]
            if current:
                data = [{**p, "outcome": "pending", "actual_result": None} for p in current]

        if data:
            won     = sum(1 for p in data if p.get("outcome") == "won")
            lost    = sum(1 for p in data if p.get("outcome") == "lost")
            pending = sum(1 for p in data if p.get("outcome") == "pending")
            summary[d] = {"total": len(data), "won": won, "lost": lost, "pending": pending}

    print(f"[Calendar] {month}: returning {len(summary)} days with data")
    return summary


@app.post("/api/feedback/result")
async def submit_match_result(body: Dict[str, Any]):
    """
    Submit a confirmed match result to update Elo and track prediction accuracy.
    Body: {home, away, date, result: "H"|"D"|"A", home_score?, away_score?}
    """
    home   = body.get("home", "")
    away   = body.get("away", "")
    date_s = body.get("date", "")
    result = body.get("result", "")  # H / D / A
    home_s = body.get("home_score")
    away_s = body.get("away_score")

    if not all([home, away, date_s, result]):
        raise HTTPException(status_code=400, detail="home, away, date, result required")

    # Update Redis history entry with actual result
    r = _get_redis()
    if r:
        try:
            raw = r.get(f"betiq:history:{date_s}")
            if raw:
                preds = json.loads(raw)
                TIP_TO_RESULT = {"1": "H", "X": "D", "2": "A"}
                updated = 0
                for p in preds:
                    h_sim = _sim_name(p.get("home",""), home)
                    a_sim = _sim_name(p.get("away",""), away)
                    if h_sim and a_sim:
                        p["actual_result"] = result
                        expected = TIP_TO_RESULT.get(p.get("tip_code",""), "")
                        p["outcome"] = "won" if result == expected else "lost"
                        if home_s is not None and away_s is not None:
                            p["score"] = f"{home_s}-{away_s}"
                        updated += 1
                if updated:
                    r.set(f"betiq:history:{date_s}", json.dumps(preds), ex=90*86400)
                    print(f"[Feedback] Updated {updated} predictions for {home} vs {away}")
        except Exception as e:
            print(f"[Feedback] Redis update error: {e}")

    # Also append to results CSV so next training run picks it up
    try:
        row = {
            "Date": date_s, "HomeTeam": home, "AwayTeam": away,
            "Result": result,
            "FTHG": home_s if home_s is not None else "",
            "FTAG": away_s if away_s is not None else "",
        }
        if os.path.exists(RESULTS_CSV):
            existing = pd.read_csv(RESULTS_CSV)
            # Avoid duplicates
            mask = (existing["HomeTeam"] == home) & (existing["AwayTeam"] == away) & (existing["Date"] == date_s)
            if not mask.any():
                existing = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
                existing.to_csv(RESULTS_CSV, index=False)
        else:
            pd.DataFrame([row]).to_csv(RESULTS_CSV, index=False)
    except Exception as e:
        print(f"[Feedback] CSV update error: {e}")

    # Trigger live Elo update if predictor is ready
    if _predictor is not None and result in ("H", "D", "A"):
        try:
            _predictor._update(home, away, result,
                               float(home_s) if home_s else 0,
                               float(away_s) if away_s else 0)
            print(f"[Feedback] Elo updated for {home} vs {away}: {result}")
        except Exception as e:
            print(f"[Feedback] Elo update error: {e}")

    return {"ok": True, "message": f"Result recorded: {home} vs {away} = {result}"}


# ── Push notification subscriptions ──────────────────────────────────────── #

PUSH_SUBS_KEY = "betiq:push_subs"

@app.get("/api/push/public-key")
async def push_public_key():
    key = os.getenv("VAPID_PUBLIC_KEY", "")
    return {"public_key": key}

@app.post("/api/push/subscribe")
async def push_subscribe(req: Request):
    body = await req.json()
    sub = body.get("subscription")
    if not sub:
        return {"ok": False, "error": "no subscription"}
    r = _get_redis()
    if r:
        import json as _json
        r.sadd(PUSH_SUBS_KEY, _json.dumps(sub, sort_keys=True))
    return {"ok": True}

@app.delete("/api/push/subscribe")
async def push_unsubscribe(req: Request):
    body = await req.json()
    sub = body.get("subscription")
    if not sub:
        return {"ok": False}
    r = _get_redis()
    if r:
        import json as _json
        r.srem(PUSH_SUBS_KEY, _json.dumps(sub, sort_keys=True))
    return {"ok": True}


async def _send_push_notifications(value_preds: list):
    """Send push notification to all subscribers when high-value picks are found."""
    vapid_private = os.getenv("VAPID_PRIVATE_KEY", "")
    vapid_claims_email = os.getenv("VAPID_CLAIMS_EMAIL", "admin@betiq.app")
    if not vapid_private:
        return
    r = _get_redis()
    if not r:
        return
    try:
        from pywebpush import webpush, WebPushException
        import json as _json
        subs_raw = r.smembers(PUSH_SUBS_KEY)
        if not subs_raw:
            return
        payload_obj = {
            "title": f"BetIQ — {len(value_preds)} Value Bet{'s' if len(value_preds) > 1 else ''} Found!",
            "body": " · ".join(f"{p['home']} vs {p['away']}" for p in value_preds[:3]),
            "icon": "/logo.svg",
            "url": "/",
        }
        payload = _json.dumps(payload_obj)
        for sub_raw in subs_raw:
            try:
                sub = _json.loads(sub_raw)
                webpush(
                    subscription_info=sub,
                    data=payload,
                    vapid_private_key=vapid_private,
                    vapid_claims={"sub": f"mailto:{vapid_claims_email}"},
                )
            except WebPushException as e:
                if "410" in str(e) or "404" in str(e):
                    r.srem(PUSH_SUBS_KEY, sub_raw)  # remove expired subscription
            except Exception:
                pass
        print(f"[Push] Sent notifications to {len(subs_raw)} subscribers")
    except ImportError:
        print("[Push] pywebpush not installed — push notifications disabled")
    except Exception as e:
        print(f"[Push] Error: {e}")


async def _refresh_understat_xg():
    """Background: fetch Understat xG for European leagues, cache in Redis."""
    try:
        from understat_fetcher import fetch_all_leagues_xg, cache_xg
        r = _get_redis()
        if not r:
            return
        xg_data = await fetch_all_leagues_xg()
        if xg_data:
            cache_xg(r, xg_data)
    except Exception as e:
        print(f"[Understat] Refresh error: {e}")


@app.post("/api/admin/upload/basketball-csv")
async def upload_basketball_csv(request: Request):
    """
    Upload a Kaggle NBA/basketball CSV to train the Elo model.
    Multipart form: file field named 'file'.
    Protected by ADMIN_SECRET header.
    """
    from fastapi import UploadFile, File
    import shutil

    if request.headers.get("x-admin-secret") != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Admin access only")

    form = await request.form()
    upload = form.get("file")
    if not upload:
        raise HTTPException(status_code=400, detail="No file provided")

    os.makedirs(DATA_DIR, exist_ok=True)
    dest = os.path.join(DATA_DIR, "basketball_games.csv")
    try:
        content = await upload.read()
        with open(dest, "wb") as f:
            f.write(content)
        size_mb = len(content) / (1024 * 1024)

        # Retrain immediately
        from basketball_predictor import train_from_csv, _bball_elo
        import basketball_predictor as bp_mod
        elo = train_from_csv(dest)
        bp_mod._bball_elo = elo  # reset singleton
        teams = len(elo.ratings) if elo else 0

        return {
            "ok": True,
            "size_mb": round(size_mb, 2),
            "teams_trained": teams,
            "message": f"Basketball Elo trained on {teams} teams. Delete cache to refresh predictions.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/upload/tennis-csv")
async def upload_tennis_csv(request: Request):
    """
    Upload Jeff Sackmann ATP/WTA CSV files to train the tennis Elo model.
    Can upload multiple files — all go into data/tennis/ folder.
    Protected by ADMIN_SECRET header.
    Form fields: file (required), tour (atp|wta, optional)
    """
    if request.headers.get("x-admin-secret") != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Admin access only")

    form = await request.form()
    upload = form.get("file")
    if not upload:
        raise HTTPException(status_code=400, detail="No file provided")

    tennis_dir = os.path.join(DATA_DIR, "tennis")
    os.makedirs(tennis_dir, exist_ok=True)

    filename = getattr(upload, "filename", None) or "tennis_matches.csv"
    dest = os.path.join(tennis_dir, filename)
    try:
        content = await upload.read()
        with open(dest, "wb") as f:
            f.write(content)
        size_mb = len(content) / (1024 * 1024)

        # Retrain with all CSVs in the tennis folder
        from tennis_predictor import train_tennis_elo
        import tennis_predictor as tp_mod
        elo = train_tennis_elo(tennis_dir)
        tp_mod._tennis_elo = elo
        players = len(elo.ratings) if elo else 0

        return {
            "ok": True,
            "file": filename,
            "size_mb": round(size_mb, 2),
            "players_trained": players,
            "message": f"Tennis Elo trained on {players} players. Predictions will now use this model.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/model-status")
async def model_status():
    """Return status of all trained models."""
    status: Dict[str, Any] = {}

    # Football
    status["football"] = {
        "ready": _predictor is not None,
        "predictions": len(_predictions_cache),
    }

    # Basketball
    try:
        from basketball_predictor import get_basketball_elo
        belo = get_basketball_elo()
        status["basketball"] = {
            "ready": belo is not None,
            "teams": len(belo.ratings) if belo else 0,
            "games": sum(belo.games_played.values()) // 2 if belo else 0,
        }
    except Exception:
        status["basketball"] = {"ready": False, "teams": 0}

    # Tennis
    try:
        from tennis_predictor import get_tennis_elo
        telo = get_tennis_elo()
        status["tennis"] = {
            "ready": telo is not None,
            "players": len(telo.ratings) if telo else 0,
            "matches": sum(telo.matches.values()) // 2 if telo else 0,
        }
    except Exception:
        status["tennis"] = {"ready": False, "players": 0}

    return status


@app.get("/api/debug/calendar-status")
async def debug_calendar_status():
    """Quick diagnostic: shows what the calendar will return and what's in cache."""
    from datetime import date as _date
    month = _date.today().strftime("%Y-%m")
    r = _get_redis()
    cache_dates = sorted({p.get("date", p.get("Date",""))[:10] for p in _predictions_cache if p.get("date") or p.get("Date")})
    redis_keys = []
    if r:
        try:
            redis_keys = [k.decode() if isinstance(k, bytes) else k
                          for k in r.keys("betiq:history:*")]
        except Exception:
            pass
    return {
        "current_month": month,
        "predictions_in_cache": len(_predictions_cache),
        "prediction_dates": cache_dates[:10],
        "history_redis_keys": sorted(redis_keys)[:20],
        "sample_prediction_keys": list(_predictions_cache[0].keys()) if _predictions_cache else [],
    }


@app.get("/api/debug/fixtures")
async def debug_fixtures(league: str = "WC", days: int = 90):
    """
    Diagnostic: hit football-data.org directly for one competition and report
    what actually comes back — HTTP reachability, per-status counts, a sample,
    and how many survive our upcoming-fixtures filter. Use this to tell apart
    "no API access" vs "matches all TBD/finished" vs "working".
    Example: /api/debug/fixtures?league=WC
    """
    from datetime import date as _date, timedelta as _td
    league = league.upper()
    out: Dict = {
        "league": league,
        "known_league": league in LEAGUES,
        "api_key_set": bool(API_KEY),
        "date_from": str(_date.today()),
        "date_to": str(_date.today() + _td(days=days)),
    }
    if not API_KEY:
        out["error"] = "FOOTBALL_DATA_API_KEY not set on the server"
        return out

    client = FootballDataClient(API_KEY)
    raw = await client.fetch_matches_raw(league, days)
    if raw is None:
        out["error"] = ("football-data.org returned no data — the competition may "
                        "not be available on this API key's plan (or it was rate-limited).")
        out["raw_matches"] = 0
        return out

    matches = raw.get("matches", []) or []
    status_counts: Dict[str, int] = {}
    tbd = 0
    _SKIP = {"tbd", "tba", "to be announced", "", "none"}
    for m in matches:
        st = m.get("status", "UNKNOWN")
        status_counts[st] = status_counts.get(st, 0) + 1
        hn = (m.get("homeTeam", {}).get("name") or "").strip().lower()
        an = (m.get("awayTeam", {}).get("name") or "").strip().lower()
        if hn in _SKIP or an in _SKIP:
            tbd += 1

    # What our real fetch_upcoming would return after filtering
    upcoming = await client.fetch_upcoming(league, days)

    out.update({
        "competition": raw.get("competition", {}).get("name"),
        "raw_matches": len(matches),
        "status_breakdown": status_counts,
        "matches_with_tbd_teams": tbd,
        "fixtures_after_filter": len(upcoming),
        "sample": [
            {
                "home": m.get("homeTeam", {}).get("name"),
                "away": m.get("awayTeam", {}).get("name"),
                "date": m.get("utcDate", "")[:10],
                "status": m.get("status"),
            }
            for m in matches[:8]
        ],
    })
    return out


@app.get("/api/explain")
async def explain_match(home: str, away: str):
    """
    Generate a plain-language AI explanation for a match prediction.
    Combines XGBoost/Elo stats with live web search for injuries & lineups.
    Results are cached in Redis for 1 hour to avoid redundant API calls.
    """
    from llm_service import explain_match as _explain

    cache_key = f"betiq:explain:{home.lower()}:{away.lower()}"

    # Return cached explanation if available
    r = _get_redis()
    if r:
        try:
            cached = r.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

    if _predictor is None:
        raise HTTPException(status_code=503, detail="Model not ready")

    analysis   = _predictor.predict_match_full(home, away)
    prediction = _predictor.predict_match(home, away)

    if not analysis or not prediction:
        raise HTTPException(status_code=404, detail="Could not generate prediction")

    result = await _explain(home, away, analysis, prediction)

    # Cache for 1 hour
    if r and result.get("explanation"):
        try:
            r.set(cache_key, json.dumps(result), ex=3600)
        except Exception:
            pass

    # Track usage count in Redis
    r = _get_redis()
    if r:
        try:
            today = date.today().isoformat()
            r.incr(f"betiq:stats:explain:{today}")
            r.expire(f"betiq:stats:explain:{today}", 86400 * 7)
            r.incr("betiq:stats:explain:total")
        except Exception:
            pass

    return result


def _check_admin(secret: str):
    if not ADMIN_SECRET or secret.strip() != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")


@app.get("/api/admin/stats")
async def admin_stats(secret: str = ""):
    _check_admin(secret)
    r = _get_redis()
    if not r:
        return {"error": "no_redis"}

    from datetime import timedelta
    today = date.today()
    daily = []
    overall = {"won": 0, "lost": 0, "pending": 0}
    league_stats: Dict[str, Any] = {}
    tip_stats: Dict[str, Any] = {}
    all_preds: List[Dict] = []

    for i in range(30):
        d = (today - timedelta(days=i)).isoformat()
        try:
            raw = r.get(f"betiq:history:{d}")
            if not raw:
                continue
            preds = json.loads(raw)
            won     = sum(1 for p in preds if p.get("outcome") == "won")
            lost    = sum(1 for p in preds if p.get("outcome") == "lost")
            pending = sum(1 for p in preds if p.get("outcome") == "pending")
            daily.append({
                "date": d, "won": won, "lost": lost, "pending": pending,
                "accuracy": round(won / (won + lost) * 100, 1) if (won + lost) > 0 else None
            })
            overall["won"]     += won
            overall["lost"]    += lost
            overall["pending"] += pending
            all_preds.extend(preds)
        except Exception:
            pass

    daily.reverse()

    for p in all_preds:
        if p.get("outcome") not in ("won", "lost"):
            continue
        lg = p.get("league", "?")
        league_stats.setdefault(lg, {"won": 0, "lost": 0, "name": p.get("league_name", lg), "flag": p.get("flag", "")})
        league_stats[lg]["won" if p["outcome"] == "won" else "lost"] += 1
        tip = p.get("tip_1x2", "?")
        tip_stats.setdefault(tip, {"won": 0, "lost": 0})
        tip_stats[tip]["won" if p["outcome"] == "won" else "lost"] += 1

    for lg in league_stats:
        t = league_stats[lg]["won"] + league_stats[lg]["lost"]
        league_stats[lg]["accuracy"] = round(league_stats[lg]["won"] / t * 100, 1) if t else 0
        league_stats[lg]["total"] = t
    for tip in tip_stats:
        t = tip_stats[tip]["won"] + tip_stats[tip]["lost"]
        tip_stats[tip]["accuracy"] = round(tip_stats[tip]["won"] / t * 100, 1) if t else 0
        tip_stats[tip]["total"] = t

    settled = overall["won"] + overall["lost"]
    overall["accuracy"] = round(overall["won"] / settled * 100, 1) if settled else 0

    # AI usage
    today_str = today.isoformat()
    explain_today = int(r.get(f"betiq:stats:explain:{today_str}") or 0)
    explain_total = int(r.get("betiq:stats:explain:total") or 0)
    chat_queries  = r.lrange("betiq:stats:chat_queries", 0, 19) if hasattr(r, "lrange") else []

    return {
        "overall": overall,
        "daily": daily,
        "by_league": league_stats,
        "by_tip": tip_stats,
        "ai": {"explain_today": explain_today, "explain_total": explain_total, "recent_queries": chat_queries},
    }


@app.get("/api/admin/revenue")
async def admin_revenue(secret: str = ""):
    _check_admin(secret)
    paystack_key = os.getenv("PAYSTACK_SECRET_KEY", "")
    if not paystack_key:
        return {"error": "no_paystack_key"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://api.paystack.co/transaction?status=success&perPage=100",
                headers={"Authorization": f"Bearer {paystack_key}"},
            )
        if r.status_code != 200:
            return {"error": f"paystack_{r.status_code}"}
        data = r.json().get("data", [])
        total = sum(t.get("amount", 0) for t in data) / 100
        return {
            "total_revenue": round(total, 2),
            "transaction_count": len(data),
            "recent": [
                {
                    "email": t.get("customer", {}).get("email", ""),
                    "amount": t.get("amount", 0) / 100,
                    "date": (t.get("paid_at") or "")[:10],
                    "reference": t.get("reference", ""),
                }
                for t in data[:10]
            ],
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/admin/banner")
async def get_banner():
    r = _get_redis()
    if r:
        try:
            val = r.get("betiq:config:banner")
            if val:
                return {"banner": val}
        except Exception:
            pass
    return {"banner": None}


@app.post("/api/admin/banner")
async def set_banner(body: Dict[str, Any]):
    _check_admin(body.get("secret", ""))
    r = _get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    text = (body.get("text") or "").strip()
    if text:
        r.set("betiq:config:banner", text)
    else:
        r.delete("betiq:config:banner")
    return {"banner": text or None}


@app.get("/api/config/maintenance")
async def get_maintenance():
    r = _get_redis()
    if r:
        try:
            val = r.get("betiq:config:maintenance")
            if val is not None:
                return {"enabled": val == "true"}
        except Exception:
            pass
    return {"enabled": False}


@app.post("/api/config/maintenance")
async def set_maintenance(body: Dict[str, Any]):
    _check_admin(body.get("secret", ""))
    r = _get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    enabled = bool(body.get("enabled", False))
    r.set("betiq:config:maintenance", "true" if enabled else "false")
    return {"enabled": enabled}


@app.post("/api/admin/clear-cache")
async def clear_cache(body: Dict[str, Any]):
    _check_admin(body.get("secret", ""))
    r = _get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    try:
        r.delete("betiq:predictions")
        # Clear all explanation caches
        for key in r.scan_iter("betiq:explain:*"):
            r.delete(key)
        return {"cleared": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/featured")
async def get_featured():
    r = _get_redis()
    if r:
        try:
            val = r.get("betiq:config:featured")
            if val:
                return {"featured": json.loads(val)}
        except Exception:
            pass
    return {"featured": []}


@app.post("/api/admin/featured")
async def set_featured(body: Dict[str, Any]):
    _check_admin(body.get("secret", ""))
    r = _get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    picks = body.get("picks", [])[:3]
    if picks:
        r.set("betiq:config:featured", json.dumps(picks))
    else:
        r.delete("betiq:config:featured")
    return {"featured": picks}


@app.post("/api/log/query")
async def log_query(body: Dict[str, Any]):
    """Log a chat query for admin analytics (called from Next.js chat route)."""
    query = (body.get("query") or "").strip()[:200]
    if not query:
        return {"ok": True}
    r = _get_redis()
    if r:
        try:
            r.lpush("betiq:stats:chat_queries", query)
            r.ltrim("betiq:stats:chat_queries", 0, 99)
        except Exception:
            pass
    return {"ok": True}


# ------------------------------------------------------------------ #
# User data (saves, bets, accumulators, stats)
# ------------------------------------------------------------------ #

def _ukey(uid: str, suffix: str) -> str:
    return f"betiq:user:{uid}:{suffix}"


@app.get("/api/user/saves")
async def get_saves(uid: str):
    r = _get_redis()
    if not r: return []
    raw = r.get(_ukey(uid, "saves"))
    return json.loads(raw) if raw else []


@app.post("/api/user/saves")
async def toggle_save(body: Dict[str, Any]):
    uid = body.get("uid", "")
    pred = body.get("prediction", {})
    if not uid or not pred: raise HTTPException(status_code=400, detail="Missing uid or prediction")
    r = _get_redis()
    if not r: raise HTTPException(status_code=503, detail="No Redis")
    key = _ukey(uid, "saves")
    raw = r.get(key)
    saves: List[Dict] = json.loads(raw) if raw else []
    match_key = f"{pred.get('home')}:{pred.get('away')}:{pred.get('date')}"
    existing = next((i for i, s in enumerate(saves) if f"{s.get('home')}:{s.get('away')}:{s.get('date')}" == match_key), None)
    if existing is not None:
        saves.pop(existing)
        saved = False
    else:
        saves.insert(0, pred)
        saves = saves[:50]
        saved = True
    r.set(key, json.dumps(saves), ex=365 * 86400)
    return {"saved": saved, "count": len(saves)}


@app.get("/api/user/bets")
async def get_bets(uid: str):
    r = _get_redis()
    if not r: return []
    raw = r.get(_ukey(uid, "bets"))
    return json.loads(raw) if raw else []


@app.post("/api/user/bets")
async def log_bet(body: Dict[str, Any]):
    uid = body.get("uid", "")
    bet = body.get("bet", {})
    if not uid or not bet: raise HTTPException(status_code=400, detail="Missing uid or bet")
    r = _get_redis()
    if not r: raise HTTPException(status_code=503, detail="No Redis")
    key = _ukey(uid, "bets")
    raw = r.get(key)
    bets: List[Dict] = json.loads(raw) if raw else []
    bet["logged_at"] = datetime.utcnow().isoformat()
    bets.insert(0, bet)
    bets = bets[:200]
    r.set(key, json.dumps(bets), ex=365 * 86400)
    # Update leaderboard if won
    if bet.get("result") == "won":
        r.zincrby("betiq:leaderboard", 1, uid)
    return {"ok": True, "total_bets": len(bets)}


@app.get("/api/user/codes")
async def get_codes(uid: str):
    r = _get_redis()
    if not r: return []
    raw = r.get(_ukey(uid, "codes"))
    return json.loads(raw) if raw else []


@app.post("/api/user/codes")
async def save_code(body: Dict[str, Any]):
    uid = body.get("uid", "")
    entry = body.get("entry", {})
    if not uid or not entry: raise HTTPException(status_code=400, detail="Missing uid or entry")
    r = _get_redis()
    if not r: raise HTTPException(status_code=503, detail="No Redis")
    key = _ukey(uid, "codes")
    raw = r.get(key)
    codes: List[Dict] = json.loads(raw) if raw else []
    entry["saved_at"] = datetime.utcnow().isoformat()
    codes.insert(0, entry)
    codes = codes[:100]
    r.set(key, json.dumps(codes), ex=365 * 86400)
    return {"ok": True}


@app.get("/api/user/stats")
async def get_user_stats(uid: str):
    r = _get_redis()
    if not r: return {}
    bets_raw  = r.get(_ukey(uid, "bets"))
    saves_raw = r.get(_ukey(uid, "saves"))
    codes_raw = r.get(_ukey(uid, "codes"))
    bets:  List[Dict] = json.loads(bets_raw)  if bets_raw  else []
    saves: List[Dict] = json.loads(saves_raw) if saves_raw else []
    codes: List[Dict] = json.loads(codes_raw) if codes_raw else []

    won   = sum(1 for b in bets if b.get("result") == "won")
    lost  = sum(1 for b in bets if b.get("result") == "lost")
    void  = sum(1 for b in bets if b.get("result") == "void")
    total_stake   = sum(float(b.get("stake", 0))  for b in bets)
    total_return  = sum(float(b.get("payout", 0)) for b in bets)
    roi = round((total_return - total_stake) / total_stake * 100, 1) if total_stake > 0 else 0

    # Current streak
    streak, streak_type = 0, None
    for b in bets:
        res = b.get("result")
        if res not in ("won", "lost"): continue
        if streak_type is None: streak_type = res
        if res == streak_type: streak += 1
        else: break

    return {
        "won": won, "lost": lost, "void": void,
        "total_stake": round(total_stake, 2),
        "total_return": round(total_return, 2),
        "roi": roi,
        "accuracy": round(won / (won + lost) * 100, 1) if (won + lost) > 0 else 0,
        "streak": streak, "streak_type": streak_type,
        "saved_count": len(saves),
        "codes_count": len(codes),
    }


@app.get("/api/user/prefs")
async def get_prefs(uid: str):
    r = _get_redis()
    if not r: return {}
    raw = r.get(_ukey(uid, "prefs"))
    return json.loads(raw) if raw else {"followed_leagues": [], "digest": False}


@app.post("/api/user/prefs")
async def set_prefs(body: Dict[str, Any]):
    uid = body.get("uid", "")
    if not uid: raise HTTPException(status_code=400, detail="Missing uid")
    r = _get_redis()
    if not r: raise HTTPException(status_code=503, detail="No Redis")
    prefs = {k: v for k, v in body.items() if k != "uid"}
    r.set(_ukey(uid, "prefs"), json.dumps(prefs), ex=365 * 86400)
    return {"ok": True}


@app.get("/api/sports/{sport}/leagues")
async def get_sport_leagues(sport: str):
    """Return distinct leagues/tournaments being predicted for a sport."""
    import json as _json
    cache_key = f"betiq:sports:{sport}"
    r = _get_redis()
    preds = []
    if r:
        try:
            cached = r.get(cache_key)
            if cached:
                preds = _json.loads(cached)
        except Exception:
            pass
    leagues = sorted({p.get("league_name","") or p.get("league","") for p in preds if p.get("league_name") or p.get("league")})
    return {"sport": sport, "leagues": leagues, "total_predictions": len(preds)}


@app.get("/api/sports/{sport}")
async def get_sport_predictions(sport: str):
    """
    Multi-sport predictions endpoint.
    sport: basketball | tennis | table-tennis
    Requires ODDS_API_KEY env var.
    """
    from sports_fetcher import (
        fetch_basketball_predictions,
        fetch_tennis_predictions,
        fetch_table_tennis_predictions,
    )
    import json as _json

    CACHE_TTL = 3600  # 1 hour — conserves Odds API quota
    cache_key = f"betiq:sports:{sport}"
    r = _get_redis()

    if r:
        try:
            cached = r.get(cache_key)
            if cached:
                return _json.loads(cached)
        except Exception:
            pass

    if sport == "basketball":
        data = await fetch_basketball_predictions()
    elif sport == "tennis":
        data = await fetch_tennis_predictions()
    elif sport == "table-tennis":
        data = await fetch_table_tennis_predictions()
    else:
        raise HTTPException(status_code=400, detail=f"Unknown sport: {sport}")

    if r and data:
        try:
            r.setex(cache_key, CACHE_TTL, _json.dumps(data))
        except Exception:
            pass

    return data


@app.get("/api/sports/{sport}/event")
async def get_sport_event_detail(sport: str, home: str, away: str, date: str):
    """
    Full market detail for a specific basketball/tennis/table-tennis match.
    Used by the sport analysis modal.
    """
    from sports_fetcher import fetch_event_detail
    import json as _json

    cache_key = f"betiq:sport_event:{sport}:{home}:{away}:{date}"
    r = _get_redis()
    if r:
        try:
            cached = r.get(cache_key)
            if cached:
                return _json.loads(cached)
        except Exception:
            pass

    detail = await fetch_event_detail(sport, home, away, date)
    if not detail:
        raise HTTPException(status_code=404, detail="Event not found")

    if r:
        try:
            r.setex(cache_key, 3600, _json.dumps(detail))
        except Exception:
            pass
    return detail


@app.get("/api/value-bets")
async def get_value_bets():
    """
    Returns predictions where BetIQ's model probability beats SportyBet's
    implied probability by ≥ 3%. Results cached in Redis for 30 min (only when non-empty).
    """
    from odds_fetcher import fetch_odds_for_predictions, compute_value_bets
    import json as _json

    CACHE_KEY = "betiq:value_bets"
    r = _get_redis()

    # Serve from cache only if non-empty result was previously stored
    if r:
        try:
            cached = r.get(CACHE_KEY)
            if cached:
                data = _json.loads(cached)
                if data:  # don't serve empty cache — always retry if previously empty
                    return data
        except Exception:
            pass

    preds = _predictions_cache
    print(f"[ValueBets] {len(preds)} predictions in cache")
    if not preds:
        return []

    # Log sample dates so we know what we're working with
    dates = sorted({p.get("date", "") for p in preds if p.get("date")})
    print(f"[ValueBets] Prediction dates: {dates[:5]}")

    try:
        odds_index = await fetch_odds_for_predictions(preds)
        value_bets = compute_value_bets(preds, odds_index)
        print(f"[ValueBets] Found {len(value_bets)} value bets from {len(odds_index)} matched events")

        if r and value_bets:  # only cache non-empty results
            try:
                r.setex(CACHE_KEY, 1800, _json.dumps(value_bets))
            except Exception:
                pass

        return value_bets
    except Exception as e:
        print(f"[ValueBets] Error: {e}")
        return []


@app.get("/api/leaderboard")
async def get_leaderboard():
    r = _get_redis()
    if not r: return []
    try:
        entries = r.zrevrange("betiq:leaderboard", 0, 19, withscores=True)
        return [{"uid": uid, "wins": int(score)} for uid, score in entries]
    except Exception:
        return []


@app.post("/api/track/match")
async def track_match(body: Dict[str, Any]):
    home = body.get("home", "")
    away = body.get("away", "")
    if not home or not away: return {"ok": True}
    r = _get_redis()
    if r:
        try: r.zincrby("betiq:stats:matches", 1, f"{home} vs {away}")
        except Exception: pass
    return {"ok": True}


@app.post("/api/track/league")
async def track_league(body: Dict[str, Any]):
    league = body.get("league", "")
    if not league: return {"ok": True}
    r = _get_redis()
    if r:
        try: r.zincrby("betiq:stats:leagues", 1, league)
        except Exception: pass
    return {"ok": True}


@app.get("/api/admin/popular")
async def get_popular(secret: str = ""):
    _check_admin(secret)
    r = _get_redis()
    if not r: return {"matches": [], "leagues": []}
    try:
        matches = r.zrevrange("betiq:stats:matches", 0, 9, withscores=True)
        leagues = r.zrevrange("betiq:stats:leagues", 0, 9, withscores=True)
        return {
            "matches": [{"name": n, "clicks": int(s)} for n, s in matches],
            "leagues": [{"code": c, "clicks": int(s)} for c, s in leagues],
        }
    except Exception:
        return {"matches": [], "leagues": []}


@app.get("/api/referral/stats")
async def get_referral_stats(uid: str):
    r = _get_redis()
    if not r: return {"count": 0}
    code = f"ref_{uid[-8:]}"
    count = int(r.get(f"betiq:referral:{code}:count") or 0)
    return {"code": code, "count": count, "link": f"https://predict-withbetiq.vercel.app?ref={code}"}


@app.post("/api/referral/use")
async def use_referral(body: Dict[str, Any]):
    """Called when a new user signs up with a referral code."""
    code = body.get("code", "").strip()
    if not code: return {"ok": False}
    r = _get_redis()
    if r:
        try: r.incr(f"betiq:referral:{code}:count")
        except Exception: pass
    return {"ok": True}


@app.get("/api/debug/odds-sample")
async def debug_odds_sample():
    """
    Returns raw SportyBet events for the first prediction date we have cached,
    so we can verify the odds extraction is working.
    """
    from sportybet import fetch_events_for_date
    from datetime import date

    # Use first prediction date if available, otherwise today
    dates = sorted({p.get("date","") for p in (_predictions_cache or []) if p.get("date")})
    target = dates[0] if dates else date.today().isoformat()

    results = {}
    for d in (dates[:5] if dates else [target]):
        evs = await fetch_events_for_date(d)
        results[d] = len(evs)

    # Also clear value bets cache so next request recomputes
    r = _get_redis()
    if r:
        try: r.delete("betiq:value_bets")
        except: pass

    return {
        "all_prediction_dates": dates[:10],
        "events_per_date": results,
        "note": "Value bets Redis cache cleared — refresh /api/value-bets now",
    }


@app.get("/api/sportybet-event")
async def get_sportybet_event(home: str, away: str, date: str):
    """
    Fetch the live SportyBet event for a specific match and return its full
    market/outcome structure with real IDs — used by the in-modal bet picker.
    """
    from sportybet import fetch_events_for_date, find_event

    CACHE_KEY = f"betiq:sb_event:{home}:{away}:{date}"
    r = _get_redis()
    if r:
        try:
            cached = r.get(CACHE_KEY)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

    try:
        events = await fetch_events_for_date(date)
        event = find_event(home, away, events)
        if not event:
            return {"found": False, "markets": []}

        # Build a clean market list from the raw event
        clean_markets = []
        for m in (event.get("markets") or []):
            mid = str(m.get("id") or "")
            mname = m.get("name") or m.get("desc") or ""
            specifier = m.get("specifier") or ""
            outcomes = []
            for o in (m.get("outcomes") or []):
                if not o.get("isActive", 1):
                    continue
                outcomes.append({
                    "id":    str(o.get("id") or ""),
                    "desc":  o.get("desc") or o.get("name") or "",
                    "odds":  str(o.get("odds") or o.get("value") or ""),
                })
            if outcomes:
                clean_markets.append({
                    "id":        mid,
                    "name":      mname,
                    "specifier": specifier,
                    "outcomes":  outcomes,
                })

        result = {
            "found":      True,
            "eventId":    str(event.get("eventId") or event.get("id") or ""),
            "gameId":     str(event.get("gameId") or ""),
            "homeTeam":   event.get("homeTeamName") or home,
            "awayTeam":   event.get("awayTeamName") or away,
            "markets":    clean_markets,
        }

        if r and result["found"]:
            try:
                r.setex(CACHE_KEY, 1800, json.dumps(result))
            except Exception:
                pass

        return result
    except Exception as e:
        print(f"[SportyBet Event] {home} vs {away}: {e}")
        return {"found": False, "markets": [], "error": str(e)}


@app.post("/api/booking")
async def create_booking(body: Dict[str, Any]):
    """
    Generate a SportyBet booking code.
    Mode A (direct): body = { "selections": [{matchId, marketId, outcomeId, ...}] }
               — uses real IDs from /api/sportybet-event, skips fuzzy matching
    Mode B (predictions): body = { "predictions": [{home, away, date, tip_code, ...}] }
               — does fuzzy matching via sportybet.py then falls back to 1xBet
    """
    from sportybet import post_booking, generate_booking_code as sportybet_code
    from onexbet import generate_booking_code as onexbet_code

    # ── Mode A: direct selections with real SportyBet IDs ─────────────────
    direct_selections = body.get("selections", [])
    if direct_selections:
        code = await post_booking(direct_selections)
        matched = [
            {"game": f"{s.get('homeTeamName','?')} vs {s.get('awayTeamName','?')}",
             "tip":  s.get("outcomeName", ""),
             "odds": s.get("odds", "")}
            for s in direct_selections
        ]
        picks = [{"home": s.get("homeTeamName",""), "away": s.get("awayTeamName",""),
                  "tip": s.get("outcomeName",""), "tip_code": "", "date": "", "league": ""}
                 for s in direct_selections]
        total_odds = None
        try:
            from functools import reduce
            total_odds = round(reduce(lambda a, b: a * b,
                [float(s.get("odds","1")) for s in direct_selections if s.get("odds")]), 2)
        except Exception:
            pass
        return {"code": code, "bookie": "sportybet", "matched": matched,
                "unmatched": [], "total_odds": total_odds, "picks": picks,
                "error": None if code else "SportyBet did not return a code — paste the copy card instead."}

    predictions = body.get("predictions", [])
    if not predictions:
        raise HTTPException(status_code=400, detail="No predictions provided")

    # Always include raw picks so frontend can show copy card regardless of code result
    picks = [
        {
            "home": p.get("home", ""),
            "away": p.get("away", ""),
            "tip": p.get("tip_1x2", p.get("tip_code", "?")),
            "tip_code": p.get("tip_code", "?"),
            "date": p.get("date", ""),
            "league": p.get("league_name", ""),
        }
        for p in predictions
        if p.get("tip_code") in ("1", "X", "2")
    ]

    # 1. Try SportyBet
    try:
        result = await sportybet_code(predictions)
        if result.get("code"):
            return {**result, "bookie": "sportybet", "picks": picks}
    except Exception as e:
        print(f"[Booking] SportyBet error: {e}")

    # 2. Try 1xBet
    try:
        result = await onexbet_code(predictions)
        if result.get("code"):
            return {**result, "bookie": "1xbet", "picks": picks}
    except Exception as e:
        print(f"[Booking] 1xBet error: {e}")

    # 3. No code from either — return picks for copy-card
    return {
        "code": None,
        "bookie": None,
        "matched": [],
        "unmatched": [f"{p['home']} vs {p['away']}" for p in predictions],
        "total_odds": None,
        "picks": picks,
        "error": "Booking APIs unavailable — use the copy card below to add picks manually.",
    }


ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")
PAYWALL_KEY  = "betiq:config:paywall_enabled"


@app.get("/api/config/paywall")
async def get_paywall_state():
    """Public endpoint — returns current paywall on/off state."""
    r = _get_redis()
    if r:
        try:
            val = r.get(PAYWALL_KEY)
            if val is not None:
                return {"enabled": val == "true"}
        except Exception:
            pass
    return {"enabled": False}   # default: paywall OFF when Redis unavailable


@app.post("/api/config/paywall")
async def set_paywall_state(body: Dict[str, Any], request: Any = None):
    """Admin-only endpoint — toggle paywall on or off."""
    from fastapi import Request
    admin_secret = (body.get("secret") or "").strip()
    if not ADMIN_SECRET or admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    enabled = bool(body.get("enabled", True))
    r = _get_redis()
    if r:
        try:
            # No TTL — persists forever in Redis
            r.set(PAYWALL_KEY, "true" if enabled else "false")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Redis error: {e}")

    print(f"[Admin] Paywall {'enabled' if enabled else 'DISABLED'}")
    return {"enabled": enabled}


@app.post("/api/refresh")
async def refresh_predictions(background_tasks: BackgroundTasks):
    background_tasks.add_task(_run_pipeline)
    return {"message": "Refresh started"}


# ------------------------------------------------------------------ #
# Startup
# ------------------------------------------------------------------ #

scheduler = AsyncIOScheduler()


async def _load_fbref_data():
    """Load cards CSV, rebuilding from EPL CSV if missing or >7 days old."""
    global _cards_df
    import time as _time

    needs_scrape = True
    if os.path.exists(CORNERS_CSV):
        age_days = (_time.time() - os.path.getmtime(CORNERS_CSV)) / 86400
        if age_days < 7:
            needs_scrape = False

    if needs_scrape:
        print("[fbref] Data stale or missing — building from EPL CSV...")
        try:
            await scrape_fbref(epl_csv_path=EPL_HISTORY)
        except Exception as e:
            print(f"[fbref] Build failed: {e}")

    _cards_df = load_cards()
    print(f"[fbref] Loaded cards data ({len(_cards_df)} teams)")


@app.on_event("startup")
async def startup():
    _load_h2h_cache()
    _load_predictions_cache()   # serve cached predictions instantly while pipeline rebuilds
    asyncio.create_task(_run_pipeline())
    asyncio.create_task(_load_fbref_data())
    # results fetcher runs via scheduler only — not on boot to avoid API contention with pipeline
    scheduler.add_job(_run_pipeline, "interval", hours=12, id="refresh")
    scheduler.add_job(_load_fbref_data, "interval", days=7, id="fbref_refresh")
    scheduler.add_job(_fetch_and_save_results, "interval", hours=3, id="results_refresh")
    scheduler.start()


@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown()
