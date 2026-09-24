"""
Backtest: metrics are computed correctly from records, and the walk-forward
loop never lets a model see the month it is predicting.
"""

import json
import math

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import backtest
import main
from backtest import calibration, confidence_tier, summarize, walk_forward


def rec(result="H", score=(2, 0), probs=(0.6, 0.25, 0.15), o15=0.8, o25=0.55,
        odds=(1.6, 4.0, 6.0), ou=(1.9, 1.9), league="PL", date="2025-01-10"):
    from predictor import pick_tips
    return {
        "date": date, "league": league, "home": "A", "away": "B",
        "p_home": probs[0], "p_draw": probs[1], "p_away": probs[2], "p_over15": o15, "p_over25": o25,
        **pick_tips(*probs, o15, o25),
        "odds_home": odds[0], "odds_draw": odds[1], "odds_away": odds[2],
        "odds_over25": ou[0], "odds_under25": ou[1],
        "home_goals": score[0], "away_goals": score[1], "result": result,
    }


class TestTiers:
    def test_mirror_frontend_thresholds(self):
        # Same numbers as frontend/lib/picks.ts confidenceTier
        assert confidence_tier(0.6, "single") == "strong"
        assert confidence_tier(0.45, "single") == "lean"
        assert confidence_tier(0.44, "single") == "weak"
        assert confidence_tier(0.8, "double") == "strong"
        assert confidence_tier(0.65, "double") == "lean"
        assert confidence_tier(0.75, "goals") == "strong"
        assert confidence_tier(0.59, "goals") == "weak"


class TestSummarize:
    def test_log_loss_and_brier(self):
        m = summarize([rec(result="H"), rec(result="A", score=(0, 1))])
        model = m["match_result"]["model"]
        expected_ll = (-math.log(0.6) - math.log(0.15)) / 2
        assert model["log_loss"] == pytest.approx(expected_ll, abs=1e-4)
        brier_h = (0.4 ** 2 + 0.25 ** 2 + 0.15 ** 2)
        brier_a = (0.6 ** 2 + 0.25 ** 2 + 0.85 ** 2)
        assert model["brier"] == pytest.approx((brier_h + brier_a) / 2, abs=1e-4)
        assert model["accuracy"] == 0.5

    def test_market_removes_the_margin(self):
        m = summarize([rec(odds=(2.0, 4.0, 4.0))])  # raw 0.5+0.25+0.25 = 1.0
        assert m["match_result"]["market"]["log_loss"] == pytest.approx(-math.log(0.5), abs=1e-4)

    def test_matches_without_odds_are_left_out_of_the_market_comparison(self):
        m = summarize([rec(), rec(odds=(0, 0, 0))])
        assert m["matches"] == 2 and m["match_result"]["n"] == 1

    def test_picks_grouped_by_code_and_tier(self):
        m = summarize([
            rec(result="H"),                                          # "1" at 0.6 → strong, won
            rec(result="D", score=(1, 1)),                            # "1" strong, lost
            rec(result="D", score=(0, 0), probs=(0.45, 0.35, 0.2)),   # "X" 0.35 → weak, won
        ])
        by_code = {r["code"]: r for r in m["picks"]["by_code"]}
        assert (by_code["1"]["n"], by_code["1"]["won"]) == (2, 1)
        strong = next(r for r in m["picks"]["by_tier"] if r["kind"] == "single" and r["tier"] == "strong")
        assert (strong["n"], strong["hit_rate"]) == (2, 0.5)

    def test_goals_tips_count_pushes_separately(self):
        # o25 0.6 → "Over 2.0 (Asian)"; 1-1 is a push, 2-1 a win, 1-0 a loss
        records = [rec(o15=0.7, o25=0.6, score=s, result=r)
                   for s, r in [((1, 1), "D"), ((2, 1), "H"), ((1, 0), "H")]]
        [row] = summarize(records)["goals_tips"]["by_tip"]
        assert row["tip"] == "Over 2.0 (Asian)"
        assert (row["won"], row["lost"], row["push"], row["hit_rate"]) == (1, 1, 1, 0.5)

    def test_flat_stake_returns(self):
        # Two home tips at 1.6: one wins (+0.6), one loses (-1)
        m = summarize([rec(result="H"), rec(result="A", score=(0, 1))])
        straight = m["betting"]["all_straight_tips"]
        assert (straight["bets"], straight["won"], straight["profit"]) == (2, 1, -0.4)
        assert straight["roi"] == -0.2

    def test_value_bets_need_an_edge(self):
        # Model 0.6 vs implied 1/1.6 = 0.625 → no edge; at 2.2 (0.4545) → edge
        m = summarize([rec(), rec(odds=(2.2, 3.5, 3.5))])
        assert m["betting"]["value_bets"]["bets"] == 1

    def test_calibration_buckets(self):
        buckets = {b["from"]: b for b in calibration([rec(result="H")])}
        assert buckets[0.6]["n"] == 1 and buckets[0.6]["actual"] == 1.0
        assert buckets[0.1]["actual"] == 0.0  # the 0.15 away prob didn't happen

    def test_empty(self):
        m = summarize([])
        assert m["matches"] == 0 and m["period"] is None


