"""
Which of our predictions come in more often than we said, and which make
money at SportyBet's prices: every settled pick of the last DAYS days, all
four sports, wins and losses alike, grouped many ways.

Looking only at the picks that won can't tell what to focus on (a group with
many winners may simply have many picks, or be priced at 1.10). So each group
is measured against two yardsticks:

- the model's own chance: hit rate minus the average chance we gave
  ("lift"; positive = we're better there than we think), with a z-score so
  small groups don't pass for findings;
- the odds, where SportyBet's price was kept: profit per unit staked (ROI)
  and its z-score (a bet at decimal odds o is a coin with p = 1/o if the
  price were fair).

Groups: sport × market, confidence band, league, odds band, our pick vs the
market's favourite, our edge over the price, home/away/draw side, rated
players or teams vs market-only, kick-off hour, weekday.

    python insights.py   # GitHub Actions ("Basketball data", job insights)
"""

import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

KEY = "betiq:insights"
DAYS = 120
MIN_N = 60                       # a group needs this many picks to be listed among the findings
BANDS = ((0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01))
ODDS_BANDS = ((1.0, 1.25), (1.25, 1.5), (1.5, 1.8), (1.8, 2.2), (2.2, 3.0), (3.0, 100.0))
EDGE_BANDS = ((-1.0, -0.05), (-0.05, 0.0), (0.0, 0.05), (0.05, 0.10), (0.10, 10.0))


def _band(x: Optional[float], bands) -> Optional[str]:
    if not isinstance(x, (int, float)):
        return None
    for lo, hi in bands:
        if lo <= x < hi:
            return f"{lo:g}–{min(hi, 99):g}"
    return None


def _when(e: Dict) -> Dict[str, Any]:
    try:
        ko = datetime.strptime(f"{e['date']} {e.get('time') or '12:00'}", "%Y-%m-%d %H:%M")
    except (KeyError, ValueError):
        return {}
    return {"weekday": ko.strftime("%a"), "hour": f"{ko.hour:02d}h UTC"}


def _row(sport: str, e: Dict, market: str, prob: Any, won: bool, odds: Any = None,
         side: Optional[str] = None, favourite: Optional[bool] = None, rated: Optional[bool] = None) -> Optional[Dict]:
    if not isinstance(prob, (int, float)) or not 0 < prob < 1:
        return None
    r = {"sport": sport, "market": market, "league": e.get("league_name") or e.get("league") or "?",
         "prob": float(prob), "won": bool(won), **_when(e)}
    if isinstance(odds, (int, float)) and odds > 1.0:
        r["odds"] = float(odds)
        r["edge"] = prob * odds - 1
    if side:
        r["side"] = side
    if favourite is not None:
        r["favourite"] = favourite
    if rated is not None:
        r["rated"] = rated
    return r


# ── settled picks of each sport ──────────────────────────────────────────

def football_rows(e: Dict) -> List[Dict]:
    """Every outcome the model rated ≥ 50% (market_accuracy), with SportyBet's
    price where it was kept (price_book), plus the main tip."""
    import market_accuracy
    import price_book
    res = e.get("result") or {}
    if res.get("status") != "finished":
        return []
    p = e.get("pred") or {}
    priced = {(b["market"], b["code"]): b["odds"] for b in price_book.settle(e)}
    rows = []
    for market, code, prob, won in market_accuracy.settle(e):
        rows.append(_row("football", e, market, prob, won, priced.get((market, code))))
    tip = (e.get("grades") or {}).get("tip")
    if tip and tip.get("verdict") in ("won", "lost"):
        code = p.get("tip_code") or ""
        odds = {"1": p.get("odds_home"), "X": p.get("odds_draw"), "2": p.get("odds_away")}.get(code)
        probs = {"1": p.get("odds_home"), "X": p.get("odds_draw"), "2": p.get("odds_away")}
        fav = min((k for k, v in probs.items() if isinstance(v, (int, float))), key=lambda k: probs[k], default=None)
        rows.append(_row("football", e, "tip", tip.get("prob"), tip["verdict"] == "won", odds,
                         side={"1": "home", "X": "draw", "2": "away"}.get(code, code or None),
                         favourite=(fav == code) if fav and code in probs else None))
    return [r for r in rows if r]


