"""
Corners / bookings totals (set_pieces.py) and the goal markets derived from
the score grid (predictor.goal_markets).
"""

import math

import pandas as pd
import pytest

import set_pieces
from predictor import goal_markets
from set_pieces import SetPieceModel, p_over


def season(n_rounds=12, start="2023-08-05"):
    """A four-team league where Corner FC wins many corners and Card FC gets many bookings."""
    teams = ["Corner FC", "Card FC", "Plain FC", "Other FC"]
    rows, day = [], pd.Timestamp(start)
    stats = {"Corner FC": (9, 2), "Card FC": (4, 4), "Plain FC": (4, 1), "Other FC": (5, 1)}  # (corners, yellows)
    for r in range(n_rounds):
        for i, h in enumerate(teams):
            for a in teams[i + 1:]:
                home, away = (h, a) if r % 2 == 0 else (a, h)
                rows.append({"Date": day, "HomeTeam": home, "AwayTeam": away, "league": "PL",
                             "HC": stats[home][0], "AC": stats[away][0],
                             "HY": stats[home][1], "AY": stats[away][1], "HR": 0, "AR": 0})
                day += pd.Timedelta(days=3)
    return pd.DataFrame(rows)


class TestDistribution:
    def test_probabilities_are_valid_and_fall_with_the_line(self):
        for r in (None, 15.0, 70.0):
            ps = [p_over(line, 9.8, r) for line in (7.5, 8.5, 9.5, 10.5, 11.5)]
            assert all(0 < p < 1 for p in ps) and ps == sorted(ps, reverse=True)

    def test_negative_binomial_is_wider_than_poisson(self):
        assert p_over(7.5, 4.5, 5.0) > p_over(7.5, 4.5, None)
        assert p_over(0.5, 4.5, 5.0) < p_over(0.5, 4.5, None)

    def test_poisson_matches_the_closed_form(self):
        assert p_over(0.5, 2.0, None) == pytest.approx(1 - math.exp(-2.0))


class TestModel:
    def test_team_tendencies_show_in_the_totals(self):
        model = SetPieceModel.fit(season())
        corner = model.markets("Corner FC", "Plain FC", "PL")
        plain = model.markets("Other FC", "Plain FC", "PL")
        assert corner["corners"]["mean"] > plain["corners"]["mean"]
        assert model.markets("Card FC", "Plain FC", "PL")["bookings"]["mean"] > plain["bookings"]["mean"]
        assert set(corner["corners"]["over"]) == {"7.5", "8.5", "9.5", "10.5", "11.5"}
        assert set(corner["bookings"]["over"]) == {"2.5", "3.5", "4.5", "5.5", "6.5"}

    def test_reds_count_double_as_bookings(self):
        assert set_pieces._counts({"HC": 5, "AC": 4, "HY": 2, "AY": 1, "HR": 1, "AR": 0})["bookings"] == (4, 1)

    def test_unknown_or_new_teams_get_nothing(self):
        model = SetPieceModel.fit(season())
        assert model.markets("Nobody FC", "Plain FC", "PL") is None
        assert SetPieceModel.fit(season(n_rounds=1)).markets("Corner FC", "Plain FC", "PL") is None

    def test_needs_the_stat_columns(self):
        assert SetPieceModel.fit(season().drop(columns=["HC"])) is None
        assert SetPieceModel.fit(pd.DataFrame()) is None

    def test_replay_only_uses_earlier_matches(self):
        data = season()
        _, rows = SetPieceModel.replay(data, test_from=pd.Timestamp("2023-10-01"))
        assert rows and all(d >= pd.Timestamp("2023-10-01") for d, *_ in rows)


class TestGoalMarkets:
    def test_consistent_with_the_classifier(self):
        g = goal_markets(1.6, 1.1, 0.55, 0.48, 0.27)
        assert g["p_over35"] < 0.55
        assert 0.3 < g["p_btts"] < 0.75
        assert g["p_dnb_home"] == pytest.approx(0.48 / 0.75, abs=1e-3)

    def test_more_goals_expected_means_more_over_35(self):
        assert goal_markets(1.0, 0.9, 0.35, 0.4, 0.3)["p_over35"] < goal_markets(2.0, 1.4, 0.7, 0.5, 0.25)["p_over35"]


class TestTeamCorners:
    def test_team_lines_and_most_corners(self):
        model = SetPieceModel.fit(season())
        m = model.markets("Corner FC", "Plain FC", "PL")
        assert m["corners_home"]["mean"] > m["corners_away"]["mean"]
        race = m["corners_1x2"]
        assert race["home"] > race["away"] and sum(race.values()) == pytest.approx(1, abs=0.002)

    def test_most_corners_is_symmetric(self):
        r = set_pieces.most_corners(5.0, 5.0, 10.0, 10.0)
        assert r["home"] == pytest.approx(r["away"], abs=1e-9) and 0.05 < r["draw"] < 0.2


class TestScoreGrid:
    def test_grid_matches_the_classifier(self):
        from predictor import score_grid, _DIFF, _TOTALS
        g = score_grid(1.5, 1.2, 0.56, 0.5, 0.25, -0.1)
        h, a = g[_DIFF > 0].sum(), g[_DIFF < 0].sum()
        assert h / (h + a) == pytest.approx(0.5 / 0.75, abs=0.01)
        assert g[_TOTALS > 2.5].sum() == pytest.approx(0.56, abs=0.01)

    def test_markets_are_consistent(self):
        gm = goal_markets(1.6, 1.1, 0.55, 0.48, 0.27)["goal_markets"]
        assert gm["win_to_nil"]["home"] < gm["clean_sheet"]["home"]
        assert gm["team_totals"]["home"]["0.5"] == pytest.approx(1 - gm["clean_sheet"]["away"], abs=0.002)
        h = gm["handicap"]
        assert h["home_-1.5"] + h["away_+1.5"] == pytest.approx(1, abs=0.002)
        assert h["home_-2.5"] <= h["home_-1.5"]
        dc = gm["dc_total"]["1X"]
        assert dc["o2.5"] + dc["u2.5"] == pytest.approx(0.48 + (1 - 0.48 - 0.27), abs=0.05)

    def test_handicap_caps(self):
        from predictor import HANDICAP_CAPS
        gm = goal_markets(3.5, 0.3, 0.8, 0.9, 0.03)["goal_markets"]
        assert gm["handicap"]["home_-1.5"] <= HANDICAP_CAPS[1.5]
        assert gm["handicap"]["home_-2.5"] <= HANDICAP_CAPS[2.5]
