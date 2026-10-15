"""
Weekly accuracy review (market_review.py) and league drift check (league_review.py).

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import json
import os
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from typing import Any, Dict

import main

router = APIRouter()


def _restore_review() -> None:
    r = main._get_redis()
    if r and not main._review.get("loaded"):
        try:
            main._review.update(json.loads(r.get(main.REVIEW_KEY) or "{}"), loaded=True)
            import market_review
            if not main._review.get("mode_chosen"):
                # "auto" was only ever the old default: markets are corrected now, not paused
                main._review["mode"] = market_review.DEFAULT_MODE
        except Exception:
            pass


def _save_review() -> bool:
    r = main._get_redis()
    if not r:
        return False
    try:
        r.set(main.REVIEW_KEY, json.dumps({k: v for k, v in main._review.items() if k != "loaded"}))
        return True
    except Exception:
        return False


def _paused_markets() -> set:
    """Markets the optimizer, daily slips and code check leave out now."""
    import market_review
    main._restore_review()
    return market_review.blocked(main._review)


def _run_review() -> Dict[str, Any]:
    """Review every market over the last market_review.WINDOW_DAYS settled days."""
    import market_review
    r = main._get_redis()
    if not r:
        return {"skipped": "no Redis"}
    main._restore_review()
    today = date.today()
    days = main._md_many(r, [(today - timedelta(days=i)).isoformat() for i in range(1, market_review.WINDOW_DAYS + 1)])
    result = market_review.review((e for day in days.values() for e in day.values()),
                                  (main._review.get("latest") or {}).get("paused") or [])
    main._review.update(market_review.remember(main._review, result, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    main._save_review()
    print(f"[Review] {result['matches']} matches · paused {result['paused'] or 'none'} · "
          f"newly {result['newly_paused'] or 'none'} · restored {result['restored'] or 'none'}")
    return result


def _review_view() -> Dict[str, Any]:
    import market_review
    return {"mode": main._review.get("mode", "flag"), "overrides": main._review.get("overrides") or {},
            "latest": main._review.get("latest"), "history": main._review.get("history") or [],
            "blocked": sorted(market_review.blocked(main._review)),
            "markets": [{"market": m, "name": n} for m, n in main._market_names().items()],
            "rules": {"window_days": market_review.WINDOW_DAYS, "min_matches": market_review.MIN_MATCHES,
                      "pause_gap": market_review.PAUSE_GAP, "pause_z": market_review.PAUSE_Z}}


def _market_names() -> Dict[str, str]:
    import optimizer
    return dict(optimizer.MARKET_NAMES)


@router.get("/api/admin/market-review")
async def get_market_review(_admin: str = Depends(main.require_admin)):
    """The latest weekly accuracy review, past ones, and the settings."""
    main._restore_review()
    return main._review_view()


@router.put("/api/admin/market-review")
async def put_market_review(body: Dict[str, Any], _admin: str = Depends(main.require_admin)):
    """Body: {mode?: auto|flag, overrides?: {market: on|off|auto}}."""
    import market_review
    main._restore_review()
    try:
        new = market_review.settings(body or {}, main._review)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    before = {k: main._review.get(k) for k in ("mode", "overrides", "mode_chosen")}
    main._review.update(new)
    if "mode" in (body or {}):
        main._review["mode_chosen"] = True
    if not main._save_review():
        main._review.update(before)
        raise HTTPException(status_code=503, detail="Couldn't save: the database isn't connected")
    main._audit(_admin, "market_review", **new)
    return main._review_view()


@router.get("/api/market-review/paused")
async def paused_markets():
    """Markets left out of slips right now, for the optimizer page's note."""
    import market_review
    main._restore_review()
    latest = {m["market"]: m for m in (main._review.get("latest") or {}).get("markets") or []}
    return {"paused": [{"market": m, "name": main._market_names().get(m, m),
                        "why": "switched off by the admin" if (main._review.get("overrides") or {}).get(m) == "off"
                        else (latest.get(m) or {}).get("why") or market_review.DEFAULT_OFF.get(m)}
                       for m in sorted(market_review.blocked(main._review))]}


def _restore_league_review() -> None:
    r = main._get_redis()
    if r and not main._league_review.get("loaded"):
        try:
            main._league_review.update(json.loads(r.get(main.LEAGUE_REVIEW_KEY) or "{}"), loaded=True)
        except Exception:
            pass


def _save_league_review() -> bool:
    r = main._get_redis()
    if not r:
        return False
    try:
        r.set(main.LEAGUE_REVIEW_KEY, json.dumps({k: v for k, v in main._league_review.items() if k != "loaded"}))
        return True
    except Exception:
        return False


def _paused_leagues() -> set:
    """Leagues the optimizer and daily slips leave out now."""
    import league_review
    main._restore_league_review()
    return league_review.blocked(main._league_review)


def _run_league_review() -> Dict[str, Any]:
    """Check every league over the last league_review.WINDOW_DAYS settled days."""
    import league_review
    r = main._get_redis()
    if not r:
        return {"skipped": "no Redis"}
    main._restore_league_review()
    today = date.today()
    days = main._md_many(r, [(today - timedelta(days=i)).isoformat() for i in range(1, league_review.WINDOW_DAYS + 1)])
    result = league_review.review((e for day in days.values() for e in day.values()),
                                  (main._league_review.get("latest") or {}).get("failing") or [])
    main._league_review.update(league_review.remember(main._league_review, result,
                                                 datetime.now(timezone.utc).isoformat(timespec="seconds")))
    main._save_league_review()
    print(f"[Drift] {result['matches']} matches · failing {result['failing'] or 'none'} · "
          f"newly {result['newly_failing'] or 'none'} · recovered {result['recovered'] or 'none'}")
    return result


def _league_review_view() -> Dict[str, Any]:
    import league_review
    return {"mode": main._league_review.get("mode", "flag"), "overrides": main._league_review.get("overrides") or {},
            "latest": main._league_review.get("latest"), "history": main._league_review.get("history") or [],
            "blocked": sorted(league_review.blocked(main._league_review)),
            "telegram_admin": bool(os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()),
            "rules": {"window_days": league_review.WINDOW_DAYS, "min_matches": league_review.MIN_MATCHES,
                      "pause_gap": league_review.PAUSE_GAP, "pause_z": league_review.PAUSE_Z}}


@router.get("/api/admin/league-review")
async def get_league_review(_admin: str = Depends(main.require_admin)):
    """The latest league drift check, past ones, and the settings."""
    main._restore_league_review()
    return main._league_review_view()


@router.put("/api/admin/league-review")
async def put_league_review(body: Dict[str, Any], _admin: str = Depends(main.require_admin)):
    """Body: {mode?: auto|flag, overrides?: {league: on|off|auto}}."""
    import league_review
    main._restore_league_review()
    try:
        new = league_review.settings(body or {}, main._league_review)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    before = {k: main._league_review.get(k) for k in ("mode", "overrides")}
    main._league_review.update(new)
    if not main._save_league_review():
        main._league_review.update(before)
        raise HTTPException(status_code=503, detail="Couldn't save: the database isn't connected")
    main._audit(_admin, "league_review", **new)
    return main._league_review_view()


async def _review_job() -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(main._run_review)
    except Exception as e:
        print(f"[Review] failed: {e}")
        return {"error": str(e)}


async def _league_review_job() -> Dict[str, Any]:
    try:
        result = await asyncio.to_thread(main._run_league_review)
    except Exception as e:
        print(f"[Drift] failed: {e}")
        return {"error": str(e)}
    import league_review
    text = league_review.message(result, main._league_review.get("mode", "flag")) if "leagues" in result else None
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
