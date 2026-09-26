"""
FastAPI backend for Sport Bet Predictions.
- Trains on historical EPL + UCL CSV data (for model accuracy)
- Fetches ALL upcoming fixtures live from football-data.org API
- Serves predictions via REST API
- Refreshes every 6 hours via APScheduler
"""

import asyncio
import os
import re
import time
import glob
import json
from datetime import datetime, date, timedelta, timezone
from typing import List, Dict, Any, Optional, Tuple

import httpx
import numpy as np
import pandas as pd
from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException, Query, Request
from auth import auth_enforced, optional_user, require_user
from grading import grade_prediction, regrade, to_goals
from team_names import UCL_ALIASES, TeamResolver
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from predictor import LeaguePredictor
from data_fetcher import FootballDataClient, LEAGUES, API_BASE, NOT_IN_PLAN
import international_fixtures as intl
import security
import traffic
import set_pieces
import shots
from scrapers.fbref import load_cards, load_corners, refresh as scrape_fbref, CORNERS_CSV, CARDS_CSV

load_dotenv()

API_KEY = os.getenv("FOOTBALL_DATA_API_KEY", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
EPL_HISTORY = os.getenv("EPL_HISTORY_CSV", "../data/epl-final.csv")
UCL_CSV_PATTERN = os.getenv("UCL_CSV", "../data/champions-league-*.csv")
# The CSVs moved from the repository root to data/: settings that still
# point at the root fall back to the new place
if not os.path.exists(EPL_HISTORY):
    EPL_HISTORY = "../data/epl-final.csv"
if not glob.glob(UCL_CSV_PATTERN):
    UCL_CSV_PATTERN = "../data/champions-league-*.csv"
REDIS_URL = os.getenv("UPSTASH_REDIS_URL", "")
H2H_CACHE_FILE = os.path.join("data", "h2h_cache.json")
H2H_TTL_DAYS = 7
PREDICTIONS_CACHE_FILE = os.path.join("data", "predictions_cache.json")
RESULTS_CSV = os.path.join("data", "recent_results.csv")
# Only fixtures within this many days from today are fetched and predicted
PREDICTION_DAYS = int(os.getenv("PREDICTION_DAYS", "14"))


def _within_window(match_date: str, today: Optional[date] = None) -> bool:
    """True unless the fixture is further out than PREDICTION_DAYS."""
    last = (today or date.today()) + timedelta(days=PREDICTION_DAYS)
    return not match_date or match_date <= last.isoformat()

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

# Admins: Clerk user ids (comma-separated) whose session token grants admin,
# so the admin page needs no shared secret in the browser. ADMIN_SECRET
# still works for scripts, but only in the X-Admin-Secret header — never in
# a URL (URLs end up in server logs and browser history) or a request body.
ADMIN_USER_IDS = {u.strip() for u in os.getenv("ADMIN_USER_IDS", "").split(",") if u.strip()}


async def _admin_identity(request: Request):
    """(how this request is an admin — "secret" / "clerk" — or None,
    the signed-in Clerk user id if any)."""
    import hmac
    import auth
    given = request.headers.get("x-admin-secret", "").strip()
    if given:
        # A wrong secret counts towards a lockout; while locked out, even the
        # right one is refused (it stops guessing, not the real admin for long)
        if security.admin_locked(request):
            raise HTTPException(status_code=429, detail="Too many wrong admin secrets — try again later.")
        if ADMIN_SECRET and hmac.compare_digest(given.encode(), ADMIN_SECRET.encode()):
            return "secret", None
        security.admin_failed(request)
    uid = await auth.optional_user(request)
    if uid and uid in ADMIN_USER_IDS:
        return "clerk", uid
    return None, uid


async def require_admin(request: Request) -> str:
    """The admin making this request, for the audit log: "secret" or "clerk:<user id>"."""
    via, uid = await _admin_identity(request)
    if not via:
        raise HTTPException(status_code=403, detail="Forbidden")
    return f"clerk:{uid}" if via == "clerk" else via


AUDIT_KEY = "betiq:admin:audit"


def _audit(actor: str, action: str, **detail) -> None:
    """Record an admin action (who, what, when) for the admin panel's log."""
    r = _get_redis()
    if not r:
        return
    try:
        r.lpush(AUDIT_KEY, json.dumps({"at": int(time.time()), "actor": actor, "action": action,
                                       **{k: v for k, v in detail.items() if v is not None}}, default=str))
        r.ltrim(AUDIT_KEY, 0, 199)
    except Exception:
        pass


async def require_premium(request: Request) -> Optional[str]:
    """Premium content (match analysis, AI explanation): a signed-in premium
    user, an admin, or anyone while the paywall is switched off. Not enforced
    until CLERK_ISSUER and CLERK_SECRET_KEY are both set (logged once)."""
    import auth
    if not _paywall_enabled():
        return None
    if not auth.premium_enforced():
        global _warned_premium
        if not _warned_premium:
            print("[Auth] WARNING: premium content isn't protected — set CLERK_ISSUER and "
                  "CLERK_SECRET_KEY on the backend.")
            _warned_premium = True
        return None
    via, uid = await _admin_identity(request)
    if via:
        return uid
    if not uid:
        raise HTTPException(status_code=401, detail="Sign in to see this.")
    if not await auth.user_is_premium(uid):
        raise HTTPException(status_code=402, detail="premium_required")
    return uid


_warned_premium = False


def _paywall_enabled() -> bool:
    r = _get_redis()
    if r:
        try:
            return r.get(PAYWALL_KEY) != "false"
        except Exception:
            pass
    return True

# Browsers may call this API only from our own site (and the app, which
# loads the same site). ALLOWED_ORIGINS adds more, comma-separated.
ALLOWED_ORIGINS = sorted({o.strip().rstrip("/") for o in [
    FRONTEND_URL, "https://predict-withbetiq.vercel.app", "http://localhost:3000",
    *os.getenv("ALLOWED_ORIGINS", "").split(","),
] if o.strip()})

app.add_middleware(security.SecurityMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Secret"],
    max_age=3600,
)
security.configure(lambda: _get_redis())

# --- Global state ---
_predictor: Optional[LeaguePredictor] = None
# The European competitions model (europe_model.py), when its check adopted
# one: Champions League, Europa League and Conference League fixtures
_europe_predictor: Optional[LeaguePredictor] = None
_europe_model_info: Dict[str, Any] = {}
_predictions_cache: List[Dict] = []
_last_updated: Optional[str] = None
_is_training = False
_history_df: Optional[pd.DataFrame] = None
# Every result the server holds, newest first, for the match page's recent
# form and head-to-head (match_facts.py); rebuilt each pipeline run
_results_idx: Optional[pd.DataFrame] = None
# Corners / bookings totals, refitted from the league CSVs every pipeline run
_set_pieces: Optional[set_pieces.SetPieceModel] = None
# The same for internationals (data from collect_international_stats.py),
# per stat only where it beat the competition average on unseen matches
_intl_set_pieces: Optional[set_pieces.SetPieceModel] = None
_intl_sp_info: Dict[str, Any] = {}
# Shots / shots on target totals (shots.py), per stat only where the last
# season's walk-forward check beat the league average
_shots: Optional[shots.ShotModel] = None
_shots_info: Dict[str, Any] = {}
_intl_shots: Optional[shots.ShotModel] = None   # the same for internationals, where their check passed
_cards_df: pd.DataFrame = pd.DataFrame()
_corners_df: pd.DataFrame = pd.DataFrame()

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


def _predictor_form_summary(team: str, model: Optional[LeaguePredictor] = None) -> Dict:
    """
    Return the current rolling form for a team.
    Primary source: predictor.team_stats (built from CSVs + API).
    Fallback: Redis-cached web form (fetched async in pipeline for sparse teams).
    """
    model = model or _predictor
    if model is None:
        return {}

    key   = model.canon(team)
    stats = model.team_stats.get(key, {})
    elo   = round(model.elo.get(key))

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
                _snapshot_matchdays(r)
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
    r = _get_redis()

    # 1. Save main predictions cache (TTL: 8 hours)
    if r:
        try:
            r.set("betiq:predictions", payload, ex=8 * 3600)
            print(f"[Cache] Saved {len(_predictions_cache)} predictions to Redis.")
        except Exception as e:
            print(f"[Cache] Redis save error: {e}")

    # 2. Match days: each match's latest pre-match prediction, locked at
    #    kick-off (graded once the result is in: _refresh_matchdays)
    if r and _predictions_cache:
        try:
            _snapshot_matchdays(r)
        except Exception as e:
            print(f"[MatchDay] Snapshot error: {e}")

    # 3. Disk fallback
    try:
        os.makedirs("data", exist_ok=True)
        with open(PREDICTIONS_CACHE_FILE, "w") as f:
            f.write(payload)
    except Exception as e:
        print(f"[Cache] Disk save error: {e}")

ODDS_FETCH_COOLDOWN_SECONDS = 4 * 3600  # don't re-hit The Odds API more than once per 4h
ODDS_CACHE_FILE = os.path.join("data", "odds_cache.json")


def _load_cached_live_odds() -> tuple:
    """Returns (live_odds_dict, fetched_at_iso_or_None)."""
    r = _get_redis()
    if r:
        try:
            raw = r.get("betiq:odds_cache")
            if raw:
                saved = json.loads(raw)
                return saved.get("live_odds", {}), saved.get("fetched_at")
        except Exception as e:
            print(f"[OddsCache] Redis load error: {e}")
    if os.path.exists(ODDS_CACHE_FILE):
        try:
            with open(ODDS_CACHE_FILE) as f:
                saved = json.load(f)
            return saved.get("live_odds", {}), saved.get("fetched_at")
        except Exception as e:
            print(f"[OddsCache] Disk load error: {e}")
    return {}, None


def _save_cached_live_odds(live_odds: Dict) -> None:
    payload = json.dumps({"live_odds": live_odds, "fetched_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")})
    r = _get_redis()
    if r:
        try:
            r.set("betiq:odds_cache", payload, ex=ODDS_FETCH_COOLDOWN_SECONDS * 3)
        except Exception as e:
            print(f"[OddsCache] Redis save error: {e}")
    try:
        os.makedirs("data", exist_ok=True)
        with open(ODDS_CACHE_FILE, "w") as f:
            f.write(payload)
    except Exception as e:
        print(f"[OddsCache] Disk save error: {e}")


async def _get_live_odds_throttled(stubs: List[Dict]) -> Dict:
    """
    Fetch live 1X2 odds for the given fixtures from The Odds API, but skip the
    call entirely (reusing the last known result) if we fetched within the
    last ODDS_FETCH_COOLDOWN_SECONDS.

    Why this exists: free hosting spins the instance down when idle, and the
    startup handler re-runs the full pipeline on every cold start — which can
    happen many times a day under light/sporadic traffic. Without a cooldown,
    each cold start re-fetches odds for every league from scratch, which
    burns through The Odds API's free 500-requests/month quota fast. Once the
    quota is gone, every odds badge and value bet silently disappears until
    the next monthly reset — and each further cold start keeps trying and
    failing, so nothing self-heals on its own. Throttling the underlying
    fetch (not just caching the HTTP response) fixes this at the source.
    """
    from odds_fetcher import fetch_odds_for_predictions
    from odds_cache import is_within_cooldown

    cached_odds, fetched_at = _load_cached_live_odds()
    if cached_odds and is_within_cooldown(fetched_at, ODDS_FETCH_COOLDOWN_SECONDS):
        print(f"[Pipeline] Reusing cached live odds (cooldown active) — {len(cached_odds)} fixtures.")
        return cached_odds

    live_odds = await fetch_odds_for_predictions(stubs)
    if live_odds:
        _save_cached_live_odds(live_odds)
    elif cached_odds:
        # Fresh fetch came back empty (quota exhausted, API down, etc.) — keep
        # serving the last known-good odds rather than dropping them to zero.
        print("[Pipeline] Live odds fetch returned nothing — falling back to last known odds.")
        return cached_odds
    return live_odds


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

def _load_football_data_csvs(directory: Optional[str] = None) -> pd.DataFrame:
    """
    Load football-data.co.uk CSVs (with Bet365 odds) from data/football/*.csv.
    These files have columns: Date, HomeTeam, AwayTeam, FTHG, FTAG, FTR, B365H, B365D, B365A, HC, AC, HY, AY, HR, AR
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

    csvs = sorted(glob.glob(os.path.join(directory or FOOTBALL_DATA_DIR, "*.csv")))
    csvs = [c for c in csvs if not os.path.basename(c).startswith("new_")]
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
            for col in ["B365H", "B365D", "B365A", "B365>2.5", "B365<2.5", "HS", "AS", "HST", "AST"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

            df["league"] = league_code

            keep = ["Date", "HomeTeam", "AwayTeam", "Result", "FTHG", "FTAG",
                    "B365H", "B365D", "B365A", "B365>2.5", "B365<2.5",  # O/U odds: backtest baseline
                    "HC", "AC", "HY", "AY", "HR", "AR", "HS", "AS", "HST", "AST",  # shots: model features
                    "Referee", "league"]
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


EXTRA_DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "football_extra")
# football-data.co.uk/new/ country files → our league codes
_NEW_LEAGUE_CODES = {"AUT": "AUT", "SWZ": "SUI", "DNK": "DEN", "NOR": "NOR", "SWE": "SWE",
                     "POL": "POL", "ROU": "ROU", "BRA": "BSA"}
EXTRA_SINCE = "2019-07-01"


def _load_new_league_csv(path: str, league: str) -> pd.DataFrame:
    """One football-data.co.uk/new/ file (Home, Away, HG, AG, Res, closing
    odds) in the league CSVs' shape."""
    df = pd.read_csv(path, low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    if not all(c in df.columns for c in ("Date", "Home", "Away", "HG", "AG", "Res")):
        return pd.DataFrame()
    out = pd.DataFrame({
        "Date": pd.to_datetime(df["Date"], dayfirst=True, errors="coerce"),
        "HomeTeam": df["Home"].astype(str).str.strip(), "AwayTeam": df["Away"].astype(str).str.strip(),
        "FTHG": pd.to_numeric(df["HG"], errors="coerce"), "FTAG": pd.to_numeric(df["AG"], errors="coerce"),
        "Result": df["Res"], "league": league,
    })
    for ours, theirs in (("B365H", ("B365CH", "PSCH", "AvgCH")), ("B365D", ("B365CD", "PSCD", "AvgCD")),
                         ("B365A", ("B365CA", "PSCA", "AvgCA"))):
        col = next((c for c in theirs if c in df.columns), None)
        if col:
            out[ours] = pd.to_numeric(df[col], errors="coerce")
    out = out.dropna(subset=["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"])
    return out[out["Result"].isin(["H", "D", "A"])]


MAIN_EXTRA_LEAGUES = {"BSA"}


def _load_extra_leagues(known: Optional[set] = None, leagues: Optional[set] = None) -> pd.DataFrame:
    """The extra leagues (football_data_sync.EXTRA_DIVISIONS / NEW_LEAGUES)
    since EXTRA_SINCE, flagged Context: the model learns their clubs'
    ratings and form from them but doesn't train on them, so its predictions
    for the leagues it trains on don't change (predictor.train). A club
    whose name clashes with one in `known` (the trained leagues' clubs) gets
    its league added — two clubs must never share a history."""
    from team_names import normalise
    parts = []
    main = _load_football_data_csvs(EXTRA_DATA_DIR) if os.path.isdir(EXTRA_DATA_DIR) else pd.DataFrame()
    if not main.empty:
        parts.append(main)
    for path in sorted(glob.glob(os.path.join(EXTRA_DATA_DIR, "new_*.csv"))):
        code = os.path.basename(path)[4:-4]
        try:
            df = _load_new_league_csv(path, _NEW_LEAGUE_CODES.get(code, code))
        except Exception as e:
            print(f"[Data] {os.path.basename(path)}: {e}")
            continue
        if not df.empty:
            parts.append(df)
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    df = df[df["Date"] >= EXTRA_SINCE]
    if leagues is not None:
        df = df[df["league"].isin(leagues)]
    if df.empty:
        return pd.DataFrame()
    clash = {normalise(n) for n in (known or set())}
    for col in ("HomeTeam", "AwayTeam"):
        df[col] = [f"{n} ({lg})" if normalise(n) in clash else n for n, lg in zip(df[col], df["league"])]
    df = df.drop_duplicates(subset=["Date", "HomeTeam", "AwayTeam"]).sort_values("Date").reset_index(drop=True)
    df["Context"] = True
    print(f"[Data] Extra leagues: {len(df)} matches for ratings only "
          f"({', '.join(f'{k} {v}' for k, v in df['league'].value_counts().items())})")
    return df


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
            # Same league tag as live international fixtures (international_fixtures.py)
            "league":   intl.LEAGUE_CODE,
        }).dropna(subset=["Date", "HomeTeam", "AwayTeam", "Result", "FTHG", "FTAG"])

        print(f"[CSV] International results: {len(result)} matches (post-2010)")
        return result.sort_values("Date").reset_index(drop=True)
    except Exception as e:
        print(f"[CSV] International results load error: {e}")
        return pd.DataFrame()


# ------------------------------------------------------------------ #
# Train + predict pipeline
# ------------------------------------------------------------------ #

_MAJOR_TOURNAMENT_HINTS = (
    "world cup", "euro", "afcon", "africa cup", "copa america",
    "nations league", "confederations",
)


def _apply_web_form(predictor, team: str, form: dict) -> None:
    """
    Persist a fetched web form to the 24h Redis cache (so the match page's
    team-form display picks it up) and feed its confirmed results into the
    live predictor's team_stats/Elo, same as locally-sourced results would
    be. Shared by the background prefetch and the on-demand debug endpoint
    so a manual live=true fetch is immediately useful, not just diagnostic.
    """
    _set_web_form_cache(team, form)
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


async def _prefetch_web_forms(predictor, fixtures: list):
    """
    Background task: fetch last-10-match form + xG via Groq web
    search for every team in upcoming fixtures that doesn't already have a
    cached web form, and cache it in Redis for 24 hours.

    Note this is NOT gated on "few local matches" despite team_stats often
    holding plenty of historical W/D/L records for national teams (from the
    international results CSV) — that history has no xG in it at all, so a
    team can have hundreds of local matches and still need this fetch for
    real xG. The only thing that skips a team is already having a cached
    web form.

    Teams playing in a major international tournament right now (World Cup,
    Euros, AFCON, ...) are fetched first — those are exactly the teams with
    the sparsest local history (national teams, limited club-style CSV
    coverage) and where a tournament-aware search matters most, so they
    shouldn't lose their slot in the 25-per-run Groq quota cap to a random
    friendly fixture that happened to be seen first.
    """
    from llm_service import fetch_team_form_web, GROQ_API_KEY
    if not GROQ_API_KEY:
        return

    seen = set()
    sparse_teams = []
    team_competition: Dict[str, str] = {}
    for fx in fixtures:
        league_name = fx.get("league_name", "")
        for team in (fx["home"], fx["away"]):
            if team not in team_competition and league_name:
                team_competition[team] = league_name
            if team in seen:
                continue
            seen.add(team)
            local_pts = len(predictor.team_stats.get(predictor.canon(team), {}).get("pts", []))
            # Already have enough local data AND a cached web form → skip
            if local_pts >= 5 and _get_web_form_cache(team):
                continue
            sparse_teams.append(team)

    if not sparse_teams:
        return

    def _is_major_tournament(team: str) -> bool:
        comp = team_competition.get(team, "").lower()
        return any(hint in comp for hint in _MAJOR_TOURNAMENT_HINTS)

    sparse_teams.sort(key=lambda t: 0 if _is_major_tournament(t) else 1)

    print(f"[WebForm] Fetching form for {len(sparse_teams)} teams with sparse data...")
    for team in sparse_teams[:25]:  # cap at 25 to respect Groq quota
        try:
            cached = _get_web_form_cache(team)
            if cached:
                continue  # already have it
            form = await fetch_team_form_web(team, competition=team_competition.get(team, ""))
            if form and form.get("matches"):
                _apply_web_form(predictor, team, form)
        except Exception as e:
            print(f"[WebForm] Failed for {team}: {e}")
        await asyncio.sleep(7)  # stay within Groq rate limit

    print("[WebForm] Pre-fetch complete.")


# ------------------------------------------------------------------ #

def _pick_confidence(p: Dict) -> float:
    """Probability of a prediction's 1X2 tip; the goals tip's for older cached
    predictions that predate tip_confidence."""
    tc = p.get("tip_confidence")
    return float(tc) if isinstance(tc, (int, float)) else float(p.get("goals_confidence", 0) or 0)


# Fixture clubs the model has no matches for (their predictions rest on
# defaults), by name → league; in the admin data status
_unknown_clubs: Dict[str, str] = {}


def _note_unknown_clubs(model, fx: Dict) -> None:
    """Remember a fixture's clubs the model has no matches for."""
    stats = getattr(model, "team_stats", None)
    if not isinstance(stats, dict) or fx.get("sport") not in (None, "football"):
        return
    canon = getattr(model, "canon", lambda n: n)
    for side in ("home", "away"):
        try:
            if not (stats.get(canon(fx[side])) or {}).get("pts"):
                _unknown_clubs[fx[side]] = fx.get("league", "")
        except Exception:
            pass


def _build_predictions(predictor, fixtures: list, live_odds: dict) -> list:
    """Turn upcoming fixtures + live odds into prediction dicts (with value-bet flags)."""
    predictions = []
    _unknown_clubs.clear()
    for fx in fixtures:
        if not _within_window(fx.get("date", "")):
            continue
        try:
            key = f"{fx['home']}:{fx['away']}:{fx.get('date','')}"
            odds = live_odds.get(key, {})
            model = _europe_predictor if (predictor is _predictor and _europe_covers(fx.get("league", ""))) \
                else predictor
            tip = model.predict_match(
                fx["home"], fx["away"],
                odds_home=float(odds.get("1") or 0),
                odds_draw=float(odds.get("X") or 0),
                odds_away=float(odds.get("2") or 0),
                match_date=fx.get("date"),
                # Internationals are listed per competition but modelled as "INT"
                league=fx.get("model_league") or fx.get("league", ""),
            )
            if not tip:
                continue
            _note_unknown_clubs(model, fx)
            # Value bet detection: model prob vs bookmaker implied prob
            tip_code = tip.get("tip_code", "?")
            if tip_code == "1" and odds.get("1") and float(odds.get("1", 0)) > 1:
                implied = 1.0 / float(odds["1"]); model_p = tip.get("p_home", 0)
            elif tip_code == "X" and odds.get("X") and float(odds.get("X", 0)) > 1:
                implied = 1.0 / float(odds["X"]); model_p = tip.get("p_draw", 0)
            elif tip_code == "2" and odds.get("2") and float(odds.get("2", 0)) > 1:
                implied = 1.0 / float(odds["2"]); model_p = tip.get("p_away", 0)
            else:
                implied = None; model_p = None

            value_edge = round(model_p - implied, 3) if (model_p is not None and implied is not None) else None

            extras, referee = _set_piece_extras(fx, predictor)

            predictions.append({
                **fx, **tip,
                "odds_home": round(float(odds.get("1") or 0), 2) or None,
                "odds_draw": round(float(odds.get("X") or 0), 2) or None,
                "odds_away": round(float(odds.get("2") or 0), 2) or None,
                "value_edge": value_edge,
                "is_value_bet": value_edge is not None and value_edge > 0.05,
                # Still linked from the last run (keys are home|away|date), so a
                # rebuild doesn't hide "bookable" until the next linking run
                "sportybet": _linked_event(fx) is not None,
                **({"set_pieces": extras} if extras else {}),
                **({"referee": referee} if referee else {}),
            })
        except Exception:
            pass
    return predictions


