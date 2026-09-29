"""
Basketball model (basketball_model.py): ratings recovered from a simulated
league, probabilities that come true as often as they say, and the market's
expectation read back from its prices.
"""

import random
from datetime import date, timedelta

import pytest

import basketball_model as bm

TEAMS = [f"T{i}" for i in range(16)]


def simulate(seasons=2, seed=1, sd=11.0, hc=3.0, avg=80.0):
    """A league with known strengths: attack and defence per team, points
    normal around the expectation, quarter scores that add up."""
    rng = random.Random(seed)
    att = {t: rng.gauss(0, 4) for t in TEAMS}
    dfn = {t: rng.gauss(0, 4) for t in TEAMS}
    games, day = [], date(2024, 10, 1)
    for _ in range(seasons):
        for h in TEAMS:
            for a in TEAMS:
                if h == a:
                    continue
                eh = avg + hc + att[h] - dfn[a]
                ea = avg + att[a] - dfn[h]
                shared = rng.gauss(0, sd * 0.5)             # pace: moves both teams' points
                hs = round(eh + shared + rng.gauss(0, sd * 0.6))
                as_ = round(ea + shared + rng.gauss(0, sd * 0.6))
                ot = hs == as_
                qs = []
                rh, ra = hs, as_
                for q in range(3):
                    qh, qa = round(rh / (4 - q) + rng.gauss(0, 3)), round(ra / (4 - q) + rng.gauss(0, 3))
                    qs.append((qh, qa))
                    rh, ra = rh - qh, ra - qa
                qs.append((rh, ra))
                if ot:
                    hs += rng.choice([3, -3]) + 8
                    as_ += 8
                games.append(bm.Game(day.isoformat(), h, a, hs, as_, qs, ot=ot))
                day += timedelta(days=1)
    return games, att, dfn


@pytest.fixture(scope="module")
def league():
    games, att, dfn = simulate()
    return bm.fit("Sim", games), games, att, dfn


def test_recovers_the_home_court_and_the_teams(league):
    lg, _, att, dfn = league
    assert abs(lg.home_court - 3.0) < 1.0 and abs(lg.avg - 80.0) < 1.5
    # Strength (attack + defence) in the right order, near the truth
    true = {t: att[t] + dfn[t] for t in TEAMS}
    got = {t: lg.attack[t] + lg.defence[t] for t in TEAMS}
    order = sorted(TEAMS, key=true.get)
    assert sorted(TEAMS, key=got.get)[:4] == order[:4] or sum(
        abs(true[t] - got[t]) for t in TEAMS) / len(TEAMS) < 2.0


