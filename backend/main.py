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
                  "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
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
            tip = _blend_1x2(tip, odds)
            if _note_unknown_clubs(model, fx):
                tip = {**tip, "thin_history": True}
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
    print(f"[Europe] {len(report['fixtures'])} Europa/Conference League fixtures ({report['sources']}, "
          f"read by {report.get('how')}), "
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
            _set_pieces = await asyncio.to_thread(lambda: set_pieces.SetPieceModel.fit(_with_club_referees(history)))
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
    request: Request,
    league: Optional[str] = None,
    date_str: Optional[str] = None,
    min_confidence: float = 0.0,
    limit: int = 500,
):
    """The upcoming football predictions, most confident first. Each variant is
    built once per predictions update (and per minute, as the window moves)
    and served with an ETag: a browser holding the same list gets a 304."""
    key = (id(_predictions_cache), _last_updated, (league or "").upper(), date_str or "", min_confidence, limit,
           int(time.time() // 60))
    hit = _predictions_response.get(key)
    if hit is None:
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
        body = json.dumps({"predictions": data[:limit], "total": len(data), "last_updated": _last_updated},
                          separators=(",", ":"), default=str).encode()
        hit = (body, '"' + hashlib.sha1(body).hexdigest()[:20] + '"')
        if len(_predictions_response) > 32:
            _predictions_response.clear()
        _predictions_response[key] = hit
    body, etag = hit
    headers = {"ETag": etag, "Cache-Control": "public, max-age=30, stale-while-revalidate=600"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


_predictions_response: Dict[Tuple, Tuple[bytes, str]] = {}


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
            # The live feed's stats too, so a team's averages include the match just played
            stats = {}
            for key, (hc, ac) in (("corners", ("HC", "AC")), ("bookings", ("HB", "AB")),
                                  ("shots", ("HS", "AS")), ("sot", ("HST", "AST"))):
                pair = res.get(key)
                if isinstance(pair, (list, tuple)) and len(pair) == 2 and None not in pair:
                    stats[hc], stats[ac] = pair
            rows.append({"Date": e.get("date") or d, "HomeTeam": canon(e.get("home", "")),
                         "AwayTeam": canon(e.get("away", "")), "FTHG": res["hg"], "FTAG": res["ag"],
                         "comp": e.get("league_name") or match_facts.comp_name(e.get("league")), **stats})
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
    return match_facts.facts(idx, h, a, day or None, stats_for=_intl_stats_for)


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
                             _access=Depends(require_feature("match_analysis"))):
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
        "shots_blend": _shot_blend,
        "learning": _learn_status,
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
MD_LIVE_MINUTES = 3                       # live scores: how often while matches are on
MD_SETTLE_MINUTES = 15                    # live runs settle tickets at least this often (or on a new score)
_md_last_settle: List[float] = [0.0]
_md_status: Dict[str, Any] = {"at": None, "trigger": None, "report": None,
                              "started": None, "stage": None, "error": None, "failed_at": None}
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
    sb_ids: Dict[str, Dict[str, str]] = {}   # date -> match key -> SportyBet event id (its stats come by it)
    for p in _predictions_cache:
        if p.get("sport") in (None, "football") and p.get("date"):
            ev = _linked_event(p)
            try:
                priced = price_book.prices(p, ev)
            except Exception:
                priced = None
            by_date.setdefault(p["date"], []).append({**p, "_sb_prices": priced} if priced else p)
            if ev and ev.get("eventId") and p.get("home") and p.get("away"):
                sb_ids.setdefault(p["date"], {})[matchday.key(p["home"], p["away"])] = str(ev["eventId"])
    now = datetime.now(timezone.utc)
    written, failed = 0, []
    for d, preds in sorted(by_date.items()):
        # Each date on its own: one failed write mustn't leave the later dates unsaved
        try:
            day = _md_load(r, d)
            changed = matchday.merge_predictions(day, preds, now)
            for k, eid in (sb_ids.get(d) or {}).items():
                if k in day and not day[k].get("sb_id"):
                    day[k]["sb_id"] = eid
                    changed = True
            if changed:
                _md_save(r, d, day)
                written += 1
        except Exception as e:
            failed.append(f"{d}: {type(e).__name__}: {e}"[:200])
    if failed:
        print(f"[MatchDay] snapshot failed for {len(failed)} date(s): {failed[:3]}")
        _record_job("football_snapshot", {"written": written, "failed": failed[:10]})
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


LIVE_JOBS_KEY = "betiq:live:last"


def _record_job(name: str, report: Any) -> None:
    """A live job's last run (time, and its report or error) in Redis, for
    the probe and the admin page: these jobs otherwise fail silently."""
    r = _get_redis()
    if not r:
        return
    try:
        r.hset(LIVE_JOBS_KEY, name, json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                "report": report}, default=str)[:4000])
        r.expire(LIVE_JOBS_KEY, 7 * 86400)
    except Exception:
        pass


async def _guarded_tick(name: str, run, limit: float) -> Dict[str, Any]:
    """A live job that can't hang: given up after `limit` seconds, so the next
    minute's run isn't skipped behind a stuck request forever (the jobs allow
    one run at a time), and its outcome recorded either way."""
    try:
        report = await asyncio.wait_for(run(), limit)
    except asyncio.TimeoutError:
        report = {"error": f"gave up after {limit:.0f}s"}
        print(f"[Live] {name}: gave up after {limit:.0f}s")
    except Exception as e:
        report = {"error": f"{type(e).__name__}: {e}"[:300]}
        print(f"[Live] {name} failed: {report['error']}")
    await asyncio.to_thread(_record_job, name, report)
    return report


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
    need = {d: {k: e for k, e in day.items() if matchday.needs_result(e, now)} for d, day in days.items()}
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
                    if matchday.apply_result(day[k], res):
                        changed = True
                        report["updated"] += 1
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


async def _sportybet_scores(stale: Dict[str, Dict], now: datetime) -> Tuple[List[Dict], Dict[str, Any]]:
    """Live scores and finals from SportyBet for the matches ESPN doesn't
    score: its live list (one request) each run, and the results of their
    days at most every SB_RESULTS_EVERY."""
    import results_feed
    out = await results_feed.fetch_sportybet_live()
    report: Dict[str, Any] = {"live_events": len(out)}
    days = sorted({e["date"] for e in stale.values() if e.get("date")})
    for d in days:
        if time.time() - _sb_results_at.get(d, 0) < SB_RESULTS_EVERY:
            continue
        _sb_results_at[d] = time.time()
        got = await results_feed.fetch_sportybet_results(d)
        report[f"results {d}"] = len(got)
        out += got
    return out, report


AF_LIVE_EVERY = 15 * 60   # seconds between backup requests
AF_LIVE_DAILY_CAP = 24     # of the key's 100 a day (referees 12, nightly stats collector 60)
_af_live_last = [0.0]


def _unscored(need: Dict[str, Dict[str, Dict]], espn: List[Dict], now: datetime) -> Dict[str, Dict]:
    """{entry key: entry} for matches kicked off 5+ minutes ago that ESPN
    gives no live or final score for (and that we don't have final)."""
    import matchday
    out = {}
    for entries in need.values():
        got = dict(matchday.match_results(entries, espn))
        for k, e in entries.items():
            ko = matchday.kickoff(e)
            if not ko or ko > now - timedelta(minutes=5):
                continue
            if (e.get("result") or {}).get("status") in ("finished", "postponed"):
                continue
            if (got.get(k) or {}).get("status") in ("live", "finished", "postponed"):
                continue
            out[k] = e
    return out


async def _backup_results(r, stale: Dict[str, Dict]) -> Tuple[List[Dict], Dict[str, Any]]:
    """API-Football results for the days of `stale` matches: at most every
    AF_LIVE_EVERY and AF_LIVE_DAILY_CAP requests a day."""
    import results_feed
    key = os.getenv("APIFOOTBALL_KEY", "").strip()
    if not key:
        return [], {"skipped": "set APIFOOTBALL_KEY to score matches ESPN doesn't"}
    if time.time() - _af_live_last[0] < AF_LIVE_EVERY:
        return [], {"skipped": "asked recently", "unscored": len(stale)}
    count_key = f"betiq:af:live:{date.today().isoformat()}"
    try:
        used = int(r.get(count_key) or 0)
    except Exception:
        used = 0
    days = sorted({e["date"] for e in stale.values() if e.get("date")})
    if used + len(days) > AF_LIVE_DAILY_CAP:
        return [], {"skipped": f"used today's {AF_LIVE_DAILY_CAP} requests", "unscored": len(stale)}
    _af_live_last[0] = time.time()
    out: List[Dict] = []
    report: Dict[str, Any] = {"requests": 0, "errors": [], "unscored": len(stale)}
    async with httpx.AsyncClient(timeout=20) as client:
        for d in days:
            got, err = await results_feed.fetch_api_football(client, date.fromisoformat(d), key)
            report["requests"] += 1
            if err:
                report["errors"].append(f"{d}: {err}")
            out += got
    try:
        r.incrby(count_key, report["requests"])
        r.expire(count_key, 2 * 86400)
    except Exception:
        pass
    report["results"] = len(out)
    return out, report


MD_RUN_LIMIT = {"live": 170, "sweep": 900}   # seconds before a run is given up (live: under its 3 minutes)


async def _guarded_refresh(days_back: int, trigger: str) -> None:
    """A results run that can't hang: given up after MD_RUN_LIMIT, so the
    next scheduled run isn't skipped behind it forever."""
    try:
        await asyncio.wait_for(_refresh_matchdays(days_back, trigger), MD_RUN_LIMIT.get(trigger, 600))
    except asyncio.TimeoutError:
        print(f"[MatchDay] {trigger} run gave up after {MD_RUN_LIMIT.get(trigger)}s at: {_md_status.get('stage')}")
        _md_status.update(failed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          error=f"timed out at {_md_status.get('stage')}")
    except Exception as e:
        print(f"[MatchDay] {trigger} run failed at {_md_status.get('stage')}: {type(e).__name__}: {e}")
        _md_status.update(failed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          error=f"{type(e).__name__} at {_md_status.get('stage')}")


LEARN_DAYS = 10           # finished matches looked at after a model is (re)built
_learn_status: Dict[str, Any] = {"at": None, "matches": 0, "total": 0, "last": [], "repredicted": 0}


def _learn_finished_matches(days: int = 1) -> Optional[Dict[str, Any]]:
    """Feed every finished match of the last `days` days (and today) that the
    models don't hold yet into them, then redo the upcoming predictions of the
    teams that played. Returns {matches, teams, repredicted} or None when
    nothing was new."""
    import live_learning
    if _predictor is None:
        return None
    r = _get_redis()
    if not r:
        return None
    today = date.today()
    stored = _md_many(r, [(today - timedelta(days=o)).isoformat() for o in range(days, -1, -1)])
    moved: set = set()
    learned = []
    for day in stored.values():
        for e in (day or {}).values():
            row = live_learning.row_from_entry(e)
            if not row:
                continue
            international = intl.is_international(row["league"])
            got = live_learning.learn(row, predictor=_predictor, set_pieces=_set_pieces, shots=_shots,
                                      international=international,
                                      competition=intl.LEAGUE_CODE if international else row["league"])
            if got:
                moved |= got
                learned.append(f"{row['HomeTeam']} {row['FTHG']}-{row['FTAG']} {row['AwayTeam']}")
    if not learned:
        return None
    redone = _repredict(moved)
    _learn_status.update(at=datetime.now(timezone.utc).isoformat(timespec="seconds"), matches=len(learned),
                         total=_learn_status["total"] + len(learned), last=(learned + _learn_status["last"])[:20],
                         repredicted=redone)
    print(f"[Learn] {len(learned)} finished match(es) learned; {redone} upcoming prediction(s) redone: "
          + "; ".join(learned[:5]))
    return {"matches": len(learned), "teams": sorted(moved), "repredicted": redone}


def _repredict(teams: set) -> int:
    """Redo the not-yet-started predictions involving `teams` (model names)
    with the models as they now stand, keeping their odds; returns how many."""
    import live_learning
    global _predictions_cache
    if not teams or _predictor is None or not _predictions_cache:
        return 0
    now = datetime.now(timezone.utc)
    unknown = dict(_unknown_clubs)           # _build_predictions starts that list afresh
    out, redone = [], 0
    try:
        for p in _predictions_cache:
            if (p.get("sport") in (None, "football") and not live_learning.kicked_off(p, now)
                    and (_predictor.canon(p.get("home", "")) in teams or _predictor.canon(p.get("away", "")) in teams)):
                odds = {"1": p.get("odds_home") or 0, "X": p.get("odds_draw") or 0, "2": p.get("odds_away") or 0}
                new = _build_predictions(_predictor, [p], {f"{p['home']}:{p['away']}:{p.get('date', '')}": odds})
                if new:
                    out.append({**{k: v for k, v in p.items() if k != "thin_history"}, **new[0]})
                    redone += 1
                    continue
            out.append(p)
    finally:
        _unknown_clubs.update(unknown)
    if redone:
        _predictions_cache = out
        _save_predictions_cache()
    return redone


async def _matchday_live() -> None:
    await _guarded_refresh(1, "live")
    await asyncio.to_thread(_record_job, "football_live",
                            {k: _md_status.get(k) for k in ("at", "stage", "error", "failed_at", "report")})
    # Matches that just finished: into the models, and their teams' next predictions redone
    try:
        await asyncio.to_thread(_learn_finished_matches, 1)
    except Exception as e:
        print(f"[Learn] failed: {e}")


async def _matchday_sweep() -> None:
    await _guarded_refresh(MD_DAYS_BACK, "sweep")


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
    await asyncio.to_thread(_with_football_stats, d.isoformat(), day, matches)
    return {"date": d.isoformat(), "today": today.isoformat(), "matches": matches,
            "summary": matchday.day_summary(day.values()),
            "updated": _md_status.get("at"),
            "check": {**{k: _md_status.get(k) for k in ("started", "stage", "error", "failed_at")},
                      "backup": ((_md_status.get("report") or {}).get("backup"))}}


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
        stored = await asyncio.to_thread(_md_many, r, dates) if r else {}
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


_sport_accuracy_cache: Dict[Tuple[str, int], Tuple[float, Any]] = {}


@app.get("/api/accuracy/{sport}")
async def get_sport_accuracy(sport: str, request: Request, days: int = 30):
    """The track record of basketball, tennis or table tennis over the last
    `days` days, in football's shape (sport_accuracy.py): our tips, the
    total line, our best line; calibration; the winner Brier score against
    the bookmaker's; by competition and by day."""
    import basketball_matchday as bbmd
    import racket_matchday as rmd
    import sport_accuracy
    sport = "table_tennis" if sport == "table-tennis" else sport
    if sport not in ("basketball", "tennis", "table_tennis"):
        raise HTTPException(status_code=404, detail="Unknown sport")
    await _check_sport_access(request, "basketball" if sport == "basketball" else _rk_url(sport))
    days = max(1, min(int(days), 90))
    hit = _sport_accuracy_cache.get((sport, days))
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    r = _get_redis()
    empty = {"days": days, "matches": 0, "markets": {}, "calibration": [], "brier": {}, "leagues": [], "daily": []}
    if not r:
        return empty
    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(days, -1, -1)]
    keys = [bbmd.KEY.format(d) if sport == "basketball" else rmd.key(sport, d) for d in dates]

    def load() -> Dict[str, List[Dict]]:
        out: Dict[str, List[Dict]] = {}
        for d, raw in zip(dates, r.mget(keys)):
            try:
                out[d] = list(json.loads(raw).values()) if raw else []
            except Exception:
                out[d] = []
        return out
    res = {"days": days, "sport": sport, **sport_accuracy.accuracy(await asyncio.to_thread(load), sport)}
    _sport_accuracy_cache[(sport, days)] = (time.time(), res)
    return res


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
TICKET_SOURCES = {"slip", "optimizer", "code_check", "chat", "match", "daily", "other"}


ANON_UID = "anonymous"      # codes made by visitors who aren't signed in (admin sees them all)
ANON_MAX_TICKETS = 1000


def _record_ticket(uid: str, ticket: Dict[str, Any]) -> None:
    import tickets
    r = _get_redis()
    if not r or not ticket.get("legs"):
        return
    key = _ukey(uid, "tickets")
    raw = r.get(key)
    items: List[Dict] = [t for t in (json.loads(raw) if raw else []) if t.get("code") != ticket["code"]]
    items.insert(0, ticket)
    cap = ANON_MAX_TICKETS if uid == ANON_UID else tickets.MAX_TICKETS
    r.set(key, json.dumps(items[:cap], separators=(",", ":")), ex=365 * 86400)
    r.sadd(TICKETS_OPEN_KEY, uid)
    r.hincrby(TICKETS_STATS_KEY, "created", 1)
    r.hincrby(TICKETS_STATS_KEY, f"source:{ticket.get('source') or 'other'}", 1)
    r.incr(f"betiq:tickets:day:{date.today().isoformat()}")
    r.expire(f"betiq:tickets:day:{date.today().isoformat()}", 90 * 86400)


def _md_entry_for(r, leg: Dict, days: Dict[str, Dict]) -> Optional[Dict]:
    """A ticket leg's match in the match-day store (its date, or a day either
    side for kick-offs near midnight); `days` caches the loaded days."""
    import matchday
    k = matchday.key(leg.get("home", ""), leg.get("away", ""))
    try:
        d0 = date.fromisoformat(leg.get("date") or "")
    except ValueError:
        return None
    today = date.today()
    for d in (d0, d0 - timedelta(days=1), d0 + timedelta(days=1)):
        ds = d.isoformat()
        if ds not in days:
            days[ds] = _md_load(r, ds) if d <= today else {}
        e = days[ds].get(k)
        if e:
            return e
    return None


