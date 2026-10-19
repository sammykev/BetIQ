import market_ranking as mr


def test_rank_orders_by_hit_rate_and_drops_small_markets():
    rows = [("tennis", "Our tip (winner)", 0.7, True, 1.4)] * 18 + [("tennis", "Our tip (winner)", 0.7, False, 1.4)] * 2
    rows += [("basketball", "Total points", 0.6, i % 2 == 0, None) for i in range(20)]
    rows += [("football", "Tiny", 0.9, True, None)] * 3
    out = mr.rank(rows, 20)
    assert [x["market"] for x in out] == ["Our tip (winner)", "Total points"]
    assert abs(out[0]["hit_rate"] - 0.9) < 1e-9
    assert abs(out[0]["roi"] - (18 * 0.4 - 2) / 20) < 1e-9
    assert out[1]["roi"] is None


def test_sport_rows_groups_best_line_by_market():
    e = {"pred": {"best": {"market": "bb_handicap"}},
         "grades": {"tip": {"prob": 0.8, "verdict": "won"}, "best": {"prob": 0.75, "odds": 1.5, "verdict": "lost"},
                    "points": {"prob": None, "verdict": "won"}}}
    rows = mr.sport_rows("basketball", [e])
    assert ("basketball", "Our tip (winner)", 0.8, True, None, "bb_winner") in rows
    assert ("basketball", "Best line: Handicap", 0.75, False, 1.5, "bb_handicap") in rows
    assert len(rows) == 2


def test_line_names_read_as_words():
    assert mr.line_name("basketball", "bb_h1_home_total") == "1st half home team points"
    assert mr.line_name("tennis", "rk_s1_total") == "1st set total games"
    assert mr.line_name("table_tennis", "rk_s1_total") == "1st game total points"
    assert mr.line_name("basketball", "bb_total") == "Total points"


def test_summary_carries_the_previous_hit_rate():
    rows = [("tennis", "Our tip (winner)", 0.7, True, None)] * 3 + [("tennis", "Our tip (winner)", 0.7, False, None)]
    out = mr.summary(rows, mr.rank(rows, 1), {"markets": [{"sport": "tennis", "market": "Our tip (winner)", "hit_rate": 0.6}]})
    assert out["markets"][0]["before"] == 0.6
    assert out["sports"] == [{"sport": "tennis", "n": 4, "hit_rate": 0.75, "said": 0.7}]


def test_every_quarter_line_is_graded_not_only_the_best():
    e = {"result": {"status": "finished", "score": [85, 82], "periods": [[20, 20], [22, 20], [21, 22], [22, 20]], "ot": False},
         "pred": {"best": {"market": "bb_q1_total"},
                  "lines": [["bb_q1_total", "O35.5", 0.8], ["bb_q2_total", "U40.5", 0.7], ["bb_h1_total", "O80.5", 0.6],
                            ["bb_q3_total", "O50.5", 0.3], ["bb_total", "O150.5", 0.9]]},
         "grades": {"best": {"prob": 0.8, "odds": 1.3, "verdict": "won"}}}
    rows = mr.sport_rows("basketball", [e])
    assert ("basketball", "1st quarter total points", 0.8, True, None, "bb_q1_total") in rows
    assert ("basketball", "2nd quarter total points", 0.7, False, None, "bb_q2_total") in rows
    assert ("basketball", "1st half total points", 0.6, True, None, "bb_h1_total") in rows
    # under 50% and full-game lines aren't part lines; the best line isn't counted twice
    assert len(rows) == 3
    assert mr.by_code(rows, 1)["basketball:bb_q2_total"] == {"n": 1, "hit_rate": 0.0, "said": 0.7, "roi": None}
