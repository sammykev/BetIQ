import sport_accuracy as sa


def _e(league, p_home, score, tip_ok, prob=0.7, games_ok=None, odds=(1.5, 2.6)):
    grades = {"tip": {"pick": "x", "prob": prob, "verdict": "won" if tip_ok else "lost"}}
    if games_ok is not None:
        grades["games"] = {"pick": "Over", "prob": 0.6, "verdict": "won" if games_ok else "lost"}
    return {"league": league, "league_name": league, "flag": "🎾", "grades": grades,
            "pred": {"p_home": p_home, "odds_home": odds[0], "odds_away": odds[1]},
            "result": {"status": "finished", "score": score}}


def test_accuracy_from_match_days():
    days = {"2026-09-28": [_e("ATP Beijing", 0.7, [2, 0], True, games_ok=True),
                           _e("ATP Beijing", 0.65, [0, 2], False, prob=0.65, games_ok=False)],
            "2026-09-29": [_e("WTA Wuhan", 0.8, [2, 1], True, prob=0.8),
                           {"league": "x", "grades": None, "pred": {}, "result": {"status": "scheduled"}}]}
    a = sa.accuracy(days, "tennis")
    assert a["matches"] == 3
    assert a["markets"]["tip"] == {"n": 3, "name": "Our tip (winner)", "hit_rate": round(2 / 3, 4),
                                   "avg_prob": round((0.7 + 0.65 + 0.8) / 3, 4)}
    assert a["markets"]["games"]["n"] == 2 and a["markets"]["games"]["name"] == "Total games"
    assert [d["favourite_hit"] for d in a["daily"]] == [0.5, 1.0]
    assert a["leagues"][0] == {"league": "ATP Beijing", "name": "ATP Beijing", "flag": "🎾", "n": 2, "hits": 1, "hit_rate": 0.5}
    assert a["brier"]["matches"] == 3 and a["brier"]["vs_bookmaker"] is None      # fewer than 20
    assert sum(b["n"] for b in a["calibration"]) == 5
    assert sa.accuracy(days, "table_tennis")["markets"]["games"]["name"] == "Total points"


def test_retired_matches_count_only_by_their_grades():
    e = _e("ITF", 0.6, [1, 0], True)
    e["result"]["ret"] = True
    e["grades"] = {}
    assert sa.accuracy({"2026-09-29": [e]}, "tennis")["matches"] == 0
