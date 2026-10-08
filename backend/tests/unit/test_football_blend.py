"""The football blend check (check_football_blend.py): settled matches read
from the match-day store, and a blend found where the market knows better."""

import random

import check_football_blend as cfb


def entry(hg, ag, ours, odds=None, prices=None, over=None, d="2026-10-01"):
    p = {"p_home": ours[0], "p_draw": ours[1], "p_away": ours[2]}
    if odds:
        p.update(odds_home=odds[0], odds_draw=odds[1], odds_away=odds[2])
    if prices:
        p["prices"] = prices
    if over is not None:
        p["p_over25"] = over
    return {"date": d, "league_name": "L", "pred": p, "result": {"status": "finished", "hg": hg, "ag": ag}}


def test_rows_take_the_odds_or_sportybets_prices():
    r = cfb.row(entry(2, 1, [0.5, 0.3, 0.2], odds=[1.9, 3.4, 4.2]))
    assert r["won"] == "H" and r["odds"] == [1.9, 3.4, 4.2] and abs(sum(r["ours"]) - 1) < 1e-9
    r = cfb.row(entry(1, 1, [0.4, 0.3, 0.3], prices=[["1x2", "1", 0.4, 2.4], ["1x2", "X", 0.3, 3.1],
                                                       ["1x2", "2", 0.3, 3.0], ["goals_ou", "O25", 0.5, 1.9],
                                                       ["goals_ou", "U25", 0.5, 1.9]], over=0.55))
    assert r["won"] == "D" and r["odds"] == [2.4, 3.1, 3.0] and r["ou"]["over"] is False
    assert cfb.row({**entry(1, 0, [0.5, 0.3, 0.2]), "result": {"status": "live", "hg": 1, "ag": 0}}) is None
    assert cfb.row(entry(1, 0, [0.5, 0.3, 0.2])) is None       # no prices at all


def test_a_noisy_model_is_told_to_lean_on_the_market():
    rnd = random.Random(5)
    rows = []
    for i in range(800):
        truth = [rnd.uniform(0.2, 0.6), 0.25]
        truth.append(1 - sum(truth))
        noisy = [max(0.05, t + rnd.gauss(0, 0.15)) for t in truth]
        k = rnd.random()
        won = "H" if k < truth[0] else "D" if k < truth[0] + truth[1] else "A"
        hg, ag = {"H": (1, 0), "D": (0, 0), "A": (0, 1)}[won]
        rows.append(cfb.row(entry(hg, ag, noisy, odds=[1 / (t * 1.05) for t in truth],
                                  d=f"2026-09-{1 + i % 28:02d}")))
    got = cfb.check(rows, cfb._losses_1x2)
    assert float(got["best"][1:]) <= 0.3 and got["grid"][got["best"]]["vs_ours_z"] < -2
    assert got["holdout"]["chosen_on_earlier"] <= 0.4


def test_the_site_blends_1x2_half_and_half_with_the_market():
    import fair_odds
    import main
    tip = {"p_home": 0.62, "p_draw": 0.22, "p_away": 0.16, "p_over15": 0.75, "p_over25": 0.5}
    odds = {"1": 2.1, "X": 3.3, "2": 3.6}
    b = main._blend_1x2(tip, odds)
    m = fair_odds.fair([2.1, 3.3, 3.6])
    assert abs(b["p_home"] - round(0.5 * 0.62 + 0.5 * m[0], 3)) < 1e-9
    assert abs(b["p_home"] + b["p_draw"] + b["p_away"] - 1) < 0.005
    assert b["p_home_model"] == 0.62 and b["p_over25"] == 0.5          # over/under stays ours
    assert b["tip_code"] in ("1", "1X") and b["tip_confidence"] >= b["p_home"]
    # Without all three prices the model's chances stand
    assert main._blend_1x2(tip, {"1": 2.1}) is tip and main._blend_1x2(tip, {}) is tip
    # The check reads the model's own chances, not the blend
    import check_football_blend as cfb
    e = {"date": "2026-10-09", "pred": {**b, "odds_home": 2.1, "odds_draw": 3.3, "odds_away": 3.6},
         "result": {"status": "finished", "hg": 1, "ag": 0}}
    assert abs(cfb.row(e)["ours"][0] - 0.62) < 1e-9
