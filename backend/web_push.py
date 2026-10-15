"""
Browser push notifications (Web Push with VAPID keys).

Subscriptions are kept in a Redis set (SUBS_KEY), one JSON object per
browser. A notification goes to every subscriber; one a push service says
is gone (404/410) is dropped. Each kind of notification goes out at most
once a day (`once`), so a rerun or a redeploy can't send it twice. It
needs, in .env:

    VAPID_PUBLIC_KEY    the applicationServerKey browsers subscribe with
    VAPID_PRIVATE_KEY   signs each push
    VAPID_CLAIMS_EMAIL  a contact address for the push services (optional)
"""

import json
import os
from typing import Any, Dict, Optional

SUBS_KEY = "betiq:push_subs"
SENT_KEY = "betiq:push_sent:{}:{}"   # kind, date
TAGS = {"daily": "betiq-daily", "value": "betiq-value"}


def configured() -> bool:
    return bool(os.getenv("VAPID_PRIVATE_KEY", "").strip() and os.getenv("VAPID_PUBLIC_KEY", "").strip())


def payload(title: str, body: str, url: str = "/", kind: str = "daily") -> str:
    return json.dumps({"title": title[:80], "body": body[:200], "url": url if url.startswith("/") else "/",
                       "icon": "/icon-192.png", "tag": TAGS.get(kind, "betiq")})


def once(r, kind: str, day: str) -> bool:
    """True the first time `kind` is claimed for `day` (and never again)."""
    return bool(r.set(SENT_KEY.format(kind, day), "1", nx=True, ex=3 * 86400))


def send_all(r, data: str, sender=None) -> Dict[str, Any]:
    """Push `data` to every subscriber: {sent, dropped, failed}. Blocking (run it in a thread)."""
    if sender is None:
        from pywebpush import webpush as sender
    private = os.getenv("VAPID_PRIVATE_KEY", "").strip()
    claims = {"sub": "mailto:" + (os.getenv("VAPID_CLAIMS_EMAIL", "").strip() or "admin@betiq.app")}
    out = {"sent": 0, "dropped": 0, "failed": 0}
    for raw in r.smembers(SUBS_KEY) or ():
        try:
            sender(subscription_info=json.loads(raw), data=data, vapid_private_key=private,
                   vapid_claims=dict(claims), ttl=12 * 3600)
            out["sent"] += 1
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 410) or " 410 " in f" {e} " or " 404 " in f" {e} ":
                r.srem(SUBS_KEY, raw)
                out["dropped"] += 1
            else:
                out["failed"] += 1
    return out


def daily_text(doc: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """The 'today's odds are out' notification, or None when no slip was made."""
    slips = [s for s in doc.get("slips") or [] if s.get("status") != "none" and s.get("picks")]
    if not slips:
        return None
    odds = ", ".join(f"{float(s['total_odds']):.2f}" if s.get("total_odds") else f"~{int(s['target'])}" for s in slips)
    n = len(slips)
    return {"title": "Today's odds are out",
            "body": f"{n} slip{'s' if n != 1 else ''} at {odds} odds, with booking codes. Tap to see them."}
