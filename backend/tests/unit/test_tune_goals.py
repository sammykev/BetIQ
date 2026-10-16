"""
Per-league Elo K, home advantage and form decay for the football result /
goals model (predictor.LeaguePredictor.league_params, tune_goals.py).
"""

import json

import pandas as pd

import tune_goals as tg
from predictor import LEAGUE_PARAM_DEFAULTS, LeaguePredictor


def _played(model, league):
    for i, (h, a, r, hg, ag) in enumerate([("A", "B", "H", 3, 0), ("B", "A", "D", 1, 1),
                                           ("A", "B", "H", 2, 0), ("B", "A", "A", 0, 4)]):
        model._update(h, a, r, hg, ag, match_date=f"2026-01-0{i + 1}", competition=league)
    return model


def test_a_leagues_own_setting_comes_first():
    m = LeaguePredictor()
    assert m.param("PL", "k_mult") == LEAGUE_PARAM_DEFAULTS["k_mult"]
    m.league_params = {"PL": {"k_mult": 1.5}}
    assert m.param("PL", "k_mult") == 1.5
    assert m.param("PL", "home_adv") == LEAGUE_PARAM_DEFAULTS["home_adv"]   # not tuned: the default
    assert m.param("SA", "k_mult") == LEAGUE_PARAM_DEFAULTS["k_mult"] and m.param("", "k_mult") == 1.0


def test_league_settings_move_elo_and_form_only_in_that_league():
    plain = _played(LeaguePredictor(), "PL")
    tuned = LeaguePredictor()
    tuned.league_params = {"PL": {"k_mult": 1.5, "home_adv": 40.0, "form_alpha": 0.35}}
    _played(tuned, "PL")
    gap = lambda m: m.elo.get("A") - m.elo.get("B")
    assert gap(tuned) > gap(plain)                           # A's wins count for more
    f_plain = plain._feats("A", "B", match_date="2026-01-09", league="PL")
    f_tuned = tuned._feats("A", "B", match_date="2026-01-09", league="PL")
    assert f_plain != f_tuned
    # Another league's matches aren't touched
    other = LeaguePredictor()
    other.league_params = {"PL": {"k_mult": 1.5, "home_adv": 40.0, "form_alpha": 0.35}}
    assert gap(_played(other, "SA")) == gap(_played(LeaguePredictor(), "SA"))


def test_settings_survive_the_saved_model():
    m = LeaguePredictor()
    m.league_params = {"PL": {"k_mult": 0.75}}
    back = LeaguePredictor._from_payload(m._payload(0.0))
    assert back.league_params == {"PL": {"k_mult": 0.75}} and back.param("PL", "k_mult") == 0.75


def test_load_reads_only_the_kept_settings():
    class R:
        def __init__(self, v):
            self.v = v

        def get(self, k):
            assert k == tg.TUNING_KEY
            return self.v
    rep = {"tuned": {"PL": {"k_mult": 1.25, "junk": 3}, "SA": "bad"}, "leagues": {"BL1": {"best": {"k_mult": 2}}}}
    assert tg.load(R(json.dumps(rep))) == {"PL": {"k_mult": 1.25}}
    assert tg.load(R(None)) == {} and tg.load(R("{not json")) == {} and tg.load(None) == {}


def test_keeps_only_a_clear_win_on_the_held_out_half(monkeypatch):
    days = pd.date_range("2022-01-01", "2025-06-01", freq="7D")
    matches = pd.DataFrame({"Date": days, "HomeTeam": "A", "AwayTeam": "B", "FTHG": 1, "FTAG": 0, "Result": "H"})

    def fake(matches, params, start, cut, make=None):
        # "PL" does better with K x0.5 on both halves; "SA" with home advantage 40
        # on the earlier half only; "L3" better on both but too few matches.
        def per(lg, half):
            p = params.get(lg, {})
            b = 1.0
            if lg == "PL" and p.get("k_mult") == 0.5:
                b = 0.9
            if lg == "SA" and p.get("home_adv") == 40.0:
                b = 0.9 if half == 0 else 1.1
            if lg == "L3" and p.get("form_alpha") == 0.35:
                b = 0.5
            n = 20 if lg == "L3" else 200
            return [n, b * n]
        return {(lg, h): per(lg, h) for lg in ("PL", "SA", "L3") for h in (0, 1)}
    monkeypatch.setattr(tg, "score", fake)
    rep = tg.tune(matches, log=lambda s: None)
    assert rep["tuned"] == {"PL": {**LEAGUE_PARAM_DEFAULTS, "k_mult": 0.5}}
    assert rep["leagues"]["SA"]["best"]["home_adv"] == 40.0 and not rep["leagues"]["SA"]["keep"]
    assert not rep["leagues"]["L3"]["keep"]
    assert rep["overall"]["used"]["brier"] < rep["overall"]["default"]["brier"]
