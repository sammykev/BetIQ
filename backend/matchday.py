"""
Match days: every predicted match with its pre-match prediction and, once
played, its result and how each market's pick did. Pure functions, no I/O —
main.py stores one JSON object per date in Redis (betiq:md:{date}) and the
results come from results_feed.py.

An entry's prediction is refreshed each time the predictions are rebuilt,
until kick-off; from then on it's locked, so what's graded is exactly what
the site showed before the match. Grades (per market: the pick, the
probability we gave it, and won / lost / push…) are what the track record
adds up: hit rate next to the probability we said, 1X2 Brier score against
the bookmaker's, by league and by day.
"""

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import grading

# Prediction fields kept with each entry (what the pick and the grading need)
_PRED_FIELDS = ("p_home", "p_draw", "p_away", "p_over15", "p_over25", "p_over35", "p_btts",
                "tip_1x2", "tip_code", "tip_confidence", "tip_goals", "goals_type", "goals_confidence",
                "odds_home", "odds_draw", "odds_away", "value_edge", "is_value_bet")
_HEADER_FIELDS = ("home", "away", "date", "time", "league", "league_name", "flag")
# Main lines graded for corners and bookings (the model's side of each)
SET_PIECE_LINES = {"corners": "9.5", "bookings": "4.5"}
# Match stats kept with a result (for settling tickets): the graded ones + shots
RESULT_STATS = (*SET_PIECE_LINES, "shots", "sot")

FINISHED, LIVE, SCHEDULED, POSTPONED = "finished", "live", "scheduled", "postponed"
MARKET_NAMES = {"tip": "Our tip (1X2 / double chance)", "goals": "Goals tip", "favourite": "Most likely result",
                "ou25": "Over/under 2.5", "btts": "Both teams to score",
                "corners": "Corners 9.5", "bookings": "Bookings 4.5"}


def key(home: str, away: str) -> str:
    return f"{(home or '').strip().lower()}|{(away or '').strip().lower()}"


