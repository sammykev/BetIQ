"""
Model set: calibrated result and goals models, plus a no-odds set used for
fixtures the odds feed missed.
"""

import numpy as np
import pandas as pd
import pytest
from sklearn.calibration import CalibratedClassifierCV

from predictor import FEATURE_COLS, NO_ODDS_COLS, LeaguePredictor


def season(teams=("Arsenal", "Chelsea", "Leeds", "Hull", "Wolves", "Everton"), rounds=8, seed=1):
    rng = np.random.default_rng(seed)
    rows, day = [], pd.Timestamp("2024-08-01")
    for _ in range(rounds):
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                hg, ag = int(rng.poisson(1.5)), int(rng.poisson(1.1))
                rows.append({"Date": day, "HomeTeam": h, "AwayTeam": a, "FTHG": hg, "FTAG": ag,
                             "Result": "H" if hg > ag else "A" if hg < ag else "D", "league": "PL",
                             "B365H": 2.1, "B365D": 3.4, "B365A": 3.6})
                day += pd.Timedelta(days=2)
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def model():
    m = LeaguePredictor()
    m.train(season())
    return m


def test_no_odds_columns_drop_only_the_odds():
    assert set(FEATURE_COLS) - set(NO_ODDS_COLS) == {"Impl_Home", "Impl_Draw", "Impl_Away"}


def test_trains_both_model_sets_calibrated(model):
    for name in ("win", "o15", "o25", "win_noodds", "o15_noodds", "o25_noodds"):
        assert isinstance(model.models[name], CalibratedClassifierCV), name


def test_features_say_whether_odds_were_used(model):
    assert model._feats("Arsenal", "Chelsea", 1.8, 3.5, 4.5)["_has_odds"] is True
    assert model._feats("Arsenal", "Chelsea")["_has_odds"] is False


def test_fixtures_without_odds_use_the_no_odds_models(model, monkeypatch):
    used = []
    for name in ("win", "win_noodds"):
        real = model.models[name]

        class Spy:
            def __init__(self, inner, name): self.inner, self.name = inner, name
            def predict_proba(self, X):
                used.append((self.name, list(X.columns)))
                return self.inner.predict_proba(X)
        monkeypatch.setitem(model.models, name, Spy(real, name))

    model.predict_match("Arsenal", "Chelsea")
    model.predict_match("Arsenal", "Chelsea", 1.8, 3.5, 4.5)
    assert used[0] == ("win_noodds", NO_ODDS_COLS)
    assert used[1] == ("win", FEATURE_COLS)


def test_probabilities_are_valid(model):
    for odds in ((0, 0, 0), (1.8, 3.5, 4.5)):
        p = model.predict_match("Leeds", "Hull", *odds)
        assert abs(p["p_home"] + p["p_draw"] + p["p_away"] - 1) < 0.01
        assert 0 < p["p_over25"] < p["p_over15"] < 1 or p["p_over25"] <= p["p_over15"]


def test_full_analysis_uses_the_same_probabilities(model):
    quick = model.predict_match("Wolves", "Everton", 2.4, 3.2, 3.0)
    full = model.predict_match_full("Wolves", "Everton", 2.4, 3.2, 3.0)
    one_x_two = {o["code"]: o["prob"] for o in next(m for m in full["markets"] if m["id"] == "1x2")["options"]}
    assert one_x_two["1"] == pytest.approx(quick["p_home"], abs=0.002)


def test_old_cached_models_without_the_no_odds_set_still_predict(model, monkeypatch):
    for name in ("win_noodds", "o15_noodds", "o25_noodds"):
        monkeypatch.delitem(model.models, name)
    assert model.predict_match("Arsenal", "Chelsea") is not None


def test_missing_card_data_is_skipped_not_recorded():
    # International and UCL results carry no cards: pandas hands over NaN
    m = LeaguePredictor()
    m._update("Spain", "Italy", "H", 2, 1, hyc=float("nan"), ayc=float("nan"), match_date="2026-06-01")
    assert m.team_stats["Spain"]["yc"] == []
    assert m._feats("Spain", "Italy")["Home_Cards_Avg"] == 1.5
    m._update("Spain", "Italy", "D", 1, 1, hyc=2, ayc=3, hrc=float("nan"), arc=1, match_date="2026-06-05")
    assert (m.team_stats["Spain"]["yc"], m.team_stats["Italy"]["yc"]) == ([2], [5])


def test_matches_without_card_data_still_train():
    # Before the fix every row here had a NaN cards feature and was dropped
    nations = season(teams=("Spain", "Italy", "France", "Brazil", "Japan", "Chile"), seed=2)
    nations = nations.assign(league="INT", HY=np.nan, AY=np.nan, B365H=np.nan, B365D=np.nan, B365A=np.nan)
    m = LeaguePredictor()
    m.train(nations)
    assert all(np.isfinite(v) for k, v in m._feats("Spain", "Italy").items() if k in FEATURE_COLS)
    p = m.predict_match("Spain", "Italy", league="INT")
    assert 0.1 < p["p_draw"] < 0.5
