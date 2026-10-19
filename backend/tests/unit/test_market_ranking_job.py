"""
The nightly market ranking (market_ranking.py): run at midnight (Lagos),
kept in Redis with the previous run's hit rates, read by the admin panel.
"""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

import main
from tests.unit.test_user_endpoints import FakeRedis


def day(won):
    return {f"m{i}": {"grades": {"tip": {"prob": 0.7, "odds": 1.5, "verdict": "won" if i < won else "lost"}}}
            for i in range(10)}


@pytest.fixture
def server(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)

    async def identity(request):
        return "secret", None
    monkeypatch.setattr(main, "_admin_identity", identity)
    return fake


def test_nightly_run_saves_and_remembers_the_last_hit_rate(server):
    key = f"betiq:table_tennis:md:{date.today().isoformat()}"
    c = TestClient(main.app)
    assert c.get("/api/admin/market-ranking").json() == {"report": None}

    server.kv[key] = json.dumps(day(6))
    assert main._run_ranking()["markets"] == 1
    first = c.get("/api/admin/market-ranking").json()["report"]
    tip = first["markets"][0]
    assert (tip["sport"], tip["n"], tip["hit_rate"], tip["before"]) == ("table_tennis", 10, 0.6, None)
    assert first["sports"] == [{"sport": "table_tennis", "n": 10, "hit_rate": 0.6, "said": pytest.approx(0.7)}]

    server.kv[key] = json.dumps(day(8))
    main._run_ranking()
    tip = c.get("/api/admin/market-ranking").json()["report"]["markets"][0]
    assert (tip["hit_rate"], tip["before"]) == (0.8, 0.6)


def test_listed_as_an_admin_job():
    assert "market_ranking" in main.ADMIN_JOBS
