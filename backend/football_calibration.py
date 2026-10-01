"""
Football chances corrected by how each market's picks have actually come in.

The match-day store keeps every settled pick the model rated ≥ 50%
(market_accuracy.settle: market, code, our chance, won). On 120 days of
them (insights.py, 1 Oct 2026) corners and handicaps came in less often
than we said (total corners 8.9 points under, most corners 19.7), the
80–90% band 3.6 points under, and shots, shots on target and cards more
often (away shots on target 7.3 points over).

Each market gets a map of our chance p to

    q = 1 / (1 + exp(-(a + b * logit p)))           (Platt scaling)

fitted on its settled picks, pulled towards one map for all markets by
SHRINK picks' worth (a market with few picks keeps close to it). Checked
first on time: fitted on the earlier days, scored on the later ones (log
loss, against our raw chances); a market keeps its own map, the shared one,
or none, whichever did best on the later days. The maps are then refitted
on every day and stored (REPORT_KEY), and the server applies them to the
optimizer's and daily slips' chances (optimizer.candidates). The accuracy
records (market_accuracy, price_book) keep the raw chances these maps
are fitted from.

    python football_calibration.py     (GitHub Actions: "Train model", nightly)
"""

import json
import math
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

REPORT_KEY = "betiq:football_calibration"
DAYS = 120
SHRINK = 500           # picks' worth pulling a market's map towards the shared one
HOLDOUT = 0.3          # the later share of days the maps are checked on
MIN_TEST = 100         # a market's own choice needs this many later picks
LO, HI = 0.5, 0.995    # the chances the maps apply to (what they're fitted on)
B_RANGE = (0.3, 2.5)

Map = Tuple[float, float]
_maps: Dict[str, Map] = {}


def _logit(p: float) -> float:
    p = min(HI, max(1 - HI, p))
    return math.log(p / (1 - p))


def _sig(x: float) -> float:
    return 1 / (1 + math.exp(-x)) if x > -40 else 0.0


def apply(market: str, p: float, maps: Optional[Dict[str, Map]] = None) -> float:
    """Our chance for a pick in `market` through its map (unchanged without one)."""
    m = (_maps if maps is None else maps).get(market)
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
    """Platt map (a, b) by Newton's method, with a small pull to `prior`."""
    a, b = prior
    xs = [_logit(p) for p, _ in rows]
    ys = [1.0 if w else 0.0 for _, w in rows]
    for _ in range(50):
        ga = ridge * (a - prior[0])
        gb = ridge * (b - prior[1])
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
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a, b = a - da, b - db
        if abs(da) < 1e-7 and abs(db) < 1e-7:
            break
    return a, min(B_RANGE[1], max(B_RANGE[0], b))


def shrunk(own: Map, shared: Map, n: int) -> Map:
    k = n / (n + SHRINK)
    return shared[0] + (own[0] - shared[0]) * k, shared[1] + (own[1] - shared[1]) * k


def fit_all(rows: List[Dict]) -> Tuple[Map, Dict[str, Map]]:
    """The shared map and each market's (shrunk towards it)."""
    pairs = [(r["prob"], r["won"]) for r in rows]
    shared = fit(pairs)
    by: Dict[str, List[Tuple[float, bool]]] = {}
    for r in rows:
        by.setdefault(r["market"], []).append((r["prob"], r["won"]))
    return shared, {m: shrunk(fit(v, shared), shared, len(v)) for m, v in by.items()}


def _ll(p: float, won: bool) -> float:
    p = min(0.995, max(0.005, p))
    return -math.log(p if won else 1 - p)


def _score(rows: List[Dict], how) -> Dict:
    n = len(rows)
    q = [how(r) for r in rows]
    hit = sum(r["won"] for r in rows)
    return {"n": n, "log_loss": round(sum(_ll(p, r["won"]) for p, r in zip(q, rows)) / n, 5),
            "said": round(sum(q) / n, 4), "hit": round(hit / n, 4)}


