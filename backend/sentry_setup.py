"""
Turn on the server's error tracking without logging in to the server: saves
the SENTRY_DSN GitHub secret in Redis (error_tracking.DSN_KEY), which the
server picks up within the hour, and sends a test event from here so it
shows in Sentry's Issues straight away. An empty secret turns it off at the
server's next restart.

    python sentry_setup.py     (GitHub Actions: "Basketball data", job sentry)
"""

import os

import error_tracking
import model_store


def main() -> None:
    dsn = os.getenv("SENTRY_DSN", "").strip()
    r = model_store._client()
    if r is None:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    if not dsn:
        r.delete(error_tracking.DSN_KEY)
        print("No SENTRY_DSN secret: removed from Redis (off at the server's next restart)")
        return
    if not dsn.startswith("https://") or "@" not in dsn:
        raise SystemExit("SENTRY_DSN doesn't look like a Sentry DSN (https://…@….ingest.sentry.io/…)")
    r.set(error_tracking.DSN_KEY, dsn)
    print("Saved: the server turns error tracking on within the hour (or at its next restart)")
    import sentry_sdk
    if error_tracking.init(dsn):
        sentry_sdk.capture_message("BetIQ: Sentry test from GitHub Actions")
        sentry_sdk.flush(10)
        print("Test event sent: look in Sentry → Issues")


if __name__ == "__main__":
    main()
