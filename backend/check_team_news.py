"""
Do expected goals (xG) and the starting XI predict football better than
goals alone? A walk-forward check on the top five leagues' last seasons,
from Understat's per-match player data (props_collect.py: every player's
minutes, start, goals, xG and penalties per match).

Match by match, in date order, each team's attack and defence are rated
from its earlier matches only (exponentially weighted, ALPHA per match):

  goals     from goals scored and conceded
  xg        from non-penalty xG for and against
  blend     a mix of the two (weight W on xG)

and then, knowing who starts (the lineup is out an hour before kick-off):

  attack present   the starters' share of the xG the team's players usually
                   produce, against the team's usual starters' share
  regulars present the starters' usual minutes, against the team's usual

which scale the team's own expected goals (attack, power B) and its
opponent's (regulars, power C: missing regulars concede more).

The expected goals go through a Dixon-Coles grid to 1X2 and over 2.5
chances, scored by log loss. Weights are chosen on matches before SPLIT and
judged on the matches after it; z compares each with the goals-only
ratings on the same matches (below -2 is a clear improvement).

    python check_team_news.py      (GitHub Actions: "Basketball data", job team_news)

The weights that win (only if they beat goals-only on the later matches)
are saved in Redis (CONFIG_KEY) for the server.
"""

import json
import math
from datetime import date
from typing import Dict, List, Optional, Tuple

import numpy as np

PLAYERS_KEY = "betiq:props:fb:{league}"
CONFIG_KEY = "betiq:team_news:config"
LEAGUES = ("Premier League", "LaLiga", "Bundesliga", "Serie A", "Ligue 1")
SPLIT = "2025-07-01"          # fit before, judge after
WARMUP = "2024-10-15"         # ratings need a couple of months first
PEN_XG = 0.76                 # a penalty's xG (Understat's)
RHO = -0.10
ALPHAS = (0.05, 0.08, 0.12)
WEIGHTS = (0.0, 0.25, 0.5, 0.75, 1.0)
POWERS_B = (0.0, 0.5, 1.0, 1.5)
POWERS_C = (0.0, 0.5, 1.0)
PLAYER_ALPHA = 0.15
K = np.arange(11)
FACT = np.array([math.factorial(int(k)) for k in K], dtype=float)
DIFF = K[:, None] - K[None, :]
TOTALS = K[:, None] + K[None, :]


# ── matches from the player rows ──────────────────────────────────────────

def matches_from_players(players: Dict[str, Dict], league: str) -> List[Dict]:
    """[{league, date, home, away, h: [...], a: [...]}] — each side's player
    rows (name, minutes, started, npxg, goals), date order."""
    out: Dict[Tuple[str, str, str], Dict] = {}
    for key, p in players.items():
        for g in p.get("games") or []:
            day, team, opp, is_home, mins, goals, _shots, xg, _ast, started, pens = (list(g) + [0] * 11)[:11]
            home, away = (team, opp) if is_home else (opp, team)
            m = out.setdefault((day, home, away), {"league": league, "date": day, "home": home, "away": away,
                                                   "h": [], "a": []})
            npxg = max(0.0, float(xg or 0) - PEN_XG * float(pens or 0))
            m["h" if is_home else "a"].append((key, float(mins or 0), bool(started), npxg, float(goals or 0)))
    return sorted((m for m in out.values() if m["h"] and m["a"]), key=lambda m: m["date"])


# ── probabilities ─────────────────────────────────────────────────────────

def grid(mh: float, ma: float, rho: float = RHO) -> np.ndarray:
    mh, ma = max(mh, 0.05), max(ma, 0.05)
    g = np.outer(mh ** K * np.exp(-mh) / FACT, ma ** K * np.exp(-ma) / FACT)
    g[0, 0] *= 1 - mh * ma * rho
    g[0, 1] *= 1 + mh * rho
    g[1, 0] *= 1 + ma * rho
    g[1, 1] *= 1 - rho
    g = np.clip(g, 0, None)
    return g / g.sum()


