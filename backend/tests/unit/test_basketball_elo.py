"""Basketball Elo with margin of victory (basketball_elo.py) and its walk-forward check."""

import random
from datetime import date, datetime, timedelta, timezone

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


def test_league_weights_follow_the_check_shrunk_by_its_size():
    report = {"leagues": {
        "Big": {"tested": 20000, "scale": 0.048, "scores": {"w0": {"log_loss": 0.60}, "w1": {"log_loss": 0.58}}},
        "Small": {"tested": 80, "scale": 0.04, "scores": {"w0": {"log_loss": 0.60}, "w1": {"log_loss": 0.59}}},
        "Level": {"tested": 8000, "scale": 0.035, "scores": {"w0.25": {"log_loss": 0.63}, "w1": {"log_loss": 0.64}}}}}
    w = be.league_weights(report)
    assert w["Big"][0] > 0.95 and w["Big"][1] == 0.048
    assert be.DEFAULT_WEIGHT < w["Small"][0] < 0.35          # pulled most of the way back
    assert w["Level"][0] == be.DEFAULT_WEIGHT
    assert be.league_weights(None) == {}


def test_expect_mixes_elos_margin_and_keeps_the_total():
    lg = bm.League("L", 80.0, 3.0, {"A": 2.0, "B": -2.0}, {"A": 0.0, "B": 0.0}, {"A": 20, "B": 20},
                   dict(bm.DEFAULT_SIGMA))
    h0, a0 = lg.expect("A", "B")
    lg.elo, lg.elo_scale, lg.elo_weight = {"A": 1700.0, "B": 1400.0}, 0.04, 0.5
    h, a = lg.expect("A", "B")
    elo_margin = 0.04 * (1700 + be.HOME - 1400)
    assert abs((h - a) - (0.5 * (h0 - a0) + 0.5 * elo_margin)) < 1e-9
    assert abs((h + a) - (h0 + a0)) < 1e-9
    again = bm.League.from_json(lg.to_json())
    assert again.elo == lg.elo and again.elo_weight == 0.5 and again.expect("A", "B") == (h, a)
    assert bm.team_rating(lg, "A")["elo"] == 1700


def test_fit_all_rates_elo_over_the_long_history():
    import basketball_data as bd
    random.seed(5)
    teams = [f"T{i}" for i in range(8)]
    res = []
    for k in range(120):
        day = date(2026, 1, 1) + timedelta(days=k)
        random.shuffle(teams)
        for i in range(0, 8, 2):
            hs, as_ = random.randint(70, 95), random.randint(70, 95)
            res.append({"id": f"{k}-{i}", "t": "Test League", "h": teams[i], "a": teams[i + 1], "hs": hs,
                        "as": as_ + (hs == as_), "ko": int(datetime(day.year, day.month, day.day, 18, tzinfo=timezone.utc).timestamp())})
    report = {"leagues": {"Test League": {"tested": 5000, "scale": 0.04, "scores": {"w1": {"log_loss": 0.5}, "w0": {"log_loss": 0.6}}}}}
    leagues = bd.fit_all(res, elo_report=report, history=res)
    lg = leagues["Test League"]
    assert lg.elo_weight > 0.85 and len(lg.elo) == 8
    assert bd.fit_all(res)["Test League"].elo_weight == 0.0          # no report: as before
