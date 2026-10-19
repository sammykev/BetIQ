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
    res, _ = run(monkeypatch, codes, {**BODY, "min_hit_rate": daily_slips.MIN_HIT_RATE})
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
    assert market_ranking.by_code(rows, 5) == {"basketball:bb_total": {"n": 10, "hit_rate": 0.5},
                                               "basketball:bb_winner": {"n": 5, "hit_rate": 1.0}}
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
