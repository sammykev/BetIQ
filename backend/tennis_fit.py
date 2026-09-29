"""
Fit the tennis model (GitHub Actions, "Racket data" → tennis_fit) and check
it walk-forward: every match is predicted from the matches before it only,
then added.

Sources, merged in date order (a match in two sources counted once):
- web history (tennis_data.py): years of tour, Challenger and ITF matches;
- SportyBet's own results the server collects (tennis_facts.py, ~300 days,
  every level it prices), which carry the names the server matches on.

Saved to Redis for the server: the players' ratings and serve/return
records (MODEL_KEY) and the check's report (REPORT_KEY).

    python tennis_fit.py            # fit, check, save
    python tennis_fit.py --sample   # a quick run on part of the data, saves nothing
"""

import argparse
import asyncio
import json
import zlib
from datetime import date, datetime, timedelta, timezone
from math import log
from typing import Dict, Iterable, List, Optional, Tuple

import tennis_model as tm

MODEL_KEY = "betiq:tennis:model"
REPORT_KEY = "betiq:tennis:check"
SERVE_HALF_LIFE = 365.0          # days: a serve point a year old counts half
CHECK_FROM_DAYS = 365 * 3        # the walk-forward check covers the last three years
BUCKETS = ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0))
KEEP_DAYS = 3 * 365              # players who played in this window are saved
K_MULTS = (0.8, 1.0, 1.25, 1.6)  # the Elo step, tried (FiveThirtyEight's ×1)
SURFACE_WEIGHTS = (0.2, 0.35, 0.5, 0.65, 0.8)
SHRINKS = tuple(round(0.6 + 0.04 * i, 2) for i in range(11))     # 0.6 .. 1.0
DETAIL_CHECKS = 25000            # matches whose every line is checked (a sample; the winner is checked on all)


def sportybet_rows(results: Iterable[Dict]) -> List[Dict]:
    """SportyBet's tennis results (tennis_facts.parse_result) as fit rows."""
    out = []
    for m in results:
        if m.get("ret"):
            continue
        t = m.get("t") or ""
        winner_home = m["sets"][0] > m["sets"][1]
        d = datetime.fromtimestamp(m["ko"], timezone.utc).date().isoformat()
        sets = [g if winner_home else [g[1], g[0]] for g in m.get("games") or []]
        out.append({"date": d, "tour": tm.tour_of(t), "surface": tm_surface(t), "w": m["h"] if winner_home else m["a"],
                    "l": m["a"] if winner_home else m["h"], "sets": sets, "ret": False,
                    "best_of": 5 if max(m["sets"]) == 3 else 3, "src": "sportybet", "tourney": t})
    return out


def tm_surface(tournament: str) -> str:
    from sports_fetcher import _surface
    return _surface(tournament or "")


def _key(tour: str, name: str) -> str:
    return f"{tour}|{tm.name_key(name)}"


def dedupe(rows: List[Dict]) -> List[Dict]:
    """One row per match: same day (±1), same two players."""
    seen = set()
    out = []
    for r in sorted(rows, key=lambda r: (r["date"], r.get("src") != "web")):
        pair = tuple(sorted((tm.name_key(r["w"]), tm.name_key(r["l"]))))
        d = date.fromisoformat(r["date"])
        keys = {(pair, (d + timedelta(days=o)).isoformat()) for o in (-1, 0, 1)}
        if keys & seen:
            continue
        seen.add((pair, r["date"]))
        out.append(r)
    return out


