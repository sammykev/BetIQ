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
import hashlib
import json
from datetime import datetime, date, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx
import numpy as np
import pandas as pd
from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from auth import auth_enforced, optional_user, require_user
from grading import grade_prediction, regrade, to_goals
from team_names import UCL_ALIASES, TeamResolver
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from predictor import LeaguePredictor
import data_fetcher
from data_fetcher import FootballDataClient, LEAGUES, API_BASE, not_in_plan
import international_fixtures as intl
import perf
import security
import traffic
import set_pieces
import shots
from scrapers.fbref import load_cards, load_corners, refresh as scrape_fbref, CORNERS_CSV, CARDS_CSV

load_dotenv()
import error_tracking
error_tracking.init()

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

# Settings the server needs by name; a name with stray characters around it
# in .env (e.g. "4145r1546UPSTASH_REDIS_URL") silently leaves it unset
KNOWN_SETTINGS = ("UPSTASH_REDIS_URL", "CLERK_ISSUER", "CLERK_SECRET_KEY", "CLERK_AUTHORIZED_PARTIES",
                  "ADMIN_SECRET", "ADMIN_USER_IDS", "TRAFFIC_KEY", "FOOTBALL_DATA_API_KEY", "FRONTEND_URL",
                  "APIFOOTBALL_KEY", "ODDS_API_KEY", "GROQ_API_KEY", "PAYSTACK_SECRET_KEY",
                  "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET",
                  "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SENTRY_DSN")
_STARTED_AT = time.time()


def _mangled_settings() -> List[Tuple[str, str]]:
    """[(name found in the environment, the setting it was meant to be)] for
    known settings that are unset while a name containing them is set."""
    out = []
    for name in sorted(os.environ):
        for known in KNOWN_SETTINGS:
            if name != known and known in name and not os.getenv(known) and len(name) - len(known) <= 24:
                out.append((name, known))
    return out


# Redis client — only active when UPSTASH_REDIS_URL is set
_redis = None
_redis_problem: Dict[str, Any] = {"error": None, "retry_at": 0.0, "warned": False}
REDIS_RETRY_SECONDS = 30


def _redis_missing_reason() -> str:
    """Why there's no database, in words the admin can act on."""
    if not REDIS_URL:
        bad = [f for f, known in _mangled_settings() if known == "UPSTASH_REDIS_URL"]
        if bad:
            return (f'UPSTASH_REDIS_URL isn\'t set: .env has "{bad[0]}" instead (stray characters before the '
                    "name). Fix that line, then run: sudo docker compose up -d")
        return "UPSTASH_REDIS_URL isn't set in the server's .env: nothing is saved or read (tickets, results, settings)."
    return f"Can't connect to the database: {_redis_problem['error'] or 'unknown error'}"


def _get_redis():
    global _redis
    if _redis is not None:
        return _redis
    if not REDIS_URL:
        if not _redis_problem["warned"]:
            print(f"[Redis] WARNING: {_redis_missing_reason()}")
            _redis_problem["warned"] = True
        return None
    # A failing database isn't asked again on every request: every 30s
    if time.time() < _redis_problem["retry_at"]:
        return None
    try:
        import redis as redis_lib
        # Timeouts and keep-alive: a connection the network silently dropped
        # must fail (and reconnect), not leave a call waiting forever (that
        # froze the live-score job: every later run was skipped behind it)
        _redis = redis_lib.from_url(REDIS_URL, decode_responses=True, socket_timeout=15,
                                    socket_connect_timeout=10, socket_keepalive=True,
                                    health_check_interval=30, retry_on_timeout=True)
        _redis.ping()
        print("[Redis] Connected to Upstash Redis.")
        _redis_problem.update(error=None, retry_at=0.0)
        return _redis
    except Exception as e:
        print(f"[Redis] WARNING: could not connect: {e}")
        _redis = None
        _redis_problem.update(error=f"{type(e).__name__}: {str(e)[:160]}", retry_at=time.time() + REDIS_RETRY_SECONDS)
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


PLANS_UNCHECKED = "Paid features are unavailable right now. Try again later."


def _plans_unchecked() -> HTTPException:
    """Refuse paid content: without CLERK_ISSUER and CLERK_SECRET_KEY the
    server can't tell who has paid, and letting everyone in would give paid
    features away (logged once)."""
    global _warned_premium
    if not _warned_premium:
        print("[Auth] ERROR: paid features are refusing everyone but admins — set "
              "CLERK_ISSUER and CLERK_SECRET_KEY on the backend.")
        _warned_premium = True
    return HTTPException(status_code=503, detail=PLANS_UNCHECKED)


async def require_premium(request: Request) -> Optional[str]:
    """Premium content (match analysis, AI explanation): a signed-in premium
    user, an admin, or anyone while the paywall is switched off. With the
    paywall on, refused (503) until CLERK_ISSUER and CLERK_SECRET_KEY are set."""
    import auth
    if not _paywall_enabled():
        return None
    via, uid = await _admin_identity(request)
    if via:
        return uid
    if not auth.premium_enforced():
        raise _plans_unchecked()
    if not uid:
        raise HTTPException(status_code=401, detail="Sign in to see this.")
    if not await auth.user_is_premium(uid):
        raise HTTPException(status_code=402, detail="premium_required")
    return uid


_warned_premium = False


# ── Feature switches (access.py) ─────────────────────────────────────────────
FEATURES_KEY = "betiq:config:features"
FEATURES_CACHE_SECONDS = 15
_features_cache: List[Any] = [0.0, None]


def _features() -> Dict[str, Dict[str, Any]]:
    """Every feature's state, tier and allowed accounts (cached briefly)."""
    import access
    if _features_cache[1] is not None and time.time() - _features_cache[0] < FEATURES_CACHE_SECONDS:
        return _features_cache[1]
    saved = None
    r = _get_redis()
    if r:
        try:
            raw = r.get(FEATURES_KEY)
            saved = json.loads(raw) if raw else None
        except Exception:
            saved = None
    feats = access.merged(saved)
    _features_cache[:] = [time.time(), feats]
    return feats


async def check_feature(request: Request, fid: str) -> Optional[str]:
    """Let this request use a feature, or refuse it: 404 feature_off when it's
    switched off (or for testers only), 401 signed out, 402 lite_required /
    premium_required without the tier, 503 while plans can't be checked (no
    CLERK_ISSUER / CLERK_SECRET_KEY). Returns the user id, if signed in."""
    import access
    import auth
    f = _features()[fid]
    via, uid = await _admin_identity(request)
    admin = bool(via)
    if not access.visible(f, uid, admin):
        raise HTTPException(status_code=404, detail="feature_off")
    if f["tier"] == "free" or access.granted(f, uid, admin) or not _paywall_enabled():
        return uid
    if not auth.premium_enforced():
        raise _plans_unchecked()
    if not uid:
        raise HTTPException(status_code=401, detail="Sign in to see this.")
    if not auth.tier_at_least(await auth.user_tier(uid), f["tier"]):
        raise HTTPException(status_code=402, detail=f"{f['tier']}_required")
    return uid


def require_feature(fid: str):
    """FastAPI dependency: check_feature for one feature."""
    async def dependency(request: Request) -> Optional[str]:
        return await check_feature(request, fid)
    return dependency


LIVE_STALE_MINUTES = 15


@app.get("/api/admin/alerts")
async def admin_alerts(_admin: str = Depends(require_admin)):
    """Problems the admin page shows as a red banner: the database missing or
    unreachable, settings whose names are broken in .env, live scores
    stalled, markets the weekly accuracy review just paused."""
    alerts: List[Dict[str, str]] = []
    if _get_redis() is None:
        alerts.append({"level": "danger", "title": "Database not connected",
                       "detail": _redis_missing_reason() + " The site shows no tickets, results or settings until it's fixed; "
                                 "the data itself is safe in Upstash."})
    for found, meant in _mangled_settings():
        if meant != "UPSTASH_REDIS_URL":
            alerts.append({"level": "warn", "title": f"{meant} isn't set",
                           "detail": f'.env has "{found}": stray characters around the name. Fix that line, '
                                     "then run: sudo docker compose up -d"})
    up_minutes = (time.time() - _STARTED_AT) / 60
    last = _md_status.get("at")
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 60 if last else None
    except ValueError:
        age = None
    if up_minutes > 10 and (age is None or age > LIVE_STALE_MINUTES):
        why = _md_status.get("error")
        alerts.append({"level": "warn", "title": "Live scores aren't updating",
                       "detail": (f"Last score check: {last or 'none since the server started'}"
                                  + (f" ({why})" if why else "") + ". It should run every 3 minutes.")})
    import market_review
    _restore_review()
    try:
        at = (_review.get("latest") or {}).get("at")
        review_age = (datetime.now(timezone.utc) - datetime.fromisoformat(at)).total_seconds() / 86400 if at else None
    except ValueError:
        review_age = None
    review_alert = market_review.alert(_review, review_age)
    if review_alert:
        alerts.append(review_alert)
    import league_review
    _restore_league_review()
    try:
        at = (_league_review.get("latest") or {}).get("at")
        drift_age = (datetime.now(timezone.utc) - datetime.fromisoformat(at)).total_seconds() / 86400 if at else None
    except ValueError:
        drift_age = None
    drift_alert = league_review.alert(_league_review, drift_age)
    if drift_alert:
        alerts.append(drift_alert)
    alerts += await asyncio.to_thread(_post_alerts)
    return {"alerts": alerts}


_clerk_keys_cache: List[Any] = [0.0, None]


async def _clerk_keys() -> Dict[str, Any]:
    """auth.check_clerk_keys, at most every 10 minutes."""
    import auth
    if _clerk_keys_cache[1] is None or time.time() - _clerk_keys_cache[0] > 600:
        _clerk_keys_cache[:] = [time.time(), await auth.check_clerk_keys()]
    return _clerk_keys_cache[1]


@app.get("/api/admin/access-check")
async def admin_access_check(uid: str, _admin: str = Depends(require_admin)):
    """How the server sees one account: its plan as Clerk reports it (fresh,
    not cached) and, feature by feature, whether it gets in."""
    import access
    import auth
    uid = uid.strip()
    if not re.fullmatch(r"user_[A-Za-z0-9]{6,64}", uid):
        raise HTTPException(status_code=400, detail="Give a Clerk user id (user_…)")
    note, tier, status, meta = None, None, None, {}
    if not auth.premium_enforced():
        note = "The server doesn't check plans yet: CLERK_ISSUER and CLERK_SECRET_KEY are both needed."
    else:
        auth._tier_cache.pop(uid, None)
        status, meta = await auth.clerk_user(uid)
        if status == 404:
            note = ("Clerk says this account doesn't exist, so the server treats it as Free. "
                    "CLERK_SECRET_KEY is probably from a different Clerk application than the site's.")
        elif status != 200:
            note = f"Couldn't read the account from Clerk (HTTP {status})."
        tier = auth.tier_from_metadata(meta) if status == 200 else "free"
    admin = uid in ADMIN_USER_IDS
    paywall = _paywall_enabled()
    feats = _features()
    return {"uid": uid, "tier": tier, "admin": admin, "paywall": paywall, "note": note,
            "subscription": meta.get("subscription") if status == 200 else None,
            "expires": meta.get("subscription_expires") if status == 200 else None,
            "keys": await _clerk_keys() if auth.premium_enforced() else None,
            "features": {fid: {"allowed": access.allowed(f, uid, admin, tier or "premium", paywall),
                               "visible": access.visible(f, uid, admin), "state": f["state"], "tier": f["tier"]}
                         for fid, f in feats.items()}}


