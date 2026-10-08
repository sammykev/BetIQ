"""
Basketball match days, as matchday.py is for football: every basketball
match we priced, with the prediction it had before tip-off, its live score,
and once finished, how our picks did. Pure functions; main.py keeps one JSON
object per UTC date in Redis (betiq:bbmd:{date}), keyed by SportyBet's event
id (the same id its results carry, so no team-name matching).

Graded picks:
    tip    our winner pick (overtime counts, as on SportyBet)
    points our side of SportyBet's main total points line
    best   our likeliest line at a useful price (1.15 odds or more)
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

import basketball_markets as bmk

FINISHED, LIVE, SCHEDULED = "finished", "live", "scheduled"
KEY = "betiq:bbmd:{}"
TTL = 120 * 86400
MIN_BEST_ODDS = 1.15
_HEADER = ("home", "away", "date", "time", "league", "league_name", "flag", "home_logo", "away_logo")
_PRED = ("p_home", "p_away", "tip_1x2", "tip_code", "tip_confidence", "tip_goals", "p_over_line", "total_line",
         "handicap_line", "exp_home_pts", "exp_away_pts", "odds_home", "odds_away", "rated", "model")
GRADES = ("tip", "points", "best")


def kickoff(e: Dict) -> Optional[datetime]:
    try:
        return datetime.strptime(f"{e['date']} {e.get('time') or '12:00'}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return None


def best_line(pred: Dict) -> Optional[Dict]:
    """Our likeliest line at a useful price (no overtime or player lines)."""
    lines = [x for x in pred.get("bb_markets") or pred.get("top_lines") or []
             if x.get("odds", 0) >= MIN_BEST_ODDS and x.get("family") not in ("bb_overtime", "bb_player")
             and not str(x.get("market", "")).startswith("bb_player")]
    if not lines:
        return None
    x = max(lines, key=lambda l: l["prob"])
    return {k: x.get(k) for k in ("market", "market_name", "code", "label", "prob", "odds")}


def snapshot(pred: Dict) -> Dict[str, Any]:
    out = {k: pred.get(k) for k in _PRED}
    out["best"] = best_line(pred)
    detail = pred.get("model_detail") or {}
    ratings = detail.get("ratings")
    if ratings:   # each side's rating before the start (the site shows them)
        out["ratings"] = ratings
    # Our margin and total and the market's, before blending (check_basketball_blend.py)
    lines = {k: detail[k] for k in ("model_margin", "model_total", "market_margin", "market_total")
             if isinstance(detail.get(k), (int, float))}
    if lines:
        out["lines"] = lines
    return out


def merge_predictions(day: Dict[str, Dict], preds: Iterable[Dict], now: datetime) -> bool:
    """Add or refresh `preds` (one date) in `day` ({event id: entry}); a
    match that has tipped off keeps the prediction it had. True if changed."""
    changed = False
    for p in preds:
        eid = str(p.get("sportybet_event_id") or "")
        if not eid or not p.get("home") or not p.get("away"):
            continue
        e = day.get(eid)
        started = (kickoff(p) or now) <= now
        if e is not None and (e.get("locked") or started):
            if not e.get("locked"):
                e["locked"] = True
                changed = True
            continue
        head, snap = {k: p.get(k) for k in _HEADER}, snapshot(p)
        if e is None:
            day[eid] = {**head, "id": eid, "pred": snap, "locked": started, "result": None, "grades": None}
            changed = True
        elif e.get("pred") != snap or any(e.get(k) != v for k, v in head.items()):
            e.update(head, pred=snap)
            changed = True
    return changed


def _settle_result(res: Dict) -> Dict:
    return {"status": "finished", "final": res.get("score"), "periods": res.get("periods"), "ot": res.get("ot")}


def grade(entry: Dict) -> Optional[Dict[str, Dict]]:
    """How each of our picks did, once the game is final."""
    res = entry.get("result") or {}
    if res.get("status") != FINISHED or not res.get("score"):
        return None
    p, out = entry.get("pred") or {}, {}
    settled = _settle_result(res)
    if p.get("tip_code") in ("1", "2"):
        out["tip"] = {"pick": p.get("tip_1x2"), "prob": p.get("tip_confidence"),
                      "verdict": bmk.settle("bb_winner", p["tip_code"], settled)}
    tip_total, line, over = p.get("tip_goals") or "", p.get("total_line"), p.get("p_over_line")
    if tip_total and line is not None and over is not None:
        is_over = tip_total.startswith("Over")
        out["points"] = {"pick": tip_total, "prob": round(over if is_over else 1 - over, 4),
                        "verdict": bmk.settle("bb_total", f"{'O' if is_over else 'U'}{line:g}", settled)}
    b = p.get("best")
    if b:
        out["best"] = {"pick": b.get("label"), "prob": b.get("prob"), "odds": b.get("odds"),
                       "verdict": bmk.settle(b["market"], b["code"], settled)}
    # A pick that can't be settled from what we have (no quarter scores) isn't counted
    return {k: v for k, v in out.items() if v["verdict"] in ("won", "lost")}


def apply_result(entry: Dict, game: Dict) -> bool:
    """A final score from SportyBet's results (basketball_data.parse_result)."""
    new = {"status": FINISHED, "score": [game["hs"], game["as"]], "periods": game.get("q"), "ot": bool(game.get("ot"))}
    old = entry.get("result") or {}
    if {k: old.get(k) for k in new} == new:
        return False
    entry["result"] = {**new, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    entry["grades"] = grade(entry)
    return True


def apply_live(entry: Dict, live: Dict) -> bool:
    """An in-play score ({score, periods, minute}); never over a final."""
    old = entry.get("result") or {}
    if old.get("status") == FINISHED:
        return False
    new = {"status": LIVE, "score": live.get("score"), "periods": live.get("periods"), "minute": live.get("minute")}
    if {k: old.get(k) for k in new} == new:
        return False
    entry["result"] = {**new, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    return True


def stale_live(entry: Dict, live: Dict[str, Dict], checked: Iterable[str]) -> bool:
    """Shown live, checked on SportyBet and no longer in play: it has ended
    (the final comes with the results); awaiting the final meanwhile."""
    res = entry.get("result") or {}
    eid = entry.get("id")
    if res.get("status") == LIVE and eid in set(checked) and eid not in live:
        entry["result"] = {**res, "status": SCHEDULED, "minute": "Final soon"}
        return True
    return False


def needs_result(entry: Dict, now: datetime) -> bool:
    ko = kickoff(entry)
    return (ko is not None and ko <= now and (entry.get("result") or {}).get("status") != FINISHED
            and now - ko < timedelta(days=3))


def public(entry: Dict) -> Dict[str, Any]:
    """An entry as the site shows it (lib/matchday.ts MatchdayMatch, plus
    quarters and our best line)."""
    res = entry.get("result") or {}
    p = entry.get("pred") or {}
    over = p.get("p_over_line")
    return {**{k: entry.get(k) for k in _HEADER}, "key": entry.get("id"), "id": entry.get("id"),
            "status": res.get("status") or SCHEDULED, "minute": res.get("minute"), "aet": bool(res.get("ot")),
            "score": res.get("score"), "periods": res.get("periods"),
            "corners": None, "bookings": None,
            "pred": {**{k: v for k, v in p.items() if k != "best"}, "p_draw": 0,
                     "goals_confidence": (over if (p.get("tip_goals") or "").startswith("Over") else 1 - over)
                     if over is not None else None},
            "best": p.get("best"), "grades": entry.get("grades"), "locked": bool(entry.get("locked"))}


def day_summary(entries: Iterable[Dict]) -> Dict[str, Any]:
    entries = list(entries)
    s: Dict[str, Any] = {"total": len(entries), "finished": 0, "live": 0, **{g: [0, 0] for g in GRADES}}
    for e in entries:
        status = (e.get("result") or {}).get("status")
        s["finished"] += status == FINISHED
        s["live"] += status == LIVE
        for g in GRADES:
            v = ((e.get("grades") or {}).get(g) or {}).get("verdict")
            if v == "won":
                s[g][0] += 1
            elif v == "lost":
                s[g][1] += 1
    return s


def by_date(preds: Iterable[Dict]) -> Dict[str, List[Dict]]:
    out: Dict[str, List[Dict]] = {}
    for p in preds:
        if p.get("date"):
            out.setdefault(p["date"], []).append(p)
    return out
