"""Basketball quarter markets: highest scoring quarter and the highest /
lowest quarter's points, from league patterns, team trends and meetings."""

import random
from datetime import date, timedelta

import basketball_markets as bmk
import basketball_model as bm
import basketball_quarters as bq


def _league(n=600, late_team="Late", seed=1):
    """A league where every quarter is alike except "Late", which piles up its points in the 4th."""
    rnd = random.Random(seed)
    teams = [late_team] + [f"T{i}" for i in range(9)]
    games = []
    for i in range(n):
        h, a = rnd.sample(teams, 2)
        q = []
        for k in range(4):
            hp = rnd.gauss(20, 4) + (8 if (h == late_team and k == 3) else 0) - (2 if h == late_team else 0)
            ap = rnd.gauss(20, 4) + (8 if (a == late_team and k == 3) else 0) - (2 if a == late_team else 0)
            q.append((max(5, round(hp)), max(5, round(ap))))
        hs, as_ = sum(x[0] for x in q), sum(x[1] for x in q)
        d = (date(2026, 1, 1) + timedelta(days=i // 4)).isoformat()
        games.append(bm.Game(d, h, a, hs, as_, q))
    return games


def test_team_trend_moves_the_highest_quarter():
    m = bq.fit("L", _league(), k_team=10)
    assert m is not None and abs(sum(m.shares) - 1) < 1e-6
    assert m.scoring["Late"][3] > 0.03                     # its 4th-quarter share stands out
    bq.MODELS.clear()
    bq.MODELS["L"] = m
    with_late = bq.odds_for("L", "Late", "T1", 80, 80, 12).highest()
    plain = bq.odds_for("L", "T2", "T1", 80, 80, 12).highest()
    assert with_late["Q4"] > plain["Q4"] + 0.1
    assert abs(sum(with_late.values()) - 1) < 1e-9 and plain["EQ"] > 0.01   # ties happen on whole points
    bq.MODELS.clear()


def test_meetings_nudge_the_match():
    games = _league(n=400)
    # Two teams whose meetings always explode in the 1st quarter
    for i in range(8):
        d = (date(2026, 3, 1) + timedelta(days=i)).isoformat()
        games.append(bm.Game(d, "T1", "T2", 90, 90, [(35, 35), (18, 18), (18, 18), (19, 19)]))
    m = bq.fit("L", games, k_team=25)
    mu_h2h = bq.expected(m, "T1", "T2", 85, 85, k_h2h=3)
    mu_none = bq.expected(m, "T1", "T2", 85, 85, k_h2h=-1)
    assert mu_h2h[0] > mu_none[0] + 3
    assert abs(sum(mu_h2h) - 170) < 1e-6


def test_lines_on_the_highest_and_lowest_quarter():
    m = bq.fit("L", _league())
    import numpy as np
    sims = bq.simulate(bq.expected(m, "T1", "T2", 80, 80), m.cov, 12, sims=5000)
    o = bq.QuarterOdds(sims, [])
    assert o.p_high_over(30.5) > o.p_high_over(50.5)
    assert o.p_low_over(30.5) < o.p_high_over(30.5)
    assert (sims == np.rint(sims)).all()


def test_settling():
    res = {"status": "finished", "final": [90, 85], "periods": [[20, 20], [25, 22], [20, 18], [25, 25]]}
    assert bmk.settle("bb_hsq", "Q4", res) == "won"           # 50 points, the most
    assert bmk.settle("bb_hsq", "Q2", res) == "lost"
    tie = {**res, "periods": [[25, 22], [25, 22], [20, 18], [20, 20]]}
    assert bmk.settle("bb_hsq", "EQ", tie) == "won" and bmk.settle("bb_hsq", "Q1", tie) == "lost"
    assert bmk.settle("bb_hsq_total", "O47.5", res) == "won"
    assert bmk.settle("bb_lsq_total", "U38.5", res) == "won"   # the 3rd: 38
    assert bmk.settle("bb_hsq", "Q1", {**res, "periods": []}) == "void"


def test_offers_read_sportybets_quarter_markets():
    ev = {"eventId": "sr:match:1", "markets": [
        {"id": "234", "specifier": "", "outcomes": [{"id": "920", "odds": "3.9"}, {"id": "923", "odds": "3.1"},
                                                    {"id": "924", "odds": "9.5"}]},
        {"id": "1193", "specifier": "total=47.5", "outcomes": [{"id": "12", "odds": "1.85"}, {"id": "13", "odds": "1.9"}]},
        {"id": "1195", "specifier": "total=33.5", "outcomes": [{"id": "12", "odds": "1.8"}, {"id": "13", "odds": "1.95"}]}]}
    offs = {(o["market"], o["code"]) for o in bmk.offers(ev)}
    assert {("bb_hsq", "Q1"), ("bb_hsq", "Q4"), ("bb_hsq", "EQ"), ("bb_hsq_total", "O47.5"),
            ("bb_lsq_total", "U33.5")} <= offs
    one = next(o for o in bmk.offers(ev) if o["market"] == "bb_hsq" and o["code"] == "Q4")
    assert bmk.label(one, "A", "B") == "Highest scoring quarter: 4th quarter"
    assert bmk.market_name("bb_hsq") == "Highest Scoring Quarter"
