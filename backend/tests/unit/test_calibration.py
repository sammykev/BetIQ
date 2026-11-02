"""Every market's correction, all sports (calibration.py): fitted on earlier
days, switched on only where it beats raw chances on later ones."""

import random
from datetime import date, timedelta

import calibration as cal


def _rows(sport, market, n, said, truth, rnd, days=30):
    out = []
    for i in range(n):
        p = min(0.97, max(0.5, rnd.gauss(said, 0.05)))
        out.append({"sport": sport, "market": market, "code": "x", "prob": p,
                    "won": rnd.random() < truth(p), "date": (date(2026, 9, 1) + timedelta(days=i % days)).isoformat(),
                    "match": f"{market}{i}"})
    return out


def test_families_group_the_same_bet_over_other_parts_of_the_game():
    assert cal.family("basketball", "bb_h2_handicap") == cal.family("basketball", "bb_handicap") == "bb_handicap"
    assert cal.family("basketball", "bb_q1_home_total") == "bb_team_total"
    assert cal.family("tennis", "rk_s1_total") == "rk_total"
    assert cal.family("tennis", "rk_home_games") == "rk_team_games"
    assert cal.family("football", "home_corners_ou") == "corners"
    assert cal.family("football", "double_chance") == "result"


def test_an_overconfident_market_is_corrected_and_a_good_one_left_alone():
    rnd = random.Random(7)
    rows = (_rows("basketball", "bb_h2_handicap", 900, 0.78, lambda p: p - 0.25, rnd)       # badly overrated
            + _rows("football", "goals_ou", 900, 0.75, lambda p: p, rnd))                   # well calibrated
    rep = cal.check(rows)
    bad = rep["markets"]["basketball|bb_h2_handicap"]
    assert bad["choice"] == "corrected" and bad["decided_on"] == "market"
    assert bad["corrected"]["log_loss"] < bad["raw"]["log_loss"]
    assert abs(bad["corrected"]["said"] - bad["raw"]["hit"]) < 0.06        # says about what comes in
    cal.set_maps(rep["maps"])
    assert cal.apply("basketball", "bb_h2_handicap", 0.8) < 0.65
    good = rep["markets"]["football|goals_ou"]
    assert abs(cal.apply("football", "goals_ou", 0.75) - 0.75) < 0.05 or good["choice"] == "raw"
    assert rep["overall"]["live"]["log_loss"] <= rep["overall"]["raw"]["log_loss"]
    cal.set_maps({})


def test_a_thin_market_borrows_its_familys_verdict():
    rnd = random.Random(3)
    rows = (_rows("basketball", "bb_handicap", 1200, 0.78, lambda p: p - 0.2, rnd)
            + _rows("basketball", "bb_q2_handicap", 40, 0.78, lambda p: p - 0.2, rnd))
    rep = cal.check(rows)
    thin = rep["markets"]["basketball|bb_q2_handicap"]
    assert thin["decided_on"] == "family" and thin["choice"] == "corrected"
    assert "basketball|bb_q2_handicap" in rep["maps"]


def test_too_few_days_changes_nothing():
    rnd = random.Random(1)
    rep = cal.check(_rows("football", "1x2", 50, 0.7, lambda p: p, rnd, days=3))
    assert rep["maps"] == {} and "note" in rep


def test_unpriced_or_out_of_range_chances_pass_through():
    m = {"football|corners_ou": (-0.5, 1.0)}
    assert cal.apply("football", "corners_ou", 0.4, m) == 0.4
    assert cal.apply("football", "btts", 0.7, m) == 0.7
    assert cal.apply("football", "corners_ou", 0.7, m) < 0.7


def test_match_day_lines_and_grades_become_rows():
    entry = {"home": "A", "away": "B", "date": "2026-10-01",
             "result": {"status": "finished", "score": [90, 80], "periods": None, "ot": False},
             "pred": {"lines": [["bb_winner", "1", 0.7], ["bb_total", "O160.5", 0.6]]}}
    rows = cal._other_rows("basketball", entry, "2026-10-01")
    got = {(r["market"], r["won"]) for r in rows}
    assert ("bb_winner", True) in got and ("bb_total", True) in got
    old = {"home": "A", "away": "B", "date": "2026-10-01", "result": {"status": "finished"},
           "pred": {"lines": {"model_margin": 1.0}},
           "grades": {"tip": {"prob": 0.66, "verdict": "won"}}}
    assert [(r["market"], r["won"]) for r in cal._other_rows("basketball", old, "2026-10-01")] == [("bb_winner", True)]


def test_the_tail_pull_only_lowers_and_starts_at_80():
    assert cal.tail(0.7, 0.8) == 0.7 and cal.tail(0.9, 1.0) == 0.9
    assert 0.8 < cal.tail(0.9, 0.8) < 0.9 and cal.tail(0.95, 0.8) > cal.tail(0.9, 0.8)
    maps = {"basketball|_tail": (0.8, 0.8)}
    assert cal.apply("basketball", "bb_winner", 0.9, maps) == cal.tail(0.9, 0.8)
    assert cal.apply("tennis", "rk_winner", 0.9, maps) == 0.9


def test_overrated_80_percent_picks_get_a_tail_pull_and_right_ones_none():
    rnd = random.Random(11)
    hi = lambda n, truth: [r for r in _rows("basketball", "bb_handicap", n, 0.88, truth, rnd) if r["prob"] > 0.8]
    short = lambda p: p - 0.05
    got = cal.tail_choice(hi(2000, short), hi(800, short), lambda r: r["prob"])
    assert got["choice"] == "pulled" and got["pulled"]["log_loss"] < got["none"]["log_loss"] and got["c_earlier"] < 1
    right = cal.tail_choice(hi(2000, lambda p: p), hi(800, lambda p: p), lambda r: r["prob"])
    assert right["choice"] == "none" or right["c_earlier"] > 0.9
    assert cal.tail_choice(hi(50, short), hi(10, short), lambda r: r["prob"])["choice"] == "too few picks"


def test_check_reports_each_sports_tail_and_leaves_held_out_markets_out():
    rnd = random.Random(5)
    rows = (_rows("tennis", "rk_winner", 3000, 0.8, lambda p: p, rnd)
            + _rows("basketball", "bb_total", 3000, 0.85, lambda p: p - 0.3, rnd))     # held out
    rep = cal.check(rows)
    assert set(rep["tails"]) == set(cal.SPORTS)
    assert rep["tails"]["basketball"]["choice"] == "too few picks"       # bb_total never counted
    assert "tennis|_tail" not in rep["maps"] or rep["maps"]["tennis|_tail"][0] > 0.9