def losses(mh: float, ma: float, hg: int, ag: int) -> Tuple[float, float]:
    g = grid(mh, ma)
    p = {"H": g[DIFF > 0].sum(), "D": g[DIFF == 0].sum(), "A": g[DIFF < 0].sum()}
    res = "H" if hg > ag else "A" if ag > hg else "D"
    o25 = g[TOTALS > 2.5].sum()
    over = hg + ag > 2.5
    clip = lambda x: min(max(float(x), 1e-4), 1 - 1e-4)
    return -math.log(clip(p[res])), -math.log(clip(o25 if over else 1 - o25))


# ── the walk ──────────────────────────────────────────────────────────────

class Ratings:
    """Each team's weighted goals and npxG for and against, and each player's
    weighted minutes share and npxG per match, from earlier matches only."""

    def __init__(self, alpha: float):
        self.a = alpha
        self.team: Dict[str, Dict[str, float]] = {}
        self.lg: Dict[str, Dict[str, float]] = {}
        self.players: Dict[str, Dict[str, List[float]]] = {}   # team -> player -> [minutes share, npxg]

    def league(self, lg: str) -> Dict[str, float]:
        return self.lg.setdefault(lg, {"gh": 1.5, "ga": 1.2, "xh": 1.4, "xa": 1.15})

    def get(self, team: str, lg: str) -> Dict[str, float]:
        if team not in self.team:
            L = self.league(lg)
            g, x = (L["gh"] + L["ga"]) / 2, (L["xh"] + L["xa"]) / 2
            # A team new to the data (promoted): a little below average
            self.team[team] = {"gf": 0.85 * g, "ga": 1.15 * g, "xf": 0.85 * x, "xa": 1.15 * x}
        return self.team[team]

    def expected(self, m: Dict, w: float) -> Tuple[float, float]:
        L = self.league(m["league"])
        h, a = self.get(m["home"], m["league"]), self.get(m["away"], m["league"])
        g_avg, x_avg = (L["gh"] + L["ga"]) / 2, (L["xh"] + L["xa"]) / 2
        att = lambda t: (1 - w) * t["gf"] / g_avg + w * t["xf"] / x_avg
        dfn = lambda t: (1 - w) * t["ga"] / g_avg + w * t["xa"] / x_avg
        return L["gh"] * att(h) * dfn(a), L["ga"] * att(a) * dfn(h)

    def present(self, team: str, rows: List) -> Tuple[float, float]:
        """(attack present, regulars present) for these starters, against the
        team's usual XI (its 11 most-used players): 1 is a full-strength side."""
        ps = self.players.get(team) or {}
        if len(ps) < 11:
            return 1.0, 1.0
        usual = sorted(ps.values(), key=lambda v: -v[0])[:11]
        u_val, u_min = sum(v[1] for v in usual), sum(v[0] for v in usual)
        if u_val <= 0 or u_min <= 0:
            return 1.0, 1.0
        starters = [r[0] for r in rows if r[2]]
        v = sum(ps[p][1] for p in starters if p in ps)
        mins = sum(ps[p][0] for p in starters if p in ps)
        return (max(0.4, min(1.6, v / u_val)), max(0.4, min(1.6, mins / u_min)))

    def update(self, m: Dict) -> None:
        a = self.a
        L = self.league(m["league"])
        hg, ag = sum(r[4] for r in m["h"]), sum(r[4] for r in m["a"])
        hx, ax = sum(r[3] for r in m["h"]), sum(r[3] for r in m["a"])
        h, w = self.get(m["home"], m["league"]), self.get(m["away"], m["league"])
        for t, gf, ga, xf, xa in ((h, hg, ag, hx, ax), (w, ag, hg, ax, hx)):
            t["gf"] += a * (gf - t["gf"])
            t["ga"] += a * (ga - t["ga"])
            t["xf"] += a * (xf - t["xf"])
            t["xa"] += a * (xa - t["xa"])
        b = 0.02
        L["gh"] += b * (hg - L["gh"]); L["ga"] += b * (ag - L["ga"])
        L["xh"] += b * (hx - L["xh"]); L["xa"] += b * (ax - L["xa"])
        for team, rows in ((m["home"], m["h"]), (m["away"], m["a"])):
            ps = self.players.setdefault(team, {})
            seen = {r[0]: r for r in rows}
            for p in set(ps) | set(seen):
                cur = ps.setdefault(p, [0.0, 0.0])
                r = seen.get(p)
                share, npxg = (min(r[1], 90) / 90, r[3]) if r else (0.0, 0.0)
                cur[0] += PLAYER_ALPHA * (share - cur[0])
                cur[1] += PLAYER_ALPHA * (npxg - cur[1])


