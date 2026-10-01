"""Basketball Elo with margin of victory (basketball_elo.py) and its walk-forward check."""

import random
from datetime import date, timedelta

import basketball_elo as be
import basketball_model as bm
import elo_check_basketball as ec


def g(day, home, away, hs, as_):
    return bm.Game(day, home, away, hs, as_)


def test_winner_gains_what_loser_loses_and_big_wins_count_more():
    small = be.rate([g("2026-01-01", "A", "B", 80, 79)])
    big = be.rate([g("2026-01-01", "A", "B", 100, 70)])
    assert small["A"] > be.START > small["B"] and abs(small["A"] - be.START) == abs(small["B"] - be.START)
    assert big["A"] - be.START > small["A"] - be.START


def test_an_away_win_moves_more_than_a_home_win():
    home_win = be.rate([g("2026-01-01", "A", "B", 80, 75)])
    away_win = be.rate([g("2026-01-01", "A", "B", 75, 80)])
    assert be.START - away_win["A"] > home_win["A"] - be.START


def test_ratings_are_pulled_back_over_an_off_season():
    r = be.rate([g("2026-01-01", "A", "B", 110, 60)])
    gained = r["A"] - be.START
    r2 = be.rate([g("2026-01-01", "A", "B", 110, 60), g("2026-06-01", "A", "C", 80, 80)])
    assert abs((r2["A"] - be.START) - be.CARRY * gained) < 1e-9     # a tie moves nothing; only the pull


def test_the_check_scores_the_model_elo_and_blends():
    random.seed(3)
    teams = [f"T{i}" for i in range(10)]
    s = {t: random.gauss(0, 6) for t in teams}
    games = []
    for k in range(260):
        day = (date(2025, 10, 1) + timedelta(days=k)).isoformat()
        random.shuffle(teams)
        for i in range(0, 10, 2):
            m = s[teams[i]] - s[teams[i + 1]] + 3 + random.gauss(0, 10)
            hs, as_ = int(80 + m / 2), int(80 - m / 2)
            games.append(g(day, teams[i], teams[i + 1], hs + (hs == as_), as_))
    got = ec.league_check("Test", games)
    assert got["tested"] > 500 and set(got["scores"]) == {"w0", "w0.25", "w0.5", "w0.75", "w1"}
    assert all(0.5 < v["accuracy"] < 1 and v["log_loss"] < 0.7 for v in got["scores"].values())
