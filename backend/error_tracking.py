"""
Error tracking with Sentry: every unhandled error in a request or a scheduled
job, with its stack trace, lands in the Sentry project (sentry.io emails new
kinds of error). Off unless SENTRY_DSN is set in the server's .env.

Nothing about visitors is sent: no cookies, no headers, no query strings, no
request bodies, no IP addresses.
"""

import logging
import os

log = logging.getLogger(__name__)


def _scrub(event, _hint):
    req = event.get("request")
    if isinstance(req, dict):
        for k in ("headers", "cookies", "query_string", "data", "env"):
            req.pop(k, None)
    event.pop("user", None)
    return event


def init() -> bool:
    dsn = os.getenv("SENTRY_DSN", "").strip()
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
        return True
    except Exception as e:                        # a bad DSN never stops the server
        log.warning("Sentry not started: %s", e)
        return False
