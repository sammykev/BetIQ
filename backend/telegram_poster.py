"""
Posting the daily odds to a Telegram channel.

Each morning, once the day's slips are made and booked (daily_slips.py),
one message goes to the channel: every slip with its booking code and all of
its picks (Telegram allows 4,096 characters, so the picks fit), and a link
to the site. The Bot API is free. It needs, in .env:

    TELEGRAM_BOT_TOKEN   from @BotFather (/newbot)
    TELEGRAM_CHAT_ID     the channel: "@yourchannel" for a public one, or its
                         numeric id (-100…) for a private one

and the bot added to the channel as an administrator allowed to post.
"""

import html
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
MAX_TRIES = 3
MAX_LENGTH = 4096
LAGOS = timedelta(hours=1)      # the site's pick times are UTC; the channel reads Lagos time


def configured() -> bool:
    return all(os.getenv(k, "").strip() for k in KEYS)


def missing() -> List[str]:
    return [k for k in KEYS if not os.getenv(k, "").strip()]


def message_url(chat_id: str, message_id: Any) -> Optional[str]:
    """A link to the message: t.me/<channel>/<id>, or t.me/c/<id>/<id> for a private channel."""
    chat = str(chat_id).strip()
    if chat.startswith("@"):
        return f"https://t.me/{chat[1:]}/{message_id}"
    if chat.startswith("-100"):
        return f"https://t.me/c/{chat[4:]}/{message_id}"
    return None


def error_text(status: int, body: Dict[str, Any]) -> str:
    """Telegram's reason for a refused message, with what to do about it."""
    detail = str(body.get("description") or body.get("detail") or "").strip()
    low = detail.lower()
    hint = (" (check TELEGRAM_BOT_TOKEN: copy it again from @BotFather)" if status == 401 else
            " (check TELEGRAM_CHAT_ID: @channelname, or the -100… id)" if "chat not found" in low else
            " (add the bot to the channel as an administrator allowed to post)" if status == 403 or "not a member" in low
            or "not enough rights" in low else
            " (Telegram's limit: try again shortly)" if status == 429 else "")
    return f"HTTP {status}: {detail}".strip() + hint


async def publish(text: str) -> Dict[str, Any]:
    """Send `text` (HTML) to the channel: {ok, id, url} or {ok: False, error}."""
    import httpx
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN", "").strip(), os.getenv("TELEGRAM_CHAT_ID", "").strip()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                  json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                                        "disable_web_page_preview": True})
        body = r.json()
    except Exception as e:
        # Never echo the request (its URL holds the token)
        return {"ok": False, "error": f"couldn't reach Telegram ({type(e).__name__})"}
    if r.status_code == 200 and body.get("ok"):
        mid = (body.get("result") or {}).get("message_id")
        return {"ok": True, "id": str(mid), "url": message_url(chat, mid)}
    return {"ok": False, "error": error_text(r.status_code, body)}


def _lagos_time(p: Dict[str, Any]) -> str:
    try:
        return (datetime.fromisoformat(f"{p['date']}T{p.get('time') or ''}") + LAGOS).strftime("%H:%M")
    except (KeyError, ValueError):
        return ""


def compose(doc: Dict[str, Any], site: str) -> Optional[str]:
    """The message for a day's slips (HTML), or None when there's nothing to
    post (no slips, or none with a booking code)."""
    import daily_slips
    slips = [s for s in doc.get("slips") or [] if s.get("status") != "none" and s.get("picks")]
    if not any((s.get("booking") or {}).get("code") for s in slips):
        return None
    e = html.escape
    day = date.fromisoformat(doc["date"])
    head = [f"<b>BetIQ Daily Odds · {day.strftime('%A')} {day.day} {day.strftime('%B')}</b>"]
    blocks = []
    for s in slips:
        code = (s.get("booking") or {}).get("code")
        odds = f"{float(s['total_odds']):.2f}" if s.get("total_odds") else f"~{int(s['target'])}"
        lines = [f"<b>{int(s['target'])}x slip · {odds} odds</b>",
                 f"SportyBet code: <code>{e(code)}</code>" if code else "SportyBet code: on the site shortly"]
        for p in s["picks"]:
            when = _lagos_time(p)
            price = f" @ {float(p['odds']):.2f}" if p.get("odds") else ""
            lines.append(f"• {e(p.get('home', ''))} v {e(p.get('away', ''))}{f' ({when})' if when else ''}: "
                         f"{e(p.get('label') or p.get('code') or '')}{price}")
        blocks.append("\n".join(lines))
    link = f"{site.rstrip('/')}/daily"
    tail = (f"Today's matches only, every pick rated {round(daily_slips.MIN_PROB * 100)}%+ by our model, "
            f"none at 2.0 odds or more. Times are Lagos time.\n"
            f"Full slips, chances and results: {e(link)}\n\n18+ · Bet responsibly")
    text = "\n\n".join(head + blocks + [tail])
    if len(text) > MAX_LENGTH:
        # Too long with every pick: just the slips and codes
        short = [b.split("\n")[0] + "\n" + b.split("\n")[1] for b in blocks]
        text = "\n\n".join(head + short + [f"Picks: {e(link)}\n\n18+ · Bet responsibly"])
    return text
