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
