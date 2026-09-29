"""
The track record for basketball, tennis and table tennis, in the shape the
History page shows football's (matchday.accuracy): from each sport's
match-day store (basketball_matchday / racket_matchday), where every match
keeps the prediction it had before it started and, once over, how each of
our picks did.

Picks graded per sport (the stores' GRADES):
    basketball         tip (winner), points (main total), best (likeliest line)
    tennis / TT        tip (winner), games (main total), best
"""

from typing import Any, Dict, Iterable, List, Optional

NAMES = {
    "basketball": {"tip": "Our tip (winner)", "points": "Total points", "best": "Best line"},
    "tennis": {"tip": "Our tip (winner)", "games": "Total games", "best": "Best line"},
    "table_tennis": {"tip": "Our tip (winner)", "games": "Total points", "best": "Best line"},
}
BANDS = ((50, 60), (60, 70), (70, 80), (80, 90), (90, 101))


def _winner(e: Dict) -> Optional[bool]:
    """True if the first-named side won the finished match."""
    res = e.get("result") or {}
    s = res.get("score")
    if res.get("status") != "finished" or not s or s[0] == s[1] or res.get("ret"):
        return None
    return s[0] > s[1]


def accuracy(days: Dict[str, Iterable[Dict]], sport: str) -> Dict[str, Any]:
    """{matches, markets, calibration, brier, leagues, daily} over the given
    days ({date: [entries]})."""
    names = NAMES.get(sport, NAMES["tennis"])
    picks: Dict[str, List[tuple]] = {g: [] for g in names}
    leagues: Dict[str, Dict[str, Any]] = {}
    daily: List[Dict[str, Any]] = []
    brier, brier_bk, n_bk = [], [], 0
    matches = 0
    for d in sorted(days):
        hits = n_day = 0
        for e in days[d]:
            grades = e.get("grades") or {}
            won = _winner(e)
            if won is None and not grades:
                continue
            matches += 1
            for g in names:
                x = grades.get(g) or {}
                if x.get("verdict") in ("won", "lost") and isinstance(x.get("prob"), (int, float)):
                    picks[g].append((float(x["prob"]), x["verdict"] == "won"))
            tip = grades.get("tip") or {}
            if tip.get("verdict") in ("won", "lost"):
                n_day += 1
                hits += tip["verdict"] == "won"
                key = e.get("league") or ""
                lg = leagues.setdefault(key, {"league": key, "name": e.get("league_name") or key,
                                              "flag": e.get("flag") or "", "n": 0, "hits": 0})
                lg["n"] += 1
                lg["hits"] += tip["verdict"] == "won"
            p = (e.get("pred") or {}).get("p_home")
            if won is not None and isinstance(p, (int, float)):
                brier.append((p - (1.0 if won else 0.0)) ** 2)
                oh, oa = (e.get("pred") or {}).get("odds_home"), (e.get("pred") or {}).get("odds_away")
                if oh and oa and oh > 1 and oa > 1:
                    q = (1 / oh) / (1 / oh + 1 / oa)
                    brier_bk.append(((q - (1.0 if won else 0.0)) ** 2, (p - (1.0 if won else 0.0)) ** 2))
                    n_bk += 1
        if n_day:
            daily.append({"date": d, "n": n_day, "favourite_hit": round(hits / n_day, 4)})
    markets = {}
    for g, rows in picks.items():
        if rows:
            markets[g] = {"n": len(rows), "name": names[g], "hit_rate": round(sum(w for _, w in rows) / len(rows), 4),
                          "avg_prob": round(sum(p for p, _ in rows) / len(rows), 4)}
    everything = [r for rows in picks.values() for r in rows]
    calibration = []
    for lo, hi in BANDS:
        sel = [(p, w) for p, w in everything if lo <= p * 100 < hi]
        if sel:
            calibration.append({"from": lo, "to": min(hi, 100), "n": len(sel),
                                "said": round(sum(p for p, _ in sel) / len(sel), 4),
                                "happened": round(sum(1 for _, w in sel if w) / len(sel), 4)})
    return {
        "matches": matches, "markets": markets, "calibration": calibration,
        "brier": {"model": round(sum(brier) / len(brier), 4) if brier else None, "matches": len(brier),
                  "vs_bookmaker": {"bookmaker": round(sum(b for b, _ in brier_bk) / n_bk, 4),
                                   "model": round(sum(m for _, m in brier_bk) / n_bk, 4), "matches": n_bk}
                  if n_bk >= 20 else None},
        "leagues": sorted(({**lg, "hit_rate": round(lg["hits"] / lg["n"], 4)} for lg in leagues.values()),
                          key=lambda x: -x["n"]),
        "daily": daily,
    }
