"""
Settling predictions: 1X2 and double chance tips, goals tips including Asian
lines with pushes, and re-settling entries archived by the old grader.
"""

import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import main
from grading import grade_goals, grade_prediction, grade_tip, parse_score, regrade, to_goals
from tests.unit.test_user_endpoints import FakeRedis


class TestGradeTip:
    @pytest.mark.parametrize("code,result,expected", [
        ("1", "H", "won"), ("1", "D", "lost"), ("X", "D", "won"), ("2", "A", "won"),
        ("1X", "H", "won"), ("1X", "D", "won"), ("1X", "A", "lost"),
        ("2X", "A", "won"), ("2X", "D", "won"), ("2X", "H", "lost"),
        ("X2", "D", "won"), ("12", "D", "lost"), ("12", "A", "won"),
    ])
    def test_codes(self, code, result, expected):
        assert grade_tip(code, result) == expected

    @pytest.mark.parametrize("code", ["?", "Skip", "", None])
    def test_no_tip_is_void(self, code):
        assert grade_tip(code, "H") == "void"


class TestGradeGoals:
    # Exactly the strings predictor.predict_match emits
    @pytest.mark.parametrize("tip,score,expected", [
        ("Over 1.5 Goals", (1, 1), "won"),
        ("Over 1.5 Goals", (1, 0), "lost"),
        ("Over 2.0 (Asian)", (2, 1), "won"),
        ("Over 2.0 (Asian)", (1, 1), "push"),
        ("Over 2.0 (Asian)", (1, 0), "lost"),
        ("Over 1.0 (Asian)", (1, 0), "push"),
        ("Over 1.0 (Asian)", (0, 0), "lost"),
        ("Over 1.0 (Asian)", (2, 0), "won"),
        ("Under 3.0 (Asian)", (1, 1), "won"),
        ("Under 3.0 (Asian)", (2, 1), "push"),
        ("Under 3.0 (Asian)", (3, 1), "lost"),
    ])
    def test_predictor_tips(self, tip, score, expected):
        assert grade_goals(tip, *score) == expected

    @pytest.mark.parametrize("tip,total,expected", [
        ("Over 2.25", 3, "won"), ("Over 2.25", 2, "half_lost"), ("Over 2.25", 1, "lost"),
        ("Over 2.75", 3, "half_won"), ("Over 2.75", 2, "lost"), ("Over 2.75", 4, "won"),
        ("Under 2.25", 2, "half_won"), ("Under 2.75", 3, "half_lost"),
    ])
    def test_quarter_lines_split_the_stake(self, tip, total, expected):
        assert grade_goals(tip, total, 0) == expected

    def test_btts(self):
        assert grade_goals("BTTS Yes", 1, 1) == "won"
        assert grade_goals("BTTS Yes", 2, 0) == "lost"
        assert grade_goals("BTTS No", 2, 0) == "won"

    @pytest.mark.parametrize("tip", ["Skip", "", None, "Handicap -1"])
    def test_no_goals_tip(self, tip):
        assert grade_goals(tip, 2, 1) is None


PRED = {"home": "Arsenal", "away": "Chelsea", "tip_code": "1X", "tip_goals": "Over 2.0 (Asian)"}


class TestGradePrediction:
    def test_score_grades_both_markets(self):
        out = grade_prediction(PRED, home_goals=1, away_goals=1)
        assert out["actual_result"] == "D"
        assert out["outcome"] == "won"          # 1X covers the draw
        assert out["goals_outcome"] == "push"   # exactly 2 goals on a 2.0 line
        assert out["score"] == "1-1"
        assert "outcome" not in PRED            # input untouched

    def test_result_only_leaves_goals_ungraded(self):
        out = grade_prediction(PRED, result="A")
        assert out["outcome"] == "lost"
        assert out["goals_outcome"] is None

    def test_score_wins_over_a_conflicting_result(self):
        assert grade_prediction(PRED, result="H", home_goals=0, away_goals=2)["actual_result"] == "A"

    def test_no_result_is_pending(self):
        out = grade_prediction(PRED)
        assert (out["outcome"], out["actual_result"], out["goals_outcome"]) == ("pending", None, None)

    def test_goals_only_pick_is_void_on_1x2_but_graded_on_goals(self):
        out = grade_prediction({**PRED, "tip_code": "?"}, home_goals=3, away_goals=0)
        assert out["outcome"] == "void"
        assert out["goals_outcome"] == "won"


