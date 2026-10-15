"""
Push notification subscriptions and the daily-odds notification.

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from fastapi import APIRouter, Request
from typing import Any, Dict

import main

router = APIRouter()


@router.get("/api/push/public-key")
async def push_public_key():
    key = os.getenv("VAPID_PUBLIC_KEY", "")
    return {"public_key": key}


def _valid_push_subscription(sub: Any) -> bool:
    from urllib.parse import urlparse
    if not isinstance(sub, dict) or len(json.dumps(sub)) > 2000:
        return False
    url = urlparse(str(sub.get("endpoint") or ""))
    keys = sub.get("keys")
    return (url.scheme == "https" and bool(main._PUSH_HOSTS.match(url.hostname or ""))
            and isinstance(keys, dict) and isinstance(keys.get("p256dh"), str) and isinstance(keys.get("auth"), str))


@router.post("/api/push/subscribe")
async def push_subscribe(req: Request):
    body = await req.json()
    sub = body.get("subscription") if isinstance(body, dict) else None
    if not main._valid_push_subscription(sub):
        return {"ok": False, "error": "invalid subscription"}
    r = main._get_redis()
    if r:
        if r.scard(main.PUSH_SUBS_KEY) >= main.MAX_PUSH_SUBS:
            return {"ok": False, "error": "full"}
        r.sadd(main.PUSH_SUBS_KEY, json.dumps({"endpoint": sub["endpoint"], "keys": {
            "p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]}}, sort_keys=True))
    return {"ok": True}


@router.delete("/api/push/subscribe")
async def push_unsubscribe(req: Request):
    body = await req.json()
    sub = body.get("subscription") if isinstance(body, dict) else None
    if not main._valid_push_subscription(sub):
        return {"ok": False}
    r = main._get_redis()
    if r:
        r.srem(main.PUSH_SUBS_KEY, json.dumps({"endpoint": sub["endpoint"], "keys": {
            "p256dh": sub["keys"]["p256dh"], "auth": sub["keys"]["auth"]}}, sort_keys=True))
    return {"ok": True}


async def _send_push_notifications(value_preds: list):
    """Tell subscribers about strong value picks, at most once a day."""
    import web_push
    r = main._get_redis()
    if not r or not web_push.configured():
        return
    try:
        if not await asyncio.to_thread(web_push.once, r, "value", datetime.now(timezone.utc).date().isoformat()):
            return
        n = len(value_preds)
        data = web_push.payload(f"{n} value bet{'s' if n != 1 else ''} found",
                                " · ".join(f"{p['home']} vs {p['away']}" for p in value_preds[:3]), "/", "value")
        print(f"[Push] value picks: {await asyncio.to_thread(web_push.send_all, r, data)}")
    except ImportError:
        print("[Push] pywebpush not installed — push notifications disabled")
    except Exception as e:
        print(f"[Push] Error: {type(e).__name__}: {e}")


async def _push_daily(doc: Dict[str, Any]) -> None:
    """Tell subscribers the day's slips are out, once a day."""
    import web_push
    r = main._get_redis()
    text = web_push.daily_text(doc)
    if not r or not text or not web_push.configured():
        return
    try:
        if not await asyncio.to_thread(web_push.once, r, "daily", doc["date"]):
            return
        data = web_push.payload(text["title"], text["body"], "/daily", "daily")
        print(f"[Push] daily odds: {await asyncio.to_thread(web_push.send_all, r, data)}")
    except ImportError:
        print("[Push] pywebpush not installed — push notifications disabled")
    except Exception as e:
        print(f"[Push] daily error: {type(e).__name__}: {e}")
