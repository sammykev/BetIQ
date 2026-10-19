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
    assert ("basketball", "Our tip (winner)", 0.8, True, None) in rows
    assert ("basketball", "Best line: Handicap", 0.75, False, 1.5) in rows
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
