"""
The tennis model's scoring maths (tennis_model.py): hold, tiebreak, set and
match chances from each player's chance of winning a point on serve, and
the markets read off the match's distribution.
"""

import pytest

import tennis_model as tm


def test_hold_matches_the_textbook():
    assert tm.p_hold(0.5) == pytest.approx(0.5)
    assert tm.p_hold(0.6) == pytest.approx(0.7357, abs=1e-4)
    assert tm.p_hold(0.64) == pytest.approx(0.8126, abs=1e-4)


def test_symmetry_and_totals():
    assert tm.p_tiebreak(0.64, 0.64) == pytest.approx(0.5, abs=1e-9)
    s = tm.set_scores(0.66, 0.62)
    assert sum(s.values()) == pytest.approx(1.0, abs=1e-9)
    assert set(s) <= {(6, i) for i in range(5)} | {(i, 6) for i in range(5)} | {(7, 5), (5, 7), (7, 6), (6, 7)}
    md = tm.match_dist(0.66, 0.62, 3)
    assert sum(md.sets.values()) == pytest.approx(1.0) and sum(md.total_games.values()) == pytest.approx(1.0)
    assert md.p_win == pytest.approx(tm.p_match(0.66, 0.62, 3), abs=1e-9)
    # The better server wins more, and more often in straight sets
    assert md.p_win > 0.5 and md.sets[(2, 0)] > md.sets[(0, 2)]


def test_markets_are_consistent():
    md = tm.match_dist(0.68, 0.60, 3)
    assert md.p_handicap(-0.5) == pytest.approx(sum(p for d, p in md.games_diff.items() if d > 0))
    assert md.p_handicap(100) == pytest.approx(1.0)
    assert md.p_total_over(11.5) == pytest.approx(1.0)          # a best-of-3 has 12 games at least
    assert md.p_total_over(12.5) < 1.0                           # 6-0 6-0 is possible
    assert md.p_set_handicap(-1.5) == pytest.approx(md.sets[(2, 0)])
    assert 0.5 < md.p_first_set() < md.p_win + 0.1


def test_best_of_five_widens_the_favourite():
    assert tm.p_match(0.66, 0.62, 5) > tm.p_match(0.66, 0.62, 3)


def test_level_set_by_the_rating_shape_by_the_serve():
    pa, pb = tm.solve_serve(0.8, 0.64, 0.64)
    assert tm.p_match(pa, pb, 3) == pytest.approx(0.8, abs=1e-3)
    # A big server against a weak returner: same chance of winning, more games
    big = tm.match_dist(*tm.solve_serve(0.7, 0.74, 0.70), 3)
    grind = tm.match_dist(*tm.solve_serve(0.7, 0.58, 0.54), 3)
    assert big.p_win == pytest.approx(grind.p_win, abs=0.01)
    assert sum(g * p for g, p in big.total_games.items()) > sum(g * p for g, p in grind.total_games.items())


def test_predict_uses_ratings_and_market():
    a = tm.Player("Jannik Sinner", "ATP", elo=2200, n=300, surf={"Hard": 2250, "Clay": 2150, "Grass": 2150})
    b = tm.Player("Qualifier One", "ATP", elo=1700, n=40, surf={"Hard": 1680, "Clay": 1700, "Grass": 1650})
    players = {f"ATP|{tm.name_key(a.name)}": a, f"ATP|{tm.name_key(b.name)}": b}
    md = tm.predict(players, {"ATP": 0.64}, "Sinner, Jannik", "One, Qualifier", "Hard")
    assert md.p_win > 0.9 and md.detail["known"]
    blended = tm.predict(players, {"ATP": 0.64}, "Sinner, Jannik", "One, Qualifier", "Hard", market_p1=0.8)
    assert 0.8 < blended.p_win < md.p_win
    assert tm.predict(players, {}, "Nobody", "Else", "Hard") is None
    assert tm.predict(players, {}, "Nobody", "Else", "Hard", market_p1=0.6).p_win == pytest.approx(0.6, abs=0.01)
    assert tm.tour_of("WTA Wuhan") == "WTA" and tm.tour_of("ITF Women Cairo") == "WTA" and tm.tour_of("ATP Shanghai") == "ATP"


def test_shrink_pulls_toward_even():
    assert tm.shrink(0.5, 0.8) == pytest.approx(0.5)
    assert 0.5 < tm.shrink(0.8, 0.8) < 0.8
    assert tm.shrink(0.8, 1.0) == pytest.approx(0.8)
    assert tm.shrink(0.2, 0.8) == pytest.approx(1 - tm.shrink(0.8, 0.8))
