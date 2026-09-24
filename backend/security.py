"""
Request-level protections shared by every endpoint:

- client_ip: the caller's address behind Render's proxies.
- Rate limits per IP (in memory — one Render instance), tighter on the
  endpoints that cost money or CPU: the optimizer, booking codes (SportyBet
  requests), the AI explanation (LLM), logo lookups (outside APIs).
- Admin brute force: after ADMIN_MAX_FAILURES wrong admin secrets from one
  IP in ADMIN_FAILURE_WINDOW seconds, that IP is locked out for the window.
- A request-size cap (uploads excepted) and standard security headers.
- A short security event log (Redis) the admin panel shows.
"""

import hashlib
import ipaddress
import json
import os
import time
from collections import defaultdict, deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# (path prefix, requests, per seconds) — first match wins
RATE_LIMITS: List[Tuple[str, int, int]] = [
    ("/api/admin/", 240, 60),
    ("/api/traffic/hit", 3000, 60),  # from our site's server (key-checked), not browsers
    ("/api/optimizer", 20, 60),
    ("/api/booking/convert", 20, 60),
    ("/api/explain", 10, 60),
    ("/api/analysis", 60, 60),
    ("/api/sportybet-event", 30, 60),
    ("/api/sports/", 60, 60),
    ("/api/team-logo", 120, 60),
    ("/api/competition-logo", 120, 60),
    ("/api/track", 120, 60),
    ("/api/push/", 10, 60),
    ("/api/log/query", 30, 60),
    ("/api/referral/", 10, 60),
    ("/api/refresh", 5, 60),
    ("/api/", 300, 60),
]

MAX_BODY_BYTES = 256 * 1024
UPLOAD_PATHS = ("/api/admin/upload/",)
MAX_UPLOAD_BYTES = 60 * 1024 * 1024

ADMIN_MAX_FAILURES = 10
ADMIN_FAILURE_WINDOW = 15 * 60

EVENTS_KEY = "betiq:security:events"
EVENTS_KEEP = 300


def client_ip(request: Request) -> str:
    """The caller's IP. Render (and Cloudflare in front of it) put the real
    client in these headers; a client-supplied X-Forwarded-For is only ever
    prepended to, so its last entry is the one our proxy added."""
    for header in ("cf-connecting-ip", "true-client-ip"):
        value = (request.headers.get(header) or "").strip()
        if _valid_ip(value):
            return value
    forwarded = [p.strip() for p in (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
    if forwarded and _valid_ip(forwarded[-1]):
        return forwarded[-1]
    return request.client.host if request.client else "unknown"


def _valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def mask_ip(ip: str) -> str:
    """Enough of an IP to spot repeats, not enough to identify a person."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "unknown"
    if addr.version == 4:
        a, b, _, _ = ip.split(".")
        return f"{a}.{b}.x.x"
    return ":".join(ip.split(":")[:3]) + ":…"


def ip_hash(ip: str, salt: str = "") -> str:
    return hashlib.sha256(f"{salt}|{ip}".encode()).hexdigest()[:16]


class SlidingWindow:
    """Timestamps of recent hits per key."""

    def __init__(self):
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._last_sweep = time.monotonic()

    def hit(self, key: str, limit: int, window: int, now: Optional[float] = None) -> Tuple[bool, int]:
        """(allowed, seconds until the next hit is allowed)."""
        now = time.monotonic() if now is None else now
        q = self._hits[key]
        while q and q[0] <= now - window:
            q.popleft()
        if len(q) >= limit:
            return False, max(1, int(q[0] + window - now) + 1)
        q.append(now)
        self._sweep(now)
        return True, 0

    def count(self, key: str, window: int, now: Optional[float] = None) -> int:
        now = time.monotonic() if now is None else now
        q = self._hits.get(key)
        if not q:
            return 0
        while q and q[0] <= now - window:
            q.popleft()
        return len(q)

    def add(self, key: str, now: Optional[float] = None) -> None:
        self._hits[key].append(time.monotonic() if now is None else now)

    def _sweep(self, now: float) -> None:
        # Drop idle keys now and then so memory stays flat
        if now - self._last_sweep < 300:
            return
        self._last_sweep = now
        for k in [k for k, q in self._hits.items() if not q or q[-1] < now - 3600]:
            del self._hits[k]


_limiter = SlidingWindow()
_admin_failures = SlidingWindow()
_logged = SlidingWindow()  # one "rate_limited" event per IP and rule per window
_redis_getter: Callable[[], Any] = lambda: None


def configure(redis_getter: Callable[[], Any]) -> None:
    global _redis_getter
    _redis_getter = redis_getter


def log_event(kind: str, request: Optional[Request] = None, **detail) -> None:
    """Append to the admin panel's security log (best effort)."""
    event = {"at": int(time.time()), "type": kind, **detail}
    if request is not None:
        event.setdefault("ip", mask_ip(client_ip(request)))
        event.setdefault("path", request.url.path)
        country = request.headers.get("cf-ipcountry")
        if country:
            event.setdefault("country", country)
    r = _redis_getter()
    if not r:
        return
    try:
        r.lpush(EVENTS_KEY, json.dumps(event))
        r.ltrim(EVENTS_KEY, 0, EVENTS_KEEP - 1)
    except Exception:
        pass


def recent_events(limit: int = 100) -> List[Dict]:
    r = _redis_getter()
    if not r:
        return []
    try:
        return [json.loads(x) for x in r.lrange(EVENTS_KEY, 0, limit - 1)]
    except Exception:
        return []


def admin_locked(request: Request) -> bool:
    return _admin_failures.count(client_ip(request), ADMIN_FAILURE_WINDOW) >= ADMIN_MAX_FAILURES


def admin_failed(request: Request) -> None:
    ip = client_ip(request)
    _admin_failures.add(ip)
    n = _admin_failures.count(ip, ADMIN_FAILURE_WINDOW)
    log_event("admin_lockout" if n == ADMIN_MAX_FAILURES else "admin_bad_secret", request, failures=n)


def _rule(path: str) -> Optional[Tuple[str, int, int]]:
    for prefix, limit, window in RATE_LIMITS:
        if path.startswith(prefix):
            return prefix, limit, window
    return None


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Resource-Policy": "cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if request.method != "OPTIONS" and os.getenv("RATE_LIMITS", "1") != "0":
            rule = _rule(path)
            if rule:
                prefix, limit, window = rule
                ok, retry = _limiter.hit(f"{prefix}|{client_ip(request)}", limit, window)
                if not ok:
                    if _logged.hit(f"{prefix}|{client_ip(request)}", 1, window)[0]:
                        log_event("rate_limited", request, rule=prefix)
                    return JSONResponse({"detail": "Too many requests — slow down and try again shortly."},
                                        status_code=429, headers={"Retry-After": str(retry)})
        length = request.headers.get("content-length")
        cap = MAX_UPLOAD_BYTES if path.startswith(UPLOAD_PATHS) else MAX_BODY_BYTES
        if length and length.isdigit() and int(length) > cap:
            return JSONResponse({"detail": "Request too large"}, status_code=413)
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response
