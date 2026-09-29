"""
Fit the table tennis model (GitHub Actions, "Racket data" → tt_fit) on
SportyBet's own results (the server collects them: tennis_facts.py,
betiq:table_tennis:results) and check it walk-forward: every match is
predicted from the matches before it only, then added.

What it settles, by the check's log loss:
- the Elo step (K_MULTS: how fast a rating follows results — players here
  play several times a day, so faster than tennis may suit);
- the shrink: Elo's chances pulled toward even on the logit scale (they run
  a little overconfident — ratings are estimates);
- SWING, how much a game's rally chance strays from the match's (from the
  games' actual scores: the likelihood of every game's points).

Saved to Redis for the server: the players' ratings and the constants
(MODEL_KEY), and the check's report (REPORT_KEY).

    python table_tennis_fit.py            # fit, check, save
"""

import argparse
import json
from datetime import date, datetime, timezone
from functools import lru_cache
from math import log
from typing import Dict, Iterable, List, Optional, Tuple

import table_tennis_model as ttm

MODEL_KEY = "betiq:table_tennis:model"
REPORT_KEY = "betiq:table_tennis:check"
CHECK_SHARE = 0.5                 # the check covers the later half of the matches
K_MULTS = (0.25, 0.4, 0.6, 1.0, 1.5, 2.2)
SHRINKS = tuple(round(0.5 + 0.05 * i, 2) for i in range(15))   # 0.5 .. 1.2
SWINGS = (0.0, 0.02, 0.03, 0.045, 0.06, 0.08)
BUCKETS = ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0))
MIN_MATCHES = 10                  # rated after this many


def rows_from(results: Iterable[Dict]) -> List[Dict]:
    """SportyBet's results (tennis_facts.parse_result), oldest first, once each."""
    seen = set()
    out = []
    for m in results:
        if m.get("ret") or m.get("id") in seen:
            continue
        seen.add(m.get("id"))
        out.append(m)
    return sorted(out, key=lambda m: (m["ko"], m.get("id") or ""))


def best_of(m: Dict) -> int:
    return 2 * max(m["sets"]) - 1


@lru_cache(maxsize=20000)
def _dist(p: float, bo: int, swing: float) -> ttm.MatchDist:
    return ttm.match_dist(p, bo, swing)


@lru_cache(maxsize=50000)
def _rally(level: float, bo: int, swing: float) -> float:
    return round(ttm.solve_rally(level, bo, swing), 3)


def elo_pass(rows: List[Dict], k_mult: float, check_from: int) -> Tuple[Dict[str, ttm.Player], List[Tuple[Dict, float]]]:
    """Ratings through every match; the checked matches with the home player's chance before it."""
    players: Dict[str, ttm.Player] = {}
    checked = []
    for i, m in enumerate(rows):
        a = players.get(m["h"]) or players.setdefault(m["h"], ttm.Player(m["h"]))
        b = players.get(m["a"]) or players.setdefault(m["a"], ttm.Player(m["a"]))
        p = ttm.elo_p(a.elo, b.elo)
        if i >= check_from and a.n >= MIN_MATCHES and b.n >= MIN_MATCHES:
            checked.append((m, p))
        won = m["sets"][0] > m["sets"][1]
        ka, kb = k_mult * ttm.k_factor(a.n), k_mult * ttm.k_factor(b.n)
        a.elo += ka * ((1 if won else 0) - p)
        b.elo -= kb * ((1 if won else 0) - p)
        a.n += 1
        b.n += 1
        a.last = b.last = int(m["ko"])
    return players, checked


def log_loss(checked: List[Tuple[Dict, float]]) -> float:
    return -sum(log(max(1e-9, p if m["sets"][0] > m["sets"][1] else 1 - p)) for m, p in checked) / max(1, len(checked))


def games_loglik(checked: List[Tuple[Dict, float]], swing: float, limit: int = 20000) -> float:
    """Mean log chance of each game's actual score (and so its points)."""
    total, n = 0.0, 0
    for m, p in checked[-limit:]:
        bo = best_of(m)
        gd = ttm.game_scores(_rally(round(p, 3), bo, swing), swing)
        for g in m.get("games") or []:
            key = (g[0], g[1])
            if max(key) < 11:
                continue
            total += log(max(1e-9, gd.get(key, 0.0)))
            n += 1
    return total / max(1, n)


