"""
Posting the daily odds to a Telegram channel.

Each morning, once the day's slips are made and booked (daily_slips.py),
one message goes to the channel: each slip's odds and SportyBet booking
code, and a link to the site. The picks themselves stay on the site. The Bot API is free. It needs, in .env:

    TELEGRAM_BOT_TOKEN   from @BotFather (/newbot)
    TELEGRAM_CHAT_ID     the channel: "@yourchannel" for a public one, or its
                         numeric id (-100…) for a private one

and the bot added to the channel as an administrator allowed to post.
"""

import html
import os
from datetime import date
from typing import Any, Dict, List, Optional

KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
MAX_TRIES = 3
MAX_LENGTH = 4096


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


def compose(doc: Dict[str, Any], site: str) -> Optional[str]:
    """The message for a day's slips (HTML): each slip's odds and SportyBet
    booking code, no match names or picks (those stay on the site). None
    when there's nothing to post (no slips, or none with a booking code)."""
    import daily_slips
    slips = [s for s in doc.get("slips") or [] if s.get("status") != "none" and s.get("picks")]
    if not any((s.get("booking") or {}).get("code") for s in slips):
        return None
    e = html.escape
    day = date.fromisoformat(doc["date"])
    lines = [f"<b>BetIQ Daily Odds · {day.strftime('%A')} {day.day} {day.strftime('%B')}</b>", ""]
    for s in slips:
        code = (s.get("booking") or {}).get("code")
        odds = f"{float(s['total_odds']):.2f}" if s.get("total_odds") else f"~{int(s['target'])}"
        lines.append(f"<b>{int(s['target'])}x</b> ({odds} odds): " + (f"<code>{e(code)}</code>" if code else "code on the site shortly"))
    lines += ["", f"Load a code on SportyBet. Every pick rated {round(daily_slips.MIN_PROB * 100)}%+ by our model, "
                  "none at 2.0 odds or more, today's matches only.",
              f"Slips and results: {e(site.rstrip('/') + '/daily')}", "", "18+ · Bet responsibly"]
    return "\n".join(lines)
