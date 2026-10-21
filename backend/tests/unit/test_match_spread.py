"""
One bad match shouldn't sink many tickets: a match already on the cap of
the account's open tickets is left out of the next optimizer slip.
"""

import json

import pytest
from fastapi.testclient import TestClient

import auth
import main
from routers import optimizer
from tests.unit.test_user_endpoints import FakeRedis


def leg(home, away, status="pending", day="2026-10-22"):
    return {"home": home, "away": away, "date": day, "market": "1x2", "code": "1", "status": status}


def ticket(status, legs):
    return {"code": "C", "status": status, "legs": legs}


def test_matches_on_the_cap_of_open_tickets_are_crowded():
    tickets = [ticket("pending", [leg("A", "B"), leg("C", "D")]),
               ticket("open", [leg("A", "B"), leg("A", "B")]),      # counted once per ticket
               ticket("pending", [leg("C", "D", status="won")]),     # settled leg: no longer at risk
               ticket("lost", [leg("C", "D")]),                      # settled ticket
               ticket("pending", [leg("E", "F", status="unknown")])]
    assert optimizer._crowded_matches(tickets, 2) == [("A", "B", "2026-10-22")]
    assert optimizer._crowded_matches(tickets, 1) == [("A", "B", "2026-10-22"), ("C", "D", "2026-10-22"),
                                                      ("E", "F", "2026-10-22")]
    assert optimizer._crowded_matches(tickets, 0) == []


@pytest.fixture
def api(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    monkeypatch.setattr(main, "_paywall_enabled", lambda: False)
    monkeypatch.setattr(main, "_features_cache", [0.0, None])
    monkeypatch.setattr(optimizer, "MAX_TICKETS_PER_MATCH", 2)
    seen = []

    async def optimize(body, window=None):
        seen.append(body)
        return {"picks": []}
    monkeypatch.setattr(main, "_optimize_request", optimize)

    async def user(request):
        return "u1"
    monkeypatch.setattr(auth, "optional_user", user)
    fake.kv[main._ukey("u1", "tickets")] = json.dumps(
        [ticket("pending", [leg("A", "B")]), ticket("pending", [leg("A", "B"), leg("C", "D")])])
    return seen


def test_the_optimizer_leaves_out_crowded_matches(api):
    c = TestClient(main.app)
    r = c.post("/api/optimizer", json={"target_odds": 5, "exclude_matches": [["X", "Y", "2026-10-22"]]}).json()
    assert r["crowded_matches"] == {"left_out": 1, "cap": 2}
    assert api[-1]["exclude_matches"] == [["X", "Y", "2026-10-22"], ["A", "B", "2026-10-22"]]

    r = c.post("/api/optimizer", json={"target_odds": 5, "spread_matches": False}).json()
    assert "crowded_matches" not in r and not api[-1].get("exclude_matches")