def walk(matches: List[Dict], alpha: float, w: float, b: float, c: float) -> List[Tuple[str, float, float]]:
    """[(date, 1X2 loss, over 2.5 loss)] for every match after WARMUP."""
    R = Ratings(alpha)
    out = []
    for m in matches:
        if m["date"] >= WARMUP:
            mh, ma = R.expected(m, w)
            if b or c:
                ah, rh = R.present(m["home"], m["h"])
                aa, ra = R.present(m["away"], m["a"])
                mh *= ah ** b * ra ** (-c)
                ma *= aa ** b * rh ** (-c)
            hg, ag = int(sum(r[4] for r in m["h"])), int(sum(r[4] for r in m["a"]))
            out.append((m["date"],) + losses(mh, ma, hg, ag))
        R.update(m)
    return out


def xg_probs(matches: List[Dict], alpha: float, w: float) -> Tuple[Dict[Tuple, Tuple], "Ratings"]:
    """{(league, date, home, away): (p_home, p_draw, p_away)} from the ratings
    before each match, and the ratings after the last one (for what's next)."""
    R = Ratings(alpha)
    out = {}
    for m in matches:
        mh, ma = R.expected(m, w)
        g = grid(mh, ma)
        out[(m["league"], m["date"], m["home"], m["away"])] = (float(g[DIFF > 0].sum()), float(g[DIFF == 0].sum()),
                                                                float(g[DIFF < 0].sum()))
        R.update(m)
    return out, R


# ── on our published predictions ───────────────────────────────────────────
LEAGUE_CODES = {"PL": "Premier League", "PD": "LaLiga", "BL1": "Bundesliga", "SA": "Serie A", "FL1": "Ligue 1"}
OVERLAY_WEIGHTS = tuple(round(0.1 * i, 1) for i in range(11))


def index(probs: Dict[Tuple, Tuple]) -> Dict[Tuple[str, str], List]:
    """The xG model's chances by (league, date), for find()."""
    out: Dict[Tuple[str, str], List] = {}
    for (lg, d, h, a), p in probs.items():
        out.setdefault((lg, d), []).append((h, a, p))
    return out


def find(idx: Dict[Tuple[str, str], List], league: str, day: str, home: str, away: str) -> Optional[Tuple]:
    """The xG model's chances for a match: same league, a day either side, both names alike."""
    import matchday
    from datetime import timedelta
    d0 = date.fromisoformat(day)
    best, score = None, 0.0
    for off in (0, -1, 1):
        for h, a, p in idx.get((league, (d0 + timedelta(days=off)).isoformat()), []):
            sh, sa = matchday._similar(home, h), matchday._similar(away, a)
            if min(sh, sa) >= 0.75 and sh + sa > score:
                best, score = p, sh + sa
    return best