def _leg_results(r, days: Dict[str, Dict]):
    """result_for(leg) for grading a ticket leg or daily pick: player props
    from the box scores, basketball by its SportyBet event, football from
    the match-day store; tennis and table tennis by their SportyBet event."""
    bb_days: Dict[str, Dict] = {}
    rk_days: Dict[Tuple[str, str], Dict] = {}
    md_days: Dict[Tuple[str, str], Dict] = {}

    def result_for(leg: Dict) -> Optional[Dict]:
        m = str(leg.get("market") or "")
        if m.startswith("bb_player_") or m == "anytime_scorer":
            return _props_result_for(leg)
        # Basketball, tennis, table tennis: SportyBet's results feed, else the
        # match-day store's final (some matches never reach the feed)
        if m.startswith("bb_"):
            if not leg.get("event_id") and leg.get("sb"):   # a daily pick: its SportyBet ids
                leg = {**leg, "event_id": leg["sb"].get("eventId")}
            return _bb_result_for(r, leg, bb_days) or _other_sport_md_result(r, leg, "basketball", md_days)
        if m.startswith("rk_"):
            return _rk_result_for(r, leg, rk_days) or _other_sport_md_result(r, leg, _leg_sport(leg), md_days)
        e = _md_entry_for(r, leg, days)
        return e.get("result") if e else None
    return result_for


SETTLE_LEGS_DAYS = 7          # a settled ticket's other legs are still graded this long after its last match
TICKETS_RESCAN_HOURS = 6      # how often every account's tickets are looked through for legs left pending
_tickets_rescan_at = [0.0]


def _ticket_needs_settling(t: Dict, today: date) -> bool:
    """A ticket still being played, or a settled one (a lost leg settles it)
    whose other legs aren't graded yet, up to SETTLE_LEGS_DAYS after its last match."""
    legs = t.get("legs") or []
    last = max((l.get("date") or "" for l in legs), default="")
    recent = last >= (today - timedelta(days=SETTLE_LEGS_DAYS)).isoformat()
    if t.get("status") == "pending":
        return True
    if t.get("status") == "open":
        return last >= (today - timedelta(days=3)).isoformat()
    return recent and any(l.get("status") in (None, "pending") for l in legs)


def _rescan_open_tickets(r, today: date) -> int:
    """Every account with a ticket that still needs settling, back in the
    open set (accounts can drop out of it; this puts them back)."""
    added = 0
    for key in r.scan_iter(match="betiq:user:*:tickets", count=500):
        key = key.decode() if isinstance(key, bytes) else key
        try:
            items = json.loads(r.get(key) or "[]")
        except ValueError:
            continue
        if any(_ticket_needs_settling(t, today) for t in items if isinstance(t, dict)):
            added += int(r.sadd(TICKETS_OPEN_KEY, key[len("betiq:user:"):-len(":tickets")]) or 0)
    return added


def _settle_tickets(r) -> Dict[str, int]:
    """Grade the legs of every ticket still being played, and the legs left
    pending on settled ones (_ticket_needs_settling), whose matches have finished."""
    import tickets
    today = date.today()
    report: Dict[str, Any] = {"settled": 0, "legs": 0}
    if time.time() - _tickets_rescan_at[0] > TICKETS_RESCAN_HOURS * 3600:
        _tickets_rescan_at[0] = time.time()
        report["rescan_added"] = _rescan_open_tickets(r, today)
    uids = [u.decode() if isinstance(u, bytes) else u for u in (r.smembers(TICKETS_OPEN_KEY) or [])]
    report["accounts"] = len(uids)
    days: Dict[str, Dict] = {}
    result_for = _leg_results(r, days)

    for uid in uids:
        try:
            key = _ukey(uid, "tickets")
            raw = r.get(key)
            items: List[Dict] = json.loads(raw) if raw else []
            changed = False
            for t in items:
                if not _ticket_needs_settling(t, today):
                    continue
                was = t.get("status")
                before = sum(1 for l in t.get("legs") or [] if l.get("status") != "pending")
                if tickets.settle(t, result_for):
                    changed = True
                    report["legs"] += sum(1 for l in t.get("legs") or [] if l.get("status") != "pending") - before
                    if t["status"] != was and t["status"] in ("won", "lost", "void"):
                        report["settled"] += 1
                        r.hincrby(TICKETS_STATS_KEY, t["status"], 1)
            if changed:
                r.set(key, json.dumps(items, separators=(",", ":")), ex=365 * 86400)
            # Nothing left to settle (or only legs we can't settle, all played)
            if not any(_ticket_needs_settling(t, today) for t in items):
                r.srem(TICKETS_OPEN_KEY, uid)
        except Exception as e:   # one account's tickets can't stop the others'
            report.setdefault("errors", []).append(f"{uid[:8]}…: {e}"[:200])
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


_all_tickets_cache: List[Any] = [0.0, None]
_names_cache: Dict[str, Tuple[float, Dict[str, str]]] = {}


def _all_tickets(r) -> List[Dict[str, Any]]:
    """Every account's tickets, newest first, each with its account id
    (cached a minute: it reads every account that has made a code)."""
    if _all_tickets_cache[1] is not None and time.time() - _all_tickets_cache[0] < 60:
        return _all_tickets_cache[1]
    keys = [k.decode() if isinstance(k, bytes) else k for k in r.scan_iter(match="betiq:user:*:tickets", count=500)]
    out: List[Dict[str, Any]] = []
    for i in range(0, len(keys), 100):
        chunk = keys[i:i + 100]
        for key, raw in zip(chunk, r.mget(chunk)):
            uid = key[len("betiq:user:"):-len(":tickets")]
            try:
                items = json.loads(raw) if raw else []
            except ValueError:
                continue
            out += [{**t, "uid": uid} for t in items if isinstance(t, dict)]
    out.sort(key=lambda t: t.get("created_at") or "", reverse=True)
    _all_tickets_cache[:] = [time.time(), out]
    return out


async def _account_names(uids: List[str]) -> Dict[str, Dict[str, str]]:
    """Name and email per account, from Clerk (kept 10 minutes)."""
    import auth
    now = time.time()
    need = [u for u in set(uids) if u not in _names_cache or now - _names_cache[u][0] > 600]
    if need and auth.CLERK_SECRET_KEY:
        try:
            found = await auth.clerk_names(need)
        except Exception as e:
            print(f"[Tickets] couldn't read names from Clerk: {e}")
            found = {}
        for u in need:
            if u in found:
                _names_cache[u] = (now, found[u])
    return {u: _names_cache[u][1] for u in uids if u in _names_cache}


@app.get("/api/admin/tickets/all")
async def admin_all_tickets(limit: int = 200, status: str = "", source: str = "", q: str = "",
                            _admin: str = Depends(require_admin)):
    """Every booking code made on the site, newest first, with the account
    that made it (name, email) and its legs. Filters: status
    (open|won|lost|void), source, q (code, name or email)."""
    r = _get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="The database isn't connected")
    limit = max(1, min(int(limit), 1000))
    items = await asyncio.to_thread(_all_tickets, r)
    if status:
        items = [t for t in items if t.get("status") == status]
    if source:
        items = [t for t in items if (t.get("source") or "other") == source]
    names = await _account_names([t["uid"] for t in items if t["uid"] != ANON_UID])
    if q:
        ql = q.strip().lower()
        items = [t for t in items if ql in (t.get("code") or "").lower()
                 or ql in (names.get(t["uid"], {}).get("name") or "").lower()
                 or ql in (names.get(t["uid"], {}).get("email") or "").lower()]
    page = items[:limit]
    return {"total": len(items), "tickets": [
        {**{k: t.get(k) for k in ("code", "created_at", "source", "share_url", "status", "total_odds", "settled_at", "legs")},
         "uid": t["uid"], "name": "Not signed in" if t["uid"] == ANON_UID else names.get(t["uid"], {}).get("name") or "",
         "email": names.get(t["uid"], {}).get("email") or ""}
        for t in page]}


TICKET_LIVE_DAYS = 14   # settled tickets keep their legs' scores and stats this long


def _leg_sport(leg: Dict) -> str:
    """football, basketball or a racket sport ("tennis", "table_tennis", or
    "racket" when the leg doesn't say which)."""
    m = str(leg.get("market") or "")
    if m.startswith("bb_") and not m.startswith("bb_player_"):
        return "basketball"
    if m.startswith("rk_"):
        sp = leg.get("sport")
        return sp if sp in ("tennis", "table_tennis") else "racket"
    return "football"


def _other_sport_entry(r, leg: Dict, sport: str,
                       days: Dict[Tuple[str, str], Dict]) -> Optional[Tuple[str, str, Dict]]:
    """A basketball, tennis or table tennis leg's match-day entry, by the
    SportyBet event it was booked on: (sport, date, entry), or None."""
    eid = str(leg.get("event_id") or (leg.get("sb") or {}).get("eventId") or "")
    try:
        d0 = date.fromisoformat(leg.get("date") or "")
    except ValueError:
        return None
    if not eid:
        return None
    if sport == "basketball":
        sports = ["basketball"]
    else:   # the leg's racket sport first, then the other (a leg may not say which)
        sports = sorted(("tennis", "table_tennis"), key=lambda x: x != sport)
    for sp in sports:
        for d in (d0, d0 - timedelta(days=1), d0 + timedelta(days=1)):
            ds = d.isoformat()
            if (sp, ds) not in days:
                days[(sp, ds)] = (_bbmd_load(r, ds) if sp == "basketball" else _rkmd_load(r, sp, ds)) \
                    if d <= date.today() else {}
            e = days[(sp, ds)].get(eid)
            if e:
                return sp, ds, e
    return None


def _other_sport_md_result(r, leg: Dict, sport: str, days: Dict[Tuple[str, str], Dict]) -> Optional[Dict]:
    """A finished basketball/racket leg's result from its match-day entry, in
    the settle format: for matches SportyBet's results feed never had."""
    import basketball_matchday as bbmd
    import racket_matchday as rmd
    found = _other_sport_entry(r, leg, sport, days)
    if not found:
        return None
    sp, _, e = found
    res = e.get("result") or {}
    if res.get("status") != "finished" or not res.get("score"):
        return None
    return bbmd._settle_result(res) if sp == "basketball" else rmd._settled(res)


def _other_sport_live(r, leg: Dict, sport: str, days: Dict[Tuple[str, str], Dict],
                      stats: Dict[Tuple[str, str], Dict]) -> Optional[Dict]:
    """A basketball, tennis or table tennis leg's match as it stands, by the
    SportyBet event it was booked on: score, the period/set scores and the
    live stats rows (live_stats.py)."""
    import basketball_matchday as bbmd
    import live_stats as ls
    import racket_matchday as rmd
    found = _other_sport_entry(r, leg, sport, days)
    if not found:
        return None
    sp, ds, e = found
    eid = e.get("id") or str(leg.get("event_id") or (leg.get("sb") or {}).get("eventId") or "")
    m = (bbmd if sp == "basketball" else rmd).public(e)
    if m.get("status") not in ("live", "finished"):
        return None
    if (sp, ds) not in stats:
        stats[(sp, ds)] = ls.load(r, sp, ds)
    rows = (stats[(sp, ds)].get(eid) or {}).get("rows")
    if not rows and not ls.sr_number(eid):
        rows = ls.from_score(m.get("periods"), sp)
    return {"status": m["status"], "minute": m.get("minute"), "score": m.get("score"),
            "aet": bool(m.get("aet")), "stats": None, "events": None, "sport": sp,
            "periods": m.get("periods"), "rows": rows or None}


def _attach_live(r, items: List[Dict]) -> None:
    """Each leg's match as it stands (score, minute, live stats; the final
    score and stats once it's over) and, in play, whether the pick would win
    if it ended now. Open tickets, and settled ones for TICKET_LIVE_DAYS.
    For the response only: nothing is saved."""
    import matchday
    import tickets
    days: Dict[str, Dict] = {}
    other_days: Dict[Tuple[str, str], Dict] = {}
    other_stats: Dict[Tuple[str, str], Dict] = {}
    oldest = (date.today() - timedelta(days=TICKET_LIVE_DAYS)).isoformat()
    for t in items:
        open_ = t.get("status") in ("pending", "open")
        for leg in t.get("legs") or []:
            if not open_ and (leg.get("date") or "") < oldest:
                continue
            sport = _leg_sport(leg)
            if sport != "football":
                try:
                    live = _other_sport_live(r, leg, sport, other_days, other_stats)
                except Exception:
                    live = None
                if live:
                    leg["live"] = live
                continue
            try:
                e = _md_entry_for(r, leg, days)
            except Exception:
                e = None
            m = matchday.public(e) if e else None
            if not m or m.get("status") not in ("live", "finished"):
                continue
            live = {k: m.get(k) for k in ("status", "minute", "score", "aet", "stats", "events")}
            res = (e or {}).get("result") or {}
            if m["status"] == "live" and leg.get("market") != "sportybet" and res.get("hg") is not None:
                now = tickets.grade_leg(leg.get("market", ""), leg.get("code", ""), {**res, "status": "finished", "aet": False})
                live["as_it_stands"] = now if now in ("won", "lost", "push", "half_won", "half_lost", "void") else None
            leg["live"] = live