class _FakeModel:
    """Records what it trained on; predicts fixed probabilities."""
    trained_through = []

    def train(self, df):
        _FakeModel.trained_through.append(df["Date"].max())

    def _feats(self, *a, **k):
        return {}

    def predict_proba(self, feats):
        return 0.5, 0.3, 0.2, 0.7, 0.5

    def _update(self, *a, **k):
        pass


class TestWalkForward:
    def _matches(self):
        days = pd.date_range("2024-01-01", "2024-04-28", freq="7D")
        return pd.DataFrame({
            "Date": days, "HomeTeam": "A", "AwayTeam": "B", "Result": "H",
            "FTHG": 2, "FTAG": 1, "B365H": 1.8, "B365D": 3.5, "B365A": 4.5, "league": "PL",
        })

    def test_never_trains_on_the_month_it_predicts(self):
        _FakeModel.trained_through = []
        records = walk_forward(self._matches(), "2024-03-01", "2024-04-30",
                               min_train=1, make_model=_FakeModel, log=lambda *_: None)
        assert len(_FakeModel.trained_through) == 2   # March, April
        assert _FakeModel.trained_through[0] < pd.Timestamp("2024-03-01")
        assert _FakeModel.trained_through[1] < pd.Timestamp("2024-04-01")
        assert records and all(r["date"] >= "2024-03-01" for r in records)
        assert records[0]["tip_code"] == "1X"  # 0.5 + 0.3 draw clears the 0.75 double-chance bar

    def test_skips_months_without_enough_history(self):
        records = walk_forward(self._matches(), "2024-01-01", "2024-01-31",
                               min_train=1, make_model=_FakeModel, log=lambda *_: None)
        assert records == []


class TestEndpoint:
    URL = "/api/admin/model-metrics"

    @pytest.fixture(autouse=True)
    def admin_secret(self, monkeypatch):
        monkeypatch.setattr(main, "ADMIN_SECRET", "s3cret")

    def test_serves_the_metrics_file(self, tmp_path, monkeypatch):
        path = tmp_path / "m.json"
        path.write_text(json.dumps({"matches": 12}))
        monkeypatch.setattr(backtest, "METRICS_PATH", str(path))
        r = TestClient(main.app).get(self.URL, headers={"X-Admin-Secret": "s3cret"}).json()
        assert r == {"available": True, "matches": 12}

    def test_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(backtest, "METRICS_PATH", str(tmp_path / "nope.json"))
        assert TestClient(main.app).get(self.URL, headers={"X-Admin-Secret": "s3cret"}).json() == {"available": False}

    @pytest.mark.parametrize("secret", ["", "wrong"])
    def test_admin_only(self, secret):
        assert TestClient(main.app).get(self.URL, headers={"X-Admin-Secret": secret}).status_code == 403

    def test_old_public_path_is_gone(self):
        assert TestClient(main.app).get("/api/model/metrics").status_code == 404


def test_without_odds_summary_keeps_the_headline_numbers():
    m = summarize([rec(result="H"), rec(result="A", score=(0, 1), probs=(0.2, 0.2, 0.6))])
    w = backtest.without_odds_summary(m)
    assert w["match_result"] == m["match_result"]
    assert (w["straight"]["kind"], w["straight"]["tier"]) == ("single", "all")
    assert w["straight"]["n"] == 2
