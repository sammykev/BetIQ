"""
How much weight do our basketball ratings deserve against SportyBet's lines?
Every prediction mixes our expected margin and total with the market's
(basketball_predictions.MODEL_WEIGHT, our share):

    margin = w * ours + (1 - w) * market's      (and the same for the total)

The match-day store keeps both before mixing (basketball_matchday.snapshot,
"lines"); this scores each weight on settled games that ended in regulation
by the squared error of the margin and of the total (lower is better), with
the weight chosen on the earlier days scored on the later ones.

    python check_basketball_blend.py   (GitHub Actions: "Basketball data", job basketball_blend; nightly)
"""

import json
import math
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

REPORT_KEY = "betiq:basketball_blend_check"
DAYS = 120
WEIGHTS = (0.0, 0.1, 0.2, 0.3, 0.35, 0.4, 0.5, 0.6, 0.8, 1.0)
MIN_GAMES = 100


def row(e: Dict) -> Optional[Dict]:
    res, p = e.get("result") or {}, e.get("pred") or {}
    lines = p.get("lines") or {}
    score = res.get("score")
    if res.get("status") != "finished" or res.get("ot") or not isinstance(score, list) or len(score) != 2:
        return None
    if not all(isinstance(lines.get(k), (int, float)) for k in ("model_margin", "market_margin")):
        return None
    hs, as_ = float(score[0]), float(score[1])
    out = {"date": e.get("date") or "", "league": e.get("league_name") or e.get("league") or "?",
           "margin": (lines["model_margin"], lines["market_margin"], hs - as_)}
    if all(isinstance(lines.get(k), (int, float)) for k in ("model_total", "market_total")):
        out["total"] = (lines["model_total"], lines["market_total"], hs + as_)
    return out


def _errors(rows: List[Dict], what: str, w: float) -> List[float]:
    return [(w * r[what][0] + (1 - w) * r[what][1] - r[what][2]) ** 2 for r in rows if what in r]


def score(rows: List[Dict], what: str, w: float, base: Optional[List[float]] = None) -> Dict:
    e = _errors(rows, what, w)
    n = len(e)
    out = {"n": n, "rmse": round(math.sqrt(sum(e) / n), 3)}
    if base is not None and n > 1:
        d = [a - b for a, b in zip(e, base)]
        mean = sum(d) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in d) / (n - 1))
        out["vs_market_z"] = round(mean / (sd / math.sqrt(n)), 2) if sd > 0 else 0.0
    return out


def check(rows: List[Dict], what: str) -> Dict:
    rows = sorted((r for r in rows if what in r), key=lambda r: r["date"])
    if len(rows) < MIN_GAMES:
        return {"n": len(rows), "note": "too few settled games with both lines kept"}
    base = _errors(rows, what, 0.0)
    grid = {f"w{w:g}": score(rows, what, w, base) for w in WEIGHTS}
    best = min(grid, key=lambda k: grid[k]["rmse"])
    days = sorted({r["date"] for r in rows})
    cut = days[len(days) // 2]
    early, late = [r for r in rows if r["date"] < cut], [r for r in rows if r["date"] >= cut]
    out = {"n": len(rows), "days": len(days), "grid": grid, "best": best}
    if len(early) >= 30 and len(late) >= 30:
        chosen = min(WEIGHTS, key=lambda w: score(early, what, w)["rmse"])
        out["holdout"] = {"cut": cut, "chosen_on_earlier": chosen,
                          "later": {f"w{w:g}": score(late, what, w)["rmse"] for w in sorted({chosen, 0.35, 0.0, 1.0})}}
    return out


def run(rows: List[Dict]) -> Dict:
    import basketball_predictions as bp
    report = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "in_use": bp.MODEL_WEIGHT, "checks": {}}
    for what in ("margin", "total"):
        got = check(rows, what)
        report["checks"][what] = got
        print(f"\n{what}: {got['n']} games" + (f" · {got['note']}" if "note" in got else
                                                f" over {got['days']} days · best {got['best']} (w = our share; "
                                                f"in use w{bp.MODEL_WEIGHT:g})"))
        for k, v in got.get("grid", {}).items():
            print(f"  {k:<5} {json.dumps(v)}")
        if "holdout" in got:
            h = got["holdout"]
            print(f"  chosen on days before {h['cut']}: w{h['chosen_on_earlier']:g} → later days {json.dumps(h['later'])}")
    return report


def load(r, days: int = DAYS, today: Optional[date] = None) -> List[Dict]:
    import basketball_matchday as bmd
    today = today or datetime.now(timezone.utc).date()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(1, days + 1)]
    rows: List[Dict] = []
    for i in range(0, len(dates), 30):
        for raw in r.mget([bmd.KEY.format(d) for d in dates[i:i + 30]]):
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
    print(f"{len(rows)} settled basketball games with our lines and the market's kept")
    r.set(REPORT_KEY, json.dumps(run(rows)))


if __name__ == "__main__":
    main()