class Fit:
    """The ratings and serve/return records, match by match."""

    def __init__(self, k_mult: float = 1.0, surface_weight: float = tm.SURFACE_WEIGHT, shrink: float = 1.0,
                 detail_every: int = 1):
        self.k_mult, self.sw, self.shrink, self.detail_every = k_mult, surface_weight, shrink, detail_every
        self.players: Dict[str, tm.Player] = {}
        self.avg = {"ATP": [0.0, 0.0], "WTA": [0.0, 0.0]}      # tour serve points [played, won]
        self.checked: List[Tuple[str, str, float, bool, Dict]] = []

    def player(self, tour: str, name: str) -> tm.Player:
        k = _key(tour, name)
        p = self.players.get(k)
        if p is None:
            p = self.players[k] = tm.Player(name, tour)
        return p

    def tour_avg(self) -> Dict[str, float]:
        return {t: (v[1] / v[0] if v[0] > 1000 else tm.DEFAULT_SPW[t]) for t, v in self.avg.items()}

    def _decay(self, p: tm.Player, day: str) -> None:
        if not p.last:
            return
        gap = (date.fromisoformat(day) - date.fromisoformat(p.last)).days
        if gap > 0:
            f = 0.5 ** (gap / SERVE_HALF_LIFE)
            p.sv = [p.sv[0] * f, p.sv[1] * f]
            p.rt = [p.rt[0] * f, p.rt[1] * f]

    def add(self, r: Dict, check: bool) -> None:
        tour, surf = r["tour"], r["surface"] if r["surface"] in tm.SURFACES else "Hard"
        w, l = self.player(tour, r["w"]), self.player(tour, r["l"])
        if check and w.n >= 5 and l.n >= 5:
            self._check(r, w, l, surf)
        # Elo: overall and surface
        pw = tm.elo_p(w.elo, l.elo)
        kw, kl = tm.k_factor(w.n, self.k_mult), tm.k_factor(l.n, self.k_mult)
        w.elo += kw * (1 - pw)
        l.elo -= kl * (1 - pw)
        ps = tm.elo_p(w.surf[surf], l.surf[surf])
        ksw, ksl = tm.k_factor(w.surf_n[surf], self.k_mult), tm.k_factor(l.surf_n[surf], self.k_mult)
        w.surf[surf] += ksw * (1 - ps)
        l.surf[surf] -= ksl * (1 - ps)
        w.n += 1
        l.n += 1
        w.surf_n[surf] += 1
        l.surf_n[surf] += 1
        # Serve and return points (web rows carry them)
        for p in (w, l):
            self._decay(p, r["date"])
            p.last = r["date"]
        sv = r.get("sv")
        if sv:
            w_pts, w_won, l_pts, l_won = sv
            w.sv = [w.sv[0] + w_pts, w.sv[1] + w_won]
            l.sv = [l.sv[0] + l_pts, l.sv[1] + l_won]
            w.rt = [w.rt[0] + l_pts, w.rt[1] + (l_pts - l_won)]
            l.rt = [l.rt[0] + w_pts, l.rt[1] + (w_pts - w_won)]
            self.avg[tour][0] += w_pts + l_pts
            self.avg[tour][1] += w_won + l_won

    def _check(self, r: Dict, w: tm.Player, l: tm.Player, surf: str) -> None:
        """The winner's chance before the match, and its scores' chances."""
        # Randomise the side so "player 1" isn't always the winner
        h = zlib.crc32(f"{r['date']}|{r['w']}|{r['l']}".encode())
        first_is_w = h % 2 == 0
        a, b = (w, l) if first_is_w else (l, w)
        p_a = tm.shrink(tm.elo_p(a.rating(surf, self.sw), b.rating(surf, self.sw)), self.shrink)
        group = r["tour"] + ":" + r.get("level", r.get("src", ""))
        if (h >> 1) % self.detail_every:
            self.checked.append((r["date"], group, p_a, first_is_w, None))
            return
        pa0, pb0 = tm.serve_chances(a, b, self.tour_avg())
        pa, pb = tm.solve_serve(p_a, pa0, pb0)
        md = tm.match_dist(pa, pb, r.get("best_of", 3))
        a_won = first_is_w
        games_a = sum(g[0] for g in r["sets"]) if first_is_w else sum(g[1] for g in r["sets"])
        games_b = sum(g[1] for g in r["sets"]) if first_is_w else sum(g[0] for g in r["sets"])
        total = games_a + games_b
        mean_total = sum(g * p for g, p in md.total_games.items())
        info = {"total": total, "diff": games_a - games_b, "first_set": None, "mean_total": mean_total}
        if r["sets"]:
            fs = r["sets"][0] if first_is_w else [r["sets"][0][1], r["sets"][0][0]]
            info["first_set"] = fs[0] > fs[1]
            info["p_first_set"] = md.p_first_set()
        # Market-style lines around our expectation
        lines = []
        mid = round(mean_total) + 0.5
        for line in (mid - 3, mid, mid + 3):
            po = md.p_total_over(line)
            lines += [("total_games", po, total > line), ("total_games", 1 - po, total < line)]
        for line in (-4.5, -2.5, 2.5, 4.5):
            ph = md.p_handicap(line)
            lines += [("games_handicap", ph, games_a - games_b + line > 0), ("games_handicap", 1 - ph, games_a - games_b + line < 0)]
        info["lines"] = lines
        self.checked.append((r["date"], group, p_a, a_won, info))


def calibration(rows: List[Tuple[float, bool]]) -> List[Dict]:
    out = []
    for lo, hi in BUCKETS:
        sel = [(p, w) for p, w in rows if lo <= p < hi]
        if sel:
            out.append({"bucket": f"{int(lo * 100)}-{int(hi * 100)}%", "n": len(sel),
                        "said": round(sum(p for p, _ in sel) / len(sel), 3),
                        "came_in": round(sum(1 for _, w in sel if w) / len(sel), 3)})
    return out


