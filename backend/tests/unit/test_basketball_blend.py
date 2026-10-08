"""The basketball weight check (check_basketball_blend.py) and the lines the
match-day snapshot keeps for it."""

import random

import basketball_matchday as bmd
import check_basketball_blend as cbb


def test_snapshot_keeps_both_lines():
    snap = bmd.snapshot({"p_home": 0.6, "model_detail": {"model_margin": 4.2, "market_margin": 3.0,
                                                         "model_total": 160.0, "market_total": 158.5}})
    assert snap["lines"] == {"model_margin": 4.2, "market_margin": 3.0, "model_total": 160.0, "market_total": 158.5}
    assert "lines" not in bmd.snapshot({"p_home": 0.6})


def _e(ours, market, hs, as_, d, ot=False):
    return {"date": d, "pred": {"lines": {"model_margin": ours, "market_margin": market,
                                          "model_total": 160.0, "market_total": 158.0}},
            "result": {"status": "finished", "score": [hs, as_], "ot": ot}}


def test_rows_skip_overtime_and_missing_lines():
    assert cbb.row(_e(3, 2, 80, 75, "2026-10-01"))["margin"] == (3, 2, 5.0)
    assert cbb.row(_e(3, 2, 90, 88, "2026-10-01", ot=True)) is None
    assert cbb.row({"date": "x", "pred": {}, "result": {"status": "finished", "score": [80, 75]}}) is None


def test_check_prefers_the_sharper_line():
    rnd = random.Random(1)
    rows = []
    for i in range(300):
        truth = rnd.gauss(0, 8)
        m = truth + rnd.gauss(0, 2)                 # sharp market
        ours = truth + rnd.gauss(0, 8)              # noisy ratings
        actual = truth + rnd.gauss(0, 10)
        rows.append(cbb.row(_e(ours, m, 80 + actual / 2, 80 - actual / 2, f"2026-0{1 + i // 100}-{1 + i % 28:02d}")))
    got = cbb.check(rows, "margin")
    assert float(got["best"][1:]) <= 0.2 and "holdout" in got
    assert cbb.check(rows[:20], "margin")["note"]
