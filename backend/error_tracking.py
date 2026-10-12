"""
Error tracking with Sentry: every unhandled error in a request or a scheduled
job, with its stack trace, lands in the Sentry project (sentry.io emails new
kinds of error). Off unless there's a DSN: SENTRY_DSN in the server's .env,
or else the one the "sentry" job (sentry_setup.py, from the SENTRY_DSN GitHub
secret) saved in Redis, which the server checks at start and every hour.

Nothing about visitors is sent: no cookies, no headers, no query strings, no
request bodies, no IP addresses.
"""

import logging
import os

log = logging.getLogger(__name__)
DSN_KEY = "betiq:settings:sentry_dsn"
_started = False


def _scrub(event, _hint):
    req = event.get("request")
    if isinstance(req, dict):
        for k in ("headers", "cookies", "query_string", "data", "env"):
            req.pop(k, None)
    event.pop("user", None)
    return event


def init(dsn: str = "") -> bool:
    global _started
    if _started:
        return True
    dsn = (dsn or os.getenv("SENTRY_DSN", "")).strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=dsn,
            environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
            send_default_pii=False,
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0") or 0),
            before_send=_scrub,
            max_request_body_size="never",
        )
        _started = True
        print("[Sentry] error tracking on")
        return True
    except Exception as e:                        # a bad DSN never stops the server
        log.warning("Sentry not started: %s", e)
        return False


def init_from_redis(r) -> bool:
    """Start with the DSN saved in Redis, unless already on (or no Redis)."""
    if _started or r is None:
        return _started
    try:
        raw = r.get(DSN_KEY)
    except Exception:
        return False
    dsn = raw.decode() if isinstance(raw, bytes) else (raw or "")
    return init(dsn) if dsn else False
