"""Blending each football market with SportyBet's prices (market_blend.py)
and the check that decides which markets get a weight (check_market_blend.py)."""

import random

import check_market_blend as cmb
import market_blend
import optimizer


def test_outcomes_group_into_complete_sets():
    assert market_blend.group("goals_ou", "O25") == market_blend.group("goals_ou", "U25") == "goals_ou:25"
    assert market_blend.group("handicap", "H-1.5") == market_blend.group("handicap", "A+1.5")
    assert market_blend.group("handicap", "H+1.5") != market_blend.group("handicap", "H-1.5")
    assert market_blend.group("btts", "BTTS-Y") == "btts"
    for m, c in (("1x2", "1"), ("double_chance", "1X"), ("clean_sheet", "CS-H"), ("dc_goals", "1X&O25")):
        assert market_blend.group(m, c) is None
    rows = [["goals_ou", "O25", 0.6, 1.7], ["goals_ou", "U25", 0.4, 2.2], ["goals_ou", "O35", 0.3, 3.0]]
    assert market_blend.sets(rows) == {"goals_ou:25": [0, 1]}        # O35 has no partner


def test_apply_blends_only_markets_with_a_weight_and_full_prices():
    probs, odds = {"O25": 0.7, "U25": 0.3}, {"O25": 1.9, "U25": 1.9}
    assert market_blend.apply("goals_ou", probs, odds, w={}) == probs
    got = market_blend.apply("goals_ou", probs, odds, w={"goals_ou": 0.5})
    assert abs(got["O25"] - 0.6) < 1e-6 and abs(sum(got.values()) - 1) < 1e-9
    assert market_blend.apply("goals_ou", probs, {"O25": 1.9}, w={"goals_ou": 0.5}) == probs


def _entry(won_over, ours_over, odds, d):
    hg, ag = (2, 1) if won_over else (1, 0)
    return {"date": d, "pred": {"prices": [["goals_ou", "O25", ours_over, odds[0]],
                                            ["goals_ou", "U25", 1 - ours_over, odds[1]]]},
            "result": {"status": "finished", "hg": hg, "ag": ag}}


def test_check_gives_a_weight_where_the_market_knows_better():
    rnd = random.Random(3)
    rows = []
    for i in range(400):
        truth = rnd.uniform(0.3, 0.7)
        ours = min(0.95, max(0.05, truth + rnd.gauss(0, 0.2)))        # noisy
        mk = truth + rnd.gauss(0, 0.02)                                   # sharp
        rows += cmb.sets_of(_entry(rnd.random() < truth, ours, [1 / mk * 0.95, 1 / (1 - mk) * 0.95],
                                   f"2026-0{1 + i // 100}-{1 + i % 28:02d}"))
    assert len(rows) == 400 and rows[0]["market"] == "goals_ou"
    report = cmb.run(rows)
    assert report["weights"].get("goals_ou", 1) < 0.5


def test_check_keeps_ours_when_too_few():
    rows = cmb.sets_of(_entry(True, 0.6, [1.8, 2.0], "2026-10-01"))
    assert cmb.run(rows)["weights"] == {}


def test_optimizer_blends_with_a_weight_and_not_for_the_records(monkeypatch):
    pred = {"home": "A", "away": "B", "date": "2026-10-09", "p_home": 0.5, "p_draw": 0.25, "p_away": 0.25,
            "p_over15": 0.8, "p_over25": 0.7}
    monkeypatch.setattr(optimizer, "sportybet_ids", lambda m, c: (m, c) if m == "goals_ou" else None)
    monkeypatch.setattr(optimizer, "_event_odds", lambda ev, ids: 1.9 if ids[1] in ("O25", "U25") else None)
    monkeypatch.setattr(market_blend, "weights", {"goals_ou": 0.5})

    def p(calibrate):
        opts = optimizer.candidates(pred, {"x": 1}, min_prob=0.0, markets={"goals_ou"}, calibrate=calibrate)
        return {o.code: o.prob for o in opts}
    assert abs(p(True)["O25"] - 0.6) < 1e-3          # half ours (0.7), half SportyBet's fair 0.5
    assert abs(p(False)["O25"] - 0.7) < 1e-3         # the records keep ours alone