def side_rows(sport: str, e: Dict) -> List[Dict]:
    """Basketball, tennis, table tennis: the tip (winner), the total and the best line."""
    g = e.get("grades") or {}
    p = e.get("pred") or {}
    rows = []
    rated = bool(p.get("rated")) if "rated" in p else (p.get("model") in ("ratings+market", "model", "blend"))
    tip = g.get("tip")
    if tip and tip.get("verdict") in ("won", "lost"):
        code = p.get("tip_code")
        odds = p.get("odds_home") if code == "1" else p.get("odds_away") if code == "2" else None
        other = p.get("odds_away") if code == "1" else p.get("odds_home") if code == "2" else None
        fav = (odds <= other) if isinstance(odds, (int, float)) and isinstance(other, (int, float)) else None
        rows.append(_row(sport, e, "winner", tip.get("prob"), tip["verdict"] == "won", odds,
                         side={"1": "home", "2": "away"}.get(code or ""), favourite=fav, rated=rated))
    for k in ("points", "games"):
        t = g.get(k)
        if t and t.get("verdict") in ("won", "lost"):
            rows.append(_row(sport, e, "total", t.get("prob"), t["verdict"] == "won", rated=rated))
    b = g.get("best")
    if b and b.get("verdict") in ("won", "lost"):
        rows.append(_row(sport, e, "best line", b.get("prob"), b["verdict"] == "won", b.get("odds"), rated=rated))
    return [r for r in rows if r]


# ── grouping ─────────────────────────────────────────────────────────────

def stats(rows: List[Dict]) -> Dict[str, Any]:
    n = len(rows)
    won = sum(r["won"] for r in rows)
    said = sum(r["prob"] for r in rows)
    var = sum(r["prob"] * (1 - r["prob"]) for r in rows)
    out: Dict[str, Any] = {"n": n, "hit": round(won / n, 4), "said": round(said / n, 4),
                           "lift": round((won - said) / n, 4),
                           "z": round((won - said) / math.sqrt(var), 2) if var > 0 else 0.0}
    priced = [r for r in rows if "odds" in r]
    if priced:
        profit = sum(r["odds"] - 1 if r["won"] else -1.0 for r in priced)
        # Under fair odds each bet's profit has mean 0 and variance odds - 1
        sd = math.sqrt(sum(r["odds"] - 1 for r in priced))
        out.update({"bets": len(priced), "roi": round(profit / len(priced), 4),
                    "avg_odds": round(sum(r["odds"] for r in priced) / len(priced), 3),
                    "roi_z": round(profit / sd, 2) if sd > 0 else 0.0})
    return out


DIMENSIONS = {
    "market": lambda r: f"{r['sport']} · {r['market']}",
    "confidence": lambda r: f"{r['sport']} · {_band(r['prob'], BANDS)}",
    "league": lambda r: f"{r['sport']} · {r['league']}",
    "odds": lambda r: f"{r['sport']} · odds {_band(r.get('odds'), ODDS_BANDS)}" if "odds" in r else None,
    "edge": lambda r: f"{r['sport']} · edge {_band(r.get('edge'), EDGE_BANDS)}" if "edge" in r else None,
    "favourite": lambda r: (f"{r['sport']} · {r['market']} · {'with' if r['favourite'] else 'against'} the market's favourite"
                            if "favourite" in r else None),
    "side": lambda r: f"{r['sport']} · {r['market']} · {r['side']}" if r.get("side") else None,
    "rated": lambda r: (f"{r['sport']} · {'rated by us' if r['rated'] else 'market only'}" if "rated" in r else None),
    "hour": lambda r: f"{r['sport']} · {r.get('hour')}" if r.get("hour") else None,
    "weekday": lambda r: f"{r['sport']} · {r.get('weekday')}" if r.get("weekday") else None,
}


