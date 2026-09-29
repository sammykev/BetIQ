"""Props walk-forward check on simulated players: dispersion measured near
the truth, chances that come in as often as they say, a scorer scale near 1."""

import random
from math import exp

import props_backtest as pb


def simulated_bb(seed=2, players=60, games=40, r_true=8.0):
    rng = random.Random(seed)
    out = {}
    for k in range(players):
        rates = {"pts": rng.uniform(0.25, 0.75), "reb": rng.uniform(0.08, 0.3), "ast": rng.uniform(0.04, 0.25),
                 "tpm": rng.uniform(0.01, 0.1)}
        mins0 = rng.uniform(15, 34)
        rows = []
        for i in range(games):
            mins = max(3.0, rng.gauss(mins0, 3))
            counts = []
            for s in ("pts", "reb", "ast", "tpm"):
                lam = rng.gammavariate(r_true, rates[s] * mins / r_true)
                counts.append(float(sum(1 for _ in range(150) if rng.random() < lam / 150)))
            rows.append([f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "T", "O", True, round(mins, 1), *counts, True])
        out[f"p{k}"] = {"name": f"P{k}", "team": "T", "games": rows}
    return out


def test_basketball_check():
    players = simulated_bb()
    disp, _ = pb.bb_check(players, {})
    assert 4 < disp["pts"] < 20
    _, cal = pb.bb_check(players, disp)
    for s in ("pts", "reb"):
        for row in cal[s]:
            if row["n"] > 300 and row["bucket"] in ("60-70%", "70-80%", "80-90%"):
                assert abs(row["came_in"] - row["said"]) < 0.06, (s, row)


def test_scorer_check():
    rng = random.Random(5)
    players = {}
    for k in range(80):
        per90 = rng.uniform(0.05, 0.6)
        rows = []
        for i in range(30):
            xg = per90 * rng.uniform(0.6, 1.4)
            goal = 1.0 if rng.random() < 1 - exp(-per90) else 0.0
            rows.append([f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "T", "O", True, 90.0, goal, 2.0, xg, 0.0, True, 0])
        players[f"p{k}"] = {"name": f"P{k}", "team": "T", "games": rows}
    cal, scale, n = pb.fb_check(players)
    assert n > 1500 and 0.8 <= scale <= 1.2
    for row in cal:
        if row["n"] > 200:
            assert abs(row["came_in"] - row["said"]) < 0.07, row