@app.get("/api/admin/features")
async def admin_get_features(_admin: str = Depends(require_admin)):
    """Every switchable feature, grouped, with its state, tier and accounts."""
    import access
    feats = _features()
    return {"paywall": _paywall_enabled(),
            "features": [{**{k: d[k] for k in ("id", "label", "group", "about")},
                          "default_state": d["state"], "default_tier": d["tier"], **feats[d["id"]]}
                         for d in access.REGISTRY]}


@app.put("/api/admin/features/{fid}")
async def admin_set_feature(fid: str, body: Dict[str, Any], _admin: str = Depends(require_admin)):
    """Change one feature: {state?, tier?, allow?: [{id, label}]}."""
    import access
    if fid not in access.DEFAULTS:
        raise HTTPException(status_code=404, detail="Unknown feature")
    try:
        change = access.validate(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    r = _get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Settings need Redis")
    raw = r.get(FEATURES_KEY)
    saved = json.loads(raw) if raw else {}
    feats = access.merged(saved)
    feats[fid] = {**feats[fid], **change}
    r.set(FEATURES_KEY, json.dumps(feats))
    _features_cache[:] = [0.0, None]
    _audit(_admin, "feature", feature=fid, **{k: (len(v) if k == "allow" else v) for k, v in change.items()})
    return {"id": fid, **feats[fid]}


@app.get("/api/features")
async def get_features(request: Request):
    """What this visitor may see: per feature whether it's shown, the tier it
    needs and whether this account was given it. The site works out the rest
    from the visitor's own tier."""
    import access
    via, uid = await _admin_identity(request)
    return {"paywall": _paywall_enabled(), "admin": bool(via), "signed_in": bool(uid),
            "features": access.for_visitor(_features(), uid, bool(via))}


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
    allow_methods=["GET", "POST", "PUT", "DELETE"],   # PUT: admin → Access saves
    allow_headers=["Authorization", "Content-Type", "X-Admin-Secret"],
    max_age=3600,
)
security.configure(lambda: _get_redis())
# Outermost: how long each API request takes, whole (perf.py)
app.add_middleware(perf.TimingMiddleware)

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


def _get_web_form_caches(teams: List[str]) -> Dict[str, Dict]:
    """The cached web forms of many teams in one Redis round trip (MGET)."""
    r = _get_redis()
    if not r or not teams:
        return {}
    try:
        raws = r.mget([_redis_team_key(t) for t in teams])
    except Exception:
        return {}
    out = {}
    for t, raw in zip(teams, raws):
        try:
            if raw:
                out[t] = json.loads(raw)
        except Exception:
            continue
    return out


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


# The Brasileirão and the European leagues we publish (league_fixtures.py):
# context rows — ratings and form, no training rows
MAIN_EXTRA_LEAGUES = {"BSA", "JPL", "SPL", "GSL", "TSL", "AUT", "SUI", "DEN", "NOR", "SWE", "POL", "ROU"}
# Leagues whose walk-forward check hasn't passed: published with a warning, kept out of slips
_unproven_leagues: set = set()
# Group B (sb_league_fixtures.py): fixtures from SportyBet's catalog, kept between linking runs
_sb_fixtures: List[Dict] = []
SB_FIXTURES_KEY = "betiq:sb_league_fixtures:v1"


def _sb_league_rows(known: set) -> pd.DataFrame:
    """SportyBet's top flights (collect_sb_leagues.py) as context rows."""
    import europe_model
    import model_store
    try:
        return europe_model.sb_leagues(known, model_store._client())
    except Exception as e:
        print(f"[Data] SportyBet leagues not read: {e}")
        return pd.DataFrame()


def _sb_model_name(predictor, name: str, league: str) -> str:
    """The name a group B club trains under: with its league added when it
    clashes with a club elsewhere (europe_model.sb_leagues)."""
    tagged = f"{name} ({league})"
    ratings = getattr(getattr(predictor, "elo", None), "ratings", None) or {}
    return tagged if tagged in ratings else name


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

    teams = list(dict.fromkeys(t for fx in fixtures for t in (fx["home"], fx["away"])))
    # One round trip, off the event loop (a read per team held it for ~50s)
    cached_forms = await asyncio.to_thread(_get_web_form_caches, teams)
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
            if local_pts >= 5 and team in cached_forms:
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
            if team in cached_forms:
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

# Fixtures an admin took off the site (a source listed a match that isn't
# happening): home|away|date → {home, away, date, at, by, source}
HIDDEN_KEY = "betiq:hidden:matches"
_hidden: Dict[str, Dict[str, Any]] = {}
_hidden_loaded = [False]


def _hidden_key(home: str, away: str, day: str) -> str:
    return f"{(home or '').strip().lower()}|{(away or '').strip().lower()}|{str(day or '')[:10]}"


def _load_hidden() -> None:
    r = _get_redis()
    if not r:
        return
    try:
        _hidden.clear()
        _hidden.update(json.loads(r.get(HIDDEN_KEY) or "{}"))
        _hidden_loaded[0] = True
    except Exception as e:
        print(f"[Hidden] couldn't load: {e}")


def _is_hidden(fx: Dict) -> bool:
    if not _hidden_loaded[0]:
        _load_hidden()
    return bool(_hidden) and _hidden_key(fx.get("home", ""), fx.get("away", ""), fx.get("date", "")) in _hidden


def _fixture_source(p: Dict) -> str:
    """Where a fixture came from, for the admin: the source and its competition."""
    mid = str(p.get("match_id") or "")
    src = {"espn": "ESPN", "sofa": "SofaScore", "odds": "The Odds API"}.get(mid.split(":", 1)[0]) if ":" in mid else None
    return " · ".join(x for x in (src or ("football-data.org" if mid.isdigit() else None),
                                  p.get("league_name") or p.get("league")) if x) or "unknown"


@app.get("/api/admin/matches/hidden")
async def admin_hidden_matches(_admin: str = Depends(require_admin)):
    _load_hidden()
    return {"hidden": sorted(_hidden.values(), key=lambda h: (h.get("date") or "", h.get("home") or ""))}