# Current-season CSVs from football-data.co.uk (see football_data_sync.py)
FOOTBALL_DATA_SYNC_HOURS = 20
_football_sync: Dict[str, Any] = {"at": None, "report": None}


async def _sync_football_data(force: bool = False) -> Optional[Dict[str, list]]:
    """Download this and last season's league CSVs and the international
    results, at most once every FOOTBALL_DATA_SYNC_HOURS. Set FOOTBALL_DATA_SYNC=0 to turn off."""
    if os.getenv("FOOTBALL_DATA_SYNC", "1") == "0":
        return None
    last = _football_sync["at"]
    if not force and last and (datetime.now(timezone.utc) - last).total_seconds() < FOOTBALL_DATA_SYNC_HOURS * 3600:
        return None
    from football_data_sync import sync, sync_extra, sync_international
    try:
        report = await sync(FOOTBALL_DATA_DIR)
        for extra in (await sync_international(INTERNATIONAL_CSV), await sync_extra(EXTRA_DATA_DIR)):
            for key, items in extra.items():
                report[key] += items
    except Exception as e:
        print(f"[DataSync] failed: {e}")
        return None
    _football_sync.update(at=datetime.now(timezone.utc), report=report)
    print(f"[DataSync] updated {report['updated'] or 'nothing'}; "
          f"{len(report['unchanged'])} unchanged, {len(report['skipped'])} not published, "
          f"failed {report['failed'] or 'none'}")
    if report["failed"]:
        asyncio.create_task(_retry_football_sync())
    return report


async def _manual_football_sync() -> None:
    """Admin job: download the league files now, and rebuild if any changed."""
    report = await _sync_football_data(force=True)
    if report and report["updated"] and not _is_training:
        await _run_pipeline()


FOOTBALL_DATA_RETRY_MINUTES = 30
FOOTBALL_DATA_RETRIES = 4


async def _retry_football_sync() -> None:
    """Try the league files the sync couldn't download again, every 30
    minutes (up to 4 times) rather than waiting a day; rebuild the
    predictions when one comes through."""
    from football_data_sync import failed_files, sync
    for _ in range(FOOTBALL_DATA_RETRIES):
        todo = failed_files(_football_sync["report"] or {})
        if not todo:
            return
        await asyncio.sleep(FOOTBALL_DATA_RETRY_MINUTES * 60)
        retried = {"updated": [], "unchanged": [], "skipped": [], "failed": []}
        for div, season in todo:
            try:
                got = await sync(FOOTBALL_DATA_DIR, divisions=[div], seasons=[season])
            except Exception as e:
                got = {"updated": [], "unchanged": [], "skipped": [], "failed": [f"{div}_{season}.csv: {e}"]}
            for k in retried:
                retried[k] += got[k]
        report = _football_sync["report"]
        names = {f"{d}_{s}.csv" for d, s in todo}
        report["failed"] = [f for f in report["failed"] if f.split(":", 1)[0] not in names] + retried["failed"]
        for k in ("updated", "unchanged", "skipped"):
            report[k] = report[k] + retried[k]
        print(f"[DataSync] retry: updated {retried['updated'] or 'nothing'}, still failing {retried['failed'] or 'none'}")
        if retried["updated"] and not _is_training:
            asyncio.create_task(_run_pipeline())


_intl_status: Dict[str, Any] = {"at": None, "fixtures": 0, "sources": {}, "errors": []}


# {international league code: badge URL}, captured from the fixture sources
# (ESPN, SofaScore) and kept in Redis so restarts keep them
INTL_LOGOS_KEY = "betiq:intl:competition_logos"
_intl_logos: Dict[str, str] = {}


def _remember_competition_logos(fixtures: list) -> None:
    found = intl.competition_logos(fixtures)
    if not found:
        return
    _intl_logos.update(found)
    r = _get_redis()
    if r:
        try:
            r.hset(INTL_LOGOS_KEY, mapping=found)
        except Exception as e:
            print(f"[International] Could not save competition logos: {e}")


def _international_competition_logo(name: str) -> Optional[str]:
    """The captured badge for one of our international competitions, by the
    display name the site shows ("AFCON Qualifiers"), else None."""
    code = {display: c for c, display, _, _ in intl.COMPETITIONS}.get(name)
    if code is None and name == intl.LEAGUE_INFO["name"]:
        code = intl.LEAGUE_CODE
    if code is None:
        return None
    if code not in _intl_logos:
        r = _get_redis()
        if r:
            try:
                _intl_logos.update(r.hgetall(INTL_LOGOS_KEY) or {})
            except Exception:
                pass
    return _intl_logos.get(code)


async def _fetch_international_fixtures() -> list:
    """Upcoming national-team fixtures; [] when every source fails."""
    try:
        report = await intl.fetch_international(days_ahead=PREDICTION_DAYS)
    except Exception as e:
        print(f"[International] fetch failed: {e}")
        _intl_status.update(at=datetime.now(timezone.utc), fixtures=0, sources={}, errors=[str(e)])
        return []
    _intl_status.update(at=datetime.now(timezone.utc), fixtures=len(report["fixtures"]),
                        sources=report["sources"], errors=report["errors"])
    _remember_competition_logos(report["fixtures"])
    print(f"[International] {len(report['fixtures'])} fixtures — ESPN {report['sources']['espn']}, "
          f"Odds API {report['sources']['odds_api']}; errors {report['errors'] or 'none'}")
    return report["fixtures"]


_europe_status: Dict[str, Any] = {"at": None, "fixtures": 0, "published": 0, "skipped": [], "errors": []}


async def _fetch_europe_fixtures(predictor, training: pd.DataFrame) -> list:
    """Upcoming Europa and Conference League fixtures from ESPN, only those
    where the model that will predict them (the Europe model when adopted)
    has recent matches for both clubs (europe_fixtures.known)."""
    import europe_fixtures
    try:
        report = await europe_fixtures.fetch(PREDICTION_DAYS)
    except Exception as e:
        print(f"[Europe] fetch failed: {e}")
        _europe_status.update(at=datetime.now(timezone.utc).isoformat(), errors=[str(e)])
        return []
    main_counts = europe_fixtures.recent_counts(training)
    keep, skipped = [], []
    for code in europe_fixtures.COMPETITIONS:
        fixtures = [f for f in report["fixtures"] if f["league"] == code]
        if _europe_covers(code) and _europe_model_info.get("recent_counts"):
            k, sk = europe_fixtures.known(fixtures, _europe_predictor.canon, _europe_model_info["recent_counts"])
        else:
            k, sk = europe_fixtures.known(fixtures, predictor.canon, main_counts)
        keep += k
        skipped += sk
    _europe_status.update(at=datetime.now(timezone.utc).isoformat(), fixtures=len(report["fixtures"]),
                          published=len(keep), skipped=skipped, errors=report["errors"],
                          sources=report["sources"],
                          model={c: "europe" if _europe_covers(c) else "main" for c in europe_fixtures.COMPETITIONS})
    _remember_competition_logos(keep)
    print(f"[Europe] {len(report['fixtures'])} Europa/Conference League fixtures ({report['sources']}), "
          f"{len(keep)} published; {len(skipped)} skipped (a club with under "
          f"{europe_fixtures.MIN_MATCHES} recent matches in our data)"
          f"{'; errors ' + str(report['errors']) if report['errors'] else ''}")
    return keep


def _load_europe_model() -> None:
    """The Europe model, if its check adopted a configuration and a fresh
    one is published (europe_model.py; trained on GitHub Actions)."""
    global _europe_predictor, _europe_model_info
    import europe_model
    import model_store
    from predictor import MODEL_CACHE_VERSION
    try:
        verdict = europe_model.load_check(_get_redis())
        cals = europe_model.calibrations(verdict)
        if _predictor is not None:   # competitions the main model keeps, with their calibration
            _predictor.calibration = cals["main"]
        if cals["main"]:
            print(f"[Europe] Main model calibrated for {', '.join(sorted(cals['main']))}.")
        if not verdict.get("adopted"):
            _europe_predictor, _europe_model_info = None, {"check": verdict.get("reason")}
            return
        shared = model_store.fetch(MODEL_CACHE_VERSION, name=europe_model.MODEL_NAME)
        if shared is None:
            print("[Europe] Adopted, but no fresh Europe model is published — using the main model.")
            return
        blob, meta = shared
        m = LeaguePredictor.from_bytes(blob)
        if m is not None:
            comps = europe_model.competitions(verdict)
            m.calibration = cals["europe"]
            _europe_predictor, _europe_model_info = m, {**meta, "competitions": comps,
                                                        "calibrated": sorted(cals["europe"])}
            print(f"[Europe] Europe model loaded ({meta.get('config')}) for {', '.join(comps) or 'nothing'}.")
    except Exception as e:
        print(f"[Europe] Europe model not loaded: {e}")


def _europe_covers(league: str) -> bool:
    """Whether the Europe model predicts this competition (europe_model.competitions)."""
    return _europe_predictor is not None and league in (_europe_model_info.get("competitions") or [])


def _model_for(fx: Dict[str, Any]):
    """The model that predicts this fixture."""
    return _europe_predictor if _europe_covers(fx.get("league", "")) else _predictor


# Whether the club model uses league strength (predictor.LEAGUE_STRENGTH):
# the European/cup check's verdict, read with the matches (_club_cup_rows)
_club_league_strength = False


def _club_cup_rows(club_names: set, codes: Optional[set] = None) -> pd.DataFrame:
    """ESPN's European and cup matches (club_cups.py) with club names
    resolved to the league data's; `codes` defaults to the approved sets,
    each as full training rows or ratings-only rows (StrengthOnly) as the
    check chose."""
    import club_cups
    import model_store
    global _club_league_strength
    try:
        data = club_cups.load(model_store._client())
    except Exception as e:
        print(f"[Pipeline] Club cups not loaded: {e}")
        return pd.DataFrame()
    _club_league_strength = club_cups.league_strength(data)
    modes = club_cups.modes(data)
    df = club_cups.rows_frame(data, codes if codes is not None else set(modes))
    if df.empty:
        return df
    df["StrengthOnly"] = df["league"].map(lambda c: modes.get(c) == "strength")
    if club_names:
        df = TeamResolver(club_names, aliases=UCL_ALIASES).resolve_frame(df)
    print(f"[Pipeline] +{len(df)} European/cup matches from ESPN "
          f"({int(df['StrengthOnly'].sum())} for ratings only)")
    return df


def _club_cups_status() -> Dict[str, Any]:
    """The European/cup collection and its check, for the admin panel."""
    import club_cups
    import model_store
    try:
        return club_cups.summary(club_cups.load(model_store._client()))
    except Exception as e:
        return {"error": str(e)}


def _assemble_training_data():
    """(history for H2H lookups, training matches, newest data mtime), or None
    without data. Shared by the pipeline and train_model.py."""
    print("[Pipeline] Loading CSV data...")
    # Primary: football-data.co.uk CSVs (include Bet365 odds — best for accuracy)
    fd_df = _load_football_data_csvs()
    # Legacy: our existing EPL + UCL CSVs (no odds but more historical depth)
    epl_df = _load_epl_csv()
    ucl_df = _load_ucl_csv()
    # International match history (fixes national team calibration)
    intl_df = _load_international_csv()

    # One naming scheme for club training data — football-data.co.uk's.
    # UCL CSVs and API results name clubs differently ("Atleti",
    # "Arsenal FC"); unresolved, each club would train as two teams.
    club_names: set = set()
    for df in (fd_df, epl_df):
        if not df.empty:
            club_names |= set(df["HomeTeam"].dropna()) | set(df["AwayTeam"].dropna())
    nation_names: set = set()
    if not intl_df.empty:
        nation_names = set(intl_df["HomeTeam"].dropna()) | set(intl_df["AwayTeam"].dropna())
    # The Brasileirão as context rows (ratings and form, no training rows):
    # no Brazilian club is in the European data, so no other prediction
    # changes. The European extra leagues feed the Europe model only
    # (europe_model.py), which is judged on European matches.
    extra_df = _load_extra_leagues(club_names | nation_names, leagues=MAIN_EXTRA_LEAGUES)
    if not ucl_df.empty and club_names:
        ucl_df = TeamResolver(club_names, aliases=UCL_ALIASES).resolve_frame(ucl_df)
    if not ucl_df.empty:
        ucl_df = ucl_df.assign(Source="ucl")   # for europe_model; the model ignores it

    # European competitions and domestic cups from ESPN (club_cups.py), the
    # sets the nightly check found to improve the league predictions
    cups_df = _club_cup_rows(club_names)
    parts = [df for df in [fd_df, epl_df, ucl_df, intl_df, cups_df, extra_df] if not df.empty]
    if not parts:
        print("[Pipeline] No training data found!")
        return None
    combined = pd.concat(parts, ignore_index=True)
    combined = combined.drop_duplicates(
        subset=["Date", "HomeTeam", "AwayTeam"]
    ).sort_values("Date").reset_index(drop=True)
    if combined.empty:
        print("[Pipeline] No training data after dedup!")
        return None
    history = combined

    # Augment training set with API results saved between runs
    if os.path.exists(RESULTS_CSV):
        try:
            saved_results = pd.read_csv(RESULTS_CSV, parse_dates=["Date"])
            saved_results = saved_results[["Date", "HomeTeam", "AwayTeam", "Result", "FTHG", "FTAG"]].dropna()
            if club_names or nation_names:
                saved_results = TeamResolver(club_names | nation_names).resolve_frame(saved_results)
            combined = pd.concat([combined, saved_results], ignore_index=True)
            combined = combined.drop_duplicates(subset=["Date", "HomeTeam", "AwayTeam"])
            combined = combined.sort_values("Date").reset_index(drop=True)
            print(f"[Pipeline] +{len(saved_results)} saved API results → {len(combined)} total training rows.")
        except Exception as e:
            print(f"[Pipeline] Saved results load error: {e}")

    # The latest mtime across all data sources, so a cached model newer than
    # all of them can be reused
    def _mtime(path):
        try: return os.path.getmtime(path)
        except OSError: return 0.0
    data_mtime = max(
        _mtime(INTERNATIONAL_CSV),
        _mtime(RESULTS_CSV) if os.path.exists(RESULTS_CSV) else 0,
        *[_mtime(os.path.join(DATA_DIR, f)) for f in os.listdir(DATA_DIR) if f.endswith(".csv")],
        # League CSVs — refreshed daily by _sync_football_data
        *[_mtime(f) for f in glob.glob(os.path.join(FOOTBALL_DATA_DIR, "*.csv"))],
        *[_mtime(f) for f in glob.glob(os.path.join(EXTRA_DATA_DIR, "*.csv"))],
    )
    return history, combined, data_mtime


def _train_new(combined: pd.DataFrame) -> LeaguePredictor:
    predictor = LeaguePredictor()
    predictor.use_league_strength = _club_league_strength
    # Seed national team Elo from FIFA rankings BEFORE training
    # This prevents unknown national teams (Ecuador, Algeria etc.) from
    # starting at 1500 and looking equal to Germany/France/Brazil
    predictor.elo.seed_national_teams()
    predictor.train(combined)
    return predictor


def _load_or_train(combined: pd.DataFrame, data_mtime: float) -> LeaguePredictor:
    """
    The model, as cheaply as possible:
      1. this server's cached model, if newer than its data;
      2. the shared model (trained nightly on GitHub Actions, see
         train_model.py), if fresh — seconds instead of minutes of training;
      3. training here, then sharing the result so a restart can load it.
    """
    import model_store
    from predictor import MODEL_CACHE_VERSION

    predictor = LeaguePredictor.load_cache(data_mtime)
    if predictor is not None:
        return predictor
    try:
        shared = model_store.fetch(MODEL_CACHE_VERSION)
        if shared is not None:
            blob, meta = shared
            predictor = LeaguePredictor.from_bytes(blob)
            if predictor is not None:
                age_h = (time.time() - meta["trained_at"]) / 3600
                print(f"[ModelStore] Loaded shared model ({meta.get('source', '?')}, "
                      f"{age_h:.1f}h old, {meta.get('rows', '?')} matches) — skipping training.")
                predictor.save_cache(data_mtime)
                return predictor
    except Exception as e:
        print(f"[ModelStore] Could not load the shared model: {e}")

    predictor = _train_new(combined)
    predictor.save_cache(data_mtime)
    try:
        if model_store._client() is not None:
            model_store.publish(predictor.to_bytes(), MODEL_CACHE_VERSION,
                                {"source": "api-server", "rows": len(combined)})
            print("[ModelStore] Shared the model trained here.")
    except Exception as e:
        print(f"[ModelStore] Could not share the model: {e}")
    return predictor


