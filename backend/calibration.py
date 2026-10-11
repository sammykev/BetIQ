"""
Every market's chances, in every sport, corrected by how its picks have
actually come in — and only where that correction is shown to work on
picks it never saw.

Our chance p for a pick becomes

    q = 1 / (1 + exp(-(a + b * logit p)))           (Platt scaling)

with (a, b) fitted on settled picks at four levels, each pulled towards the
one above it by how many picks it has (SHRINK):

    all picks → the sport → the market's family → the market

so a market with few picks (basketball 2nd-half handicap) learns from its
family (every basketball handicap) and its sport, and one with many keeps
its own shape.

The proof: maps are fitted on the earlier days (1 − HOLDOUT of them) and
scored on the later ones by log loss against our raw chances. A market
gets its correction only if that beat raw on at least MIN_TEST later
picks — its own, or failing that its family's, or its sport's. The live
maps are then refitted on every day. The report (REPORT_KEY, admin panel)
keeps, per market and overall: picks, hit rate, what we said raw and
corrected, and the later days' log loss and Brier score both ways.

Settled picks come from:
  football      every pick rated ≥ 50% in the match-day store (market_accuracy)
  basketball,   the match-day grades (winner, total, best line) and every line
  tennis, TT    priced (snapshot "lines", kept from Oct 2026), and ticket legs
                booked before RAW_TICKETS_UNTIL (whose chances were uncorrected)

    python calibration.py          (GitHub Actions: "Train model", nightly)
"""

import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional, Tuple

REPORT_KEY = "betiq:calibration"
DAYS = 120
SHRINK = {"sport": 300, "family": 150, "market": 100}
HOLDOUT = 0.3
MIN_TEST = 40
MIN_DAYS = 6
LO, HI = 0.5, 0.995
B_RANGE = (0.3, 2.5)
SPORTS = ("football", "basketball", "tennis", "table_tennis")
# Ticket legs before this carry the optimizer's raw chances (basketball and
# racket had no correction then); later ones are already corrected
RAW_TICKETS_UNTIL = "2026-10-12"

Map = Tuple[float, float]
_maps: Dict[str, Map] = {}          # "sport|market" → map


def _logit(p: float) -> float:
    p = min(HI, max(1 - HI, p))
    return math.log(p / (1 - p))


def _sig(x: float) -> float:
    return 1 / (1 + math.exp(-x)) if x > -40 else 0.0


def family(sport: str, market: str) -> str:
    """Markets that behave alike: the same kind of bet over another part of
    the game (bb_h2_handicap → bb_handicap; rk_s1_total → rk_total)."""
    m = market
    if sport == "football":
        if m in ("1x2", "double_chance", "dnb", "handicap", "dc_goals"):
            return "result"
        if m in ("goals_ou", "home_goals_ou", "away_goals_ou", "btts", "clean_sheet", "win_to_nil"):
            return "goals"
        if "corners" in m:
            return "corners"
        if "cards" in m:
            return "cards"
        if "shots" in m or "sot" in m:
            return "shots"
        return m
    m = re.sub(r"_(h1|h2|q[1-4]|s[1-5])(?=_)", "", m)
    m = re.sub(r"_player_\w+$", "_player", m)
    m = m.replace("_home_", "_team_").replace("_away_", "_team_")
    if m.endswith(("_home_total", "_away_total")):
        m = m.rsplit("_", 2)[0] + "_team_total"
    return m


def key(sport: str, market: str) -> str:
    return f"{sport}|{market}"


def apply(sport: str, market: str, p: float, maps: Optional[Dict[str, Map]] = None) -> float:
    """Our chance for a pick, through its market's live map (unchanged without one)."""
    m = (_maps if maps is None else maps).get(key(sport, market))
    if not m or not LO <= p < HI:
        return p
    return min(0.99, max(0.01, _sig(m[0] + m[1] * _logit(p))))


