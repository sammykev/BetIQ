"""
Posting the daily odds on X (Twitter).

Each morning, once the day's slips are made and booked (daily_slips.py),
one post gives each slip's total odds and SportyBet booking code, with a
link to the picks. It posts as the account whose keys are in .env:

    X_API_KEY, X_API_SECRET        the app's consumer keys
    X_ACCESS_TOKEN, X_ACCESS_SECRET the account's access token (Read and write)

Requests are signed with OAuth 1.0a (HMAC-SHA1), which X's v2 "create post"
endpoint takes for posting as a user; the JSON body isn't part of the
signature.
"""

import base64
import hashlib
import hmac
import os
import secrets
import time
import unicodedata
from datetime import date
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

POST_URL = "https://api.x.com/2/tweets"
KEYS = ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")
MAX_WEIGHT = 280
URL_WEIGHT = 23          # X counts every link as 23 characters
MAX_TRIES = 3            # failed posts a day before giving up (and alerting)


def configured() -> bool:
    return all(os.getenv(k, "").strip() for k in KEYS)


def missing() -> List[str]:
    return [k for k in KEYS if not os.getenv(k, "").strip()]


def _pct(s: str) -> str:
    return quote(str(s), safe="~-._")


def oauth_header(method: str, url: str, keys: Dict[str, str], nonce: Optional[str] = None,
                 timestamp: Optional[int] = None, signed: Optional[Dict[str, str]] = None) -> str:
    """The Authorization header for an OAuth 1.0a request. `signed`: query or
    form parameters, which go into the signature (a JSON body doesn't)."""
    params = {
        "oauth_consumer_key": keys["X_API_KEY"],
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(timestamp or int(time.time())),
        "oauth_token": keys["X_ACCESS_TOKEN"],
        "oauth_version": "1.0",
    }
    everything = {**params, **(signed or {})}
    param_str = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted(everything.items()))
    base = "&".join((method.upper(), _pct(url), _pct(param_str)))
    signing_key = f"{_pct(keys['X_API_SECRET'])}&{_pct(keys['X_ACCESS_SECRET'])}"
    sig = base64.b64encode(hmac.new(signing_key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    params["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(params.items()))


async def post(text: str) -> Tuple[int, Dict[str, Any]]:
    """Post `text` as the account in .env: (HTTP status, X's answer)."""
    import httpx
    keys = {k: os.getenv(k, "").strip() for k in KEYS}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(POST_URL, json={"text": text},
                              headers={"Authorization": oauth_header("POST", POST_URL, keys)})
    try:
        body = r.json()
    except ValueError:
        body = {"detail": (r.text or "")[:200]}
    return r.status_code, body


def error_text(status: int, body: Dict[str, Any]) -> str:
    """X's reason for a refused post, in a line."""
    detail = body.get("detail") or body.get("title") or ""
    errors = body.get("errors") or []
    if errors and isinstance(errors, list):
        detail = detail or errors[0].get("message", "")
    hint = {401: " (check the four X_ keys)",
            403: " (the app needs Read and write permission, and new access tokens made after setting it)",
            429: " (X's posting limit: try later)"}.get(status, "")
    return f"HTTP {status}: {detail}".strip() + hint


def weight(text: str) -> int:
    """X's length count: links 23, wide characters (emoji, CJK) 2."""
    total = 0
    for word in text.split(" "):
        stripped = word.strip("\n")
        if stripped.startswith(("http://", "https://")):
            total += URL_WEIGHT + (len(word) - len(stripped))
        else:
            total += sum(2 if (ord(ch) > 0x10FF or unicodedata.east_asian_width(ch) in "WF") else 1 for ch in word)
    return total + text.count(" ")


def compose(doc: Dict[str, Any], site: str) -> Optional[str]:
    """The post for a day's slips, or None when there's nothing to post (no
    slips, or none with a booking code)."""
    slips = [s for s in doc.get("slips") or [] if s.get("status") != "none" and s.get("picks")]
    if not any((s.get("booking") or {}).get("code") for s in slips):
        return None
    day = date.fromisoformat(doc["date"])
    lines = [f"BetIQ Daily Odds · {day.strftime('%a')} {day.day} {day.strftime('%b')}", ""]
    for s in slips:
        code = (s.get("booking") or {}).get("code")
        odds = f"{float(s['total_odds']):.2f}" if s.get("total_odds") else f"~{int(s['target'])}"
        lines.append(f"{int(s['target'])}x slip: {odds} odds · " + (f"code {code}" if code else "code on the site"))
    link = f"{site.rstrip('/')}/daily"
    import daily_slips
    tail = ["", f"Today's matches, every pick rated {round(daily_slips.MIN_PROB * 100)}%+ by our model, none at 2.0 odds or more. "
            "Load a code on SportyBet, or see the picks:", link,
            "", "18+ · Bet responsibly #BetIQ"]
    text = "\n".join(lines + tail)
    if weight(text) > MAX_WEIGHT:
        text = "\n".join(lines + ["", f"Picks: {link}", "18+ · Bet responsibly"])
    return text
