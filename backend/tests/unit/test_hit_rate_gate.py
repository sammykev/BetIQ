"""
Slips take only markets that came in often enough in the nightly ranking
(market_ranking.by_code): 70% for the optimizer, 75% for the daily slips.
"""

import asyncio
from datetime import date

import daily_slips
import main
import market_ranking
from routers import optimizer as opt_router
from tests.unit.test_matchday import pred

BODY = {"target_odds": 3, "min_odds": 2.5, "max_odds": 3.5, "min_prob": 0.5, "no_live_check": True}


def run(monkeypatch, codes, body):
    monkeypatch.setattr(main, "_predictions_cache", [{**pred(d=date.today().isoformat()), "sport": "football"}])
    monkeypatch.setattr(main, "_get_redis", lambda: None)
    monkeypatch.setattr(main, "_ranked_codes", lambda: codes)
    seen = []

    async def solve(groups, *a, **k):
        seen.extend(o.market for g in groups for o in g)
        return {"picks": [], "total_odds": 3.0, "win_chance": 0.4, "within_target": True}
    monkeypatch.setattr(main, "_solve", solve)
    return asyncio.run(main._optimize_request(body)), set(seen)


def test_no_ranking_yet_means_no_gate(monkeypatch):
    res, seen = run(monkeypatch, None, BODY)
    assert seen and "hit_rate_left_out" not in res


def test_markets_below_70_or_unranked_are_left_out(monkeypatch):
    _, every = run(monkeypatch, None, BODY)
    keep, low = sorted(every)[:2], sorted(every)[2]
    codes = {f"football:{keep[0]}": {"n": 40, "hit_rate": 0.70}, f"football:{keep[1]}": {"n": 40, "hit_rate": 0.9},
             f"football:{low}": {"n": 40, "hit_rate": 0.69}}
    res, seen = run(monkeypatch, codes, BODY)
    assert seen == set(keep)
    assert f"football:{low}" in res["hit_rate_left_out"] and res["min_hit_rate"] == 0.7


def test_daily_slips_ask_for_75(monkeypatch):
    _, every = run(monkeypatch, None, BODY)
    m = sorted(every)[0]
    codes = {f"football:{m}": {"n": 40, "hit_rate": 0.72}}
    assert daily_slips.request(10, {})["min_hit_rate"] == 0.75
    assert daily_slips.request(10, {})["hit_fallback"] is False
    res, _ = run(monkeypatch, codes, {**BODY, "min_hit_rate": daily_slips.MIN_HIT_RATE, "hit_fallback": False})
    assert "75%" in res["error"] and res["matches_considered"] == 0
    _, seen = run(monkeypatch, codes, BODY)
    assert seen == {m}


def test_off_turns_the_gate_off(monkeypatch):
    _, seen = run(monkeypatch, {}, {**BODY, "min_hit_rate": "off"})
    assert seen


def test_codes_pool_each_market_by_its_optimizer_name():
    e = {"pred": {"best": {"market": "bb_total"}},
         "grades": {"tip": {"prob": 0.8, "verdict": "won"}, "best": {"prob": 0.75, "verdict": "lost"},
                    "points": {"prob": 0.7, "verdict": "won"}}}
    rows = market_ranking.sport_rows("basketball", [e] * 5)
    codes = market_ranking.by_code(rows, 5)
    assert {k: (x["n"], x["hit_rate"]) for k, x in codes.items()} == {"basketball:bb_total": (10, 0.5),
                                                                      "basketball:bb_winner": (5, 1.0)}
    assert codes["basketball:bb_winner"]["said"] == 0.8 and codes["basketball:bb_winner"]["roi"] is None
    tt = {"grades": {"tip": {"prob": 0.7, "verdict": "won"}, "games": {"prob": 0.6, "verdict": "lost"}}}
    assert set(market_ranking.by_code(market_ranking.sport_rows("table_tennis", [tt]), 1)) == \
        {"table_tennis:rk_winner", "table_tennis:rk_total_games"}


def test_ranked_codes_read_from_the_stored_ranking(monkeypatch):
    import json
    from tests.unit.test_user_endpoints import FakeRedis
    fake = FakeRedis()
    fake.kv[main.RANKING_KEY] = json.dumps({"codes": {"tennis:rk_winner": {"n": 20, "hit_rate": 0.8}}})
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    monkeypatch.setattr(opt_router, "_ranking_cache", {"at": 0.0, "codes": None})
    assert opt_router._ranked_codes() == {"tennis:rk_winner": {"n": 20, "hit_rate": 0.8}}


def test_floors_step_down_the_ranking():
    assert opt_router._hit_floors([0.9, 0.8], 0.7) == [0.7]
    assert opt_router._hit_floors([0.68, 0.66, 0.52], 0.7) == [0.7, 0.65, 0.5]
    assert opt_router._hit_floors([0.69, 0.5, 0.49], 0.7) == [0.7, 0.65, 0.5, 0.45]