def set_maps(maps: Dict[str, Map]) -> None:
    global _maps
    _maps = {k: (float(v[0]), float(v[1])) for k, v in (maps or {}).items()}


def maps() -> Dict[str, Map]:
    return dict(_maps)


# ── fitting ──────────────────────────────────────────────────────────────

def fit(rows: List[Tuple[float, bool]], prior: Map = (0.0, 1.0), ridge: float = 1.0) -> Map:
    """Platt map (a, b) by Newton's method, pulled a little towards `prior`."""
    a, b = prior
    xs = [_logit(p) for p, _ in rows]
    ys = [1.0 if w else 0.0 for _, w in rows]
    for _ in range(50):
        ga, gb = ridge * (a - prior[0]), ridge * (b - prior[1])
        haa = hbb = ridge
        hab = 0.0
        for x, y in zip(xs, ys):
            q = _sig(a + b * x)
            e, w = q - y, q * (1 - q)
            ga += e
            gb += e * x
            haa += w
            hab += w * x
            hbb += w * x * x
        det = haa * hbb - hab * hab
        if det <= 0:
            break
        da, db = (hbb * ga - hab * gb) / det, (haa * gb - hab * ga) / det
        a, b = a - da, b - db
        if abs(da) < 1e-7 and abs(db) < 1e-7:
            break
    return a, min(B_RANGE[1], max(B_RANGE[0], b))


def _shrunk(own: Map, parent: Map, n: int, k: int) -> Map:
    w = n / (n + k)
    return parent[0] + (own[0] - parent[0]) * w, parent[1] + (own[1] - parent[1]) * w


def _group(rows: Iterable[Dict], how: Callable[[Dict], str]) -> Dict[str, List[Dict]]:
    out: Dict[str, List[Dict]] = {}
    for r in rows:
        out.setdefault(how(r), []).append(r)
    return out


def fit_tree(rows: List[Dict]) -> Dict[str, Dict[str, Map]]:
    """Maps at every level: {"all": {"": m}, "sport": {s: m}, "family": {s|f: m}, "market": {s|m: m}}."""
    pairs = lambda rs: [(r["prob"], r["won"]) for r in rs]
    top = fit(pairs(rows)) if rows else (0.0, 1.0)
    tree: Dict[str, Dict[str, Map]] = {"all": {"": top}, "sport": {}, "family": {}, "market": {}}
    for s, rs in _group(rows, lambda r: r["sport"]).items():
        tree["sport"][s] = _shrunk(fit(pairs(rs), top), top, len(rs), SHRINK["sport"])
    for f, rs in _group(rows, lambda r: key(r["sport"], family(r["sport"], r["market"]))).items():
        parent = tree["sport"][f.split("|", 1)[0]]
        tree["family"][f] = _shrunk(fit(pairs(rs), parent), parent, len(rs), SHRINK["family"])
    for m, rs in _group(rows, lambda r: key(r["sport"], r["market"])).items():
        s, mk = m.split("|", 1)
        parent = tree["family"][key(s, family(s, mk))]
        tree["market"][m] = _shrunk(fit(pairs(rs), parent), parent, len(rs), SHRINK["market"])
    return tree


def _ll(p: float, won: bool) -> float:
    p = min(0.995, max(0.005, p))
    return -math.log(p if won else 1 - p)


def score(rows: List[Dict], how: Callable[[Dict], float]) -> Dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    q = [how(r) for r in rows]
    won = [r["won"] for r in rows]
    return {"n": n, "hit": round(sum(won) / n, 4), "said": round(sum(q) / n, 4),
            "log_loss": round(sum(_ll(p, w) for p, w in zip(q, won)) / n, 5),
            "brier": round(sum((p - w) ** 2 for p, w in zip(q, won)) / n, 5)}


def _z(rows: List[Dict], how: Callable[[Dict], float]) -> float:
    d = [_ll(how(r), r["won"]) - _ll(r["prob"], r["won"]) for r in rows]
    if len(d) < 2:
        return 0.0
    mean = sum(d) / len(d)
    sd = math.sqrt(sum((x - mean) ** 2 for x in d) / (len(d) - 1))
    return round(mean / (sd / math.sqrt(len(d))), 2) if sd > 0 else 0.0