def report(fit: Fit) -> Dict:
    winner = [(max(p, 1 - p), won if p >= 0.5 else not won) for _, _, p, won, _ in fit.checked]
    ll = -sum(log(max(1e-9, p if won else 1 - p)) for _, _, p, won, _ in fit.checked) / max(1, len(fit.checked))
    acc = sum(1 for p, w in winner if w) / max(1, len(winner))
    detailed = [i for *_, i in fit.checked if i]
    first = [(max(i["p_first_set"], 1 - i["p_first_set"]), i["first_set"] if i["p_first_set"] >= 0.5 else not i["first_set"])
             for i in detailed if i.get("first_set") is not None]
    lines: Dict[str, List[Tuple[float, bool]]] = {}
    for i in detailed:
        for m, p, w in i["lines"]:
            lines.setdefault(m, []).append((p, w))
    by_group: Dict[str, List[Tuple[float, bool]]] = {}
    for _, g, p, won, _ in fit.checked:
        by_group.setdefault(g, []).append((max(p, 1 - p), won if p >= 0.5 else not won))
    return {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "matches": len(fit.checked),
            "detailed": len(detailed), "k_mult": fit.k_mult, "surface_weight": fit.sw, "shrink": fit.shrink,
            "winner": {"accuracy": round(acc, 4), "log_loss": round(ll, 4), "calibration": calibration(winner)},
            "first_set": calibration(first),
            "lines": {m: calibration([x for x in v if x[0] >= 0.5]) for m, v in lines.items()},
            "by_group": {g: {"n": len(v), "accuracy": round(sum(1 for _, w in v if w) / len(v), 4)}
                         for g, v in sorted(by_group.items(), key=lambda kv: -len(kv[1]))},
            "players": len(fit.players), "tour_avg": fit.tour_avg()}


def elo_grid(rows: List[Dict], check_from: str) -> Tuple[float, float, float, Dict]:
    """The Elo step, surface weight and shrink with the lowest walk-forward
    log loss on the winner (Elo only: one pass per step, every weight read
    from the same pass)."""
    tried: Dict[Tuple[float, float], float] = {}
    best_pairs: List[Tuple[float, float, bool]] = []
    best = (1.0, tm.SURFACE_WEIGHT, 9.0)
    for km in K_MULTS:
        elo: Dict[str, List[float]] = {}           # key -> [overall, n, hard, clay, grass, n_h, n_c, n_g]
        diffs: List[Tuple[float, float]] = []      # (overall diff, surface diff), winner's side
        for r in rows:
            surf = r["surface"] if r["surface"] in tm.SURFACES else "Hard"
            si = 2 + tm.SURFACES.index(surf)
            w = elo.setdefault(_key(r["tour"], r["w"]), [tm.START, 0, tm.START, tm.START, tm.START, 0, 0, 0])
            l = elo.setdefault(_key(r["tour"], r["l"]), [tm.START, 0, tm.START, tm.START, tm.START, 0, 0, 0])
            if r["date"] >= check_from and w[1] >= 5 and l[1] >= 5:
                diffs.append((w[0] - l[0], w[si] - l[si]))
            pw = tm.elo_p(w[0], l[0])
            w[0] += tm.k_factor(w[1], km) * (1 - pw)
            l[0] -= tm.k_factor(l[1], km) * (1 - pw)
            ps = tm.elo_p(w[si], l[si])
            w[si] += tm.k_factor(w[si + 3], km) * (1 - ps)
            l[si] -= tm.k_factor(l[si + 3], km) * (1 - ps)
            w[1] += 1
            l[1] += 1
            w[si + 3] += 1
            l[si + 3] += 1
        for sw in SURFACE_WEIGHTS:
            ps_ = [1.0 / (1.0 + 10 ** (-((1 - sw) * d0 + sw * d1) / 400.0)) for d0, d1 in diffs]
            ll = -sum(log(max(1e-9, p)) for p in ps_) / max(1, len(ps_))
            tried[(km, sw)] = round(ll, 4)
            print(f"  Elo step x{km}, surface weight {sw}: log loss {ll:.4f} on {len(ps_)}", flush=True)
            if ll < best[2]:
                best = (km, sw, ll)
                best_pairs = ps_
    km, sw, _ = best
    shrink = min(SHRINKS, key=lambda s: -sum(log(max(1e-9, tm.shrink(p, s))) for p in best_pairs))
    return km, sw, shrink, {f"{k[0]}|{k[1]}": v for k, v in tried.items()}