@app.post("/api/admin/matches/hide")
async def admin_hide_match(body: Dict[str, Any], _admin: str = Depends(require_admin)):
    """Take a fixture off the site: predictions, match-day list, optimizer
    and daily odds, now and in every rebuild. Body: {home, away, date}."""
    import matchday
    global _predictions_cache
    home, away, day = str(body.get("home") or ""), str(body.get("away") or ""), str(body.get("date") or "")[:10]
    if not home or not away or len(day) != 10:
        raise HTTPException(status_code=400, detail="Give home, away and date (YYYY-MM-DD)")
    r = _get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Couldn't save: the database isn't connected")
    _load_hidden()
    was = next((p for p in _predictions_cache if _hidden_key(p.get("home", ""), p.get("away", ""), p.get("date", ""))
                == _hidden_key(home, away, day)), {})
    _hidden[_hidden_key(home, away, day)] = {"home": home, "away": away, "date": day, "by": _admin,
                                             "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                             "source": _fixture_source(was) if was else None}
    r.set(HIDDEN_KEY, json.dumps(_hidden))
    _predictions_cache = [p for p in _predictions_cache if not _is_hidden(p)]
    _save_predictions_cache()
    # Off the day's list too, unless it has a result already
    removed = False
    try:
        md = _md_load(r, day)
        k = matchday.key(home, away)
        if k in md and not (md[k].get("result") or {}).get("status"):
            del md[k]
            _md_save(r, day, md)
            removed = True
    except Exception as e:
        print(f"[Hidden] match-day entry: {e}")
    _audit(_admin, "hide_match", match=f"{home} v {away}", date=day)
    return {"hidden": True, "removed_from_day": removed}


@app.post("/api/admin/matches/unhide")
async def admin_unhide_match(body: Dict[str, Any], _admin: str = Depends(require_admin)):
    """Put a hidden fixture back (it returns with the next predictions rebuild)."""
    r = _get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Couldn't save: the database isn't connected")
    _load_hidden()
    _hidden.pop(_hidden_key(str(body.get("home") or ""), str(body.get("away") or ""), str(body.get("date") or "")), None)
    r.set(HIDDEN_KEY, json.dumps(_hidden))
    _audit(_admin, "unhide_match", match=f"{body.get('home')} v {body.get('away')}", date=body.get("date"))
    return {"hidden": False}


# Fewer matches than this for either club and a fixture stays out of slips
THIN_HISTORY = 5


def _note_unknown_clubs(model, fx: Dict) -> bool:
    """Remember a fixture's clubs the model has no matches for; True if
    either club has fewer than THIN_HISTORY."""
    stats = getattr(model, "team_stats", None)
    if not isinstance(stats, dict) or fx.get("sport") not in (None, "football"):
        return False
    canon = getattr(model, "canon", lambda n: n)
    thin = False
    for side in ("home", "away"):
        try:
            n = len((stats.get(canon(fx[side])) or {}).get("pts") or [])
            if not n:
                _unknown_clubs[fx[side]] = fx.get("league", "")
            thin = thin or n < THIN_HISTORY
        except Exception:
            pass
    return thin


# Our share of the 1X2 chances against the bookmaker's de-vigged prices
# (fair_odds). On 93 settled matches (check_football_blend.py, 8 Oct 2026)
# blending did better: log loss 0.933 ours alone, 0.910 at 0.5, 0.908 at 0.3,
# and the weight chosen on the earlier days also won on the later ones; not
# yet conclusive (z -1.1), so a cautious 0.5. Over/under stays ours (the
# check found the market no help there). The check runs nightly.
FOOTBALL_1X2_OURS = 0.5


def _blend_1x2(tip: Dict, odds: Dict) -> Dict:
    """The model's 1X2 chances mixed with the bookmaker's (when it priced all
    three), and the tips that follow from them; the model's own chances kept
    as p_*_model (the blend check compares them, not the blend, to the market)."""
    import fair_odds
    import predictor as pr
    try:
        prices = [float(odds.get(k) or 0) for k in ("1", "X", "2")]
    except (TypeError, ValueError):
        return tip
    ours = [tip.get("p_home"), tip.get("p_draw"), tip.get("p_away")]
    if not all(o > 1.0 for o in prices) or not all(isinstance(x, (int, float)) for x in ours) or sum(ours) <= 0:
        return tip
    s = sum(ours)
    market = fair_odds.fair(prices)
    w = FOOTBALL_1X2_OURS
    p_h, p_d, p_a = (w * o / s + (1 - w) * m for o, m in zip(ours, market))
    p_o15, p_o25 = float(tip.get("p_over15") or 0), float(tip.get("p_over25") or 0)
    return {**tip, **pr._rounded_probs(p_h, p_d, p_a, p_o15, p_o25), **pr.pick_tips(p_h, p_d, p_a, p_o15, p_o25),
            "p_home_model": tip.get("p_home"), "p_draw_model": tip.get("p_draw"), "p_away_model": tip.get("p_away")}


def _build_predictions(predictor, fixtures: list, live_odds: dict) -> list:
    """Turn upcoming fixtures + live odds into prediction dicts (with value-bet flags)."""
    predictions = []
    _unknown_clubs.clear()
    for fx in fixtures:
        if not _within_window(fx.get("date", "")):
            continue
        if _is_hidden(fx):
            continue       # an admin took this fixture off the site (a wrong one from a source)
        try:
            key = f"{fx['home']}:{fx['away']}:{fx.get('date','')}"
            odds = live_odds.get(key, {})
            if fx.get("needs_odds") and not all(float(odds.get(k) or 0) > 1 for k in ("1", "X", "2")):
                odds = _sb_1x2(fx)
                if not odds:
                    continue      # not priced on SportyBet yet: it waits (_publish_waiting_europe)
            elif fx.get("sb_odds") and not all(float(odds.get(k) or 0) > 1 for k in ("1", "X", "2")):
                odds = _sb_1x2(fx) or fx["sb_odds"]       # group B: the catalog's prices, fresher once linked
            elif not all(float(odds.get(k) or 0) > 1 for k in ("1", "X", "2")):
                # No odds from the feed (the leagues it doesn't cover, cup ties
                # it skips): SportyBet's prices once linked (_price_unpriced),
                # the no-odds model till then
                odds = _sb_1x2(fx) or odds
            model = _europe_predictor if (predictor is _predictor and _europe_covers(fx.get("league", ""))) \
                else predictor
            goals_only = bool(fx.get("goals_only"))
            names = (_sb_model_name(model, fx["home"], fx["league"]), _sb_model_name(model, fx["away"], fx["league"])) \
                if goals_only else (fx["home"], fx["away"])
            tip = model.predict_match(
                *names,
                odds_home=float(odds.get("1") or 0),
                odds_draw=float(odds.get("X") or 0),
                odds_away=float(odds.get("2") or 0),
                match_date=fx.get("date"),
                # Internationals are listed per competition but modelled as "INT"
                league=fx.get("model_league") or fx.get("league", ""),
            )
            if not tip:
                continue
            tip = _blend_1x2(tip, odds)
            if _note_unknown_clubs(model, fx) or fx.get("needs_odds"):
                tip = {**tip, "thin_history": True}
            if fx.get("league") in _unproven_leagues:
                tip = {**tip, "league_unproven": True}
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

            # Group B: no corners, cards or shots data, so result and goals markets only
            extras, referee = _set_piece_extras(fx, predictor) if not goals_only else (None, None)

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
    import europe_fixtures
    code = {display: c for c, display, _, _ in intl.COMPETITIONS}.get(name)
    if code is None:
        code = {display: c for c, (_slug, display, *_r) in europe_fixtures.COMPETITIONS.items()}.get(name)
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


# Europa / Conference League matches with a club our data barely knows
# (europe_fixtures.known): held until SportyBet prices them, then predicted
# with its 1X2 odds as the model's input and half the published chance
# (_blend_1x2), and shown with the thin-history badge (kept out of slips)
_europe_waiting: List[Dict] = []


async def _fetch_more_leagues() -> list:
    """Upcoming fixtures of the European leagues in league_fixtures (ESPN);
    and which of them haven't passed their check yet."""
    global _unproven_leagues
    import league_fixtures
    try:
        report = await league_fixtures.fetch(PREDICTION_DAYS)
    except Exception as e:
        print(f"[Leagues] fetch failed: {e}")
        return []
    _unproven_leagues = league_fixtures.unproven(_get_redis(), set(league_fixtures.COMPETITIONS)
                                                 | {f["league"] for f in _sb_fixtures})
    print(f"[Leagues] {report['sources']}{'; errors ' + str(report['errors']) if report['errors'] else ''}")
    return report["fixtures"]


async def _fetch_europe_fixtures(predictor, training: pd.DataFrame) -> list:
    """Upcoming Europa and Conference League fixtures from ESPN: those whose
    clubs the model knows (europe_fixtures.known), and the others marked
    needs_odds — published once SportyBet prices them (_sb_1x2)."""
    global _europe_waiting
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
    _remember_competition_logos(report["fixtures"])
    kept = {(f["home"], f["away"], f.get("date")) for f in keep}
    _europe_waiting = [{**f, "needs_odds": True} for f in report["fixtures"]
                       if (f["home"], f["away"], f.get("date")) not in kept]
    print(f"[Europe] {len(report['fixtures'])} Europa/Conference League fixtures ({report['sources']}, "
          f"read by {report.get('how')}), "
          f"{len(keep)} from our data; {len(_europe_waiting)} with a club under "
          f"{europe_fixtures.MIN_MATCHES} recent matches in our data, priced from SportyBet's odds once it lists them"
          f"{'; errors ' + str(report['errors']) if report['errors'] else ''}")
    return keep + _europe_waiting


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
        meta_now = model_store.describe(MODEL_CACHE_VERSION, name=europe_model.MODEL_NAME) or {}
        if _europe_predictor is not None and meta_now.get("gen") == _europe_model_info.get("gen"):
            _europe_predictor.calibration = cals["europe"]   # same model: only the plan may have changed
            _europe_model_info.update(competitions=europe_model.competitions(verdict), calibrated=sorted(cals["europe"]))
            return
        _europe_predictor = None   # never two Europe models in memory at once
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
            print(f"[Europe] Europe model loaded ({meta.get('config')}) for {', '.join(comps) or 'nothing'}; "
                  f"{_rss_mb()} MB in use.")
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
    # The top flights only SportyBet covers (sb_league_fixtures.py), the same way
    sb_df = _sb_league_rows(club_names | nation_names | (
        set(extra_df["HomeTeam"]) | set(extra_df["AwayTeam"]) if not extra_df.empty else set()))
    if not ucl_df.empty and club_names:
        ucl_df = TeamResolver(club_names, aliases=UCL_ALIASES).resolve_frame(ucl_df)
    if not ucl_df.empty:
        ucl_df = ucl_df.assign(Source="ucl")   # for europe_model; the model ignores it

    # European competitions and domestic cups from ESPN (club_cups.py), the
    # sets the nightly check found to improve the league predictions
    cups_df = _club_cup_rows(club_names)
    parts = [df for df in [fd_df, epl_df, ucl_df, intl_df, cups_df, extra_df, sb_df] if not df.empty]
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


def _rss_mb() -> Optional[int]:
    """This process's memory in use (MB), where /proc has it (Linux: Render)."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def _load_or_train(combined: pd.DataFrame, data_mtime: float) -> LeaguePredictor:
    """
    The model, as cheaply as possible:
      1. this server's cached model, if newer than its data;
      2. the shared model (trained nightly on GitHub Actions, see
         train_model.py), if fresh — seconds instead of minutes of training;
      3. the shared model again, a few times (it may be being replaced), then
         the one this server already runs, then a shared model of any age;
      4. only with none of those, training here (it needs ~400 MB, most of
         a 512 MB instance), then sharing the result.
    """
    import model_store
    from predictor import MODEL_CACHE_VERSION

    predictor = LeaguePredictor.load_cache(data_mtime)
    if predictor is not None:
        return predictor
    try:
        shared_on = model_store._client() is not None
    except Exception as e:
        print(f"[ModelStore] Redis unavailable: {e}")
        shared_on = False
    for attempt in range(3 if shared_on else 1):
        if attempt:
            time.sleep(20)
        try:
            shared = model_store.fetch(MODEL_CACHE_VERSION)
        except Exception as e:
            print(f"[ModelStore] Could not load the shared model: {e}")
            shared = None
        if shared is not None:
            blob, meta = shared
            predictor = LeaguePredictor.from_bytes(blob)
            if predictor is not None:
                age_h = (time.time() - meta["trained_at"]) / 3600
                print(f"[ModelStore] Loaded shared model ({meta.get('source', '?')}, "
                      f"{age_h:.1f}h old, {meta.get('rows', '?')} matches) — skipping training.")
                predictor.save_cache(data_mtime)
                return predictor
    if _predictor is not None:
        print("[ModelStore] No fresh shared model — keeping the model already running (not training here).")
        return _predictor
    if shared_on:
        try:
            stale = model_store.fetch(MODEL_CACHE_VERSION, max_age_hours=24 * 14)
            if stale is not None and (p := LeaguePredictor.from_bytes(stale[0])) is not None:
                print("[ModelStore] Using an older shared model rather than training here.")
                return p
        except Exception as e:
            print(f"[ModelStore] Older shared model not loaded: {e}")

    print(f"[Memory] Training here ({_rss_mb()} MB in use before).")
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
            _set_pieces = await asyncio.to_thread(
                lambda: set_pieces.SetPieceModel.fit(_with_club_referees(history), set_pieces.CLUB_GUARDS))
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
        # Fresh fits: they hold the history's matches (live_learning.py)
        import live_learning
        live_learning.mark_trained(_set_pieces, history)
        live_learning.mark_trained(_shots, history)
        print(f"[Pipeline] {len(combined)} training matches.")

        # Training takes minutes of CPU. On a worker thread the API keeps
        # answering (from the previous model) instead of timing out.
        print(f"[Memory] {_rss_mb()} MB before loading the model")
        predictor = await asyncio.to_thread(_load_or_train, combined, data_mtime)
        print(f"[Memory] {_rss_mb()} MB with the model")

        # Make predictor available immediately so card analysis works during API calibration
        global _predictor
        # A newly loaded model holds its training matches; the one already
        # running (kept when no fresh model was found) also holds every match
        # fed in since, so its memory stays
        if getattr(predictor, "_applied_keys", None) is None or predictor is not _predictor:
            live_learning.mark_trained(predictor, combined, predictor.canon)
        _predictor = predictor
        print("[Pipeline] Predictor ready — card analysis now available.")
        # Then every match finished since the training data was made
        try:
            learned = await asyncio.to_thread(_learn_finished_matches, LEARN_DAYS)
            if learned:
                print(f"[Pipeline] Learned {learned['matches']} finished matches the models didn't hold.")
        except Exception as e:
            print(f"[Pipeline] Learning recent results failed (non-fatal): {e}")

        # Archive yesterday's predictions from the OLD cache before we start
        # overwriting it below.

        predictions = []
        fixtures: list = []  # pre-init so the block below is safe when API_KEY is unset

        # ── International fixtures (ESPN + The Odds API, no football-data key needed) ──
        # First, so an international break shows up within seconds.
        fixtures.extend(await _fetch_international_fixtures())
        if fixtures:
            await asyncio.to_thread(_cache_team_crests, fixtures)
            predictions = await asyncio.to_thread(_build_predictions, predictor, fixtures, {})
            _predictions_cache = predictions
            _last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            await asyncio.to_thread(_save_predictions_cache)
            print(f"[Pipeline] +INT: {len(fixtures)} international fixtures — "
                  f"{len(predictions)} predictions published.")

        # ── Europa and Conference League (ESPN; not in football-data.org's free plan) ──
        await asyncio.to_thread(_load_europe_model)
        europe = await _fetch_europe_fixtures(predictor, combined)
        if europe:
            fixtures.extend(europe)
            await asyncio.to_thread(_cache_team_crests, europe)
            predictions = await asyncio.to_thread(_build_predictions, predictor, fixtures, {})
            _predictions_cache = predictions
            _last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            await asyncio.to_thread(_save_predictions_cache)
            print(f"[Pipeline] +EL/UECL: {len(europe)} Europa/Conference League fixtures published.")

        # ── More European leagues (ESPN fixtures; league_fixtures.py), and
        # the top flights only SportyBet covers (the linker's catalog; sb_league_fixtures.py) ──
        if not _sb_fixtures:
            await asyncio.to_thread(_load_sb_fixtures)
        more = await _fetch_more_leagues()
        more += [fx for fx in _sb_fixtures if _within_window(fx.get("date", ""))]
        if more:
            fixtures.extend(more)
            await asyncio.to_thread(_cache_team_crests, more)
            predictions = await asyncio.to_thread(_build_predictions, predictor, fixtures, {})
            _predictions_cache = predictions
            _last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            await asyncio.to_thread(_save_predictions_cache)
            print(f"[Pipeline] +{len(more)} fixtures from Belgium, Scotland, Greece, Turkey and the rest "
                  f"({len(_unproven_leagues)} leagues still unproven).")

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
                if not_in_plan(code):
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
                    await asyncio.to_thread(_cache_team_crests, league_fixtures)
                    # No live odds yet on this fast pass — predict_match() falls back
                    # to league-average implied probabilities, which is fine for an
                    # initial publish; the odds pass below refines it.
                    predictions = await asyncio.to_thread(_build_predictions, predictor, fixtures, {})
                    _predictor = predictor
                    _predictions_cache = predictions
                    _last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                    await asyncio.to_thread(_save_predictions_cache)
                    print(f"[Pipeline] +{code}: {len(league_fixtures)} fixtures — "
                          f"{len(predictions)} predictions published so far.")
                # Backfill this league's badge only if we don't already have a
                # confirmed one cached — a one-time cost per league, not worth
                # paying every run, and this is also what self-heals a
                # previously rate-limited/failed lookup (see
                # _has_cached_competition_emblem).
                if not await asyncio.to_thread(_has_cached_competition_emblem, code):
                    try:
                        emblem = await client.fetch_competition_emblem(code)
                        await asyncio.to_thread(_cache_competition_emblem, code, emblem)
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

                predictions = await asyncio.to_thread(_build_predictions, predictor, fixtures, live_odds)
                _predictions_cache = predictions
                _last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                await asyncio.to_thread(_save_predictions_cache)
                print(f"[Pipeline] Republished {len(predictions)} predictions (with live odds).")
                if _unknown_clubs:
                    print(f"[Pipeline] {len(_unknown_clubs)} fixture clubs the model has no matches for: "
                          + ", ".join(f"{n} ({lg})" for n, lg in sorted(_unknown_clubs.items(), key=lambda kv: kv[1])))

            # ── Recent-results Elo calibration (refinement) ───────────────────
            print("[Pipeline] Fetching recent API results to calibrate Elo...")
            calibrated = False
            for code in list(LEAGUES.keys()):
                if not_in_plan(code):
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
                predictions = await asyncio.to_thread(_build_predictions, predictor, fixtures, live_odds)
                _predictions_cache = predictions
                _last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                await asyncio.to_thread(_save_predictions_cache)
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
        if not_in_plan(code):
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

    # New results into the live model, each match once (live_learning.py):
    # most of the last 30 days are already in its training data or were
    # learned from the live scores, and must not count again
    if _predictor is not None:
        import live_learning
        moved: set = set()
        for r in new_df.to_dict("records"):
            row = live_learning.result_row(r)
            if not row:
                continue
            try:
                moved |= live_learning.learn(row, predictor=_predictor,
                                             competition=intl.LEAGUE_CODE if row["league"] == intl.LEAGUE_CODE
                                             else row["league"])
            except Exception as e:
                print(f"[Results] Couldn't learn {row['HomeTeam']} v {row['AwayTeam']}: {e}")
        print(f"[Results] {len(moved) // 2} new result(s) learned by the live model (it already held the rest).")
        if moved:
            _repredict(moved)

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
        # football-data.org competitions paused after a 401/403 (data_fetcher.not_in_plan)
        "leagues_paused": sorted(c for c in data_fetcher.NOT_IN_PLAN if c not in data_fetcher.ESPN_ONLY),
        # The nightly redis backup (deploy/vm/backup-redis.sh): when, how big, and the off-server copy
        "backup": _last_backup(),
    }


_predictions_response: Dict[Tuple, Tuple[bytes, str]] = {}


# ------------------------------------------------------------------ #
# Match page: each side's last five and their last five meetings
# ------------------------------------------------------------------ #
_recent_md: Tuple[float, Optional[pd.DataFrame]] = (0.0, None)
_facts_names: List[Optional[TeamResolver]] = [None]   # names in the results index
RECENT_MD_DAYS = 10


# ------------------------------------------------------------------ #
# Match page: the slow parts, built once per match (match_cache.py)
# ------------------------------------------------------------------ #
import match_cache
_mcache = match_cache.MatchCache(lambda: _get_redis())


# ── Match days: pre-match predictions, results, grades (matchday.py) ──────
MD_KEY = "betiq:md:{}"
MD_TTL = 120 * 86400
MD_DAYS_BACK, MD_DAYS_AHEAD = 7, 14      # what the site's date strip shows
MD_LIVE_MINUTES = 3                       # live scores: how often while matches are on
MD_SETTLE_MINUTES = 15                    # live runs settle tickets at least this often (or on a new score)
_md_last_settle: List[float] = [0.0]
_md_status: Dict[str, Any] = {"at": None, "trigger": None, "report": None,
                              "started": None, "stage": None, "error": None, "failed_at": None}
_md_read_cache: Dict[str, Tuple[float, Dict]] = {}


LIVE_JOBS_KEY = "betiq:live:last"


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
    _md_status.update(started=now.isoformat(timespec="seconds"), stage="loading match days")
    today = now.date()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(days_back + 1)]
    days = await asyncio.to_thread(_md_many, r, dates)
    # A day the snapshot never wrote (or a match added since) would never be
    # scored: its predictions go into the store here, before asking for scores
    added = []
    for d in dates:
        preds = [p for p in _predictions_cache if p.get("date") == d and p.get("sport") in (None, "football")]
        if not preds:
            continue
        try:
            if matchday.merge_predictions(days[d], preds, now):
                await asyncio.to_thread(_md_save, r, d, days[d])
                added.append(d)
        except Exception as e:
            print(f"[MatchDay] couldn't store {d}'s predictions: {e}")
    # Finals from SportyBet or the backups are checked against ESPN by the
    # 3-hourly sweeps (not the 3-minute live runs)
    need = {d: {k: e for k, e in day.items() if matchday.needs_result(e, now, recheck=trigger != "live")}
            for d, day in days.items()}
    need = {d: v for d, v in need.items() if v}
    report: Dict[str, Any] = {"dates": sorted(need), "matches": sum(len(v) for v in need.values()),
                              "updated": 0, "requests": 0, "errors": [], "unmatched": 0}
    if added:
        report["stored_from_predictions"] = added
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
        _md_status["stage"] = f"asking ESPN ({len(pairs)} scoreboards)"
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
        # Backup: matches under way that ESPN still shows as not started
        # (it lists some small friendlies but never scores them)
        backup: List[Dict] = []
        stale = _unscored(need, espn, now)
        if stale:
            # SportyBet first (its live list every run, its results now and then), then API-Football
            _md_status["stage"] = f"asking SportyBet ({len(stale)} unscored)"
            try:
                sb, report["sportybet"] = await asyncio.wait_for(_sportybet_scores(stale, now), 60)
                backup += sb
            except asyncio.TimeoutError:
                report["sportybet"] = {"error": "over 60s"}
            except Exception as e:
                report["sportybet"] = {"error": f"{type(e).__name__}: {e}"[:200]}
            still = _unscored(need, espn + backup, now)
            if still:
                _md_status["stage"] = f"asking API-Football ({len(still)} unscored)"
                af, report["backup"] = await _backup_results(r, still)
                backup += af
        near = {(date.fromisoformat(d) + timedelta(days=o)).isoformat() for d in need for o in (-1, 0, 1)}
        csv = []
        # The league CSVs arrive a day or two after a match: the 3-hourly sweep
        # reads them; the 3-minute live runs only ask ESPN
        if trigger != "live":
            try:
                csv = await asyncio.to_thread(results_feed.csv_results, near)
            except Exception as e:
                report["errors"].append(f"CSV: {type(e).__name__}")
        _md_status["stage"] = "saving scores"
        for d, entries in need.items():
            day, changed = days[d], False
            matched = set()
            for source in (espn, backup, csv):
                for k, res in matchday.match_results(entries, source):
                    matched.add(k)
                    before = {f: (day[k].get("result") or {}).get(f) for f in ("status", "hg", "ag")}
                    was_corrected = (day[k].get("result") or {}).get("corrected")
                    if matchday.apply_result(day[k], res):
                        changed = True
                        report["updated"] += 1
                        fix = day[k]["result"].get("corrected")
                        if fix and fix != was_corrected:
                            report.setdefault("corrected", []).append(
                                f"{day[k]['home']} v {day[k]['away']}: {fix['was'][0]}-{fix['was'][1]} → "
                                f"{day[k]['result']['hg']}-{day[k]['result']['ag']}")
                            r.set(MD_CORRECTED_KEY, now.isoformat(timespec="seconds"))
                        if before != {f: day[k]["result"].get(f) for f in ("status", "hg", "ag")}:
                            report["scores"] = report.get("scores", 0) + 1
            report["unmatched"] += sum(1 for k in entries if k not in matched
                                       and (matchday.kickoff(entries[k]) or now) < now - timedelta(hours=3))
            if changed:
                await asyncio.to_thread(_md_save, r, d, day)
    report["errors"] = report["errors"][:20]
    # Settling reads every account with open tickets (a Redis command each):
    # live runs do it when a score changed, or every MD_SETTLE_MINUTES
    # (live stats change every run; only a new score or status can settle a ticket)
    if trigger == "live" and not report.get("scores") and time.time() - _md_last_settle[0] < MD_SETTLE_MINUTES * 60:
        report["tickets"] = {"skipped": "no new scores"}
    else:
        _md_last_settle[0] = time.time()
        if trigger != "live":
            # Finals for the basketball / racket legs no other source scored
            _md_status["stage"] = "asking Sportradar for missing finals"
            try:
                import sr_results
                report["sportradar"] = await asyncio.wait_for(sr_results.refresh(r), 60)
            except Exception as e:
                report["sportradar"] = {"error": f"{type(e).__name__}: {e}"[:200]}
            # …and Flashscore's day feeds for the ones Sportradar has nothing for
            _md_status["stage"] = "asking Flashscore for missing finals"
            try:
                import fs_results
                report["flashscore"] = await asyncio.wait_for(fs_results.refresh(r), 60)
            except Exception as e:
                report["flashscore"] = {"error": f"{type(e).__name__}: {e}"[:200]}
        _md_status["stage"] = "settling tickets"
        try:
            report["tickets"] = await asyncio.wait_for(asyncio.to_thread(_settle_tickets, r), 90)
        except asyncio.TimeoutError:
            report["tickets"] = {"error": "settling took over 90s; next run retries"}
        except Exception as e:
            report["tickets"] = {"error": str(e)}
    _md_status.update(at=now.isoformat(timespec="seconds"), trigger=trigger, report=report,
                      stage="done", error=None)
    if report["updated"]:
        print(f"[MatchDay] {trigger}: {report['updated']} results updated over {report['dates']}")
    return report


SB_RESULTS_EVERY = 10 * 60  # seconds between reads of SportyBet's football results (a day is many pages)
_sb_results_at: Dict[str, float] = {}


AF_LIVE_EVERY = 15 * 60   # seconds between backup requests
AF_LIVE_DAILY_CAP = 24     # of the key's 100 a day (referees 12, nightly stats collector 60)
_af_live_last = [0.0]


MD_RUN_LIMIT = {"live": 170, "sweep": 900}   # seconds before a run is given up (live: under its 3 minutes)


LEARN_DAYS = 10           # finished matches looked at after a model is (re)built
_learn_status: Dict[str, Any] = {"at": None, "matches": 0, "total": 0, "last": [], "repredicted": 0}


_strip_cache: Dict[str, Tuple[float, Any]] = {}


_accuracy_cache: Dict[int, Tuple[float, Any]] = {}


_sport_accuracy_cache: Dict[Tuple[str, int], Tuple[float, Any]] = {}


# ── Tickets: booking codes per account, settled from the match days ──────
TICKETS_OPEN_KEY = "betiq:tickets:open"       # accounts with unsettled tickets
TICKETS_STATS_KEY = "betiq:tickets:stats"
TICKET_SOURCES = {"slip", "optimizer", "code_check", "chat", "match", "daily", "other"}


ANON_UID = "anonymous"      # codes made by visitors who aren't signed in (admin sees them all)
ANON_MAX_TICKETS = 1000


SETTLE_LEGS_DAYS = 30         # a settled ticket's other legs are still graded this long after its last match
TICKETS_RESCAN_HOURS = 6      # how often every account's tickets are looked through for legs left pending
_tickets_rescan_at = [0.0]


MD_CORRECTED_KEY = "betiq:md:corrected_at"     # when a final score was last corrected (matchday.corrects)
TICKETS_RECHECKED_KEY = "betiq:tickets:rechecked_at"
RECHECK_TICKET_DAYS = 14


_all_tickets_cache: List[Any] = [0.0, None]
_names_cache: Dict[str, Tuple[float, Dict[str, str]]] = {}


TICKET_LIVE_DAYS = 14   # settled tickets keep their legs' scores and stats this long


TICKETS_TRASH_MAX = 50
TICKETS_TRASH_DAYS = 30       # a removed ticket can be put back this long


# ── Push notification subscriptions ──────────────────────────────────────── #

PUSH_SUBS_KEY = "betiq:push_subs"   # web_push.SUBS_KEY


# Browser push services. The server POSTs notifications to a subscription's
# endpoint, so any other host would let callers aim our server at anything.
_PUSH_HOSTS = re.compile(r"^(fcm\.googleapis\.com|android\.googleapis\.com|updates\.push\.services\.mozilla\.com|"
                         r"[a-z0-9.-]+\.push\.apple\.com|[a-z0-9.-]+\.notify\.windows\.com)$")
MAX_PUSH_SUBS = 50_000


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


REVENUE_CACHE_SECONDS = 300
_revenue_cache: Dict[str, Any] = {"at": 0.0, "data": None}


# ── Traffic (traffic.py) ──────────────────────────────────────────────────
_traffic = traffic.Traffic()
TRAFFIC_FLUSH_MINUTES = 5


def _flush_traffic() -> int:
    return _traffic.flush(_get_redis())


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
    "bb_collect": ("Basketball: collect results from SportyBet (and older days)", lambda: _bb_collect()),
    "bb_fit": ("Basketball: rate every league from the results", lambda: _bb_fit()),
    "bb_refresh": ("Basketball: price SportyBet's matches now", lambda: _bb_refresh()),
    "bb_live": ("Basketball: live scores and finals for games under way", lambda: _bb_live_tick()),
    "tennis_collect": ("Tennis: collect results from SportyBet (form and head-to-head)", lambda: _tennis_collect()),
    "web_probe": ("Probe football.com and SportyBet tennis/table tennis from this server (to Redis)",
                  lambda: _web_probe()),
    "table_tennis_collect": ("Table tennis: collect results from SportyBet (form and head-to-head)",
                             lambda: _table_tennis_collect()),
    "tennis_refresh": ("Tennis: price SportyBet's matches now", lambda: _tennis_refresh()),
    "table_tennis_refresh": ("Table tennis: price SportyBet's matches now", lambda: _table_tennis_refresh()),
    "tennis_live": ("Tennis: live scores and finals for matches under way", lambda: _tennis_live_tick()),
    "table_tennis_live": ("Table tennis: live scores and finals for matches under way", lambda: _table_tennis_live_tick()),
    "racket_models": ("Tennis & table tennis: load the nightly ratings", lambda: _rk_load_models()),
    "props_load": ("Player props: load box scores and the check's numbers", lambda: _props_load()),
    "fb_props": ("Player props: price goalscorers now", lambda: _fb_props_refresh()),
    "daily_slips": ("Remake today's daily odds slips (new booking codes)", lambda: _build_daily(__import__("daily_slips").today(), force=True)),
    "shot_blend": ("Score our shot lines against SportyBet's", lambda: _shot_blend_job()),
    "market_review": ("Weekly accuracy review (pause markets falling short)", lambda: _review_job()),
    "league_review": ("Weekly league drift check (flag or pause leagues falling short)", lambda: _league_review_job()),
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


# ------------------------------------------------------------------ #
# User data (saves, bets, accumulators, stats)
# ------------------------------------------------------------------ #


# "table_tennis" is how a table-tennis prediction names its sport (the event modal uses it)
SPORTS = ("basketball", "tennis", "table-tennis", "table_tennis")


_sports_memory_cache: Dict[str, tuple] = {}  # sport -> (data, fetched_at_monotonic)


# ── Basketball (basketball_data / _model / _markets / _predictions) ─────────
# Results from SportyBet (every league it covers) → each league's ratings →
# every line SportyBet offers on its listed matches, priced, with its ids.
BB_MODEL_KEY = "betiq:bb:model"             # the rated leagues (encoded)
BB_PREDICTIONS_KEY = "betiq:bb:predictions"  # every listed match, every priced line (encoded)
BB_STATUS_KEY = "betiq:bb:status"
BB_SLIM_LINES = 8                           # lines per match in the list (the rest: the match's own endpoint)
_bb_leagues: Dict[str, Any] = {}
_bb_predictions: List[Dict] = []
_bb_status: Dict[str, Any] = {}


async def _bb_collect() -> Dict[str, Any]:
    """Yesterday's and today's results, and a few older days (the backfill)."""
    import basketball_data as bd
    r = _get_redis()
    if not r:
        return {"error": "no database"}
    have = [k.decode() if isinstance(k, bytes) else k for k in (r.hkeys(bd.RESULTS_KEY) or [])]
    today = date.today()
    fetched, games, failed = 0, 0, 0
    for day in bd.days_to_collect(have, today):
        try:
            found = await bd.fetch_results_day(day)
        except Exception as e:
            failed += 1
            print(f"[Basketball] results for {day}: {e}")
            continue
        # A past day with nothing (older than SportyBet keeps) is stored too, so it isn't asked again
        r.hset(bd.RESULTS_KEY, day, bd.encode(found))
        fetched += 1
        games += len(found)
        await asyncio.sleep(0.5)
    total_days = r.hlen(bd.RESULTS_KEY)
    _bb_save_status(collected={"days": fetched, "games": games, "failed": failed, "days_stored": total_days})
    print(f"[Basketball] results: {fetched} days, {games} games ({total_days} days stored)")
    if games:
        await _bb_fit()
    return {"days": fetched, "games": games, "failed": failed}


_bb_team_games: Dict[str, List] = {}      # basketball_facts index: team -> its games, newest first


async def _bb_fit() -> Dict[str, Any]:
    """Rate every league from the stored results."""
    try:
        got = await asyncio.to_thread(_bb_fit_sync)
    except Exception as e:
        print(f"[Basketball] rating failed: {e}")
        return {"error": str(e)}
    _bb_save_status(model=got)
    print(f"[Basketball] rated {got['leagues']} leagues ({got['teams']} teams) from {got['games']} games")
    return got


async def _bb_refresh() -> List[Dict]:
    """Price every basketball match SportyBet lists."""
    import basketball_data as bd
    import basketball_predictions as bp
    try:
        events, report = await bd.fetch_upcoming()
    except Exception as e:
        print(f"[Basketball] listing failed: {e}")
        events, report = [], [str(e)]
    preds = await asyncio.to_thread(bp.build, events, dict(_bb_leagues))
    try:
        await _bb_index()          # each team's recent games, for the form on the list
    except Exception as e:
        print(f"[Basketball] couldn't index teams: {e}")
    try:
        props = await _bb_add_props(preds)
    except Exception as e:
        props = 0
        print(f"[Props] basketball player lines failed: {e}")
    if not preds and not events:
        _bb_save_status(listing={"events": 0, "report": report})
        return _bb_predictions
    _bb_predictions[:] = preds
    r = _get_redis()
    if r:
        try:
            r.set(BB_PREDICTIONS_KEY, bd.encode(preds), ex=6 * 3600)
            r.setex("betiq:sports:basketball", 3600, json.dumps([_bb_slim(p) for p in preds]))
        except Exception as e:
            print(f"[Basketball] couldn't save predictions: {e}")
    try:
        if _bbmd_merge(preds):
            _bb_strip_cache.clear()
    except Exception as e:
        print(f"[Basketball] couldn't keep the match days: {e}")
    rated = sum(1 for p in preds if p.get("rated"))
    _bb_save_status(listing={"events": len(events), "predictions": len(preds), "rated": rated, "report": report,
                             "player_lines": props})
    print(f"[Basketball] {len(preds)} matches priced ({rated} with both teams rated) · {' · '.join(report)}")
    return preds


# ── Tennis and table tennis form and head-to-head (tennis_facts.py) ─────────
# sport: "tennis" | "table_tennis" (tennis_facts.SPORTS)
_racket_players: Dict[str, Dict[str, List]] = {"tennis": {}, "table_tennis": {}}   # player -> his matches, newest first
_racket_status: Dict[str, Dict[str, Any]] = {"tennis": {}, "table_tennis": {}}
_tennis_players = _racket_players["tennis"]          # (the tennis one, by its old name)
_tennis_status = _racket_status["tennis"]


async def _web_probe() -> Dict[str, Any]:
    """What this server can reach (web_probe.py), saved for the read_probe workflow job."""
    import web_probe
    report = await web_probe.run()
    try:
        report["live"] = await _live_probe()
    except Exception as e:
        report["live"] = {"error": str(e)[:300]}
    r = _get_redis()
    if r:
        r.set(web_probe.PROBE_KEY, json.dumps(report, default=str)[:900_000], ex=7 * 86400)
    print(f"[Probe] done: {list(report)}")
    return report


async def _tennis_collect() -> Dict[str, Any]:
    return await _racket_collect("tennis")


async def _table_tennis_collect() -> Dict[str, Any]:
    return await _racket_collect("table_tennis")


# ── Tennis and table tennis: every SportyBet line priced, match days, live ──
# (racket_predictions / _markets / _matchday; the ratings from the nightly
# "Racket data" fits: tennis_fit.py, table_tennis_fit.py)
RK_SPORTS = ("tennis", "table_tennis")
RK_PRED_KEY = "betiq:{sport}:predictions"
RK_SLIM_LINES = 8
_rk_models: Dict[str, Dict] = {}
_rk_predictions: Dict[str, List[Dict]] = {"tennis": [], "table_tennis": []}
_rk_status: Dict[str, Dict[str, Any]] = {"tennis": {}, "table_tennis": {}}
_rkmd_status: Dict[str, Dict[str, Any]] = {"tennis": {}, "table_tennis": {}}
_rk_strip_cache: Dict[str, Tuple[float, Any]] = {}


async def _rk_load_models() -> Dict[str, int]:
    """The fitted ratings (nightly, GitHub Actions) from Redis."""
    got = await asyncio.to_thread(_rk_load_models_sync)
    print(f"[Racket] models loaded: {got} players")
    return got


async def _tennis_refresh() -> List[Dict]:
    return await _rk_refresh("tennis")


async def _table_tennis_refresh() -> List[Dict]:
    return await _rk_refresh("table_tennis")


RESULTS_REFRESH_MINUTES = 8  # SportyBet's finals for the live ticks
PAGE_FROZEN_MINUTES = 20   # a live score read from a match's own page, unchanged this long: taken as over


OVERDUE_EVERY = 10 * 60   # seconds between reads of one overdue match's page
OVERDUE_PER_TICK = 15
_overdue_at: Dict[str, float] = {}


async def _tennis_live_tick() -> Dict[str, Any]:
    return await _guarded_tick("tennis_live", lambda: _rk_live_tick("tennis"), 110)


async def _table_tennis_live_tick() -> Dict[str, Any]:
    return await _guarded_tick("table_tennis_live", lambda: _rk_live_tick("table_tennis"), 110)


LIVE_STATS_MINUTES = 2


# ── Basketball match days (basketball_matchday.py): the date strip, live, history ──
_bbmd_status: Dict[str, Any] = {}


async def _bb_live_tick() -> Dict[str, Any]:
    """Live scores for games under way, and finals (graded) from SportyBet's results."""
    import basketball_data as bd
    import basketball_matchday as bbmd
    r = _get_redis()
    if not r:
        return {"skipped": "no Redis"}
    now = datetime.now(timezone.utc)
    dates = [(now.date() - timedelta(days=i)).isoformat() for i in (2, 1, 0)]
    days = await asyncio.to_thread(lambda: {d: _bbmd_load(r, d) for d in dates})
    open_ = {e["id"]: (d, e) for d, day in days.items() for e in day.values() if bbmd.needs_result(e, now)}
    if not open_:
        return {"open": 0}
    changed = set()
    # Finals from the stored results (read by _bb_results_refresh every few minutes)
    finals = {g["id"]: g for g in await asyncio.to_thread(_bb_results, r, dates + [(now.date() + timedelta(days=1)).isoformat()])}
    for eid, (d, e) in open_.items():
        if eid in finals and bbmd.apply_result(e, finals[eid]):
            changed.add(d)
    # In play: the rest that have tipped off
    playing = [eid for eid, (d, e) in open_.items() if (e.get("result") or {}).get("status") != bbmd.FINISHED
               and now - (bbmd.kickoff(e) or now) < timedelta(hours=4)]
    overdue = _overdue("basketball", [eid for eid, (d, e) in open_.items() if eid not in playing
                                      and (e.get("result") or {}).get("status") != bbmd.FINISHED])
    how = "none started"
    ended: Dict[str, Dict] = {}
    if playing or overdue:
        paged: set = set()
        try:
            live, how, checked = await bd.fetch_live(playing + overdue, final=bd.parse_result, finals=ended,
                                                     from_pages=paged)
        except Exception as e:
            live, how, checked = {}, f"failed: {e}", set()
        for eid in playing + overdue:
            d, e = open_[eid]
            if _apply_live_reading(e, eid, eid in overdue, live, ended, checked, paged, now,
                                   bbmd.apply_result, bbmd.apply_live, bbmd.stale_live):
                changed.add(d)
    if changed:
        await asyncio.to_thread(lambda: [_bbmd_save(r, d, days[d]) for d in changed])
    if changed:
        _bb_strip_cache.clear()
    _bbmd_status.update(at=now.isoformat(timespec="seconds"), open=len(open_), playing=len(playing),
                        overdue=len(overdue), finals_from_pages=len(ended), live_source=how)
    return dict(_bbmd_status)


_bb_strip_cache: Dict[str, Tuple[float, Any]] = {}


# ── Player props (props_collect → props_backtest → props_pricing) ─────────
# Box scores collected nightly in GitHub Actions; the walk-forward check's
# spreads and scale; SportyBet's player lines priced on its match pages.
PROPS_FB_KEY = "betiq:props:fb:priced"      # football event id -> priced goalscorers
PROPS_BB_MAX_PAGES = 40                     # basketball match pages read per refresh (props are on big games)
FB_PROP_LEAGUES = {"PL": "Premier League", "PD": "LaLiga", "BL1": "Bundesliga", "SA": "Serie A", "FL1": "Ligue 1"}
_props_players: Dict[Tuple[str, str], Dict[str, Dict]] = {}
_props_calib: Dict[str, Any] = {}
_props_fb: Dict[str, List[Dict]] = {}


async def _props_load() -> Dict[str, int]:
    """The players' box scores and the walk-forward check's numbers, from Redis."""
    try:
        got = await asyncio.to_thread(_props_load_sync)
    except Exception as e:
        print(f"[Props] couldn't load: {e}")
        return {}
    print(f"[Props] players loaded: {got}")
    return got


async def _fb_props_refresh() -> Dict[str, int]:
    """Anytime goalscorers on our football matches in the next three days (top five leagues)."""
    import props_pricing as pr
    import sportybet
    now = datetime.now(timezone.utc)
    last = (now + timedelta(days=3)).strftime("%Y-%m-%d")
    scale_by = {lg: (v or {}).get("scale") or 1.0 for lg, v in (_props_calib.get("fb") or {}).items()}
    priced: Dict[str, List[Dict]] = {}
    for p in _predictions_cache:
        league = FB_PROP_LEAGUES.get(p.get("league") or "")
        players = _props_players.get(("fb", league)) if league else None
        if not players or not (now.strftime("%Y-%m-%d") <= p.get("date", "") <= last):
            continue
        ev = _linked_event(p)
        if not ev or not ev.get("eventId"):
            continue
        try:
            page = await sportybet.event_page(str(ev["eventId"]))
        except Exception as e:
            print(f"[Props] scorer page {ev.get('eventId')}: {e}")
            continue
        tt = (p.get("goal_markets") or {}).get("team_totals") or {}
        lam = {"home": pr.team_goals((tt.get("home") or {}).get("0.5")),
               "away": pr.team_goals((tt.get("away") or {}).get("0.5"))}
        team_exp: Dict[str, Optional[float]] = {}
        for o in pr.scorer_offers(page or {}):
            if o["team"] not in team_exp:
                side = "home" if sportybet.team_similarity(o["team"], p["home"]) >= sportybet.team_similarity(o["team"], p["away"]) else "away"
                team_exp[o["team"]] = lam[side]
        lines = pr.scorer_price(page or {}, players, team_exp, scale_by.get(league, 1.0))
        if lines:
            priced[str(ev["eventId"])] = [{**x, "home": p["home"], "away": p["away"], "date": p["date"],
                                           "time": p.get("time") or "", "league": p.get("league_name") or league}
                                          for x in lines]
        await asyncio.sleep(0.3)
    _props_fb.clear()
    _props_fb.update(priced)
    r = _get_redis()
    if r:
        try:
            r.set(PROPS_FB_KEY, json.dumps(priced), ex=6 * 3600)
        except Exception:
            pass
    print(f"[Props] goalscorers priced on {len(priced)} matches")
    return {"matches": len(priced), "players": sum(len(v) for v in priced.values())}


_LEAGUE_NAME_TO_CODE = {info["name"].lower(): code for code, info in LEAGUES.items()}

# Single shared client (and its rate-limit semaphore) for on-demand
# football-data.org calls made outside the paced pipeline loop — so
# concurrent requests (e.g. several browsers loading different league badges
# at once) serialize against the same 10 req/min budget instead of each
# spawning its own unthrottled client.
_fd_ondemand_client: Optional[FootballDataClient] = None


CREST_GAP_S = 15          # between team-list requests: well under football-data.org's 10 a minute


LOGO_SECONDS = 24 * 3600        # a badge found is kept this long (in memory, and by browsers)
LOGO_MISS_SECONDS = 3600        # a badge not found is asked for again after this
_logo_memo: Dict[Tuple, Tuple[float, Dict[str, Any]]] = {}


OLD_LEADERBOARD_KEY = "betiq:leaderboard"   # counted self-reported "won" bets: deleted at startup
LEADERBOARD_SIZE = 20


# ── SportyBet links: predictions matched to SportyBet events ahead of time ──
# Booking then needs no event listing, just one request for the code.
SB_LINKS_KEY = "betiq:sportybet:links"
# The last linking run's result, so a restart (a new deploy) shows it at once
SB_LINK_STATUS_KEY = "betiq:sportybet:link_status"
SB_LINK_MINUTES = 30
_sb_links: Dict[str, Dict] = {}
_sb_link_status: Dict[str, Any] = {"at": None, "events": 0, "predictions": 0, "linked": 0, "report": []}
_sb_link_lock: Optional[Tuple[Any, asyncio.Lock]] = None  # (event loop, lock)


_INTL_TOURNAMENT = re.compile(r"international|friendl|nations league|qualif|world cup|africa|afcon|"
                              r"concacaf|conmebol|uefa|asian cup|euro\b", re.I)


MARKET_PAGES = 3   # SportyBet match pages read to confirm market names


async def _link_sportybet_events(trigger: str = "schedule") -> Dict[str, Any]:
    """Match upcoming football predictions to SportyBet events and keep the
    links. `trigger` says what started the run: startup (a new deploy or
    restart), pipeline, international, schedule or manual."""
    async with _link_lock():
        return await _link_sportybet_events_now(trigger)


# ── Referees appointed to upcoming matches (referees.py) ──
REFEREE_HOURS = 3
_referees: Dict[str, Any] = {"at": None, "appointments": {}, "report": None, "trigger": None}
_referee_lock: Optional[Tuple[Any, asyncio.Lock]] = None


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
        await asyncio.to_thread(_save_predictions_cache)
        print(f"[Referees] {trigger}: {len(found)} referees; errors {report.get('errors') or 'none'}")
        return report


# ── Past referees from football-data.org, for the cards model ──
_fd_refs_status: Dict[str, Any] = {"at": None, "report": None, "refs": 0, "seasons_done": 0}


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
    state = await asyncio.to_thread(_fd_refs_load, r)
    fd = FootballDataClient(API_KEY)
    def save():
        r.set(referee_sources.REFS_KEY, gzip.compress(json.dumps(state, separators=(",", ":")).encode()))
    try:
        async with httpx.AsyncClient() as hc:
            report = await referee_sources.collect_past(lambda url: fd._get(hc, url), state, date.today(), asyncio.sleep)
    except asyncio.CancelledError:                # the server is stopping (a redeploy): keep the seasons read so far
        save()
        raise
    await asyncio.to_thread(save)
    _fd_refs_status.update(at=state.get("at"), report=report, refs=len(state.get("refs") or {}),
                           seasons_done=len(state.get("done") or []), trigger=trigger)
    print(f"[Referees] football-data.org: {report['found']} new past referees, {report['requests']} requests")
    return report


# National-team match stats (corners, bookings, shots) by (date, team keys):
# the match page's team averages, whose results come from another source
_intl_stats_by_key: Dict[Tuple[str, str, str], Dict[str, float]] = {}


# ── Our shot lines vs SportyBet's (shots.blend_report) ──
SHOT_BLEND_KEY = "betiq:shots:blend"
SHOT_BLEND_DAYS = 90
_shot_blend: Dict[str, Any] = {"weights": {}, "report": None, "at": None}


async def _shot_blend_job() -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(_refresh_shot_blend)
    except Exception as e:
        print(f"[Shots] vs SportyBet failed: {e}")
        return {"error": str(e)}


# ── Weekly accuracy review (market_review.py) ──
REVIEW_KEY = "betiq:review"
_review: Dict[str, Any] = {"mode": "flag", "overrides": {}, "latest": None, "history": [], "loaded": False}


async def _review_job() -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(_run_review)
    except Exception as e:
        print(f"[Review] failed: {e}")
        return {"error": str(e)}


# ── Weekly league drift check (league_review.py) ──
LEAGUE_REVIEW_KEY = "betiq:league_review"
_league_review: Dict[str, Any] = {"mode": "flag", "overrides": {}, "latest": None, "history": [], "loaded": False}


async def _league_review_job() -> Dict[str, Any]:
    try:
        result = await asyncio.to_thread(_run_league_review)
    except Exception as e:
        print(f"[Drift] failed: {e}")
        return {"error": str(e)}
    import league_review
    text = league_review.message(result, _league_review.get("mode", "flag")) if "leagues" in result else None
    if text:
        import telegram_poster
        sent = await telegram_poster.notify_admin(text)
        result["telegram"] = sent.get("ok") or sent.get("error")
        if result.get("newly_failing"):
            try:
                import sentry_sdk
                sentry_sdk.capture_message(text, level="warning")
            except Exception:
                pass
    return result


OPT_SPORTS = ("football", "basketball", "tennis", "table_tennis")


LIVE_CHECK_ROUNDS = 3          # check, drop and re-solve at most this often
LIVE_PAGE_SECONDS = 60         # a match page read for the check is reused this long
LIVE_PAGES_AT_ONCE = 8
LIVE_PAGE_TIMEOUT = 8.0
_live_pages: Dict[str, Tuple[float, Optional[Dict]]] = {}


# ── Daily odds (daily_slips.py) ─────────────────────────────────────────────
DAILY_KEY = "betiq:daily:{}"
DAILY_RECORD_KEY = "betiq:daily:record"
_daily_lock = asyncio.Lock()
_daily_memory: Dict[str, Dict[str, Any]] = {}   # when the database is down: still one set a day


async def _build_daily(day: str, force: bool = False) -> Optional[Dict[str, Any]]:
    """The day's slips, made once (just before its midnight, by the daily
    tick) and booked on SportyBet straight away, so everyone gets the same
    slips and codes all day. `force` (admin) makes them again."""
    import daily_slips
    r = _get_redis()
    async with _daily_lock:
        if not force:
            doc = _daily_load(r, day)
            if doc:
                return doc
        if not _predictions_cache:
            return None
        # In turn, each leaving out the matches the ones before took: no match
        # on two targets' slips, so one result can't cut them all
        slips: List[Dict[str, Any]] = []
        for target in daily_slips.TARGETS:
            slips.append(await _daily_slip(target, day, daily_slips.match_keys({"slips": slips})))
        doc = {"date": day, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "slips": slips}
        await _book_daily(doc)
        _daily_save(r, doc)
        print(f"[Daily] {day}: " + ", ".join(
            f"{int(s['target'])}x " + ((s.get("booking") or {}).get("code") or ("no slip" if s["status"] == "none" else "no code yet"))
            for s in slips))
        return doc


DAILY_SWEEP_DAYS = 30           # past the 7-day expiry, so every pick ends graded or "unknown"
DAILY_SWEEP_MINUTES = 60
_daily_swept = [0.0]


_reliability_cache: Dict[str, Any] = {}
RELIABILITY_MIN_SETTLED = 30      # a band needs this many settled legs before the slip uses it


DAILY_RECHECKED_KEY = "betiq:daily:rechecked_at"


# ── The daily odds on X and Telegram (x_poster.py, telegram_poster.py) ──
# Each channel module: KEYS, MAX_TRIES, configured(), missing(),
# compose(doc, site) -> text | None, async publish(text) -> {ok, id, url, error}
POST_CHANNELS = {"x": "x_poster", "telegram": "telegram_poster"}
POST_CONFIG_KEY = "betiq:config:{}"         # channel → {"enabled": bool}
POST_DAY_KEY = "betiq:post:{}:{}"           # channel, date → that day's post
POST_GRACE_MINUTES = 60  # after the day starts (midnight, Lagos): post then even if a slip still waits for its code


ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")
PAYWALL_KEY  = "betiq:config:paywall_enabled"


# ── Free trial for new accounts (trial.py) ──
# The site asks for it (POST /api/trial/start) on a new account's visits; the
# trial is written to the account's Clerk metadata, so every plan check
# covers it, and recorded against its email.
TRIAL_KEY = "betiq:config:trial"
TRIAL_EMAIL_KEY = "betiq:trial:email:{}"    # hashed mailbox → the account that had the trial
TRIAL_STATS_KEY = "betiq:trial:stats"


REFRESH_COOLDOWN = 30 * 60
_last_public_refresh = 0.0


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


async def _clubelo_refresh() -> None:
    import clubelo
    r = _get_redis()
    if not r:
        return
    def run():
        with httpx.Client(follow_redirects=True, headers={"User-Agent": "BetIQ/1.0"}) as c:
            return clubelo.refresh(r, c)
    try:
        st = await asyncio.to_thread(run)
        print(f"[ClubElo] {st}")
    except Exception as e:
        print(f"[ClubElo] refresh failed: {e}")


@app.on_event("startup")
async def startup():
    # `docker compose kill -s SIGUSR1 api` prints every thread's stack to the
    # logs: to see where something is stuck without restarting
    try:
        import faulthandler
        import signal
        faulthandler.register(signal.SIGUSR1, all_threads=True)
    except Exception:
        pass
    for found, meant in _mangled_settings():
        print(f'[Settings] WARNING: .env has "{found}" — did you mean "{meant}"? It is unset until the name is fixed.')
    r = _get_redis()            # says loudly, right away, if the database is missing
    error_tracking.init_from_redis(r)
    if r:
        try:
            r.delete(OLD_LEADERBOARD_KEY)   # self-reported wins; the leaderboard now reads tickets
        except Exception:
            pass
    _restore_shot_blend()
    _restore_review()
    _load_hidden()
    _load_h2h_cache()
    _load_predictions_cache()   # serve cached predictions instantly while pipeline rebuilds
    _bb_load()
    asyncio.create_task(_props_load())
    asyncio.create_task(_link_on_startup())  # every deploy re-links to SportyBet straight away
    asyncio.create_task(_run_pipeline())
    asyncio.create_task(_load_fbref_data())
    # results fetcher runs via scheduler only — not on boot to avoid API contention with pipeline
    scheduler.add_job(_run_pipeline, "interval", hours=12, id="refresh")
    # ClubElo (clubelo.py): today's ratings, and the weekly history a few at a time
    scheduler.add_job(_clubelo_refresh, "interval", hours=2, id="clubelo",
                      next_run_time=datetime.now() + timedelta(minutes=4))
    scheduler.add_job(lambda: error_tracking.init_from_redis(_get_redis()), "interval", hours=1, id="sentry_dsn")
    scheduler.add_job(_load_fbref_data, "interval", days=7, id="fbref_refresh")
    scheduler.add_job(_fetch_and_save_results, "interval", hours=3, id="results_refresh")
    scheduler.add_job(_link_sportybet_events, "interval", minutes=SB_LINK_MINUTES, id="sportybet_links")
    scheduler.add_job(_flush_traffic, "interval", minutes=TRAFFIC_FLUSH_MINUTES, id="traffic_flush")
    scheduler.add_job(_refresh_referees, "interval", hours=REFEREE_HOURS, id="referees")
    # Past referees: daily, first 10 minutes after start (after the pipeline's own football-data requests)
    scheduler.add_job(_collect_fd_referees, "interval", hours=24, id="fd_referees",
                      next_run_time=datetime.now() + timedelta(minutes=10))
    scheduler.add_job(_matchday_live, "interval", minutes=MD_LIVE_MINUTES, id="matchday_live",
                      max_instances=1, coalesce=True, misfire_grace_time=60)
    scheduler.add_job(_matchday_sweep, "interval", hours=3, id="matchday_sweep",
                      max_instances=1, coalesce=True)
    scheduler.add_job(_daily_tick, "interval", minutes=5, id="daily_slips_tick", max_instances=1, coalesce=True,
                      next_run_time=datetime.now() + timedelta(minutes=3))
    # Sundays 22:30 UTC (23:30 Lagos), before Monday's daily slips are made (23:50 Lagos)
    scheduler.add_job(_review_job, "cron", day_of_week="sun", hour=22, minute=30, id="market_review",
                      max_instances=1, coalesce=True)
    scheduler.add_job(_league_review_job, "cron", day_of_week="sun", hour=22, minute=40, id="league_review",
                      max_instances=1, coalesce=True)
    # Basketball: results (and the backfill) every half hour, ratings after new
    # results and every 6 hours, SportyBet's matches priced every 15 minutes
    scheduler.add_job(_bb_collect, "interval", minutes=30, id="bb_collect", max_instances=1, coalesce=True,
                      next_run_time=datetime.now() + timedelta(minutes=2))
    scheduler.add_job(_bb_fit, "interval", hours=6, id="bb_fit", max_instances=1, coalesce=True)
    # Tennis results for form and head-to-head (yesterday, today, and the backfill)
    scheduler.add_job(_tennis_collect, "interval", minutes=30, id="tennis_collect", max_instances=1, misfire_grace_time=300, coalesce=True,
                      next_run_time=datetime.now() + timedelta(minutes=5))
    scheduler.add_job(_table_tennis_collect, "interval", minutes=30, id="table_tennis_collect", max_instances=1, misfire_grace_time=300,
                      coalesce=True, next_run_time=datetime.now() + timedelta(minutes=8))
    # Once after each deploy: what football.com and SportyBet's racket sports look like from here
    # (a one-off "date" job is dropped if the loop is busy at that second: startup is busy)
    scheduler.add_job(_web_probe, "interval", hours=24, id="web_probe", max_instances=1, coalesce=True,
                      misfire_grace_time=900, next_run_time=datetime.now() + timedelta(seconds=75))
    # Tennis and table tennis: the nightly ratings, SportyBet's matches priced, live scores and finals
    # Crests: long after the boot pipeline's fixture requests (the index persists in Redis meanwhile)
    scheduler.add_job(_crest_index_refresh, "interval", hours=24, id="crest_index", max_instances=1, misfire_grace_time=600,
                      coalesce=True, next_run_time=datetime.now() + timedelta(hours=3))
    scheduler.add_job(_fb_calibration_load, "interval", hours=3, id="fb_calibration", max_instances=1, misfire_grace_time=300,
                      coalesce=True, next_run_time=datetime.now() + timedelta(seconds=20))
    scheduler.add_job(_rk_load_models, "interval", hours=3, id="racket_models", max_instances=1, misfire_grace_time=300, coalesce=True,
                      next_run_time=datetime.now() + timedelta(seconds=30))
    scheduler.add_job(_tennis_refresh, "interval", minutes=15, id="tennis_refresh", max_instances=1, misfire_grace_time=300, coalesce=True,
                      next_run_time=datetime.now() + timedelta(minutes=2))
    scheduler.add_job(_table_tennis_refresh, "interval", minutes=10, id="table_tennis_refresh", max_instances=1, misfire_grace_time=300,
                      coalesce=True, next_run_time=datetime.now() + timedelta(minutes=3))
    scheduler.add_job(_live_stats_tick, "interval", minutes=LIVE_STATS_MINUTES, id="live_stats", max_instances=1,
                      misfire_grace_time=300, coalesce=True, next_run_time=datetime.now() + timedelta(minutes=1))
    scheduler.add_job(_tennis_live_tick, "interval", minutes=1, id="tennis_live", max_instances=1, misfire_grace_time=300, coalesce=True,
                      next_run_time=datetime.now() + timedelta(minutes=4))
    scheduler.add_job(_table_tennis_live_tick, "interval", minutes=1, id="table_tennis_live", max_instances=1, misfire_grace_time=300,
                      coalesce=True, next_run_time=datetime.now() + timedelta(minutes=5))
    # Basketball games under way: live scores every minute, and finals graded as they come in
    # Finals for the live ticks (a day's results are many pages: their own jobs)
    scheduler.add_job(_bb_results_job, "interval", minutes=RESULTS_REFRESH_MINUTES, id="bb_results", max_instances=1,
                      coalesce=True, misfire_grace_time=300, next_run_time=datetime.now() + timedelta(minutes=3))
    scheduler.add_job(_tennis_results_job, "interval", minutes=RESULTS_REFRESH_MINUTES, id="tennis_results", max_instances=1,
                      coalesce=True, misfire_grace_time=300, next_run_time=datetime.now() + timedelta(minutes=4))
    scheduler.add_job(_table_tennis_results_job, "interval", minutes=RESULTS_REFRESH_MINUTES, id="table_tennis_results",
                      max_instances=1, coalesce=True, misfire_grace_time=300,
                      next_run_time=datetime.now() + timedelta(minutes=6))
    scheduler.add_job(_bb_live_guarded, "interval", minutes=1, id="bb_live", max_instances=1, coalesce=True, misfire_grace_time=300,
                      next_run_time=datetime.now() + timedelta(minutes=4))
    # Player props: box scores and the check's numbers (nightly in Actions), goalscorers priced
    scheduler.add_job(_props_load, "interval", hours=3, id="props_load", max_instances=1, coalesce=True)
    scheduler.add_job(_fb_props_refresh, "interval", minutes=45, id="fb_props", max_instances=1, coalesce=True,
                      next_run_time=datetime.now() + timedelta(minutes=6))
    scheduler.add_job(_bb_refresh, "interval", minutes=15, id="bb_refresh", max_instances=1, coalesce=True,
                      next_run_time=datetime.now() + timedelta(minutes=1))
    scheduler.add_job(_shot_blend_job, "interval", hours=6, id="shot_blend",
                      next_run_time=datetime.now() + timedelta(minutes=15))
    # How long each job runs, and the loop-lag watch (perf.py; the probe reads them)
    from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED, EVENT_JOB_SUBMITTED

    def _job_done(ev):
        try:
            perf.record_job(ev.job_id, (datetime.now(ev.scheduled_run_time.tzinfo) - ev.scheduled_run_time).total_seconds())
        except Exception:
            pass
    scheduler.add_listener(_job_done, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
    scheduler.add_listener(_job_marker, EVENT_JOB_SUBMITTED | EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED)
    asyncio.get_event_loop().create_task(perf.watch_loop(_get_redis))
    scheduler.start()
    # Which code this server runs (the "Basketball data" workflow's read_probe job prints it)
    if r:
        try:
            r.set("betiq:server:boot", json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                   "jobs": sorted(j.id for j in scheduler.get_jobs())}), ex=30 * 86400)
        except Exception as e:
            print(f"[Boot] couldn't record: {e}")