def bands(rows: List[Dict], how: Callable[[Dict], float]) -> List[Dict]:
    out = []
    for lo, hi in ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0)):
        rs = [r for r in rows if lo <= how(r) < hi]
        if rs:
            out.append({"band": f"{int(lo * 100)}–{int(hi * 100)}%", **score(rs, how)})
    return out


def check(rows: List[Dict]) -> Dict:
    """Fit on the earlier days, judge on the later ones, then refit the
    chosen maps on every day."""
    rows = [r for r in rows if LO <= r["prob"] < HI and r.get("sport") in SPORTS]
    days = sorted({r["date"] for r in rows})
    rep: Dict = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "picks": len(rows),
                 "days": len(days), "first_day": days[0] if days else None, "last_day": days[-1] if days else None,
                 "min_test": MIN_TEST, "markets": {}, "maps": {}, "sports": {}}
    if len(days) < MIN_DAYS:
        rep["note"] = "too few days of settled picks"
        return rep
    cut = days[int(len(days) * (1 - HOLDOUT))]
    train = [r for r in rows if r["date"] < cut]
    test = [r for r in rows if r["date"] >= cut]
    tree = fit_tree(train)
    mapped = lambda r: apply(r["sport"], r["market"], r["prob"], tree["market"])
    raw = lambda r: r["prob"]

    def better(rs: List[Dict]) -> Optional[bool]:
        if len(rs) < MIN_TEST:
            return None
        return score(rs, mapped)["log_loss"] < score(rs, raw)["log_loss"]
    test_market = _group(test, lambda r: key(r["sport"], r["market"]))
    test_family = _group(test, lambda r: key(r["sport"], family(r["sport"], r["market"])))
    test_sport = _group(test, lambda r: r["sport"])
    train_n = {k: len(v) for k, v in _group(train, lambda r: key(r["sport"], r["market"])).items()}
    chosen: Dict[str, str] = {}
    for mk in sorted(set(train_n) | set(test_market)):
        s, m = mk.split("|", 1)
        f = key(s, family(s, m))
        verdict, basis = better(test_market.get(mk, [])), "market"
        if verdict is None:
            verdict, basis = better(test_family.get(f, [])), "family"
        if verdict is None:
            verdict, basis = better(test_sport.get(s, [])), "sport"
        use = bool(verdict) and mk in tree["market"]
        chosen[mk] = "corrected" if use else "raw"
        rs = test_market.get(mk, [])
        rep["markets"][mk] = {"sport": s, "market": m, "family": f.split("|", 1)[1], "train": train_n.get(mk, 0),
                              "decided_on": basis if verdict is not None else "too few later picks",
                              "choice": chosen[mk], "raw": score(rs, raw), "corrected": score(rs, mapped)}
    live = lambda r: mapped(r) if chosen.get(key(r["sport"], r["market"])) == "corrected" else r["prob"]
    for s, rs in test_sport.items():
        rep["sports"][s] = {"raw": score(rs, raw), "live": score(rs, live), "vs_raw_z": _z(rs, live),
                            "bands_raw": bands(rs, raw), "bands_live": bands(rs, live)}
    rep["overall"] = {"raw": score(test, raw), "live": score(test, live), "vs_raw_z": _z(test, live),
                      "bands_raw": bands(test, raw), "bands_live": bands(test, live)}
    rep.update({"cut": cut, "train_picks": len(train), "test_picks": len(test)})
    full = fit_tree(rows)["market"]
    for mk, c in chosen.items():
        if c == "corrected" and mk in full:
            rep["maps"][mk] = [round(v, 4) for v in full[mk]]
    return rep


# ── settled picks (Redis) ────────────────────────────────────────────────

