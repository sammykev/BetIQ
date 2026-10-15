"""
Paywall switch, free trial (trial.py) and the manual refresh.

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import json
import time
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from typing import Any, Dict

import main

router = APIRouter()


@router.get("/api/config/paywall")
async def get_paywall_state():
    """Public endpoint — returns current paywall on/off state."""
    r = main._get_redis()
    if r:
        try:
            val = r.get(main.PAYWALL_KEY)
            if val is not None:
                return {"enabled": val == "true"}
        except Exception:
            pass
    return {"enabled": False}   # default: paywall OFF when Redis unavailable


@router.post("/api/config/paywall")
async def set_paywall_state(body: Dict[str, Any], _admin: str = Depends(main.require_admin)):
    """Admin-only endpoint — toggle paywall on or off."""

    enabled = bool(body.get("enabled", True))
    r = main._get_redis()
    if r:
        try:
            # No TTL — persists forever in Redis
            r.set(main.PAYWALL_KEY, "true" if enabled else "false")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Redis error: {e}")

    print(f"[Admin] Paywall {'enabled' if enabled else 'DISABLED'}")
    main._audit(_admin, "paywall", enabled=enabled)
    return {"enabled": enabled}


def _trial_config() -> Dict[str, Any]:
    import trial
    r = main._get_redis()
    if r:
        try:
            return trial.public({**trial.DEFAULTS, **json.loads(r.get(main.TRIAL_KEY) or "{}")})
        except Exception:
            pass
    return dict(trial.DEFAULTS)


@router.get("/api/trial")
async def get_trial():
    """Public: whether new accounts get a free trial, how long, of which plan."""
    return main._trial_config()


@router.put("/api/admin/trial")
async def put_trial(body: Dict[str, Any], _admin: str = Depends(main.require_admin)):
    """Body: {enabled?, days? (1–30), tier? (lite|premium)}. Switching it on
    starts it for accounts made from now on."""
    import trial
    try:
        cfg = trial.settings(body or {}, main._trial_config())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    r = main._get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Couldn't save: the database isn't connected")
    r.set(main.TRIAL_KEY, json.dumps(cfg))
    main._audit(_admin, "trial", **{k: cfg[k] for k in ("enabled", "days", "tier")})
    return cfg


@router.get("/api/admin/trial/stats")
async def trial_stats(_admin: str = Depends(main.require_admin)):
    """Trials started, and refused by reason (email already used, throwaway…)."""
    r = main._get_redis()
    raw = (r.hgetall(main.TRIAL_STATS_KEY) if r else None) or {}
    return {(k.decode() if isinstance(k, bytes) else str(k)): int(v) for k, v in raw.items()}


@router.post("/api/trial/start")
async def start_trial(request: Request):
    """Start the signed-in account's free trial if it qualifies (trial.check).
    Returns {started, tier, days, expires} or {started: false, reason, message}."""
    import auth
    import trial
    if not auth.premium_enforced():
        return {"started": False, "reason": "not_configured", "message": None}
    uid = await auth.require_user(request)
    cfg = main._trial_config()
    if not cfg["enabled"]:
        return {"started": False, "reason": "trial_off", "message": None}
    status, user = await auth.clerk_record(uid)
    if status != 200:
        raise HTTPException(status_code=503, detail="Couldn't reach the sign-in service. Try again.")
    r = main._get_redis()
    if not r:
        return {"started": False, "reason": "unavailable", "message": None}
    verdict = trial.check(user, cfg, lambda d: r.get(main.TRIAL_EMAIL_KEY.format(d)))
    reason = verdict.get("reason")
    if reason:
        if reason in trial.MESSAGES:
            r.hincrby(main.TRIAL_STATS_KEY, f"refused:{reason}", 1)
        if reason in trial.FINAL:
            # Won't change: stop the site asking on every visit
            await auth.set_public_metadata(uid, {"trial_used": True, "trial_denied": reason})
        return {"started": False, "reason": reason, "message": trial.MESSAGES.get(reason)}
    expires = verdict["expires"].isoformat().replace("+00:00", "Z")
    code = await auth.set_public_metadata(uid, {"subscription": cfg["tier"], "subscription_expires": expires,
                                                "trial": True, "trial_used": True})
    if code != 200:
        raise HTTPException(status_code=503, detail="Couldn't start your trial just now. Try again.")
    r.set(main.TRIAL_EMAIL_KEY.format(verdict["email"]), uid, ex=trial.KEEP_DAYS * 86400)
    r.hincrby(main.TRIAL_STATS_KEY, "started", 1)
    print(f"[Trial] {uid}: {cfg['tier']} until {expires}")
    return {"started": True, "tier": cfg["tier"], "days": cfg["days"], "expires": expires}


@router.post("/api/refresh")
async def refresh_predictions(request: Request, background_tasks: BackgroundTasks):
    """Rebuild predictions. Admins any time; the public button (the home
    page's retry when nothing is cached) at most once per REFRESH_COOLDOWN —
    a run takes minutes of the server's CPU."""
    via, uid = await main._admin_identity(request)
    if main._is_training:
        return {"message": "Already refreshing", "started": False}
    if via:
        main._audit(f"clerk:{uid}" if via == "clerk" else via, "refresh")
    if not via:
        if time.time() - main._last_public_refresh < main.REFRESH_COOLDOWN:
            return {"message": "Refreshed recently", "started": False}
        main._last_public_refresh = time.time()
    background_tasks.add_task(main._run_pipeline)
    return {"message": "Refresh started", "started": True}