def calibration(rows: List[Tuple[float, bool]]) -> List[Dict]:
    out = []
    for lo, hi in BUCKETS:
        sel = [(p, w) for p, w in rows if lo <= p < hi]
        if sel:
            out.append({"bucket": f"{int(lo * 100)}-{int(hi * 100)}%", "n": len(sel),
                        "said": round(sum(p for p, _ in sel) / len(sel), 3),
                        "came_in": round(sum(1 for _, w in sel if w) / len(sel), 3)})
    return out


def _even(pairs: List[Tuple[float, bool]]) -> List[Tuple[float, bool]]:
    """Each line's likelier side (as a pick is made)."""
    return [(p, w) if p >= 0.5 else (1 - p, not w) for p, w in pairs]


def market_check(checked: List[Tuple[Dict, float]], swing: float) -> Dict[str, List[Tuple[float, bool]]]:
    """Lines like SportyBet's, around our expectation: total points, points
    handicap, games handicap, total games, a game's winner and its points."""
    lines: Dict[str, List[Tuple[float, bool]]] = {}

    def add(name: str, p: float, won: Optional[bool]) -> None:
        if won is not None:
            lines.setdefault(name, []).append((p, won))
    for m, p in checked:
        bo = best_of(m)
        md = _dist(_rally(round(p, 3), bo, swing), bo, swing)
        games = m.get("games") or []
        if len(games) != sum(m["sets"]):
            continue
        pts_h, pts_a = sum(g[0] for g in games), sum(g[1] for g in games)
        total, diff = pts_h + pts_a, pts_h - pts_a
        mean_total = sum(t * v for t, v in md.total_games.items())
        mid = round(mean_total) + 0.5
        for line in (mid - 4, mid, mid + 4):
            add("total_points", md.p_total_over(line), total > line)
        mean_diff = sum(d * v for d, v in md.games_diff.items())
        for line in (-round(mean_diff) - 3.5, -round(mean_diff) + 0.5, -round(mean_diff) + 3.5):
            add("points_handicap", md.p_handicap(line), diff + line > 0)
        for line in (-1.5, 1.5):
            add("games_handicap", md.p_set_handicap(line), m["sets"][0] - m["sets"][1] + line > 0)
        add("total_games", md.p_total_sets_over(bo // 2 + 1.5), sum(m["sets"]) > bo // 2 + 1.5)
        g1 = games[0]
        add("game_winner", md.p_first_set(), g1[0] > g1[1])
        add("game_points", md.p_first_set_total_over(18.5), g1[0] + g1[1] > 18.5)
        add("odd_even", md.p_odd_total(), total % 2 == 1)
    return lines


def calibration_maps(lines: Dict[str, List[Tuple[float, bool]]]) -> Tuple[Dict, Dict]:
    """Per market: the map pricing applies (fitted on all), and how a map
    fitted on half did on the other half."""
    import player_props as pp
    maps, held_out = {}, {}
    for m, rows in lines.items():
        maps[m] = pp.fit_calibration_map(rows)
        half = pp.fit_calibration_map(rows[::2])
        held_out[m] = calibration(_even([(pp.calibrate(p, half), w) for p, w in rows[1::2]]))
    return maps, held_out


def run(rows: List[Dict]) -> Tuple[Dict[str, ttm.Player], Dict]:
    check_from = int(len(rows) * (1 - CHECK_SHARE))
    tried = {}
    for km in K_MULTS:
        players, checked = elo_pass(rows, km, check_from)
        tried[km] = (log_loss(checked), players, checked)
        print(f"  Elo step x{km}: log loss {tried[km][0]:.4f} on {len(checked)} matches", flush=True)
    k_mult = min(tried, key=lambda k: tried[k][0])
    _, players, raw = tried[k_mult]
    shrink = min(SHRINKS, key=lambda s: log_loss([(m, ttm.shrink(p, s)) for m, p in raw]))
    checked = [(m, ttm.shrink(p, shrink)) for m, p in raw]
    ll = log_loss(checked)
    print(f"  shrink {shrink}: log loss {ll:.4f}", flush=True)
    swings = {}
    for s in SWINGS:
        swings[s] = games_loglik(checked, s)
        print(f"  swing {s}: game scores log chance {swings[s]:.4f}", flush=True)
    swing = max(swings, key=lambda s: swings[s])
    winner = _even([(p, m["sets"][0] > m["sets"][1]) for m, p in checked])
    lines = market_check(checked, swing)
    maps, held_out = calibration_maps(lines)
    by_league: Dict[str, List[bool]] = {}
    for m, p in checked:
        by_league.setdefault(m.get("t") or "", []).append((p >= 0.5) == (m["sets"][0] > m["sets"][1]))
    rep = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "matches": len(rows), "checked": len(checked),
           "k_mult": k_mult, "shrink": shrink, "swing": swing,
           "winner": {"accuracy": round(sum(1 for _, w in winner if w) / max(1, len(winner)), 4),
                      "log_loss": round(ll, 4), "calibration": calibration(winner)},
           "markets": {k: calibration(_even(v)) for k, v in lines.items()},
           "calibration_maps": maps, "calibrated_held_out": held_out,
           "by_league": {t: {"n": len(v), "accuracy": round(sum(v) / len(v), 4)}
                         for t, v in sorted(by_league.items(), key=lambda kv: -len(kv[1]))[:20]},
           "players": len(players)}
    return players, rep


def save(r, players: Dict[str, ttm.Player], rep: Dict, keep_since: int) -> int:
    import basketball_data as bd
    keep = {k: p.to_json() for k, p in players.items() if p.last >= keep_since}
    r.set(MODEL_KEY, bd.encode([{"players": keep, "swing": rep["swing"], "k_mult": rep["k_mult"], "shrink": rep["shrink"],
                                 "calibration": rep.get("calibration_maps") or {},
                                 "as_of": date.today().isoformat()}]))
    r.set(REPORT_KEY, json.dumps(rep))
    return len(keep)


def load_model(blob) -> Dict:
    """{players, swing, shrink, k_mult, as_of}"""
    import basketball_data as bd
    d = (bd.decode(blob) or [{}])[0]
    return {"players": {k: ttm.Player.from_json(v) for k, v in (d.get("players") or {}).items()},
            "swing": float(d.get("swing", ttm.SWING)), "shrink": float(d.get("shrink", 1.0)),
            "k_mult": float(d.get("k_mult", 1.0)), "as_of": d.get("as_of", ""), "calibration": d.get("calibration") or {}}


def print_report(rep: Dict) -> None:
    w = rep["winner"]
    print(f"Walk-forward: {rep['checked']} of {rep['matches']} matches checked; winner right {w['accuracy']:.1%}, "
          f"log loss {w['log_loss']}; Elo step x{rep['k_mult']}, shrink {rep['shrink']}, swing {rep['swing']}")
    for b in w["calibration"]:
        print(f"  winner {b['bucket']}: said {b['said']:.1%} came in {b['came_in']:.1%} ({b['n']})")
    for name, rows in rep["markets"].items():
        for b in rows:
            print(f"  {name} {b['bucket']}: said {b['said']:.1%} came in {b['came_in']:.1%} ({b['n']})")
    for name, rows in (rep.get("calibrated_held_out") or {}).items():
        for b in rows:
            print(f"  {name} calibrated (held out) {b['bucket']}: said {b['said']:.1%} came in {b['came_in']:.1%} ({b['n']})")
    for t, v in rep["by_league"].items():
        print(f"  {t}: {v['n']} matches, {v['accuracy']:.1%} right")


def main() -> None:
    import model_store
    import tennis_facts as tf
    import basketball_data as bd
    r = model_store._client()
    if r is None:
        raise SystemExit("No Redis")
    results: List[Dict] = []
    for blob in (r.hgetall(tf.SPORTS["table_tennis"]["key"]) or {}).values():
        results += bd.decode(blob)
    rows = rows_from(results)
    print(f"{len(rows)} table tennis matches stored", flush=True)
    if len(rows) < 500:
        raise SystemExit("Too few matches to fit yet")
    players, rep = run(rows)
    print_report(rep)
    kept = save(r, players, rep, keep_since=int(rows[-1]["ko"]) - 60 * 86400)
    print(f"Saved {kept} players")


if __name__ == "__main__":
    argparse.ArgumentParser().parse_args()
    main()