def kickoff(entry: Dict) -> Optional[datetime]:
    """UTC kick-off (predictions carry UTC date and HH:MM); midday without a time."""
    try:
        return datetime.strptime(f"{entry['date']} {entry.get('time') or '12:00'}",
                                 "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return None


def _num(v: Any) -> Optional[float]:
    return round(float(v), 4) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def snapshot(pred: Dict) -> Dict[str, Any]:
    """The part of a prediction kept for grading and display."""
    out: Dict[str, Any] = {}
    for f in _PRED_FIELDS:
        v = pred.get(f)
        out[f] = _num(v) if f.startswith(("p_", "odds_", "value_", "goals_conf", "tip_conf")) else v
    sp = pred.get("set_pieces") or {}
    for stat, line in SET_PIECE_LINES.items():
        node = sp.get(stat) or {}
        over = (node.get("over") or {}).get(line)
        if isinstance(over, (int, float)):
            out[f"{stat}_mean"] = _num(node.get("mean"))
            out[f"{stat}_over"] = _num(over)
    ref = pred.get("referee")
    if isinstance(ref, dict) and ref.get("name"):
        out["referee"] = ref["name"]
    # SportyBet's prices for the outcomes we model (price_book.py), kept till kick-off
    if pred.get("_sb_prices"):
        out["prices"] = pred["_sb_prices"]
    return out


def _header(pred: Dict) -> Dict[str, Any]:
    return {f: pred.get(f) for f in _HEADER_FIELDS}


def merge_predictions(day: Dict[str, Dict], preds: Iterable[Dict], now: datetime) -> bool:
    """Add or refresh `preds` (all one date) in `day` ({key: entry}). A
    match that has kicked off keeps the prediction it had. True if anything
    changed."""
    changed = False
    for p in preds:
        if not p.get("home") or not p.get("away"):
            continue
        k = key(p["home"], p["away"])
        e = day.get(k)
        started = (kickoff(p) or now) <= now
        if e is not None and (e.get("locked") or started):
            if not e.get("locked"):
                e["locked"] = True
                changed = True
            continue
        snap, head = snapshot(p), _header(p)
        if e is None:
            day[k] = {**head, "pred": snap, "snap_at": now.isoformat(timespec="seconds"),
                      "locked": started, "result": None, "grades": None}
            changed = True
        elif e.get("pred") != snap or any(e.get(f) != v for f, v in head.items()):
            e.update(head, pred=snap, snap_at=now.isoformat(timespec="seconds"))
            changed = True
    return changed


# ── grading ──────────────────────────────────────────────────────────────
def _side_grade(prob_over: Optional[float], total: Optional[float], line: float,
                over_label: str, under_label: str) -> Optional[Dict]:
    if prob_over is None or total is None:
        return None
    over = prob_over >= 0.5
    won = total > line if over else total < line
    return {"pick": over_label if over else under_label, "prob": round(prob_over if over else 1 - prob_over, 4),
            "verdict": "won" if won else "lost"}


def grade(entry: Dict) -> Optional[Dict[str, Dict]]:
    """{market: {"pick", "prob", "verdict"}} for a finished match, or None."""
    res, p = entry.get("result") or {}, entry.get("pred") or {}
    if res.get("status") != FINISHED or res.get("aet") or res.get("hg") is None or res.get("ag") is None:
        return None
    hg, ag = int(res["hg"]), int(res["ag"])
    outcome = grading.result_from_score(hg, ag)
    out: Dict[str, Dict] = {}

    probs = {k: p.get(f"p_{n}") for k, n in (("H", "home"), ("D", "draw"), ("A", "away"))}
    if all(isinstance(v, (int, float)) for v in probs.values()):
        fav = max(probs, key=lambda k: probs[k])
        out["favourite"] = {"pick": {"H": entry.get("home"), "D": "Draw", "A": entry.get("away")}[fav],
                            "prob": probs[fav], "verdict": "won" if fav == outcome else "lost"}
    verdict = grading.grade_tip(p.get("tip_code") or "", outcome)
    if verdict != "void":
        prob = p.get("tip_confidence")
        if not isinstance(prob, (int, float)) and all(isinstance(v, (int, float)) for v in probs.values()):
            wins_on = grading._TIP_WINS_ON.get(p.get("tip_code") or "", set())
            prob = round(sum(probs[k] for k in wins_on), 4)
        out["tip"] = {"pick": p.get("tip_1x2") or p.get("tip_code"), "prob": prob, "verdict": verdict}
    goals = grading.grade_goals(p.get("tip_goals") or "", hg, ag)
    if goals:
        out["goals"] = {"pick": p.get("tip_goals"), "prob": p.get("goals_confidence"), "verdict": goals}
    g = _side_grade(p.get("p_over25"), hg + ag, 2.5, "Over 2.5", "Under 2.5")
    if g:
        out["ou25"] = g
    btts = p.get("p_btts")
    if isinstance(btts, (int, float)):
        yes = btts >= 0.5
        both = hg > 0 and ag > 0
        out["btts"] = {"pick": "Yes" if yes else "No", "prob": round(btts if yes else 1 - btts, 4),
                       "verdict": "won" if both == yes else "lost"}
    for stat, line in SET_PIECE_LINES.items():
        counts = res.get(stat)
        total = sum(counts) if isinstance(counts, list) and len(counts) == 2 else None
        g = _side_grade(p.get(f"{stat}_over"), total, float(line), f"Over {line}", f"Under {line}")
        if g:
            out[stat] = g
    return out


def apply_result(entry: Dict, res: Dict) -> bool:
    """Put a feed result on an entry (never a finished one back to live) and
    grade it. True if anything changed."""
    old = entry.get("result") or {}
    if old.get("status") == FINISHED:
        # Final already: a second source only fills in stats it lacked
        if res.get("status") != FINISHED:
            return False
        new = {k: v for k, v in old.items() if k != "at"}
        for stat in RESULT_STATS:
            if new.get(stat) is None and res.get(stat) is not None:
                new[stat] = res[stat]
    else:
        new = {k: res.get(k) for k in ("status", "minute", "hg", "ag", "aet", "source", *RESULT_STATS)}
        new = {k: v for k, v in new.items() if v is not None and v is not False}
    if {k: v for k, v in old.items() if k != "at"} == new:
        return False
    new["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry["result"] = new
    entry["grades"] = grade(entry)
    return True


def needs_result(entry: Dict, now: datetime) -> bool:
    """Kicked off (or due within 15 minutes) and not finished with stats."""
    res = entry.get("result") or {}
    ko = kickoff(entry)
    if ko is None or ko > now + timedelta(minutes=15):
        return False
    if res.get("status") == FINISHED:
        missing_stats = any(res.get(s) is None for s in SET_PIECE_LINES if (entry.get("pred") or {}).get(f"{s}_over") is not None)
        return missing_stats and now - ko < timedelta(days=4)
    return res.get("status") != POSTPONED or now - ko < timedelta(hours=6)


# ── matching feed results to entries ─────────────────────────────────────
def _similar(a: str, b: str) -> float:
    import international_fixtures as intl
    from sportybet import team_similarity
    if intl.team_key(a) == intl.team_key(b):
        return 1.0
    return team_similarity(a, b)


def match_results(entries: Dict[str, Dict], results: List[Dict]) -> List[Tuple[str, Dict]]:
    """[(entry key, feed result)] — both teams clearly the same, same side,
    kick-off within a day."""
    out = []
    for k, e in entries.items():
        try:
            d = date.fromisoformat(e["date"])
        except (KeyError, TypeError, ValueError):
            continue
        best, best_score = None, 0.0
        for res in results:
            try:
                if abs((date.fromisoformat(res["date"]) - d).days) > 1:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            sh, sa = _similar(e["home"], res["home"]), _similar(e["away"], res["away"])
            if min(sh, sa) < 0.75 or sh + sa <= best_score:
                continue
            best, best_score = res, sh + sa
        if best:
            out.append((k, best))
    return out


# ── reading ──────────────────────────────────────────────────────────────
def public(entry: Dict) -> Dict[str, Any]:
    """An entry as the site shows it."""
    res = entry.get("result") or {}
    status = res.get("status") or SCHEDULED
    return {**{f: entry.get(f) for f in _HEADER_FIELDS}, "key": key(entry.get("home", ""), entry.get("away", "")),
            "status": status, "minute": res.get("minute"), "aet": bool(res.get("aet")),
            "score": [res["hg"], res["ag"]] if res.get("hg") is not None and res.get("ag") is not None else None,
            "corners": res.get("corners"), "bookings": res.get("bookings"),
            "shots": res.get("shots"), "sot": res.get("sot"),
            "pred": {k: v for k, v in (entry.get("pred") or {}).items() if k != "prices"},
            "grades": entry.get("grades"), "locked": bool(entry.get("locked"))}


def day_summary(entries: Iterable[Dict]) -> Dict[str, Any]:
    entries = list(entries)
    s: Dict[str, Any] = {"total": len(entries), "finished": 0, "live": 0, "tip": [0, 0], "goals": [0, 0],
                         "favourite": [0, 0]}
    for e in entries:
        status = (e.get("result") or {}).get("status")
        if status == FINISHED:
            s["finished"] += 1
        elif status == LIVE:
            s["live"] += 1
        for m in ("tip", "goals", "favourite"):
            v = ((e.get("grades") or {}).get(m) or {}).get("verdict")
            if v in ("won", "half_won"):
                s[m][0] += 1
            elif v in ("lost", "half_lost"):
                s[m][1] += 1
    return s


_SCORE = {"won": 1.0, "half_won": 0.75, "half_lost": 0.25, "lost": 0.0}  # push: stake back, not counted


def _implied(p: Dict) -> Optional[Dict[str, float]]:
    odds = [p.get(f"odds_{s}") for s in ("home", "draw", "away")]
    if not all(isinstance(o, (int, float)) and o > 1 for o in odds):
        return None
    inv = [1 / o for o in odds]
    total = sum(inv)
    return dict(zip("HDA", (i / total for i in inv)))


def accuracy(days: Dict[str, Dict[str, Dict]]) -> Dict[str, Any]:
    """The track record over {date: {key: entry}}: per market hit rate next
    to the average probability we gave, calibration buckets over every
    graded pick, the 1X2 Brier score (ours vs the bookmaker's on matches
    with odds), per league and per day."""
    markets: Dict[str, Dict[str, float]] = {}
    buckets: Dict[int, List[float]] = {}
    brier = {"model": 0.0, "bookmaker": 0.0, "with_odds": 0, "model_all": 0.0, "n": 0}
    leagues: Dict[str, Dict[str, Any]] = {}
    daily = []
    matches = 0
    for d in sorted(days):
        day_n, day_hit = 0, 0.0
        for e in days[d].values():
            grades, res = e.get("grades") or {}, e.get("result") or {}
            if not grades:
                continue
            matches += 1
            for m, g in grades.items():
                v, prob = g.get("verdict"), g.get("prob")
                if v not in _SCORE:
                    continue
                s = markets.setdefault(m, {"n": 0, "hits": 0.0, "prob": 0.0, "with_prob": 0})
                s["n"] += 1
                s["hits"] += _SCORE[v]
                if isinstance(prob, (int, float)):
                    s["prob"] += prob
                    s["with_prob"] += 1
                    b = buckets.setdefault(min(9, int(prob * 10)), [0, 0.0, 0.0])
                    b[0] += 1
                    b[1] += prob
                    b[2] += _SCORE[v]
            p = e.get("pred") or {}
            outcome = grading.result_from_score(int(res["hg"]), int(res["ag"]))
            probs = [p.get(f"p_{n}") for n in ("home", "draw", "away")]
            if all(isinstance(x, (int, float)) for x in probs):
                score = sum((x - (1.0 if o == outcome else 0.0)) ** 2 for x, o in zip(probs, "HDA"))
                brier["model_all"] += score
                brier["n"] += 1
                book = _implied(p)
                if book:
                    brier["model"] += score
                    brier["bookmaker"] += sum((book[o] - (1.0 if o == outcome else 0.0)) ** 2 for o in "HDA")
                    brier["with_odds"] += 1
            fav = grades.get("favourite")
            if fav:
                lg = leagues.setdefault(e.get("league") or "?", {"league": e.get("league"), "name": e.get("league_name"),
                                                                "flag": e.get("flag"), "n": 0, "hits": 0})
                lg["n"] += 1
                lg["hits"] += fav["verdict"] == "won"
                day_n += 1
                day_hit += fav["verdict"] == "won"
        if day_n:
            daily.append({"date": d, "n": day_n, "favourite_hit": round(day_hit / day_n, 3)})

    def rate(s):
        return {"n": s["n"], "hit_rate": round(s["hits"] / s["n"], 3) if s["n"] else None,
                "avg_prob": round(s["prob"] / s["with_prob"], 3) if s["with_prob"] else None}
    wo = brier["with_odds"]
    return {
        "matches": matches,
        "markets": {m: {**rate(s), "name": MARKET_NAMES.get(m, m)} for m, s in markets.items()},
        "calibration": [{"from": b * 10, "to": b * 10 + 10, "n": v[0], "said": round(v[1] / v[0], 3),
                         "happened": round(v[2] / v[0], 3)} for b, v in sorted(buckets.items())],
        "brier": {"model": round(brier["model_all"] / brier["n"], 4) if brier["n"] else None, "matches": brier["n"],
                  "vs_bookmaker": {"model": round(brier["model"] / wo, 4), "bookmaker": round(brier["bookmaker"] / wo, 4),
                                   "matches": wo} if wo else None},
        "leagues": sorted(({**v, "hit_rate": round(v["hits"] / v["n"], 3)} for v in leagues.values()),
                          key=lambda v: -v["n"]),
        "daily": daily,
    }


def from_legacy(p: Dict) -> Dict[str, Any]:
    """An entry from the old history format (betiq:history:{date}: the whole
    prediction plus outcome / actual_result / score)."""
    e = {**_header(p), "pred": snapshot(p), "snap_at": None, "locked": True, "result": None, "grades": None}
    score = grading.parse_score(p.get("score"))
    if score:
        apply_result(e, {"status": FINISHED, "hg": score[0], "ag": score[1], "source": p.get("source") or "archive"})
    return e
