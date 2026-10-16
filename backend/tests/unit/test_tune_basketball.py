import json
from datetime import date, timedelta

import basketball_model as bm
import tune_basketball as tb


def _rows(margin, n=200, weeks=20, drop=()):
    # (week, our margin, sigma, actual margin, ot, game): home wins 3 games in 4
    return [(i * weeks // n, margin, 12.0, 5 if i % 4 else -5, False, i) for i in range(n) if i not in drop]


def test_keeps_a_pair_only_when_it_wins_on_the_held_out_weeks():
    base = (bm.HALF_LIFE_DAYS, bm.RIDGE)
    rows = {c: _rows(1.0) for c in tb.candidates()}
    rows[(180.0, 4.0)] = _rows(4.0)          # sharper on both halves
    got = tb.choose(rows)
    assert got["best"] == [180.0, 4.0] and got["keep"]
    assert got["tuned"]["log_loss"] < got["default"]["log_loss"]
    rows[(180.0, 4.0)] = _rows(1.0)          # no better: the defaults stay
    assert not tb.choose(rows)["keep"]
    assert tb.choose({**rows, base: rows[base]})["default"]["n"] == 100   # the held-out half


def test_a_pair_that_leaves_games_unpriced_is_out():
    rows = {c: _rows(1.0) for c in tb.candidates()}
    rows[(45.0, 4.0)] = _rows(4.0, drop=range(0, 200, 10))   # misses 10% of the games
    got = tb.choose(rows)
    assert got["best"] != [45.0, 4.0] and "45/4" not in got["grid"]


def test_tuned_leagues_are_fitted_with_their_own_pair():
    class R:
        def get(self, k):
            return json.dumps({"tuned": {"NBA": [180, 4]}})
    assert tb.load(R()) == {"NBA": (180.0, 4.0)} and tb.load(None) == {}
    d0 = date(2025, 1, 1)
    teams = [f"T{i}" for i in range(6)]
    games = [bm.Game((d0 + timedelta(days=k)).isoformat(), teams[k % 6], teams[(k + 1 + k // 6) % 6 or 1], 100 + k % 7, 98)
             for k in range(120) if teams[k % 6] != teams[(k + 1 + k // 6) % 6 or 1]]
    plain = bm.fit("NBA", games)
    try:
        bm.TUNED["NBA"] = (180.0, 4.0)
        tuned = bm.fit("NBA", games)
    finally:
        bm.TUNED.clear()
    assert tuned.attack != plain.attack
    assert bm.fit("NBA", games, half_life=180.0, ridge=4.0).attack == tuned.attack
