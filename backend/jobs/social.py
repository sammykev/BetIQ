"""
The daily odds on X and Telegram (x_poster.py, telegram_poster.py).

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from typing import Any, Dict, List

import main

router = APIRouter()


def _channel(ch: str):
    import importlib
    if ch not in main.POST_CHANNELS:
        raise HTTPException(status_code=404, detail="Unknown channel")
    return importlib.import_module(main.POST_CHANNELS[ch])


def _site_url() -> str:
    return main.FRONTEND_URL if main.FRONTEND_URL.startswith("https://") else "https://predict-withbetiq.vercel.app"


def _post_enabled(ch: str) -> bool:
    r = main._get_redis()
    try:
        return bool(r and json.loads(r.get(main.POST_CONFIG_KEY.format(ch)) or "{}").get("enabled"))
    except Exception:
        return False


def _post_day(r, ch: str, day: str) -> Dict[str, Any]:
    try:
        return json.loads(r.get(main.POST_DAY_KEY.format(ch, day)) or "{}") if r else {}
    except Exception:
        return {}


def _post_ready(doc: Dict[str, Any], now: datetime) -> bool:
    """Every slip that can still be booked has its code, or it's
    POST_GRACE_MINUTES into the day."""
    import daily_slips
    if now >= daily_slips.window(doc["date"])[0] + timedelta(minutes=main.POST_GRACE_MINUTES):
        return True
    return all((s.get("booking") or {}).get("code") or not daily_slips.open_picks(s, now)
               for s in doc.get("slips") or [] if s.get("status") != "none")


async def _post_daily(ch: str, doc: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
    """Post the day's slips to one channel, once a day (`force`: again). The
    day's state: {status: posted|failed|skipped, id, url, text, error, tries, at}."""
    mod = main._channel(ch)
    r = main._get_redis()
    state = main._post_day(r, ch, doc["date"])
    if state.get("status") == "posted" and not force:
        return state
    if not mod.configured():
        return {"status": "failed", "error": f"Missing in .env: {', '.join(mod.missing())}"}
    text = mod.compose(doc, main._site_url())
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
        r.set(main.POST_DAY_KEY.format(ch, doc["date"]), json.dumps(state), ex=14 * 86400)
    return state


async def _maybe_post_daily(doc: Dict[str, Any]) -> None:
    """From the daily tick: post today's slips to each switched-on channel once they're booked."""
    import daily_slips
    if doc.get("date") != daily_slips.today() or not main._post_ready(doc, daily_slips.now_utc()):
        return
    await main._push_daily(doc)
    for ch in main.POST_CHANNELS:
        mod = main._channel(ch)
        if not await asyncio.to_thread(main._post_enabled, ch) or not mod.configured():
            continue
        state = await asyncio.to_thread(main._post_day, main._get_redis(), ch, doc["date"])
        if state.get("status") in ("posted", "skipped") or int(state.get("tries") or 0) >= mod.MAX_TRIES:
            continue
        try:
            await main._post_daily(ch, doc)
        except Exception as e:
            print(f"[Post:{ch}] error: {e}")


def _post_alerts() -> List[Dict[str, str]]:
    """Banner lines for channels that gave up on today's post."""
    import daily_slips
    out = []
    r = main._get_redis()
    for ch in main.POST_CHANNELS:
        if not main._post_enabled(ch):
            continue
        mod = main._channel(ch)
        state = main._post_day(r, ch, daily_slips.today())
        if state.get("status") == "failed" and int(state.get("tries") or 0) >= mod.MAX_TRIES:
            name = "X" if ch == "x" else "Telegram"
            out.append({"level": "warn", "title": f"Today's daily odds weren't posted on {name}",
                        "detail": f"{state.get('error')}. Fix it, then use Post now in Messaging → Daily odds on {name}."})
    return out


@router.get("/api/admin/post/{ch}")
async def admin_post_channel(ch: str, _admin: str = Depends(main.require_admin)):
    """One channel: keys, switch, today's post and a preview."""
    import daily_slips
    mod = main._channel(ch)
    today = daily_slips.today()
    r = main._get_redis()
    doc = main._daily_load(r, today)
    return {"channel": ch, "configured": mod.configured(), "missing": mod.missing(), "enabled": main._post_enabled(ch),
            "today": main._post_day(r, ch, today), "preview": mod.compose(doc, main._site_url()) if doc else None,
            "post_by_utc": (daily_slips.window(today)[0] + timedelta(minutes=main.POST_GRACE_MINUTES)).strftime("%H:%M"),
            "max_tries": mod.MAX_TRIES}


@router.put("/api/admin/post/{ch}")
async def put_admin_post_channel(ch: str, body: Dict[str, Any], _admin: str = Depends(main.require_admin)):
    """Body: {enabled}: post the daily odds to this channel each morning."""
    main._channel(ch)
    r = main._get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="Couldn't save: the database isn't connected")
    enabled = bool((body or {}).get("enabled"))
    r.set(main.POST_CONFIG_KEY.format(ch), json.dumps({"enabled": enabled}))
    main._audit(_admin, "daily_posting", channel=ch, enabled=enabled)
    return await main.admin_post_channel(ch, _admin)


@router.post("/api/admin/post/{ch}/now")
async def admin_post_now(ch: str, body: Dict[str, Any] = None, _admin: str = Depends(main.require_admin)):
    """Post today's daily odds to this channel now. Body: {again?: true} to
    post even if today's post already went out."""
    mod = main._channel(ch)
    if not mod.configured():
        raise HTTPException(status_code=400, detail=f"Missing in .env: {', '.join(mod.missing())}")
    import daily_slips
    doc = main._daily_load(main._get_redis(), daily_slips.today())
    if not doc:
        raise HTTPException(status_code=404, detail="Today's slips aren't made yet")
    state = await main._post_daily(ch, doc, force=bool((body or {}).get("again")))
    main._audit(_admin, "daily_post", channel=ch, status=state.get("status"))
    return state
