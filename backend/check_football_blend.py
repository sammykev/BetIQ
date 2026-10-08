"""
Does football do better leaning on the market's prices? On every settled
match in the match-day store: our 1X2 chances (and over 2.5 goals) against
the bookmaker's prices for the same match, de-vigged (fair_odds.py), and
blends of the two,

    chance = w * ours + (1 - w) * market's

scored on what happened: log loss (lower is better), Brier score, and how
often the likeliest outcome won. Each blend is compared with ours alone,
match by match (the difference's z: below -2 is a clear improvement). The
weight is also chosen on the earlier days and scored on the later ones, so
the answer isn't fitted to the days it's judged on.

The model already reads the odds as inputs where it has them; this asks
whether its final chances still overrate its own view against the price.

    python check_football_blend.py   (GitHub Actions: "Basketball data", job football_blend)
"""

import json
import math
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

import fair_odds

REPORT_KEY = "betiq:football_blend_check"
DAYS = 120
WEIGHTS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)   # w: our share (1 = ours alone)
OUTCOMES = ("H", "D", "A")


def _prices(p: Dict) -> Dict[Tuple[str, str], float]:
    """SportyBet's prices kept with the match (price_book): {(market, code): odds}."""
    out = {}
    for row in p.get("prices") or []:
        try:
            market, code, _, odds = row
            out[(str(market), str(code))] = float(odds)
        except (TypeError, ValueError):
            continue
    return out


def row(e: Dict) -> Optional[Dict]:
    """A settled match: our chances, the market's prices and what happened."""
    res = e.get("result") or {}
    p = e.get("pred") or {}
    if res.get("status") != "finished" or res.get("aet") or res.get("hg") is None or res.get("ag") is None:
        return None
    hg, ag = int(res["hg"]), int(res["ag"])
    ours = [p.get("p_home"), p.get("p_draw"), p.get("p_away")]
    if not all(isinstance(x, (int, float)) and x > 0 for x in ours):
        return None
    s = sum(ours)
    out: Dict = {"date": e.get("date") or "", "league": e.get("league_name") or e.get("league") or "?",
                 "ours": [x / s for x in ours], "won": "H" if hg > ag else "A" if ag > hg else "D"}
    sb = _prices(p)
    odds = [p.get("odds_home"), p.get("odds_draw"), p.get("odds_away")]
    if not all(isinstance(o, (int, float)) and o > 1.0 for o in odds):
        odds = [sb.get(("1x2", c)) for c in ("1", "X", "2")]
    if all(isinstance(o, (int, float)) and o > 1.0 for o in odds):
        out["odds"] = [float(o) for o in odds]
    over, under = sb.get(("goals_ou", "O25")), sb.get(("goals_ou", "U25"))
    if isinstance(p.get("p_over25"), (int, float)) and over and under and over > 1 and under > 1:
        out["ou"] = {"ours": float(p["p_over25"]), "odds": [over, under], "over": hg + ag > 2.5}
    return out if ("odds" in out or "ou" in out) else None


def _ll(p: float) -> float:
    return -math.log(min(0.995, max(0.005, p)))


def _losses_1x2(rows: List[Dict], w: float, method: str) -> Tuple[List[float], float, int]:
    lls, brier, right = [], 0.0, 0
    for r in rows:
        m = fair_odds.fair(r["odds"], method)
        q = [w * a + (1 - w) * b for a, b in zip(r["ours"], m)]
        k = OUTCOMES.index(r["won"])
        lls.append(_ll(q[k]))
        brier += sum((q[i] - (i == k)) ** 2 for i in range(3))
        right += max(range(3), key=lambda i: q[i]) == k
    return lls, brier, right


def _losses_ou(rows: List[Dict], w: float, method: str) -> Tuple[List[float], float, int]:
    lls, brier, right = [], 0.0, 0
    for r in rows:
        o = r["ou"]
        q = w * o["ours"] + (1 - w) * fair_odds.fair(o["odds"], method)[0]
        lls.append(_ll(q if o["over"] else 1 - q))
        brier += (q - o["over"]) ** 2
        right += (q >= 0.5) == o["over"]
    return lls, brier, right