@app.get("/api/user/tickets")
async def get_tickets(request: Request, uid: str = ""):
    """The account's booking codes, each leg settled from the result, plus
    codes saved before tracking (no legs to settle)."""
    import tickets
    uid = await require_user(request, uid)
    r = _get_redis()
    if not r:
        return {"tickets": [], "summary": tickets.summary([]), "older": []}
    def build() -> Dict[str, Any]:
        raw = r.get(_ukey(uid, "tickets"))
        items: List[Dict] = json.loads(raw) if raw else []
        _attach_live(r, items)
        tracked = {t.get("code") for t in items}
        old_raw = r.get(_ukey(uid, "codes"))
        older = [c for c in (json.loads(old_raw) if old_raw else []) if c.get("code") not in tracked]
        return {"tickets": items, "summary": tickets.summary(items), "older": older[:50]}
    return await asyncio.to_thread(build)


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
                        _access=Depends(require_feature("ai_preview"))):
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
    keys = await _clerk_keys() if auth.premium_enforced() else {"ok": False, "reason": ""}
    checks = [
        {"id": "clerk_issuer", "ok": auth.auth_enforced(), "label": "Signed-in users verified (CLERK_ISSUER)",
         "fix": "Set CLERK_ISSUER in the server's .env: without it, user data endpoints trust the uid the browser sends."},
        {"id": "premium", "ok": auth.premium_enforced() and keys["ok"], "label": "Paywall enforced on the server (CLERK_SECRET_KEY)",
         "fix": keys["reason"] or "Set CLERK_SECRET_KEY (Clerk dashboard → API keys → Secret key) in the server's .env so premium analysis can't be fetched directly."},
        {"id": "admin_ids", "ok": bool(ADMIN_USER_IDS), "label": "Admins sign in with Clerk (ADMIN_USER_IDS)",
         "fix": "Add your Clerk user id to ADMIN_USER_IDS in the server's .env and on Vercel, then you rarely need the secret."},
        {"id": "admin_secret", "ok": len(ADMIN_SECRET) >= 24, "label": "Admin secret is long (24+ characters)",
         "fix": "Use a long random ADMIN_SECRET (e.g. `openssl rand -hex 24`), the same value in the server's .env and on Vercel."},
        {"id": "traffic_key", "ok": bool(os.getenv("TRAFFIC_KEY")), "label": "Separate traffic key (TRAFFIC_KEY)",
         "fix": "Optional: set TRAFFIC_KEY, the same value in the server's .env and on Vercel, so page-view reporting doesn't reuse the admin secret."},
        {"id": "redis", "ok": _get_redis() is not None, "label": "Redis connected",
         "fix": "Set UPSTASH_REDIS_URL in the server's .env."},
        {"id": "rate_limits", "ok": os.getenv("RATE_LIMITS", "1") != "0", "label": "Rate limits on",
         "fix": "Remove RATE_LIMITS=0 from the server's .env."},
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


@app.get("/api/user/codes")
async def get_codes(request: Request, uid: str = ""):
    uid = await require_user(request, uid)
    r = _get_redis()
    if not r: return []
    raw = r.get(_ukey(uid, "codes"))
    return json.loads(raw) if raw else []


@app.get("/api/user/stats")
async def get_user_stats(request: Request, uid: str = ""):
    """The account's record from the codes it booked here, settled by the
    server from the results (never self-reported), and what it has saved."""
    import tickets
    uid = await require_user(request, uid)
    r = _get_redis()
    if not r: return {}
    tickets_raw = r.get(_ukey(uid, "tickets"))
    saves_raw = r.get(_ukey(uid, "saves"))
    codes_raw = r.get(_ukey(uid, "codes"))
    return {
        "tickets": tickets.summary(json.loads(tickets_raw) if tickets_raw else []),
        "saved_count": len(json.loads(saves_raw) if saves_raw else []),
        "codes_count": len(json.loads(codes_raw) if codes_raw else []),
    }


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


async def _check_sport_access(request: Request, sport: str) -> None:
    """Sports other than football can be switched off or kept for testers."""
    import access
    if sport in access.SPORT_FEATURES:
        await check_feature(request, access.SPORT_FEATURES[sport])


@app.get("/api/sports/{sport}/leagues")
async def get_sport_leagues(sport: str, request: Request):
    """Return distinct leagues/tournaments being predicted for a sport."""
    _check_sport(sport)
    await _check_sport_access(request, sport)
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
async def get_sport_predictions(sport: str, request: Request):
    """
    Multi-sport predictions endpoint.
    sport: basketball | tennis | table-tennis
    Requires ODDS_API_KEY env var.
    """
    _check_sport(sport)
    await _check_sport_access(request, sport)
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
            cached = await asyncio.to_thread(r.get, cache_key)
            if cached:
                out = drop_started_events(_json.loads(cached))
                return _with_racket_form(out, "table_tennis" if sport == "table-tennis" else sport) if sport in ("tennis", "table-tennis") else out
        except Exception:
            pass
    else:
        # No Redis (or it's briefly down) — fall back to an in-process cache
        # so this endpoint still doesn't hit The Odds API on every request.
        # Without this, any Redis outage turns every page view into a fresh
        # API call, burning the free 500/month quota within hours.
        cached_entry = _sports_memory_cache.get(sport)
        if cached_entry and (_time.monotonic() - cached_entry[1]) < CACHE_TTL:
            out = drop_started_events(cached_entry[0])
            return _with_racket_form(out, "table_tennis" if sport == "table-tennis" else sport) if sport in ("tennis", "table-tennis") else out

    if sport == "basketball":
        data = [_bb_slim(p) for p in (_bb_upcoming() or await _bb_refresh())]
        if not data:
            data = await fetch_basketball_predictions()
    elif sport in ("tennis", "table-tennis"):
        rk = "table_tennis" if sport == "table-tennis" else "tennis"
        # Every SportyBet line priced (racket_predictions); the old path if the listing is down
        data = [_rk_slim(p) for p in (_rk_upcoming(rk) or await _rk_refresh(rk))]
        if not data:
            data = await (fetch_tennis_predictions() if rk == "tennis" else fetch_table_tennis_predictions())
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

    out = drop_started_events(data)
    return _with_racket_form(out, "table_tennis" if sport == "table-tennis" else sport) if sport in ("tennis", "table-tennis") else out


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


def _bb_save_status(**kw) -> None:
    _bb_status.update(kw, at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    r = _get_redis()
    if r:
        try:
            r.set(BB_STATUS_KEY, json.dumps(_bb_status, default=str))
        except Exception:
            pass


def _bb_results(r, days: Optional[Iterable[str]] = None) -> List[Dict]:
    """Stored results: every day's, or the given days'."""
    import basketball_data as bd
    if not r:
        return []
    try:
        if days is None:
            raw = r.hgetall(bd.RESULTS_KEY) or {}
            blobs = list(raw.values())
        else:
            blobs = r.hmget(bd.RESULTS_KEY, list(days)) or []
    except Exception as e:
        print(f"[Basketball] couldn't read results: {e}")
        return []
    out: List[Dict] = []
    for b in blobs:
        try:
            out += bd.decode(b)
        except Exception:
            continue
    return out


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


def _bb_index_set(results: List[Dict]) -> None:
    import basketball_facts as bf
    idx = bf.build_index(results)
    _bb_team_games.clear()
    _bb_team_games.update(idx)


async def _bb_index() -> Dict[str, List]:
    """The team index; built from the stored results the first time it's asked for."""
    if not _bb_team_games:
        r = _get_redis()
        if r:
            await asyncio.to_thread(lambda: _bb_index_set(_bb_results(r)))
    return _bb_team_games


def _bb_fit_sync() -> Dict[str, Any]:
    import basketball_data as bd
    r = _get_redis()
    results = _bb_results(r)
    try:
        backtest = json.loads(r.get("betiq:bb:backtest") or "null") if r else None   # backtest_basketball.py
    except Exception:
        backtest = None
    # Elo (basketball_elo.py) over the long history (bb_history.py), weighted
    # in each league as the Elo check found best (elo_check_basketball.py)
    elo_report, history = None, None
    try:
        import bb_history
        import elo_check_basketball as ec
        elo_report = json.loads(r.get(ec.REPORT_KEY) or "null") if r else None
        history = bb_history.combine(results, bb_history.load(r)) if r and elo_report else None
    except Exception as e:
        print(f"[Basketball] Elo not used: {e}")
    leagues = bd.fit_all(results, backtest=backtest, elo_report=elo_report, history=history)
    _bb_leagues.clear()
    _bb_leagues.update(leagues)
    _bb_index_set(results)
    if r and leagues:
        r.set(BB_MODEL_KEY, bd.encode([lg.to_json() for lg in leagues.values()]))
    return {"games": len(results), "leagues": len(leagues),
            "teams": sum(len(lg.attack) for lg in leagues.values())}


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


def _bb_load() -> None:
    """At startup: the rated leagues and the last predictions, from Redis."""
    import basketball_data as bd
    import basketball_model as bm
    r = _get_redis()
    if not r:
        return
    try:
        for d in bd.decode(r.get(BB_MODEL_KEY)):
            lg = bm.League.from_json(d)
            _bb_leagues[lg.name] = lg
        _bb_predictions[:] = bd.decode(r.get(BB_PREDICTIONS_KEY))
        _bb_status.update(json.loads(r.get(BB_STATUS_KEY) or "{}"))
    except Exception as e:
        print(f"[Basketball] couldn't load: {e}")
    print(f"[Basketball] loaded {len(_bb_leagues)} rated leagues, {len(_bb_predictions)} predictions")


def _bb_slim(p: Dict) -> Dict:
    """A prediction for the list: its likeliest lines (at useful prices) and
    the middle handicap and total; the rest from /api/basketball/match."""
    lines = p.get("bb_markets") or []
    useful = [x for x in lines if x["odds"] >= 1.15 and x["market"] != "bb_overtime"]
    top = sorted(useful, key=lambda x: -x["prob"])[:BB_SLIM_LINES]
    out = {**{k: v for k, v in p.items() if k != "bb_markets"}, "top_lines": top, "lines": len(lines)}
    if _bb_team_games:
        import basketball_facts as bf
        out["home_form"] = bf.form_string(_bb_team_games, p["home"])
        out["away_form"] = bf.form_string(_bb_team_games, p["away"])
    return out


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


def _bb_upcoming() -> List[Dict]:
    """The priced matches not started yet (full lines)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return [p for p in _bb_predictions if f"{p['date']} {p['time']}" > now]


@app.get("/api/basketball/match")
async def get_basketball_match(request: Request, event: str = Query(..., max_length=40)):
    """One basketball match: every line SportyBet offers on it that we priced."""
    await _check_sport_access(request, "basketball")
    p = next((x for x in _bb_predictions if x.get("sportybet_event_id") == event), None)
    if not p:
        raise HTTPException(status_code=404, detail="Match not found")
    return p


# ── Tennis and table tennis form and head-to-head (tennis_facts.py) ─────────
# sport: "tennis" | "table_tennis" (tennis_facts.SPORTS)
_racket_players: Dict[str, Dict[str, List]] = {"tennis": {}, "table_tennis": {}}   # player -> his matches, newest first
_racket_status: Dict[str, Dict[str, Any]] = {"tennis": {}, "table_tennis": {}}
_tennis_players = _racket_players["tennis"]          # (the tennis one, by its old name)
_tennis_status = _racket_status["tennis"]


def _racket_results(r, sport: str) -> List[Dict]:
    import basketball_data as bd
    import tennis_facts as tf
    out: List[Dict] = []
    for blob in (r.hgetall(tf.SPORTS[sport]["key"]) or {}).values() if r else []:
        try:
            out += bd.decode(blob)
        except Exception:
            continue
    return out


def _tennis_results(r) -> List[Dict]:
    return _racket_results(r, "tennis")


def _racket_index_build(sport: str) -> int:
    import tennis_facts as tf
    rows = _tennis_results(_get_redis()) if sport == "tennis" else _racket_results(_get_redis(), sport)
    idx = tf.build_index(rows)
    _racket_players[sport].clear()
    _racket_players[sport].update(idx)
    return len(idx)


async def _racket_collect(sport: str) -> Dict[str, Any]:
    """Yesterday's and today's results, a few older days (the backfill), and
    days past the sport's window dropped."""
    import basketball_data as bd
    import tennis_facts as tf
    r = _get_redis()
    if not r:
        return {"error": "no database"}
    key = tf.SPORTS[sport]["key"]
    have = [k.decode() if isinstance(k, bytes) else k for k in (r.hkeys(key) or [])]
    old = tf.stale_days(have, date.today(), sport)
    if old:
        r.hdel(key, *old)
    fetched, matches, failed = 0, 0, 0
    for day in tf.days_to_collect(have, date.today(), sport):
        try:
            found = await tf.fetch_results_day(day, sport=sport)
        except Exception as e:
            failed += 1
            print(f"[{sport}] results for {day}: {e}")
            continue
        r.hset(key, day, bd.encode(found))
        fetched += 1
        matches += len(found)
        await asyncio.sleep(0.5)
    players = await asyncio.to_thread(_racket_index_build, sport)
    _racket_status[sport].update(at=datetime.now(timezone.utc).isoformat(timespec="seconds"), days=fetched,
                                 matches=matches, failed=failed, days_stored=r.hlen(key), players=players)
    print(f"[{sport}] results: {fetched} days, {matches} matches "
          f"({_racket_status[sport]['days_stored']} days stored, {players} players)")
    return dict(_racket_status[sport])


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


async def _live_probe() -> Dict[str, Any]:
    """What SportyBet's live listings return per sport (fields and a few
    events), and each live job's last run: for the read_probe job."""
    import basketball_data as bd
    import sportybet
    import tennis_facts as tf
    out: Dict[str, Any] = {"basketball_tick": dict(_bbmd_status),
                           "racket_ticks": {s: dict(v) for s, v in _rkmd_status.items()}}
    session = sportybet.shared_session()
    for name, sid in (("basketball", bd.BASKETBALL), ("tennis", tf.SPORTS["tennis"]["id"]),
                      ("table_tennis", tf.SPORTS["table_tennis"]["id"])):
        part = {}
        for path, params in bd.LIVE_LISTS:
            try:
                data = await sportybet._request(session, "GET", path,
                                                params={**params, "sportId": sid, "_t": sportybet._now_ms()})
                found: List[Dict] = []
                sportybet._collect_events(data.get("data"), found)
                part[path] = {"events": len(found), "sample": [
                    {k: e.get(k) for k in ("eventId", "homeTeamName", "matchStatus", "status", "setScore", "gameScore",
                                           "pointScore", "playedSeconds", "remainingTimeInPeriod", "period")}
                    for e in found[:3]]}
            except Exception as e:
                part[path] = {"error": str(e)[:200]}
        out[name] = part
    return out


async def _tennis_collect() -> Dict[str, Any]:
    return await _racket_collect("tennis")


async def _table_tennis_collect() -> Dict[str, Any]:
    return await _racket_collect("table_tennis")


async def _racket_index(sport: str) -> Dict[str, List]:
    if not _racket_players[sport]:
        await asyncio.to_thread(_racket_index_build, sport)
    return _racket_players[sport]


def _with_racket_form(preds: List[Dict], sport: str) -> List[Dict]:
    """Each player's last 5 on the list (for the cards), where we have his results."""
    idx = _racket_players.get(sport)
    if not idx:
        return preds
    import tennis_facts as tf
    return [{**p, "home_form": tf.form_string(idx, p.get("home") or ""),
             "away_form": tf.form_string(idx, p.get("away") or "")} for p in preds]


def _with_tennis_form(preds: List[Dict]) -> List[Dict]:
    return _with_racket_form(preds, "tennis")


async def _racket_facts(request: Request, sport: str, home: str, away: str, date_: str, time_: str, league: str):
    import tennis_facts as tf
    await _check_sport_access(request, "tennis" if sport == "tennis" else "table-tennis")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_) or (time_ and not re.fullmatch(r"\d{2}:\d{2}", time_)):
        raise HTTPException(status_code=400, detail="Invalid match")
    idx = await _racket_index(sport)
    return {**tf.facts(idx, {"home": home, "away": away, "date": date_, "time": time_ or "12:00", "league": league}, sport),
            "results_days": _racket_status[sport].get("days_stored")}


@app.get("/api/tennis/facts")
async def get_tennis_facts(request: Request, home: str = Query(..., max_length=80), away: str = Query(..., max_length=80),
                           date_: str = Query(..., alias="date"), time_: str = Query("", alias="time", max_length=5),
                           league: str = Query("", max_length=120)):
    """One tennis match's form: each player's last 5, their meetings, and
    each player's numbers over his last 20 matches (tennis_facts.py)."""
    return await _racket_facts(request, "tennis", home, away, date_, time_, league)


@app.get("/api/table-tennis/facts")
async def get_table_tennis_facts(request: Request, home: str = Query(..., max_length=80),
                                 away: str = Query(..., max_length=80), date_: str = Query(..., alias="date"),
                                 time_: str = Query("", alias="time", max_length=5), league: str = Query("", max_length=120)):
    """The same for table tennis: games and points in place of sets and games."""
    return await _racket_facts(request, "table_tennis", home, away, date_, time_, league)


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


def _rk_url(sport: str) -> str:
    """The sport as the site's URLs and access switches name it."""
    return "table-tennis" if sport == "table_tennis" else sport


def _rk_load_models_sync() -> Dict[str, int]:
    import basketball_data as bd
    import table_tennis_fit
    import tennis_fit
    r = _get_redis()
    if not r:
        return {}
    for sport, mod in (("tennis", tennis_fit), ("table_tennis", table_tennis_fit)):
        try:
            raw = r.get(mod.MODEL_KEY)
            if raw:
                _rk_models[sport] = mod.load_model(raw)
        except Exception as e:
            print(f"[{sport}] couldn't load the model: {e}")
        try:
            raw = r.get(RK_PRED_KEY.format(sport=sport))
            if raw and not _rk_predictions[sport]:
                _rk_predictions[sport][:] = bd.decode(raw)
        except Exception:
            pass
    return {s: len(m.get("players") or {}) for s, m in _rk_models.items()}


async def _rk_load_models() -> Dict[str, int]:
    """The fitted ratings (nightly, GitHub Actions) from Redis."""
    got = await asyncio.to_thread(_rk_load_models_sync)
    print(f"[Racket] models loaded: {got} players")
    return got


async def _fb_calibration_load() -> int:
    """Football's calibration maps (fitted nightly, GitHub Actions) from Redis."""
    import football_calibration

    def load() -> int:
        r = _get_redis()
        if r is None:
            return 0
        football_calibration.set_maps(football_calibration.load(r))
        # and each market's blend with SportyBet's prices (check_market_blend.py)
        try:
            raw = r.get(market_blend.REPORT_KEY)
            got = (json.loads(raw) or {}).get("weights") if raw else {}
            market_blend.weights = {str(k): float(v) for k, v in (got or {}).items()
                                    if isinstance(v, (int, float)) and 0 <= v < 1}
        except Exception as e:
            print(f"[Calibration] market blend weights not loaded: {e}")
        return len(football_calibration.maps())
    import market_blend
    got = await asyncio.to_thread(load)
    print(f"[Calibration] football maps loaded: {got} markets; blended with SportyBet: "
          f"{market_blend.weights or 'none'}")
    return got


def _rk_slim(p: Dict) -> Dict:
    """A prediction for the list: its likeliest lines at useful prices; the
    rest from /api/{sport}/match."""
    lines = p.get("rk_markets") or []
    top = sorted((x for x in lines if x["odds"] >= 1.15), key=lambda x: -x["prob"])[:RK_SLIM_LINES]
    return {**{k: v for k, v in p.items() if k != "rk_markets"}, "top_lines": top, "lines": len(lines)}


def _rk_upcoming(sport: str) -> List[Dict]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return [p for p in _rk_predictions[sport] if f"{p['date']} {p['time']}" > now]


async def _rk_refresh(sport: str) -> List[Dict]:
    """Price every match SportyBet lists in the sport (every line of our markets)."""
    import basketball_data as bd
    import racket_markets as rkm
    import racket_predictions as rp
    import sportybet
    if not _rk_models:
        await _rk_load_models()
    try:
        events, report = await sportybet.fetch_sport_events(sport, markets=rkm.listing(sport))
    except Exception as e:
        print(f"[{sport}] listing failed: {e}")
        events, report = [], [str(e)]
    preds = await asyncio.to_thread(rp.build, events, sport, _rk_models.get(sport))
    if not preds:
        _rk_status[sport].update(listing={"events": len(events), "predictions": 0, "report": report})
        return _rk_predictions[sport]
    _rk_predictions[sport][:] = preds
    r = _get_redis()
    if r:
        try:
            def save():
                r.set(RK_PRED_KEY.format(sport=sport), bd.encode(preds), ex=6 * 3600)
                r.setex(f"betiq:sports:{_rk_url(sport)}", 3600, json.dumps([_rk_slim(p) for p in preds]))
            await asyncio.to_thread(save)
        except Exception as e:
            print(f"[{sport}] couldn't save predictions: {e}")
    try:
        if await asyncio.to_thread(_rkmd_merge, sport, preds):
            _rk_strip_cache.pop(sport, None)
    except Exception as e:
        print(f"[{sport}] couldn't keep the match days: {e}")
    rated = sum(1 for p in preds if p.get("rated"))
    _rk_status[sport].update(at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             listing={"events": len(events), "predictions": len(preds), "rated": rated,
                                      "lines": sum(len(p.get("rk_markets") or []) for p in preds), "report": report})
    print(f"[{sport}] {len(preds)} matches priced ({rated} with both players rated) · {' · '.join(report)}")
    return preds


async def _tennis_refresh() -> List[Dict]:
    return await _rk_refresh("tennis")


async def _table_tennis_refresh() -> List[Dict]:
    return await _rk_refresh("table_tennis")


def _rkmd_load(r, sport: str, d: str) -> Dict[str, Dict]:
    import racket_matchday as rmd
    try:
        raw = r.get(rmd.key(sport, d)) if r else None
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _rkmd_save(r, sport: str, d: str, day: Dict[str, Dict]) -> None:
    import racket_matchday as rmd
    if r:
        r.set(rmd.key(sport, d), json.dumps(day, separators=(",", ":")), ex=rmd.TTL)


def _rkmd_merge(sport: str, preds: List[Dict]) -> int:
    import racket_matchday as rmd
    r = _get_redis()
    if not r:
        return 0
    now, changed = datetime.now(timezone.utc), 0
    for d, day_preds in rmd.by_date(preds).items():
        day = _rkmd_load(r, sport, d)
        if rmd.merge_predictions(day, day_preds, now):
            _rkmd_save(r, sport, d, day)
            changed += 1
    return changed


def _rk_results_days(r, sport: str, days: Iterable[str]) -> Dict[str, Dict]:
    """SportyBet's finished matches on these days (the results store), by event id."""
    import basketball_data as bd
    import tennis_facts as tf
    key = tf.SPORTS[sport]["key"]
    out: Dict[str, Dict] = {}
    days = list(days)
    for raw in (r.hmget(key, days) if r and days else []):
        if raw:
            try:
                out.update({m["id"]: m for m in bd.decode(raw)})
            except Exception:
                continue
    return out


RESULTS_REFRESH_MINUTES = 8  # SportyBet's finals for the live ticks
PAGE_FROZEN_MINUTES = 20   # a live score read from a match's own page, unchanged this long: taken as over


def _apply_live_reading(e: Dict, eid: str, overdue: bool, live: Dict[str, Dict], ended: Dict[str, Dict],
                        checked: set, paged: set, now: datetime, apply_result, apply_live, stale_live) -> bool:
    """What one live read means for a started match; True if it changed.
    A final wins. A match past its live window takes no live score (its page
    can still show the last in-play one): it waits for its final. A score
    from the match's own page that hasn't moved in PAGE_FROZEN_MINUTES is
    taken as a match that has ended."""
    if eid in ended:
        return apply_result(e, ended[eid])
    res = e.get("result") or {}

    def final_soon() -> bool:
        if res.get("status") != "live":
            return False
        e["result"] = {**res, "status": "scheduled", "minute": "Final soon"}
        return True
    if overdue:
        return final_soon()
    if eid in live:
        if apply_live(e, live[eid]):
            return True
        if eid in paged and _older_than(res.get("at"), now, PAGE_FROZEN_MINUTES):
            return final_soon()
        return False
    return stale_live(e, live, checked)


def _older_than(at: Optional[str], now: datetime, minutes: int) -> bool:
    try:
        return now - datetime.fromisoformat(at) > timedelta(minutes=minutes)
    except (TypeError, ValueError):
        return False


async def _rk_results_refresh(sport: str) -> Dict[str, Any]:
    """SportyBet's finals for the days with matches still open (and today),
    stored for the live ticks: a separate job, as a day is many pages."""
    import basketball_data as bd
    import racket_matchday as rmd
    import tennis_facts as tf
    r = _get_redis()
    if not r:
        return {"skipped": "no Redis"}
    now = datetime.now(timezone.utc)
    dates = [(now.date() - timedelta(days=i)).isoformat() for i in (2, 1, 0)]
    loaded = await asyncio.to_thread(lambda: {d: _rkmd_load(r, sport, d) for d in dates})
    open_days = {d for d in dates if any(rmd.needs_result(e, now) for e in loaded[d].values())}
    got = {}
    for d in sorted(open_days | {now.date().isoformat()}):
        try:
            found = await asyncio.wait_for(tf.fetch_results_day(d, sport=sport), 120)
            if found:
                await asyncio.to_thread(r.hset, tf.SPORTS[sport]["key"], d, bd.encode(found))
            got[d] = len(found)
        except Exception as e:
            got[d] = f"{type(e).__name__} {e}"[:120]
    return {"results": got}


async def _bb_results_refresh() -> Dict[str, Any]:
    import basketball_data as bd
    import basketball_matchday as bbmd
    r = _get_redis()
    if not r:
        return {"skipped": "no Redis"}
    now = datetime.now(timezone.utc)
    dates = [(now.date() - timedelta(days=i)).isoformat() for i in (2, 1, 0)]
    loaded = await asyncio.to_thread(lambda: {d: _bbmd_load(r, d) for d in dates})
    open_days = {d for d in dates if any(bbmd.needs_result(e, now) for e in loaded[d].values())}
    got = {}
    for d in sorted(open_days | {now.date().isoformat()}):
        try:
            found = await asyncio.wait_for(bd.fetch_results_day(d), 120)
            if found:
                await asyncio.to_thread(r.hset, bd.RESULTS_KEY, d, bd.encode(found))
            got[d] = len(found)
        except Exception as e:
            got[d] = f"{type(e).__name__} {e}"[:120]
    return {"results": got}


async def _tennis_results_job() -> Dict[str, Any]:
    return await _guarded_tick("tennis_results", lambda: _rk_results_refresh("tennis"), 400)


async def _table_tennis_results_job() -> Dict[str, Any]:
    return await _guarded_tick("table_tennis_results", lambda: _rk_results_refresh("table_tennis"), 400)


async def _bb_results_job() -> Dict[str, Any]:
    return await _guarded_tick("basketball_results", _bb_results_refresh, 400)


OVERDUE_EVERY = 10 * 60   # seconds between reads of one overdue match's page
OVERDUE_PER_TICK = 15
_overdue_at: Dict[str, float] = {}


def _overdue(sport: str, ids: List[str]) -> List[str]:
    """Of the started matches past their live window without a final, the
    ones whose page is due a read (each at most every OVERDUE_EVERY; the
    longest-waiting first, OVERDUE_PER_TICK a run)."""
    now = time.time()
    due = sorted((i for i in ids if now - _overdue_at.get(f"{sport}:{i}", 0) >= OVERDUE_EVERY),
                 key=lambda i: _overdue_at.get(f"{sport}:{i}", 0))[:OVERDUE_PER_TICK]
    for i in due:
        _overdue_at[f"{sport}:{i}"] = now
    return due


async def _rk_live_tick(sport: str) -> Dict[str, Any]:
    """Live scores for matches under way, and finals (graded) from SportyBet's results."""
    import basketball_data as bd
    import racket_matchday as rmd
    import tennis_facts as tf
    r = _get_redis()
    if not r:
        return {"skipped": "no Redis"}
    now = datetime.now(timezone.utc)
    dates = [(now.date() - timedelta(days=i)).isoformat() for i in (2, 1, 0)]
    # Redis (a network round trip each) off the event loop: page requests don't wait on it
    days = await asyncio.to_thread(lambda: {d: _rkmd_load(r, sport, d) for d in dates})
    open_ = {e["id"]: (d, e) for d, day in days.items() for e in day.values() if rmd.needs_result(e, now)}
    if not open_:
        _rkmd_status[sport].update(at=now.isoformat(timespec="seconds"), open=0)
        return {"open": 0}
    changed = set()
    # Finals from the stored results (read by _rk_results_refresh every few minutes)
    finals = await asyncio.to_thread(_rk_results_days, r, sport, dates + [(now.date() + timedelta(days=1)).isoformat()])
    for eid, (d, e) in open_.items():
        if eid in finals and rmd.apply_result(e, finals[eid]):
            changed.add(d)
    playing = [eid for eid, (d, e) in open_.items() if (e.get("result") or {}).get("status") != rmd.FINISHED
               and now - (rmd.kickoff(e) or now) < timedelta(hours=6)]
    # Past the live window with no final in the results list: their own pages,
    # now and then (a final there is taken; otherwise they stop showing "live")
    overdue = _overdue(sport, [eid for eid, (d, e) in open_.items() if eid not in playing
                               and (e.get("result") or {}).get("status") != rmd.FINISHED])
    how = "none started"
    ended: Dict[str, Dict] = {}
    if playing or overdue:
        paged: set = set()
        try:
            live, how, checked = await bd.fetch_live(playing + overdue, sport_id=tf.SPORTS[sport]["id"],
                                                     parse=lambda ev: rmd.parse_live(ev, sport),
                                                     final=tf.parse_result, finals=ended, from_pages=paged)
        except Exception as e:
            live, how, checked = {}, f"failed: {e}", set()
        for eid in playing + overdue:
            d, e = open_[eid]
            if _apply_live_reading(e, eid, eid in overdue, live, ended, checked, paged, now,
                                   rmd.apply_result, rmd.apply_live, rmd.stale_live):
                changed.add(d)
    if changed:
        await asyncio.to_thread(lambda: [_rkmd_save(r, sport, d, days[d]) for d in changed])
    if changed:
        _rk_strip_cache.pop(sport, None)
    _rkmd_status[sport].update(at=now.isoformat(timespec="seconds"), open=len(open_), playing=len(playing),
                               overdue=len(overdue), finals_from_pages=len(ended), live_source=how)
    return dict(_rkmd_status[sport])


async def _tennis_live_tick() -> Dict[str, Any]:
    return await _guarded_tick("tennis_live", lambda: _rk_live_tick("tennis"), 110)


async def _table_tennis_live_tick() -> Dict[str, Any]:
    return await _guarded_tick("table_tennis_live", lambda: _rk_live_tick("table_tennis"), 110)


LIVE_STATS_MINUTES = 2


async def _live_stats_run() -> Dict[str, Any]:
    """Match stats (live_stats.py) for basketball, tennis and table tennis:
    matches in play every EVERY seconds, and each finished match of today
    and yesterday once for its full-match numbers."""
    import live_stats as ls
    r = _get_redis()
    if not r:
        return {"skipped": "no Redis"}
    now = datetime.now(timezone.utc)
    dates = [(now.date() - timedelta(days=i)).isoformat() for i in (1, 0)]
    report: Dict[str, Any] = {}
    for sport in ("basketball", "tennis", "table_tennis", "football"):
        if sport == "basketball":
            days = await asyncio.to_thread(lambda: {d: _bbmd_load(r, d) for d in dates})
        elif sport == "football":
            # By SportyBet's event id, for matches ESPN gives no stats for
            loaded = await asyncio.to_thread(lambda: {d: _md_load(r, d) for d in dates})
            days = {d: {e["sb_id"]: {"id": e["sb_id"], "result": e.get("result")} for e in day.values()
                        if e.get("sb_id") and not (e.get("result") or {}).get("stats")}
                    for d, day in loaded.items()}
        else:
            days = await asyncio.to_thread(lambda sp=sport: {d: _rkmd_load(r, sp, d) for d in dates})
        stored = await asyncio.to_thread(lambda sp=sport: {d: ls.load(r, sp, d) for d in dates})
        todo = {eid: d for d in dates for eid in ls.due(days[d].values(), stored[d], now)}
        if not todo:
            report[sport] = {"due": 0}
            continue
        got = await ls.fetch(list(todo), sport)
        at = ls.now_iso()
        by_day: Dict[str, Dict[str, Dict]] = {}
        for eid, rows in got.items():
            d = todo[eid]
            have = stored[d].get(eid) or {}
            final = ((days[d].get(eid) or {}).get("result") or {}).get("status") == "finished"
            if rows is None:
                if final:  # unreadable: tried again next time, up to ls.MAX_TRIES
                    by_day.setdefault(d, {})[eid] = {**have, "tries": int(have.get("tries") or 0) + 1}
                continue
            if rows or final:
                # Nothing for a finished match: keep what was read while it was live
                by_day.setdefault(d, {})[eid] = {"at": at, "rows": rows or have.get("rows") or [], "final": final}
        await asyncio.to_thread(lambda sp=sport: [ls.save(r, sp, d, v) for d, v in by_day.items()])
        report[sport] = {"due": len(todo), "read": sum(1 for v in got.values() if v is not None),
                         "with_stats": sum(1 for v in got.values() if v), "failed": sum(1 for v in got.values() if v is None)}
    report["at"] = now.isoformat(timespec="seconds")
    return report


async def _live_stats_tick() -> Dict[str, Any]:
    return await _guarded_tick("live_stats", _live_stats_run, 100)


def _with_football_stats(date_: str, day: Dict[str, Dict], matches: List[Dict]) -> None:
    """Football matches ESPN gives no stats for: Sportradar's (live_stats.py), by SportyBet's event id."""
    import live_stats as ls
    import matchday
    ids = {matchday.key(e.get("home", ""), e.get("away", "")): e.get("sb_id") for e in day.values() if e.get("sb_id")}
    if not ids:
        return
    stored = ls.load(_get_redis(), "football", date_)
    for m in matches:
        got = stored.get(ids.get(m.get("key")) or "") or {}
        if not m.get("stats") and got.get("rows"):
            m["stats"] = ls.football_stats(got["rows"])


def _with_live_stats(sport: str, date_: str, matches: List[Dict]) -> List[Dict]:
    """Each match with its latest stats (live_stats.py) as `live_stats`."""
    import live_stats as ls
    stored = ls.load(_get_redis(), sport, date_)
    for m in matches:
        got = stored.get(m.get("id") or "")
        rows = (got or {}).get("rows")
        if not rows and not ls.sr_number(m.get("id") or "") and m.get("status") in ("live", "finished"):
            # SportyBet's own events (e.g. the Setka Cup): Sportradar has none; the score's own numbers
            rows = ls.from_score(m.get("periods"), sport)
        m["live_stats"] = rows or None
    return matches


def _rk_strip(sport: str) -> Dict[str, Any]:
    import racket_matchday as rmd
    today = date.today()
    hit = _rk_strip_cache.get(sport)
    if hit and time.time() - hit[0] < 60 and hit[1]["today"] == today.isoformat():
        return hit[1]
    dates = [(today + timedelta(days=o)).isoformat() for o in range(-MD_DAYS_BACK, MD_DAYS_AHEAD + 1)]
    r = _get_redis()
    upcoming: Dict[str, int] = {}
    for p in _rk_upcoming(sport):
        upcoming[p["date"]] = upcoming.get(p["date"], 0) + 1
    raws = r.mget([rmd.key(sport, d) for d in dates]) if r else [None] * len(dates)
    days = []
    for d, raw in zip(dates, raws):
        try:
            entries = json.loads(raw).values() if raw else []
        except Exception:
            entries = []
        s = rmd.day_summary(entries)
        if d >= today.isoformat():
            s["total"] = max(s["total"], upcoming.get(d, 0))
        days.append({"date": d, **s})
    out = {"today": today.isoformat(), "days": days}
    _rk_strip_cache[sport] = (time.time(), out)
    return out


def _rk_day(sport: str, date_: str) -> Dict[str, Any]:
    import racket_matchday as rmd
    today = date.today()
    d = _date_param(date_ or today.isoformat())
    if not (today - timedelta(days=90) <= d <= today + timedelta(days=MD_DAYS_AHEAD + 1)):
        raise HTTPException(status_code=400, detail="date out of range")
    day = _rkmd_load(_get_redis(), sport, d.isoformat())
    idx = _racket_players.get(sport) or {}
    matches = []
    for e in day.values():
        m = rmd.public(e)
        if idx:
            import tennis_facts as tf
            m["home_form"], m["away_form"] = tf.form_string(idx, m["home"] or ""), tf.form_string(idx, m["away"] or "")
        matches.append(m)
    matches.sort(key=lambda m: (m.get("league_name") or "", m.get("time") or "", m.get("home") or ""))
    _with_live_stats(sport, d.isoformat(), matches)
    return {"date": d.isoformat(), "today": today.isoformat(), "matches": matches,
            "summary": rmd.day_summary(day.values()), "updated": _rkmd_status[sport].get("at")}


def _rk_match(sport: str, event: str) -> Dict:
    p = next((x for x in _rk_predictions[sport] if x.get("sportybet_event_id") == event), None)
    if not p:
        raise HTTPException(status_code=404, detail="Match not found")
    return p


@app.get("/api/tennis/matchday/strip")
async def get_tennis_strip(request: Request):
    """The tennis date strip: 7 days back to 14 ahead."""
    await _check_sport_access(request, "tennis")
    return await asyncio.to_thread(_rk_strip, "tennis")


@app.get("/api/tennis/matchday")
async def get_tennis_matchday(request: Request, date_: str = Query("", alias="date")):
    """One day's tennis: each match's prediction from before it started, its
    live or final score, and how our picks did."""
    await _check_sport_access(request, "tennis")
    return await asyncio.to_thread(_rk_day, "tennis", date_)


@app.get("/api/tennis/match")
async def get_tennis_match(request: Request, event: str = Query(..., max_length=40)):
    """One tennis match: every line SportyBet offers on it that we priced."""
    await _check_sport_access(request, "tennis")
    return _rk_match("tennis", event)


@app.get("/api/table-tennis/matchday/strip")
async def get_table_tennis_strip(request: Request):
    await _check_sport_access(request, "table-tennis")
    return await asyncio.to_thread(_rk_strip, "table_tennis")


@app.get("/api/table-tennis/matchday")
async def get_table_tennis_matchday(request: Request, date_: str = Query("", alias="date")):
    await _check_sport_access(request, "table-tennis")
    return await asyncio.to_thread(_rk_day, "table_tennis", date_)


@app.get("/api/table-tennis/match")
async def get_table_tennis_match(request: Request, event: str = Query(..., max_length=40)):
    await _check_sport_access(request, "table-tennis")
    return _rk_match("table_tennis", event)


@app.get("/api/racket/status")
async def get_racket_status(_admin: str = Depends(require_admin)):
    """Admin: each racket sport's model, listing, match days and results, and the fits' checks."""
    import table_tennis_fit
    import tennis_fit
    r = _get_redis()
    out: Dict[str, Any] = {}
    for sport, mod in (("tennis", tennis_fit), ("table_tennis", table_tennis_fit)):
        try:
            check = json.loads(r.get(mod.REPORT_KEY) or "null") if r else None
        except Exception:
            check = None
        m = _rk_models.get(sport) or {}
        out[sport] = {"players": len(m.get("players") or {}), "as_of": m.get("as_of"),
                      "constants": {k: v for k, v in m.items() if k not in ("players", "avg")},
                      **_rk_status[sport], "matchday": dict(_rkmd_status[sport]), "results": dict(_racket_status[sport]),
                      "check": {k: check.get(k) for k in ("at", "matches", "checked", "winner", "k_mult", "shrink",
                                                           "swing", "surface_weight")} if check else None}
    return out


def _rk_result_for(r, leg: Dict, cache: Dict[str, Dict]) -> Optional[Dict]:
    """A tennis or table tennis leg's result, by the SportyBet event it was booked on."""
    sport = "table_tennis" if leg.get("sport") == "table_tennis" else "tennis"
    try:
        d0 = date.fromisoformat(leg.get("date") or "")
    except ValueError:
        return None
    eid = str(leg.get("event_id") or (leg.get("sb") or {}).get("eventId") or "")
    days = [(d0 + timedelta(days=i)).isoformat() for i in (-1, 0, 1)]
    for s in (sport, "tennis" if sport == "table_tennis" else "table_tennis"):
        for d in days:
            if (s, d) not in cache:
                cache[(s, d)] = _rk_results_days(r, s, [d])
            m = cache[(s, d)].get(eid)
            if m:
                return {"status": "finished", "sets": m["sets"], "games": m.get("games"), "ret": m.get("ret")}
    return None


@app.get("/api/basketball/facts")
async def get_basketball_facts(request: Request, event: str = Query(..., max_length=40)):
    """One match's form: each team's last 5, their meetings, and each team's
    averages over its last 10 (basketball_facts.py), like football's match page."""
    import basketball_facts as bf
    await _check_sport_access(request, "basketball")
    p = next((x for x in _bb_predictions if x.get("sportybet_event_id") == event), None)
    if not p:
        # Started or finished: the match-day store keeps it (with the prediction it had)
        p = _bbmd_find(event)
    if not p:
        raise HTTPException(status_code=404, detail="Match not found")
    idx = await _bb_index()
    return bf.facts(idx, p)


def _bbmd_find(event: str) -> Optional[Dict]:
    """A basketball match from the last week's match days, in a prediction's shape."""
    r = _get_redis()
    if not r:
        return None
    today = date.today()
    for i in range(0, 8):
        e = _bbmd_load(r, (today - timedelta(days=i)).isoformat()).get(event)
        if e:
            pred = e.get("pred") or {}
            return {"home": e.get("home"), "away": e.get("away"), "date": e.get("date"), "time": e.get("time"),
                    "total_line": pred.get("total_line"), "handicap_line": pred.get("handicap_line")}
    return None


# ── Basketball match days (basketball_matchday.py): the date strip, live, history ──
_bbmd_status: Dict[str, Any] = {}


def _bbmd_load(r, d: str) -> Dict[str, Dict]:
    import basketball_matchday as bbmd
    try:
        raw = r.get(bbmd.KEY.format(d)) if r else None
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _bbmd_save(r, d: str, day: Dict[str, Dict]) -> None:
    import basketball_matchday as bbmd
    if r:
        r.set(bbmd.KEY.format(d), json.dumps(day, separators=(",", ":")), ex=bbmd.TTL)


def _bbmd_merge(preds: List[Dict]) -> int:
    """Keep each priced match's prediction in its day (until tip-off)."""
    import basketball_matchday as bbmd
    r = _get_redis()
    if not r:
        return 0
    now, changed = datetime.now(timezone.utc), 0
    for d, day_preds in bbmd.by_date(preds).items():
        day = _bbmd_load(r, d)
        if bbmd.merge_predictions(day, day_preds, now):
            _bbmd_save(r, d, day)
            changed += 1
    return changed


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


async def _bb_live_guarded() -> Dict[str, Any]:
    return await _guarded_tick("basketball_live", _bb_live_tick, 110)


_bb_strip_cache: Dict[str, Tuple[float, Any]] = {}


@app.get("/api/basketball/matchday/strip")
async def get_basketball_strip(request: Request):
    """The basketball date strip: 7 days back to 14 ahead, like football's."""
    import basketball_matchday as bbmd
    await _check_sport_access(request, "basketball")
    today = date.today()
    hit = _bb_strip_cache.get("strip")
    if hit and time.time() - hit[0] < 60 and hit[1]["today"] == today.isoformat():
        return hit[1]
    dates = [(today + timedelta(days=o)).isoformat() for o in range(-MD_DAYS_BACK, MD_DAYS_AHEAD + 1)]
    r = _get_redis()
    upcoming: Dict[str, int] = {}
    for p in _bb_upcoming():
        upcoming[p["date"]] = upcoming.get(p["date"], 0) + 1
    raws = r.mget([bbmd.KEY.format(d) for d in dates]) if r else [None] * len(dates)
    days = []
    for d, raw in zip(dates, raws):
        try:
            entries = json.loads(raw).values() if raw else []
        except Exception:
            entries = []
        s = bbmd.day_summary(entries)
        if d >= today.isoformat():
            s["total"] = max(s["total"], upcoming.get(d, 0))
        days.append({"date": d, **s})
    out = {"today": today.isoformat(), "days": days}
    _bb_strip_cache["strip"] = (time.time(), out)
    return out


@app.get("/api/basketball/matchday")
async def get_basketball_matchday(request: Request, date_: str = Query("", alias="date")):
    """One day's basketball: each game's prediction from before tip-off, its
    live or final score, and how our picks did."""
    import basketball_matchday as bbmd
    await _check_sport_access(request, "basketball")
    today = date.today()
    d = _date_param(date_ or today.isoformat())
    if not (today - timedelta(days=90) <= d <= today + timedelta(days=MD_DAYS_AHEAD + 1)):
        raise HTTPException(status_code=400, detail="date out of range")
    day = await asyncio.to_thread(_bbmd_load, _get_redis(), d.isoformat())
    matches = sorted((bbmd.public(e) for e in day.values()),
                     key=lambda m: (m.get("league_name") or "", m.get("time") or "", m.get("home") or ""))
    await asyncio.to_thread(_with_live_stats, "basketball", d.isoformat(), matches)
    return {"date": d.isoformat(), "today": today.isoformat(), "matches": matches,
            "summary": bbmd.day_summary(day.values()), "updated": _bbmd_status.get("at")}


@app.get("/api/basketball/status")
async def get_basketball_status(_admin: str = Depends(require_admin)):
    """Admin: results stored, leagues rated, the last listing."""
    import basketball_data as bd
    r = _get_redis()
    leagues = sorted(((lg.name, lg.n, len(lg.attack), round(lg.home_court, 1), round(lg.sigma["margin"], 1))
                      for lg in _bb_leagues.values()), key=lambda t: -t[1])
    try:
        bt = json.loads(r.get("betiq:bb:backtest") or "null") if r else None
    except Exception:
        bt = None
    return {**_bb_status, "matchday": dict(_bbmd_status), "days_stored": r.hlen(bd.RESULTS_KEY) if r else 0, "result_days": bd.RESULT_DAYS,
            "leagues": [{"league": n, "games": g, "teams": t, "home_court": hc, "margin_sd": sd}
                        for n, g, t, hc, sd in leagues],
            "backtest": {"at": bt.get("at"), "overall": bt.get("overall"), "leagues": len(bt.get("leagues") or {})}
            if bt else None}


# ── Player props (props_collect → props_backtest → props_pricing) ─────────
# Box scores collected nightly in GitHub Actions; the walk-forward check's
# spreads and scale; SportyBet's player lines priced on its match pages.
PROPS_FB_KEY = "betiq:props:fb:priced"      # football event id -> priced goalscorers
PROPS_BB_MAX_PAGES = 40                     # basketball match pages read per refresh (props are on big games)
FB_PROP_LEAGUES = {"PL": "Premier League", "PD": "LaLiga", "BL1": "Bundesliga", "SA": "Serie A", "FL1": "Ligue 1"}
_props_players: Dict[Tuple[str, str], Dict[str, Dict]] = {}
_props_calib: Dict[str, Any] = {}
_props_fb: Dict[str, List[Dict]] = {}


def _props_load_sync() -> Dict[str, int]:
    import basketball_data as bd
    import props_backtest
    import props_collect as pc
    r = _get_redis()
    if not r:
        return {}
    got = {}
    for sport, leagues in (("bb", ("NBA", "WNBA", "Euroleague", "Eurocup")), ("fb", tuple(pc.UNDERSTAT.values()))):
        for league in leagues:
            raw = r.get(pc.PLAYERS_KEY.format(sport=sport, league=league))
            if raw:
                _props_players[(sport, league)] = bd.decode(raw)
                got[f"{sport}:{league}"] = len(_props_players[(sport, league)])
    try:
        _props_calib.clear()
        _props_calib.update(json.loads(r.get(props_backtest.CALIB_KEY) or "{}"))
        _props_fb.update(json.loads(r.get(PROPS_FB_KEY) or "{}"))
    except Exception:
        pass
    return got


async def _props_load() -> Dict[str, int]:
    """The players' box scores and the walk-forward check's numbers, from Redis."""
    try:
        got = await asyncio.to_thread(_props_load_sync)
    except Exception as e:
        print(f"[Props] couldn't load: {e}")
        return {}
    print(f"[Props] players loaded: {got}")
    return got


def _bb_prop_lines(pred: Dict, page: Dict) -> List[Dict]:
    """A basketball match's player lines, priced (props_pricing.bb_price)."""
    import basketball_model as bm
    import props_pricing as pr
    import sportybet
    league = pr.BB_LEAGUES.get(pred.get("league") or "")
    players = _props_players.get(("bb", league)) if league else None
    if not players:
        return []
    # Tonight's expected points against each team's usual (our basketball ratings)
    lg = _bb_leagues.get(pred["league"])
    factor: Dict[str, float] = {}
    if lg:
        for side, exp_pts in (("home", pred.get("exp_home_pts")), ("away", pred.get("exp_away_pts"))):
            name = pred[side]
            usual = lg.avg + lg.attack.get(name, 0.0) + (lg.home_court / 2)
            if exp_pts and usual > 0:
                # Box scores name teams their own way: every team name close to this side's
                for t in {p["team"] for p in players.values()}:
                    if sportybet.team_similarity(t, name) >= 0.75:
                        factor[t] = exp_pts / usual
    calib = (_props_calib.get("bb") or {}).get(league) or {}
    disp = dict(calib.get("dispersion") or {})
    return pr.bb_price(page, players, factor, disp, teams=(pred["home"], pred["away"]),
                       calibration=calib.get("calibration_map") or {})


async def _bb_add_props(preds: List[Dict]) -> int:
    """Player lines on the matches of leagues we have box scores for."""
    import props_pricing as pr
    import sportybet
    todo = [p for p in preds if p.get("league") in pr.BB_LEAGUES][:PROPS_BB_MAX_PAGES]
    n = 0
    for p in todo:
        try:
            page = await sportybet.event_page(p["sportybet_event_id"])
        except Exception as e:
            print(f"[Props] page {p['sportybet_event_id']}: {e}")
            continue
        lines = _bb_prop_lines(p, page or {})
        if lines:
            p["bb_markets"] = (p.get("bb_markets") or []) + lines
            p["props"] = len(lines)
            n += len(lines)
        await asyncio.sleep(0.3)
    return n


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


@app.get("/api/props/status")
async def get_props_status(_admin: str = Depends(require_admin)):
    """Admin: players loaded per league, and the walk-forward check's numbers."""
    return {"players": {f"{s}:{l}": len(v) for (s, l), v in _props_players.items()},
            "check": _props_calib or None,
            "football_matches_priced": len(_props_fb)}


@app.get("/api/props/football")
async def get_football_props(home: str = Query(..., max_length=80), away: str = Query(..., max_length=80),
                             date_: str = Query("", alias="date", max_length=10)):
    """A football match's anytime goalscorers: our chance, SportyBet's price and ids."""
    for lines in _props_fb.values():
        if lines and lines[0]["home"] == home and lines[0]["away"] == away and (not date_ or lines[0]["date"] == date_):
            return {"scorers": lines}
    return {"scorers": []}


def _props_result_for(leg: Dict) -> Optional[Dict]:
    """A player-prop leg's result from the box scores: {"status", "value"}
    (his count of the stat, or goals), "void" when he didn't play."""
    import player_props as pp
    import props_pricing as pr
    market = str(leg.get("market") or "")
    key = str(leg.get("code") or "").split("|")[0]
    if market.startswith("bb_player_"):
        stat = market[len("bb_player_"):]
        col = pr.BB_STATS.get(stat, (None,))[0]
        pools = [v for (sport, _), v in _props_players.items() if sport == "bb"]
    elif market == "anytime_scorer":
        col, pools = 5, [v for (sport, _), v in _props_players.items() if sport == "fb"]
    else:
        return None
    try:
        d0 = date.fromisoformat(leg.get("date") or "")
    except ValueError:
        return None
    days = {(d0 + timedelta(days=i)).isoformat() for i in (-1, 0, 1)}
    for players in pools:
        p = players.get(key)
        if not p or col is None:
            continue
        rows = [r for r in p["games"] if r[0] in days]
        if rows:
            return {"status": "finished", "value": float(rows[-1][col])}
        # His team's game is in but he isn't: he didn't play (void)
        if any(r[0] in days and r[1] == p["team"] for q in players.values() for r in q["games"][-5:]):
            return {"status": "void"}
    return None


def _bb_result_for(r, leg: Dict, cache: Dict[str, Dict]) -> Optional[Dict]:
    """A basketball ticket leg's result, by the SportyBet event it was booked on."""
    import basketball_data as bd
    try:
        d0 = date.fromisoformat(leg.get("date") or "")
    except ValueError:
        return None
    days = [(d0 + timedelta(days=i)).isoformat() for i in (-1, 0, 1)]
    for d in days:
        if d not in cache:
            cache[d] = {g["id"]: g for g in _bb_results(r, [d])}
    games = {k: v for d in days for k, v in cache[d].items()}
    return bd.result_for(games, leg)


@app.get("/api/team-logo")
async def get_team_logo(name: str, response: Response):
    """
    Generic team badge/logo lookup. Tries the football-data.org crest cache
    first — crests for every team in our tracked football competitions
    (World Cup, Premier League, ...) are captured for free from the fixtures
    the pipeline already fetches, no extra API call needed and no fuzzy name
    matching required. Falls back to the TheSportsDB-backed generic lookup
    for anything else (basketball, tennis, a team not yet seen in a fixture
    window). Cached server-side (Redis, 30 days) since badges don't change.
    """
    key = ("team", name)
    hit = _logo_answer(response, key)
    if hit is not None:
        return hit
    return _logo_answer(response, key, await _team_logo(name))


async def _team_logo(name: str) -> Dict[str, Any]:
    from team_logos import lookup_team_logo
    logo = await asyncio.to_thread(_get_cached_team_crest, name)
    source = "football-data.org" if logo else None
    if not logo:
        logo = await lookup_team_logo(name, redis_client=_get_redis())
        source = "thesportsdb" if logo else None
    return {"name": name, "logo": logo, "source": source}


@app.get("/api/team-logos")
async def get_team_logos(names: str, response: Response):
    """Several clubs' badges at once (names joined by "|", at most 120):
    {"logos": {name: url or null}} — a page's badges in one request."""
    wanted = list(dict.fromkeys(n.strip() for n in names.split("|") if n.strip()))[:120]
    from team_logos import lookup_team_logo
    out: Dict[str, Optional[str]] = {}
    missing = []
    for n in wanted:
        hit = _logo_answer(response, ("team", n))
        if hit is None:
            missing.append(n)
        else:
            out[n] = hit.get("logo")
    # Crests in one worker thread for the lot (not a thread each: the pool is
    # shared with every other request), then TheSportsDB for the rest, a few at a time
    found = await asyncio.to_thread(lambda: {n: _get_cached_team_crest(n) for n in missing}) if missing else {}
    gate = asyncio.Semaphore(4)

    async def outside(n: str) -> Dict[str, Any]:
        async with gate:
            logo = await lookup_team_logo(n, redis_client=_get_redis())
        return {"name": n, "logo": logo, "source": "thesportsdb" if logo else None}
    rest = [n for n in missing if not found.get(n)]
    more = dict(zip(rest, await asyncio.gather(*(outside(n) for n in rest)))) if rest else {}
    for n in missing:
        got = {"name": n, "logo": found[n], "source": "football-data.org"} if found.get(n) else more[n]
        out[n] = _logo_answer(response, ("team", n), got).get("logo")
    response.headers["Cache-Control"] = f"public, max-age={LOGO_MISS_SECONDS}"
    return {"logos": out}


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

    def cached() -> Optional[str]:
        r = _get_redis()
        try:
            return (r.get(_competition_emblem_cache_key(code)) or None) if r else None
        except Exception:
            return None
    hit = await asyncio.to_thread(cached)
    if hit:
        return hit

    try:
        emblem = await _get_fd_client().fetch_competition_emblem(code)
    except Exception as e:
        # Do NOT cache failures — an outage or a transient 429 shouldn't be
        # remembered as "this competition has no emblem" for a month.
        print(f"[CompetitionLogo] football-data.org emblem fetch failed for {code}: {e}")
        return None

    await asyncio.to_thread(_cache_competition_emblem, code, emblem)
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


def _cache_team_crests(fixtures: Iterable[Dict]) -> None:
    """Every fixture's crests in one Redis round trip (one write per team took
    a network round trip each, hundreds a pipeline run)."""
    r = _get_redis()
    if not r:
        return
    pairs = {(fx.get(side) or "").strip(): fx.get(f"{side}_crest")
             for fx in fixtures for side in ("home", "away")}
    pairs = {team: url for team, url in pairs.items() if team and url}
    if not pairs:
        return
    try:
        import crests
        crests.save(r, {t: u for t, u in pairs.items() if "football-data.org" in u})
    except Exception:
        pass
    try:
        pipe = r.pipeline(transaction=False)
        for team, url in pairs.items():
            pipe.setex(_team_crest_cache_key(team), 60 * 60 * 24 * 30, url)
        pipe.execute()
    except Exception:
        for team, url in pairs.items():
            _cache_team_crest(team, url)


def _get_cached_team_crest(team_name: str) -> Optional[str]:
    """The club's crest: its own fixture's, else football-data.org's team
    lists by name (crests.py: whole words, never a guess from a city)."""
    import crests
    if not team_name or not team_name.strip():
        return None
    r = _get_redis()
    if not r:
        return None
    try:
        return r.get(_team_crest_cache_key(team_name)) or crests.lookup(r, team_name)
    except Exception:
        return None


async def _crest_index_refresh() -> int:
    """Every tracked competition's clubs and crests from football-data.org
    (one request a competition, spaced for the rate limit) into crests.KEY."""
    import crests
    if not API_KEY:
        return 0
    client = FootballDataClient(API_KEY)
    found: Dict[str, str] = {}
    for code in LEAGUES:
        if not_in_plan(code):
            continue
        try:
            found.update(crests.entries(await client.fetch_competition_teams(code)))
        except Exception as e:
            print(f"[Crests] {code}: {e}")
        await asyncio.sleep(7)
    n = await asyncio.to_thread(crests.save, _get_redis(), found)
    _logo_memo.clear()
    print(f"[Crests] {n} club names with crests from football-data.org")
    return n


LOGO_SECONDS = 24 * 3600        # a badge found is kept this long (in memory, and by browsers)
LOGO_MISS_SECONDS = 3600        # a badge not found is asked for again after this
_logo_memo: Dict[Tuple, Tuple[float, Dict[str, Any]]] = {}


def _logo_answer(response: Response, key: Tuple, found: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The remembered answer for `key` (None when there isn't a fresh one);
    with `found`, remember it. Badges don't change: browsers keep them a day."""
    now = time.time()
    if found is not None:
        _logo_memo[key] = (now, found)
        if len(_logo_memo) > 5000:
            _logo_memo.clear()
        hit = found
    else:
        memo = _logo_memo.get(key)
        if not memo or now - memo[0] > (LOGO_SECONDS if memo[1].get("logo") else LOGO_MISS_SECONDS):
            return None
        hit = memo[1]
    ttl = LOGO_SECONDS if hit.get("logo") else LOGO_MISS_SECONDS
    response.headers["Cache-Control"] = f"public, max-age={ttl}, stale-while-revalidate={LOGO_SECONDS}"
    return hit


@app.get("/api/competition-logo")
async def get_competition_logo(name: str, response: Response, sport: str = "Soccer"):
    """
    Competition/league badge lookup (World Cup, Premier League, EuroLeague,
    etc.). For football competitions we already know (our own LEAGUES dict),
    tries football-data.org's emblem first — a real API key with exact code
    matching, no fuzzy search needed. Falls back to the generic
    TheSportsDB-backed lookup (used for basketball etc., or if the football
    league isn't one of ours). Cached server-side (Redis, 30 days).
    """
    from competition_logos import lookup_competition_logo
    key = ("competition", name, sport)
    hit = _logo_answer(response, key)
    if hit is not None:
        return hit

    # International competitions: the badge their fixture source supplied
    logo = _international_competition_logo(name)
    if logo:
        return _logo_answer(response, key, {"name": name, "sport": sport, "logo": logo, "source": "fixture-source"})

    logo = await _get_football_competition_emblem(name)
    source = "football-data.org" if logo else None

    if not logo:
        r = _get_redis()
        logo = await lookup_competition_logo(name, redis_client=r, sport=sport)
        source = "thesportsdb" if logo else None

    return _logo_answer(response, key, {"name": name, "sport": sport, "logo": logo, "source": source})


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
async def get_sport_event_detail(sport: str, home: str, away: str, date: str, request: Request):
    """
    Full market detail for a specific basketball/tennis/table-tennis match.
    Used by the sport analysis modal.
    """
    _check_sport(sport)
    await _check_sport_access(request, sport)
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


OLD_LEADERBOARD_KEY = "betiq:leaderboard"   # counted self-reported "won" bets: deleted at startup
LEADERBOARD_SIZE = 20


def _leaderboard(r) -> List[Tuple[str, int]]:
    """Accounts by winning tickets: codes booked here and settled by the
    server from the results, so nobody can report their own wins."""
    wins: Dict[str, int] = {}
    for t in _all_tickets(r):
        if t.get("status") == "won" and t["uid"] != ANON_UID:
            wins[t["uid"]] = wins.get(t["uid"], 0) + 1
    return sorted(wins.items(), key=lambda kv: (-kv[1], kv[0]))[:LEADERBOARD_SIZE]


@app.get("/api/leaderboard")
async def get_leaderboard(request: Request, uid: str = ""):
    """Top predictors. Public, so it never returns user ids (those are what the
    user endpoints key on); `you` marks the caller's own row when signed in."""
    r = _get_redis()
    if not r: return []
    # The verified user; the client's uid only in local development
    import auth
    me = (uid or None) if auth.unverified_uid_allowed() else await optional_user(request)
    try:
        rows = await asyncio.to_thread(_leaderboard, r)
    except Exception:
        return []
    return [{"name": f"#{u[-6:]}", "wins": n, "you": u == me} for u, n in rows]


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

    def near(p: Dict) -> List[Dict]:
        d = date.fromisoformat(p["date"])
        return [ev for k in {(d + timedelta(days=o)).isoformat() for o in (-1, 0, 1)} for ev in by_day.get(k, [])]
    # Both names first, for every prediction; the kick-off pass then only
    # takes events nobody claimed, so one SportyBet match never serves two of ours
    found = {id(p): sportybet.find_event(p["home"], p["away"], near(p)) for p in preds}
    taken = {ev.get("eventId") for ev in found.values() if ev}
    for p in preds:
        ev = found[id(p)]
        kickoff = _kickoff_ms(p)
        if ev is None and kickoff is not None:
            ev = sportybet.find_event_by_kickoff(p["home"], p["away"], kickoff,
                                                 [e for e in near(p) if e.get("eventId") not in taken])
            if ev:
                taken.add(ev.get("eventId"))
        if ev and ev.get("eventId"):
            links[_sb_key(p["home"], p["away"], p["date"])] = sportybet.slim_event(ev)
        elif unlinked is not None:
            guess, score = sportybet.closest_event(p["home"], p["away"], near(p))
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
                    await asyncio.to_thread(lambda: r.set(SB_LINKS_KEY, json.dumps(links), ex=6 * 3600))
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
    await asyncio.to_thread(_save_link_status)
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
    if not event:
        return pred
    priced = {} if ("corners" in have and "bookings" in have) else (
        set_pieces.from_prices(event, getattr(_set_pieces, "size", None)) or {})
    # Shot lines too, for national teams we have too little data on
    if str(pred.get("league") or "").startswith("INT"):
        priced.update(shots.from_prices(event, getattr(_intl_shots, "size", None)) or {})
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
        await asyncio.to_thread(_save_predictions_cache)
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
    state = await asyncio.to_thread(_fd_refs_load, r)
    fd = FootballDataClient(API_KEY)
    async with httpx.AsyncClient() as hc:
        report = await referee_sources.collect_past(lambda url: fd._get(hc, url), state, date.today(), asyncio.sleep)
    await asyncio.to_thread(lambda: r.set(referee_sources.REFS_KEY, gzip.compress(json.dumps(state, separators=(",", ":")).encode())))
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


# National-team match stats (corners, bookings, shots) by (date, team keys):
# the match page's team averages, whose results come from another source
_intl_stats_by_key: Dict[Tuple[str, str, str], Dict[str, float]] = {}


def _index_intl_stats(frame: pd.DataFrame) -> None:
    import match_facts
    if frame is None or frame.empty:
        return
    ours = match_facts.frame(frame)
    table: Dict[Tuple[str, str, str], Dict[str, float]] = {}
    for rec in ours.to_dict("records"):
        try:
            day = pd.Timestamp(rec["Date"]).date().isoformat()
        except (TypeError, ValueError):
            continue
        stats = {k: float(rec[k]) for k in match_facts.STAT_COLS if rec.get(k) == rec.get(k) and rec.get(k) is not None}
        if stats:
            table[(day, intl.team_key(rec["HomeTeam"]), intl.team_key(rec["AwayTeam"]))] = stats
    _intl_stats_by_key.clear()
    _intl_stats_by_key.update(table)


def _intl_stats_for(day: str, home: str, away: str) -> Optional[Dict[str, float]]:
    """A national-team match's stats from the international dataset (a day either side)."""
    if not _intl_stats_by_key:
        return None
    h, a = intl.team_key(home), intl.team_key(away)
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return None
    for o in (0, -1, 1):
        got = _intl_stats_by_key.get(((d + timedelta(days=o)).isoformat(), h, a))
        if got:
            return got
    return None


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
    _index_intl_stats(frame)
    _intl_shots = None
    if any((shot_verdict.get("use") or {}).values()) and shot_verdict.get("params"):
        # The check chose to start teams' shot ratings from their Elo: the same
        # ratings here, from the synced results history
        if (shot_verdict["params"] or {}).get("elo_weight"):
            import intl_elo
            import football_data_sync
            elo = intl_elo.EloTimeline.from_file(football_data_sync.INTERNATIONAL_PATH)
            set_pieces.STRENGTH = elo.strength if elo else None
            _intl_sp_info["elo"] = {"teams": elo.teams()} if elo else {"error": "no results history file"}
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
    # SportyBet's own shot lines, where it prices them: the fallback when we
    # have too little data on a side, mixed with ours when the settled
    # comparison (_shot_blend) showed the mix predicts better
    try:
        priced = shots.from_prices(_linked_event(fx), getattr(_intl_shots, "size", None)) or {}
    except Exception:
        priced = {}
    weight = float((_shot_blend.get("weights") or {}).get("international") or 0.0)
    for stat, line in priced.items():
        if stat not in keep:
            keep[stat] = line
        elif weight:
            keep[stat] = shots.blend(keep[stat], line, weight)
    return keep or None


# ── Our shot lines vs SportyBet's (shots.blend_report) ──
SHOT_BLEND_KEY = "betiq:shots:blend"
SHOT_BLEND_DAYS = 90
_shot_blend: Dict[str, Any] = {"weights": {}, "report": None, "at": None}


def _restore_shot_blend() -> None:
    r = _get_redis()
    if r and not _shot_blend.get("at"):
        try:
            _shot_blend.update(json.loads(r.get(SHOT_BLEND_KEY) or "{}"))
        except Exception:
            pass


async def _shot_blend_job() -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(_refresh_shot_blend)
    except Exception as e:
        print(f"[Shots] vs SportyBet failed: {e}")
        return {"error": str(e)}


def _refresh_shot_blend() -> Dict[str, Any]:
    """Score our shot lines against SportyBet's on the settled matches of the
    last SHOT_BLEND_DAYS days and choose, per group, how much of SportyBet's
    to mix in (none unless the mix predicted better on enough lines)."""
    r = _get_redis()
    if not r:
        return {"skipped": "no Redis"}
    today = date.today()
    days = _md_many(r, [(today - timedelta(days=i)).isoformat() for i in range(1, SHOT_BLEND_DAYS + 1)])
    report = shots.blend_report(e for day in days.values() for e in day.values())
    _shot_blend.update(weights={g: v["chosen"] for g, v in report.items()}, report=report,
                       at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    try:
        r.set(SHOT_BLEND_KEY, json.dumps(_shot_blend))
    except Exception:
        pass
    print(f"[Shots] vs SportyBet: {json.dumps(report)}")
    return report


# ── Weekly accuracy review (market_review.py) ──
REVIEW_KEY = "betiq:review"
_review: Dict[str, Any] = {"mode": "auto", "overrides": {}, "latest": None, "history": [], "loaded": False}


def _restore_review() -> None:
    r = _get_redis()
    if r and not _review.get("loaded"):
        try:
            _review.update(json.loads(r.get(REVIEW_KEY) or "{}"), loaded=True)
        except Exception:
            pass


def _save_review() -> bool:
    r = _get_redis()
    if not r:
        return False
    try:
        r.set(REVIEW_KEY, json.dumps({k: v for k, v in _review.items() if k != "loaded"}))
        return True
    except Exception:
        return False


def _paused_markets() -> set:
    """Markets the optimizer, daily slips and code check leave out now."""
    import market_review
    _restore_review()
    return market_review.blocked(_review)


async def _review_job() -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(_run_review)
    except Exception as e:
        print(f"[Review] failed: {e}")
        return {"error": str(e)}


def _run_review() -> Dict[str, Any]:
    """Review every market over the last market_review.WINDOW_DAYS settled days."""
    import market_review
    r = _get_redis()
    if not r:
        return {"skipped": "no Redis"}
    _restore_review()
    today = date.today()
    days = _md_many(r, [(today - timedelta(days=i)).isoformat() for i in range(1, market_review.WINDOW_DAYS + 1)])
    result = market_review.review((e for day in days.values() for e in day.values()),
                                  (_review.get("latest") or {}).get("paused") or [])
    _review.update(market_review.remember(_review, result, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    _save_review()
    print(f"[Review] {result['matches']} matches · paused {result['paused'] or 'none'} · "
          f"newly {result['newly_paused'] or 'none'} · restored {result['restored'] or 'none'}")
    return result


def _review_view() -> Dict[str, Any]:
    import market_review
    return {"mode": _review.get("mode", "auto"), "overrides": _review.get("overrides") or {},
            "latest": _review.get("latest"), "history": _review.get("history") or [],
            "blocked": sorted(market_review.blocked(_review)),
            "markets": [{"market": m, "name": n} for m, n in _market_names().items()],
            "rules": {"window_days": market_review.WINDOW_DAYS, "min_matches": market_review.MIN_MATCHES,
                      "pause_gap": market_review.PAUSE_GAP, "pause_z": market_review.PAUSE_Z}}


def _market_names() -> Dict[str, str]:
    import optimizer
    return dict(optimizer.MARKET_NAMES)


@app.get("/api/admin/market-review")
async def get_market_review(_admin: str = Depends(require_admin)):
    """The latest weekly accuracy review, past ones, and the settings."""
    _restore_review()
    return _review_view()


@app.put("/api/admin/market-review")
async def put_market_review(body: Dict[str, Any], _admin: str = Depends(require_admin)):
    """Body: {mode?: auto|flag, overrides?: {market: on|off|auto}}."""
    import market_review
    _restore_review()
    try:
        new = market_review.settings(body or {}, _review)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    before = {k: _review.get(k) for k in ("mode", "overrides")}
    _review.update(new)
    if not _save_review():
        _review.update(before)
        raise HTTPException(status_code=503, detail="Couldn't save: the database isn't connected")
    _audit(_admin, "market_review", **new)
    return _review_view()


@app.get("/api/market-review/paused")
async def paused_markets():
    """Markets left out of slips right now, for the optimizer page's note."""
    import market_review
    _restore_review()
    latest = {m["market"]: m for m in (_review.get("latest") or {}).get("markets") or []}
    return {"paused": [{"market": m, "name": _market_names().get(m, m),
                        "why": (latest.get(m) or {}).get("why") if (_review.get("overrides") or {}).get(m) != "off"
                        else "switched off by the admin"}
                       for m in sorted(market_review.blocked(_review))]}


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


OPT_SPORTS = ("football", "basketball", "tennis", "table_tennis")


def _optimizer_sports(body: Dict[str, Any]) -> List[str]:
    """The sports an optimizer request picks from: `sports` (a list, any of
    OPT_SPORTS or "all") or else `sport` (one of them or "all"; football by default)."""
    raw = body.get("sports")
    if isinstance(raw, list) and raw:
        asked = {str(x) for x in raw}
    elif raw is not None and not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="sports must be a list")
    else:
        asked = {str(body.get("sport") or "football")}
    if "all" in asked:
        asked = set(OPT_SPORTS)
    bad = asked - set(OPT_SPORTS)
    if bad:
        raise HTTPException(status_code=400, detail=f"Unknown sport: {', '.join(sorted(bad))} "
                                                    "(football, basketball, tennis, table_tennis or all)")
    return [x for x in OPT_SPORTS if x in asked]


@app.get("/api/optimizer/leagues")
async def optimizer_leagues(request: Request, days: int = 3, sports: str = "football"):
    """The leagues with matches the optimizer can pick from: in the next
    `days` days, for the sports asked (comma-separated), with how many
    matches each has, busiest first. The optimizer's `leagues` takes their ids."""
    days = min(PREDICTION_DAYS, max(1, int(days)))
    asked = _optimizer_sports({"sports": [s for s in sports.split(",") if s]})
    now = datetime.now(timezone.utc)
    today, last = now.date().isoformat(), (now.date() + timedelta(days=days - 1)).isoformat()
    kicked_off = now.strftime("%H:%M")
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def add(sport: str, preds: Iterable[Dict]) -> None:
        for p in preds:
            d = p.get("date") or ""
            if not today <= d <= last or (d == today and (p.get("time") or "99:99") <= kicked_off):
                continue
            lg = str(p.get("league") or "")
            if not lg:
                continue
            row = out.setdefault((sport, lg), {"sport": sport, "id": lg, "name": p.get("league_name") or lg,
                                               "flag": p.get("flag") or "", "matches": 0})
            row["matches"] += 1
    for sp in asked:
        if sp != "football":
            try:
                await _check_sport_access(request, "basketball" if sp == "basketball" else _rk_url(sp))
            except HTTPException:
                continue
        if sp == "football":
            add(sp, (p for p in _predictions_cache if p.get("sport") in (None, "football")))
        elif sp == "basketball":
            add(sp, _bb_upcoming())
        else:
            add(sp, _rk_upcoming(sp))
    return {"leagues": sorted(out.values(), key=lambda x: (-x["matches"], x["name"]))}


@app.post("/api/optimizer")
async def optimize_slip(request: Request, body: Dict[str, Any], _access=Depends(require_feature("optimizer"))):
    """
    Build the slip with the best win chance whose total odds land in a target
    range (optimizer.py). Body: {min_odds, max_odds, max_games?, min_prob?,
    days?, leagues?: [codes], markets?: [ids], codes?: {market: [option
    codes]} (e.g. only some goal lines), bookable_only?, sports?: [football,
    basketball, tennis, table_tennis: any of them] (or sport?: one of them or
    all), bb_markets?: [basketball families], tn_markets? / tt_markets?:
    [tennis / table tennis families]}.
    """
    body = dict(body or {})
    sports = _optimizer_sports(body)
    if len(sports) == 1:
        if sports[0] != "football":
            await _check_sport_access(request, "basketball" if sports[0] == "basketball" else _rk_url(sports[0]))
    else:
        # Sports switched off (or for testers only) are left out of a mixed slip
        off, refused = [], None
        for sp in sports:
            if sp == "football":
                continue
            try:
                await _check_sport_access(request, "basketball" if sp == "basketball" else _rk_url(sp))
            except HTTPException as e:
                off.append(sp)
                refused = e
        if refused and len(off) == len(sports):
            raise refused
        body["_off"] = off
    return await _optimize_request(body)


LIVE_CHECK_ROUNDS = 3          # check, drop and re-solve at most this often
LIVE_PAGE_SECONDS = 60         # a match page read for the check is reused this long
LIVE_PAGES_AT_ONCE = 8
LIVE_PAGE_TIMEOUT = 8.0
_live_pages: Dict[str, Tuple[float, Optional[Dict]]] = {}


def asdict_option(o) -> Dict[str, Any]:
    return {"home": o.home, "away": o.away, "date": o.date, "market": o.market, "code": o.code}


def _pick_key(x: Dict[str, Any]) -> Tuple:
    return (x.get("home"), x.get("away"), x.get("date"), x.get("market"), x.get("code"))


async def _live_pages_for(event_ids: Iterable[str]) -> Dict[str, Optional[Dict]]:
    """Each event's SportyBet match page as it is now (every market and
    outcome, with their status): None when it couldn't be read in time."""
    import sportybet
    now = time.time()
    out: Dict[str, Optional[Dict]] = {}
    todo = []
    for eid in set(event_ids):
        hit = _live_pages.get(eid)
        if hit and now - hit[0] < LIVE_PAGE_SECONDS:
            out[eid] = hit[1]
        else:
            todo.append(eid)
    gate = asyncio.Semaphore(LIVE_PAGES_AT_ONCE)

    async def read(eid: str):
        async with gate:
            try:
                return eid, await asyncio.wait_for(sportybet.event_page(eid), LIVE_PAGE_TIMEOUT)
            except Exception:
                return eid, None
    for eid, page in await asyncio.gather(*(read(e) for e in todo)):
        out[eid] = page
        if page is not None:
            _live_pages[eid] = (time.time(), page)
    if len(_live_pages) > 2000:
        for k, (at, _) in list(_live_pages.items()):
            if now - at > LIVE_PAGE_SECONDS:
                _live_pages.pop(k, None)
    return out


async def _live_states(picks: List[Dict[str, Any]], events: Dict[Tuple, Optional[Dict]],
                       market_map: Dict[str, Dict[str, Any]]) -> Dict[Tuple, Tuple[str, Optional[float]]]:
    """{pick key: (state, SportyBet's price now)} for the picks a code can
    take: "open" / "suspended" / "missing" / "started", or "unchecked" when
    the match page couldn't be read (those stay, at the price we had)."""
    import booking_slip
    ids_of: Dict[Tuple, Dict[str, str]] = {}
    for x in picks:
        ev = events.get((x["home"], x["away"], x.get("date")))
        ids = booking_slip.pick_ids(x, str((ev or {}).get("eventId") or "") or None, market_map)
        if ids:
            ids_of[_pick_key(x)] = ids
    pages = await _live_pages_for(ids["eventId"] for ids in ids_of.values())
    now = time.time()
    out: Dict[Tuple, Tuple[str, Optional[float]]] = {}
    for k, ids in ids_of.items():
        page = pages.get(ids["eventId"])
        if page is None:
            out[k] = ("unchecked", None)
            continue
        start = booking_slip._start_seconds(page)
        if start is not None and start <= now:
            out[k] = ("started", None)
            continue
        out[k] = booking_slip.live_state(page, ids)
    return out


async def _solve(groups, lo: float, hi: float, target: float, max_games: int) -> Optional[Dict]:
    """The best slip in [lo, hi]; failing that, the nearest within ±25% of the
    target, flagged as off target (within_target is False)."""
    import optimizer
    res = await asyncio.to_thread(optimizer.optimize, groups, lo, hi, max_games)
    if res is None and lo <= target <= hi:
        for spread in (0.1, 0.25):
            near = await asyncio.to_thread(optimizer.optimize, groups, max(1.01, target * (1 - spread)),
                                           target * (1 + spread), max_games)
            if near is not None:
                return {**near, "within_target": False}
    return res


async def _solve_checked(groups, lo: float, hi: float, target: float, max_games: int,
                         events: Dict[Tuple, Optional[Dict]],
                         market_map: Dict[str, Dict[str, Any]]) -> Tuple[Optional[Dict], Dict[str, Any]]:
    """The slip, with every pick checked on SportyBet's match page as it is
    now: suspended, withdrawn or started ones are dropped and the slip solved
    again without them; the rest take SportyBet's current price (solved again
    when one moved), so the code books at the odds shown. Up to
    LIVE_CHECK_ROUNDS rounds. Returns (slip or None, what the check did)."""
    import booking_slip
    check: Dict[str, Any] = {"checked": 0, "removed": [], "repriced": 0, "unchecked": 0}
    result = await _solve(groups, lo, hi, target, max_games)
    for _ in range(LIVE_CHECK_ROUNDS):
        if not result or not result.get("picks"):
            break
        states = await _live_states(result["picks"], events, market_map)
        check["checked"] = sum(1 for st in states.values() if st[0] != "unchecked")
        check["unchecked"] = sum(1 for st in states.values() if st[0] == "unchecked")
        by_key = {_pick_key(x): x for x in result["picks"]}
        gone = {k for k, (st, _) in states.items() if st in (booking_slip.SUSPENDED, booking_slip.MISSING, "started")}
        moved = {k: o for k, (st, o) in states.items()
                 if st == booking_slip.OPEN and o and abs(o - by_key[k]["odds"]) >= 0.005}
        if not gone and not moved:
            break
        for k in gone:
            x = by_key[k]
            check["removed"].append({"home": x["home"], "away": x["away"], "label": x["label"],
                                     "reason": {"started": "Already started",
                                                booking_slip.MISSING: "No longer offered on SportyBet"}
                                     .get(states[k][0], "Suspended on SportyBet")})
        check["repriced"] += len(moved)
        groups = [[o for o in g if _pick_key(asdict_option(o)) not in gone] for g in groups]
        for g in groups:
            for o in g:
                k = _pick_key(asdict_option(o))
                if k in moved:
                    o.odds, o.odds_source = round(moved[k], 2), "sportybet"
        result = await _solve(groups, lo, hi, target, max_games)
    return result, check


async def _optimize_request(body: Dict[str, Any],
                             window: Optional[Tuple[datetime, datetime]] = None) -> Dict[str, Any]:
    """The optimizer endpoint's work, for the daily slips too. `window` (UTC
    start, end; server-side only): matches kicking off in it, from now on,
    in place of `days` from today."""
    import booking_slip
    import optimizer
    try:
        lo, hi = float(body.get("min_odds", 2)), float(body.get("max_odds", 5))
        target = float(body.get("target_odds") or (lo * hi) ** 0.5)
        max_games = int(body.get("max_games", optimizer.MAX_GAMES))
        min_prob = min(0.95, max(0.5, float(body.get("min_prob", 0.6))))
        # Optional cap on each pick's price (the daily slips use it): picks at or above it are left out
        max_leg = float(body["max_leg_odds"]) if body.get("max_leg_odds") else None
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
    # Any of football (default), basketball, tennis, table tennis: the other
    # sports' picks are the lines SportyBet offers on its listed matches, all bookable
    on = set(_optimizer_sports(body)) - set(body.get("_off") or body.get("_rk_off") or [])
    if body.get("no_racket") and len(on) > 1:
        on -= set(RK_SPORTS)
    if not on:
        raise HTTPException(status_code=403, detail="None of the sports you picked is open to your account")
    # One sport alone gets its own "nothing found" messages
    sport = next(iter(on)) if len(on) == 1 else "mixed"
    bb_families = {str(m) for m in body.get("bb_markets") or []} or None
    rk_families = {"tennis": {str(m) for m in body.get("tn_markets") or []} or None,
                   "table_tennis": {str(m) for m in body.get("tt_markets") or []} or None}
    rk_on = [rk for rk in RK_SPORTS if rk in on]

    now = datetime.now(timezone.utc)
    today, last = now.date().isoformat(), (now.date() + timedelta(days=days - 1)).isoformat()
    kicked_off = now.strftime("%H:%M")
    def in_window(p: Dict) -> bool:
        try:
            k = datetime.fromisoformat(f"{p['date']}T{p.get('time') or '12:00'}:00+00:00")
        except (KeyError, TypeError, ValueError):
            return False
        return max(window[0], now) < k < window[1]

    upcoming = [p for p in (_predictions_cache if "football" in on else [])
                if p.get("sport") in (None, "football")
                and (in_window(p) if window else
                     today <= p.get("date", "") <= last
                     and not (p.get("date") == today and (p.get("time") or "99:99") <= kicked_off))
                and (not leagues or p.get("league") in leagues)]
    # A club the model has (next to) no matches for: its chances are mostly
    # guesswork, so the match stays out (the page lists what was left out)
    thin = [{k: p.get(k) for k in ("home", "away", "date", "time", "league", "league_name", "flag")}
            for p in upcoming if p.get("thin_history")]
    upcoming = [p for p in upcoming if not p.get("thin_history")]
    # Bookable = linked to a SportyBet event. Asked of the stored links, not
    # the prediction's "sportybet" flag: a predictions rebuild replaces the
    # predictions (flags and all) minutes before the next linking run.
    linked = {id(p): _linked_event(p) for p in upcoming}
    preds = [p for p in upcoming if not bookable_only or linked[id(p)]]
    bb_preds = [] if "basketball" not in on else [
        p for p in _bb_upcoming()
        if (in_window(p) if window else today <= p["date"] <= last) and (not leagues or p.get("league") in leagues)]
    if bookable_only and upcoming and not preds and sport == "football":
        return {"error": (f"SportyBet hasn't listed any of the {len(upcoming)} matches in the next {days} "
                          f"day{'s' if days != 1 else ''} yet (or we haven't linked them since the last restart). "
                          "Untick \"Only matches SportyBet lists\", or pick more days."),
                "matches_considered": 0, "target": [lo, hi], "target_odds": target, "thin_history": thin}
    # Bookable-only slips skip markets SportyBet hasn't confirmed yet (a code
    # couldn't take those picks)
    bookable = _bookable_markets() if bookable_only else None
    # Markets the weekly accuracy review paused (or the admin switched off)
    paused = _paused_markets()
    if markets and markets <= paused and sport == "football":
        import market_review
        return {"error": (f"{market_review.names(sorted(markets))} {'is' if len(markets) == 1 else 'are'} paused: "
                          "recent picks came in less often than we said, so we're not using "
                          f"{'it' if len(markets) == 1 else 'them'} until the accuracy check clears. Add other markets."),
                "paused": sorted(markets), "matches_considered": 0, "target": [lo, hi], "target_odds": target, "thin_history": thin}

    def allowed(market: str, code: str) -> bool:
        if market in paused:
            return False
        if market in only and code not in only[market]:
            return False  # a line the user left out
        return bookable is None or bookable(market, code)
    # Matches without corner/card stats (internationals) get SportyBet-implied lines
    pairs = [(_with_priced_set_pieces(p, linked[id(p)]), linked[id(p)]) for p in preds]
    preds = [p for p, _ in pairs]
    groups = [optimizer.candidates(p, ev, min_prob, markets, allowed) for p, ev in pairs]
    if (not markets or "anytime_scorer" in markets) and not body.get("no_props"):
        # Anytime goalscorers priced on the match's SportyBet page (_fb_props_refresh)
        for g, (p, ev) in zip(groups, pairs):
            for x in _props_fb.get(str((ev or {}).get("eventId") or ""), []):
                if min_prob <= x["prob"] < 0.995 and x["odds"] > 1.01 and "anytime_scorer" not in paused:
                    g.append(optimizer.Option(p["home"], p["away"], p["date"], p.get("time") or "",
                                              p.get("league_name") or "", "anytime_scorer", x["market_name"],
                                              x["code"], x["label"], x["prob"], x["odds"], "sportybet", sb=x["sb"],
                                              league_id=str(p.get("league") or "")))
    if bb_preds:
        import basketball_predictions
        groups += [basketball_predictions.options(p, min_prob, bb_families, lambda m, c: m not in paused)
                   for p in bb_preds]
    rk_preds: List[Dict] = []
    if rk_on:
        import racket_predictions
        for rk in rk_on:
            ps = [p for p in _rk_upcoming(rk)
                  if (in_window(p) if window else today <= p["date"] <= last)
                  and (not leagues or p.get("league") in leagues)]
            rk_preds += ps
            groups += [racket_predictions.options(p, min_prob, rk_families[rk], lambda m, c: m not in paused)
                       for p in ps]
    if max_leg:
        groups = [[o for o in g if o.odds < max_leg] for g in groups]
    if bookable_only:
        # Shots are on some matches and lines only: bookable when SportyBet priced that very line
        groups = [[o for o in g if o.market not in booking_slip.LISTED_ONLY or o.odds_source == "sportybet"]
                  for g in groups]
    considered = sum(1 for g in groups if g)
    if considered == 0 and sport == "basketball":
        return {"error": (f"None of the {len(bb_preds)} basketball matches in the next {days} day{'s' if days != 1 else ''} "
                          f"has a line our model rates {min_prob:.0%} or more"
                          + (" in the markets you chose" if bb_families else "")
                          + ". Lower the minimum confidence, add markets or pick more days."
                          if bb_preds else "No basketball matches listed on SportyBet in those days yet."),
                "matches_considered": 0, "target": [lo, hi], "target_odds": target, "thin_history": thin}
    if considered == 0 and sport in RK_SPORTS:
        name = "tennis" if sport == "tennis" else "table tennis"
        return {"error": (f"None of the {len(rk_preds)} {name} matches in the next {days} day{'s' if days != 1 else ''} "
                          f"has a line our model rates {min_prob:.0%} or more"
                          + (" in the markets you chose" if rk_families[sport] else "")
                          + ". Lower the minimum confidence, add markets or pick more days."
                          if rk_preds else f"No {name} matches listed on SportyBet in those days yet."),
                "matches_considered": 0, "target": [lo, hi], "target_odds": target, "thin_history": thin}
    if considered == 0:
        reasons = optimizer.why_empty(preds, markets, min_prob, bookable, only)
        return {"error": _explain_empty(reasons, len(preds), days, min_prob), "reasons": reasons,
                "matches_considered": 0, "target": [lo, hi], "target_odds": target, "thin_history": thin}
    # Every pick checked on SportyBet's current match page (_solve_checked)
    events = {(p["home"], p["away"], p.get("date")): ev for p, ev in pairs}
    if body.get("no_live_check"):
        result = await _solve(groups, lo, hi, target, max_games)
        live_check: Dict[str, Any] = {"checked": 0, "removed": [], "repriced": 0, "unchecked": 0}
    else:
        result, live_check = await _solve_checked(groups, lo, hi, target, max_games, events, _sb_market_map())
    if result is None and live_check["removed"]:
        return {"error": (f"{len(live_check['removed'])} of the picks were suspended on SportyBet and "
                          "no slip without them reaches the target. Try again in a few minutes, or widen the target."),
                "live_check": live_check, "matches_considered": considered, "target": [lo, hi], "target_odds": target, "thin_history": thin}
    if result is None:
        return {"error": (f"No slip from {considered} matches gets near {target:,.2f}x. "
                          "Allow more games or days, add markets, or lower the minimum confidence."),
                "matches_considered": considered, "target": [lo, hi], "target_odds": target, "thin_history": thin}
    # Which picks a SportyBet code can take: the match is linked to a SportyBet
    # event and SportyBet confirmed the market (shots, for one, it doesn't offer)
    ok = bookable or _bookable_markets()
    result["live_check"] = live_check
    for pick in result.get("picks") or []:
        if pick.get("sb"):   # basketball: a line SportyBet offers, with its ids
            pick["bookable"] = True
            continue
        pick["bookable"] = (bool(events.get((pick["home"], pick["away"], pick["date"]))) and ok(pick["market"], pick["code"])
                            and (pick["market"] not in booking_slip.LISTED_ONLY or pick["odds_source"] == "sportybet"))
    result["bookable_picks"] = sum(1 for pick in result.get("picks") or [] if pick["bookable"])
    return {**result, "target": [lo, hi], "target_odds": round(target, 2), "matches_considered": considered, "thin_history": thin}


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


# ── Daily odds (daily_slips.py) ─────────────────────────────────────────────
DAILY_KEY = "betiq:daily:{}"
DAILY_RECORD_KEY = "betiq:daily:record"
_daily_lock = asyncio.Lock()
_daily_memory: Dict[str, Dict[str, Any]] = {}   # when the database is down: still one set a day


def _daily_load(r, day: str) -> Optional[Dict[str, Any]]:
    if r:
        try:
            raw = r.get(DAILY_KEY.format(day))
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    return _daily_memory.get(day)


def _daily_save(r, doc: Dict[str, Any]) -> None:
    import daily_slips
    if doc["date"] >= daily_slips.today():
        for d in [d for d in _daily_memory if d < daily_slips.today()]:
            del _daily_memory[d]
        _daily_memory[doc["date"]] = doc
    if r:
        try:
            r.set(DAILY_KEY.format(doc["date"]), json.dumps(doc), ex=daily_slips.KEEP_DAYS * 86400)
        except Exception as e:
            print(f"[Daily] couldn't save {doc['date']}: {e}")


async def _daily_slip(target: float, day: str) -> Dict[str, Any]:
    """One slip for the Lagos day `day`, from its matches not kicked off yet."""
    import daily_slips
    built: Dict[str, Any] = {}
    for attempt in daily_slips.ATTEMPTS:
        try:
            import access
            feats = _features()
            # Racket sports not open to everyone stay out of the daily slips
            off = [rk for rk in RK_SPORTS if feats[access.SPORT_FEATURES[rk]]["state"] != "on"]
            res = await _optimize_request({**daily_slips.request(target, attempt), "_rk_off": off},
                                          window=daily_slips.window(day))
        except HTTPException as e:
            res = {"error": str(e.detail)}
        built = daily_slips.slip(target, res, attempt)
        if built["status"] != "none":
            break
    return built


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
        slips = [await _daily_slip(target, day) for target in daily_slips.TARGETS]
        doc = {"date": day, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "slips": slips}
        await _book_daily(doc)
        _daily_save(r, doc)
        print(f"[Daily] {day}: " + ", ".join(
            f"{int(s['target'])}x " + ((s.get("booking") or {}).get("code") or ("no slip" if s["status"] == "none" else "no code yet"))
            for s in slips))
        return doc


async def _book_daily(doc: Dict[str, Any]) -> bool:
    """Book each slip that has no code yet (and picks still to play) on
    SportyBet; True if any slip's booking changed."""
    import booking_slip
    import daily_slips
    import sportybet
    now = daily_slips.now_utc()
    changed = False
    for s in doc.get("slips") or []:
        if not daily_slips.needs_booking(s, now):
            continue
        tries = (s.get("booking") or {}).get("tries", 0)
        sent: List[Dict[str, Any]] = []
        try:
            sent = booking_slip.validate(daily_slips.selections(s, now))
            res = await booking_slip.to_sportybet(
                sent, sportybet.fetch_events_for_date, sportybet.find_event, sportybet.share_selections,
                linked=_linked_event, market_map=_sb_market_map())
        except Exception as e:
            print(f"[Daily] booking the {s.get('target')}x slip failed: {e}")
            res = {"error": "Couldn't reach SportyBet to book this slip."}
        s["booking"] = {**daily_slips.booking(res, sent, now.isoformat(timespec="seconds")), "tries": tries + 1}
        # The same slip as a football.com code (SportyBet's platform: the same picks)
        if res.get("code"):
            try:
                fc = await booking_slip.to_sportybet(
                    sent, sportybet.fetch_events_for_date, sportybet.find_event, sportybet.share_on("football_com"),
                    linked=_linked_event, market_map=_sb_market_map(), platform="football_com")
                if fc.get("code"):
                    s["booking"]["football_com"] = {"code": fc["code"], "share_url": fc.get("share_url"),
                                                    "total_odds": fc.get("total_odds")}
            except Exception as e:
                print(f"[Daily] football.com code for the {s.get('target')}x slip failed: {e}")
        changed = True
    return changed


def _grade_daily(r, doc: Dict[str, Any]) -> bool:
    """Settle a day's slips from the results; a slip that settles counts
    towards the record once. True if anything changed."""
    import daily_slips
    if not r:
        return False
    result_for = _leg_results(r, {})
    changed = False
    for s in doc.get("slips") or []:
        before = s.get("status")
        if daily_slips.grade(s, result_for):
            changed = True
            if before == "pending" and s["status"] in ("won", "lost"):
                r.hincrby(DAILY_RECORD_KEY, f"{int(s['target'])}:{s['status']}", 1)
    return changed


async def _remake_cut(doc: Dict[str, Any]) -> bool:
    """A new slip in place of each cut one (a pick lost), from the day's
    matches that haven't kicked off; _book_daily then books it. True if
    anything changed."""
    import daily_slips
    changed = False
    for i, s in enumerate(doc.get("slips") or []):
        if not daily_slips.needs_remake(doc, s):
            continue
        at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        new = await _daily_slip(s["target"], doc["date"])
        made = daily_slips.remake(doc, i, new, at)
        changed = True
        print(f"[Daily] {doc['date']}: {int(s['target'])}x cut by {daily_slips.cut_by(s)}: "
              + ("new slip made" if made else f"no new slip ({new.get('error') or 'no matches left'})"))
    return changed


def _daily_public(s: Dict[str, Any]) -> Dict[str, Any]:
    """A slip as the page gets it: the booking without its bookkeeping."""
    b = s.get("booking")
    if not b:
        return s
    return {**s, "booking": {k: b.get(k) for k in ("code", "share_url", "total_odds", "booked", "of", "error", "at", "football_com")}}


@app.get("/api/daily-slips")
async def get_daily_slips(day: str = Query("", alias="date"), _access=Depends(require_feature("daily_slips"))):
    """A day's 10x / 15x / 20x slips with their SportyBet booking codes, each
    pick graded and, while it's being played, its live score; and the record
    of every slip so far, and the day's cut slips (each replaced by a new
    one). Read-only: the slips and codes are made by the daily tick
    (_daily_tick) just before midnight in Lagos, not by visitors."""
    import daily_slips
    today = daily_slips.today()
    d = _date_param(day or today).isoformat()
    r = _get_redis()
    doc = await asyncio.to_thread(_daily_load, r, d)
    if doc and r:
        async with _daily_lock:
            if await asyncio.to_thread(_grade_daily, r, doc):
                await asyncio.to_thread(_daily_save, r, doc)
        await asyncio.to_thread(lambda: [_attach_live(r, [{"status": "pending", "legs": s.get("picks") or []}])
                                         for s in doc.get("slips") or []])
    record: Dict[str, Dict[str, int]] = {}
    for k, v in ((r.hgetall(DAILY_RECORD_KEY) if r else None) or {}).items():
        k = k.decode() if isinstance(k, bytes) else str(k)
        target, _, outcome = k.partition(":")
        record.setdefault(target, {"won": 0, "lost": 0})[outcome] = int(v)
    return {"date": d, "today": today, "built_at": (doc or {}).get("built_at"),
            "slips": [_daily_public(s) for s in (doc or {}).get("slips") or []],
            "cut": [_daily_public(s) for s in (doc or {}).get("cut") or []], "record": record,
            "min_prob": daily_slips.MIN_PROB, "targets": list(daily_slips.TARGETS),
            # Midnight in Lagos, in UTC: when a day's slips are out
            "publish_at_utc": daily_slips.window(today)[0].strftime("%H:%M"),
            "retry_minutes": daily_slips.RETRY_MINUTES}


@app.post("/api/daily-slips/track")
async def track_daily_slip(request: Request, body: Dict[str, Any], _access=Depends(require_feature("daily_slips"))):
    """Add a daily slip's booking code to the account's tickets (Dashboard →
    Tickets), graded leg by leg like any code it books. Body: {date, target}."""
    import auth
    import daily_slips
    import tickets
    uid = await auth.require_user(request, str(body.get("uid") or ""))
    d = _date_param(str(body.get("date") or daily_slips.today())).isoformat()
    try:
        target = float(body.get("target"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="target must be 10, 15, 20, 50 or 100")
    doc = _daily_load(_get_redis(), d)
    s = next((x for x in (doc or {}).get("slips") or [] if float(x.get("target", 0)) == target), None)
    code = ((s or {}).get("booking") or {}).get("code")
    if not code:
        raise HTTPException(status_code=404, detail="That slip has no booking code yet.")
    sels, booked = daily_slips.ticket_legs(s)
    b = s["booking"]
    _record_ticket(uid, tickets.new_ticket(code, sels, booked, "daily", b.get("share_url"), b.get("total_odds"),
                                           datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return {"tracked": True, "code": code}


async def _daily_tick() -> None:
    """Every few minutes, for today and (from just before midnight in Lagos)
    tomorrow: make the day's slips if they aren't made yet, settle them, make
    a new slip in place of any that was cut, book any slip without a code,
    and post today's."""
    import daily_slips
    for day in daily_slips.days_to_run(daily_slips.now_utc()):
        try:
            r = _get_redis()
            doc = _daily_load(r, day)
            if doc is None:
                await _build_daily(day)
                continue
            async with _daily_lock:
                changed = _grade_daily(r, doc)
                changed = await _remake_cut(doc) or changed
                changed = await _book_daily(doc) or changed
                if changed:
                    _daily_save(r, doc)
            await _maybe_post_daily(doc)
        except Exception as e:
            print(f"[Daily] tick for {day} failed: {e}")


# ── The daily odds on X and Telegram (x_poster.py, telegram_poster.py) ──
# Each channel module: KEYS, MAX_TRIES, configured(), missing(),
# compose(doc, site) -> text | None, async publish(text) -> {ok, id, url, error}
POST_CHANNELS = {"x": "x_poster", "telegram": "telegram_poster"}
POST_CONFIG_KEY = "betiq:config:{}"         # channel → {"enabled": bool}
POST_DAY_KEY = "betiq:post:{}:{}"           # channel, date → that day's post
POST_GRACE_MINUTES = 60  # after the day starts (midnight, Lagos): post then even if a slip still waits for its code


def _channel(ch: str):
    import importlib
    if ch not in POST_CHANNELS:
        raise HTTPException(status_code=404, detail="Unknown channel")
    return importlib.import_module(POST_CHANNELS[ch])


def _site_url() -> str:
    return FRONTEND_URL if FRONTEND_URL.startswith("https://") else "https://predict-withbetiq.vercel.app"


def _post_enabled(ch: str) -> bool:
    r = _get_redis()
    try:
        return bool(r and json.loads(r.get(POST_CONFIG_KEY.format(ch)) or "{}").get("enabled"))
    except Exception:
        return False


def _post_day(r, ch: str, day: str) -> Dict[str, Any]:
    try:
        return json.loads(r.get(POST_DAY_KEY.format(ch, day)) or "{}") if r else {}
    except Exception:
        return {}


def _post_ready(doc: Dict[str, Any], now: datetime) -> bool:
    """Every slip that can still be booked has its code, or it's
    POST_GRACE_MINUTES into the day."""
    import daily_slips
    if now >= daily_slips.window(doc["date"])[0] + timedelta(minutes=POST_GRACE_MINUTES):
        return True
    return all((s.get("booking") or {}).get("code") or not daily_slips.open_picks(s, now)
               for s in doc.get("slips") or [] if s.get("status") != "none")


async def _post_daily(ch: str, doc: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
    """Post the day's slips to one channel, once a day (`force`: again). The
    day's state: {status: posted|failed|skipped, id, url, text, error, tries, at}."""
    mod = _channel(ch)
    r = _get_redis()
    state = _post_day(r, ch, doc["date"])
    if state.get("status") == "posted" and not force:
        return state
    if not mod.configured():
        return {"status": "failed", "error": f"Missing in .env: {', '.join(mod.missing())}"}
    text = mod.compose(doc, _site_url())
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not text:
        state = {"status": "skipped", "error": "No slip with a booking code to post", "at": at}
    else:
        tries = int(state.get("tries") or 0) + 1
        sent = await mod.publish(text)
        if sent.get("ok"):
            state = {"status": "posted", "id": sent.get("id"), "url": sent.get("url"), "text": text, "at": at, "tries": tries}
            print(f"[Post:{ch}] posted the daily odds: {sent.get('url') or sent.get('id')}")
        else:
            state = {"status": "failed", "error": sent.get("error"), "text": text, "at": at, "tries": tries}
            print(f"[Post:{ch}] failed ({tries}/{mod.MAX_TRIES}): {sent.get('error')}")
    if r:
        r.set(POST_DAY_KEY.format(ch, doc["date"]), json.dumps(state), ex=14 * 86400)
    return state


async def _maybe_post_daily(doc: Dict[str, Any]) -> None:
    """From the daily tick: post today's slips to each switched-on channel once they're booked."""
    import daily_slips
    if doc.get("date") != daily_slips.today() or not _post_ready(doc, daily_slips.now_utc()):
        return
    for ch in POST_CHANNELS:
        mod = _channel(ch)
        if not await asyncio.to_thread(_post_enabled, ch) or not mod.configured():
            continue
        state = await asyncio.to_thread(_post_day, _get_redis(), ch, doc["date"])
        if state.get("status") in ("posted", "skipped") or int(state.get("tries") or 0) >= mod.MAX_TRIES:
            continue
        try:
            await _post_daily(ch, doc)
        except Exception as e:
            print(f"[Post:{ch}] error: {e}")


def _post_alerts() -> List[Dict[str, str]]:
    """Banner lines for channels that gave up on today's post."""
    import daily_slips
    out = []
    r = _get_redis()
    for ch in POST_CHANNELS:
        if not _post_enabled(ch):
            continue
        mod = _channel(ch)
        state = _post_day(r, ch, daily_slips.today())
        if state.get("status") == "failed" and int(state.get("tries") or 0) >= mod.MAX_TRIES:
            name = "X" if ch == "x" else "Telegram"
            out.append({"level": "warn", "title": f"Today's daily odds weren't posted on {name}",
                        "detail": f"{state.get('error')}. Fix it, then use Post now in Messaging → Daily odds on {name}."})
    return out


@app.get("/api/admin/post/{ch}")
async def admin_post_channel(ch: str, _admin: str = Depends(require_admin)):
    """One channel: keys, switch, today's post and a preview."""
    import daily_slips
    mod = _channel(ch)
    today = daily_slips.today()
    r = _get_redis()
    doc = _daily_load(r, today)
    return {"channel": ch, "configured": mod.configured(), "missing": mod.missing(), "enabled": _post_enabled(ch),
            "today": _post_day(r, ch, today), "preview": mod.compose(doc, _site_url()) if doc else None,
            "post_by_utc": (daily_slips.window(today)[0] + timedelta(minutes=POST_GRACE_MINUTES)).strftime("%H:%M"),
            "max_tries": mod.MAX_TRIES}


@app.put("/api/admin/post/{ch}")
async def put_admin_post_channel(ch: str, body: Dict[str, Any], _admin: str = Depends(require_admin)):
    """Body: {enabled}: post the daily odds to this channel each morning."""
    _channel(ch)
    r = _get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Couldn't save: the database isn't connected")
    enabled = bool((body or {}).get("enabled"))
    r.set(POST_CONFIG_KEY.format(ch), json.dumps({"enabled": enabled}))
    _audit(_admin, "daily_posting", channel=ch, enabled=enabled)
    return await admin_post_channel(ch, _admin)


@app.post("/api/admin/post/{ch}/now")
async def admin_post_now(ch: str, body: Dict[str, Any] = None, _admin: str = Depends(require_admin)):
    """Post today's daily odds to this channel now. Body: {again?: true} to
    post even if today's post already went out."""
    mod = _channel(ch)
    if not mod.configured():
        raise HTTPException(status_code=400, detail=f"Missing in .env: {', '.join(mod.missing())}")
    import daily_slips
    doc = _daily_load(_get_redis(), daily_slips.today())
    if not doc:
        raise HTTPException(status_code=404, detail="Today's slips aren't made yet")
    state = await _post_daily(ch, doc, force=bool((body or {}).get("again")))
    _audit(_admin, "daily_post", channel=ch, status=state.get("status"))
    return state


@app.post("/api/optimizer/code")
async def optimize_code(body: Dict[str, Any], _access=Depends(require_feature("code_check"))):
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

    bookable, paused = _bookable_markets(), _paused_markets()
    report = await asyncio.to_thread(
        code_check.analyse, selections, find,
        _linked_event, confirmed, lambda m, c: m not in paused and bookable(m, c))
    return {"code": code.upper(), **report}


@app.post("/api/booking/convert")
async def convert_slip(request: Request, body: Dict[str, Any]):
    """
    Turn the bet slip into a booking code on the chosen platform.
    Body: {"platform": "sportybet" | "football_com", "selections": [{home, away, date, market, code, label?}]}
    """
    import booking_slip
    import sportybet

    platform = body.get("platform", "sportybet")
    if platform not in sportybet.PLATFORMS:
        raise HTTPException(status_code=400, detail="Booking codes are made on SportyBet or football.com")
    try:
        selections = booking_slip.validate(body.get("selections"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # football.com is SportyBet's platform: the same events and ids, its own code
    result = booking_slip.name_platform(await booking_slip.to_sportybet(
        selections, sportybet.fetch_events_for_date, sportybet.find_event, sportybet.share_on(platform),
        linked=_linked_event, market_map=_sb_market_map(), platform=platform))
    # Every code a signed-in account makes becomes a ticket, settled leg by
    # leg as the results come in (Dashboard → Tickets)
    if result.get("code"):
        import auth
        import tickets
        uid = await auth.optional_user(request)
        if not uid and auth.unverified_uid_allowed():
            uid = str(body.get("uid") or "")[:64] or None
        # Signed out: kept too, so the admin sees every code the site makes
        source = body.get("source") if body.get("source") in TICKET_SOURCES else "other"
        try:
            ticket = tickets.new_ticket(
                result["code"], selections, result.get("picks") or [], source, result.get("share_url"),
                result.get("total_odds"), datetime.now(timezone.utc).isoformat(timespec="seconds"))
            ticket["platform"] = platform
            _record_ticket(uid or ANON_UID, ticket)
            if uid:
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


# ── Free trial for new accounts (trial.py) ──
# The site asks for it (POST /api/trial/start) on a new account's visits; the
# trial is written to the account's Clerk metadata, so every plan check
# covers it, and recorded against its email.
TRIAL_KEY = "betiq:config:trial"
TRIAL_EMAIL_KEY = "betiq:trial:email:{}"    # hashed mailbox → the account that had the trial
TRIAL_STATS_KEY = "betiq:trial:stats"


def _trial_config() -> Dict[str, Any]:
    import trial
    r = _get_redis()
    if r:
        try:
            return trial.public({**trial.DEFAULTS, **json.loads(r.get(TRIAL_KEY) or "{}")})
        except Exception:
            pass
    return dict(trial.DEFAULTS)


@app.get("/api/trial")
async def get_trial():
    """Public: whether new accounts get a free trial, how long, of which plan."""
    return _trial_config()


@app.put("/api/admin/trial")
async def put_trial(body: Dict[str, Any], _admin: str = Depends(require_admin)):
    """Body: {enabled?, days? (1–30), tier? (lite|premium)}. Switching it on
    starts it for accounts made from now on."""
    import trial
    try:
        cfg = trial.settings(body or {}, _trial_config())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    r = _get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Couldn't save: the database isn't connected")
    r.set(TRIAL_KEY, json.dumps(cfg))
    _audit(_admin, "trial", **{k: cfg[k] for k in ("enabled", "days", "tier")})
    return cfg


@app.get("/api/admin/trial/stats")
async def trial_stats(_admin: str = Depends(require_admin)):
    """Trials started, and refused by reason (email already used, throwaway…)."""
    r = _get_redis()
    raw = (r.hgetall(TRIAL_STATS_KEY) if r else None) or {}
    return {(k.decode() if isinstance(k, bytes) else str(k)): int(v) for k, v in raw.items()}


@app.post("/api/trial/start")
async def start_trial(request: Request):
    """Start the signed-in account's free trial if it qualifies (trial.check).
    Returns {started, tier, days, expires} or {started: false, reason, message}."""
    import auth
    import trial
    if not auth.premium_enforced():
        return {"started": False, "reason": "not_configured", "message": None}
    uid = await auth.require_user(request)
    cfg = _trial_config()
    if not cfg["enabled"]:
        return {"started": False, "reason": "trial_off", "message": None}
    status, user = await auth.clerk_record(uid)
    if status != 200:
        raise HTTPException(status_code=503, detail="Couldn't reach the sign-in service. Try again.")
    r = _get_redis()
    if not r:
        return {"started": False, "reason": "unavailable", "message": None}
    verdict = trial.check(user, cfg, lambda d: r.get(TRIAL_EMAIL_KEY.format(d)))
    reason = verdict.get("reason")
    if reason:
        if reason in trial.MESSAGES:
            r.hincrby(TRIAL_STATS_KEY, f"refused:{reason}", 1)
        if reason in trial.FINAL:
            # Won't change: stop the site asking on every visit
            await auth.set_public_metadata(uid, {"trial_used": True, "trial_denied": reason})
        return {"started": False, "reason": reason, "message": trial.MESSAGES.get(reason)}
    expires = verdict["expires"].isoformat().replace("+00:00", "Z")
    code = await auth.set_public_metadata(uid, {"subscription": cfg["tier"], "subscription_expires": expires,
                                                "trial": True, "trial_used": True})
    if code != 200:
        raise HTTPException(status_code=503, detail="Couldn't start your trial just now. Try again.")
    r.set(TRIAL_EMAIL_KEY.format(verdict["email"]), uid, ex=trial.KEEP_DAYS * 86400)
    r.hincrby(TRIAL_STATS_KEY, "started", 1)
    print(f"[Trial] {uid}: {cfg['tier']} until {expires}")
    return {"started": True, "tier": cfg["tier"], "days": cfg["days"], "expires": expires}


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
    scheduler.add_job(_crest_index_refresh, "interval", hours=24, id="crest_index", max_instances=1, misfire_grace_time=600,
                      coalesce=True, next_run_time=datetime.now() + timedelta(minutes=4))
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
    from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

    def _job_done(ev):
        try:
            perf.record_job(ev.job_id, (datetime.now(ev.scheduled_run_time.tzinfo) - ev.scheduled_run_time).total_seconds())
        except Exception:
            pass
    scheduler.add_listener(_job_done, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
    asyncio.get_event_loop().create_task(perf.watch_loop(_get_redis))
    scheduler.start()
    # Which code this server runs (the "Basketball data" workflow's read_probe job prints it)
    if r:
        try:
            r.set("betiq:server:boot", json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                   "jobs": sorted(j.id for j in scheduler.get_jobs())}), ex=30 * 86400)
        except Exception as e:
            print(f"[Boot] couldn't record: {e}")


@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown()
    _flush_traffic()  # keep the last few minutes of page views