def _football_rows(e: Dict, day: str) -> List[Dict]:
    import market_accuracy
    if (e.get("result") or {}).get("status") != "finished":
        return []
    return [{"sport": "football", "market": m, "code": c, "prob": float(p), "won": bool(w),
             "date": e.get("date") or day, "match": f"{e.get('home')}|{e.get('away')}|{e.get('date') or day}"}
            for m, c, p, w in market_accuracy.settle(e) if isinstance(p, (int, float))]


_GRADE_MARKET = {"basketball": {"tip": "bb_winner", "points": "bb_total"},
                 "tennis": {"tip": "rk_winner", "games": "rk_total_games"},
                 "table_tennis": {"tip": "rk_winner", "games": "rk_total_games"}}


def _other_rows(sport: str, e: Dict, day: str) -> List[Dict]:
    """A finished basketball/racket match's settled picks: every priced line
    kept with it ("lines"), else its graded tip/total/best picks."""
    res, p = e.get("result") or {}, e.get("pred") or {}
    if res.get("status") != "finished":
        return []
    match = f"{e.get('home')}|{e.get('away')}|{e.get('date') or day}"
    base = {"sport": sport, "date": e.get("date") or day, "match": match}
    out = []
    if isinstance(p.get("lines"), list) and p["lines"]:
        settle = _settler(sport)
        st = _settled(sport, res)
        for market, code, prob in p["lines"]:
            v = settle(market, code, st)
            if v in ("won", "lost") and isinstance(prob, (int, float)):
                out.append({**base, "market": market, "code": code, "prob": float(prob), "won": v == "won"})
        if out:
            return out
    for g, v in (e.get("grades") or {}).items():
        prob = v.get("prob")
        market = _GRADE_MARKET.get(sport, {}).get(g) or ((p.get("best") or {}).get("market") if g == "best" else None)
        code = (p.get("best") or {}).get("code") if g == "best" else g
        if market and isinstance(prob, (int, float)) and v.get("verdict") in ("won", "lost"):
            out.append({**base, "market": market, "code": str(code), "prob": float(prob), "won": v["verdict"] == "won"})
    return out


def _settler(sport: str):
    if sport == "basketball":
        import basketball_markets as bmk
        return bmk.settle
    import racket_markets as rkm
    return rkm.settle


def _settled(sport: str, res: Dict) -> Dict:
    if sport == "basketball":
        import basketball_matchday as bmd
        return bmd._settle_result(res)
    import racket_matchday as rmd
    return rmd._settled(res)


def _ticket_rows(r) -> List[Dict]:
    """Basketball and racket ticket legs from before RAW_TICKETS_UNTIL (raw chances)."""
    out = []
    for k in r.scan_iter(match="betiq:user:*:tickets", count=500):
        try:
            tickets = json.loads(r.get(k) or "[]")
        except ValueError:
            continue
        for t in tickets:
            if str(t.get("created_at") or "") >= RAW_TICKETS_UNTIL:
                continue
            for leg in t.get("legs") or []:
                m = str(leg.get("market") or "")
                sport = "basketball" if m.startswith("bb_") else (leg.get("sport") if m.startswith("rk_") else None)
                if sport == "racket" or (m.startswith("rk_") and sport not in ("tennis", "table_tennis")):
                    sport = "tennis"
                if sport not in ("basketball", "tennis", "table_tennis") or "_player_" in m:
                    continue
                if leg.get("status") in ("won", "lost") and isinstance(leg.get("prob"), (int, float)):
                    out.append({"sport": sport, "market": m, "code": str(leg.get("code")), "prob": float(leg["prob"]),
                                "won": leg["status"] == "won", "date": str(leg.get("date") or t.get("created_at"))[:10],
                                "match": f"{leg.get('home')}|{leg.get('away')}|{leg.get('date')}"})
    return out