async def _run_pipeline():
    global _predictor, _predictions_cache, _last_updated, _is_training

    if _is_training:
        return
    _is_training = True

    try:
        await _sync_football_data()
        assembled = await asyncio.to_thread(_assemble_training_data)
        if assembled is None:
            return
        history, combined, data_mtime = assembled
        global _history_df
        _history_df = history  # keep for H2H lookups
        try:
            await asyncio.to_thread(_build_results_index, combined)
        except Exception as e:
            print(f"[Pipeline] Results index failed (non-fatal): {e}")
        global _set_pieces
        try:
            _set_pieces = await asyncio.to_thread(set_pieces.SetPieceModel.fit, _with_club_referees(history))
        except Exception as e:
            print(f"[Pipeline] Corners/bookings model failed (non-fatal): {e}")
        try:
            await asyncio.to_thread(_load_international_set_pieces)
        except Exception as e:
            print(f"[Pipeline] International corners/bookings model failed (non-fatal): {e}")
        try:
            await asyncio.to_thread(_fit_shots, history)
        except Exception as e:
            print(f"[Pipeline] Shots model failed (non-fatal): {e}")
        print(f"[Pipeline] {len(combined)} training matches.")

        # Training takes minutes of CPU. On a worker thread the API keeps
        # answering (from the previous model) instead of timing out.
        predictor = await asyncio.to_thread(_load_or_train, combined, data_mtime)

        # Make predictor available immediately so card analysis works during API calibration
        global _predictor
        _predictor = predictor
        print("[Pipeline] Predictor ready — card analysis now available.")

        # Archive yesterday's predictions from the OLD cache before we start
        # overwriting it below.

        predictions = []
        fixtures: list = []  # pre-init so the block below is safe when API_KEY is unset

        # ── International fixtures (ESPN + The Odds API, no football-data key needed) ──
        # First, so an international break shows up within seconds.
        fixtures.extend(await _fetch_international_fixtures())
        if fixtures:
            for fx in fixtures:
                _cache_team_crest(fx["home"], fx.get("home_crest"))
                _cache_team_crest(fx["away"], fx.get("away_crest"))
            predictions = _build_predictions(predictor, fixtures, {})
            _predictions_cache = predictions
            _last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            _save_predictions_cache()
            print(f"[Pipeline] +INT: {len(fixtures)} international fixtures — "
                  f"{len(predictions)} predictions published.")

        # ── Europa and Conference League (ESPN; not in football-data.org's free plan) ──
        await asyncio.to_thread(_load_europe_model)
        europe = await _fetch_europe_fixtures(predictor, combined)
        if europe:
            fixtures.extend(europe)
            for fx in europe:
                _cache_team_crest(fx["home"], fx.get("home_crest"))
                _cache_team_crest(fx["away"], fx.get("away_crest"))
            predictions = _build_predictions(predictor, fixtures, {})
            _predictions_cache = predictions
            _last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            _save_predictions_cache()
            print(f"[Pipeline] +EL/UECL: {len(europe)} Europa/Conference League fixtures published.")

        if API_KEY:
            client = FootballDataClient(API_KEY)

            # ── Fetch + publish per league, incrementally ──────────────────────
            # football-data.org's free tier is rate-limited, so fetching all 12
            # leagues takes ~2 minutes minimum (10s pacing per league) and can take
            # much longer under 429 backoff. Waiting for all 12 to finish before
            # showing anything means the app looks broken for minutes at a time,
            # and on a free host that can sleep mid-run, a stall anywhere in the
            # loop means NONE of it ever gets served. Instead: publish predictions
            # after each league's fixtures come in, so the first league (World Cup)
            # is visible within seconds, and every completed league survives even
            # if a later one stalls or the run never finishes.
            print("[Pipeline] Fetching upcoming fixtures (publishing after each league)...")
            for code in list(LEAGUES.keys()):
                if code in NOT_IN_PLAN:
                    continue
                try:
                    league_fixtures = await client.fetch_upcoming(code, days_ahead=PREDICTION_DAYS)
                except Exception as e:
                    print(f"[Pipeline] Fixture fetch error for {code}: {e}")
                    league_fixtures = []
                if league_fixtures:
                    fixtures.extend(league_fixtures)
                    # Team crests ride along for free on the fixtures response
                    # (no extra API call) — cache them for /api/team-logo.
                    for fx in league_fixtures:
                        _cache_team_crest(fx["home"], fx.get("home_crest"))
                        _cache_team_crest(fx["away"], fx.get("away_crest"))
                    # No live odds yet on this fast pass — predict_match() falls back
                    # to league-average implied probabilities, which is fine for an
                    # initial publish; the odds pass below refines it.
                    predictions = _build_predictions(predictor, fixtures, {})
                    _predictor = predictor
                    _predictions_cache = predictions
                    _last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                    _save_predictions_cache()
                    print(f"[Pipeline] +{code}: {len(league_fixtures)} fixtures — "
                          f"{len(predictions)} predictions published so far.")
                # Backfill this league's badge only if we don't already have a
                # confirmed one cached — a one-time cost per league, not worth
                # paying every run, and this is also what self-heals a
                # previously rate-limited/failed lookup (see
                # _has_cached_competition_emblem).
                if not _has_cached_competition_emblem(code):
                    try:
                        emblem = await client.fetch_competition_emblem(code)
                        _cache_competition_emblem(code, emblem)
                    except Exception as e:
                        print(f"[Pipeline] Emblem fetch failed for {code}: {e}")
                await asyncio.sleep(10)  # respect football-data.org rate limit

            # ── Live odds pass — improves accuracy + enables value-bet detection ──
            live_odds: dict = {}
            if fixtures:
                try:
                    stubs = [{"home": fx["home"], "away": fx["away"],
                              "date": fx.get("date",""), "league_name": fx.get("league_name",""),
                              **({"odds_sport": fx.get("odds_sport")} if "odds_sport" in fx else {})}
                             for fx in fixtures]
                    live_odds = await _get_live_odds_throttled(stubs)
                    print(f"[Pipeline] Got live odds for {len(live_odds)}/{len(fixtures)} fixtures")
                except Exception as e:
                    print(f"[Pipeline] Live odds fetch error (non-fatal): {e}")

                predictions = _build_predictions(predictor, fixtures, live_odds)
                _predictions_cache = predictions
                _last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                _save_predictions_cache()
                print(f"[Pipeline] Republished {len(predictions)} predictions (with live odds).")
                if _unknown_clubs:
                    print(f"[Pipeline] {len(_unknown_clubs)} fixture clubs the model has no matches for: "
                          + ", ".join(f"{n} ({lg})" for n, lg in sorted(_unknown_clubs.items(), key=lambda kv: kv[1])))

            # ── Recent-results Elo calibration (refinement) ───────────────────
            print("[Pipeline] Fetching recent API results to calibrate Elo...")
            calibrated = False
            for code in list(LEAGUES.keys()):
                if code in NOT_IN_PLAN:
                    continue
                try:
                    recent = await client.fetch_recent_results(code, days_back=60)
                    if recent.empty or "HomeTeam" not in recent.columns:
                        await asyncio.sleep(6)  # still pace requests even on empty results
                        continue
                    for _, r in recent.iterrows():
                        predictor._update(r["HomeTeam"], r["AwayTeam"], r["Result"], r["FTHG"], r["FTAG"],
                                          competition=code)
                    calibrated = True
                    await asyncio.sleep(10)
                except Exception as e:
                    print(f"[Pipeline] Recent results error for {code}: {e}")

            # Re-predict with the calibrated Elo and republish.
            if calibrated and fixtures:
                predictions = _build_predictions(predictor, fixtures, live_odds)
                _predictions_cache = predictions
                _last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                _save_predictions_cache()
                print(f"[Pipeline] Republished {len(predictions)} predictions (calibrated).")
        else:
            print("[Pipeline] WARNING: No FOOTBALL_DATA_API_KEY set. Add your key to .env to get live fixtures.")

        # Pre-fetch web form for teams that have sparse local data
        # Runs as a background task so it doesn't block the pipeline
        if fixtures:
            asyncio.create_task(_prefetch_web_forms(predictor, fixtures))

        # Fetch Understat xG in background (updates Redis cache for European clubs)
        asyncio.create_task(_refresh_understat_xg())

        _predictor = predictor
        print(f"[Pipeline] Done — {len(predictions)} predictions cached.")
        asyncio.create_task(_link_sportybet_events("pipeline"))
        asyncio.create_task(_refresh_referees("pipeline"))

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
    predictions stay fresh between full retrains. International results come
    from ESPN (international_fixtures.py) and need no key.
    """
    print("[Results] Fetching recent finished results...")
    all_rows: List[pd.DataFrame] = []
    try:
        intl_report = await intl.fetch_international(days_ahead=0, days_back=10)
        if intl_report["results"]:
            all_rows.append(pd.DataFrame(intl_report["results"]).assign(Date=lambda d: pd.to_datetime(d["Date"])))
            print(f"[Results] {len(intl_report['results'])} international results.")
    except Exception as e:
        print(f"[Results] International results error: {e}")

    client = FootballDataClient(API_KEY) if API_KEY else None
    for code in (LEAGUES if client else []):
        if code in NOT_IN_PLAN:
            continue
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
    """Matches from the last 7 days still without a result a day after
    kick-off (neither ESPN nor the league CSVs had them — cups, small
    friendlies): ask the web (Groq), at most 5 a run."""
    import matchday
    from llm_service import GROQ_API_KEY, fetch_missing_results
    if not GROQ_API_KEY:
        return
    r = _get_redis()
    if not r:
        return
    now = datetime.now(timezone.utc)
    searched = 0
    for days_ago in range(1, 8):
        d = (now.date() - timedelta(days=days_ago)).isoformat()
        try:
            day = _md_load(r, d)
        except Exception:
            continue
        changed = False
        for e in day.values():
            if searched >= 5:
                break
            res = e.get("result") or {}
            ko = matchday.kickoff(e)
            if res.get("status") in ("finished", "postponed") or not ko or now - ko < timedelta(hours=24):
                continue
            searched += 1
            try:
                found = await fetch_missing_results(e["home"], e["away"], d)
            except Exception:
                continue
            hg, ag = to_goals(found.get("home_goals")), to_goals(found.get("away_goals"))
            if found.get("found") and hg is not None and ag is not None:
                changed |= matchday.apply_result(e, {"status": "finished", "hg": hg, "ag": ag, "source": "web_search"})
                print(f"[WebResults] {e['home']} {hg}-{ag} {e['away']} ({d})")
            await asyncio.sleep(1)
        if changed:
            _md_save(r, d, day)


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
    # International competitions with upcoming matches first (they're only
    # busy during international windows), then the club leagues
    live = {p.get("league", "") for p in _predictions_cache if _within_window(p.get("date", ""))}
    international = [
        {"code": code, "name": name, "country": "World", "flag": flag}
        for code, name, flag, _ in intl.COMPETITIONS if code in live
    ]
    if intl.LEAGUE_CODE in live:
        international.append({"code": intl.LEAGUE_CODE, **intl.LEAGUE_INFO})
    return international + [
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
    # Predictions cached before the window shrank stay hidden
    data = [p for p in _predictions_cache if _within_window(p.get("date", ""))]

    if league and league.upper() != "ALL":
        data = [p for p in data if p.get("league", "").upper() == league.upper()]

    if date_str:
        data = [p for p in data if p.get("date") == date_str]

    if min_confidence > 0:
        data = [p for p in data if _pick_confidence(p) >= min_confidence]

    # Sort by confidence desc, then date
    data = sorted(data, key=lambda x: (-_pick_confidence(x), x.get("date", "")))

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
    if _predictor is not None:  # the CSVs use the model's names, not the API's
        home, away = _predictor.canon(home), _predictor.canon(away)
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


# ------------------------------------------------------------------ #
# Match page: each side's last five and their last five meetings
# ------------------------------------------------------------------ #
_recent_md: Tuple[float, Optional[pd.DataFrame]] = (0.0, None)
_facts_names: List[Optional[TeamResolver]] = [None]   # names in the results index
RECENT_MD_DAYS = 10


def _build_results_index(combined: pd.DataFrame) -> None:
    """The training matches plus every European/cup match collected (not
    only the sets the model trains on), as one table."""
    import club_cups
    import match_facts
    global _results_idx
    clubs = combined[combined.get("league", pd.Series("", index=combined.index)) != intl.LEAGUE_CODE]
    names = set(clubs["HomeTeam"].dropna()) | set(clubs["AwayTeam"].dropna())
    cups = _club_cup_rows(names, club_cups.EUROPE_CODES | club_cups.CUP_CODES)
    # The Champions League CSVs carry no competition (named rows win duplicates)
    ucl = _load_ucl_csv()
    if not ucl.empty:
        ucl = TeamResolver(names, aliases=UCL_ALIASES).resolve_frame(ucl).assign(league="CL")
    extra = _load_extra_leagues(names)      # every extra league, for their clubs' last five
    _results_idx = match_facts.index([match_facts.frame(ucl), match_facts.frame(combined),
                                      match_facts.frame(cups), match_facts.frame(extra)])
    _facts_names[0] = TeamResolver(set(_results_idx["HomeTeam"]) | set(_results_idx["AwayTeam"]),
                                   aliases=UCL_ALIASES)
    print(f"[Pipeline] Results index: {len(_results_idx)} matches.")


def _recent_results_frame() -> pd.DataFrame:
    """Finished matches from the last RECENT_MD_DAYS match days (the site's
    live scores; newer than the CSVs), in the model's names. Cached 10 min."""
    global _recent_md
    import match_facts
    import matchday
    if _recent_md[1] is not None and time.time() - _recent_md[0] < 600:
        return _recent_md[1]
    rows = []
    today = date.today()
    for o in range(RECENT_MD_DAYS + 1):
        d = (today - timedelta(days=o)).isoformat()
        for e in _md_day_view(d).values():
            res = e.get("result") or {}
            if res.get("status") != matchday.FINISHED or res.get("hg") is None or res.get("ag") is None:
                continue
            if e.get("sport") not in (None, "football"):
                continue
            canon = _predictor.canon if _predictor is not None else (lambda n: n)
            rows.append({"Date": e.get("date") or d, "HomeTeam": canon(e.get("home", "")),
                         "AwayTeam": canon(e.get("away", "")), "FTHG": res["hg"], "FTAG": res["ag"],
                         "comp": e.get("league_name") or match_facts.comp_name(e.get("league"))})
    df = match_facts.index([pd.DataFrame(rows)]) if rows else pd.DataFrame(columns=match_facts.COLUMNS)
    _recent_md = (time.time(), df)
    return df


def _team_results(teams: set) -> pd.DataFrame:
    import match_facts
    base = _results_idx
    if base is None and _history_df is not None:
        base = match_facts.frame(_history_df)
    parts = []
    try:
        rec = _recent_results_frame()
        parts.append(rec[rec["HomeTeam"].isin(teams) | rec["AwayTeam"].isin(teams)])
    except Exception as e:
        print(f"[Facts] Recent results unavailable: {e}")
    if base is not None and not base.empty:
        parts.append(base[base["HomeTeam"].isin(teams) | base["AwayTeam"].isin(teams)])
    return match_facts.index(parts)


async def _facts_for(home: str, away: str, day: str = "") -> Dict[str, Any]:
    import match_facts
    resolver = _facts_names[0]
    if resolver is not None:
        h, a = resolver.resolve(home), resolver.resolve(away)
    elif _predictor is not None:
        h, a = _predictor.canon(home), _predictor.canon(away)
    else:
        h, a = home, away
    idx = await asyncio.to_thread(_team_results, {h, a})
    return match_facts.facts(idx, h, a, day or None)


@app.get("/api/match/facts")
async def get_match_facts(home: str, away: str, day: str = Query("", alias="date")):
    """Each side's last five results and the last five meetings between them
    (empty when they've never met), before the match date."""
    day = day or _fixture_of(home, away).get("date", "")
    return _sanitize(await _facts_for(home, away, day))


# ------------------------------------------------------------------ #
# Match page: the slow parts, built once per match (match_cache.py)
# ------------------------------------------------------------------ #
import match_cache
_mcache = match_cache.MatchCache(lambda: _get_redis())


def _fixture_of(home: str, away: str, day: str = "") -> Dict[str, Any]:
    """The prediction for a fixture (date, time, league, odds, referee), or {}."""
    h, a = home.lower(), away.lower()
    found = [p for p in _predictions_cache
             if p.get("home", "").lower() == h and p.get("away", "").lower() == a]
    return next((p for p in found if not day or p.get("date") == day), found[0] if found else {})


def _match_timing(home: str, away: str, day: str = "") -> Tuple[Dict[str, Any], str, Optional[datetime]]:
    """(fixture, cache key, kick-off)."""
    import matchday
    fx = _fixture_of(home, away, day)
    day = day or fx.get("date", "")
    ko = matchday.kickoff({**fx, "date": day}) if day else None
    return fx, match_cache.match_key(home, away, day), ko


async def _match_news(home: str, away: str, day: str = "") -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """The fixture's current team news (dated, last week only) and the
    model adjustments read from it, shared by every visitor and refreshed
    more often as kick-off nears."""
    import llm_service
    fx, key, ko = _match_timing(home, away, day)

    async def build():
        news = await llm_service.fetch_match_news(home, away, day or fx.get("date", ""),
                                                  fx.get("league_name", ""))
        news["adjustments"] = (await llm_service.extract_model_adjustments(home, away, news["text"])
                               if news["text"] else {})
        if not news["searched"] and llm_service.GROQ_API_KEY:
            news["_fresh_for"] = 600   # the search failed: try again soon
        return news

    return await _mcache.get("news", key, build, fp="v1",
                             fresh_for=match_cache.news_fresh_for(ko), ttl=match_cache.ttl_seconds(ko))


async def _analysis_cached(home: str, away: str, day: str = "") -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """The full market breakdown, rebuilt when the model, the odds or the
    team news change (the visitor meanwhile gets the previous one)."""
    fx, key, ko = _match_timing(home, away, day)
    news, n_info = await _match_news(home, away, day)
    fp = match_cache.fingerprint(id(_model_for(fx)), n_info.get("at"), fx.get("odds_home"), fx.get("odds_draw"),
                                 fx.get("odds_away"), (fx.get("referee") or {}).get("name"))
    return await _mcache.get("analysis", key, lambda: _build_analysis(home, away, fx, news),
                             fp=fp, fresh_for=3600, ttl=match_cache.ttl_seconds(ko))


@app.get("/api/analysis")
async def get_match_analysis(home: str, away: str, day: str = Query("", alias="date"),
                             _premium=Depends(require_premium)):
    if _predictor is None:
        raise HTTPException(status_code=503, detail="Model not ready yet")
    result, info = await _analysis_cached(home, away, day)
    if result is None:
        raise HTTPException(status_code=404, detail="Could not generate analysis")
    return {**result, "updated_at": info["at"], "refreshing": info["refreshing"]}


async def _build_analysis(home: str, away: str, cached_fx: Dict[str, Any], news: Dict[str, Any]) -> Optional[Dict]:
    P = _model_for(cached_fx)   # the Europe model for European fixtures, when adopted
    if P is None:
        return None
    fx_date = cached_fx.get("date", "")

    # Fetch LIVE odds right now from The Odds API
    live_odds = await _fetch_live_odds(home, away, fx_date)

    # Model adjustments read from the current team news (_match_news)
    news_sources = news.get("sources") or []
    adjustments = news.get("adjustments") or {}

    # Apply web-search adjustments to xG before running the model
    # This makes injury news actually move the prediction numbers
    adj_xg_h = 1.0 + adjustments.get("home_attack_modifier", 0.0)
    adj_xg_a = 1.0 + adjustments.get("away_attack_modifier", 0.0)
    adj_def_h = 1.0 + adjustments.get("home_defense_modifier", 0.0)
    adj_def_a = 1.0 + adjustments.get("away_defense_modifier", 0.0)
    # The model's names for these teams (display names stay as given)
    h_key, a_key = P.canon(home), P.canon(away)
    if adjustments:
        P._init(h_key)
        P._init(a_key)
        # Temporarily scale goal lists so Dixon-Coles xG reflects the news adjustment.
        # We scale both overall gf and the venue-specific home_gf/away_gf lists.
        orig_home_gf      = P.team_stats[h_key].get("gf", [])
        orig_home_gf_home = P.team_stats[h_key].get("home_gf", [])
        orig_away_gf      = P.team_stats[a_key].get("gf", [])
        orig_away_gf_away = P.team_stats[a_key].get("away_gf", [])
        if adj_xg_h != 1.0:
            if orig_home_gf:
                P.team_stats[h_key]["gf"]      = [v * adj_xg_h for v in orig_home_gf]
            if orig_home_gf_home:
                P.team_stats[h_key]["home_gf"] = [v * adj_xg_h for v in orig_home_gf_home]
        if adj_xg_a != 1.0:
            if orig_away_gf:
                P.team_stats[a_key]["gf"]      = [v * adj_xg_a for v in orig_away_gf]
            if orig_away_gf_away:
                P.team_stats[a_key]["away_gf"] = [v * adj_xg_a for v in orig_away_gf_away]

    # If live odds available, re-run prediction with them for better accuracy
    if live_odds:
        result = P.predict_match_full(
            home, away,
            odds_home=live_odds.get("1", 0),
            odds_draw=live_odds.get("X", 0),
            odds_away=live_odds.get("2", 0),
            league=cached_fx.get("model_league") or cached_fx.get("league", ""),
        )
    else:
        result = P.predict_match_full(home, away, league=cached_fx.get("model_league") or cached_fx.get("league", ""))

    # Restore original stats after prediction (don't permanently alter training data)
    if adjustments:
        if adj_xg_h != 1.0:
            P.team_stats[h_key]["gf"]      = orig_home_gf
            P.team_stats[h_key]["home_gf"] = orig_home_gf_home
        if adj_xg_a != 1.0:
            P.team_stats[a_key]["gf"]      = orig_away_gf
            P.team_stats[a_key]["away_gf"] = orig_away_gf_away

    # Apply confidence modifier from web search
    conf_mod = adjustments.get("confidence_modifier", 0.0)
    if conf_mod and result:
        result["web_confidence_modifier"] = conf_mod
        result["web_adjustment_flags"] = adjustments.get("flags", [])
        result["web_adjustment_reason"] = adjustments.get("reasoning", "")
        result["web_news_sources"] = news_sources

    if result is None:
        return None

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
        "home": _predictor_form_summary(home, P),
        "away": _predictor_form_summary(away, P),
    }

    # Inject cards + corners markets if data is available
    extra = _predictor.predict_cards(home, away, _cards_df, _corners_df)
    for market_id in ("cards", "corners", "corners_race"):
        if market_id in extra:
            result["markets"].append(extra[market_id])

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


@app.get("/api/admin/sportybet-check")
async def sportybet_check(_admin: str = Depends(require_admin)):
    """Book a real one-pick SportyBet code from this server and report each step."""
    import sportybet
    today = date.today().isoformat()
    upcoming = [{"home": p.get("home"), "away": p.get("away")} for p in _predictions_cache
                if p.get("date", "") >= today and p.get("sport") in (None, "football")]
    return await sportybet.diagnose(upcoming)


@app.get("/api/admin/sportybet-link")
async def sportybet_link_now(_admin: str = Depends(require_admin)):
    """Match upcoming predictions to SportyBet events now (normally every 30 min)."""
    _audit(_admin, "sportybet_link")
    return await _link_sportybet_events("manual")


@app.get("/api/admin/sportybet-link-status")
async def sportybet_link_status(_admin: str = Depends(require_admin)):
    """The last linking run's full result (the admin panel polls this)."""
    _restore_link_status()
    return _sb_link_status


@app.get("/api/admin/international-check")
async def international_check(_admin: str = Depends(require_admin)):
    """Fetch international fixtures now, report what each source returned,
    and publish them without waiting for the next pipeline run."""
    global _predictions_cache, _last_updated
    _audit(_admin, "international_check")
    fixtures = await _fetch_international_fixtures()
    published = 0
    if fixtures and _predictor is not None:
        cached_odds, _ = _load_cached_live_odds()
        fresh = _build_predictions(_predictor, fixtures, cached_odds or {})
        if fresh:
            _predictions_cache = [p for p in _predictions_cache
                                  if not intl.is_international(p.get("league", ""))] + fresh
            _last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            _save_predictions_cache()
            published = len(fresh)
            asyncio.create_task(_link_sportybet_events("international"))
    by_competition: Dict[str, int] = {}
    for f in fixtures:
        by_competition[f["league_name"]] = by_competition.get(f["league_name"], 0) + 1
    return {"fixtures": len(fixtures), "published": published, "model_ready": _predictor is not None,
            "by_competition": by_competition, "sources": _intl_status["sources"],
            "errors": _intl_status["errors"]}


def _shared_model_status() -> Optional[Dict[str, Any]]:
    """The model published for this server (model_store), if any."""
    try:
        import model_store
        from predictor import MODEL_CACHE_VERSION
        meta = model_store.describe(MODEL_CACHE_VERSION)
    except Exception:
        return None
    if not meta:
        return None
    return {"source": meta.get("source"), "rows": meta.get("rows"), "size": meta.get("size"),
            "trained_at": datetime.fromtimestamp(meta["trained_at"], timezone.utc).isoformat(),
            "max_age_hours": model_store.MAX_AGE_HOURS}


@app.get("/api/admin/data-status")
async def data_status(_admin: str = Depends(require_admin)):
    """How fresh the training data is, and which upcoming teams the model
    knows little or nothing about (a name it couldn't match, or a new club)."""
    leagues: Dict[str, Dict[str, Any]] = {}
    for path in sorted(glob.glob(os.path.join(FOOTBALL_DATA_DIR, "*.csv"))):
        div = os.path.basename(path).split("_")[0]
        try:
            dates = pd.to_datetime(pd.read_csv(path, usecols=["Date"])["Date"], dayfirst=True, errors="coerce")
            latest = dates.max()
        except Exception:
            continue
        if pd.notna(latest) and (div not in leagues or str(latest.date()) > leagues[div]["latest_match"]):
            leagues[div] = {"latest_match": str(latest.date()), "file": os.path.basename(path)}

    teams: Dict[str, Dict[str, Any]] = {}
    if _predictor is not None:
        for p in _predictions_cache:
            if p.get("sport") not in (None, "football"):
                continue
            for name in (p.get("home"), p.get("away")):
                if not name or name in teams:
                    continue
                key = _predictor.canon(name)
                teams[name] = {"model_name": key, "league": p.get("league", ""),
                               "matches": len(_predictor.team_stats.get(key, {}).get("pts", []))}
    thin = sorted(({"team": k, **v} for k, v in teams.items() if v["matches"] < 5),
                  key=lambda t: (t["matches"], t["team"]))
    last = _football_sync["at"]
    return {
        "leagues": leagues,
        "last_sync": {"at": last.isoformat() if last else None, "report": _football_sync["report"]},
        "teams_checked": len(teams),
        "renamed": {k: v["model_name"] for k, v in sorted(teams.items()) if v["model_name"] != k},
        "thin_history": thin,
        "international": {**_intl_status, "at": _intl_status["at"].isoformat() if _intl_status["at"] else None},
        "unknown_clubs": dict(sorted(_unknown_clubs.items(), key=lambda kv: kv[1])),
        "europa_league": {**_europe_status, "europe_model": {k: v for k, v in _europe_model_info.items()
                                                            if k != "recent_counts"}},
        "shared_model": _shared_model_status(),
        "sportybet_links": _sb_link_status,
        "international_set_pieces": {**_intl_sp_info, "active": _intl_set_pieces is not None,
                                     "shots_active": _intl_shots is not None},
        "shots": {**_shots_info, "active": _shots is not None},
        "referees": _referee_status(),
        "matchday": _md_status,
        "club_cups": _club_cups_status(),
    }


@app.get("/api/admin/model-metrics")
async def model_metrics(_admin: str = Depends(require_admin)):
    """Walk-forward backtest results (generated offline by backtest.py). Admin only."""
    from backtest import METRICS_PATH
    try:
        with open(METRICS_PATH) as f:
            return {"available": True, **json.load(f)}
    except (OSError, ValueError):
        return {"available": False}


# ── Match days: pre-match predictions, results, grades (matchday.py) ──────
MD_KEY = "betiq:md:{}"
MD_TTL = 120 * 86400
MD_DAYS_BACK, MD_DAYS_AHEAD = 7, 14      # what the site's date strip shows
MD_LIVE_MINUTES = 15                      # live scores: how often while matches are on
_md_status: Dict[str, Any] = {"at": None, "trigger": None, "report": None}
_md_read_cache: Dict[str, Tuple[float, Dict]] = {}


