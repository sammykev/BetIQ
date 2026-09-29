"""
Player props maths (player_props.py): the negative binomial, minutes and
rates, dispersion measured from games, and chances that come in as often
as they say on simulated players.
"""

import random

import pytest

import player_props as pp


def test_negative_binomial_is_a_distribution():
    assert sum(pp.nb_pmf(k, 12.0, 6.0) for k in range(200)) == pytest.approx(1.0, abs=1e-6)
    # Mean kept; wider than Poisson for small r
    mean = sum(k * pp.nb_pmf(k, 12.0, 6.0) for k in range(300))
    assert mean == pytest.approx(12.0, abs=1e-4)
    assert pp.nb_cdf(8, 12.0, 3.0) > pp.nb_cdf(8, 12.0, 1e7)  # fatter left tail


def test_minutes_and_rate_are_recency_weighted_and_pulled_to_the_prior():
    games = [pp.Game("d", 34, {"pts": 30})] + [pp.Game("d", 30, {"pts": 15})] * 9
    m, sd = pp.expected_minutes(games)
    assert 30 < m < 34 and sd > 0
    r, behind = pp.rate(games, "pts", prior=0.4)
    raw = (30 + 15 * 9) / (34 + 30 * 9)
    assert min(raw, 0.4) < r < max(raw, 0.4)       # between his rate and the prior
    # Games he didn't play don't lower his minutes
    assert pp.expected_minutes(games + [pp.Game("d", 0, {})])[0] == m


def test_too_few_games_is_not_priced():
    assert pp.project([pp.Game("d", 30, {"pts": 15})] * 3, "pts", 0.4, 8.0) is None


def test_lines_and_the_game_factor():
    games = [pp.Game("d", 32, {"pts": 16})] * 20
    p = pp.project(games, "pts", 0.5, 8.0)
    assert p.mean == pytest.approx(32 * p.rate)
    assert p.p_over(10.5) > p.p_over(16.5) > p.p_over(24.5)
    assert p.p_at_least(11) == pytest.approx(p.p_over(10.5))
    hot = pp.project(games, "pts", 0.5, 8.0, game_factor=1.1)
    assert hot.mean == pytest.approx(p.mean * 1.1)


def test_dispersion_measured_from_games():
    rng = random.Random(4)
    # Counts with variance mean + mean²/5
    pairs = []
    for _ in range(4000):
        mean = rng.uniform(5, 25)
        lam = rng.gammavariate(5.0, mean / 5.0)
        y = sum(1 for _ in range(1000) if rng.random() < lam / 1000)
        pairs.append((mean, y))
    assert 3.5 < pp.fit_dispersion(pairs) < 7.0


def test_chances_come_in_as_often_as_they_say():
    """Simulated players: true per-minute rates, minutes that swing, counts
    spread like real ones. Our over/under chances land within a few points."""
    rng = random.Random(9)
    said, came = [], []
    for _ in range(400):
        true_rate, true_min = rng.uniform(0.2, 0.8), rng.uniform(18, 36)
        hist = []
        for _ in range(26):
            mins = max(4.0, rng.gauss(true_min, 4))
            lam = rng.gammavariate(8.0, true_rate * mins / 8.0)
            hist.append(pp.Game("d", mins, {"pts": float(sum(1 for _ in range(200) if rng.random() < lam / 200))}))
        past, tonight = hist[:25][::-1], hist[25]
        proj = pp.project(past, "pts", 0.45, 8.0)
        for line in (proj.mean - 6.5, proj.mean - 3.5, proj.mean + 3.5):
            line = int(line) + 0.5
            p = proj.p_over(line)
            for pr, hit in ((p, tonight.stats["pts"] > line), (1 - p, tonight.stats["pts"] < line)):
                if 0.6 <= pr < 0.95:
                    said.append(pr)
                    came.append(hit)
    assert len(said) > 300
    assert abs(sum(came) / len(came) - sum(said) / len(said)) < 0.05


def test_names_from_both_sides_match():
    assert pp.name_key("Shengelia, Tornike") == pp.name_key("Tornike Shengelia")
    assert pp.name_key("Mbappé, Kylian") == pp.name_key("Kylian Mbappe")
    assert pp.name_key("Jaren Jackson Jr.") == pp.name_key("Jackson, Jaren")