def check(rows: List[Dict]) -> Dict:
    """Earlier days fit, later days score; each market's choice and the
    overall effect, then the maps refitted on every day."""
    rows = [r for r in rows if LO <= r["prob"] < HI]
    days = sorted({r["date"] for r in rows})
    cut = days[int(len(days) * (1 - HOLDOUT))] if len(days) >= 10 else None
    report: Dict = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "picks": len(rows),
                    "days": len(days), "markets": {}, "maps": {}}
    if cut is None:
        return report
    train = [r for r in rows if r["date"] < cut]
    test = [r for r in rows if r["date"] >= cut]
    shared, own = fit_all(train)
    choice: Dict[str, str] = {}
    by_test: Dict[str, List[Dict]] = {}
    for r in test:
        by_test.setdefault(r["market"], []).append(r)
    ways = {"raw": lambda r: r["prob"],
            "shared": lambda r: apply("*", r["prob"], {"*": shared}),
            "own": lambda r: apply(r["market"], r["prob"], own) if r["market"] in own else apply("*", r["prob"], {"*": shared})}
    overall = {k: _score(test, f) for k, f in ways.items()}
    best_overall = min(("shared", "own"), key=lambda k: overall[k]["log_loss"])
    for m, rs in sorted(by_test.items(), key=lambda kv: -len(kv[1])):
        s = {k: _score(rs, f) for k, f in ways.items()}
        if len(rs) >= MIN_TEST:
            pick = min(s, key=lambda k: s[k]["log_loss"])
        else:   # too few later picks to judge alone: the better way overall, if it beat raw
            pick = best_overall if overall[best_overall]["log_loss"] < overall["raw"]["log_loss"] else "raw"
        choice[m] = pick
        report["markets"][m] = {"choice": pick, "train": sum(r["market"] == m for r in train), **s}
    chosen = lambda r: ways[choice.get(r["market"], "raw")](r)
    overall["chosen"] = _score(test, chosen)
    d = [_ll(chosen(r), r["won"]) - _ll(r["prob"], r["won"]) for r in test]
    mean = sum(d) / len(d)
    sd = math.sqrt(sum((x - mean) ** 2 for x in d) / max(1, len(d) - 1))
    overall["chosen"]["vs_raw"] = round(mean, 5)
    overall["chosen"]["vs_raw_z"] = round(mean / (sd / math.sqrt(len(d))), 2) if sd > 0 else 0.0
    report.update({"cut": cut, "train_picks": len(train), "test_picks": len(test), "overall": overall})
    # Live maps: refitted on every day, for the markets whose check chose one
    shared_all, own_all = fit_all(rows)
    if overall["chosen"]["log_loss"] < overall["raw"]["log_loss"]:
        for m, pick in choice.items():
            if pick == "own" and m in own_all:
                report["maps"][m] = [round(v, 4) for v in own_all[m]]
            elif pick == "shared":
                report["maps"][m] = [round(v, 4) for v in shared_all]
    report["shared"] = [round(v, 4) for v in shared_all]
    return report


# ── loading (Redis) ──────────────────────────────────────────────────────

def rows_of(e: Dict, day: str) -> List[Dict]:
    import market_accuracy
    if (e.get("result") or {}).get("status") != "finished":
        return []
    return [{"market": m, "code": c, "prob": float(p), "won": bool(w), "date": e.get("date") or day}
            for m, c, p, w in market_accuracy.settle(e) if isinstance(p, (int, float))]


def load_rows(r, days: int = DAYS, today: Optional[date] = None) -> List[Dict]:
    today = today or datetime.now(timezone.utc).date()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(1, days + 1)]
    rows: List[Dict] = []
    for i in range(0, len(dates), 30):
        chunk = dates[i:i + 30]
        for d, raw in zip(chunk, r.mget([f"betiq:md:{d}" for d in chunk])):
            if not raw:
                continue
            try:
                day = json.loads(raw)
            except Exception:
                continue
            for e in day.values():
                try:
                    rows += rows_of(e, d)
                except Exception:
                    continue
    return rows


def load(r) -> Dict[str, Map]:
    """The stored live maps (the server calls this; empty without any)."""
    try:
        raw = r.get(REPORT_KEY)
        return {k: tuple(v) for k, v in ((json.loads(raw) or {}).get("maps") or {}).items()} if raw else {}
    except Exception:
        return {}


def print_report(rep: Dict) -> None:
    print(f"{rep['picks']} settled picks over {rep['days']} days")
    if "overall" not in rep:
        print("too few days to check")
        return
    print(f"fitted on {rep['train_picks']} picks before {rep['cut']}, scored on {rep['test_picks']} after")
    for k, v in rep["overall"].items():
        print(f"  {k:<7} {json.dumps(v)}")
    print("\nBy market (later days): raw → own map / shared map, and the choice")
    for m, v in rep["markets"].items():
        print(f"  {m:<22} n {v['raw']['n']:>5} hit {v['raw']['hit']:.3f} · said raw {v['raw']['said']:.3f} "
              f"own {v['own']['said']:.3f} shared {v['shared']['said']:.3f} · log loss raw {v['raw']['log_loss']:.4f} "
              f"own {v['own']['log_loss']:.4f} shared {v['shared']['log_loss']:.4f} → {v['choice']}")
    print(f"\nLive maps ({len(rep['maps'])}): {json.dumps(rep['maps'])}")


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