def _md_load(r, d: str) -> Dict[str, Dict]:
    """One date's entries ({match key: entry}); the old history format
    (betiq:history:{date}) is converted on the fly."""
    import matchday
    raw = r.get(MD_KEY.format(d))
    if raw:
        return json.loads(raw)
    legacy = r.get(f"betiq:history:{d}")
    if legacy:
        day = {}
        for p in json.loads(legacy):
            if p.get("home") and p.get("away"):
                day[matchday.key(p["home"], p["away"])] = matchday.from_legacy({**p, "date": p.get("date") or d})
        return day
    return {}


def _md_save(r, d: str, day: Dict[str, Dict]) -> None:
    r.set(MD_KEY.format(d), json.dumps(day, separators=(",", ":")), ex=MD_TTL)
    _md_read_cache.pop(d, None)


def _md_many(r, dates: List[str]) -> Dict[str, Dict[str, Dict]]:
    """Several dates in one request (MGET); dates only in the old format are
    read one by one."""
    raws = r.mget([MD_KEY.format(d) for d in dates]) if dates else []
    out = {}
    for d, raw in zip(dates, raws):
        out[d] = json.loads(raw) if raw else (_md_load(r, d) if d < date.today().isoformat() else {})
    return out


def _snapshot_matchdays(r) -> int:
    """Store each football prediction as its match day's pre-match entry
    (refreshed until kick-off, then locked). Returns dates written."""
    import matchday
    import price_book
    by_date: Dict[str, List[Dict]] = {}
    for p in _predictions_cache:
        if p.get("sport") in (None, "football") and p.get("date"):
            try:
                priced = price_book.prices(p, _linked_event(p))
            except Exception:
                priced = None
            by_date.setdefault(p["date"], []).append({**p, "_sb_prices": priced} if priced else p)
    now = datetime.now(timezone.utc)
    written = 0
    for d, preds in by_date.items():
        day = _md_load(r, d)
        if matchday.merge_predictions(day, preds, now):
            _md_save(r, d, day)
            written += 1
    return written


def _md_day_view(d: str) -> Dict[str, Dict]:
    """A date's entries for the site: stored (cached a minute), with the
    current predictions merged in for matches not yet started."""
    import matchday
    hit = _md_read_cache.get(d)
    if hit and time.time() - hit[0] < 60:
        day = hit[1]
    else:
        r = _get_redis()
        try:
            day = _md_load(r, d) if r else {}
        except Exception:
            day = {}
        _md_read_cache[d] = (time.time(), day)
    if d >= date.today().isoformat():
        day = json.loads(json.dumps(day))  # the cached copy stays as stored
        matchday.merge_predictions(day, [p for p in _predictions_cache
                                         if p.get("date") == d and p.get("sport") in (None, "football")],
                                   datetime.now(timezone.utc))
    return day


async def _refresh_matchdays(days_back: int = 1, trigger: str = "schedule") -> Dict[str, Any]:
    """Scores for matches that have kicked off in the last `days_back` days
    (ESPN live/final, then the league CSVs for stats and anything missed);
    grades them, then settles booked tickets."""
    import matchday
    import results_feed
    r = _get_redis()
    if not r:
        return {"skipped": "no Redis"}
    now = datetime.now(timezone.utc)
    today = now.date()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(days_back + 1)]
    days = _md_many(r, dates)
    need = {d: {k: e for k, e in day.items() if matchday.needs_result(e, now)} for d, day in days.items()}
    need = {d: v for d, v in need.items() if v}
    report: Dict[str, Any] = {"dates": sorted(need), "matches": sum(len(v) for v in need.values()),
                              "updated": 0, "requests": 0, "errors": [], "unmatched": 0}
    if need:
        pairs = set()
        for entries in need.values():
            for e in entries.values():
                day0 = date.fromisoformat(e["date"])
                for slug in results_feed.slugs_for(e.get("league") or ""):
                    pairs.add((day0, slug))
                    if (e.get("time") or "12:00") < "06:00":  # ESPN files it under the US date
                        pairs.add((day0 - timedelta(days=1), slug))
        espn: List[Dict] = []
        if pairs:
            from curl_cffi.requests import AsyncSession
            try:
                async with AsyncSession(impersonate=intl.IMPERSONATE, timeout=20) as client:
                    for d0, slug in sorted(pairs):
                        got = await results_feed.fetch_espn(client, [d0], [slug])
                        espn += got["results"]
                        report["requests"] += got["requests"]
                        report["errors"] += got["errors"]
            except Exception as e:
                report["errors"].append(f"ESPN: {type(e).__name__}")
        near = {(date.fromisoformat(d) + timedelta(days=o)).isoformat() for d in need for o in (-1, 0, 1)}
        try:
            csv = await asyncio.to_thread(results_feed.csv_results, near)
        except Exception as e:
            csv = []
            report["errors"].append(f"CSV: {type(e).__name__}")
        for d, entries in need.items():
            day, changed = days[d], False
            matched = set()
            for source in (espn, csv):
                for k, res in matchday.match_results(entries, source):
                    matched.add(k)
                    if matchday.apply_result(day[k], res):
                        changed = True
                        report["updated"] += 1
            report["unmatched"] += sum(1 for k in entries if k not in matched
                                       and (matchday.kickoff(entries[k]) or now) < now - timedelta(hours=3))
            if changed:
                _md_save(r, d, day)
    report["errors"] = report["errors"][:20]
    try:
        report["tickets"] = await asyncio.to_thread(_settle_tickets, r)
    except Exception as e:
        report["tickets"] = {"error": str(e)}
    _md_status.update(at=now.isoformat(timespec="seconds"), trigger=trigger, report=report)
    if report["updated"]:
        print(f"[MatchDay] {trigger}: {report['updated']} results updated over {report['dates']}")
    return report


async def _matchday_live() -> None:
    await _refresh_matchdays(1, "live")


async def _matchday_sweep() -> None:
    await _refresh_matchdays(MD_DAYS_BACK, "sweep")


def _legacy_view(e: Dict) -> Dict[str, Any]:
    """A match-day entry in the old history shape (admin stats, /api/history)."""
    import grading
    res, g = e.get("result") or {}, e.get("grades") or {}
    finished = res.get("status") == "finished" and res.get("hg") is not None and not res.get("aet")
    tip = (g.get("tip") or {}).get("verdict")
    return {**{f: e.get(f) for f in ("home", "away", "date", "time", "league", "league_name", "flag")},
            **(e.get("pred") or {}),
            "outcome": tip or ("void" if finished else "pending"),
            "actual_result": grading.result_from_score(int(res["hg"]), int(res["ag"])) if finished else None,
            "score": f"{res['hg']}-{res['ag']}" if finished else None,
            "goals_outcome": (g.get("goals") or {}).get("verdict")}


def _read_history(r, d: str) -> List[Dict]:
    return [_legacy_view(e) for e in _md_load(r, d).values()]


def _date_param(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")


@app.get("/api/matchday")
async def get_matchday(date_: str = Query("", alias="date")):
    """One day's matches: the pre-match prediction, the score (live or
    final) and how each market's pick did. From 90 days back to the last
    predicted day."""
    import matchday
    today = date.today()
    d = _date_param(date_ or today.isoformat())
    if not (today - timedelta(days=90) <= d <= today + timedelta(days=PREDICTION_DAYS + 1)):
        raise HTTPException(status_code=400, detail="date out of range")
    day = _md_day_view(d.isoformat())
    matches = sorted((matchday.public(e) for e in day.values()),
                     key=lambda m: (m.get("league_name") or "", m.get("time") or "", m.get("home") or ""))
    return {"date": d.isoformat(), "today": today.isoformat(), "matches": matches,
            "summary": matchday.day_summary(day.values()),
            "updated": _md_status.get("at")}


_strip_cache: Dict[str, Tuple[float, Any]] = {}


@app.get("/api/matchday/strip")
async def get_matchday_strip():
    """The date strip: 7 days back to 14 ahead, each with its match count,
    how many are live / finished, and how our tips did."""
    import matchday
    today = date.today()
    hit = _strip_cache.get("strip")
    if hit and time.time() - hit[0] < 60 and hit[1]["today"] == today.isoformat():
        return hit[1]
    dates = [(today + timedelta(days=o)).isoformat() for o in range(-MD_DAYS_BACK, MD_DAYS_AHEAD + 1)]
    r = _get_redis()
    try:
        stored = _md_many(r, dates) if r else {}
    except Exception:
        stored = {}
    upcoming: Dict[str, int] = {}
    for p in _predictions_cache:
        if p.get("sport") in (None, "football") and p.get("date"):
            upcoming[p["date"]] = upcoming.get(p["date"], 0) + 1
    days = []
    for d in dates:
        s = matchday.day_summary((stored.get(d) or {}).values())
        if d >= today.isoformat():
            s["total"] = max(s["total"], upcoming.get(d, 0))
        days.append({"date": d, **s})
    out = {"today": today.isoformat(), "days": days}
    _strip_cache["strip"] = (time.time(), out)
    return out


_accuracy_cache: Dict[int, Tuple[float, Any]] = {}


@app.get("/api/accuracy")
async def get_accuracy(days: int = 30):
    """The model's track record over the last `days` days (7–90): each
    market's hit rate next to the probability we gave, calibration, the
    1X2 Brier score against the bookmaker's, by league and by day."""
    import matchday
    days = max(1, min(int(days), 90))
    hit = _accuracy_cache.get(days)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    r = _get_redis()
    if not r:
        return {"days": days, "matches": 0, "markets": {}, "calibration": [], "brier": {}, "leagues": [], "daily": []}
    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(days, -1, -1)]
    out = {"days": days, **matchday.accuracy(await asyncio.to_thread(_md_many, r, dates))}
    _accuracy_cache[days] = (time.time(), out)
    return out


@app.get("/api/history")
async def get_history(date: str):
    """A date's predictions in the old shape (outcome / actual_result / score)."""
    return [_legacy_view(e) for e in _md_day_view(_date_param(date).isoformat()).values()]


@app.get("/api/admin/edge-report")
async def edge_report(days: int = 60, _admin: str = Depends(require_admin)):
    """Flat-stake profit at SportyBet's pre-match prices over the last `days`
    days, by market and by the edge the model claimed (price_book.py)."""
    import price_book
    days = max(1, min(int(days), 118))
    r = _get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(1, days + 1)]
    stored = await asyncio.to_thread(_md_many, r, dates)
    entries = [e for d in dates for e in (stored.get(d) or {}).values()]
    return {"days": days, **price_book.report(entries)}


@app.get("/api/admin/market-accuracy")
async def market_accuracy_report(days: int = 7, _admin: str = Depends(require_admin)):
    """Every market's picks (outcomes the model rated ≥ 50%) over the last
    `days` days, settled: hit rate vs what the model said, by market and
    confidence band (market_accuracy.py)."""
    import market_accuracy
    days = max(1, min(int(days), 60))
    r = _get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(0, days + 1)]
    stored = await asyncio.to_thread(_md_many, r, dates)
    entries = [e for d in dates for e in (stored.get(d) or {}).values()]
    return {"days": days, **await asyncio.to_thread(market_accuracy.report, entries)}


@app.get("/api/calendar")
async def get_calendar(month: str = ""):
    """Per-date summary for a month (YYYY-MM), in the old shape."""
    import calendar as cal_lib
    import matchday
    month = month or datetime.utcnow().strftime("%Y-%m")
    try:
        year, m = map(int, month.split("-"))
        days_in_month = cal_lib.monthrange(year, m)[1]
    except (ValueError, cal_lib.IllegalMonthError):
        raise HTTPException(status_code=400, detail="month must be YYYY-MM")
    dates = [f"{year:04d}-{m:02d}-{d:02d}" for d in range(1, days_in_month + 1)]
    r = _get_redis()
    stored = _md_many(r, dates) if r else {}
    summary: Dict[str, Any] = {}
    for d in dates:
        entries = list((stored.get(d) or {}).values())
        if not entries:
            continue
        s = matchday.day_summary(entries)
        summary[d] = {"total": s["total"], "won": s["tip"][0], "lost": s["tip"][1],
                      "pending": s["total"] - s["finished"], "goals_won": s["goals"][0], "goals_lost": s["goals"][1]}
    return summary


# ── Tickets: booking codes per account, settled from the match days ──────
TICKETS_OPEN_KEY = "betiq:tickets:open"       # accounts with unsettled tickets
TICKETS_STATS_KEY = "betiq:tickets:stats"
TICKET_SOURCES = {"slip", "optimizer", "code_check", "chat", "match", "other"}


def _record_ticket(uid: str, ticket: Dict[str, Any]) -> None:
    import tickets
    r = _get_redis()
    if not r or not ticket.get("legs"):
        return
    key = _ukey(uid, "tickets")
    raw = r.get(key)
    items: List[Dict] = [t for t in (json.loads(raw) if raw else []) if t.get("code") != ticket["code"]]
    items.insert(0, ticket)
    r.set(key, json.dumps(items[:tickets.MAX_TICKETS], separators=(",", ":")), ex=365 * 86400)
    r.sadd(TICKETS_OPEN_KEY, uid)
    r.hincrby(TICKETS_STATS_KEY, "created", 1)
    r.hincrby(TICKETS_STATS_KEY, f"source:{ticket.get('source') or 'other'}", 1)
    r.incr(f"betiq:tickets:day:{date.today().isoformat()}")
    r.expire(f"betiq:tickets:day:{date.today().isoformat()}", 90 * 86400)


def _settle_tickets(r) -> Dict[str, int]:
    """Grade the legs of every open ticket whose matches have finished."""
    import matchday
    import tickets
    uids = [u.decode() if isinstance(u, bytes) else u for u in (r.smembers(TICKETS_OPEN_KEY) or [])]
    days: Dict[str, Dict] = {}
    report = {"accounts": len(uids), "settled": 0, "legs": 0}
    today = date.today()

    def result_for(leg: Dict) -> Optional[Dict]:
        k = matchday.key(leg.get("home", ""), leg.get("away", ""))
        try:
            d0 = date.fromisoformat(leg.get("date") or "")
        except ValueError:
            return None
        for d in (d0, d0 - timedelta(days=1), d0 + timedelta(days=1)):
            ds = d.isoformat()
            if ds not in days:
                days[ds] = _md_load(r, ds) if d <= today else {}
            e = days[ds].get(k)
            if e:
                return e.get("result")
        return None

    for uid in uids:
        key = _ukey(uid, "tickets")
        raw = r.get(key)
        items: List[Dict] = json.loads(raw) if raw else []
        changed = False
        for t in items:
            if t.get("status") in ("pending", "open"):
                before = sum(1 for l in t.get("legs") or [] if l.get("status") != "pending")
                if tickets.settle(t, result_for):
                    changed = True
                    report["legs"] += sum(1 for l in t.get("legs") or [] if l.get("status") != "pending") - before
                    if t["status"] in ("won", "lost", "void"):
                        report["settled"] += 1
                        r.hincrby(TICKETS_STATS_KEY, t["status"], 1)
                        if t["status"] == "won":   # the dashboard's Top predictors
                            r.zincrby("betiq:leaderboard", 1, uid)
        if changed:
            r.set(key, json.dumps(items, separators=(",", ":")), ex=365 * 86400)
        # Nothing left to settle (or only legs we can't settle, all played)
        def open_(t):
            if t.get("status") == "pending":
                return True
            last = max((l.get("date") or "" for l in t.get("legs") or []), default="")
            return t.get("status") == "open" and last >= (today - timedelta(days=3)).isoformat()
        if not any(open_(t) for t in items):
            r.srem(TICKETS_OPEN_KEY, uid)
    return report


@app.get("/api/admin/tickets")
async def admin_tickets(_admin: str = Depends(require_admin)):
    """Booking codes made by signed-in accounts: totals, by source, settled
    win rate, and codes per day for the last 14 days."""
    r = _get_redis()
    if not r:
        return {"error": "no_redis"}
    stats = {(k.decode() if isinstance(k, bytes) else k): int(v) for k, v in (r.hgetall(TICKETS_STATS_KEY) or {}).items()}
    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    counts = r.mget([f"betiq:tickets:day:{d}" for d in dates])
    won, lost = stats.get("won", 0), stats.get("lost", 0)
    return {"created": stats.get("created", 0), "won": won, "lost": lost, "void": stats.get("void", 0),
            "hit_rate": round(won / (won + lost), 3) if won + lost else None,
            "sources": {k.split(":", 1)[1]: v for k, v in stats.items() if k.startswith("source:")},
            "daily": [{"date": d, "codes": int(c or 0)} for d, c in zip(dates, counts)],
            "open_accounts": r.scard(TICKETS_OPEN_KEY)}


@app.get("/api/user/tickets")
async def get_tickets(request: Request, uid: str = ""):
    """The account's booking codes, each leg settled from the result, plus
    codes saved before tracking (no legs to settle)."""
    import tickets
    uid = await require_user(request, uid)
    r = _get_redis()
    if not r:
        return {"tickets": [], "summary": tickets.summary([]), "older": []}
    raw = r.get(_ukey(uid, "tickets"))
    items: List[Dict] = json.loads(raw) if raw else []
    tracked = {t.get("code") for t in items}
    old_raw = r.get(_ukey(uid, "codes"))
    older = [c for c in (json.loads(old_raw) if old_raw else []) if c.get("code") not in tracked]
    return {"tickets": items, "summary": tickets.summary(items), "older": older[:50]}


@app.post("/api/feedback/result")
async def submit_match_result(body: Dict[str, Any], _admin: str = Depends(require_admin)):
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
    if result not in ("H", "D", "A") or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date_s)):
        raise HTTPException(status_code=400, detail="result must be H/D/A and date YYYY-MM-DD")
    for score in (home_s, away_s):
        if score is not None and (not str(score).isdigit() or int(score) > 30):
            raise HTTPException(status_code=400, detail="scores must be whole numbers")
    if len(str(home)) > 80 or len(str(away)) > 80:
        raise HTTPException(status_code=400, detail="team names too long")
    _audit(_admin, "result", match=f"{home} v {away} {date_s}", result=result,
           score=f"{home_s}-{away_s}" if home_s is not None and away_s is not None else None)

    # Grade the match day's entry (needs the score: 1X2 alone can't settle goals markets)
    r = _get_redis()
    if r and home_s is not None and away_s is not None:
        import matchday
        try:
            day = _md_load(r, date_s)
            hit = matchday.match_results(day, [{"date": date_s, "home": home, "away": away}])
            for k, _ in hit:
                matchday.apply_result(day[k], {"status": "finished", "hg": int(home_s), "ag": int(away_s),
                                               "source": "admin"})
            if hit:
                _md_save(r, date_s, day)
                print(f"[Feedback] Graded {home} vs {away} ({date_s})")
        except Exception as e:
            print(f"[Feedback] Match day update error: {e}")

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

# Browser push services. The server POSTs notifications to a subscription's
# endpoint, so any other host would let callers aim our server at anything.
_PUSH_HOSTS = re.compile(r"^(fcm\.googleapis\.com|android\.googleapis\.com|updates\.push\.services\.mozilla\.com|"
                         r"[a-z0-9.-]+\.push\.apple\.com|[a-z0-9.-]+\.notify\.windows\.com)$")
MAX_PUSH_SUBS = 50_000


def _valid_push_subscription(sub: Any) -> bool:
    from urllib.parse import urlparse
    if not isinstance(sub, dict) or len(json.dumps(sub)) > 2000:
        return False
    url = urlparse(str(sub.get("endpoint") or ""))
    keys = sub.get("keys")
    return (url.scheme == "https" and bool(_PUSH_HOSTS.match(url.hostname or ""))
            and isinstance(keys, dict) and isinstance(keys.get("p256dh"), str) and isinstance(keys.get("auth"), str))


