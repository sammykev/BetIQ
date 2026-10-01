"""insights.py: settled picks grouped, measured against our chance and the odds."""

import random

import insights


def row(prob, won, odds=None, **kw):
    r = {"sport": "tennis", "market": "winner", "league": "ATP", "prob": prob, "won": won, **kw}
    if odds:
        r["odds"], r["edge"] = odds, prob * odds - 1
    return r


def test_stats_lift_z_and_roi():
    rows = [row(0.6, True, 1.8)] * 70 + [row(0.6, False, 1.8)] * 30
    s = insights.stats(rows)
    assert s["n"] == 100 and s["hit"] == 0.7 and s["said"] == 0.6 and abs(s["lift"] - 0.1) < 1e-9
    assert s["z"] > 2 and s["bets"] == 100 and abs(s["roi"] - (70 * 0.8 - 30) / 100) < 1e-9


def test_findings_need_size_and_strength():
    random.seed(2)
    good = [row(0.6, random.random() < 0.75, 1.7, league="Good") for _ in range(300)]
    fair = [row(0.6, random.random() < 0.6, 1.6, league="Fair") for _ in range(300)]
    tiny = [row(0.6, True, 1.7, league="Tiny") for _ in range(10)]
    rep = insights.analyse(good + fair + tiny)
    better = [x["group"] for x in rep["findings"]["better_than_we_say"]]
    assert "tennis · Good" in better and "tennis · Fair" not in better and "tennis · Tiny" not in better
    assert rep["groups"]["league"]["tennis · Good"]["roi"] > 0


def test_side_rows_from_a_graded_match():
    e = {"league_name": "ATP Beijing", "date": "2026-09-30", "time": "07:05",
         "pred": {"tip_code": "2", "odds_home": 2.6, "odds_away": 1.5, "rated": True},
         "grades": {"tip": {"pick": "B", "prob": 0.66, "verdict": "won"},
                    "games": {"pick": "Over 21.5", "prob": 0.58, "verdict": "lost"},
                    "best": {"pick": "B +1.5 sets", "prob": 0.9, "odds": 1.12, "verdict": "won"}}}
    rows = insights.side_rows("tennis", e)
    w = rows[0]
    assert w["market"] == "winner" and w["odds"] == 1.5 and w["favourite"] is True and w["side"] == "away"
    assert w["rated"] is True and w["weekday"] == "Wed"
    assert [r["market"] for r in rows] == ["winner", "total", "best line"]