# Long jobs a redeploy waits for: while one runs it holds betiq:job:running:<id>,
# and deploy/vm/update.sh holds off (up to 30 min) until none are left
REDEPLOY_WAITS_FOR = {"refresh", "fd_referees", "bb_collect", "bb_fit", "tennis_collect", "table_tennis_collect",
                      "fbref_refresh", "matchday_sweep", "web_probe", "shot_blend", "results_refresh"}


def _job_marker(ev):
    if ev.job_id not in REDEPLOY_WAITS_FOR:
        return
    r = _get_redis()
    if r is None:
        return
    try:
        from apscheduler.events import EVENT_JOB_SUBMITTED
        key = f"betiq:job:running:{ev.job_id}"
        if ev.code == EVENT_JOB_SUBMITTED:
            r.set(key, datetime.now(timezone.utc).isoformat(timespec="seconds"), ex=3600)
        else:
            r.delete(key)
    except Exception:
        pass


@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown()
    r = _get_redis()
    if r:
        try:
            r.delete(*[f"betiq:job:running:{j}" for j in REDEPLOY_WAITS_FOR])
        except Exception:
            pass
    _flush_traffic()  # keep the last few minutes of page views


# ------------------------------------------------------------------ #
# Routes and jobs that live in routers/ and jobs/ (imported last: they
# read this module's state as `main.<name>` when they run)
# ------------------------------------------------------------------ #
from routers.matches import (  # noqa: E402
    _analysis_cached, _build_analysis, _build_results_index, _facts_for, _fetch_live_odds,
    _fixture_of, _last_backup, _match_news, _match_timing, _parse_api_h2h, _parse_csv_h2h,
    _recent_results_frame, _sanitize, _shared_model_status, _team_results,
)
from routers.matchday import (  # noqa: E402
    _backup_results, _date_param, _guarded_refresh, _guarded_tick, _learn_finished_matches,
    _legacy_view, _matchday_live, _matchday_sweep, _md_day_view, _md_load, _md_many, _md_save,
    _read_history, _record_job, _repredict, _snapshot_matchdays, _sportybet_scores, _unscored,
)
from routers.tickets import (  # noqa: E402
    _account_names, _all_tickets, _attach_live, _fs_result_for, _leg_results, _leg_sport,
    _md_entry_for, _other_sport_entry, _other_sport_live, _other_sport_md_result,
    _recheck_corrected_tickets, _record_ticket, _rescan_open_tickets, _settle_tickets,
    _sr_result_for, _take_code, _ticket_needs_settling,
)
from routers.push import _push_daily, _send_push_notifications, _valid_push_subscription  # noqa: E402
from routers.admin import _revenue_summary, _traffic_key_ok  # noqa: E402
from routers.user import _save_key, _set_saved, _ukey  # noqa: E402
from routers.sports import _check_sport, _check_sport_access, _sportybet_event_id  # noqa: E402
from routers.basketball import (  # noqa: E402
    _bb_fit_sync, _bb_index, _bb_index_set, _bb_live_guarded, _bb_load, _bb_results,
    _bb_save_status, _bb_slim, _bb_upcoming, _bbmd_find, _bbmd_load, _bbmd_merge, _bbmd_save,
)
from routers.racket import (  # noqa: E402
    _apply_live_reading, _bb_results_job, _bb_results_refresh, _fb_calibration_load, _live_probe,
    _live_stats_run, _live_stats_tick, _older_than, _overdue, _racket_collect, _racket_facts,
    _racket_index, _racket_index_build, _racket_results, _rk_day, _rk_live_tick,
    _rk_load_models_sync, _rk_match, _rk_refresh, _rk_result_for, _rk_results_days,
    _rk_results_refresh, _rk_slim, _rk_strip, _rk_upcoming, _rk_url, _rkmd_load, _rkmd_merge,
    _rkmd_save, _table_tennis_results_job, _tennis_results, _tennis_results_job,
    _with_football_stats, _with_live_stats, _with_racket_form, _with_tennis_form,
)
from routers.props import (  # noqa: E402
    _bb_add_props, _bb_prop_lines, _bb_result_for, _props_load_sync, _props_result_for,
)
from routers.logos import (  # noqa: E402
    _cache_competition_emblem, _cache_team_crest, _cache_team_crests,
    _competition_emblem_cache_key, _crest_index_refresh, _get_cached_team_crest, _get_fd_client,
    _get_football_competition_emblem, _has_cached_competition_emblem, _logo_answer,
    _team_crest_cache_key, _team_logo,
)
from routers.community import _leaderboard  # noqa: E402
from jobs.sportybet_links import (  # noqa: E402
    _catalog_summary, _kickoff_ms, _link_lock, _link_on_startup, _link_sportybet_events_now,
    _linked_event, _load_sb_fixtures, _market_coverage, _market_map, _match_predictions_to_events,
    _price_unpriced, _publish_sb_leagues, _publish_waiting_europe, _restore_link_status,
    _save_link_status, _sb_1x2, _sb_key, _sb_market_map, _with_priced_set_pieces,
)
from jobs.referees import (  # noqa: E402
    _af_referee_calls, _apply_referees, _appointments, _fd_refs_load, _football_data_referees,
    _referee_status, _set_piece_extras, _with_club_referees,
)
from jobs.shots import (  # noqa: E402
    _fit_shots, _index_intl_stats, _international_set_pieces, _intl_stats_for,
    _load_international_set_pieces, _refresh_shot_blend, _restore_shot_blend, _shot_markets,
)
from jobs.reviews import (  # noqa: E402
    _league_review_view, _market_names, _paused_leagues, _paused_markets, _restore_league_review,
    _restore_review, _review_view, _run_league_review, _run_review, _save_league_review,
    _save_review,
)
from routers.optimizer import (  # noqa: E402
    _bookable_markets, _cached_sport_predictions, _explain_empty, _live_pages_for, _live_states,
    _optimize_request, _optimizer_sports, _pick_key, _prediction_for_leg, _solve, _solve_checked,
    asdict_option,
)
from routers.daily import (  # noqa: E402
    _book_daily, _daily_load, _daily_public, _daily_save, _daily_slip, _daily_tick, _grade_daily,
    _recheck_daily_after_correction, _remake_cut, _sweep_daily, slip_reliability,
)
from jobs.social import (  # noqa: E402
    _channel, _maybe_post_daily, _post_alerts, _post_daily, _post_day, _post_enabled, _post_ready,
    _site_url, admin_post_channel,
)
from routers.config import _trial_config  # noqa: E402
from routers import (  # noqa: E402
    admin, basketball, community, config, daily, logos, matchday, matches, optimizer, props, push,
    racket, sports, tickets, user,
)
from jobs import reviews, social, sportybet_links  # noqa: E402

for _routes in (
    matches, matchday, tickets, push, admin, user, sports, basketball, racket, props, logos,
    community, sportybet_links, reviews, optimizer, daily, social, config,
):
    app.include_router(_routes.router)