@app.post("/api/push/subscribe")
async def push_subscribe(req: Request):
    body = await req.json()
    sub = body.get("subscription") if isinstance(body, dict) else None
    if not _valid_push_subscription(sub):
        return {"ok": False, "error": "invalid subscription"}
    r = _get_redis()
    if r:
        if r.scard(PUSH_SUBS_KEY) >= MAX_PUSH_SUBS:
            return {"ok": False, "error": "full"}
        r.sadd(PUSH_SUBS_KEY, json.dumps({"endpoint": sub["endpoint"], "keys": {
            "p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]}}, sort_keys=True))
    return {"ok": True}

@app.delete("/api/push/subscribe")
async def push_unsubscribe(req: Request):
    body = await req.json()
    sub = body.get("subscription") if isinstance(body, dict) else None
    if not _valid_push_subscription(sub):
        return {"ok": False}
    r = _get_redis()
    if r:
        r.srem(PUSH_SUBS_KEY, json.dumps({"endpoint": sub["endpoint"], "keys": {
            "p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]}}, sort_keys=True))
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
            "icon": "/icon-192.png",
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

    await require_admin(request)

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


@app.get("/api/debug/basketball-provider")
async def debug_basketball_provider(date: str = "", league: str = "nba", _admin: str = Depends(require_admin)):
    """
    Raw diagnostic dump for the ESPN basketball integration.
    No API key required — the endpoints are public.

    Hits the scoreboard endpoint directly (bypasses fetch_scoreboard so
    zero-results vs HTTP error can be distinguished) and returns both the
    raw response and a normalized parse of the first few events.

    Example: /api/debug/basketball-provider
             /api/debug/basketball-provider?date=20260710
             /api/debug/basketball-provider?league=wnba
             /api/debug/basketball-provider?league=ncaam&date=20260301
    """
    from basketball_data_fetcher import ESPN_BASE, parse_completed_games
    from datetime import date as _date
    import httpx as _httpx

    dates_param = date or _date.today().strftime("%Y%m%d")
    url = f"{ESPN_BASE}/{league}/scoreboard"

    out: Dict = {
        "provider": "ESPN unofficial API (no key required)",
        "league": league,
        "dates_queried": dates_param,
        "url": url,
    }

    async with _httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, params={"dates": dates_param, "limit": 20})
        out["http_status"] = r.status_code
        try:
            body = r.json()
            events = body.get("events", [])
            out["event_count"] = len(events)
            out["parsed_sample"] = parse_completed_games(events[:5])
            raw_str = str(body)
            out["raw_sample"] = body if len(raw_str) < 4000 else raw_str[:4000]
        except Exception as exc:
            out["parse_error"] = str(exc)
            out["raw_sample"] = r.text[:2000]

    return out


@app.post("/api/admin/upload/tennis-csv")
async def upload_tennis_csv(request: Request):
    """
    Upload Jeff Sackmann ATP/WTA CSV files to train the tennis Elo model.
    Can upload multiple files — all go into data/tennis/ folder.
    Protected by ADMIN_SECRET header.
    Form fields: file (required), tour (atp|wta, optional)
    """
    await require_admin(request)

    form = await request.form()
    upload = form.get("file")
    if not upload:
        raise HTTPException(status_code=400, detail="No file provided")

    tennis_dir = os.path.join(DATA_DIR, "tennis")
    os.makedirs(tennis_dir, exist_ok=True)

    # The uploaded name picks the file on disk: keep only a plain *.csv name
    filename = os.path.basename(getattr(upload, "filename", None) or "") or "tennis_matches.csv"
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}\.csv", filename):
        raise HTTPException(status_code=400, detail="Upload a .csv file with a plain name")
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
async def model_status(_admin: str = Depends(require_admin)):
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
async def debug_calendar_status(_admin: str = Depends(require_admin)):
    """Quick diagnostic: shows what the calendar will return and what's in cache."""
    from datetime import date as _date
    month = _date.today().strftime("%Y-%m")
    r = _get_redis()
    cache_dates = sorted({p.get("date", p.get("Date",""))[:10] for p in _predictions_cache if p.get("date") or p.get("Date")})
    redis_keys = []
    if r:
        try:
            redis_keys = [k.decode() if isinstance(k, bytes) else k
                          for k in r.keys("betiq:md:*")]
        except Exception:
            pass
    return {
        "current_month": month,
        "predictions_in_cache": len(_predictions_cache),
        "prediction_dates": cache_dates[:10],
        "history_redis_keys": sorted(redis_keys)[:20],
        "sample_prediction_keys": list(_predictions_cache[0].keys()) if _predictions_cache else [],
    }


@app.get("/api/debug/pipeline")
async def debug_pipeline(_admin: str = Depends(require_admin)):
    """
    Pipeline / cache state — browser-friendly. Shows whether a refresh is
    running, when it last published, how many predictions are cached, the
    per-league breakdown, and any World Cup entries currently served.
    """
    by_league: Dict[str, int] = {}
    for p in _predictions_cache:
        lg = p.get("league", "?")
        by_league[lg] = by_league.get(lg, 0) + 1
    wc = [
        {"home": p.get("home"), "away": p.get("away"), "date": p.get("date")}
        for p in _predictions_cache if p.get("league") == "WC"
    ]
    return {
        "is_training": _is_training,
        "model_ready": _predictor is not None,
        "last_updated": _last_updated,
        "total_predictions": len(_predictions_cache),
        "predictions_by_league": dict(sorted(by_league.items())),
        "wc_predictions": wc,
    }


@app.get("/api/debug/team-form")
async def debug_team_form(team: str, live: bool = False, _admin: str = Depends(require_admin)):
    """
    Inspect (or force-refresh) the web-searched form/xG for one team — this is
    the pathway that supplies real xG for teams with sparse local history,
    e.g. World Cup national teams, since Understat only covers the top-5
    European club leagues and predictor.py's own "xG_Home"/"xG_Away" is a
    goals-based Dixon-Coles estimate, not real shot-based xG.

    By default reads whatever is cached (no Groq spend). Pass live=true to
    force a fresh Groq web search + extraction right now — useful
    to check whether the pathway works at all for a given team, or to see
    a fresh error, but costs one Groq call.

    Example: /api/debug/team-form?team=Argentina
             /api/debug/team-form?team=Argentina&live=true
    """
    from llm_service import GROQ_API_KEY

    cached = _get_web_form_cache(team)
    local_pts = len(_predictor.team_stats.get(_predictor.canon(team), {}).get("pts", [])) if _predictor else None

    out: Dict = {
        "team": team,
        "groq_api_key_set": bool(GROQ_API_KEY),
        "local_match_count": local_pts,
        "cached_web_form": cached,
    }
    if not cached:
        out["note"] = ("No cached web form for this team yet. It's fetched during the "
                       "pipeline's background prefetch for any team without one already "
                       "cached — NOT gated on local match count, since historical W/D/L "
                       "records (which national teams often have plenty of) contain no xG "
                       "at all. Capped at 25 teams per run, World Cup/major-tournament teams "
                       "prioritised. Pass live=true to fetch it right now instead of waiting.")

    if live:
        if not GROQ_API_KEY:
            out["live_fetch_error"] = "GROQ_API_KEY not set on the server"
        else:
            from llm_service import fetch_team_form_web
            fixture_comp = next(
                (p.get("league_name", "") for p in _predictions_cache
                 if p.get("home") == team or p.get("away") == team),
                "",
            )
            fresh = await fetch_team_form_web(team, competition=fixture_comp, debug=True)
            out["live_fetch_competition_hint"] = fixture_comp or None
            if fresh.get("_debug_stage"):
                # Failed — surface exactly where/why instead of pointing at
                # server logs the free-tier dashboard may not expose usefully.
                out["live_fetch_result"] = None
                out["live_fetch_failed_at"] = fresh["_debug_stage"]
                out["live_fetch_error"] = fresh["_debug_detail"]
                if "search_text" in fresh:
                    out["live_fetch_search_text_sample"] = fresh["search_text"]
            else:
                out["live_fetch_result"] = fresh or None
                if fresh and fresh.get("matches") and _predictor is not None:
                    # A successful live check is now also a "warm this team's
                    # cache right now" action — otherwise the data this call
                    # just fetched would be thrown away and the match page
                    # still wouldn't show it until the next full pipeline run.
                    _apply_web_form(_predictor, team, fresh)
                    out["cached"] = True
                elif not fresh:
                    out["live_fetch_error"] = "Unexpected empty result with no debug info."

    return out


@app.get("/api/debug/odds")
async def debug_odds(probe_sportybet: bool = True, probe_odds_api: bool = False, _admin: str = Depends(require_admin)):
    """
    Diagnose why market odds might be missing. There are TWO independent odds
    systems and either can fail on its own:
      1. The Odds API (the-odds-api.com) — powers the small 1X2 odds badges on
         prediction cards and value-bet detection. Free tier: 500 req/month.
      2. SportyBet (scraped, no quota) — powers the full market list + live
         odds shown in the Bet Builder / match analysis screen.

    By default this reports what's already cached (no extra Odds API calls,
    since that quota is scarce) and does one fresh SportyBet lookup (free).
    Pass probe_odds_api=true to force one live Odds API call — only do this
    if you need to see a fresh error message, since it spends quota.
    """
    out: Dict = {}

    # ── The Odds API — read from what's already cached, no extra spend ──────
    with_odds = sum(1 for p in _predictions_cache if p.get("odds_home"))
    with_value_flag = sum(1 for p in _predictions_cache if p.get("value_edge") is not None)
    out["odds_api"] = {
        "api_key_set": bool(os.getenv("ODDS_API_KEY", "")),
        "cached_predictions": len(_predictions_cache),
        "cached_predictions_with_odds_badge": with_odds,
        "cached_predictions_with_value_edge_computed": with_value_flag,
        "note": ("0 with odds while api_key_set=true usually means quota "
                 "exhausted (500/month) or no bookmaker coverage for these "
                 "leagues/dates yet.") if with_odds == 0 else None,
    }
    if probe_odds_api:
        try:
            from odds_fetcher import _fetch_odds_for_sport
            events = await _fetch_odds_for_sport("soccer_fifa_world_cup")
            out["odds_api"]["live_probe"] = {
                "sport": "soccer_fifa_world_cup",
                "events_returned": len(events),
            }
        except Exception as e:
            out["odds_api"]["live_probe"] = {"error": str(e)}

    # ── SportyBet — free to probe, no quota ──────────────────────────────────
    if probe_sportybet:
        try:
            from sportybet import fetch_events_for_date, find_event
            from datetime import date as _date, timedelta as _td
            found_any = {}
            wc_matches = [p for p in _predictions_cache if p.get("league") == "WC"][:3]
            for offset in range(0, 3):
                d = str(_date.today() + _td(days=offset))
                events = await fetch_events_for_date(d)
                found_any[d] = len(events)
                if events and wc_matches:
                    for m in wc_matches:
                        if m.get("date") == d:
                            ev = find_event(m["home"], m["away"], events)
                            found_any[f"match_{m['home']}_vs_{m['away']}"] = bool(ev)
            out["sportybet"] = {
                "events_by_date": found_any,
                "wc_fixtures_checked": [f"{m['home']} vs {m['away']} ({m['date']})" for m in wc_matches],
            }
        except Exception as e:
            out["sportybet"] = {"error": str(e)}

    return out


@app.get("/api/debug/fixtures")
async def debug_fixtures(league: str = "WC", days: int = 90, _admin: str = Depends(require_admin)):
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


@app.get("/api/debug/predict")
async def debug_predict(home: str, away: str, date_str: Optional[str] = None, _admin: str = Depends(require_admin)):
    """
    Explain a single prediction: the raw feature values the model actually
    saw (Elo, xG, market-implied odds, form, etc.) alongside the resulting
    probabilities. Use this to check whether a surprising pick (e.g. away
    favoured despite a big home Elo/xG edge) is a real bug or the market-odds
    features legitimately outweighing Elo/xG — the model uses ~27 features,
    not just those two, and Impl_Home/Impl_Draw/Impl_Away (derived from
    bookmaker odds) are typically the single strongest signal. If those odds
    got fuzzy-matched to the wrong fixture, this is exactly where it'd show.

    Example: /api/debug/predict?home=Arsenal&away=Chelsea
    """
    if _predictor is None or not getattr(_predictor, "_ready", False):
        raise HTTPException(status_code=503, detail="Model not ready")

    # Pull whatever odds we have cached for this exact fixture, same as the
    # live pipeline would have used, so the feature values shown here match
    # what's actually being served — not a fresh, potentially different fetch.
    cached_odds, odds_fetched_at = _load_cached_live_odds()
    key_candidates = [k for k in cached_odds if k.startswith(f"{home}:{away}:")]
    matched_odds = cached_odds.get(key_candidates[0]) if key_candidates else {}

    odds_home = float(matched_odds.get("1") or 0)
    odds_draw = float(matched_odds.get("X") or 0)
    odds_away = float(matched_odds.get("2") or 0)

    feats = _predictor._feats(home, away, odds_home, odds_draw, odds_away, match_date=date_str)
    prediction = _predictor.predict_match(home, away, odds_home, odds_draw, odds_away, match_date=date_str)

    elo_gap = feats["HomeElo"] - feats["AwayElo"]
    xg_gap = feats["xG_Home"] - feats["xG_Away"]

    # Flag cases where Elo/xG clearly favour one side but the model's win
    # probability favours the other — the thing the user actually asked about.
    flags = []
    if elo_gap > 50 and xg_gap > 0.2 and prediction and prediction["p_away"] > prediction["p_home"]:
        flags.append("Home leads on both Elo and xG, but the model favours Away — "
                      "check whether Impl_Home/Impl_Draw/Impl_Away below explain it.")
    if elo_gap < -50 and xg_gap < -0.2 and prediction and prediction["p_home"] > prediction["p_away"]:
        flags.append("Away leads on both Elo and xG, but the model favours Home — "
                      "check whether Impl_Home/Impl_Draw/Impl_Away below explain it.")
    if not key_candidates:
        flags.append("No cached live odds matched this exact fixture key — "
                      "Impl_Home/Draw/Away below are the league-average fallback, "
                      "not real market odds. This can happen for a new/renamed team "
                      "name or if this fixture hasn't been through the live-odds pass yet.")

    return {
        "home": home,
        "away": away,
        "prediction": prediction,
        "features": feats,
        "elo_gap_home_minus_away": round(elo_gap, 1),
        "xg_gap_home_minus_away": round(xg_gap, 3),
        "matched_live_odds": matched_odds or None,
        "live_odds_cache_age": odds_fetched_at,
        "flags": flags,
    }


@app.get("/api/explain")
async def explain_match(home: str, away: str, day: str = Query("", alias="date"),
                        _premium=Depends(require_premium)):
    """
    A plain-language AI preview of a match: the model's numbers (the same
    analysis the page shows), the teams' recent results and meetings from our
    own data, and team news published in the last week. Built once per match
    and kept until it has been played; rebuilt in the background when the
    news, the model or the results change.
    """
    from llm_service import explain_match as _explain
    import match_facts

    if _predictor is None:
        raise HTTPException(status_code=503, detail="Model not ready")
    fx, key, ko = _match_timing(home, away, day)
    analysis, a_info = await _analysis_cached(home, away, day)
    if not analysis:
        raise HTTPException(status_code=404, detail="Could not generate prediction")
    news, n_info = await _match_news(home, away, day)
    facts = await _facts_for(home, away, day or fx.get("date", ""))

    probs = {o["code"]: o["prob"] for m in analysis.get("markets", []) if m.get("id") == "1x2"
             for o in m.get("options", [])}
    prediction = {"p_home": probs.get("1", 0), "p_draw": probs.get("X", 0), "p_away": probs.get("2", 0)}
    facts_text = match_facts.text(facts, home, away)
    fp = match_cache.fingerprint(n_info.get("at"), {k: round(v, 2) for k, v in probs.items()}, facts_text)

    async def build():
        result = await _explain(home, away, analysis, prediction, news=news, facts=facts_text,
                                kickoff=day or fx.get("date", ""), competition=fx.get("league_name", ""))
        if not result.get("explanation"):
            return None
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

    result, info = await _mcache.get("explain", key, build, fp=fp, fresh_for=24 * 3600,
                                     ttl=match_cache.ttl_seconds(ko))
    if result is None:
        result = {"explanation": None, "sources": [], "model": None, "error": "all_models_failed"}
    return {**result, "news": news.get("items") or [], "news_checked_at": n_info.get("at"),
            "updated_at": info.get("at"),
            "refreshing": bool(info.get("refreshing") or n_info.get("refreshing"))}


@app.get("/api/admin/whoami")
async def admin_whoami(request: Request):
    """Whether this request is an admin (the admin page's sign-in check).
    A signed-in non-admin sees their Clerk user id, to add to ADMIN_USER_IDS."""
    via, uid = await _admin_identity(request)
    return {"admin": bool(via), "via": via, "user_id": uid,
            "clerk_admins_configured": bool(ADMIN_USER_IDS)}


@app.get("/api/admin/stats")
async def admin_stats(_admin: str = Depends(require_admin)):
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
            preds = _read_history(r, d)
            if not preds:
                continue
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


REVENUE_CACHE_SECONDS = 300
_revenue_cache: Dict[str, Any] = {"at": 0.0, "data": None}


def _revenue_summary(transactions: List[Dict], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Totals, a 30-day daily series and recent payments from Paystack's
    successful transactions (amounts in kobo)."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    by_day: Dict[str, float] = {}
    total = today_sum = week = month = 0.0
    customers = set()
    for t in transactions:
        naira = (t.get("amount") or 0) / 100
        total += naira
        email = ((t.get("customer") or {}).get("email") or "").lower()
        if email:
            customers.add(email)
        try:
            day = datetime.fromisoformat(str(t.get("paid_at") or t.get("created_at")).replace("Z", "+00:00")).date()
        except ValueError:
            continue
        age = (today - day).days
        today_sum += naira if age == 0 else 0
        week += naira if age < 7 else 0
        month += naira if age < 30 else 0
        if age < 30:
            by_day[day.isoformat()] = by_day.get(day.isoformat(), 0) + naira
    series = [{"date": (today - timedelta(days=i)).isoformat(),
               "amount": round(by_day.get((today - timedelta(days=i)).isoformat(), 0), 2)} for i in range(29, -1, -1)]
    return {
        "total_revenue": round(total, 2), "transaction_count": len(transactions),
        "today": round(today_sum, 2), "last_7_days": round(week, 2), "last_30_days": round(month, 2),
        "average": round(total / len(transactions), 2) if transactions else 0,
        "customers": len(customers), "series": series,
        "recent": [{"email": (t.get("customer") or {}).get("email", ""), "amount": (t.get("amount") or 0) / 100,
                    "date": (t.get("paid_at") or "")[:10], "reference": t.get("reference", ""),
                    "channel": t.get("channel") or ""} for t in transactions[:15]],
    }


@app.get("/api/admin/revenue")
async def admin_revenue(fresh: bool = False, _admin: str = Depends(require_admin)):
    paystack_key = os.getenv("PAYSTACK_SECRET_KEY", "")
    if not paystack_key:
        return {"error": "no_paystack_key"}
    if not fresh and _revenue_cache["data"] and time.time() - _revenue_cache["at"] < REVENUE_CACHE_SECONDS:
        return _revenue_cache["data"]
    transactions: List[Dict] = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for page in range(1, 6):  # up to 500 payments
                r = await client.get("https://api.paystack.co/transaction",
                                     params={"status": "success", "perPage": 100, "page": page},
                                     headers={"Authorization": f"Bearer {paystack_key}"})
                if r.status_code != 200:
                    if page == 1:
                        return {"error": f"paystack_{r.status_code}"}
                    break
                batch = r.json().get("data") or []
                transactions += batch
                if len(batch) < 100:
                    break
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    data = _revenue_summary(transactions)
    _revenue_cache.update(at=time.time(), data=data)
    return data


# ── Traffic (traffic.py) ──────────────────────────────────────────────────
_traffic = traffic.Traffic()
TRAFFIC_FLUSH_MINUTES = 5


def _traffic_key_ok(given: str) -> bool:
    import hmac
    expected = os.getenv("TRAFFIC_KEY") or ADMIN_SECRET
    return bool(expected and given) and hmac.compare_digest(given.encode(), expected.encode())


@app.post("/api/traffic/hit")
async def traffic_hit(request: Request, body: Dict[str, Any]):
    """A page view, sent by the site's own /api/t route (which adds the
    visitor's location) with the shared TRAFFIC_KEY (or ADMIN_SECRET)."""
    if not _traffic_key_ok(request.headers.get("x-traffic-key", "").strip()):
        raise HTTPException(status_code=403, detail="Forbidden")
    hit = traffic.clean_hit(body)
    if hit:
        _traffic.record(hit)
    return {"ok": bool(hit)}


def _flush_traffic() -> int:
    return _traffic.flush(_get_redis())


@app.get("/api/admin/traffic")
async def admin_traffic(days: int = 30, _admin: str = Depends(require_admin)):
    days = min(90, max(1, days))
    await asyncio.to_thread(_flush_traffic)
    data = await asyncio.to_thread(traffic.summary, _get_redis(), days)
    return {**data, "live": _traffic.live()}


@app.get("/api/admin/traffic/live")
async def admin_traffic_live(_admin: str = Depends(require_admin)):
    """Who's on the site in the last 5 minutes (memory only — cheap to poll)."""
    return _traffic.live()


# ── Audit log, security overview, jobs ────────────────────────────────────
@app.get("/api/admin/audit")
async def admin_audit(_admin: str = Depends(require_admin)):
    r = _get_redis()
    if not r:
        return {"entries": []}
    try:
        return {"entries": [json.loads(x) for x in r.lrange(AUDIT_KEY, 0, 99)]}
    except Exception:
        return {"entries": []}


@app.get("/api/admin/security")
async def admin_security(_admin: str = Depends(require_admin)):
    """How this server is protected, as checks the admin can act on, plus the
    recent security events (wrong admin secrets, rate limits)."""
    import auth
    events = security.recent_events(200)
    day_ago = time.time() - 86400
    recent = [e for e in events if e.get("at", 0) >= day_ago]
    checks = [
        {"id": "clerk_issuer", "ok": auth.auth_enforced(), "label": "Signed-in users verified (CLERK_ISSUER)",
         "fix": "Set CLERK_ISSUER on Render: without it, user data endpoints trust the uid the browser sends."},
        {"id": "premium", "ok": auth.premium_enforced(), "label": "Paywall enforced on the server (CLERK_SECRET_KEY)",
         "fix": "Set CLERK_SECRET_KEY on Render so premium analysis can't be fetched directly."},
        {"id": "admin_ids", "ok": bool(ADMIN_USER_IDS), "label": "Admins sign in with Clerk (ADMIN_USER_IDS)",
         "fix": "Add your Clerk user id to ADMIN_USER_IDS on Render and Vercel, then you rarely need the secret."},
        {"id": "admin_secret", "ok": len(ADMIN_SECRET) >= 24, "label": "Admin secret is long (24+ characters)",
         "fix": "Use a long random ADMIN_SECRET (e.g. `openssl rand -hex 24`) on Render and Vercel."},
        {"id": "traffic_key", "ok": bool(os.getenv("TRAFFIC_KEY")), "label": "Separate traffic key (TRAFFIC_KEY)",
         "fix": "Optional: set TRAFFIC_KEY on Render and Vercel so page-view reporting doesn't reuse the admin secret."},
        {"id": "redis", "ok": _get_redis() is not None, "label": "Redis connected",
         "fix": "Set UPSTASH_REDIS_URL on Render."},
        {"id": "rate_limits", "ok": os.getenv("RATE_LIMITS", "1") != "0", "label": "Rate limits on",
         "fix": "Remove RATE_LIMITS=0 from Render."},
        {"id": "cors", "ok": "*" not in ALLOWED_ORIGINS, "label": "Only our site may call the API from a browser",
         "fix": "Remove * from ALLOWED_ORIGINS."},
    ]
    counts: Dict[str, int] = {}
    for e in recent:
        counts[e.get("type", "?")] = counts.get(e.get("type", "?"), 0) + 1
    return {"checks": checks, "last_24h": counts, "events": events[:100], "allowed_origins": ALLOWED_ORIGINS,
            "rate_limits": [{"path": p, "limit": n, "per_seconds": w} for p, n, w in security.RATE_LIMITS]}


# Jobs an admin can run now (scheduler ids → what they do)
ADMIN_JOBS = {
    "refresh": ("Rebuild predictions", lambda: _run_pipeline()),
    "results_refresh": ("Fetch results and grade picks", lambda: _fetch_and_save_results()),
    "sportybet_links": ("Link to SportyBet", lambda: _link_sportybet_events("manual")),
    "fbref_refresh": ("Refresh corners/cards data (FBref)", lambda: _load_fbref_data()),
    "traffic_flush": ("Save traffic counts", lambda: asyncio.to_thread(_flush_traffic)),
    "referees": ("Find referees for upcoming matches", lambda: _refresh_referees("manual")),
    "fd_referees": ("Collect past referees (football-data.org)", lambda: _collect_fd_referees("manual")),
    "football_sync": ("Download league results (football-data.co.uk)", lambda: _manual_football_sync()),
    "matchday_sweep": ("Scores and grades for the last 7 days", lambda: _refresh_matchdays(MD_DAYS_BACK, "manual")),
    "set_pieces_reload": ("Reload corners, cards & shots models (after the nightly checks)",
                          lambda: _reload_set_pieces()),
}


async def _reload_set_pieces() -> Dict[str, Any]:
    """Load the international corners/cards/shots models with the latest
    nightly verdicts and re-price the current predictions — without a full
    rebuild."""
    await asyncio.to_thread(_load_international_set_pieces)
    with_referee = _apply_referees()
    return {"international_corners_cards": _intl_set_pieces is not None,
            "international_shots": _intl_shots is not None, "club_shots": _shots is not None,
            "predictions": len(_predictions_cache), "with_referee": with_referee}


@app.get("/api/admin/jobs")
async def admin_jobs(_admin: str = Depends(require_admin)):
    jobs = {j.id: j for j in scheduler.get_jobs()} if scheduler.running else {}
    return {"training": _is_training, "jobs": [
        {"id": jid, "name": name,
         "next_run": jobs[jid].next_run_time.isoformat() if jid in jobs and jobs[jid].next_run_time else None,
         "every": str(jobs[jid].trigger.interval) if jid in jobs and hasattr(jobs[jid].trigger, "interval") else None}
        for jid, (name, _) in ADMIN_JOBS.items()]}


@app.post("/api/admin/jobs/{job_id}/run")
async def admin_run_job(job_id: str, _admin: str = Depends(require_admin)):
    if job_id not in ADMIN_JOBS:
        raise HTTPException(status_code=404, detail="Unknown job")
    if job_id == "refresh" and _is_training:
        return {"started": False, "message": "Already refreshing"}
    _audit(_admin, "run_job", job=job_id)
    asyncio.create_task(ADMIN_JOBS[job_id][1]())
    return {"started": True, "message": f"{ADMIN_JOBS[job_id][0]} started"}


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
async def set_banner(body: Dict[str, Any], _admin: str = Depends(require_admin)):
    r = _get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    text = (body.get("text") or "").strip()[:300]
    if text:
        r.set("betiq:config:banner", text)
    else:
        r.delete("betiq:config:banner")
    _audit(_admin, "banner", text=text or "(cleared)")
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
async def set_maintenance(body: Dict[str, Any], _admin: str = Depends(require_admin)):
    r = _get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    enabled = bool(body.get("enabled", False))
    r.set("betiq:config:maintenance", "true" if enabled else "false")
    _audit(_admin, "maintenance", enabled=enabled)
    return {"enabled": enabled}


@app.post("/api/admin/clear-cache")
async def clear_cache(body: Dict[str, Any], _admin: str = Depends(require_admin)):
    r = _get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    try:
        r.delete("betiq:predictions")
        # Clear all explanation caches
        for key in r.scan_iter("betiq:explain:*"):
            r.delete(key)
        _audit(_admin, "clear_cache")
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
async def set_featured(body: Dict[str, Any], _admin: str = Depends(require_admin)):
    r = _get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    picks = [p for p in body.get("picks", []) if isinstance(p, dict)][:3]
    if picks:
        r.set("betiq:config:featured", json.dumps(picks))
    else:
        r.delete("betiq:config:featured")
    _audit(_admin, "featured", picks=[f"{p.get('home')} v {p.get('away')}" for p in picks] or "(cleared)")
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
async def get_saves(request: Request, uid: str = ""):
    uid = await require_user(request, uid)
    r = _get_redis()
    if not r: return []
    raw = r.get(_ukey(uid, "saves"))
    return json.loads(raw) if raw else []


def _save_key(p: Dict[str, Any]) -> str:
    return f"{p.get('home')}:{p.get('away')}:{p.get('date')}"


def _set_saved(uid: str, pred: Dict[str, Any], want: Optional[bool]) -> Dict[str, Any]:
    """Save (want=True), remove (False) or toggle (None) one prediction."""
    r = _get_redis()
    if not r: raise HTTPException(status_code=503, detail="No Redis")
    key = _ukey(uid, "saves")
    raw = r.get(key)
    saves: List[Dict] = json.loads(raw) if raw else []
    match_key = _save_key(pred)
    existing = next((i for i, s in enumerate(saves) if _save_key(s) == match_key), None)
    if want is None:
        want = existing is None
    if want and existing is None:
        saves.insert(0, pred)
        saves = saves[:50]
    elif not want and existing is not None:
        saves.pop(existing)
    r.set(key, json.dumps(saves), ex=365 * 86400)
    return {"saved": want, "count": len(saves)}


@app.post("/api/user/saves")
async def toggle_save(request: Request, body: Dict[str, Any]):
    """Body: {prediction, saved?}. `saved` true/false sets the state explicitly
    (idempotent — safe to retry, and what Undo uses); omitted, it toggles."""
    uid = await require_user(request, body.get("uid", ""))
    pred = body.get("prediction", {})
    if not pred: raise HTTPException(status_code=400, detail="Missing prediction")
    want = body.get("saved")
    return _set_saved(uid, pred, want if isinstance(want, bool) else None)


@app.delete("/api/user/saves")
async def delete_save(request: Request, home: str, away: str, date: str = "", uid: str = ""):
    """Remove one saved pick. Idempotent: removing something not saved is a no-op."""
    uid = await require_user(request, uid)
    return _set_saved(uid, {"home": home, "away": away, "date": date}, False)


@app.get("/api/user/bets")
async def get_bets(request: Request, uid: str = ""):
    uid = await require_user(request, uid)
    r = _get_redis()
    if not r: return []
    raw = r.get(_ukey(uid, "bets"))
    return json.loads(raw) if raw else []


@app.post("/api/user/bets")
async def log_bet(request: Request, body: Dict[str, Any]):
    uid = await require_user(request, body.get("uid", ""))
    bet = body.get("bet", {})
    if not bet: raise HTTPException(status_code=400, detail="Missing bet")
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
async def get_codes(request: Request, uid: str = ""):
    uid = await require_user(request, uid)
    r = _get_redis()
    if not r: return []
    raw = r.get(_ukey(uid, "codes"))
    return json.loads(raw) if raw else []


@app.post("/api/user/codes")
async def save_code(request: Request, body: Dict[str, Any]):
    uid = await require_user(request, body.get("uid", ""))
    entry = body.get("entry", {})
    if not entry: raise HTTPException(status_code=400, detail="Missing entry")
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
async def get_user_stats(request: Request, uid: str = ""):
    uid = await require_user(request, uid)
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

    # The record that counts: codes booked here, settled from the results
    import tickets
    tickets_raw = r.get(_ukey(uid, "tickets"))
    booked = tickets.summary(json.loads(tickets_raw) if tickets_raw else [])
    return {
        "tickets": booked,
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
async def get_prefs(request: Request, uid: str = ""):
    uid = await require_user(request, uid)
    r = _get_redis()
    if not r: return {}
    raw = r.get(_ukey(uid, "prefs"))
    return json.loads(raw) if raw else {"followed_leagues": [], "digest": False}


@app.post("/api/user/prefs")
async def set_prefs(request: Request, body: Dict[str, Any]):
    uid = await require_user(request, body.get("uid", ""))
    r = _get_redis()
    if not r: raise HTTPException(status_code=503, detail="No Redis")
    prefs = {k: v for k, v in body.items() if k != "uid"}
    r.set(_ukey(uid, "prefs"), json.dumps(prefs), ex=365 * 86400)
    return {"ok": True}


# "table_tennis" is how a table-tennis prediction names its sport (the event modal uses it)
SPORTS = ("basketball", "tennis", "table-tennis", "table_tennis")


def _sportybet_event_id(sport: str, home: str, away: str, day: str) -> Optional[str]:
    """The SportyBet event behind a tennis / table-tennis prediction, from the cached list."""
    key = "table-tennis" if sport.startswith("table") else sport
    cached = None
    r = _get_redis()
    if r:
        try:
            cached = json.loads(r.get(f"betiq:sports:{key}") or "null")
        except Exception:
            cached = None
    if cached is None:
        cached = (_sports_memory_cache.get(key) or (None,))[0]
    for p in cached or []:
        if p.get("home") == home and p.get("away") == away and p.get("date") == day and p.get("sportybet_event_id"):
            return str(p["sportybet_event_id"])
    return None


def _check_sport(sport: str) -> None:
    # Sport names go into cache keys and outside API calls: known ones only
    if sport not in SPORTS:
        raise HTTPException(status_code=404, detail="Unknown sport")


@app.get("/api/sports/{sport}/leagues")
async def get_sport_leagues(sport: str):
    """Return distinct leagues/tournaments being predicted for a sport."""
    _check_sport(sport)
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


_sports_memory_cache: Dict[str, tuple] = {}  # sport -> (data, fetched_at_monotonic)


@app.get("/api/sports/{sport}")
async def get_sport_predictions(sport: str):
    """
    Multi-sport predictions endpoint.
    sport: basketball | tennis | table-tennis
    Requires ODDS_API_KEY env var.
    """
    _check_sport(sport)
    import time as _time
    from sports_fetcher import (
        fetch_basketball_predictions,
        fetch_tennis_predictions,
        fetch_table_tennis_predictions,
    )
    from event_filters import drop_started_events
    import json as _json

    CACHE_TTL = 3600  # 1 hour — conserves Odds API quota
    cache_key = f"betiq:sports:{sport}"
    r = _get_redis()

    if r:
        try:
            cached = r.get(cache_key)
            if cached:
                return drop_started_events(_json.loads(cached))
        except Exception:
            pass
    else:
        # No Redis (or it's briefly down) — fall back to an in-process cache
        # so this endpoint still doesn't hit The Odds API on every request.
        # Without this, any Redis outage turns every page view into a fresh
        # API call, burning the free 500/month quota within hours.
        cached_entry = _sports_memory_cache.get(sport)
        if cached_entry and (_time.monotonic() - cached_entry[1]) < CACHE_TTL:
            return drop_started_events(cached_entry[0])

    if sport == "basketball":
        data = await fetch_basketball_predictions()
    elif sport == "tennis":
        data = await fetch_tennis_predictions()
    elif sport == "table-tennis":
        data = await fetch_table_tennis_predictions()
    else:
        raise HTTPException(status_code=400, detail=f"Unknown sport: {sport}")

    if r:
        try:
            # An empty list (a source down) is retried in 5 minutes, not an hour
            r.setex(cache_key, CACHE_TTL if data else 300, _json.dumps(data))
        except Exception:
            pass
    elif data:
        _sports_memory_cache[sport] = (data, _time.monotonic())

    return drop_started_events(data)


@app.get("/api/team-logo")
async def get_team_logo(name: str):
    """
    Generic team badge/logo lookup. Tries the football-data.org crest cache
    first — crests for every team in our tracked football competitions
    (World Cup, Premier League, ...) are captured for free from the fixtures
    the pipeline already fetches, no extra API call needed and no fuzzy name
    matching required. Falls back to the TheSportsDB-backed generic lookup
    for anything else (basketball, tennis, a team not yet seen in a fixture
    window). Cached server-side (Redis, 30 days) since badges don't change.
    """
    from team_logos import lookup_team_logo

    logo = _get_cached_team_crest(name)
    source = "football-data.org" if logo else None

    if not logo:
        r = _get_redis()
        logo = await lookup_team_logo(name, redis_client=r)
        source = "thesportsdb" if logo else None

    return {"name": name, "logo": logo, "source": source}


@app.get("/api/debug/team-logo")
async def debug_team_logo(name: str, _admin: str = Depends(require_admin)):
    """
    Diagnose the team badge lookup: whether football-data.org's crest cache
    (populated for free from the fixtures the pipeline fetches — see
    _cache_team_crest in _run_pipeline) has this team, and if not, the raw
    TheSportsDB fallback lookup. A team only appears in the crest cache once
    the pipeline has fetched at least one fixture involving them within the
    90-day fixture window, so a team with no upcoming match (e.g. a
    tournament they didn't qualify for) will always fall through to
    TheSportsDB.
    """
    from team_logos import lookup_team_logo

    out: Dict = {"name": name}
    out["football_data_crest_cached"] = _get_cached_team_crest(name)

    r = _get_redis()
    out["thesportsdb_logo"] = await lookup_team_logo(name, redis_client=r)

    out["logo"] = out["football_data_crest_cached"] or out["thesportsdb_logo"]
    out["source"] = (
        "football-data.org" if out["football_data_crest_cached"]
        else ("thesportsdb" if out["thesportsdb_logo"] else None)
    )
    return out


_LEAGUE_NAME_TO_CODE = {info["name"].lower(): code for code, info in LEAGUES.items()}

# Single shared client (and its rate-limit semaphore) for on-demand
# football-data.org calls made outside the paced pipeline loop — so
# concurrent requests (e.g. several browsers loading different league badges
# at once) serialize against the same 10 req/min budget instead of each
# spawning its own unthrottled client.
_fd_ondemand_client: Optional[FootballDataClient] = None


def _get_fd_client() -> Optional[FootballDataClient]:
    global _fd_ondemand_client
    if not API_KEY:
        return None
    if _fd_ondemand_client is None:
        _fd_ondemand_client = FootballDataClient(API_KEY)
    return _fd_ondemand_client


def _competition_emblem_cache_key(code: str) -> str:
    return f"betiq:fd_emblem:{code}"


def _cache_competition_emblem(code: str, emblem: Optional[str]) -> None:
    r = _get_redis()
    if not r:
        return
    try:
        r.setex(_competition_emblem_cache_key(code), 60 * 60 * 24 * 30, emblem or "")  # 30 days
    except Exception:
        pass


def _has_cached_competition_emblem(code: str) -> bool:
    """
    True only for a confirmed, truthy cached emblem. A missing key or a
    cached empty string are both treated as "not yet known" so a stale
    negative — e.g. from a request that failed under 429 rate-limiting
    during a burst of concurrent lookups — gets retried rather than trusted
    for the full 30-day TTL.
    """
    r = _get_redis()
    if not r:
        return False
    try:
        return bool(r.get(_competition_emblem_cache_key(code)))
    except Exception:
        return False


async def _get_football_competition_emblem(name: str) -> Optional[str]:
    """
    football-data.org emblem for one of our own football leagues — a real,
    already-paid-for API key rather than TheSportsDB's rate/data-limited free
    "3" test key, and our league codes ("WC", "PL", ...) map exactly to
    football-data.org's own competition codes, so no fuzzy name matching is
    needed at all. Returns None for anything not in our LEAGUES dict (e.g. a
    basketball competition) or if the API key isn't configured.

    Normally this is pre-populated by the pipeline (see _run_pipeline), which
    fetches each league's emblem once (paced alongside its fixture fetches)
    and caches it for 30 days — this on-demand path only fires as a fallback
    for a request that lands before the first pipeline run, or for a league
    whose cache is still empty/stale.
    """
    code = _LEAGUE_NAME_TO_CODE.get(name.strip().lower())
    if not code or not API_KEY:
        return None

    if _has_cached_competition_emblem(code):
        return _get_redis().get(_competition_emblem_cache_key(code))

    try:
        emblem = await _get_fd_client().fetch_competition_emblem(code)
    except Exception as e:
        # Do NOT cache failures — an outage or a transient 429 shouldn't be
        # remembered as "this competition has no emblem" for a month.
        print(f"[CompetitionLogo] football-data.org emblem fetch failed for {code}: {e}")
        return None

    _cache_competition_emblem(code, emblem)
    return emblem


def _team_crest_cache_key(team_name: str) -> str:
    return f"betiq:team_crest:{team_name.strip().lower()}"


def _cache_team_crest(team_name: str, crest_url: Optional[str]) -> None:
    """
    Persist a team's football-data.org crest, captured for free from a
    fixtures response (no extra API call). Only writes truthy values — a
    fixture missing a crest for one team shouldn't overwrite/block a crest
    learned from a different fixture, or block the TheSportsDB fallback.
    """
    if not crest_url or not team_name or not team_name.strip():
        return
    r = _get_redis()
    if not r:
        return
    try:
        r.setex(_team_crest_cache_key(team_name), 60 * 60 * 24 * 30, crest_url)  # 30 days
    except Exception:
        pass


def _get_cached_team_crest(team_name: str) -> Optional[str]:
    if not team_name or not team_name.strip():
        return None
    r = _get_redis()
    if not r:
        return None
    try:
        return r.get(_team_crest_cache_key(team_name)) or None
    except Exception:
        return None


@app.get("/api/competition-logo")
async def get_competition_logo(name: str, sport: str = "Soccer"):
    """
    Competition/league badge lookup (World Cup, Premier League, EuroLeague,
    etc.). For football competitions we already know (our own LEAGUES dict),
    tries football-data.org's emblem first — a real API key with exact code
    matching, no fuzzy search needed. Falls back to the generic
    TheSportsDB-backed lookup (used for basketball etc., or if the football
    league isn't one of ours). Cached server-side (Redis, 30 days).
    """
    from competition_logos import lookup_competition_logo

    # International competitions: the badge their fixture source supplied
    logo = _international_competition_logo(name)
    if logo:
        return {"name": name, "sport": sport, "logo": logo, "source": "fixture-source"}

    logo = await _get_football_competition_emblem(name)
    source = "football-data.org" if logo else None

    if not logo:
        r = _get_redis()
        logo = await lookup_competition_logo(name, redis_client=r, sport=sport)
        source = "thesportsdb" if logo else None

    return {"name": name, "sport": sport, "logo": logo, "source": source}


@app.get("/api/debug/competition-logo")
async def debug_competition_logo(name: str, sport: str = "Soccer", _admin: str = Depends(require_admin)):
    """
    Diagnose the competition logo lookup end-to-end.

    Primary path (football only): football-data.org's /competitions/{code}
    emblem field, using our own LEAGUES dict to map `name` -> code exactly
    (no fuzzy matching needed, and it's a real paid key rather than
    TheSportsDB's rate/data-limited free "3" test key).

    Fallback path (any sport, or if the football-data.org lookup found
    nothing): TheSportsDB's all_leagues.php (the one endpoint that takes NO
    query params, so its shape can't be guessed wrong) dumps every league
    across every sport; we filter to `sport` and fuzzy-match `name` against
    it, then lookupleague.php?id=X for the badge. TheSportsDB's free test key
    is known to return only a handful of domestic leagues (no World Cup), so
    this is mainly relevant for basketball etc.

    Example: /api/debug/competition-logo?name=World%20Cup
    (URL-encode the space — "World Cup" unencoded gets truncated to "World"
    by some HTTP clients/shells.)
    """
    from competition_logos import (
        lookup_competition_logo, _NAME_ALIASES, _get_all_leagues,
        find_best_league_match, _sim, SPORTSDB_BASE,
    )
    import httpx as _httpx

    out: Dict = {"name": name, "sport": sport}

    # --- football-data.org path ---
    fd_code = _LEAGUE_NAME_TO_CODE.get(name.strip().lower())
    r = _get_redis()
    cached_raw = None
    if fd_code and r:
        try:
            cached_raw = r.get(_competition_emblem_cache_key(fd_code))
        except Exception:
            pass
    out["football_data"] = {
        "matched_league_code": fd_code,
        "api_key_set": bool(API_KEY),
        "redis_cached_value": cached_raw,
        "redis_cache_treated_as_confirmed": bool(cached_raw) if fd_code else None,
    }
    if fd_code and API_KEY:
        try:
            url = f"{API_BASE}/competitions/{fd_code}"
            async with _httpx.AsyncClient() as client:
                resp = await client.get(url, headers={"X-Auth-Token": API_KEY}, timeout=20)
            out["football_data"]["status"] = resp.status_code
            if resp.status_code == 200:
                data = resp.json()
                out["football_data"]["emblem"] = data.get("emblem")
            else:
                out["football_data"]["raw_response_sample"] = resp.text[:500]
        except Exception as e:
            out["football_data"]["error"] = str(e)

    fd_emblem = out["football_data"].get("emblem")
    if fd_emblem:
        # Heal the shared cache immediately rather than waiting for the next
        # pipeline run — useful right after a fix like this one, where the
        # cache may hold a stale negative result from before the fix.
        _cache_competition_emblem(fd_code, fd_emblem)
        out["football_data"]["cache_healed"] = True
        out["logo"] = fd_emblem
        out["source"] = "football-data.org"
        return out

    # --- TheSportsDB fallback path ---
    alias = _NAME_ALIASES.get(name.strip().lower())
    search_name = alias or name
    out["alias_used"] = alias
    out["search_name"] = search_name

    async with _httpx.AsyncClient(timeout=20) as client:
        list_resp = await client.get(f"{SPORTSDB_BASE}/all_leagues.php")
        out["all_leagues_status"] = list_resp.status_code
        if list_resp.status_code != 200:
            out["all_leagues_raw_sample"] = list_resp.text[:500]
            r = _get_redis()
            out["logo"] = await lookup_competition_logo(name, redis_client=r, sport=sport)
            return out

        leagues = await _get_all_leagues(client)
        out["total_leagues_all_sports"] = len(leagues)
        out["distinct_sports_seen"] = sorted({lg.get("strSport") for lg in leagues if lg.get("strSport")})[:20]
        sport_leagues = [lg for lg in leagues if (lg.get("strSport") or "").lower() == sport.lower()]
        out["leagues_found_for_sport"] = len(sport_leagues)
        out["sample_league_names_for_sport"] = [lg.get("strLeague") for lg in sport_leagues[:8]]

        match = find_best_league_match(search_name, leagues, sport=sport)
        out["best_match"] = {
            "name": match.get("strLeague"),
            "id": match.get("idLeague"),
            "sport": match.get("strSport"),
            "score": round(_sim(search_name, match.get("strLeague") or ""), 3),
        } if match else None

        if match and match.get("idLeague"):
            resp = await client.get(f"{SPORTSDB_BASE}/lookupleague.php", params={"id": match["idLeague"]})
            out["lookupleague_status"] = resp.status_code
            out["lookupleague_response_sample"] = resp.text[:800]

    r = _get_redis()
    out["logo"] = await lookup_competition_logo(name, redis_client=r, sport=sport)
    out["source"] = "thesportsdb" if out["logo"] else None

    return out


@app.get("/api/sports/{sport}/event")
async def get_sport_event_detail(sport: str, home: str, away: str, date: str):
    """
    Full market detail for a specific basketball/tennis/table-tennis match.
    Used by the sport analysis modal.
    """
    _check_sport(sport)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or len(home) > 80 or len(away) > 80:
        raise HTTPException(status_code=400, detail="Invalid match")
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

    detail = None
    # Matches taken from SportyBet's listing: its own match page has every market
    event_id = _sportybet_event_id(sport, home, away, date)
    if event_id:
        try:
            import sportybet
            from sports_fetcher import structure_sportybet_detail
            ev = await sportybet.event_page(event_id)
            if ev:
                detail = structure_sportybet_detail(ev, "table_tennis" if sport.startswith("table") else sport)
        except Exception as e:
            print(f"[Sports Detail] SportyBet {event_id}: {e}")
    if not detail:
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
    Returns predictions where BetIQ's model probability beats the market's
    implied probability by >= 3%. Results cached in Redis for 30 min.

    Odds are read from the same throttled odds cache the pipeline maintains
    (_get_live_odds_throttled) instead of calling The Odds API directly —
    this used to only cache non-empty results, so once the API's free quota
    was exhausted, EVERY page view retried the dead API with no backoff,
    which kept the quota from ever recovering. Now this endpoint never calls
    The Odds API on its own; it just reads whatever the pipeline last fetched.
    """
    from odds_fetcher import compute_value_bets
    import json as _json

    CACHE_KEY = "betiq:value_bets"
    r = _get_redis()

    if r:
        try:
            cached = r.get(CACHE_KEY)
            if cached is not None:
                return _json.loads(cached)
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
        import value_bets as vb
        now = datetime.now(timezone.utc)
        today, clock = now.date().isoformat(), now.strftime("%H:%M")
        upcoming = [p for p in preds if p.get("sport") in (None, "football")
                    and (p.get("date", "") > today or (p.get("date") == today and (p.get("time") or "99:99") > clock))
                    and _within_window(p.get("date", ""))]
        # SportyBet's own prices for linked matches (bookable), then The Odds
        # API's cached 1X2 odds for matches SportyBet doesn't list
        found: Dict[str, Dict] = {}
        for p in upcoming:
            v = vb.best_value(p, _linked_event(p))
            if v:
                found[_sb_key(p["home"], p["away"], p["date"])] = v
        odds_index, _ = _load_cached_live_odds()
        for v in compute_value_bets(upcoming, odds_index):
            found.setdefault(_sb_key(v["home"], v["away"], v["date"]), v)
        value_bets = sorted(found.values(), key=lambda x: -x["edge"])
        print(f"[ValueBets] Found {len(value_bets)} value bets ({sum(v.get('bookie') == 'SportyBet' for v in value_bets)} "
              f"priced by SportyBet, {len(odds_index)} cached Odds API entries)")

        if r:
            try:
                # Cache even empty results (briefly) — the odds source is
                # already throttled upstream, this just avoids recomputing
                # compute_value_bets() on every request during quiet periods.
                ttl = 1800 if value_bets else 300
                r.setex(CACHE_KEY, ttl, _json.dumps(value_bets))
            except Exception:
                pass

        return value_bets
    except Exception as e:
        print(f"[ValueBets] Error: {e}")
        return []


@app.get("/api/leaderboard")
async def get_leaderboard(request: Request, uid: str = ""):
    """Top predictors. Public, so it never returns user ids (those are what the
    user endpoints key on); `you` marks the caller's own row when signed in."""
    r = _get_redis()
    if not r: return []
    # Verified user when auth is enforced; the legacy client uid otherwise
    me = await optional_user(request) if auth_enforced() else (uid or None)
    try:
        entries = r.zrevrange("betiq:leaderboard", 0, 19, withscores=True)
        return [{"name": f"#{uid[-6:]}", "wins": int(score), "you": uid == me} for uid, score in entries]
    except Exception:
        return []


@app.post("/api/track/match")
async def track_match(body: Dict[str, Any]):
    home = str(body.get("home", ""))
    away = str(body.get("away", ""))
    if not home or not away: return {"ok": True}
    # Only real fixtures, so junk can't grow the stats without bound
    if not any(p.get("home") == home and p.get("away") == away for p in _predictions_cache):
        return {"ok": True}
    r = _get_redis()
    if r:
        try: r.zincrby("betiq:stats:matches", 1, f"{home} vs {away}")
        except Exception: pass
    return {"ok": True}


@app.post("/api/track/league")
async def track_league(body: Dict[str, Any]):
    league = str(body.get("league", ""))
    if not re.fullmatch(r"[A-Za-z0-9-]{1,16}", league): return {"ok": True}
    r = _get_redis()
    if r:
        try: r.zincrby("betiq:stats:leagues", 1, league)
        except Exception: pass
    return {"ok": True}


@app.get("/api/admin/popular")
async def get_popular(_admin: str = Depends(require_admin)):
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
async def get_referral_stats(request: Request, uid: str = ""):
    uid = await require_user(request, uid)
    r = _get_redis()
    if not r: return {"count": 0}
    code = f"ref_{uid[-8:]}"
    count = int(r.get(f"betiq:referral:{code}:count") or 0)
    return {"code": code, "count": count, "link": f"https://predict-withbetiq.vercel.app?ref={code}"}


@app.post("/api/referral/use")
async def use_referral(request: Request, body: Dict[str, Any]):
    """Called when a new user signs up with a referral code: counts once per
    signed-in user, never for their own code."""
    uid = await require_user(request, body.get("uid", ""))
    code = str(body.get("code", "")).strip()
    if not re.fullmatch(r"ref_[A-Za-z0-9]{1,8}", code) or code == f"ref_{uid[-8:]}":
        return {"ok": False}
    r = _get_redis()
    if r:
        try:
            if r.set(f"betiq:referral:used:{uid}", code, nx=True):
                r.incr(f"betiq:referral:{code}:count")
        except Exception:
            pass
    return {"ok": True}


@app.get("/api/debug/odds-sample")
async def debug_odds_sample(_admin: str = Depends(require_admin)):
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
            if r:   # SportyBet may list it later: look again in 10 minutes
                try: r.setex(CACHE_KEY, 600, json.dumps({"found": False, "markets": []}))
                except Exception: pass
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


# ── SportyBet links: predictions matched to SportyBet events ahead of time ──
# Booking then needs no event listing, just one request for the code.
SB_LINKS_KEY = "betiq:sportybet:links"
# The last linking run's result, so a restart (a new deploy) shows it at once
SB_LINK_STATUS_KEY = "betiq:sportybet:link_status"
SB_LINK_MINUTES = 30
_sb_links: Dict[str, Dict] = {}
_sb_link_status: Dict[str, Any] = {"at": None, "events": 0, "predictions": 0, "linked": 0, "report": []}
_sb_link_lock: Optional[Tuple[Any, asyncio.Lock]] = None  # (event loop, lock)


def _link_lock() -> asyncio.Lock:
    """One linking run at a time (a lock belongs to one event loop)."""
    global _sb_link_lock
    loop = asyncio.get_running_loop()
    if _sb_link_lock is None or _sb_link_lock[0] is not loop:
        _sb_link_lock = (loop, asyncio.Lock())
    return _sb_link_lock[1]


def _sb_key(home: str, away: str, day: str) -> str:
    return f"{home}|{away}|{day}"


def _kickoff_ms(p: Dict) -> Optional[int]:
    try:
        dt = datetime.strptime(f"{p['date']} {p.get('time') or ''}", "%Y-%m-%d %H:%M")
    except (KeyError, ValueError):
        return None
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _match_predictions_to_events(preds: List[Dict], events: List[Dict],
                                 unlinked: Optional[List[Dict]] = None) -> Dict[str, Dict]:
    """{prediction key: slim SportyBet event}: by both team names, else by
    kick-off time plus one clear name. Unmatched predictions (with the
    closest SportyBet event) go to `unlinked`. CPU-bound: run in a thread."""
    import sportybet
    by_day: Dict[str, List[Dict]] = {}
    for ev in events:
        by_day.setdefault(sportybet._utc_day(ev) or "", []).append(ev)
    links: Dict[str, Dict] = {}
    for p in preds:
        d = date.fromisoformat(p["date"])
        near = [ev for k in {(d + timedelta(days=o)).isoformat() for o in (-1, 0, 1)} for ev in by_day.get(k, [])]
        ev = sportybet.find_event(p["home"], p["away"], near)
        kickoff = _kickoff_ms(p)
        if ev is None and kickoff is not None:
            ev = sportybet.find_event_by_kickoff(p["home"], p["away"], kickoff, near)
        if ev and ev.get("eventId"):
            links[_sb_key(p["home"], p["away"], p["date"])] = sportybet.slim_event(ev)
        elif unlinked is not None:
            guess, score = sportybet.closest_event(p["home"], p["away"], near)
            unlinked.append({
                "match": f"{p['home']} vs {p['away']}", "date": p["date"], "time": p.get("time") or "",
                "league": p.get("league_name") or p.get("league") or "",
                "closest": f"{guess['homeTeamName']} vs {guess['awayTeamName']}" if guess else None,
                "score": round(score, 2),
            })
    return links


_INTL_TOURNAMENT = re.compile(r"international|friendl|nations league|qualif|world cup|africa|afcon|"
                              r"concacaf|conmebol|uefa|asian cup|euro\b", re.I)


def _catalog_summary(events: List[Dict]) -> Dict[str, Any]:
    """How SportyBet's listing is spread over the coming days, and which
    national-team competitions it carries (to tell "not offered yet" from
    "not fetched")."""
    import sportybet
    days: Dict[str, int] = {}
    tournaments: Dict[str, int] = {}
    for ev in events:
        day = sportybet._utc_day(ev) or "?"
        days[day] = days.get(day, 0) + 1
        name = ev.get("_tournament") or ""
        if name and _INTL_TOURNAMENT.search(name):
            tournaments[name] = tournaments.get(name, 0) + 1
    return {"days": dict(sorted(days.items())[:21]),
            "international": dict(sorted(tournaments.items(), key=lambda kv: -kv[1])[:20])}


MARKET_PAGES = 3   # SportyBet match pages read to confirm market names


async def _market_map(events: List[Dict], links: Dict[str, Dict], report: List[str]) -> Tuple[Dict, Dict]:
    """(booking_slip.resolve_markets result, {market id: label}): SportyBet's
    markets from the listing plus one linked club match's own page, which
    lists every market with its outcomes."""
    import booking_slip
    import sportybet
    details = sportybet.market_details(events)
    league = {_sb_key(p.get("home", ""), p.get("away", ""), p.get("date", "")): p.get("league", "")
              for p in _predictions_cache}
    # The biggest leagues carry the most markets: try those first
    rank = {"PL": 0, "PD": 1, "SA": 2, "BL1": 3, "FL1": 4}
    club = [(key, ev) for key, ev in links.items() if not intl.is_international(league.get(key, ""))]
    club.sort(key=lambda kv: rank.get(league.get(kv[0], ""), 9))
    probes = [ev for _, ev in club[:4]]
    # None linked (an international break): any top-league match SportyBet lists
    top = re.compile(r"premier league|laliga|la liga|serie a|bundesliga|ligue 1", re.I)
    probes += [ev for ev in events if top.search(ev.get("_tournament") or "") and "women" not in (ev.get("_tournament") or "").lower()][:4]
    # Several pages, merged: specials such as shots are only on some matches
    seen, read, from_pages = set(), 0, set()
    for probe in probes:
        if read >= MARKET_PAGES or str(probe.get("eventId")) in seen:
            continue
        seen.add(str(probe.get("eventId")))
        try:
            page = await sportybet.event_market_details(str(probe["eventId"]))
            for mid, d in page.items():   # a page's entry beats the listing's; the first page's wins
                if mid not in from_pages:
                    details[mid] = d
                    from_pages.add(mid)
            read += 1
            report.append(f"match page {probe.get('homeTeamName')} v {probe.get('awayTeamName')}: {len(page)} markets")
        except Exception as e:
            report.append(f"match page: {type(e).__name__}: {e}")
    labels = {mid: d["label"] for mid, d in sorted(details.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0)}
    return booking_slip.resolve_markets({"markets": details}), labels


def _market_coverage(links: Dict[str, Dict]) -> Dict[str, int]:
    """How many linked matches carry each market we book, with a price."""
    counts: Dict[str, int] = {}
    for ev in links.values():
        for m in {str(m.get("id")) for m in ev.get("markets") or []}:
            counts[m] = counts.get(m, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0))


def _save_link_status() -> None:
    r = _get_redis()
    if r:
        try:
            r.set(SB_LINK_STATUS_KEY, json.dumps(_sb_link_status, default=str), ex=3 * 24 * 3600)
        except Exception as e:
            print(f"[SportyBet] Could not save link status: {e}")


def _restore_link_status() -> None:
    """The last run's result (from before this restart), until this server's first run."""
    r = _get_redis()
    if r and not _sb_link_status.get("at"):
        try:
            saved = json.loads(r.get(SB_LINK_STATUS_KEY) or "{}")
            if saved:
                _sb_link_status.update(saved)
        except Exception:
            pass


def _sb_market_map() -> Dict[str, Dict[str, Any]]:
    if not _sb_link_status.get("market_map"):
        _restore_link_status()
    return _sb_link_status.get("market_map") or {}


async def _link_sportybet_events(trigger: str = "schedule") -> Dict[str, Any]:
    """Match upcoming football predictions to SportyBet events and keep the
    links. `trigger` says what started the run: startup (a new deploy or
    restart), pipeline, international, schedule or manual."""
    async with _link_lock():
        return await _link_sportybet_events_now(trigger)


async def _link_sportybet_events_now(trigger: str) -> Dict[str, Any]:
    import sportybet
    today = date.today().isoformat()
    preds = [p for p in _predictions_cache
             if p.get("sport") in (None, "football") and p.get("home") and p.get("away")
             and today <= p.get("date", "") and _within_window(p.get("date", ""))]
    started = time.monotonic()
    status: Dict[str, Any] = {"at": datetime.now(timezone.utc).isoformat(), "trigger": trigger,
                              "deploy": (os.getenv("RENDER_GIT_COMMIT") or "")[:7] or None,
                              "predictions": len(preds), "events": 0, "linked": len(_sb_links), "report": []}
    if preds:
        try:
            events, status["report"] = await sportybet.fetch_catalog()
        except Exception as e:
            events, status["report"] = [], [f"{type(e).__name__}: {e}"]
        status["events"] = len(events)
        status["catalog"] = _catalog_summary(events)
        if events:  # an empty catalog (SportyBet unreachable) keeps the last links
            unlinked: List[Dict] = []
            links = await asyncio.to_thread(_match_predictions_to_events, preds, events, unlinked)
            # Worth checking first: closest candidates, highest score first
            status["unlinked"] = sorted(unlinked, key=lambda u: -u["score"])[:40]
            status["market_map"], status["market_labels"] = await _market_map(events, links, status["report"])
            status["market_coverage"] = _market_coverage(links)
            _sb_links.clear()
            _sb_links.update(links)
            status["linked"] = len(links)
            r = _get_redis()
            if r:
                try:
                    r.set(SB_LINKS_KEY, json.dumps(links), ex=6 * 3600)
                except Exception as e:
                    print(f"[SportyBet] Could not save links: {e}")
        else:
            status["error"] = "Couldn't load SportyBet's match list — kept the previous links."
            status["market_labels"] = _sb_link_status.get("market_labels") or {}
            status["market_map"] = _sb_link_status.get("market_map") or {}
    else:
        status["error"] = "No upcoming predictions to link yet."
    status["seconds"] = round(time.monotonic() - started, 1)
    for p in _predictions_cache:
        p["sportybet"] = _sb_key(p.get("home", ""), p.get("away", ""), p.get("date", "")) in _sb_links
    _sb_link_status.clear()
    _sb_link_status.update(status)
    _save_link_status()
    print(f"[SportyBet] Linked {status['linked']}/{status['predictions']} predictions "
          f"({status['events']} SportyBet events, {trigger}) — {' · '.join(status['report']) or 'no fetch'}")
    return _sb_link_status


async def _link_on_startup() -> None:
    """After a deploy or restart: show the last result straight away, then
    link the cached predictions without waiting for the pipeline."""
    _restore_link_status()
    if _predictions_cache:
        await _link_sportybet_events("startup")


@app.get("/api/sportybet/status")
async def sportybet_status():
    """Public: how many upcoming matches can be booked on SportyBet, and when that was checked."""
    import booking_slip
    _restore_link_status()
    s = _sb_link_status
    confirmed = s.get("market_map") or {}
    # How many linked matches SportyBet prices each market on (shots, corners
    # and cards appear about a day before kick-off, on some matches only)
    coverage = s.get("market_coverage") or {}
    ids = {**booking_slip._LINE_MARKETS, **{k: (v or {}).get("id") for k, v in confirmed.items() if (v or {}).get("id")}}
    return {"at": s.get("at"), "trigger": s.get("trigger"), "linked": s.get("linked", 0),
            "predictions": s.get("predictions", 0), "every_minutes": SB_LINK_MINUTES,
            "markets": {kind: bool((confirmed.get(kind) or {}).get("ok")) for kind in booking_slip.VERIFIED},
            "coverage": {kind: coverage.get(str(mid), 0) for kind, mid in ids.items() if mid}}


def _linked_event(selection: Dict[str, Any]) -> Optional[Dict]:
    if not _sb_links:
        r = _get_redis()
        if r:
            try:
                _sb_links.update(json.loads(r.get(SB_LINKS_KEY) or "{}"))
            except Exception:
                pass
    return _sb_links.get(_sb_key(selection.get("home", ""), selection.get("away", ""), selection.get("date", "")))


def _with_priced_set_pieces(pred: Dict, event: Optional[Dict]) -> Dict:
    """A prediction with corners/bookings lines implied by SportyBet's own
    prices for whichever of the two our models don't cover for it
    (internationals, clubs outside the league data) — set_pieces.from_prices."""
    have = pred.get("set_pieces") or {}
    if not event or ("corners" in have and "bookings" in have):
        return pred
    priced = set_pieces.from_prices(event, getattr(_set_pieces, "size", None)) or {}
    extra = {k: v for k, v in priced.items() if k not in have}
    return {**pred, "set_pieces": {**have, **extra}} if extra else pred


# ── Referees appointed to upcoming matches (referees.py) ──
REFEREE_HOURS = 3
_referees: Dict[str, Any] = {"at": None, "appointments": {}, "report": None, "trigger": None}
_referee_lock: Optional[Tuple[Any, asyncio.Lock]] = None


def _appointments() -> Dict[str, Dict]:
    """{home|away|date: {"name", "career"}}, from Redis after a restart."""
    if _referees["at"] is None:
        import referees
        saved = referees.load(_get_redis())
        if saved:
            _referees.update(saved)
        else:
            _referees["at"] = ""  # looked: nothing saved
    return _referees.get("appointments") or {}


def _set_piece_extras(fx: Dict, predictor) -> Tuple[Optional[Dict], Optional[Dict]]:
    """(corners/bookings/shots markets, referee shown on the match) for a fixture.
    Clubs from the league match stats; internationals from the international
    model where it earned it. An appointed referee scales the bookings."""
    ref = _appointments().get(_sb_key(fx["home"], fx["away"], fx.get("date", ""))) or {}
    name, career = ref.get("name"), ref.get("career")
    extras, model = None, None
    if not fx.get("model_league"):
        if _set_pieces is None and _shots is None:
            return None, ({"name": name, **({"games": career.get("games")} if career else {})} if name else None)
        home, away = predictor.canon(fx["home"]), predictor.canon(fx["away"])
        if _set_pieces is not None:
            model = _set_pieces
            extras = _set_pieces.markets(home, away, fx.get("league"), name, career)
        shot = _shot_markets(home, away, fx.get("league"))
        if shot:
            extras = {**(extras or {}), **shot}
    else:
        model = _intl_set_pieces
        extras = _international_set_pieces(fx, name, career)
    if not name:
        return extras, None
    referee: Dict[str, Any] = {"name": name, **({"games": career.get("games")} if career else {})}
    if model is not None and extras and "bookings" in extras:
        # >1: more cards than these teams usually get (the model's view of them)
        referee["cards_factor"] = round(model.referee_factor(name, career), 2)
    return extras, referee


def _apply_referees() -> int:
    """Re-price the cached predictions' corners/bookings with the current
    appointments. Returns how many predictions have a referee."""
    global _predictions_cache
    if _predictor is None or not _predictions_cache:
        return 0
    out, n = [], 0
    for p in _predictions_cache:
        if p.get("sport") not in (None, "football"):
            out.append(p)
            continue
        try:
            extras, referee = _set_piece_extras(p, _predictor)
        except Exception:
            out.append(p)
            continue
        q = {k: v for k, v in p.items() if k not in ("set_pieces", "referee")}
        if extras:
            q["set_pieces"] = extras
        if referee:
            q["referee"] = referee
            n += 1
        out.append(q)
    _predictions_cache = out
    return n


def _af_referee_calls(add: int = 0) -> int:
    """API-Football requests the referee lookup made today (Redis-backed, so
    restarts count too). `add` records more."""
    day = datetime.now(timezone.utc).date().isoformat()
    r = _get_redis()
    if r:
        try:
            key = f"betiq:referees:af_calls:{day}"
            if add:
                n = int(r.incrby(key, add))
                r.expire(key, 2 * 86400)
                return n
            return int(r.get(key) or 0)
        except Exception:
            pass
    calls = _referees.setdefault("_af_calls", {})
    calls[day] = calls.get(day, 0) + add
    return calls[day]


async def _refresh_referees(trigger: str = "schedule") -> Dict[str, Any]:
    """Look up the referees of the coming days' matches and re-price the
    cached predictions' bookings. SofaScore first; when it blocks this
    server, API-Football (APIFOOTBALL_KEY). The GitHub job
    (collect_referees.py) fills the same Redis key from GitHub's machines,
    so what it found is picked up here too."""
    global _referee_lock
    import httpx
    import referees
    loop = asyncio.get_running_loop()
    if _referee_lock is None or _referee_lock[0] is not loop:
        _referee_lock = (loop, asyncio.Lock())
    async with _referee_lock[1]:
        preds = [p for p in _predictions_cache if p.get("sport") in (None, "football")]
        if not preds:
            return {"skipped": "no predictions yet"}
        saved = referees.load(_get_redis())
        if saved.get("at") and saved["at"] > (_referees.get("at") or ""):
            _referees.update(saved)  # newer, from the GitHub job
        known = dict(_appointments())
        today = datetime.now(timezone.utc).date()
        from curl_cffi.requests import AsyncSession
        try:
            async with AsyncSession(impersonate=intl.IMPERSONATE, timeout=20) as client:
                found, report = await referees.fetch(client, preds, known, today)
        except Exception as e:
            found = {k: v for k, v in known.items() if k.rsplit("|", 1)[-1] >= today.isoformat()}
            report = {"errors": [f"{type(e).__name__}: {e}"]}
        report["source"] = "sofascore"

        # football-data.org: every competition it covers, in one request
        # (a SofaScore find, with the referee's career record, still wins)
        report["football_data"] = await _football_data_referees(preds, today)
        fd = report["football_data"].pop("found_map", {})
        found = {**fd, **{k: v for k, v in found.items() if v.get("source") not in ("football-data", "api-football")}}

        # API-Football for what's still missing, when SofaScore is blocked
        still = [p for p in preds if referees.key(p["home"], p["away"], p["date"]) not in found]
        api_key = os.getenv("APIFOOTBALL_KEY", "").strip()
        if still and (referees.blocked(report) or not report.get("days")):
            if not api_key:
                report["api_football"] = {"skipped": "set APIFOOTBALL_KEY on Render to use API-Football too"}
            elif _af_referee_calls() + referees.AF_DAYS > referees.AF_DAILY_CAP:
                report["api_football"] = {"skipped": f"used today's {referees.AF_DAILY_CAP} requests"}
            else:
                async with httpx.AsyncClient(timeout=30) as client:
                    af, af_report = await referees.fetch_api_football(client, still, today, api_key)
                _af_referee_calls(af_report["requests"])
                report["api_football"] = af_report
                # A SofaScore find (with the referee's career record) wins
                found = {**af, **{k: v for k, v in found.items() if v.get("source") != "api-football"}}
        report["found"] = len(found)

        _referees.update({"at": datetime.now(timezone.utc).isoformat(), "checked": None,
                          "appointments": found, "report": report, "trigger": trigger})
        try:
            referees.save(_get_redis(), {k: _referees[k] for k in ("at", "appointments", "report", "trigger")})
        except Exception as e:
            print(f"[Referees] Could not save: {e}")
        report["on_predictions"] = _apply_referees()
        _save_predictions_cache()
        print(f"[Referees] {trigger}: {len(found)} referees; errors {report.get('errors') or 'none'}")
        return report


async def _football_data_referees(preds: List[Dict], today: date) -> Dict[str, Any]:
    """Appointed referees from football-data.org for our predictions in the
    next few days: {"listed", "with_referee", "found", "found_map"} or why not."""
    import referee_sources
    import referees
    if not API_KEY:
        return {"skipped": "FOOTBALL_DATA_API_KEY not set"}
    try:
        fd = FootballDataClient(API_KEY)
        async with httpx.AsyncClient() as hc:
            listed = await referee_sources.upcoming(lambda url: fd._get(hc, url), today)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    events = [{"homeTeamName": m["home"], "awayTeamName": m["away"], "day": m["date"], "referee": m["referee"]}
              for m in listed if m["referee"]]
    last = (today + timedelta(days=4)).isoformat()
    soon = [p for p in preds if today.isoformat() <= (p.get("date") or "") <= last]
    found = {k: {"name": ev["referee"], "career": None, "source": "football-data"}
             for k, ev in referees.match(soon, events).items()}
    return {"listed": len(listed), "with_referee": len(events), "found": len(found), "found_map": found}


# ── Past referees from football-data.org, for the cards model ──
_fd_refs_status: Dict[str, Any] = {"at": None, "report": None, "refs": 0, "seasons_done": 0}


def _fd_refs_load(r) -> Dict[str, Any]:
    import gzip
    import referee_sources
    raw = r.get(referee_sources.REFS_KEY) if r else None
    return json.loads(gzip.decompress(raw)) if raw else {}


async def _collect_fd_referees(trigger: str = "schedule") -> Dict[str, Any]:
    """Referees of past matches in every football-data.org competition (the
    current season and four before), kept in Redis for _with_club_referees.
    A first run reads ~55 seasons at the free plan's pace (~7 minutes)."""
    import gzip
    import model_store
    import referee_sources
    if not API_KEY:
        return {"skipped": "FOOTBALL_DATA_API_KEY not set"}
    r = model_store._client()  # binary: the blob is gzip'd
    if r is None:
        return {"skipped": "no Redis"}
    state = _fd_refs_load(r)
    fd = FootballDataClient(API_KEY)
    async with httpx.AsyncClient() as hc:
        report = await referee_sources.collect_past(lambda url: fd._get(hc, url), state, date.today(), asyncio.sleep)
    r.set(referee_sources.REFS_KEY, gzip.compress(json.dumps(state, separators=(",", ":")).encode()))
    _fd_refs_status.update(at=state.get("at"), report=report, refs=len(state.get("refs") or {}),
                           seasons_done=len(state.get("done") or []), trigger=trigger)
    print(f"[Referees] football-data.org: {report['found']} new past referees, {report['requests']} requests")
    return report


def _referee_status() -> Dict[str, Any]:
    import referees
    appointed = _appointments()
    shown = []
    for k, v in appointed.items():
        home, away, day = (k.split("|") + ["", "", ""])[:3]
        shown.append({"match": f"{home} vs {away}", "date": day, "referee": v.get("name"),
                      "games": (v.get("career") or {}).get("games"), "source": v.get("source") or "sofascore"})
    shown.sort(key=lambda x: x["date"])
    return {"at": _referees.get("at") or None, "checked": _referees.get("checked"),
            "trigger": _referees.get("trigger"), "report": _referees.get("report"),
            "appointments": shown[:60], "count": len(appointed),
            "api_football_calls_today": _af_referee_calls(), "api_football_cap": referees.AF_DAILY_CAP,
            "past": _fd_refs_status,
            "on_predictions": sum(1 for p in _predictions_cache if p.get("referee"))}


def _with_club_referees(history: pd.DataFrame) -> pd.DataFrame:
    """The club history with referees from football-data.org and the
    nightly collector (the league CSVs name them only for England).
    Unchanged on any error."""
    import international_stats
    import model_store
    refs: Dict[str, str] = {}
    try:
        refs.update(international_stats.load(model_store._client()).get("club_refs") or {})
    except Exception as e:
        print(f"[Pipeline] SofaScore club referees not loaded: {e}")
    try:
        state = _fd_refs_load(model_store._client())
        refs.update(state.get("refs") or {})
        _fd_refs_status.update(at=state.get("at"), report=state.get("report"), refs=len(state.get("refs") or {}),
                               seasons_done=len(state.get("done") or []))
    except Exception as e:
        print(f"[Pipeline] football-data.org referees not loaded: {e}")
    try:
        return international_stats.add_club_referees(history, refs)
    except Exception as e:
        print(f"[Pipeline] Club referees not added: {e}")
        return history


def _fit_shots(history: pd.DataFrame) -> None:
    """Fit the club shots model, and check each stat on the last 12 months'
    matches (predicted from the ones before them) against the league average."""
    global _shots, _shots_info
    model = shots.ShotModel.fit(history)
    if model is None:
        _shots, _shots_info = None, {"reason": "no shots data in the league CSVs"}
        return
    last = history["Date"].max()
    verdict = shots.check(history, str((last - pd.Timedelta(days=365)).date()))
    _shots = model
    _shots_info = {"check": verdict, "use": {k: v["use"] for k, v in verdict.items()},
                   "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    print(f"[Pipeline] Shots model: {', '.join(k for k, v in verdict.items() if v['use']) or 'no stat beat the average'}")


def _shot_markets(home: str, away: str, league: Optional[str]) -> Optional[Dict]:
    """Shots / shots-on-target lines for a club fixture: the stats the check approved."""
    if _shots is None:
        return None
    got = _shots.markets(home, away, league)
    use = _shots_info.get("use") or {}
    keep = {k: v for k, v in (got or {}).items() if use.get(k)}
    return keep or None


def _load_international_set_pieces() -> None:
    """Fit the international corners/bookings model from the collected data,
    with the settings the nightly check chose — if it beat the competition
    average there. CPU-light (well under a second); run in a thread."""
    global _intl_set_pieces, _intl_sp_info, _intl_shots
    import international_stats
    import model_store
    try:
        data = international_stats.load(model_store._client())
    except Exception as e:
        _intl_sp_info = {"error": str(e)}
        return
    verdict = data.get("model") or {}
    shot_verdict = data.get("shots_model") or {}
    _intl_sp_info = {"dataset": international_stats.summary(data), "check": verdict, "shots_check": shot_verdict}
    frame = international_stats.rows_frame(data)
    _intl_shots = None
    if any((shot_verdict.get("use") or {}).values()) and shot_verdict.get("params"):
        _intl_shots = shots.ShotModel.fit(frame.dropna(subset=list(shots.ShotModel.REQUIRED)), shot_verdict["params"])
    use = verdict.get("use") or {}
    if not any(use.values()) or not verdict.get("params"):
        _intl_set_pieces = None
        return
    _intl_set_pieces = set_pieces.SetPieceModel.fit(frame, verdict["params"])


def _international_set_pieces(fx: Dict, referee: Optional[str] = None,
                              career: Optional[Dict] = None) -> Optional[Dict]:
    """Corners/bookings/shots for an international fixture, only the stats
    the nightly checks approved."""
    home, away = intl.team_key(fx["home"]), intl.team_key(fx["away"])
    keep: Dict[str, Any] = {}
    if _intl_set_pieces is not None:
        use = (_intl_sp_info.get("check") or {}).get("use") or {}
        got = _intl_set_pieces.markets(home, away, fx.get("league"), referee, career) or {}
        keep = {k: v for k, v in got.items() if use.get(k)}
        if got and use.get("corners_home") and use.get("corners_away"):
            keep["corners_1x2"] = got["corners_1x2"]
        else:
            keep.pop("corners_1x2", None)
    if _intl_shots is not None:
        shot_use = (_intl_sp_info.get("shots_check") or {}).get("use") or {}
        got = _intl_shots.markets(home, away, fx.get("league")) or {}
        keep.update({k: v for k, v in got.items() if shot_use.get(k)})
    return keep or None


def _bookable_markets():
    """(market, code) → whether a SportyBet code can take the pick now:
    trusted markets always, VERIFIED ones once SportyBet's own labels
    confirmed them. Reads the confirmed markets once."""
    import booking_slip
    confirmed = {k for k, v in _sb_market_map().items() if (v or {}).get("ok")}

    def ok(market: str, code: str) -> bool:
        if not booking_slip.sportybet_ids(market, code):
            return False
        kind = booking_slip.verified_kind(market, code)
        return not kind or kind in confirmed
    return ok


def _explain_empty(reasons: List[Dict], matches: int, days: int, min_prob: float) -> str:
    """One sentence per market saying why it gave no picks."""
    span = f"the {matches} match{'es' if matches != 1 else ''} in the next {days} day{'s' if days != 1 else ''}"
    parts = []
    for r in reasons:
        if r["reason"] == "no_data":
            extra = (" Each team's corners come from club-league match stats, so internationals have none."
                     if r["market"] in ("home_corners_ou", "away_corners_ou", "corners_1x2") else
                     " Shots come from club-league match stats (the nine leagues with shot counts), so "
                     "internationals and other clubs have none."
                     if r["market"].endswith(("shots_ou", "sot_ou")) else
                     " These come from club-league stats, or for other matches from SportyBet's own line once "
                     "SportyBet lists the match with that market (tick \"Only matches SportyBet lists\")."
                     if r["market"] in ("corners_ou", "cards_ou") else "")
            parts.append(f"{r['name']}: no predictions for {span}.{extra}")
        elif r["reason"] == "below_minimum":
            parts.append(f"{r['name']}: the likeliest pick is {round(r['best'] * 100)}%, under your "
                         f"{round(min_prob * 100)}% minimum.")
        else:
            parts.append(f"{r['name']}: SportyBet hasn't confirmed this market yet, so codes can't include it. "
                         "Untick \"Only matches SportyBet lists\" to build the slip anyway.")
    return " ".join(parts) or f"None of {span} has a pick at {round(min_prob * 100)}% or more."


@app.post("/api/optimizer")
async def optimize_slip(body: Dict[str, Any]):
    """
    Build the slip with the best win chance whose total odds land in a target
    range (optimizer.py). Body: {min_odds, max_odds, max_games?, min_prob?,
    days?, leagues?: [codes], markets?: [ids], codes?: {market: [option
    codes]} (e.g. only some goal lines), bookable_only?}.
    """
    import booking_slip
    import optimizer
    try:
        lo, hi = float(body.get("min_odds", 2)), float(body.get("max_odds", 5))
        target = float(body.get("target_odds") or (lo * hi) ** 0.5)
        max_games = int(body.get("max_games", optimizer.MAX_GAMES))
        min_prob = min(0.95, max(0.5, float(body.get("min_prob", 0.6))))
        days = min(PREDICTION_DAYS, max(1, int(body.get("days", 3))))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid optimizer settings")
    if not (1.01 <= lo <= hi <= 1_000_000):
        raise HTTPException(status_code=400, detail="Target odds need 1.01 ≤ min ≤ max ≤ 1,000,000")
    leagues = set(body.get("leagues") or [])
    markets = set(body.get("markets") or []) or None
    bookable_only = bool(body.get("bookable_only"))
    codes = body.get("codes") or {}
    if not isinstance(codes, dict):
        raise HTTPException(status_code=400, detail="codes must be {market: [codes]}")
    only = {str(m): {str(c) for c in (cs or [])} for m, cs in codes.items() if isinstance(cs, list) and cs}

    now = datetime.now(timezone.utc)
    today, last = now.date().isoformat(), (now.date() + timedelta(days=days - 1)).isoformat()
    kicked_off = now.strftime("%H:%M")
    upcoming = [p for p in _predictions_cache
                if p.get("sport") in (None, "football")
                and today <= p.get("date", "") <= last
                and not (p.get("date") == today and (p.get("time") or "99:99") <= kicked_off)
                and (not leagues or p.get("league") in leagues)]
    # Bookable = linked to a SportyBet event. Asked of the stored links, not
    # the prediction's "sportybet" flag: a predictions rebuild replaces the
    # predictions (flags and all) minutes before the next linking run.
    linked = {id(p): _linked_event(p) for p in upcoming}
    preds = [p for p in upcoming if not bookable_only or linked[id(p)]]
    if bookable_only and upcoming and not preds:
        return {"error": (f"SportyBet hasn't listed any of the {len(upcoming)} matches in the next {days} "
                          f"day{'s' if days != 1 else ''} yet (or we haven't linked them since the last restart). "
                          "Untick \"Only matches SportyBet lists\", or pick more days."),
                "matches_considered": 0, "target": [lo, hi], "target_odds": target}
    # Bookable-only slips skip markets SportyBet hasn't confirmed yet (a code
    # couldn't take those picks)
    bookable = _bookable_markets() if bookable_only else None

    def allowed(market: str, code: str) -> bool:
        if market in only and code not in only[market]:
            return False  # a line the user left out
        return bookable is None or bookable(market, code)
    # Matches without corner/card stats (internationals) get SportyBet-implied lines
    pairs = [(_with_priced_set_pieces(p, linked[id(p)]), linked[id(p)]) for p in preds]
    preds = [p for p, _ in pairs]
    groups = [optimizer.candidates(p, ev, min_prob, markets, allowed) for p, ev in pairs]
    if bookable_only:
        # Shots are on some matches and lines only: bookable when SportyBet priced that very line
        groups = [[o for o in g if o.market not in booking_slip.LISTED_ONLY or o.odds_source == "sportybet"]
                  for g in groups]
    considered = sum(1 for g in groups if g)
    if considered == 0:
        reasons = optimizer.why_empty(preds, markets, min_prob, bookable, only)
        return {"error": _explain_empty(reasons, len(preds), days, min_prob), "reasons": reasons,
                "matches_considered": 0, "target": [lo, hi], "target_odds": target}
    result = await asyncio.to_thread(optimizer.optimize, groups, lo, hi, max_games)
    if result is None and lo <= target <= hi:
        # Nothing inside the tolerance: the nearest slip within ±25% of the
        # target, flagged as off target (within_target is False)
        for spread in (0.1, 0.25):
            near = await asyncio.to_thread(optimizer.optimize, groups, max(1.01, target * (1 - spread)),
                                           target * (1 + spread), max_games)
            if near is not None:
                result = {**near, "within_target": False}
                break
    if result is None:
        return {"error": (f"No slip from {considered} matches gets near {target:,.2f}x. "
                          "Allow more games or days, add markets, or lower the minimum confidence."),
                "matches_considered": considered, "target": [lo, hi], "target_odds": target}
    # Which picks a SportyBet code can take: the match is linked to a SportyBet
    # event and SportyBet confirmed the market (shots, for one, it doesn't offer)
    ok = bookable or _bookable_markets()
    events = {(p["home"], p["away"], p.get("date")): ev for p, ev in pairs}
    for pick in result.get("picks") or []:
        pick["bookable"] = (bool(events.get((pick["home"], pick["away"], pick["date"]))) and ok(pick["market"], pick["code"])
                            and (pick["market"] not in booking_slip.LISTED_ONLY or pick["odds_source"] == "sportybet"))
    result["bookable_picks"] = sum(1 for pick in result.get("picks") or [] if pick["bookable"])
    return {**result, "target": [lo, hi], "target_odds": round(target, 2), "matches_considered": considered}


def _prediction_for_leg(sel: Dict[str, Any], by_event: Dict[str, Dict]) -> Optional[Dict]:
    """Our prediction for a booking-code leg: by the SportyBet event we
    linked it to, else by team names within a day of kick-off; tennis and
    table tennis from their cached lists."""
    import sportybet
    if sel["eventId"] in by_event:
        return by_event[sel["eventId"]]
    for sport in ("tennis", "table-tennis"):
        for p in _cached_sport_predictions(sport):
            if str(p.get("sportybet_event_id")) == sel["eventId"]:
                return p
    try:
        day = datetime.fromtimestamp(int(sel["start"]) / 1000, timezone.utc).date()
    except (TypeError, ValueError, KeyError):
        return None
    near = {(day + timedelta(days=o)).isoformat() for o in (-1, 0, 1)}
    best, score = None, 0.0
    for p in _predictions_cache:
        if p.get("date") in near and p.get("sport") in (None, "football"):
            s = min(sportybet.team_similarity(p["home"], sel["home"]), sportybet.team_similarity(p["away"], sel["away"]))
            if s >= 0.8 and s > score:
                best, score = p, s
    return best


def _cached_sport_predictions(sport: str) -> List[Dict]:
    r = _get_redis()
    if r:
        try:
            return json.loads(r.get(f"betiq:sports:{sport}") or "[]")
        except Exception:
            return []
    return (_sports_memory_cache.get(sport) or ([],))[0]


@app.post("/api/optimizer/code")
async def optimize_code(body: Dict[str, Any]):
    """
    Check a SportyBet booking code with the model: each leg's chance, a
    better pick where there is one, and two improved slips (code_check.py).
    Body: {"code": "ABC123"}.
    """
    import code_check
    import sportybet
    code = str(body.get("code") or "").strip()
    try:
        selections = await sportybet.load_share_code(code)
    except sportybet.SportyBetError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        print(f"[CodeCheck] {code}: {e}")
        raise HTTPException(status_code=502, detail="Couldn't reach SportyBet to load that code. Try again in a minute.")
    if not _sb_links:
        _linked_event({})
    by_key = {_sb_key(p.get("home", ""), p.get("away", ""), p.get("date", "")): p for p in _predictions_cache}
    by_event = {str(ev.get("eventId")): by_key[k] for k, ev in _sb_links.items() if k in by_key}
    confirmed = {k: str(v["id"]) for k, v in _sb_market_map().items() if (v or {}).get("ok")}
    def find(sel: Dict) -> Optional[Dict]:
        p = _prediction_for_leg(sel, by_event)
        return _with_priced_set_pieces(p, _linked_event(p)) if p else None

    report = await asyncio.to_thread(
        code_check.analyse, selections, find,
        _linked_event, confirmed, _bookable_markets())
    return {"code": code.upper(), **report}


@app.post("/api/booking/convert")
async def convert_slip(request: Request, body: Dict[str, Any]):
    """
    Turn the bet slip into a booking code on the chosen platform.
    Body: {"platform": "sportybet", "selections": [{home, away, date, market, code, label?}]}
    """
    import booking_slip
    import sportybet

    platform = body.get("platform", "sportybet")
    if platform != "sportybet":
        raise HTTPException(status_code=400, detail="Booking codes are only supported for SportyBet")
    try:
        selections = booking_slip.validate(body.get("selections"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    result = await booking_slip.to_sportybet(
        selections, sportybet.fetch_events_for_date, sportybet.find_event, sportybet.share_selections,
        linked=_linked_event, market_map=_sb_market_map())
    # Every code a signed-in account makes becomes a ticket, settled leg by
    # leg as the results come in (Dashboard → Tickets)
    if result.get("code"):
        import auth
        import tickets
        uid = await auth.optional_user(request)
        if not uid and not auth.auth_enforced():
            uid = str(body.get("uid") or "")[:64] or None
        if uid:
            source = body.get("source") if body.get("source") in TICKET_SOURCES else "other"
            try:
                _record_ticket(uid, tickets.new_ticket(
                    result["code"], selections, result.get("picks") or [], source, result.get("share_url"),
                    result.get("total_odds"), datetime.now(timezone.utc).isoformat(timespec="seconds")))
                result["tracked"] = True
            except Exception as e:
                print(f"[Tickets] Couldn't record {result['code']}: {e}")
    return result


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
async def set_paywall_state(body: Dict[str, Any], _admin: str = Depends(require_admin)):
    """Admin-only endpoint — toggle paywall on or off."""

    enabled = bool(body.get("enabled", True))
    r = _get_redis()
    if r:
        try:
            # No TTL — persists forever in Redis
            r.set(PAYWALL_KEY, "true" if enabled else "false")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Redis error: {e}")

    print(f"[Admin] Paywall {'enabled' if enabled else 'DISABLED'}")
    _audit(_admin, "paywall", enabled=enabled)
    return {"enabled": enabled}


REFRESH_COOLDOWN = 30 * 60
_last_public_refresh = 0.0


@app.post("/api/refresh")
async def refresh_predictions(request: Request, background_tasks: BackgroundTasks):
    """Rebuild predictions. Admins any time; the public button (the home
    page's retry when nothing is cached) at most once per REFRESH_COOLDOWN —
    a run takes minutes of the server's CPU."""
    global _last_public_refresh
    via, uid = await _admin_identity(request)
    if _is_training:
        return {"message": "Already refreshing", "started": False}
    if via:
        _audit(f"clerk:{uid}" if via == "clerk" else via, "refresh")
    if not via:
        if time.time() - _last_public_refresh < REFRESH_COOLDOWN:
            return {"message": "Refreshed recently", "started": False}
        _last_public_refresh = time.time()
    background_tasks.add_task(_run_pipeline)
    return {"message": "Refresh started", "started": True}


# ------------------------------------------------------------------ #
# Startup
# ------------------------------------------------------------------ #

scheduler = AsyncIOScheduler()


async def _load_fbref_data():
    """Load cards + corners CSVs, rebuilding from EPL CSV if missing or >7 days old."""
    global _cards_df, _corners_df
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
    _corners_df = load_corners()
    print(f"[fbref] Loaded cards data ({len(_cards_df)} teams), corners data ({len(_corners_df)} teams)")


@app.on_event("startup")
async def startup():
    _load_h2h_cache()
    _load_predictions_cache()   # serve cached predictions instantly while pipeline rebuilds
    asyncio.create_task(_link_on_startup())  # every deploy re-links to SportyBet straight away
    asyncio.create_task(_run_pipeline())
    asyncio.create_task(_load_fbref_data())
    # results fetcher runs via scheduler only — not on boot to avoid API contention with pipeline
    scheduler.add_job(_run_pipeline, "interval", hours=12, id="refresh")
    scheduler.add_job(_load_fbref_data, "interval", days=7, id="fbref_refresh")
    scheduler.add_job(_fetch_and_save_results, "interval", hours=3, id="results_refresh")
    scheduler.add_job(_link_sportybet_events, "interval", minutes=SB_LINK_MINUTES, id="sportybet_links")
    scheduler.add_job(_flush_traffic, "interval", minutes=TRAFFIC_FLUSH_MINUTES, id="traffic_flush")
    scheduler.add_job(_refresh_referees, "interval", hours=REFEREE_HOURS, id="referees")
    # Past referees: daily, first 10 minutes after start (after the pipeline's own football-data requests)
    scheduler.add_job(_collect_fd_referees, "interval", hours=24, id="fd_referees",
                      next_run_time=datetime.now() + timedelta(minutes=10))
    scheduler.add_job(_matchday_live, "interval", minutes=MD_LIVE_MINUTES, id="matchday_live")
    scheduler.add_job(_matchday_sweep, "interval", hours=3, id="matchday_sweep")
    scheduler.start()


@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown()
    _flush_traffic()  # keep the last few minutes of page views
