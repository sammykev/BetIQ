"""Each side's rating, as the site shows it (components/Ratings.tsx), carried on the predictions."""

import basketball_model as bm
import racket_matchday as rmd
import table_tennis_model as ttm
import tennis_model as tm


def test_tennis_detail_has_each_players_elo_and_surface_elo():
    a = tm.Player("Sinner, Jannik", "ATP", elo=2200.0, n=400)
    a.surf["Clay"], a.surf_n["Clay"] = 2100.0, 120
    b = tm.Player("Fils, Arthur", "ATP", elo=1900.0, n=150)
    players = {f"ATP|{tm.name_key(p.name)}": p for p in (a, b)}
    md = tm.predict(players, dict(tm.DEFAULT_SPW), "Sinner, Jannik", "Fils, Arthur", "Clay", 3, None, 0.5, "ATP")
    r = md.detail["ratings"]
    assert r["home"] == {"elo": 2200, "surface": "Clay", "surface_elo": 2100, "matches": 400, "surface_matches": 120}
    assert r["away"]["elo"] == 1900
    md2 = tm.predict(players, dict(tm.DEFAULT_SPW), "Sinner, Jannik", "Nobody", "Hard", 3, 0.6, 0.5, "ATP")
    assert md2.detail["ratings"]["away"] is None


def test_table_tennis_detail_has_each_players_elo():
    players = {"A": ttm.Player("A", 1650.0, 300), "B": ttm.Player("B", 1480.0, 90)}
    md = ttm.predict(players, "A", "B", None, 0.5)
    assert md.detail["ratings"] == {"home": {"elo": 1650, "matches": 300}, "away": {"elo": 1480, "matches": 90}}


def test_basketball_detail_and_the_match_day_keep_the_ratings():
    lg = bm.League("L", 80.0, 3.0, {"A": 2.5, "B": -1.0}, {"A": 1.5, "B": -0.5}, {"A": 30, "B": 30},
                   dict(bm.DEFAULT_SIGMA))
    m = bm.expect(lg, "A", "B")
    assert m.detail["ratings"]["home"] == {"attack": 2.5, "defence": 1.5, "net": 4.0, "games": 30}
    snap = rmd.snapshot({"p_home": 0.6, "model_detail": {"ratings": {"home": {"elo": 1600}, "away": None}}})
    assert snap["ratings"]["home"]["elo"] == 1600
