import random

import pytest

import table_tennis_fit as ttf
import table_tennis_model as ttm


def test_game_scores_sum_to_one_and_are_fair_at_even():
    g = ttm.game_scores(0.5, 0)
    assert sum(g.values()) == pytest.approx(1.0)
    assert ttm.p_game(0.5, 0) == pytest.approx(0.5)
    # 11-0 at even: 0.5^11
    assert g[(11, 0)] == pytest.approx(0.5 ** 11)
    # Every score is a finished game: 11 with a lead of 2+, or a deuce game won by 2
    for (a, b) in g:
        assert max(a, b) >= 11 and abs(a - b) >= 2
        assert max(a, b) == 11 or abs(a - b) == 2


def test_swing_keeps_the_game_even_and_spreads_scores():
    assert ttm.p_game(0.5) == pytest.approx(0.5)
    tight, loose = ttm.game_scores(0.5, 0), ttm.game_scores(0.5, 0.06)
    assert sum(loose.values()) == pytest.approx(1.0)
    assert loose[(11, 2)] > tight[(11, 2)]


def test_match_distribution():
    for bo in (5, 7):
        md = ttm.match_dist(0.53, bo)
        assert sum(md.sets.values()) == pytest.approx(1.0)
        assert sum(md.total_games.values()) == pytest.approx(1.0)
        assert md.p_win == pytest.approx(ttm.p_match(0.53, bo), abs=1e-6)
        assert all(max(k) == bo // 2 + 1 for k in md.sets)
    md = ttm.match_dist(0.55)
    assert md.p_handicap(0) == pytest.approx(sum(v for d, v in md.games_diff.items() if d > 0))
    assert md.p_total_over(0.5) == pytest.approx(1.0)
    assert md.p_set_handicap(-2.5) == pytest.approx(md.sets.get((3, 0), 0))
    assert 0 < md.p_odd_total() < 1


def test_solve_rally_hits_the_level():
    for target in (0.3, 0.5, 0.8):
        p = ttm.solve_rally(target, 5)
        assert ttm.p_match(p, 5) == pytest.approx(target, abs=2e-3)   # rally chances are cached to 4 places


def test_predict_blends_and_needs_something():
    players = {"A": ttm.Player("A", 1600, 30), "B": ttm.Player("B", 1500, 30), "C": ttm.Player("C", 1500, 2)}
    md = ttm.predict(players, "A", "B")
    assert md.detail["elo"] == pytest.approx(ttm.elo_p(1600, 1500), abs=1e-4)
    md = ttm.predict(players, "A", "B", market_p1=0.5, model_weight=0.5)
    assert md.detail["level"] == pytest.approx(0.5 * ttm.elo_p(1600, 1500) + 0.25, abs=1e-4)
    # C barely played: the market alone; nothing without it
    assert ttm.predict(players, "A", "C", market_p1=0.6).detail["level"] == pytest.approx(0.6)
    assert ttm.predict(players, "A", "C") is None


def test_best_of():
    assert ttm.best_of("Setka Cup") == 5
    assert ttm.best_of("WTT Champions Macao") == 7


def _sim(n=2500, seed=3):
    random.seed(seed)
    skill = {f"P{i}": random.gauss(0, 0.03) for i in range(40)}
    rows = []
    for i in range(n):
        h, a = random.sample(list(skill), 2)
        p = 0.5 + skill[h] - skill[a]
        games, sh, sa = [], 0, 0
        while sh < 3 and sa < 3:
            x = y = 0
            while not ((x >= 11 or y >= 11) and abs(x - y) >= 2):
                if random.random() < p:
                    x += 1
                else:
                    y += 1
            games.append([x, y])
            sh, sa = sh + (x > y), sa + (y > x)
        rows.append({"id": str(i), "h": h, "a": a, "ko": 1_700_000_000 + i * 60, "sets": [sh, sa], "games": games,
                     "t": "Sim Cup"})
    return rows


def test_fit_on_simulated_matches():
    rows = ttf.rows_from(_sim() + _sim(10))           # duplicates by id are dropped
    assert len(rows) == 2500
    players, rep = ttf.run(rows)
    assert rep["winner"]["accuracy"] > 0.6
    assert rep["k_mult"] in ttf.K_MULTS and rep["swing"] in ttf.SWINGS and rep["shrink"] in ttf.SHRINKS
    assert set(rep["markets"]) >= {"total_points", "points_handicap", "games_handicap", "game_winner"}
    for b in rep["winner"]["calibration"]:
        if b["n"] > 150:
            assert abs(b["said"] - b["came_in"]) < 0.08