def dedupe(rows: List[Dict]) -> List[Dict]:
    """One row per match, market and line (a pick on many tickets counts once)."""
    seen, out = set(), []
    for r in rows:
        k = (r["sport"], r.get("match"), r["market"], r.get("code"))
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def load_rows(r, days: int = DAYS, today: Optional[date] = None) -> List[Dict]:
    today = today or datetime.now(timezone.utc).date()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(1, days + 1)]
    import basketball_matchday as bmd
    import racket_matchday as rmd
    sources = [("football", lambda d: f"betiq:md:{d}"), ("basketball", lambda d: bmd.KEY.format(d)),
               ("tennis", lambda d: rmd.key("tennis", d)), ("table_tennis", lambda d: rmd.key("table_tennis", d))]
    rows: List[Dict] = []
    for sport, key_of in sources:
        for i in range(0, len(dates), 30):
            chunk = dates[i:i + 30]
            for d, raw in zip(chunk, r.mget([key_of(x) for x in chunk])):
                if not raw:
                    continue
                try:
                    day = json.loads(raw)
                except ValueError:
                    continue
                for e in day.values():
                    try:
                        rows += _football_rows(e, d) if sport == "football" else _other_rows(sport, e, d)
                    except Exception:
                        continue
    rows += _ticket_rows(r)
    return dedupe(rows)


def load(r) -> Dict[str, Map]:
    """The stored live maps (the server calls this)."""
    try:
        raw = r.get(REPORT_KEY)
        return {k: tuple(v) for k, v in ((json.loads(raw) or {}).get("maps") or {}).items()} if raw else {}
    except Exception:
        return {}


def _pct(x) -> str:
    return "—" if x is None else f"{100 * x:.1f}%"


def print_report(rep: Dict) -> None:
    print(f"{rep['picks']} settled picks over {rep['days']} days ({rep.get('first_day')} → {rep.get('last_day')})")
    if "overall" not in rep:
        print(rep.get("note", ""))
        return
    print(f"fitted on {rep['train_picks']} picks before {rep['cut']}, judged on {rep['test_picks']} after\n")
    o = rep["overall"]
    for name, v in (("raw", o["raw"]), ("corrected", o["live"])):
        print(f"  {name:<10} hit {_pct(v['hit'])} said {_pct(v['said'])} log loss {v['log_loss']} brier {v['brier']}")
    print(f"  z (corrected vs raw log loss, below −2 is a clear improvement): {o['vs_raw_z']}")
    print("\n  by what we said (later days):  raw → corrected")
    for a, b in zip(o["bands_raw"], o["bands_live"] + [{}] * 5):
        print(f"    {a['band']:<8} n {a['n']:>5} hit {_pct(a['hit'])} said {_pct(a['said'])}   |   "
              f"{b.get('band', ''):<8} n {b.get('n', 0):>5} hit {_pct(b.get('hit'))} said {_pct(b.get('said'))}")
    for s, v in rep["sports"].items():
        print(f"\n{s}: raw hit {_pct(v['raw']['hit'])} said {_pct(v['raw']['said'])} → corrected said "
              f"{_pct(v['live']['said'])} · log loss {v['raw']['log_loss']} → {v['live']['log_loss']} (z {v['vs_raw_z']})")
    print("\nBy market (later days): picks, hit rate, what we said raw → corrected, decision")
    for mk, v in sorted(rep["markets"].items(), key=lambda kv: -kv[1]["raw"].get("n", 0)):
        r_, c_ = v["raw"], v["corrected"]
        print(f"  {mk:<34} n {r_.get('n', 0):>5} hit {_pct(r_.get('hit'))} said {_pct(r_.get('said'))} → "
              f"{_pct(c_.get('said'))} · ll {r_.get('log_loss', '—')} → {c_.get('log_loss', '—')} · "
              f"{v['choice']} ({v['decided_on']})")
    print(f"\n{len(rep['maps'])} markets corrected live")


def main() -> None:
    import model_store
    r = model_store._client()
    if r is None:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    rep = check(load_rows(r))
    print_report(rep)
    r.set(REPORT_KEY, json.dumps(rep))


if __name__ == "__main__":
    main()
