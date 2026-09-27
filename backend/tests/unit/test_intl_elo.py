"""
National-team Elo (intl_elo.py) and the shots model's strength prior
(set_pieces.STRENGTH / elo_weight): ratings as they stood on a date, and the
walk-forward check keeping Elo only when it predicts better unseen.
"""

import math
import random

import numpy as np
import pandas as pd
import pytest

import intl_elo
import set_pieces
import shots


def results(rows):
    return [{"date": d, "home_team": h, "away_team": a, "home_score": str(hs), "away_score": str(as_),
             "tournament": t, "neutral": "FALSE"} for d, h, a, hs, as_, t in rows]


class TestElo:
    def test_winners_go_up_and_dates_only_look_back(self, monkeypatch):
        monkeypatch.setattr(intl_elo, "MIN_MATCHES", 1)
        tl = intl_elo.EloTimeline.from_rows(results([
            ("2020-01-01", "Spain", "Malta", 5, 0, "Friendly"),
            ("2020-06-01", "Spain", "Malta", 3, 0, "UEFA Euro qualification"),
            ("2021-01-01", "Malta", "Spain", "NA", "NA", "Friendly")]))   # unplayed: ignored
        assert tl.rating("Spain") > 1500 > tl.rating("Malta")
        assert tl.rating("Spain", "2020-01-01") is None                     # nothing before its first match
        assert tl.rating("Spain", "2020-06-01") < tl.rating("Spain")        # the June win not yet counted
        assert tl.strength("Spain") > 0 > tl.strength("Malta")

    def test_needs_enough_matches(self):
        tl = intl_elo.EloTimeline.from_rows(results([("2020-01-01", "Spain", "Malta", 1, 0, "Friendly")]))
        assert tl.rating("Spain") is None    # one match isn't a rating

    def test_prior(self):
        assert intl_elo.strength_prior(None, 0.5) == (1.0, 1.0)
        assert intl_elo.strength_prior(1.0, 0) == (1.0, 1.0)
        f, a = intl_elo.strength_prior(1.0, 0.5)
        assert f == pytest.approx(math.exp(0.5)) and a == pytest.approx(math.exp(-0.5))


def synthetic(n_teams=160, matches=1500, years=4.2, seed=3):
    """Stronger sides really take more shots on target; each team plays few
    matches with stats (like national teams)."""
    rng = random.Random(seed)
    npr = np.random.default_rng(seed)
    strength = {f"T{i}": rng.uniform(-1.2, 1.2) for i in range(n_teams)}
    start = pd.Timestamp("2021-01-01")
    rows = []
    for i in range(matches):
        h, a = rng.sample(sorted(strength), 2)
        d = start + pd.Timedelta(days=int(i * years * 365 / matches))
        edge = strength[h] - strength[a]
        mh, ma = 4.2 * math.exp(0.45 * edge), 3.4 * math.exp(-0.45 * edge)
        hst, ast = int(npr.poisson(mh)), int(npr.poisson(ma))
        rows.append({"Date": d, "HomeTeam": h, "AwayTeam": a, "league": "INT-FRI", "Referee": None,
                     "HS": hst + int(npr.poisson(6)), "AS": ast + int(npr.poisson(5)), "HST": hst, "AST": ast})
    return pd.DataFrame(rows), (lambda team, when=None: strength.get(team))


class TestStrengthPrior:
    def test_a_stronger_side_with_little_data_expects_more(self, monkeypatch):
        frame, strength = synthetic(matches=300)
        monkeypatch.setattr(set_pieces, "STRENGTH", strength)
        params = {"prior": 12.0, "gamma": 0.6, "decay": 0.92, "min_matches": 1, "seed_leagues": True}
        plain = shots.ShotModel.fit(frame, params)
        with_elo = shots.ShotModel.fit(frame, {**params, "elo_weight": 0.5})
        strong, weak = max(plain.team, key=lambda t: strength(t)), min(plain.team, key=lambda t: strength(t))
        # Strong at home against weak: Elo raises the strong side's shots on target
        assert with_elo.expected(strong, weak)["sot_team"][0] > plain.expected(strong, weak)["sot_team"][0]
        assert with_elo.expected(strong, weak)["sot_team"][1] < plain.expected(strong, weak)["sot_team"][1]

    def test_the_check_keeps_elo_only_because_it_scores_better(self, monkeypatch):
        frame, strength = synthetic()
        monkeypatch.setattr(set_pieces, "INTERNATIONAL_GRID", set_pieces.INTERNATIONAL_GRID[:2])
        monkeypatch.setattr(set_pieces, "STRENGTH", strength)
        today = frame["Date"].max() + pd.Timedelta(days=1)
        got = set_pieces.tune_international(frame, today=today, model_cls=shots.ShotModel)
        assert got["elo"]["chosen"] > 0 and got["params"]["elo_weight"] == got["elo"]["chosen"]
        assert got["elo"]["tried"][got["elo"]["chosen"]] < got["elo"]["tried"][0.0]
        # The before/after on the unseen year: team shots on target better with Elo
        for stat in ("sot_home", "sot_away"):
            assert got["holdout"][stat]["model"] < got["holdout_without_elo"][stat]["model"]

    def test_meaningless_strength_is_left_out(self, monkeypatch):
        frame, _ = synthetic()
        noise = random.Random(9)
        fake = {t: noise.uniform(-1.2, 1.2) for t in set(frame["HomeTeam"]) | set(frame["AwayTeam"])}
        monkeypatch.setattr(set_pieces, "INTERNATIONAL_GRID", set_pieces.INTERNATIONAL_GRID[:2])
        monkeypatch.setattr(set_pieces, "STRENGTH", lambda team, when=None: fake.get(team))
        got = set_pieces.tune_international(frame, today=frame["Date"].max() + pd.Timedelta(days=1),
                                            model_cls=shots.ShotModel)
        assert got["elo"]["chosen"] == 0.0 and "elo_weight" not in got["params"]

    def test_off_without_a_strength_source(self):
        frame, _ = synthetic(matches=700)
        got = set_pieces.tune_international(frame, today=frame["Date"].max() + pd.Timedelta(days=1),
                                            model_cls=shots.ShotModel)
        assert "elo" not in got
