"""The xG / starting-XI walk-forward check (check_team_news.py) on made-up seasons."""

import random
from datetime import date, timedelta

import check_team_news as ctn


def _players(seed=1, days=500):
    """Ten teams; team T0's striker 'star' makes most of its xG and misses some matches."""
    rnd = random.Random(seed)
    players = {}
    teams = [f"T{i}" for i in range(10)]
    for d in range(days):
        if d % 3:
            continue
        day = (date(2024, 8, 1) + timedelta(days=d)).isoformat()
        h, a = rnd.sample(teams, 2)
        for team, opp, home in ((h, a, True), (a, h, False)):
            star_plays = team != "T0" or rnd.random() < 0.7
            for i in range(11):
                name = f"{team}-p{i}" if not (team == "T0" and i == 0) else "star"
                if name == "star" and not star_plays:
                    continue
                xg = (0.9 if name == "star" else 0.1) * rnd.uniform(0.5, 1.5)
                goals = 1 if rnd.random() < xg * 0.8 else 0
                players.setdefault(name, {"name": name, "team": team, "games": []})["games"].append(
                    [day, team, opp, home, 90, goals, 2, round(xg, 3), 0, True, 0])
    return players


def test_matches_rebuilt_from_player_rows():
    ms = ctn.matches_from_players(_players(days=30), "L")
    assert ms and all(m["h"] and m["a"] for m in ms)
    assert ms == sorted(ms, key=lambda m: m["date"])
    assert all(len(m["h"]) >= 10 for m in ms)


def test_walk_scores_every_match_after_warmup():
    ms = ctn.matches_from_players(_players(), "L")
    rows = ctn.walk(ms, 0.08, 0.5, 1.0, 0.5)
    assert rows and all(r[0] >= ctn.WARMUP for r in rows)
    assert all(0 < r[1] < 5 and 0 < r[2] < 5 for r in rows)


def test_missing_star_lowers_attack_present():
    R = ctn.Ratings(0.1)
    ms = ctn.matches_from_players(_players(days=200), "L")
    for m in ms:
        R.update(m)
    full = [(f"T0-p{i}", 90, True, 0.1, 0) for i in range(1, 11)] + [("star", 90, True, 0.9, 0)]
    without = full[:-1]
    assert R.present("T0", without)[0] < R.present("T0", full)[0]
