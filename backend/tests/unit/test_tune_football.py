"""
Per-league corners / bookings / shots settings (tune_football.py) and the
models reading them (set_pieces.SetPieceModel._p, "by_league").
"""

import json

import numpy as np
import pandas as pd

import set_pieces
import tune_football as tf
from set_pieces import SetPieceModel


def _league(name, start="2022-08-05", rounds=40):
    teams = [f"{name} {i}" for i in range(6)]
    rows, day = [], pd.Timestamp(start)
    for r in range(rounds):
        for i, h in enumerate(teams):
            for j, a in enumerate(teams):
                if h != a and (i + j + r) % 3 == 0:
                    rows.append({"Date": day, "HomeTeam": h, "AwayTeam": a, "league": name,
                                 "HC": 5 + i % 3, "AC": 3 + j % 3, "HY": 2 + (i + r) % 2, "AY": 2 + j % 2,
                                 "HR": 0, "AR": 0})
                    day += pd.Timedelta(days=1)
    return pd.DataFrame(rows)


def test_a_leagues_own_setting_comes_first():
    m = SetPieceModel({"decay": 0.95, "by_league": {"PL": {"decay": 0.9}}})
    assert m._p("decay", "PL") == 0.9
    assert m._p("decay", "SA") == 0.95 and m._p("decay") == 0.95
    assert m._p("prior", "PL") == set_pieces.PRIOR          # not tuned: the default


def test_tagged_replay_adds_the_league_and_changes_nothing_else():
    data = _league("PL")
    _, plain = SetPieceModel.replay(data, test_from=data["Date"].min())
    _, tagged = SetPieceModel.replay(data, test_from=data["Date"].min(), tag=True)
    assert [r[:5] for r in tagged] == plain and {r[5] for r in tagged} == {"PL"}


def test_by_league_changes_only_that_leagues_predictions():
    data = pd.concat([_league("PL"), _league("SA")]).sort_values("Date")
    start = data["Date"].min()
    _, base = SetPieceModel.replay(data, test_from=start, tag=True)
    _, tuned = SetPieceModel.replay(data, test_from=start, params={"by_league": {"PL": {"decay": 0.8}}}, tag=True)
    pl = [(a[2], b[2]) for a, b in zip(base, tuned) if a[5] == "PL"]
    sa = [(a[2], b[2]) for a, b in zip(base, tuned) if a[5] == "SA"]
    assert any(abs(x - y) > 1e-9 for x, y in pl)
    assert all(abs(x - y) < 1e-9 for x, y in sa)


def test_vectorised_cdf_matches_the_model():
    mu = np.array([2.5, 6.0, 10.4])
    for r in (None, 9.0):
        cdf = tf._cdfs(mu, r, 11)
        for i, m in enumerate(mu):
            for line in (2.5, 7.5, 10.5):
                assert abs((1 - cdf[i, int(line)]) - set_pieces.p_over(line, m, r)) < 1e-9


def test_tune_reports_every_league_and_keeps_defaults_without_a_clear_win():
    data = pd.concat([_league("PL", rounds=80), _league("SA", rounds=80)]).sort_values("Date")
    rep = tf.tune("set_pieces", data)
    assert set(rep["leagues"]) == {"PL", "SA"}
    for lg, v in rep["leagues"].items():
        assert v["keep"] == (lg in rep["tuned"])
        if v["keep"]:
            assert v["tuned"] <= v["default"] * (1 - tf.MIN_GAIN)
    assert rep["overall"]["default"]["matches"] == rep["overall"]["used"]["matches"] > 0


class _Redis:
    def __init__(self, value=None):
        self.value = value

    def get(self, key):
        assert key == tf.TUNING_KEY
        return self.value


def test_load_reads_each_models_tuned_leagues():
    saved = {"set_pieces": {"tuned": {"PL": {"decay": 0.9}}}, "shots": {"tuned": {}}}
    assert tf.load(_Redis(json.dumps(saved))) == {"set_pieces": {"PL": {"decay": 0.9}}, "shots": {}}
    assert tf.load(_Redis()) == {"set_pieces": {}, "shots": {}}
    assert tf.load(_Redis("not json")) == {"set_pieces": {}, "shots": {}}
    assert tf.load(None) == {"set_pieces": {}, "shots": {}}