def blended(e: Dict, xg: Tuple, v: float) -> Optional[Tuple[float, float, float]]:
    """The published 1X2 with `v` of the model's own half replaced by the xG
    model's: published = ours_share·model + (1 − ours_share)·market."""
    pred = e.get("pred") or {}
    pub = [pred.get(k) for k in ("p_home", "p_draw", "p_away")]
    if not all(isinstance(x, (int, float)) for x in pub):
        return None
    model = [pred.get(k) for k in ("p_home_model", "p_draw_model", "p_away_model")]
    if all(isinstance(x, (int, float)) for x in model):
        share = 0.5                                   # main.FOOTBALL_1X2_OURS
        out = [p - share * m + share * ((1 - v) * m + v * x) for p, m, x in zip(pub, model, xg)]
    else:
        out = [(1 - v) * p + v * x for p, x in zip(pub, xg)]
    s = sum(out)
    return tuple(x / s for x in out) if s > 0 else None


def _judge(rows: List[Tuple], split: str) -> Dict:
    """rows: (date, published (h, d, a), model (h, d, a) or None, xg (h, d, a), outcome).
    The weight on the xG model (inside our half of the published 1X2) chosen
    before `split`, judged after it; z against the published 1X2."""
    ll = lambda p, o: -math.log(min(max(p[o], 1e-4), 1 - 1e-4))

    def mix(pub, model, xg, v):
        if model:
            out = [p - 0.5 * m + 0.5 * ((1 - v) * m + v * x) for p, m, x in zip(pub, model, xg)]
        else:
            out = [(1 - v) * p + v * x for p, x in zip(pub, xg)]
        s = sum(out)
        return [x / s for x in out]
    early = [r for r in rows if r[0] < split]
    late = [r for r in rows if r[0] >= split]
    if len(early) < 100 or len(late) < 100:
        return {"matches": len(rows), "note": "too few"}
    mean_ll = lambda part, v: sum(ll(mix(r[1], r[2], r[3], v), r[4]) for r in part) / len(part)
    best = min(OVERLAY_WEIGHTS, key=lambda v: mean_ll(early, v))
    d = [ll(mix(r[1], r[2], r[3], best), r[4]) - ll(mix(r[1], r[2], r[3], 0.0), r[4]) for r in late]
    mean = sum(d) / len(d)
    sd = math.sqrt(sum((x - mean) ** 2 for x in d) / (len(d) - 1))
    return {"matches": len(rows), "test": len(late), "split": split, "weight": best,
            "published": round(mean_ll(late, 0.0), 4), "with_xg": round(mean_ll(late, best), 4),
            "z": round(mean / (sd / math.sqrt(len(d))), 2) if sd > 0 else 0.0}


def backtest_overlay(probs: Dict[Tuple, Tuple], start: str = "2024-10-15", end: str = "2025-06-30",
                     split: str = "2025-03-01") -> Dict:
    """The live model re-trained month by month (backtest.walk_forward) over
    2024-25: its 1X2 mixed half and half with the bookmaker's (as published),
    with and without the xG model inside our half."""
    import backtest
    import fair_odds
    from main import _load_football_data_csvs
    idx = index(probs)
    recs = backtest.walk_forward(_load_football_data_csvs(), start, end, log=lambda s: None)
    rows = []
    for rec in recs:
        lg = LEAGUE_CODES.get(rec.get("league") or "")
        if not lg:
            continue
        xg = find(idx, lg, rec["date"], rec["home"], rec["away"])
        if not xg:
            continue
        model = (rec["p_home"], rec["p_draw"], rec["p_away"])
        odds = [rec.get("odds_home"), rec.get("odds_draw"), rec.get("odds_away")]
        if all(o and o > 1 for o in odds):
            market = fair_odds.fair(odds)
            pub = tuple(0.5 * m + 0.5 * k for m, k in zip(model, market))
            rows.append((rec["date"], pub, model, xg, "HDA".index(rec["result"])))
        else:
            rows.append((rec["date"], model, None, xg, "HDA".index(rec["result"])))
    rows.sort(key=lambda t: t[0])
    return _judge(rows, split)


