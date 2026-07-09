"""
Unit tests for event_filters.py — dropping already-started/finished events
from a cached prediction list, independent of when the cache was written.
"""

from datetime import datetime, timedelta

from event_filters import drop_started_events


def _fmt(dt: datetime) -> tuple[str, str]:
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


class TestDropStartedEvents:
    def test_future_event_kept(self):
        d, t = _fmt(datetime.utcnow() + timedelta(hours=3))
        preds = [{"home": "A", "away": "B", "date": d, "time": t}]
        assert drop_started_events(preds) == preds

    def test_past_event_dropped(self):
        d, t = _fmt(datetime.utcnow() - timedelta(hours=1))
        preds = [{"home": "A", "away": "B", "date": d, "time": t}]
        assert drop_started_events(preds) == []

    def test_mixed_list_keeps_only_future(self):
        past_d, past_t = _fmt(datetime.utcnow() - timedelta(minutes=30))
        future_d, future_t = _fmt(datetime.utcnow() + timedelta(hours=2))
        preds = [
            {"home": "Old A", "away": "Old B", "date": past_d, "time": past_t},
            {"home": "New A", "away": "New B", "date": future_d, "time": future_t},
        ]
        result = drop_started_events(preds)
        assert len(result) == 1
        assert result[0]["home"] == "New A"

    def test_missing_time_kept_rather_than_dropped(self):
        preds = [{"home": "A", "away": "B", "date": "2099-01-01", "time": ""}]
        assert drop_started_events(preds) == preds

    def test_tbd_time_kept(self):
        preds = [{"home": "A", "away": "B", "date": "2020-01-01", "time": "TBD"}]
        assert drop_started_events(preds) == preds

    def test_missing_date_kept(self):
        preds = [{"home": "A", "away": "B", "date": "", "time": "10:00"}]
        assert drop_started_events(preds) == preds

    def test_unparsable_date_kept_rather_than_dropped(self):
        preds = [{"home": "A", "away": "B", "date": "not-a-date", "time": "10:00"}]
        assert drop_started_events(preds) == preds

    def test_empty_list_returns_empty_list(self):
        assert drop_started_events([]) == []
