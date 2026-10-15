import optimizer
import reasons

PRED = {"home": "Arsenal", "away": "Chelsea", "date": "2026-10-15", "xg_home": 1.9, "xg_away": 1.1,
        "elo": {"label": "Clear Advantage", "leading": "Arsenal", "gap": 120},
        "set_pieces": {"corners": {"mean": 10.4}, "corners_home": {"mean": 5.6}}}


def test_goals_and_result_context():
    assert reasons.context(PRED, "goals_ou") == ["Expected goals: Arsenal 1.9, Chelsea 1.1 (3.0 in total)"]
    assert reasons.context(PRED, "1x2") == ["Clear Advantage: Arsenal rated 120 points higher"]
    assert reasons.context({**PRED, "elo": {"label": "Evenly Matched", "leading": "Arsenal", "gap": 5}},
                           "double_chance") == ["The two teams' ratings are close"]


def test_counts_context():
    assert reasons.context(PRED, "corners_ou") == ["We expect about 10.4 corners"]
    assert reasons.context(PRED, "home_corners_ou") == ["We expect Arsenal about 5.6 corners"]
    assert reasons.context({"home": "A", "away": "B"}, "goals_ou") == []


def test_why_price_lines():
    good = reasons.why(0.7, 1.6, "sportybet", ["x"])
    assert good[0] == "Our model gives it 70%"
    assert "good value" in good[1] and good[2] == "x"
    fair = reasons.why(0.6, 1.68, "bookmaker")
    assert fair[1] == "The market's price implies 60%, about what we think"
    assert reasons.why(0.7, 1.5, "estimated") == ["Our model gives it 70%"]
    assert len(reasons.why(0.7, 1.6, "sportybet", ["a", "b", "c"])) == 3


def test_optimizer_picks_carry_why():
    o = optimizer.Option("Arsenal", "Chelsea", "2026-10-15", "", "PL", "goals_ou", "Total Goals", "O15",
                         "Over 1.5", 0.8, 1.3, "sportybet", context=["ctx"])
    d = optimizer._result([o], 1.2, 1.4)["picks"][0]
    assert "context" not in d
    assert d["why"][0] == "Our model gives it 80%" and d["why"][-1] == "ctx"


def test_context_reads_bare_elo_gap_and_expected_goals():
    pred = {"home": "Arsenal", "away": "Chelsea", "xg_home": 1.8, "xg_away": 0.9, "elo_gap": -120}
    assert reasons.context(pred, "1x2") == ["Clear Advantage: Chelsea rated 120 points higher"]
    assert reasons.context(pred, "goals_ou") == ["Expected goals: Arsenal 1.8, Chelsea 0.9 (2.7 in total)"]
    assert reasons.context({**pred, "elo_gap": 10}, "double_chance") == ["The two teams' ratings are close"]
