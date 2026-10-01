"""
Football calibration maps (football_calibration.py): fitted on settled
picks, checked on later days, applied to the optimizer's chances but not to
the accuracy records they're fitted from.
"""

import random

import pytest

import football_calibration as fc
import market_accuracy
import optimizer
from tests.unit.test_optimizer import pred


@pytest.fixture(autouse=True)
def no_maps():
    fc.set_maps({})
    yield
    fc.set_maps({})


def settled(rnd, market, n, truth, days=60):
    """n picks whose real chance is truth(p) for our p."""
    out = []
    for i in range(n):
        p = rnd.uniform(0.5, 0.95)
        out.append({"market": market, "code": "O", "prob": p, "won": rnd.random() < truth(p),
                    "date": f"2026-{7 + i * days // n // 30:02d}-{1 + (i * days // n) % 30:02d}"})
    return out


def test_a_fitted_map_recovers_the_truth():
    rnd = random.Random(1)
    rows = settled(rnd, "corners_ou", 4000, lambda p: p - 0.08)
    a, b = fc.fit([(r["prob"], r["won"]) for r in rows])
    q = fc.apply("m", 0.8, {"m": (a, b)})
    assert 0.68 <= q <= 0.76
    # Outside what maps are fitted on, chances stay as they are
    assert fc.apply("m", 0.4, {"m": (a, b)}) == 0.4 and fc.apply("other", 0.8, {"m": (a, b)}) == 0.8


def test_the_check_keeps_maps_that_help_on_later_days_only():
    rnd = random.Random(2)
    rows = (settled(rnd, "corners_ou", 3000, lambda p: p - 0.1)       # overconfident
            + settled(rnd, "sot", 3000, lambda p: min(0.99, p + 0.06))  # underconfident
            + settled(rnd, "1x2", 3000, lambda p: p))                 # right already
    rep = fc.check(rows)
    assert rep["markets"]["corners_ou"]["choice"] != "raw" and rep["markets"]["sot"]["choice"] != "raw"
    assert rep["overall"]["chosen"]["vs_raw_z"] < -2
    assert fc.apply("corners_ou", 0.8, rep["maps"]) < 0.75 and fc.apply("sot", 0.7, rep["maps"]) > 0.73
    if "1x2" in rep["maps"]:
        assert abs(fc.apply("1x2", 0.7, rep["maps"]) - 0.7) < 0.03
    # Too few days: nothing is fitted
    assert fc.check(rows[:5])["maps"] == {}


def test_the_optimizer_uses_the_maps_and_the_accuracy_records_dont():
    p = pred(0, p_home=0.7, p_draw=0.2, p_away=0.1)
    raw = {(o.market, o.code): o.prob for o in optimizer.candidates(p, None, 0.5)}
    fc.set_maps({"1x2": (-0.5, 1.0)})
    cal = {(o.market, o.code): o.prob for o in optimizer.candidates(p, None, 0.5)}
    assert cal[("1x2", "1")] < raw[("1x2", "1")]
    assert market_accuracy.picks_of(p)["1x2"]["1"] == round(raw[("1x2", "1")], 3)


def test_the_server_loads_stored_maps():
    import json
    from tests.unit.test_user_endpoints import FakeRedis
    r = FakeRedis()
    r.set(fc.REPORT_KEY, json.dumps({"maps": {"corners_ou": [-0.3, 0.9]}}))
    assert fc.load(r) == {"corners_ou": (-0.3, 0.9)}
    assert fc.load(FakeRedis()) == {}