def test_under_70_markets_only_when_no_slip_reaches_the_odds(monkeypatch):
    _, every = run(monkeypatch, None, BODY)
    good, near, far = sorted(every)[:3]
    codes = {f"football:{good}": {"n": 40, "hit_rate": 0.8, "said": 0.78, "roi": 0.05},
             f"football:{near}": {"n": 40, "hit_rate": 0.66, "said": 0.7, "roi": -0.1},
             f"football:{far}": {"n": 40, "hit_rate": 0.55, "said": 0.7, "roi": 0.02}}
    res, seen = run(monkeypatch, codes, BODY)
    assert seen == {good} and res["fallback_used"] is False and res["below_min"] == []

    # No slip at the odds from the 70%+ markets: by default the under-70 ones stay out;
    # asked for (hit_fallback), the next ones down the ranking come in, flagged
    monkeypatch.setattr(main, "_predictions_cache", [{**pred(d=date.today().isoformat()), "sport": "football"}])
    pools = []

    async def solve(groups, *a, **k):
        pools.append({o.market for g in groups for o in g})
        o = next((o for g in groups for o in g if o.market == near), None)
        if o is None:
            return {"picks": [], "total_odds": 1.5, "win_chance": 0.6, "within_target": False}
        return {"picks": [opt_router.asdict_option(o)], "total_odds": 3.0, "win_chance": 0.4, "within_target": True}
    monkeypatch.setattr(main, "_solve", solve)
    res = asyncio.run(main._optimize_request(BODY))
    assert pools == [{good}] and not res.get("fallback_used")
    pools.clear()
    res = asyncio.run(main._optimize_request({**BODY, "hit_fallback": True}))
    assert pools[:2] == [{good}, {good, near}] and far not in set().union(*pools)
    assert res["fallback_used"] is True and res["below_min"] == [f"football:{near}"] and res["fallback_floor"] == 0.65
    hit = res["picks"][0]["market_hit"]
    assert hit["below_min"] and hit["negative_roi"] and hit["hit_rate"] == 0.66


def test_no_70_market_at_all_falls_back_only_when_asked(monkeypatch):
    _, every = run(monkeypatch, None, BODY)
    m = sorted(every)[0]
    codes = {f"football:{m}": {"n": 40, "hit_rate": 0.6}}
    res, seen = run(monkeypatch, codes, BODY)
    assert not seen and "70%" in res["error"]
    res, seen = run(monkeypatch, codes, {**BODY, "hit_fallback": True})
    assert seen == {m} and "error" not in res
    res, seen = run(monkeypatch, codes, {**BODY, "min_hit_rate": 0.75, "hit_fallback": False})
    assert not seen and "error" in res


def test_gated_runs_are_logged_for_the_admin(monkeypatch):
    from fastapi.testclient import TestClient
    from tests.unit.test_user_endpoints import FakeRedis
    _, every = run(monkeypatch, None, BODY)
    keep, low = sorted(every)[:2]
    fake = FakeRedis()
    run(monkeypatch, {f"football:{keep}": {"n": 40, "hit_rate": 0.9}}, BODY)   # no Redis: nothing logged
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    asyncio.run(main._optimize_request({**BODY, "_source": "daily"}))

    async def identity(request):
        return "secret", None
    monkeypatch.setattr(main, "_admin_identity", identity)
    runs = TestClient(main.app).get("/api/admin/optimizer/left-out").json()["runs"]
    assert len(runs) == 1 and runs[0]["source"] == "daily" and f"football:{low}" in runs[0]["left_out"]


def test_chance_pulled_down_to_the_markets_record():
    import optimizer
    o = optimizer.Option("A", "B", "2026-10-21", "", "L", "m", "M", "c", "x", 0.8, 1.3, "sportybet")
    assert opt_router._calibrated(o, None) is o
    assert opt_router._calibrated(o, {"n": 30, "hit_rate": 0.8, "said": 0.75}) is o      # came in over: kept
    c = opt_router._calibrated(o, {"n": 30, "hit_rate": 0.69, "said": 0.77})            # 8 under, half weight
    assert c.prob == 0.76 and c.model_prob == 0.8
    assert opt_router._calibrated(o, {"n": 300, "hit_rate": 0.69, "said": 0.77}).prob < 0.73


def test_markets_far_under_what_the_model_said_are_left_out(monkeypatch):
    assert opt_router._overconfident({"n": 20, "hit_rate": 0.65, "said": 0.75})
    assert not opt_router._overconfident({"n": 19, "hit_rate": 0.5, "said": 0.75})       # too few to tell
    assert not opt_router._overconfident({"n": 50, "hit_rate": 0.70, "said": 0.79})
    _, every = run(monkeypatch, None, BODY)
    good, bad = sorted(every)[:2]
    codes = {f"football:{good}": {"n": 40, "hit_rate": 0.8, "said": 0.8},
             f"football:{bad}": {"n": 40, "hit_rate": 0.72, "said": 0.85}}
    res, seen = run(monkeypatch, codes, BODY)
    assert seen == {good} and res["overconfident_left_out"] == [f"football:{bad}"]
    _, seen = run(monkeypatch, codes, {**BODY, "calibrate": False})
    assert bad in seen


def test_picks_default_to_85_percent(monkeypatch):
    body = {k: v for k, v in BODY.items() if k != "min_prob"}
    _, low = run(monkeypatch, None, BODY)
    _, high = run(monkeypatch, None, body)
    assert opt_router.MIN_PICK_PROB == 0.85 and high < low


def test_held_out_markets_never_reach_a_slip(monkeypatch):
    _, every = run(monkeypatch, None, BODY)
    m = sorted(every)[0]
    monkeypatch.setattr(opt_router, "HELD_OUT_MARKETS", {f"football:{m}"})
    res, seen = run(monkeypatch, None, BODY)
    assert m not in seen and res["held_out"] == [f"football:{m}"]
