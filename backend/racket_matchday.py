"""
Tennis and table tennis match days, as basketball_matchday.py is for
basketball: every match we priced, with the prediction it had before it
started, its live score, and once finished, how our picks did. Pure
functions; main.py keeps one JSON object per sport and UTC date in Redis
(KEY), keyed by SportyBet's event id (its results carry the same id).

Graded picks:
    tip    our winner pick
    games  our side of SportyBet's main total games line (TT: points)
    best   our likeliest line at a useful price (1.15 odds or more)
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

import racket_markets as rkm

FINISHED, LIVE, SCHEDULED = "finished", "live", "scheduled"
KEY = "betiq:{sport}:md:{date}"
TTL = 120 * 86400
MIN_BEST_ODDS = 1.15
_HEADER = ("home", "away", "date", "time", "league", "league_name", "flag", "surface")
_PRED = ("p_home", "p_away", "tip_1x2", "tip_code", "tip_confidence", "tip_goals", "p_over_line", "total_line",
         "odds_home", "odds_away", "rated", "model")
GRADES = ("tip", "games", "best")
_SET = re.compile(r"^\s*(\d+)\s*:\s*(\d+)")
_ENDED = re.compile(r"ended|finished|retired|walkover|aet|^ft$", re.I)
_NOT_STARTED = re.compile(r"not\s*start|scheduled|postponed|cancel", re.I)


def key(sport: str, date: str) -> str:
    return KEY.format(sport=sport, date=date)


def kickoff(e: Dict) -> Optional[datetime]:
    try:
        return datetime.strptime(f"{e['date']} {e.get('time') or '12:00'}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return None


def best_line(pred: Dict) -> Optional[Dict]:
    """Our likeliest line at a useful price."""
    lines = [x for x in pred.get("rk_markets") or [] if x.get("odds", 0) >= MIN_BEST_ODDS]
    if not lines:
        return None
    x = max(lines, key=lambda l: l["prob"])
    return {k: x.get(k) for k in ("market", "market_name", "code", "label", "prob", "odds")}


def snapshot(pred: Dict) -> Dict[str, Any]:
    out = {k: pred.get(k) for k in _PRED}
    out["best"] = best_line(pred)
    return out


def merge_predictions(day: Dict[str, Dict], preds: Iterable[Dict], now: datetime) -> bool:
    """Add or refresh `preds` (one date) in `day` ({event id: entry}); a
    match that has started keeps the prediction it had. True if changed."""
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


def _settled(res: Dict) -> Dict:
    return {"status": FINISHED, "sets": res.get("score"), "games": res.get("periods"), "ret": res.get("ret")}


def grade(entry: Dict) -> Optional[Dict[str, Dict]]:
    """How each of our picks did, once the match is over."""
    res = entry.get("result") or {}
    if res.get("status") != FINISHED or not res.get("score"):
        return None
    p, out = entry.get("pred") or {}, {}
    settled = _settled(res)
    if p.get("tip_code") in ("1", "2"):
        out["tip"] = {"pick": p.get("tip_1x2"), "prob": p.get("tip_confidence"),
                      "verdict": rkm.settle("rk_winner", p["tip_code"], settled)}
    tip_total, line, over = p.get("tip_goals") or "", p.get("total_line"), p.get("p_over_line")
    if tip_total and line is not None and over is not None:
        is_over = tip_total.startswith("Over")
        out["games"] = {"pick": tip_total, "prob": round(over if is_over else 1 - over, 4),
                        "verdict": rkm.settle("rk_total_games", f"{'O' if is_over else 'U'}{line:g}", settled)}
    b = p.get("best")
    if b:
        out["best"] = {"pick": b.get("label"), "prob": b.get("prob"), "odds": b.get("odds"),
                       "verdict": rkm.settle(b["market"], b["code"], settled)}
    return {k: v for k, v in out.items() if v["verdict"] in ("won", "lost")}


def apply_result(entry: Dict, m: Dict) -> bool:
    """A final from SportyBet's results (tennis_facts.parse_result)."""
    new = {"status": FINISHED, "score": list(m["sets"]), "periods": m.get("games") or None, "ret": bool(m.get("ret"))}
    old = entry.get("result") or {}
    if {k: old.get(k) for k in new} == new:
        return False
    entry["result"] = {**new, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    entry["grades"] = grade(entry)
    return True


def _pair(s: Any) -> Optional[List[int]]:
    m = _SET.match(str(s or ""))
    return [int(m.group(1)), int(m.group(2))] if m else None


def short_status(s: str, sport: str) -> str:
    """ "2nd set" -> "S2"; table tennis "3rd game" -> "G3"."""
    s = re.sub(r"(\d)(?:st|nd|rd|th)\s+set", r"S\1", s, flags=re.I)
    return re.sub(r"(\d)(?:st|nd|rd|th)\s+game", r"G\1", s, flags=re.I)


def parse_live(ev: Dict, sport: str = "tennis") -> Optional[Dict[str, Any]]:
    """An in-play match from a SportyBet live event: {id, score (sets),
    periods (games per set), minute (set and point score)}."""
    status = str(ev.get("matchStatus") or "").strip()
    score = _pair(ev.get("setScore"))
    if not score or _ENDED.search(status) or _NOT_STARTED.search(status) or ev.get("status") in (0, 4):
        return None
    periods = [p for p in (_pair(x) for x in ev.get("gameScore") or []) if p]
    point = str(ev.get("pointScore") or "").strip()
    minute = short_status(status, sport) + (f" · {point}" if point and sport == "tennis" else "")
    return {"id": str(ev.get("eventId") or ""), "score": score, "periods": periods or None, "minute": minute}


def apply_live(entry: Dict, live: Dict) -> bool:
    old = entry.get("result") or {}
    if old.get("status") == FINISHED:
        return False
    new = {"status": LIVE, "score": live.get("score"), "periods": live.get("periods"), "minute": live.get("minute")}
    if {k: old.get(k) for k in new} == new:
        return False
    entry["result"] = {**new, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    return True


def stale_live(entry: Dict, live: Dict[str, Dict], checked: Iterable[str]) -> bool:
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
    """An entry as the site shows it (the basketball days' shape: score is
    sets, periods are each set's games)."""
    res = entry.get("result") or {}
    p = entry.get("pred") or {}
    over = p.get("p_over_line")
    return {**{k: entry.get(k) for k in _HEADER}, "key": entry.get("id"), "id": entry.get("id"),
            "status": res.get("status") or SCHEDULED, "minute": res.get("minute"), "ret": bool(res.get("ret")),
            "score": res.get("score"), "periods": res.get("periods"),
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
