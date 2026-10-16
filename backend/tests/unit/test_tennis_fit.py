"""Tennis per tour: the tour groups, each tour's own Elo settings kept only
on a held-out win, the serve records' half-life, and the server reading them."""

import random
from datetime import date, timedelta

import numpy as np

import tennis_fit as tf
import tennis_model as tm


def test_tour_groups():
    assert tm.group_of("Wimbledon", "ATP") == "ATP"
    assert tm.group_of("WTA Wuhan", "WTA") == "WTA"
    assert tm.group_of("ATP Challenger Bengaluru", "ATP") == "Challenger"
    assert tm.group_of("ITF M15 Monastir", "ATP") == "ITF"
    assert tm.group_of("Anything", "WTA", "itf") == "ITF"
    assert tm.group_of("Anything", "ATP", "challenger") == "Challenger"
    assert set(tm.GROUPS) == {"ATP", "WTA", "Challenger", "ITF"}


def _pass(n, groups, rnd):
    g = np.array([groups[i % len(groups)] for i in range(n)], dtype=object)
    dates = np.array([(date(2024, 1, 1) + timedelta(days=i * 700 // n)).isoformat() for i in range(n)], dtype=object)
    d0 = np.array([rnd.gauss(60, 120) for _ in range(n)])
    return {"d0": d0, "d1": d0.copy(), "group": g, "date": dates}


def test_tour_settings_keep_only_a_held_out_win(monkeypatch):
    rnd = random.Random(3)
    base = _pass(6000, ["ATP", "ITF"], rnd)
    passes = {1.0: base}
    monkeypatch.setattr(tf, "SURFACE_WEIGHTS", (0.35,))
    monkeypatch.setattr(tf, "SHRINKS", (0.6, 1.0))
    monkeypatch.setattr(tf, "GROUP_MIN_HELD", 100)
    # Make ITF's leads noisier: the shrink-0.6 fits it better
    itf = base["group"] == "ITF"
    base["d0"][itf] *= 3
    base["d1"][itf] *= 3
    got = tf.tour_settings([], "2024-01-01", passes, (1.0, 0.35, 1.0))
    assert got["tours"]["ITF"]["best"]["shrink"] == 0.6
    assert got["tuned"] == {"ITF": {"k_mult": 1.0, "surface_weight": 0.35, "shrink": 0.6}}   # ATP: nothing to gain
    # Too few held-out matches: never tuned
    monkeypatch.setattr(tf, "GROUP_MIN_HELD", 10 ** 6)
    assert tf.tour_settings([], "2024-01-01", passes, (1.0, 0.35, 1.0))["tuned"] == {}


def _rows(n=1500, seed=2):
    rnd = random.Random(seed)
    names = [f"P{i}" for i in range(30)]
    skill = {p: rnd.uniform(0.55, 0.72) for p in names}
    rows = []
    for i in range(n):
        a, b = rnd.sample(names, 2)
        a_pts, b_pts = 80, 80
        a_won = sum(rnd.random() < skill[a] for _ in range(a_pts))
        b_won = sum(rnd.random() < skill[b] for _ in range(b_pts))
        w, l, sv = (a, b, [a_pts, a_won, b_pts, b_won]) if a_won - b_won >= 0 else (b, a, [b_pts, b_won, a_pts, a_won])
        rows.append({"date": (date(2023, 1, 1) + timedelta(days=i // 2)).isoformat(), "tour": "ATP",
                     "surface": "Hard", "w": w, "l": l, "sets": [[6, 4], [6, 4]], "best_of": 3, "sv": sv,
                     "tourney": "ATP Test"})
    return rows


def test_serve_check_scores_every_half_life():
    rows = _rows()
    got = tf.serve_check(rows, "2023-09-01", (180.0, 730.0))
    assert set(got["grid"]) == {"180", "365", "730"}
    assert got["held_points"] > 0
    assert got["use"] == (got["best"] if got["keep"] else tf.SERVE_HALF_LIFE)
    assert tf.serve_check([], "2023-09-01") == {"use": tf.SERVE_HALF_LIFE, "keep": False}


def test_fit_uses_a_tours_own_settings():
    f = tf.Fit(1.0, 0.35, 1.0, by_group={"ITF": {"k_mult": 1.6, "shrink": 0.8}}, serve_half_life=180.0)
    assert f.setting("ITF", "k_mult") == 1.6 and f.setting("ITF", "surface_weight") == 0.35
    assert f.setting("ATP", "shrink") == 1.0
    p = tm.Player("A", "ATP")
    p.sv, p.last = [100.0, 60.0], "2024-01-01"
    f._decay(p, "2024-06-29")                    # 180 days: half
    assert abs(p.sv[0] - 50.0) < 1e-6


def test_saved_model_carries_tours_and_the_server_reads_them(monkeypatch):
    rows = _rows(400)
    fit, rep = tf.run(rows, today=date(2023, 12, 1), grid=False)
    fit.by_group = {"ITF": {"k_mult": 1.25, "surface_weight": 0.5, "shrink": 0.8}}

    class R(dict):
        def set(self, k, v):
            self[k] = v
    r = R()
    tf.save(r, fit, rep, today=date(2023, 12, 1))
    m = tf.load_model(r[tf.MODEL_KEY])
    assert m["by_tour"] == {"ITF": {"k_mult": 1.25, "surface_weight": 0.5, "shrink": 0.8}}

    import racket_predictions as rp
    seen = {}

    def fake_predict(*a, **k):
        seen["shrink"], seen["sw"] = a[9], a[10]
        return None
    monkeypatch.setattr(tm, "predict", fake_predict)
    ev = {"homeTeamName": "P1", "awayTeamName": "P2", "_tournament": "ITF M25 Test", "estimateStartTime": 0}
    rp.distribution(ev, "tennis", m, [])
    assert seen == {"shrink": 0.8, "sw": 0.5}
    ev["_tournament"] = "Wimbledon"
    rp.distribution(ev, "tennis", m, [])
    assert seen == {"shrink": m["shrink"], "sw": m["surface_weight"]}
