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
    assert ("basketball", "Best line: bb_handicap", 0.75, False, 1.5) in rows
    assert len(rows) == 2
