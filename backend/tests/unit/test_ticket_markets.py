"""
Admin: booked legs by market (tickets.leg_markets): picks, hit rate, what
the model said and the return on the odds booked, by source and part.
"""

import json

import main
import tickets
from fastapi.testclient import TestClient
from tests.unit.test_user_endpoints import FakeRedis


def leg(market, status, prob=0.8, odds=1.5, sport=None, d="2026-10-01"):
    return {"market": market, "status": status, "prob": prob, "odds": odds, "date": d,
            **({"sport": sport} if sport else {})}


ITEMS = [{"source": "optimizer", "legs": [leg("bb_q1_total", "won", sport="basketball"),
                                          leg("bb_q1_total", "lost", 0.7, sport="basketball"),
                                          leg("bb_h1_total", "won", sport="basketball"),
                                          leg("over_2_5", "pending")]},
         {"source": "daily", "legs": [leg("bb_q1_total", "won", sport="basketball", d="2026-09-01"),
                                      leg("rk_s1_total", "lost", sport="tennis"), leg("sportybet", "won")]}]


def test_legs_grouped_by_market_with_hit_rate_said_and_return():
    rows = {r["market"]: r for r in tickets.leg_markets(ITEMS)}
    q1 = rows["bb_q1_total"]
    assert (q1["n"], q1["won"], q1["name"]) == (3, 2, "1st quarter total points")
    assert q1["said"] == round(2.3 / 3, 4) and q1["roi"] == round((0.5 + 0.5 - 1) / 3, 4)
    assert q1["gap"] == round(2 / 3 - 2.3 / 3, 4)
    assert set(rows) == {"bb_q1_total", "bb_h1_total", "rk_s1_total"}


def test_filters_by_source_part_and_date():
    assert [r["n"] for r in tickets.leg_markets(ITEMS, source="optimizer", part="quarter")] == [2]
    assert [r["market"] for r in tickets.leg_markets(ITEMS, part="half")] == ["bb_h1_total"]
    assert [r["market"] for r in tickets.leg_markets(ITEMS, part="set")] == ["rk_s1_total"]
    assert {r["market"]: r["n"] for r in tickets.leg_markets(ITEMS, since="2026-09-15")}["bb_q1_total"] == 2


def test_admin_endpoint(monkeypatch):
    fake = FakeRedis()
    fake.kv["betiq:user:u1:tickets"] = json.dumps(ITEMS)
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    monkeypatch.setattr(main, "_all_tickets", lambda r: [{**t, "uid": "u1"} for t in ITEMS])

    async def identity(request):
        return "secret", None
    monkeypatch.setattr(main, "_admin_identity", identity)
    c = TestClient(main.app)
    body = c.get("/api/admin/tickets/markets?source=optimizer&part=quarter").json()
    assert body["legs"] == 2 and body["markets"][0]["market"] == "bb_q1_total"
    assert c.get("/api/admin/tickets/markets?part=x").status_code == 400