def test_probabilities_come_true_as_often_as_they_say():
    """Fit on one season, then check the next: winner, handicap and total
    picks at 70–90% land within a few points of their average chance."""
    games, _, _ = simulate(seasons=3, seed=7)
    cut = games[len(games) * 2 // 3].date
    lg = bm.fit("Sim", games, as_of=date.fromisoformat(cut))
    test = [g for g in games if g.date >= cut]
    said, hit = [], []
    for g in test:
        m = bm.expect(lg, g.home, g.away)
        for p, won in ((m.p_win(True), g.hs > g.as_),
                       (m.p_handicap(True, 10.5), g.hs + 10.5 > g.as_),
                       (m.p_handicap(False, 10.5), g.as_ + 10.5 > g.hs),
                       (m.p_total(m.total - 12.5), g.hs + g.as_ > m.total - 12.5)):
            if 0.70 <= p <= 0.90:
                said.append(p)
                hit.append(won)
    assert len(said) > 200
    assert abs(sum(hit) / len(hit) - sum(said) / len(said)) < 0.05


def test_lines_are_consistent(league):
    lg, *_ = league
    m = bm.expect(lg, "T0", "T1")
    # The two sides of a line add to one; a longer line is more likely
    assert m.p_handicap(True, 5.5) + m.p_handicap(False, -5.5) == pytest.approx(1.0, abs=1e-9)
    assert m.p_handicap(True, 10.5) > m.p_handicap(True, 5.5)
    assert m.p_total(150.5) > m.p_total(160.5)
    assert m.p_team_total(True, 70.5) > m.p_team_total(True, 80.5)
    assert m.p_3way("1") + m.p_3way("X") + m.p_3way("2") == pytest.approx(1.0)
    assert 0.0 < m.p_tie() < 0.08
    assert m.p_win(True) + m.p_win(False) == pytest.approx(1.0)
    assert m.p_half("handicap", 0.5) > m.p_half("handicap", -0.5)
    assert m.p_half_3way("1") + m.p_half_3way("X") + m.p_half_3way("2") == pytest.approx(1.0)
    # Quarter spreads were measured, and are smaller than the whole game's
    assert lg.sigma["q_margin"] < lg.sigma["h1_margin"] < lg.sigma["margin"]


def test_unknown_teams_get_no_expectation(league):
    lg, *_ = league
    assert bm.expect(lg, "T0", "Newcomers") is None


def test_the_markets_expectation_from_its_prices():
    # Main handicap: home -6.5 at evens both ways means the market expects about +6.5
    assert bm.market_margin(12.0, handicap=(-6.5, 1.90, 1.90)) == pytest.approx(6.5, abs=0.01)
    # Home favoured on the handicap price: expects more than the line
    assert bm.market_margin(12.0, handicap=(-6.5, 1.70, 2.15)) > 6.5
    # Winner only: an even game expects no margin
    assert bm.market_margin(12.0, winner=(1.87, 1.87)) == pytest.approx(0.0, abs=1e-9)
    assert bm.market_total(17.0, (160.5, 1.90, 1.90)) == pytest.approx(160.5, abs=0.01)


def test_blend_uses_the_market_where_we_know_nothing(league):
    lg, *_ = league
    sig = dict(bm.DEFAULT_SIGMA)
    only_market = bm.blend(None, sig, 4.0, 160.0, model_weight=0.4)
    assert (only_market.margin, only_market.total, only_market.source) == (4.0, 160.0, "market")
    ours = bm.expect(lg, "T0", "T1")
    mixed = bm.blend(ours, sig, ours.margin + 10, ours.total, model_weight=0.4)
    assert mixed.margin == pytest.approx(ours.margin + 6.0) and mixed.source == "blend"
    assert bm.blend(None, sig, None, None, 0.4) is None


def test_saved_and_loaded(league):
    lg, *_ = league
    back = bm.League.from_json(lg.to_json())
    assert back.expect("T3", "T4") == lg.expect("T3", "T4") and back.sigma == lg.sigma


def test_parts_and_overtime(league):
    lg, *_ = league
    m = bm.expect(lg, "T0", "T1")
    # The parts add up to the game; a quarter is smaller and tighter than a half
    q = [m.period(f"q{i}") for i in range(1, 5)]
    assert sum(p.home + p.away for p in q) == pytest.approx(m.total, rel=0.02)
    assert m.period("h1").sd_margin > q[0].sd_margin
    # Overtime adds points: an over is likelier with it counted
    assert m.p_total_ot(m.total) > m.p_total(m.total)
    assert m.p_team_total_ot(True, 80.5) >= m.p_team_total(True, 80.5)
    # With overtime counted a +0.5 line is no longer a win on a regulation tie
    assert m.p_handicap_ot(True, 0.5) < m.p_handicap(True, 0.5)
    assert m.p_handicap_ot(True, 5.5) + m.p_handicap_ot(False, -5.5) == pytest.approx(1.0, abs=1e-6)
    assert m.p_handicap_ot(True, -0.5) == pytest.approx(m.p_win(True), abs=0.01)
    assert lg.q_shares != (0.25,) * 4 and sum(lg.q_shares) == pytest.approx(1.0, abs=1e-6)
