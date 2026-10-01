"""
Which way of taking SportyBet's margin out of its winner prices
(fair_odds.py), and how much weight our own ratings deserve against them
(racket_predictions.MODEL_WEIGHT), judged on every settled tennis, table
tennis and basketball match in the match-day store.

For each match the store keeps the winner prices and the chance we gave
(p_home). Where we rated both players, that chance was

    w0 * Elo's chance + (1 - w0) * SportyBet's (proportional de-vig)

so Elo's own chance is recovered from it with the weight in use then (w0).
Every method × weight is then scored on what happened: log loss (lower is
better), Brier score, and the chance given to the market's favourite
against how often it won. Each is compared with what the site does now,
match by match (the difference's z: below -2 is a clear improvement).

Basketball's winner chance comes from margins, not this blend, so only the
de-vig methods are compared on its winner prices.

    python check_fair_odds.py     (GitHub Actions: "Basketball data", job fair_odds)
"""

import json
import math
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

import fair_odds

REPORT_KEY = "betiq:fair_odds_check"
DAYS = 120
WEIGHTS = (0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.35, 0.5)
# The weight on our ratings when each match was priced; table tennis went
# from 0.35 to 0.2 late on 29 Sep 2026 (matches around the change are left out)
CURRENT = {"tennis": 0.35, "table_tennis": 0.2}
TT_CHANGE = ("2026-09-29 21:30", "2026-09-30 00:00")


def w0_of(sport: str, when: str) -> Optional[float]:
    if sport != "table_tennis":
        return CURRENT.get(sport)
    if when < TT_CHANGE[0]:
        return 0.35
    if when < TT_CHANGE[1]:
        return None
    return CURRENT[sport]


def row(sport: str, e: Dict) -> Optional[Dict]:
    """A settled match: the winner prices, Elo's chance (if rated) and who won."""
    p, g = e.get("pred") or {}, e.get("grades") or {}
    tip = g.get("tip") or {}
    oh, oa = p.get("odds_home"), p.get("odds_away")
    if tip.get("verdict") not in ("won", "lost") or p.get("tip_code") not in ("1", "2"):
        return None
    if not all(isinstance(o, (int, float)) and o > 1.0 for o in (oh, oa)):
        return None
    home_won = (p["tip_code"] == "1") == (tip["verdict"] == "won")
    out = {"sport": sport, "odds": (float(oh), float(oa)), "home_won": home_won, "p_elo": None}
    rated = bool(p.get("rated")) if "rated" in p else p.get("model") == "ratings+market"
    w0 = w0_of(sport, f"{e.get('date') or ''} {e.get('time') or ''}")
    if sport != "basketball" and rated:
        if w0 is None:
            return None
        mp = fair_odds.proportional(out["odds"])[0]
        ph = p.get("p_home")
        if not isinstance(ph, (int, float)):
            return None
        pe = (ph - (1 - w0) * mp) / w0
        if not -0.02 <= pe <= 1.02:     # not priced the way assumed: leave it out
            return None
        out["p_elo"] = min(0.995, max(0.005, pe))
    return out


def _ll(p: float, won: bool) -> float:
    p = min(0.995, max(0.005, p))
    return -math.log(p if won else 1 - p)


def chance(r: Dict, method: str, w: float) -> float:
    m = fair_odds.fair(r["odds"], method)[0]
    return m if r["p_elo"] is None else w * r["p_elo"] + (1 - w) * m


def score(rows: List[Dict], method: str, w: float, base: Optional[List[float]] = None) -> Dict:
    lls, brier, fav_said, fav_won = [], 0.0, 0.0, 0
    for r in rows:
        p = chance(r, method, w)
        lls.append(_ll(p, r["home_won"]))
        brier += (p - r["home_won"]) ** 2
        fav_home = r["odds"][0] <= r["odds"][1]
        fav_said += p if fav_home else 1 - p
        fav_won += r["home_won"] == fav_home
    n = len(rows)
    out = {"n": n, "log_loss": round(sum(lls) / n, 5), "brier": round(brier / n, 5),
           "favourite_said": round(fav_said / n, 4), "favourite_won": round(fav_won / n, 4)}
    if base is not None:
        d = [a - b for a, b in zip(lls, base)]
        mean = sum(d) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in d) / max(1, n - 1))
        out["vs_now"] = round(mean, 5)
        out["vs_now_z"] = round(mean / (sd / math.sqrt(n)), 2) if sd > 0 else 0.0
    return out


def check(sport: str, rows: List[Dict]) -> Dict:
    now_w = CURRENT.get(sport, 0.0)
    rated = [r for r in rows if r["p_elo"] is not None]
    out: Dict = {"matches": len(rows), "rated": len(rated)}
    base_all = [_ll(chance(r, "proportional", now_w), r["home_won"]) for r in rows]
    out["all"] = {f"{m} w{w:g}": score(rows, m, w, base_all)
                  for m in fair_odds.METHODS for w in (WEIGHTS if rated else (0.0,))}
    if rated:
        base = [_ll(chance(r, "proportional", now_w), r["home_won"]) for r in rated]
        out["rated_only"] = {f"{m} w{w:g}": score(rated, m, w, base) for m in fair_odds.METHODS for w in WEIGHTS}
        out["elo_alone"] = score(rated, "proportional", 1.0, base)
    best = min(out["all"], key=lambda k: out["all"][k]["log_loss"])
    out["best"] = best
    out["now"] = f"proportional w{now_w:g}" if rated else "proportional w0"
    return out


def run(rows_by_sport: Dict[str, List[Dict]]) -> Dict:
    report = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sports": {}}
    for sport, rows in rows_by_sport.items():
        if len(rows) < 30:
            print(f"{sport}: {len(rows)} settled matches, too few")
            continue
        got = check(sport, rows)
        report["sports"][sport] = got
        print(f"\n{sport}: {got['matches']} settled matches, {got['rated']} rated by us · now: {got['now']} · best: {got['best']}")
        for k, v in sorted(got["all"].items(), key=lambda kv: kv[1]["log_loss"]):
            print(f"  {k:<18} {json.dumps(v)}")
        if "rated_only" in got:
            print("  rated matches only:")
            for k, v in sorted(got["rated_only"].items(), key=lambda kv: kv[1]["log_loss"])[:10]:
                print(f"    {k:<18} {json.dumps(v)}")
            print(f"    Elo alone          {json.dumps(got['elo_alone'])}")
    return report


def load(r, days: int = DAYS, today: Optional[date] = None) -> Dict[str, List[Dict]]:
    import basketball_matchday as bbmd
    import racket_matchday as rmd
    today = today or datetime.now(timezone.utc).date()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(0, days + 1)]

    def days_of(keys: List[str]) -> Iterable[Dict]:
        for i in range(0, len(keys), 30):
            for raw in r.mget(keys[i:i + 30]):
                if raw:
                    try:
                        yield json.loads(raw)
                    except Exception:
                        continue
    out: Dict[str, List[Dict]] = {}
    keys = {"tennis": lambda d: rmd.key("tennis", d), "table_tennis": lambda d: rmd.key("table_tennis", d),
            "basketball": bbmd.KEY.format}
    for sport, key in keys.items():
        rows = []
        for day in days_of([key(d) for d in dates]):
            for e in day.values():
                got = row(sport, e)
                if got:
                    rows.append(got)
        out[sport] = rows
    return out


def main() -> None:
    import model_store
    r = model_store._client()
    if r is None:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    report = run(load(r))
    r.set(REPORT_KEY, json.dumps(report))


if __name__ == "__main__":
    main()
