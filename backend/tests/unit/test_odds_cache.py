"""
Unit tests for odds_cache.py — the cooldown decision that stops the pipeline
from re-hitting The Odds API on every cold start.
"""

from datetime import datetime, timedelta

from odds_cache import is_within_cooldown, TIMESTAMP_FMT


def _iso(dt: datetime) -> str:
    return dt.strftime(TIMESTAMP_FMT)


class TestIsWithinCooldown:
    def test_recent_timestamp_is_within_cooldown(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        fetched_at = _iso(now - timedelta(minutes=30))
        assert is_within_cooldown(fetched_at, cooldown_seconds=4 * 3600, now=now) is True

    def test_old_timestamp_is_not_within_cooldown(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        fetched_at = _iso(now - timedelta(hours=5))
        assert is_within_cooldown(fetched_at, cooldown_seconds=4 * 3600, now=now) is False

    def test_exactly_at_boundary_is_not_within_cooldown(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        fetched_at = _iso(now - timedelta(hours=4))
        assert is_within_cooldown(fetched_at, cooldown_seconds=4 * 3600, now=now) is False

    def test_just_inside_boundary_is_within_cooldown(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        fetched_at = _iso(now - timedelta(hours=4) + timedelta(seconds=1))
        assert is_within_cooldown(fetched_at, cooldown_seconds=4 * 3600, now=now) is True

    def test_none_timestamp_returns_false(self):
        assert is_within_cooldown(None, cooldown_seconds=3600) is False

    def test_empty_string_returns_false(self):
        assert is_within_cooldown("", cooldown_seconds=3600) is False

    def test_malformed_timestamp_returns_false(self):
        assert is_within_cooldown("not-a-timestamp", cooldown_seconds=3600) is False

    def test_future_timestamp_returns_false(self):
        # Clock skew / bad data — don't treat a future "last fetched" as valid
        now = datetime(2026, 1, 1, 12, 0, 0)
        fetched_at = _iso(now + timedelta(hours=1))
        assert is_within_cooldown(fetched_at, cooldown_seconds=3600, now=now) is False

    def test_zero_cooldown_never_within_cooldown(self):
        now = datetime(2026, 1, 1, 12, 0, 0)
        fetched_at = _iso(now)
        assert is_within_cooldown(fetched_at, cooldown_seconds=0, now=now) is False

    def test_defaults_now_to_current_time(self):
        # Should not raise when `now` is omitted
        recent = _iso(datetime.utcnow())
        assert is_within_cooldown(recent, cooldown_seconds=3600) is True