def run(rows: List[Dict], today: Optional[date] = None, grid: bool = True) -> Tuple[Fit, Dict]:
    today = today or date.today()
    check_from = (today - timedelta(days=CHECK_FROM_DAYS)).isoformat()
    km, sw, shrink, tried = elo_grid(rows, check_from) if grid else (1.0, tm.SURFACE_WEIGHT, 1.0, {})
    print(f"Chosen: Elo step x{km}, surface weight {sw}, shrink {shrink}", flush=True)
    n_check = sum(1 for r in rows if r["date"] >= check_from)
    fit = Fit(km, sw, shrink, max(1, n_check // DETAIL_CHECKS))
    for r in rows:
        fit.add(r, check=r["date"] >= check_from)
    rep = report(fit)
    rep["grid"] = tried
    return fit, rep


def save(r, fit: Fit, rep: Dict, today: Optional[date] = None) -> int:
    import basketball_data as bd
    today = today or date.today()
    first = (today - timedelta(days=KEEP_DAYS)).isoformat()
    keep = {k: p.to_json() for k, p in fit.players.items() if p.last >= first}
    r.set(MODEL_KEY, bd.encode([{"players": keep, "avg": fit.tour_avg(), "as_of": today.isoformat(),
                                 "k_mult": fit.k_mult, "surface_weight": fit.sw, "shrink": fit.shrink}]))
    r.set(REPORT_KEY, json.dumps(rep))
    return len(keep)


def load_model(blob) -> Dict:
    """{players, avg, as_of, k_mult, surface_weight, shrink}"""
    import basketball_data as bd
    d = (bd.decode(blob) or [{}])[0]
    return {"players": {k: tm.Player.from_json(v) for k, v in (d.get("players") or {}).items()},
            "avg": d.get("avg") or dict(tm.DEFAULT_SPW), "as_of": d.get("as_of", ""),
            "k_mult": float(d.get("k_mult", 1.0)), "surface_weight": float(d.get("surface_weight", tm.SURFACE_WEIGHT)),
            "shrink": float(d.get("shrink", 1.0))}


async def web_rows(since: int) -> List[Dict]:
    """Years of web history (tennis_data.py); the source is chosen by what answers."""
    import tennis_data as td
    rows = await td.fetch_all(since)
    for r in rows:
        r["src"] = "web"
    return rows


def print_report(rep: Dict) -> None:
    w = rep["winner"]
    print(f"Walk-forward: {rep['matches']} matches ({rep.get('detailed')} with every line); winner picked right "
          f"{w['accuracy']:.1%}, log loss {w['log_loss']}; Elo step x{rep.get('k_mult')}, surface weight "
          f"{rep.get('surface_weight')}, shrink {rep.get('shrink')}")
    for b in w["calibration"]:
        print(f"  winner {b['bucket']}: said {b['said']:.1%} came in {b['came_in']:.1%} ({b['n']})")
    for b in rep["first_set"]:
        print(f"  first set {b['bucket']}: said {b['said']:.1%} came in {b['came_in']:.1%} ({b['n']})")
    for m, rows in rep["lines"].items():
        for b in rows:
            print(f"  {m} {b['bucket']}: said {b['said']:.1%} came in {b['came_in']:.1%} ({b['n']})")
    for g, v in list(rep["by_group"].items())[:12]:
        print(f"  {g}: {v['n']} matches, {v['accuracy']:.1%} right")


async def main(sample: bool, since: int) -> None:
    import model_store
    import tennis_facts as tf
    import basketball_data as bd
    r = None if sample else model_store._client()
    rows: List[Dict] = []
    try:
        rows += await web_rows(since if not sample else date.today().year - 2)
    except Exception as e:
        print(f"web history: {e}")
    if r is not None:
        sb: List[Dict] = []
        for blob in (r.hgetall(tf.SPORTS["tennis"]["key"]) or {}).values():
            sb += bd.decode(blob)
        rows += sportybet_rows(sb)
        print(f"SportyBet results: {len(sb)} matches")
    rows = dedupe(rows)
    print(f"{len(rows)} matches to fit ({rows[0]['date'] if rows else ''} .. {rows[-1]['date'] if rows else ''})")
    if not rows:
        raise SystemExit("No tennis matches to fit")
    fit, rep = run(rows)
    print_report(rep)
    if r is not None:
        kept = save(r, fit, rep)
        print(f"Saved {kept} players")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--since", type=int, default=2010)
    a = ap.parse_args()
    asyncio.run(main(a.sample, a.since))