def overlay(r, probs: Dict[Tuple, Tuple], days_back: int = 120) -> Dict:
    """Does mixing the xG model into our published 1X2 help? Weight chosen on
    the earlier half of the stored matches, judged on the later half."""
    from datetime import timedelta
    idx = index(probs)
    rows = []
    today = date.today()
    for i in range(1, days_back + 1):
        d = (today - timedelta(days=i)).isoformat()
        raw = r.get(f"betiq:md:{d}")
        for e in (json.loads(raw).values() if raw else []):
            lg = LEAGUE_CODES.get(e.get("league") or "")
            res = e.get("result") or {}
            if not lg or res.get("status") != "finished" or res.get("hg") is None:
                continue
            xg = find(idx, lg, e["date"], e["home"], e["away"])
            if xg:
                outcome = 0 if res["hg"] > res["ag"] else 1 if res["hg"] == res["ag"] else 2
                rows.append((e["date"], e, xg, outcome))
    rows.sort(key=lambda t: t[0])
    if len(rows) < 60:
        return {"matches": len(rows), "note": "too few stored top-five-league matches"}
    half = len(rows) // 2
    ll = lambda p, o: -math.log(min(max(p[o], 1e-4), 1 - 1e-4))

    def losses(v, part):
        return [ll(b, o) for _, e, xg, o in part if (b := blended(e, xg, v))]
    best = min(OVERLAY_WEIGHTS, key=lambda v: sum(losses(v, rows[:half])) / max(1, len(losses(v, rows[:half]))))
    base, cand = losses(0.0, rows[half:]), losses(best, rows[half:])
    d = [c - b for b, c in zip(base, cand)]
    mean = sum(d) / len(d)
    sd = math.sqrt(sum((x - mean) ** 2 for x in d) / (len(d) - 1)) if len(d) > 1 else 0
    z = round(mean / (sd / math.sqrt(len(d))), 2) if sd > 0 else 0.0
    return {"matches": len(rows), "test": len(d), "weight": best, "published": round(sum(base) / len(base), 4),
            "with_xg": round(sum(cand) / len(cand), 4), "z": z}


def _mean(rows, i, after: bool) -> float:
    xs = [r[i] for r in rows if (r[0] >= SPLIT) == after]
    return sum(xs) / len(xs) if xs else float("nan")


def _z(a: List, b: List, i: int) -> float:
    d = [x[i] - y[i] for x, y in zip(a, b) if x[0] >= SPLIT]
    if len(d) < 2:
        return 0.0
    mean = sum(d) / len(d)
    sd = math.sqrt(sum((x - mean) ** 2 for x in d) / (len(d) - 1))
    return round(mean / (sd / math.sqrt(len(d))), 2) if sd > 0 else 0.0


def run(matches: List[Dict]) -> Dict:
    score = lambda rows: _mean(rows, 1, False) + _mean(rows, 2, False)     # fit on the earlier matches
    # 1) goals-only ratings: the baseline (its best ALPHA)
    base_alpha = min(ALPHAS, key=lambda a: score(walk(matches, a, 0.0, 0, 0)))
    base = walk(matches, base_alpha, 0.0, 0, 0)
    # 2) xG in the ratings
    best_aw = min(((a, w) for a in ALPHAS for w in WEIGHTS), key=lambda aw: score(walk(matches, aw[0], aw[1], 0, 0)))
    xg_rows = walk(matches, *best_aw, 0, 0)
    # 3) the starting XI on top
    best_bc = min(((b, c) for b in POWERS_B for c in POWERS_C), key=lambda bc: score(walk(matches, *best_aw, *bc)))
    xi_rows = walk(matches, *best_aw, *best_bc)
    n_test = sum(1 for r in base if r[0] >= SPLIT)
    table = {}
    for name, rows in (("goals only", base), ("with xG", xg_rows), ("with xG + lineups", xi_rows)):
        table[name] = {"1x2": round(_mean(rows, 1, True), 4), "o25": round(_mean(rows, 2, True), 4),
                       "z_1x2": _z(rows, base, 1), "z_o25": _z(rows, base, 2)}
    rep = {"matches": len(matches), "test_matches": n_test, "split": SPLIT, "base_alpha": base_alpha,
           "alpha": best_aw[0], "xg_weight": best_aw[1], "attack_power": best_bc[0], "regulars_power": best_bc[1],
           "table": table}
    xg_wins = table["with xG"]["z_1x2"] < -2 and table["with xG"]["z_o25"] <= 0.5
    xi = table["with xG + lineups"]
    xi_wins = best_bc != (0.0, 0.0) and _z(xi_rows, xg_rows, 1) < -2
    rep["z_lineups_vs_xg"] = {"1x2": _z(xi_rows, xg_rows, 1), "o25": _z(xi_rows, xg_rows, 2)}
    rep["use"] = {"xg_weight": best_aw[1] if xg_wins else 0.0, "alpha": best_aw[0] if xg_wins else base_alpha,
                  "attack_power": best_bc[0] if xi_wins else 0.0, "regulars_power": best_bc[1] if xi_wins else 0.0}
    return rep