def score(losses, rows: List[Dict], w: float, method: str, base: Optional[List[float]] = None) -> Dict:
    lls, brier, right = losses(rows, w, method)
    n = len(lls)
    out = {"n": n, "log_loss": round(sum(lls) / n, 5), "brier": round(brier / n, 5), "accuracy": round(right / n, 4)}
    if base is not None:
        d = [a - b for a, b in zip(lls, base)]
        mean = sum(d) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in d) / max(1, n - 1))
        out["vs_ours"] = round(mean, 5)
        out["vs_ours_z"] = round(mean / (sd / math.sqrt(n)), 2) if sd > 0 else 0.0
    return out


def check(rows: List[Dict], losses, method: str = fair_odds.METHOD) -> Dict:
    """Every weight on all the days, and the weight chosen on the earlier half scored on the later."""
    if len(rows) < 30:
        return {"n": len(rows), "note": "too few settled matches"}
    rows = sorted(rows, key=lambda r: r["date"])
    base = losses(rows, 1.0, method)[0]
    grid = {f"w{w:g}": score(losses, rows, w, method, base) for w in WEIGHTS}
    best = min(grid, key=lambda k: grid[k]["log_loss"])
    days = sorted({r["date"] for r in rows})
    cut = days[len(days) // 2]
    early, late = [r for r in rows if r["date"] < cut], [r for r in rows if r["date"] >= cut]
    out = {"n": len(rows), "days": len(days), "grid": grid, "best": best,
           "methods": {m: score(losses, rows, float(best[1:]), m)["log_loss"] for m in fair_odds.METHODS}}
    if len(early) >= 15 and len(late) >= 15:
        chosen = min(WEIGHTS, key=lambda w: score(losses, early, w, method)["log_loss"])
        out["holdout"] = {"cut": cut, "chosen_on_earlier": chosen,
                          "later_ours": score(losses, late, 1.0, method)["log_loss"],
                          "later_blend": score(losses, late, chosen, method, losses(late, 1.0, method)[0])}
    return out


def run(rows: List[Dict]) -> Dict:
    report = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "checks": {}}
    for name, subset, losses in (("1X2", [r for r in rows if "odds" in r], _losses_1x2),
                                 ("over/under 2.5", [r for r in rows if "ou" in r], _losses_ou)):
        got = check(subset, losses)
        report["checks"][name] = got
        print(f"\n{name}: {got.get('n')} settled matches over {got.get('days', '?')} days"
              + (f" · {got['note']}" if "note" in got else f" · best: {got['best']} (w = our share; w1 = ours alone)"))
        for k, v in got.get("grid", {}).items():
            print(f"  {k:<5} {json.dumps(v)}")
        if "methods" in got:
            print(f"  de-vig at the best weight: {json.dumps(got['methods'])}")
        if "holdout" in got:
            h = got["holdout"]
            print(f"  chosen on days before {h['cut']}: w{h['chosen_on_earlier']:g} → on the later days "
                  f"ours {h['later_ours']} vs blend {json.dumps(h['later_blend'])}")
    return report


def load(r, days: int = DAYS, today: Optional[date] = None) -> List[Dict]:
    today = today or datetime.now(timezone.utc).date()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(1, days + 1)]
    rows: List[Dict] = []
    for i in range(0, len(dates), 30):
        for raw in r.mget([f"betiq:md:{d}" for d in dates[i:i + 30]]):
            if not raw:
                continue
            try:
                day = json.loads(raw)
            except ValueError:
                continue
            for e in day.values():
                got = row(e)
                if got:
                    rows.append(got)
    return rows


def main() -> None:
    import model_store
    r = model_store._client()
    if r is None:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    rows = load(r)
    print(f"{len(rows)} settled football matches with prices "
          f"({sum('odds' in x for x in rows)} with 1X2 odds, {sum('ou' in x for x in rows)} with over/under 2.5)")
    report = run(rows)
    r.set(REPORT_KEY, json.dumps(report))


if __name__ == "__main__":
    main()
