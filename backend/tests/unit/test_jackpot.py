"""
The weekly jackpot (jackpot.py): chances from SportyBet's odds or our
model, the single line, the best combination ticket per budget and the
chance of each prize.
"""

import math

import jackpot as jp

# The 20261122 jackpot's prices (home / draw / away)
WEEK = [(2.42, 4.03, 2.72), (1.99, 3.70, 3.88), (2.45, 3.66, 2.86), (2.85, 3.40, 2.47), (2.53, 3.41, 2.77),
        (3.05, 3.60, 2.36), (2.37, 3.39, 3.19), (2.28, 3.73, 3.09), (3.50, 3.10, 2.20), (2.84, 3.40, 2.54),
        (2.71, 3.92, 2.41), (3.15, 3.97, 2.12)]


def period():
    return {"id": "p1", "periodNumber": "20261122", "deadline": 1795269600000, "maxWinnings": 122872890,
            "currency": "NGN", "elements": [
                {"index": i + 1, "home": f"H{i}", "away": f"A{i}", "eventId": f"sr:match:{i}", "result": 0,
                 "outcomes": [{"id": "1", "odds": str(o[0])}, {"id": "2", "odds": str(o[1])},
                              {"id": "3", "odds": str(o[2])}]} for i, o in enumerate(WEEK)]}


def test_fair_chances_drop_the_margin():
    f = jp.fair([2.0, 4.0, 4.0])
    assert abs(sum(f) - 1) < 1e-9 and f[0] == 0.5


def test_the_single_line_is_each_favourite_and_its_chance_is_the_product():
    a = jp.analyse(period())
    one = a["tickets"][0]
    assert one["lines"] == 1 and one["picks"][1] == "1" and one["picks"][8] == "2"
    fav = math.prod(max(jp.fair(o)) for o in WEEK)
    assert abs(one["prizes"]["12"] - fav) < 1e-8      # stored to 8 decimals
    assert one["prizes"]["10"] > one["prizes"]["11"] > one["prizes"]["12"]


def test_more_lines_cover_the_closest_matches_first_and_never_exceed_the_budget():
    probs = [jp.fair(o) for o in WEEK]
    for b in (2, 4, 16, 64):
        t = jp.best_ticket(probs, b)
        assert jp.lines(t) <= b
    four = jp.best_ticket(probs, 4)
    doubled = [i for i, c in enumerate(four) if len(c) > 1]
    # the doubles go where the favourite is weakest
    weakest = sorted(range(12), key=lambda i: max(probs[i]))[:2]
    assert set(doubled) == set(weakest)
    a = jp.analyse(period())
    chances = [t["prizes"]["12"] for t in a["tickets"]]
    assert chances == sorted(chances)


def test_our_model_is_used_where_a_prediction_is_linked():
    a = jp.analyse(period(), {"sr:match:0": {"p_home": 0.6, "p_draw": 0.25, "p_away": 0.15}})
    assert a["matches"][0]["source"] == "model" and a["matches"][0]["p"][0] == 0.6 and a["model_matches"] == 1
    assert a["matches"][1]["source"] == "odds"


def test_a_week_is_scored_once_every_result_is_in():
    els = [{"index": 1, "result": 1}, {"index": 2, "result": 2}, {"index": 3, "result": 3}]
    assert jp.score_week(els, {1: "1", 2: "X", 3: "1"}) == 2
    assert jp.score_week(els + [{"index": 4, "result": 0}], {1: "1"}) is None


def test_correct_counts_add_up():
    d = jp.correct_counts([0.5, 0.5])
    assert d == [0.25, 0.5, 0.25] and jp.at_least(d, 1) == 0.75