def load(r) -> List[Dict]:
    import basketball_data as bd
    out = []
    for lg in LEAGUES:
        raw = r.get(PLAYERS_KEY.format(league=lg))
        players = bd.decode(raw) if raw else {}
        ms = matches_from_players(players if isinstance(players, dict) else {}, lg)
        print(f"{lg}: {len(players)} players, {len(ms)} matches ({ms[0]['date'] if ms else '—'} to "
              f"{ms[-1]['date'] if ms else '—'})")
        out += ms
    return sorted(out, key=lambda m: m["date"])


def main() -> None:
    import model_store
    r = model_store._client()
    if r is None:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    matches = load(r)
    rep = run(matches)
    print(f"\n{rep['matches']} matches; judged on the {rep['test_matches']} from {SPLIT} "
          f"(fitted on the ones before). Log loss, lower is better; z vs goals only:")
    for name, row in rep["table"].items():
        print(f"  {name:<20} 1X2 {row['1x2']:.4f} (z {row['z_1x2']:+.2f})   over 2.5 {row['o25']:.4f} (z {row['z_o25']:+.2f})")
    print(f"  weights: alpha {rep['alpha']} (goals-only {rep['base_alpha']}), xG weight {rep['xg_weight']}, "
          f"attack power {rep['attack_power']}, regulars power {rep['regulars_power']}")
    print(f"  lineups vs xG alone: z {rep['z_lineups_vs_xg']}")
    print(f"  → use: {rep['use']}")
    probs, _ = xg_probs(matches, rep["alpha"], rep["xg_weight"] or 0.75)
    ov = overlay(r, probs)
    print(f"\nOn our published 1X2 (stored before kick-off), top five leagues: {ov}")
    bt = backtest_overlay(probs)
    print(f"On the live model re-trained month by month over 2024-25, published as half the bookmaker's: {bt}")
    # The server mixes the xG model into our half of the 1X2 only if that
    # helped on the later matches of the backtest (the bigger test)
    # (a clear gain: z below -2, as for every other correction)
    use_overlay = bt.get("weight", 0) > 0 and bt.get("z", 0) < -2 and bt.get("with_xg", 9) < bt.get("published", 0)
    ov = {"stored": ov, "backtest": bt}
    rep["use"]["overlay_weight"] = bt["weight"] if use_overlay else 0.0
    print(f"  → xG mixed into our 1X2 at {rep['use']['overlay_weight']}")
    r.set(CONFIG_KEY, json.dumps({**rep["use"], "report": {**{k: rep[k] for k in ("matches", "test_matches", "table",
                                                                                   "z_lineups_vs_xg")}, "overlay": ov},
                                  "at": date.today().isoformat()}))


if __name__ == "__main__":
    main()
