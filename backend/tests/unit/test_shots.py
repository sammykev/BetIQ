"""
Shots totals (shots.py): teams known mostly from cup ties or a few league
matches stay near the league's own average.
"""

import pandas as pd

import shots


def league(seasons=2, start="2023-08-05"):
    """A ten-team league averaging 14 home and 12 away shots."""
    teams = [f"Club {i}" for i in range(10)]
    rows, day = [], pd.Timestamp(start)
    for s in range(seasons):
        for i, h in enumerate(teams):
            for j, a in enumerate(teams):
                if h != a:
                    hs, as_ = 14 + (i - j) % 3 - 1, 12 + (j - i) % 3 - 1
                    rows.append({"Date": day, "HomeTeam": h, "AwayTeam": a, "league": "DED",
                                 "HS": hs, "AS": as_, "HST": hs // 3, "AST": as_ // 3})
                    day += pd.Timedelta(days=1)
    return pd.DataFrame(rows), day


def promoted_pair():
    """Two promoted clubs whose shots history is a cup (its first match an
    amateur side with 3 home shots) and three average league matches each."""
    data, day = league()
    rows = [{"Date": day, "HomeTeam": "Amateurs", "AwayTeam": "Club 0", "league": "KNVB",
             "HS": 3, "AS": 28, "HST": 1, "AST": 10}]
    for i in range(4):
        for team in ("Telstar", "ADO Den Haag"):
            day += pd.Timedelta(days=5)
            rows.append({"Date": day, "HomeTeam": f"Amateurs {i}", "AwayTeam": team, "league": "KNVB",
                         "HS": 6, "AS": 24, "HST": 2, "AST": 9})
            rows.append({"Date": day + pd.Timedelta(days=1), "HomeTeam": team, "AwayTeam": f"Village {i}",
                         "league": "KNVB", "HS": 25, "AS": 5, "HST": 9, "AST": 2})
    for team in ("Telstar", "ADO Den Haag"):
        for opp in ("Club 1", "Club 2", "Club 3"):
            day += pd.Timedelta(days=3)
            rows.append({"Date": day, "HomeTeam": team, "AwayTeam": opp, "league": "DED",
                         "HS": 13, "AS": 12, "HST": 4, "AST": 4})
    return pd.concat([data, pd.DataFrame(rows)], ignore_index=True)


class TestThinHistory:
    def test_promoted_pair_stays_near_the_league_average(self):
        model = shots.ShotModel.fit(promoted_pair())
        got = model.markets("Telstar", "ADO Den Haag", "DED")
        average = sum(model.league["DED"]["shots"])
        assert 24 < average < 28
        assert abs(got["shots"]["mean"] - average) < 3
        assert got["shots"]["over"]["28.5"] < 0.5

    def test_without_the_guards_the_cup_inflates_it(self):
        # The bug this guards against: the cup's average seeded from its first match
        off = {"seed_leagues": False, "ratio_cap": None, "league_prior": 0.0}
        model = shots.ShotModel.fit(promoted_pair(), off)
        assert model.markets("Telstar", "ADO Den Haag", "DED")["shots"]["mean"] > 35

    def test_established_teams_barely_move(self):
        on = shots.ShotModel.fit(promoted_pair()).markets("Club 1", "Club 2", "DED")
        off = shots.ShotModel.fit(promoted_pair(), {"seed_leagues": False, "ratio_cap": None, "league_prior": 0.0})
        assert abs(on["shots"]["mean"] - off.markets("Club 1", "Club 2", "DED")["shots"]["mean"]) < 0.5
