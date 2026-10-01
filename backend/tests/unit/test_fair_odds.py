"""
De-vigging (fair_odds.py) and the check of methods and weights on settled
matches (check_fair_odds.py).
"""

import pytest

import check_fair_odds as cfo
import fair_odds


@pytest.mark.parametrize("method", list(fair_odds.METHODS))
def test_chances_add_up_and_keep_the_order(method):
    for odds in ([1.25, 3.8], [1.9, 1.9], [2.1, 3.3, 3.6], [1.05, 9.0]):
        p = fair_odds.fair(odds, method)
        assert sum(p) == pytest.approx(1.0, abs=1e-9)
        assert sorted(range(len(p)), key=lambda i: -p[i]) == sorted(range(len(odds)), key=lambda i: odds[i])


def test_shin_and_power_give_the_favourite_more_than_proportional():
    odds = [1.25, 3.8]
    prop = fair_odds.proportional(odds)[0]
    assert fair_odds.shin(odds)[0] > prop and fair_odds.power(odds)[0] > prop
    # Even prices stay even
    assert fair_odds.shin([1.9, 1.9])[0] == pytest.approx(0.5)


def test_no_margin_is_left_alone():
    assert fair_odds.shin([2.0, 2.0]) == [0.5, 0.5]
    assert fair_odds.power([2.2, 2.2]) == pytest.approx([0.5, 0.5])


def entry(p_home, oh, oa, tip="1", verdict="won", rated=True, when=("2026-09-30", "15:00")):
    return {"date": when[0], "time": when[1], "grades": {"tip": {"verdict": verdict}},
            "pred": {"p_home": p_home, "odds_home": oh, "odds_away": oa, "tip_code": tip, "rated": rated}}


def test_a_rated_match_gives_back_elos_chance():
    mp = fair_odds.proportional([1.5, 2.6])[0]
    p_home = 0.2 * 0.8 + 0.8 * mp
    r = cfo.row("table_tennis", entry(p_home, 1.5, 2.6))
    assert r["p_elo"] == pytest.approx(0.8) and r["home_won"] is True
    # Before the weight's change table tennis was priced at 0.35, and around it not at all
    early = cfo.row("table_tennis", entry(0.35 * 0.8 + 0.65 * mp, 1.5, 2.6, when=("2026-09-28", "10:00")))
    assert early["p_elo"] == pytest.approx(0.8)
    assert cfo.row("table_tennis", entry(p_home, 1.5, 2.6, when=("2026-09-29", "22:00"))) is None
    # The away side's tip lost: home won
    assert cfo.row("tennis", entry(0.4, 1.8, 2.0, tip="2", verdict="lost", rated=False))["home_won"] is True
    assert cfo.row("tennis", entry(0.4, 1.8, 2.0, verdict="void")) is None


def test_the_check_finds_a_weight_that_helps():
    import random
    rnd = random.Random(3)
    rows = []
    for _ in range(600):
        truth = rnd.uniform(0.2, 0.8)
        mp = min(0.9, max(0.1, truth + rnd.gauss(0, 0.12)))      # a noisy market
        oh, oa = 1 / (mp * 1.06), 1 / ((1 - mp) * 1.06)
        rows.append({"sport": "tennis", "odds": (oh, oa), "home_won": rnd.random() < truth, "p_elo": truth})
    got = cfo.check("tennis", rows)
    w = float(got["best"].split(" w")[1])
    assert w >= 0.5 and got["all"][got["best"]]["vs_now_z"] < -2
