"""
Learning from each finished match (live_learning.py): fed into the models
once, however often it arrives, and the teams' upcoming predictions redone.
"""

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

import live_learning as ll
import main
import set_pieces


class FakePredictor:
    def __init__(self):
        self.updates = []

    def canon(self, name):
        return {"Arsenal FC": "Arsenal", "Chelsea FC": "Chelsea"}.get(name, name)

    def _update(self, home, away, result, fthg, ftag, **kw):
        self.updates.append((home, away, result, fthg, ftag, kw))


def entry(day, home="Arsenal FC", away="Chelsea FC", hg=2, ag=1, **res):
    return {"home": home, "away": away, "date": day, "league": "PL",
            "result": {"status": "finished", "hg": hg, "ag": ag, "corners": [6, 4], "bookings": [2, 3],
                       "shots": [15, 9], "sot": [6, 3], **res}}


class TestOnce:
    def test_a_match_counts_once_however_often_it_arrives(self):
        p = FakePredictor()
        ll.mark_trained(p, pd.DataFrame(columns=["Date", "HomeTeam", "AwayTeam"]), p.canon)
        row = ll.row_from_entry(entry("2026-09-27"))
        assert ll.learn(row, predictor=p) == {"Arsenal", "Chelsea"}
        assert ll.learn(row, predictor=p) == set()
        # The 3-hourly fetch has it a day later (UTC vs local date), by its source's names
        again = ll.result_row({"Date": "2026-09-28", "HomeTeam": "Arsenal", "AwayTeam": "Chelsea", "FTHG": 2, "FTAG": 1})
        assert ll.learn(again, predictor=p) == set()
        assert len(p.updates) == 1
        home, away, result, hg, ag, kw = p.updates[0]
        assert (home, away, result, hg, ag) == ("Arsenal", "Chelsea", "H", 2.0, 1.0)
        assert (kw["hst"], kw["ast"], kw["hyc"], kw["ayc"]) == (6.0, 3.0, 2.0, 3.0)

    def test_training_matches_are_never_fed_again(self):
        p = FakePredictor()
        trained = pd.DataFrame({"Date": [pd.Timestamp(date.today() - timedelta(days=2))],
                                "HomeTeam": ["Arsenal"], "AwayTeam": ["Chelsea"]})
        assert ll.mark_trained(p, trained, p.canon) == 1
        row = ll.row_from_entry(entry((date.today() - timedelta(days=2)).isoformat()))
        assert ll.learn(row, predictor=p) == set() and p.updates == []

    def test_a_model_without_memory_is_left_alone(self):
        p = FakePredictor()                              # never marked: can't tell what it holds
        assert ll.learn(ll.row_from_entry(entry("2026-09-27")), predictor=p) == set() and p.updates == []

    def test_unfinished_or_extra_time_is_not_a_result(self):
        assert ll.row_from_entry({**entry("2026-09-27"), "result": {"status": "live", "hg": 1, "ag": 0}}) is None
        assert ll.row_from_entry(entry("2026-09-27", aet=True)) is None
        assert ll.row_from_entry({**entry("2026-09-27"), "sport": "tennis"}) is None


def test_corners_bookings_and_shots_models_learn_too():
    sp = set_pieces.SetPieceModel()
    ll.mark_trained(sp, None)
    shots = set_pieces.SetPieceModel()
    ll.mark_trained(shots, None)
    before = sp.matches.get("Arsenal", 0)
    row = ll.row_from_entry(entry("2026-09-27", home="Arsenal", away="Chelsea"))
    assert ll.learn(row, set_pieces=sp, shots=shots) == {"Arsenal", "Chelsea"}
    assert sp.matches["Arsenal"] == before + 1 and "corners" in sp.team["Arsenal"]
    assert "sot" in shots.team["Arsenal"]
    ll.learn(row, set_pieces=sp, shots=shots)
    assert sp.matches["Arsenal"] == before + 1
    # Internationals only move the goals model (their corners/shots models are rebuilt nightly)
    assert ll.learn({**row, "HomeTeam": "Ghana", "AwayTeam": "Mali"}, set_pieces=sp, international=True) == set()


def test_the_server_learns_and_redoes_the_teams_predictions(monkeypatch):
    from tests.unit.test_user_endpoints import FakeRedis
    fake = FakeRedis()
    monkeypatch.setattr(main, "_get_redis", lambda: fake)
    p = FakePredictor()
    ll.mark_trained(p, None)
    monkeypatch.setattr(main, "_predictor", p)
    monkeypatch.setattr(main, "_set_pieces", None)
    monkeypatch.setattr(main, "_shots", None)
    today = date.today().isoformat()
    monkeypatch.setattr(main, "_md_many", lambda r, days: {today: {"a": entry(today)}})
    later = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")
    preds = [{"home": "Arsenal FC", "away": "Spurs", "date": later, "time": "15:00", "p_home": 0.5, "odds_home": 1.9},
             {"home": "Leeds", "away": "Everton", "date": later, "time": "15:00", "p_home": 0.4}]
    monkeypatch.setattr(main, "_predictions_cache", preds)
    monkeypatch.setattr(main, "_save_predictions_cache", lambda: None)
    rebuilt = []

    def build(predictor, fixtures, odds):
        rebuilt.append((fixtures[0]["home"], odds))
        return [{**fixtures[0], "p_home": 0.55}]
    monkeypatch.setattr(main, "_build_predictions", build)

    got = main._learn_finished_matches(1)
    assert got == {"matches": 1, "teams": ["Arsenal", "Chelsea"], "repredicted": 1}
    assert main._predictions_cache[0]["p_home"] == 0.55 and main._predictions_cache[1]["p_home"] == 0.4
    assert rebuilt[0][0] == "Arsenal FC" and list(rebuilt[0][1].values())[0]["1"] == 1.9   # odds kept
    assert main._learn_finished_matches(1) is None                                          # already learned
    assert len(p.updates) == 1


def test_the_match_just_played_is_in_the_teams_averages(monkeypatch):
    import match_facts
    today = date.today().isoformat()
    monkeypatch.setattr(main, "_recent_md", (0.0, None))
    monkeypatch.setattr(main, "_predictor", None)
    monkeypatch.setattr(main, "_md_day_view", lambda d: {"a": entry(today, home="Arsenal", away="Chelsea")} if d == today else {})
    idx = main._recent_results_frame()
    a = match_facts.averages(idx, "Arsenal", (date.today() + timedelta(days=1)).isoformat())
    assert a["played"] == 1 and a["corners"]["for"] == 6.0 and a["sot"]["against"] == 3.0
    assert a["bookings"] == {"for": 2.0, "against": 3.0, "total": 5.0, "matches": 1}
