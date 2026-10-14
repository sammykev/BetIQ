"""Sentry stays off without a DSN and never sends what visitors send us."""

import error_tracking


def test_off_without_a_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert error_tracking.init() is False


def test_scrub_drops_headers_query_and_user():
    ev = {"request": {"url": "https://x/api/a", "headers": {"Authorization": "Bearer t"}, "cookies": {"s": "1"},
                      "query_string": "key=1", "data": {"p": 1}}, "user": {"ip_address": "1.2.3.4"}}
    out = error_tracking._scrub(ev, None)
    assert out["request"] == {"url": "https://x/api/a"} and "user" not in out


class _R:
    def __init__(self, v):
        self.v = v

    def get(self, k):
        return self.v


def test_from_redis_only_when_there_is_one(monkeypatch):
    monkeypatch.setattr(error_tracking, "_started", False)
    seen = []
    monkeypatch.setattr(error_tracking, "init", lambda dsn="": seen.append(dsn) or True)
    assert error_tracking.init_from_redis(None) is False
    assert error_tracking.init_from_redis(_R(None)) is False and seen == []
    assert error_tracking.init_from_redis(_R(b"https://k@o1.ingest.sentry.io/2")) is True
    assert seen == ["https://k@o1.ingest.sentry.io/2"]


def test_scrub_drops_cancelled_tasks():
    import asyncio
    assert error_tracking._scrub({"message": "x"}, {"exc_info": (asyncio.CancelledError, None, None)}) is None
    assert error_tracking._scrub({"message": "x"}, {"exc_info": (ValueError, None, None)}) is not None
