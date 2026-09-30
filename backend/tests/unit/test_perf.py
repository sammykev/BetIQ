"""Server timings (perf.py) and the predictions list's cache and ETag."""

import asyncio

from fastapi.testclient import TestClient

import main
import perf


def test_timings_by_route_and_the_summary(monkeypatch):
    monkeypatch.setattr(perf, "_requests", perf.defaultdict(perf.deque))
    monkeypatch.setattr(perf, "_jobs", perf.defaultdict(perf.deque))
    for ms in (10, 20, 30, 400):
        perf.record_request("GET /api/matchday", ms, 2048)
    perf.record_job("tennis_live", 12.0)
    perf.record_job("tennis_live", 30.0)
    s = perf.summary()
    r = s["routes"]["GET /api/matchday"]
    assert r["n"] == 4 and r["p50_ms"] in (20, 30) and r["max_ms"] == 400 and r["kb"] == 2.0
    assert s["jobs"]["tennis_live"] == {"runs": 2, "avg_s": 21.0, "max_s": 30.0}


def test_the_middleware_times_api_routes_by_template(monkeypatch):
    monkeypatch.setattr(perf, "_requests", perf.defaultdict(perf.deque))
    c = TestClient(main.app)
    c.get("/api/matchday?date=2026-09-30")
    c.get("/api/matchday?date=2026-09-29")
    assert perf._requests["GET /api/matchday"] and len(perf._requests["GET /api/matchday"]) == 2
    assert not any("2026" in k for k in perf._requests)


def test_a_held_loop_shows_as_lag(monkeypatch):
    monkeypatch.setattr(perf, "_lag", perf.deque())
    monkeypatch.setattr(perf, "LAG_TICK", 0.05)

    async def run():
        watch = asyncio.ensure_future(perf.watch_loop(lambda: None))
        await asyncio.sleep(0.06)
        import time
        time.sleep(0.3)             # a job holding the loop
        await asyncio.sleep(0.12)
        watch.cancel()
    asyncio.run(run())
    assert max(x[1] for x in perf._lag) >= 200


def test_predictions_are_served_once_built_with_an_etag(monkeypatch):
    preds = [{"home": "A", "away": "B", "date": "2099-01-01", "league": "PL", "tip_confidence": 0.7}]
    monkeypatch.setattr(main, "_predictions_cache", preds)
    monkeypatch.setattr(main, "_within_window", lambda d: True)
    main._predictions_response.clear()
    c = TestClient(main.app)
    first = c.get("/api/predictions?limit=500")
    assert first.status_code == 200 and first.json()["predictions"][0]["home"] == "A"
    etag = first.headers["etag"]
    assert "max-age" in first.headers["cache-control"]
    again = c.get("/api/predictions?limit=500", headers={"If-None-Match": etag})
    assert again.status_code == 304 and not again.content
    # New predictions: a new list, a new tag
    monkeypatch.setattr(main, "_predictions_cache", preds + [{**preds[0], "home": "C"}])
    fresh = c.get("/api/predictions?limit=500", headers={"If-None-Match": etag})
    assert fresh.status_code == 200 and fresh.json()["total"] == 2 and fresh.headers["etag"] != etag
