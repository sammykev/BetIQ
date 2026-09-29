"""
Walk-forward check for player props (GitHub Actions, after props_collect.py):
every stored player-game priced from that player's earlier games only, then
compared with what he did.

Basketball, per stat (points, rebounds, assists, 3-pointers):
- dispersion: how widely real counts land around our expectation (the
  negative binomial's r, measured) — the server prices with it;
- calibration: of the lines we rated 60–70%, 70–80%, 80–90%, 90%+ (lines
  set around our expectation, as SportyBet sets its own, and its low "X+"
  lines), how many came in.
Football, anytime goalscorer: calibration, and a scale on our expected
goals that makes the chances match what came in (the server applies it).

Saved to Redis (CALIB_KEY); the server's pricing reads it.

    python props_backtest.py
"""

import json
from datetime import datetime, timezone
from math import exp, log
from typing import Dict, List, Tuple

import basketball_data as bd
import player_props as pp
import props_collect as pc
import props_pricing as pr

CALIB_KEY = "betiq:props:calib"
WINDOW = 60          # a player's latest games used to project the next (as the server does)


def bb_check(players: Dict[str, Dict], dispersion: Dict[str, float]) -> Tuple[Dict[str, float], Dict[str, List]]:
    """(measured dispersion per stat, calibration per stat) over one league."""
    pairs: Dict[str, List[Tuple[float, float]]] = {s: [] for s in pr.BB_STATS}
    rows: Dict[str, List[Tuple[float, bool]]] = {s: [] for s in pr.BB_STATS}
    for stat, (col, *_ ) in pr.BB_STATS.items():
        prior = pr.league_rates(players, col)
        for p in players.values():
            g = p["games"]
            for i in range(pp.MIN_GAMES, len(g)):
                past = g[max(0, i - WINDOW):i]
                proj = pp.project(pr._rows_to_games(past, col), "x", prior, dispersion.get(stat, pr.DEFAULT_DISPERSION[stat]))
                if proj is None or float(g[i][4]) <= 0:
                    continue
                actual = float(g[i][col])
                pairs[stat].append((proj.mean, actual))
                # SportyBet-like lines: the middle (x.5 near our mean) and low "X+" lines
                mid = int(proj.mean) + 0.5
                for line in {mid - 4, mid - 2, mid, mid + 2, max(0.5, mid - 6), 0.5, 1.5, 2.5}:
                    if line < 0:
                        continue
                    po = proj.p_over(line)
                    rows[stat] += [(po, actual > line), (1 - po, actual < line)]
    disp = {s: pp.fit_dispersion(v) for s, v in pairs.items() if len(v) >= 50}
    return disp, {s: pp.calibration(v) for s, v in rows.items()}


def fb_check(players: Dict[str, Dict]) -> Tuple[List[Dict], float, int]:
    """Anytime goalscorer: (calibration, the scale on expected goals that fits best, games)."""
    obs: List[Tuple[float, bool]] = []
    prior = pr.league_npxg90(players)
    for p in players.values():
        g = p["games"]
        for i in range(pr.MIN_APPS, len(g)):
            if float(g[i][4]) <= 0:
                continue
            got = pr.scorer_lambda({**p, "games": g[max(0, i - 30):i]}, None, None, prior=prior)
            if got:
                obs.append((got[0], float(g[i][5]) > 0))
    if not obs:
        return [], 1.0, 0

    def loss(k: float) -> float:
        return -sum(log(max(1e-9, (1 - exp(-k * lam)) if hit else exp(-k * lam))) for lam, hit in obs)
    scale = min((0.5 + 0.02 * i for i in range(51)), key=loss)       # 0.5 … 1.5
    return pp.calibration([(1 - exp(-scale * lam), hit) for lam, hit in obs]), round(scale, 2), len(obs)


def run(r) -> Dict:
    report: Dict = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "bb": {}, "fb": {}}
    for league in ("NBA", "WNBA", "Euroleague", "Eurocup"):
        raw = r.get(pc.PLAYERS_KEY.format(sport="bb", league=league))
        players = bd.decode(raw) if raw else {}
        if not players:
            continue
        # Two passes: measure the spread, then check the chances priced with it
        disp, _ = bb_check(players, {})
        disp2, cal = bb_check(players, disp)
        report["bb"][league] = {"players": len(players), "dispersion": {k: round(v, 2) for k, v in disp.items()},
                                "calibration": cal}
        print(f"{league}: {len(players)} players; dispersion {report['bb'][league]['dispersion']}")
        for s, rows in cal.items():
            print(f"  {s}: " + " · ".join(f"{x['bucket']} said {x['said']:.0%} came in {x['came_in']:.0%} ({x['n']})" for x in rows))
    for league in pc.UNDERSTAT.values():
        raw = r.get(pc.PLAYERS_KEY.format(sport="fb", league=league))
        players = bd.decode(raw) if raw else {}
        if not players:
            continue
        cal, scale, n = fb_check(players)
        report["fb"][league] = {"players": len(players), "games": n, "scale": scale, "calibration": cal}
        print(f"{league}: {n} player-games; scale x{scale}; " + " · ".join(
            f"{x['bucket']} said {x['said']:.0%} came in {x['came_in']:.0%} ({x['n']})" for x in cal))
    r.set(CALIB_KEY, json.dumps(report))
    return report


if __name__ == "__main__":
    import model_store
    client = model_store._client()
    if client is None:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    run(client)
