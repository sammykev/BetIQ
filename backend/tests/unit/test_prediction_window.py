"""
Only fixtures within PREDICTION_DAYS (default 14) of today are fetched,
predicted and served.
"""

import asyncio
from datetime import date, timedelta

from fastapi.testclient import TestClient

import international_fixtures as intl
import main


def day(n):
    return (date.today() + timedelta(days=n)).isoformat()


class Model:
    def predict_match(self, *a, **k):
        return {"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2, "tip_code": "1X"}


def test_default_is_two_weeks():
    assert main.PREDICTION_DAYS == 14


def test_fixtures_beyond_the_window_are_not_predicted():
    fixtures = [{"home": "A", "away": "B", "date": day(n)} for n in (0, 14, 15, 60)]
    preds = main._build_predictions(Model(), fixtures, {})
    assert [p["date"] for p in preds] == [day(0), day(14)]


def test_older_cached_predictions_beyond_the_window_are_hidden(monkeypatch):
    monkeypatch.setattr(main, "_predictions_cache", [
        {"home": "A", "away": "B", "date": day(3), "league": "PL"},
        {"home": "C", "away": "D", "date": day(40), "league": "PL"},
    ])
    r = TestClient(main.app).get("/api/predictions").json()
    assert [p["home"] for p in r["predictions"]] == ["A"] and r["total"] == 1


def test_window_setting_reaches_the_fetchers(monkeypatch):
    asked = {}

    async def fake(**kw):
        asked.update(kw)
        return {"fixtures": [], "results": [], "sources": {"espn": {}, "sofascore": {}, "odds_api": {}}, "errors": []}
    monkeypatch.setattr(intl, "fetch_international", fake)
    monkeypatch.setattr(main, "PREDICTION_DAYS", 10)
    asyncio.run(main._fetch_international_fixtures())
    assert asked["days_ahead"] == 10
