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

def _save_predictions_cache():
    payload = json.dumps({"predictions": _predictions_cache, "last_updated": _last_updated})
    # 1. Try Redis (TTL: 8 hours)
    r = _get_redis()
    if r:
        try:
            r.set("betiq:predictions", payload, ex=8 * 3600)
            print(f"[Cache] Saved {len(_predictions_cache)} predictions to Redis.")
        except Exception as e:
            print(f"[Cache] Redis save error: {e}")
    # 2. Also save to disk as fallback
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


# ------------------------------------------------------------------ #
# Train + predict pipeline
# ------------------------------------------------------------------ #

async def _run_pipeline():
    global _predictor, _predictions_cache, _last_updated, _is_training

    if _is_training:
        return
    _is_training = True

    try:
        print("[Pipeline] Loading CSV data...")
        epl_df = _load_epl_csv()
        ucl_df = _load_ucl_csv()

        combined = pd.concat([epl_df, ucl_df], ignore_index=True).sort_values("Date").reset_index(drop=True)
        if combined.empty:
            print("[Pipeline] No training data found!")
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
        predictor = LeaguePredictor()
        predictor.train(combined)

        # Fetch recent results from API to update Elo with current season data
        predictions = []
        if API_KEY:
            client = FootballDataClient(API_KEY)

            print("[Pipeline] Fetching recent API results to calibrate Elo...")
            for code in list(LEAGUES.keys()):
                try:
                    recent = await client.fetch_recent_results(code, days_back=60)
                    if recent.empty or "HomeTeam" not in recent.columns:
                        continue
                    for _, r in recent.iterrows():
                        predictor._update(r["HomeTeam"], r["AwayTeam"], r["Result"], r["FTHG"], r["FTAG"])
                    await asyncio.sleep(10)
                except Exception as e:
                    print(f"[Pipeline] Recent results error for {code}: {e}")

            print("[Pipeline] Fetching upcoming fixtures...")
            fixtures = await client.fetch_all_upcoming(days_ahead=90)

            for fx in fixtures:
                try:
                    tip = predictor.predict_match(fx["home"], fx["away"])
                    if tip:
                        predictions.append({**fx, **tip})
                except Exception:
                    pass
        else:
            print("[Pipeline] WARNING: No FOOTBALL_DATA_API_KEY set. Add your key to .env to get live fixtures.")

        _predictor = predictor
        _predictions_cache = predictions
        _last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        _save_predictions_cache()
        print(f"[Pipeline] Done — {len(predictions)} predictions cached.")

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


# ------------------------------------------------------------------ #
# Routes
# ------------------------------------------------------------------ #

@app.get("/api/health")
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


@app.get("/api/analysis")
async def get_match_analysis(home: str, away: str):
    if _predictor is None:
        raise HTTPException(status_code=503, detail="Model not ready yet")

    result = _predictor.predict_match_full(home, away)
    if result is None:
        raise HTTPException(status_code=404, detail="Could not generate analysis")

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

    return result


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


@app.post("/api/booking")
async def create_booking(body: Dict[str, Any]):
    """
    Generate a SportyBet booking code from a list of BetIQ predictions.
    Body: { "predictions": [{home, away, date, tip_code, ...}, ...] }
    """
    from sportybet import generate_booking_code
    predictions = body.get("predictions", [])
    if not predictions:
        raise HTTPException(status_code=400, detail="No predictions provided")
    result = await generate_booking_code(predictions)
    return result


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