def analyse(rows: List[Dict]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "picks": len(rows), "overall": {}, "groups": {}}
    by_sport = defaultdict(list)
    for r in rows:
        by_sport[r["sport"]].append(r)
    out["overall"] = {s: stats(rs) for s, rs in by_sport.items()}
    for dim, key in DIMENSIONS.items():
        groups = defaultdict(list)
        for r in rows:
            k = key(r)
            if k:
                groups[k].append(r)
        out["groups"][dim] = {k: stats(v) for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))}
    out["findings"] = findings(out["groups"])
    return out


def findings(groups: Dict[str, Dict[str, Dict]]) -> Dict[str, List[Dict]]:
    """The groups worth acting on: big enough, and clearly better (or worse)
    than the model thinks, or profitable (or losing) at the odds."""
    best, worst, profitable, losing = [], [], [], []
    for dim, gs in groups.items():
        for name, s in gs.items():
            if s["n"] < MIN_N:
                continue
            item = {"group": name, "dimension": dim, **s}
            if s["z"] >= 2:
                best.append(item)
            elif s["z"] <= -2:
                worst.append(item)
            if s.get("bets", 0) >= MIN_N and s.get("roi_z", 0) >= 2:
                profitable.append(item)
            elif s.get("bets", 0) >= MIN_N and s.get("roi_z", 0) <= -2:
                losing.append(item)
    return {"better_than_we_say": sorted(best, key=lambda x: -x["z"])[:25],
            "worse_than_we_say": sorted(worst, key=lambda x: x["z"])[:25],
            "profitable_at_the_odds": sorted(profitable, key=lambda x: -x["roi_z"])[:25],
            "losing_at_the_odds": sorted(losing, key=lambda x: x["roi_z"])[:25]}


# ── loading (Redis) ──────────────────────────────────────────────────────

def load_rows(r, days: int = DAYS, today: Optional[date] = None) -> List[Dict]:
    import basketball_matchday as bbmd
    import racket_matchday as rmd
    today = today or datetime.now(timezone.utc).date()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(1, days + 1)]
    rows: List[Dict] = []

    def days_of(keys: List[str]) -> Iterable[Dict]:
        for i in range(0, len(keys), 30):
            for raw in r.mget(keys[i:i + 30]):
                if raw:
                    try:
                        yield json.loads(raw)
                    except Exception:
                        continue

    for day in days_of([f"betiq:md:{d}" for d in dates]):
        for e in day.values():
            try:
                rows += football_rows(e)
            except Exception:
                continue
    for day in days_of([bbmd.KEY.format(d) for d in dates]):
        for e in day.values():
            rows += side_rows("basketball", e)
    for sport in ("tennis", "table_tennis"):
        for day in days_of([rmd.key(sport, d) for d in dates]):
            for e in day.values():
                rows += side_rows(sport, e)
    return rows


def print_report(rep: Dict[str, Any]) -> None:
    print(f"{rep['picks']} settled picks\n\nOverall:")
    for s, v in rep["overall"].items():
        print(f"  {s}: {json.dumps(v)}")
    for dim in ("market", "confidence", "favourite", "side", "rated", "odds", "edge"):
        print(f"\nBy {dim}:")
        for k, v in list(rep["groups"].get(dim, {}).items())[:40]:
            if v["n"] >= 20:
                print(f"  {k}: n {v['n']} hit {v['hit']:.1%} said {v['said']:.1%} lift {v['lift']:+.1%} z {v['z']:+.1f}"
                      + (f" | {v['bets']} priced, avg odds {v['avg_odds']}, ROI {v['roi']:+.1%} z {v['roi_z']:+.1f}" if "bets" in v else ""))
    for k, items in rep["findings"].items():
        print(f"\n== {k} ==")
        for x in items:
            print(f"  [{x['dimension']}] {x['group']}: n {x['n']} hit {x['hit']:.1%} said {x['said']:.1%} z {x['z']:+.1f}"
                  + (f" | ROI {x['roi']:+.1%} on {x['bets']} at avg {x['avg_odds']} (z {x['roi_z']:+.1f})" if "bets" in x else ""))


def main() -> None:
    import model_store
    r = model_store._client()
    if r is None:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    rows = load_rows(r)
    rep = analyse(rows)
    print_report(rep)
    r.set(KEY, json.dumps(rep), ex=14 * 86400)


if __name__ == "__main__":
    main()
