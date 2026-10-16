"""
Fit the tennis model (GitHub Actions, "Racket data" → tennis_fit) and check
it walk-forward: every match is predicted from the matches before it only,
then added.

Sources, merged in date order (a match in two sources counted once):
- web history (tennis_data.py): years of tour, Challenger and ITF matches;
- SportyBet's own results the server collects (tennis_facts.py, ~300 days,
  every level it prices), which carry the names the server matches on.

The Elo step, surface weight and shrink are chosen for all matches, then
per tour (ATP, WTA, Challenger, ITF: tennis_model.group_of): a tour's own
pick, made on the earlier half of the checked years, is kept only where it
beats the shared one on the later half by MIN_GAIN. The serve records'
half-life is checked the same way, on serve points won.

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

import numpy as np

import player_props as pp
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
SERVE_HALF_LIVES = (180.0, 365.0, 730.0)
MIN_GAIN = 0.002                 # share of the shared pick's log loss a tour's (or half-life's) must save, held out
GROUP_MIN_HELD = 2000            # held-out matches a tour needs to be tuned on its own


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


def _group(r: Dict) -> str:
    g = r.get("group")
    if g is None:
        g = r["group"] = tm.group_of(r.get("tourney") or "", r["tour"], r.get("level") or "")
    return g


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
                 detail_every: int = 1, by_group: Optional[Dict[str, Dict]] = None,
                 serve_half_life: float = SERVE_HALF_LIFE):
        self.k_mult, self.sw, self.shrink, self.detail_every = k_mult, surface_weight, shrink, detail_every
        self.by_group = by_group or {}           # tour group -> its own k_mult / surface_weight / shrink
        self.serve_hl = serve_half_life
        self.players: Dict[str, tm.Player] = {}
        self.avg = {"ATP": [0.0, 0.0], "WTA": [0.0, 0.0]}      # tour serve points [played, won]
        self.checked: List[Tuple[str, str, float, bool, Dict]] = []

    def player(self, tour: str, name: str) -> tm.Player:
        k = _key(tour, name)
        p = self.players.get(k)
        if p is None:
            p = self.players[k] = tm.Player(name, tour)
        return p

    def setting(self, group: str, name: str) -> float:
        return (self.by_group.get(group) or {}).get(name, getattr(self, {"k_mult": "k_mult", "surface_weight": "sw",
                                                                         "shrink": "shrink"}[name]))

    def tour_avg(self) -> Dict[str, float]:
        return {t: (v[1] / v[0] if v[0] > 1000 else tm.DEFAULT_SPW[t]) for t, v in self.avg.items()}

    def _decay(self, p: tm.Player, day: str) -> None:
        if not p.last:
            return
        gap = (date.fromisoformat(day) - date.fromisoformat(p.last)).days
        if gap > 0:
            f = 0.5 ** (gap / self.serve_hl)
            p.sv = [p.sv[0] * f, p.sv[1] * f]
            p.rt = [p.rt[0] * f, p.rt[1] * f]

    def add(self, r: Dict, check: bool) -> None:
        tour, surf = r["tour"], r["surface"] if r["surface"] in tm.SURFACES else "Hard"
        w, l = self.player(tour, r["w"]), self.player(tour, r["l"])
        if check and w.n >= 5 and l.n >= 5:
            self._check(r, w, l, surf)
        # Elo: overall and surface (the step the match's tour uses)
        km = self.setting(_group(r), "k_mult")
        pw = tm.elo_p(w.elo, l.elo)
        kw, kl = tm.k_factor(w.n, km), tm.k_factor(l.n, km)
        w.elo += kw * (1 - pw)
        l.elo -= kl * (1 - pw)
        ps = tm.elo_p(w.surf[surf], l.surf[surf])
        ksw, ksl = tm.k_factor(w.surf_n[surf], km), tm.k_factor(l.surf_n[surf], km)
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
        g = _group(r)
        sw = self.setting(g, "surface_weight")
        p_a = tm.shrink(tm.elo_p(a.rating(surf, sw), b.rating(surf, sw)), self.setting(g, "shrink"))
        p_a = tm.layoff(p_a, a, b, r["date"])
        group = r["tour"] + ":" + r.get("level", r.get("src", ""))
        if (h >> 1) % self.detail_every:
            self.checked.append((r["date"], group, p_a, first_is_w, None))
            return
        pa0, pb0 = tm.serve_chances(a, b, self.tour_avg())
        pa, pb = tm.solve_serve(p_a, pa0, pb0, r.get("best_of", 3))
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
            lines += [("total_games:O", po, total > line), ("total_games:U", 1 - po, total < line)]
        for line in (-4.5, -2.5, 2.5, 4.5):
            ph = md.p_handicap(line)
            lines += [("games_handicap", ph, games_a - games_b + line > 0), ("games_handicap", 1 - ph, games_a - games_b + line < 0)]
        sets_a = sum(1 for g in r["sets"] if (g[0] > g[1]) == first_is_w)
        sets_b = len(r["sets"]) - sets_a
        if len(r["sets"]) >= 2 and max(sets_a, sets_b) == r.get("best_of", 3) // 2 + 1:
            for line in (-1.5, 1.5):
                ps = md.p_set_handicap(line)
                lines += [("set_handicap", ps, sets_a - sets_b + line > 0), ("set_handicap", 1 - ps, sets_a - sets_b + line < 0)]
            ts = r.get("best_of", 3) // 2 + 1.5
            po = sum(v for (x, y), v in md.sets.items() if x + y > ts)
            lines += [("total_sets:O", po, len(r["sets"]) > ts), ("total_sets:U", 1 - po, len(r["sets"]) < ts)]
        if r["sets"]:
            g1 = r["sets"][0]
            po = md.p_first_set_total_over(9.5)
            lines += [("set_total:O", po, g1[0] + g1[1] > 9.5), ("set_total:U", 1 - po, g1[0] + g1[1] < 9.5)]
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


def calibration_maps(markets: Iterable[Tuple[str, List[Tuple[float, bool]]]]) -> Tuple[Dict, Dict]:
    """Per market, the map pricing applies (fitted on every row) and how a
    map fitted on half the rows did on the other half. A market named
    "…:O" / "…:U" (a total's over or under) is fitted on its likelier side
    only (p >= 50%): overs and unders don't mirror each other. Others
    (the two players' sides) are folded at 50%."""
    maps, held_out = {}, {}
    for m, rows in markets:
        floor = 0.5
        if m[-2:] in (":O", ":U"):
            rows = [(p, w) for p, w in rows if p >= 0.5]
            floor = 0.0
        maps[m] = pp.fit_calibration_map(rows, floor=floor)
        half = pp.fit_calibration_map(rows[::2], floor=floor)
        checked = [(pp.calibrate(p, half), w) for p, w in rows[1::2]]
        held_out[m] = calibration([(p, w) if p >= 0.5 else (1 - p, not w) for p, w in checked])
    return maps, held_out


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
    # Calibration maps per market (pricing applies them): fitted on half the
    # checked matches and tried on the other half, then fitted on all
    maps, held_out = calibration_maps([("first_set", [(p, w) for p, w in first])] + list(lines.items()))
    by_group: Dict[str, List[Tuple[float, bool]]] = {}
    for _, g, p, won, _ in fit.checked:
        by_group.setdefault(g, []).append((max(p, 1 - p), won if p >= 0.5 else not won))
    return {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "matches": len(fit.checked),
            "detailed": len(detailed), "k_mult": fit.k_mult, "surface_weight": fit.sw, "shrink": fit.shrink,
            "by_tour": fit.by_group, "serve_half_life": fit.serve_hl,
            "winner": {"accuracy": round(acc, 4), "log_loss": round(ll, 4), "calibration": calibration(winner)},
            "first_set": calibration(first),
            "lines": {m: calibration([x for x in v if x[0] >= 0.5]) for m, v in lines.items()},
            "calibration_maps": maps, "calibrated_held_out": held_out,
            "by_group": {g: {"n": len(v), "accuracy": round(sum(1 for _, w in v if w) / len(v), 4)}
                         for g, v in sorted(by_group.items(), key=lambda kv: -len(kv[1]))},
            "players": len(fit.players), "tour_avg": fit.tour_avg()}


def _elo_pass(rows: List[Dict], check_from: str, k_mult: float,
              by_group: Optional[Dict[str, float]] = None) -> Dict[str, np.ndarray]:
    """One Elo pass: for every checked match, the winner's lead in overall
    and surface rating before it, its tour group and date."""
    by_group = by_group or {}
    elo: Dict[str, List[float]] = {}           # key -> [overall, n, hard, clay, grass, n_h, n_c, n_g]
    d0, d1, groups, dates = [], [], [], []
    for r in rows:
        surf = r["surface"] if r["surface"] in tm.SURFACES else "Hard"
        si = 2 + tm.SURFACES.index(surf)
        g = _group(r)
        km = by_group.get(g, k_mult)
        w = elo.setdefault(_key(r["tour"], r["w"]), [tm.START, 0, tm.START, tm.START, tm.START, 0, 0, 0])
        l = elo.setdefault(_key(r["tour"], r["l"]), [tm.START, 0, tm.START, tm.START, tm.START, 0, 0, 0])
        if r["date"] >= check_from and w[1] >= 5 and l[1] >= 5:
            d0.append(w[0] - l[0])
            d1.append(w[si] - l[si])
            groups.append(g)
            dates.append(r["date"])
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
    return {"d0": np.array(d0, dtype=float), "d1": np.array(d1, dtype=float),
            "group": np.array(groups, dtype=object), "date": np.array(dates, dtype=object)}


def _losses(d: Dict[str, np.ndarray], sw: float, s: float) -> np.ndarray:
    """Each checked match's log loss on the winner (the blend's chance,
    shrunk by s: tennis_model.shrink is the logit times s)."""
    x = ((1 - sw) * d["d0"] + sw * d["d1"]) * (np.log(10) / 400.0)
    return np.logaddexp(0.0, -s * x)


def _mean(v: np.ndarray) -> Optional[float]:
    return float(v.mean()) if len(v) else None


def elo_grid(rows: List[Dict], check_from: str) -> Tuple[float, float, float, Dict, Dict]:
    """The Elo step, surface weight and shrink with the lowest walk-forward
    log loss on the winner (Elo only: one pass per step, every weight read
    from the same pass), then each tour's own (tour_settings)."""
    tried: Dict[Tuple[float, float], float] = {}
    passes = {km: _elo_pass(rows, check_from, km) for km in K_MULTS}
    best = (1.0, tm.SURFACE_WEIGHT, 9.0)
    for km, d in passes.items():
        for sw in SURFACE_WEIGHTS:
            ll = _mean(_losses(d, sw, 1.0)) or 9.0
            tried[(km, sw)] = round(ll, 4)
            print(f"  Elo step x{km}, surface weight {sw}: log loss {ll:.4f} on {len(d['d0'])}", flush=True)
            if ll < best[2]:
                best = (km, sw, ll)
    km, sw, _ = best
    shrink = min(SHRINKS, key=lambda s: _mean(_losses(passes[km], sw, s)) or 9.0)
    tours = tour_settings(rows, check_from, passes, (km, sw, shrink))
    return km, sw, shrink, {f"{k[0]}|{k[1]}": v for k, v in tried.items()}, tours


def tour_settings(rows: List[Dict], check_from: str, passes: Dict[float, Dict[str, np.ndarray]],
                  shared: Tuple[float, float, float]) -> Dict:
    """Per tour group, the (Elo step, surface weight, shrink) with the lowest
    log loss on the earlier half of the checked matches; kept where it beats
    the shared settings on the later half by MIN_GAIN (with GROUP_MIN_HELD
    matches there), then confirmed in one pass with every kept step."""
    km0, sw0, s0 = shared
    base = passes[km0]
    if not len(base["date"]):
        return {"tuned": {}, "tours": {}}
    dates = sorted(base["date"])
    cut = dates[len(dates) // 2]
    base_loss = _losses(base, sw0, s0)
    out: Dict = {"cut": cut, "shared": {"k_mult": km0, "surface_weight": sw0, "shrink": s0}, "tours": {}, "tuned": {}}
    for g in tm.GROUPS:
        sel = base["group"] == g
        early, late = sel & (base["date"] < cut), sel & (base["date"] >= cut)
        n_held = int(late.sum())
        if not early.any() or not n_held:
            continue
        pick, pick_loss = (km0, sw0, s0), _mean(base_loss[early])
        for km, d in passes.items():
            gs = d["group"] == g
            e = gs & (d["date"] < cut)
            for sw in SURFACE_WEIGHTS:
                for s in SHRINKS:
                    ll = _mean(_losses({k: v[e] for k, v in d.items()}, sw, s))
                    if ll is not None and ll < pick_loss - 1e-12:
                        pick, pick_loss = (km, sw, s), ll
        d = passes[pick[0]]
        late_p = (d["group"] == g) & (d["date"] >= cut)
        held_shared = _mean(base_loss[late])
        held_pick = _mean(_losses({k: v[late_p] for k, v in d.items()}, pick[1], pick[2]))
        keep = (pick != (km0, sw0, s0) and n_held >= GROUP_MIN_HELD and held_pick is not None
                and held_pick <= held_shared * (1 - MIN_GAIN))
        out["tours"][g] = {"best": {"k_mult": pick[0], "surface_weight": pick[1], "shrink": pick[2]},
                           "keep": keep, "held_matches": n_held, "shared": round(held_shared, 5),
                           "tuned": round(held_pick, 5) if held_pick is not None else None}
        if keep:
            out["tuned"][g] = out["tours"][g]["best"]
        print(f"  {g}: best x{pick[0]} / {pick[1]} / {pick[2]} on the earlier half · held out {n_held}: "
              f"log loss {held_shared:.5f} -> {held_pick:.5f} · {'KEEP' if keep else 'shared'}", flush=True)
    # Ratings are shared across tours (players move between them): the kept
    # steps together, one pass; a tour that no longer wins goes back
    steps = {g: v["k_mult"] for g, v in out["tuned"].items() if v["k_mult"] != km0}
    if steps:
        d = _elo_pass(rows, check_from, km0, steps)
        for g in list(out["tuned"]):
            v = out["tuned"][g]
            late_p = (d["group"] == g) & (d["date"] >= cut)
            got = _mean(_losses({k: x[late_p] for k, x in d.items()}, v["surface_weight"], v["shrink"]))
            out["tours"][g]["together"] = round(got, 5) if got is not None else None
            if got is None or got > out["tours"][g]["shared"] * (1 - MIN_GAIN):
                del out["tuned"][g]
                out["tours"][g]["keep"] = False
                print(f"  {g}: with every tour's step together {got} — back to shared", flush=True)
    return out


def serve_check(rows: List[Dict], check_from: str, half_lives: Iterable[float] = SERVE_HALF_LIVES) -> Dict:
    """The serve records' half-life: each checked match's serve points won,
    predicted from the records before it (tennis_model.serve_chances), per
    point log loss on the earlier and later half of the checked matches;
    another half-life is kept only where it beats SERVE_HALF_LIFE on the
    later half by MIN_GAIN."""
    checked = sorted(r["date"] for r in rows if r["date"] >= check_from and r.get("sv"))
    if not checked:
        return {"use": SERVE_HALF_LIFE, "keep": False}
    cut = checked[len(checked) // 2]
    half_lives = sorted(set(half_lives) | {SERVE_HALF_LIFE})
    res: Dict[float, List[List[float]]] = {}
    for hl in half_lives:
        fit = Fit(serve_half_life=hl)
        acc = [[0.0, 0.0], [0.0, 0.0]]                 # per half: [loss, points]
        for r in rows:
            w, l = fit.player(r["tour"], r["w"]), fit.player(r["tour"], r["l"])
            for p in (w, l):
                fit._decay(p, r["date"])
                p.last = r["date"]
            sv = r.get("sv")
            if sv and r["date"] >= check_from and w.n >= 5 and l.n >= 5:
                pw, pl = tm.serve_chances(w, l, fit.tour_avg())
                w_pts, w_won, l_pts, l_won = sv
                h = acc[int(r["date"] >= cut)]
                h[0] -= w_won * log(pw) + (w_pts - w_won) * log(1 - pw) + l_won * log(pl) + (l_pts - l_won) * log(1 - pl)
                h[1] += w_pts + l_pts
            w.n += 1
            l.n += 1
            if sv:
                w_pts, w_won, l_pts, l_won = sv
                w.sv = [w.sv[0] + w_pts, w.sv[1] + w_won]
                l.sv = [l.sv[0] + l_pts, l.sv[1] + l_won]
                w.rt = [w.rt[0] + l_pts, w.rt[1] + (l_pts - l_won)]
                l.rt = [l.rt[0] + w_pts, l.rt[1] + (w_pts - w_won)]
                fit.avg[r["tour"]][0] += w_pts + l_pts
                fit.avg[r["tour"]][1] += w_won + l_won
        res[hl] = acc
        print(f"  serve half-life {hl:g} days: log loss per point {acc[0][0] / max(1, acc[0][1]):.5f} (earlier half), "
              f"{acc[1][0] / max(1, acc[1][1]):.5f} (later)", flush=True)

    def per_point(hl: float, half: int) -> float:
        a = res[hl][half]
        return a[0] / a[1] if a[1] else 9.0
    best = min(half_lives, key=lambda hl: per_point(hl, 0))
    keep = best != SERVE_HALF_LIFE and per_point(best, 1) <= per_point(SERVE_HALF_LIFE, 1) * (1 - MIN_GAIN)
    return {"cut": cut, "best": best, "keep": keep, "use": best if keep else SERVE_HALF_LIFE,
            "held_points": res[SERVE_HALF_LIFE][1][1],
            "default": round(per_point(SERVE_HALF_LIFE, 1), 5), "tuned": round(per_point(best, 1), 5),
            "grid": {f"{hl:g}": [round(per_point(hl, 0), 5), round(per_point(hl, 1), 5)] for hl in half_lives}}


def run(rows: List[Dict], today: Optional[date] = None, grid: bool = True) -> Tuple[Fit, Dict]:
    today = today or date.today()
    check_from = (today - timedelta(days=CHECK_FROM_DAYS)).isoformat()
    if grid:
        km, sw, shrink, tried, tours = elo_grid(rows, check_from)
        serve = serve_check(rows, check_from)
    else:
        km, sw, shrink, tried, tours, serve = 1.0, tm.SURFACE_WEIGHT, 1.0, {}, {}, {}
    hl = serve.get("use", SERVE_HALF_LIFE)
    print(f"Chosen: Elo step x{km}, surface weight {sw}, shrink {shrink}; tours on their own: "
          f"{sorted((tours.get('tuned') or {}))}; serve half-life {hl:g} days", flush=True)
    n_check = sum(1 for r in rows if r["date"] >= check_from)
    fit = Fit(km, sw, shrink, max(1, n_check // DETAIL_CHECKS), tours.get("tuned") or {}, hl)
    for r in rows:
        fit.add(r, check=r["date"] >= check_from)
    rep = report(fit)
    rep["grid"] = tried
    rep["tour_tuning"] = tours
    rep["serve_tuning"] = serve
    return fit, rep


def save(r, fit: Fit, rep: Dict, today: Optional[date] = None) -> int:
    import basketball_data as bd
    today = today or date.today()
    first = (today - timedelta(days=KEEP_DAYS)).isoformat()
    keep = {k: p.to_json() for k, p in fit.players.items() if p.last >= first}
    r.set(MODEL_KEY, bd.encode([{"players": keep, "avg": fit.tour_avg(), "as_of": today.isoformat(),
                                 "k_mult": fit.k_mult, "surface_weight": fit.sw, "shrink": fit.shrink,
                                 "by_tour": fit.by_group, "serve_half_life": fit.serve_hl,
                                 "calibration": rep.get("calibration_maps") or {}}]))
    r.set(REPORT_KEY, json.dumps(rep))
    return len(keep)


def load_model(blob) -> Dict:
    """{players, avg, as_of, k_mult, surface_weight, shrink, by_tour, calibration}"""
    import basketball_data as bd
    d = (bd.decode(blob) or [{}])[0]
    return {"players": {k: tm.Player.from_json(v) for k, v in (d.get("players") or {}).items()},
            "avg": d.get("avg") or dict(tm.DEFAULT_SPW), "as_of": d.get("as_of", ""),
            "k_mult": float(d.get("k_mult", 1.0)), "surface_weight": float(d.get("surface_weight", tm.SURFACE_WEIGHT)),
            "shrink": float(d.get("shrink", 1.0)), "calibration": d.get("calibration") or {},
            "by_tour": {g: {k: float(x) for k, x in v.items()} for g, v in (d.get("by_tour") or {}).items()}}


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
          f"{rep.get('surface_weight')}, shrink {rep.get('shrink')}; tours on their own {rep.get('by_tour')}, "
          f"serve half-life {rep.get('serve_half_life')}")
    for b in w["calibration"]:
        print(f"  winner {b['bucket']}: said {b['said']:.1%} came in {b['came_in']:.1%} ({b['n']})")
    for b in rep["first_set"]:
        print(f"  first set {b['bucket']}: said {b['said']:.1%} came in {b['came_in']:.1%} ({b['n']})")
    for m, rows in rep["lines"].items():
        for b in rows:
            print(f"  {m} {b['bucket']}: said {b['said']:.1%} came in {b['came_in']:.1%} ({b['n']})")
    for m, rows in (rep.get("calibrated_held_out") or {}).items():
        for b in rows:
            print(f"  {m} calibrated (held out) {b['bucket']}: said {b['said']:.1%} came in {b['came_in']:.1%} ({b['n']})")
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