class TestRegrade:
    def test_fixes_double_chance_marked_lost_by_old_grader(self):
        old = {**PRED, "outcome": "lost", "actual_result": "D"}
        assert regrade(old)["outcome"] == "won"

    def test_uses_stored_score_for_goals(self):
        old = {**PRED, "outcome": "lost", "actual_result": "D", "score": "2-2"}
        assert regrade(old)["goals_outcome"] == "won"

    def test_pending_entries_are_left_alone(self):
        p = {**PRED, "outcome": "pending", "actual_result": None}
        assert regrade(p) == p


class TestHelpers:
    def test_parse_score(self):
        assert parse_score("2-1") == (2, 1)
        assert parse_score("0 : 0") == (0, 0)
        assert parse_score(None) is None and parse_score("abc") is None

    def test_to_goals(self):
        assert to_goals(2.0) == 2 and to_goals("3") == 3
        assert to_goals(float("nan")) is None and to_goals("") is None and to_goals(None) is None


# ── Endpoints ────────────────────────────────────────────────────────────────

@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    return fake


@pytest.fixture
def client():
    return TestClient(main.app)


class TestHistoryEndpoints:
    def test_history_regrades_legacy_entries(self, client, redis):
        redis.set("betiq:history:2026-09-20", json.dumps([
            {**PRED, "date": "2026-09-20", "outcome": "lost", "actual_result": "H", "score": "3-0"},
        ]))
        [p] = client.get("/api/history?date=2026-09-20").json()
        assert p["outcome"] == "won" and p["goals_outcome"] == "won"

    def test_calendar_counts_goals_tips(self, client, redis, monkeypatch):
        monkeypatch.setattr(main, "_predictions_cache", [])
        redis.set("betiq:history:2026-09-20", json.dumps([
            {**PRED, "outcome": "won", "actual_result": "D", "score": "1-1"},      # goals push
            {**PRED, "outcome": "won", "actual_result": "H", "score": "2-1"},      # goals won
            {**PRED, "tip_code": "?", "outcome": "pending", "actual_result": "A", "score": "0-1"},  # void; goals lost
        ]))
        day = client.get("/api/calendar?month=2026-09").json()["2026-09-20"]
        assert (day["won"], day["lost"], day["pending"]) == (2, 0, 0)
        assert (day["goals_won"], day["goals_lost"]) == (1, 1)

    def test_feedback_settles_both_markets(self, client, redis, monkeypatch, tmp_path):
        monkeypatch.setattr(main, "RESULTS_CSV", str(tmp_path / "results.csv"))
        monkeypatch.setattr(main, "ADMIN_SECRET", "s3cret")
        redis.set("betiq:history:2026-09-20", json.dumps([{**PRED, "outcome": "pending", "actual_result": None}]))
        body = {"home": "Arsenal", "away": "Chelsea", "date": "2026-09-20",
                "result": "D", "home_score": 1, "away_score": 1}
        # Results change the track record and the training data: admins only
        assert client.post("/api/feedback/result", json=body).status_code == 403
        r = client.post("/api/feedback/result", json=body, headers={"X-Admin-Secret": "s3cret"})
        assert r.status_code == 200
        [p] = json.loads(redis.get("betiq:history:2026-09-20"))
        assert (p["outcome"], p["goals_outcome"], p["score"]) == ("won", "push", "1-1")


class TestArchive:
    def test_archive_grades_from_results_csv(self, redis, monkeypatch, tmp_path):
        csv = tmp_path / "results.csv"
        pd.DataFrame([
            {"Date": "2026-09-20", "HomeTeam": "Arsenal", "AwayTeam": "Chelsea", "Result": "D", "FTHG": 1, "FTAG": 1},
            {"Date": "2026-09-20", "HomeTeam": "Everton", "AwayTeam": "Fulham", "Result": "H", "FTHG": 1, "FTAG": 0},
        ]).to_csv(csv, index=False)
        monkeypatch.setattr(main, "RESULTS_CSV", str(csv))
        monkeypatch.setattr(main, "_predictions_cache", [
            {**PRED, "date": "2026-09-20"},
            {"home": "Everton", "away": "Fulham", "date": "2026-09-20", "tip_code": "?", "tip_goals": "Over 1.0 (Asian)"},
            {"home": "Leeds", "away": "Burnley", "date": "2026-09-20", "tip_code": "1", "tip_goals": "Skip"},
        ])
        main._archive_past_predictions()
        by_home = {p["home"]: p for p in json.loads(redis.get("betiq:history:2026-09-20"))}
        assert (by_home["Arsenal"]["outcome"], by_home["Arsenal"]["goals_outcome"]) == ("won", "push")
        assert (by_home["Everton"]["outcome"], by_home["Everton"]["goals_outcome"]) == ("void", "push")
        assert by_home["Leeds"]["outcome"] == "pending"
