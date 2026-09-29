"""
Tennis and table tennis match facts, as football and basketball have them:
each player's last 5 matches, the head-to-head, and each player's numbers
over recent matches. From SportyBet's own results (tennis sr:sport:5, table
tennis sr:sport:20), which name players exactly as its fixtures do, so no
name matching is needed.

Results are kept in Redis per day (SPORTS[sport]["key"], a hash: date -> the
day's matches, compressed), collected a day at a time like basketball's:
yesterday and today each run, older days a few at a time up to "days" back
(table tennis: far more matches a day, and players play several a day, so a
shorter window). Singles only (doubles pairs, "A / B", are left out).

"sets" and "games" below are tennis's words; in table tennis they are games
and points (a "tiebreak" there is a game that went to deuce, 10-10).
"""

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

TENNIS = "sr:sport:5"
SPORTS = {
    "tennis": {"id": TENNIS, "key": "betiq:tennis:results", "days": 300, "backfill": 30, "pages": 60},
    "table_tennis": {"id": "sr:sport:20", "key": "betiq:table_tennis:results", "days": 45, "backfill": 15, "pages": 120},
}
RESULTS_KEY = SPORTS["tennis"]["key"]
RESULT_DAYS = SPORTS["tennis"]["days"]
BACKFILL_PER_RUN = SPORTS["tennis"]["backfill"]
FORM_MATCHES = 5
AVG_MATCHES = 20
H2H_MATCHES = 8
_ENDED = re.compile(r"ended|finished|retired|walkover|aet|^ft$", re.I)
_SET = re.compile(r"^\s*(\d+)\s*:\s*(\d+)")
# (kick-off, tournament, player 1, player 2, sets [p1, p2], games per set [[p1, p2], ...], retired)
Match = Tuple[int, str, str, str, List[int], List[List[int]], bool]


def surface(tournament: str, sport: str = "tennis") -> Optional[str]:
    if sport != "tennis":
        return None
    from sports_fetcher import _surface
    return _surface(tournament or "")


def _tie(g: List[int], sport: str) -> bool:
    """A tiebreak set (tennis 7-6), or a table tennis game that went to deuce (both on 10+)."""
    return min(g) >= 10 if sport == "table_tennis" else {g[0], g[1]} == {7, 6}


def _pair(s: Any) -> Optional[Tuple[int, int]]:
    m = _SET.match(str(s or ""))            # "7:6(7:4)" -> (7, 6)
    return (int(m.group(1)), int(m.group(2))) if m else None


def parse_result(ev: Dict) -> Optional[Dict[str, Any]]:
    """A finished singles match from SportyBet's results: {id, t, h, a, ko,
    sets: [h, a], games: [[h, a] per set], ret}."""
    status = str(ev.get("matchStatus") or "")
    if not _ENDED.search(status) and ev.get("status") != 4:
        return None
    h, a = ev.get("homeTeamName") or "", ev.get("awayTeamName") or ""
    if not h or not a or "/" in h or "/" in a:
        return None
    sets = _pair(ev.get("setScore"))
    try:
        ko = int(int(ev["estimateStartTime"]) / 1000)
    except (KeyError, TypeError, ValueError):
        return None
    games = [list(p) for p in (_pair(x) for x in ev.get("gameScore") or []) if p]
    if not sets or sets == (0, 0) or sets[0] == sets[1]:
        return None                         # walkover, or no winner recorded
    return {"id": str(ev.get("eventId") or ""), "t": ev.get("_tournament") or "", "h": h, "a": a, "ko": ko,
            "sets": list(sets), "games": games, "ret": bool(re.search(r"retir", status, re.I))}


def days_to_collect(have: Iterable[str], today: date, sport: str = "tennis") -> List[str]:
    cfg = SPORTS[sport]
    have = set(have)
    recent = [(today - timedelta(days=i)).isoformat() for i in (1, 0)]
    older = [(today - timedelta(days=i)).isoformat() for i in range(2, cfg["days"] + 1)]
    return recent + [d for d in older if d not in have][:cfg["backfill"]]


def stale_days(have: Iterable[str], today: date, sport: str = "tennis") -> List[str]:
    """Stored days older than the sport's window (dropped to keep the store small)."""
    first = (today - timedelta(days=SPORTS[sport]["days"])).isoformat()
    return [d for d in have if d < first]


async def fetch_results_day(day: str, session=None, sport: str = "tennis") -> List[Dict]:
    """Every finished singles match of the sport SportyBet has for one UTC day."""
    import sportybet
    cfg = SPORTS[sport]
    session = session or sportybet.shared_session()
    start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    events, _, _ = await sportybet._paged(session, "/factsCenter/eventResultList", {
        "pageSize": 100, "sportId": cfg["id"], "startTime": int(start.timestamp() * 1000),
        "endTime": int((start + timedelta(days=1)).timestamp() * 1000)}, cfg["pages"])
    return [m for m in (parse_result(e) for e in events) if m]


# ── Facts ────────────────────────────────────────────────────────────────

def build_index(results: Iterable[Dict]) -> Dict[str, List[Match]]:
    idx: Dict[str, List[Match]] = {}
    seen = set()
    for m in results:
        if m.get("id") in seen:
            continue
        seen.add(m.get("id"))
        row: Match = (int(m["ko"]), m.get("t") or "", m["h"], m["a"], list(m["sets"]), m.get("games") or [], bool(m.get("ret")))
        idx.setdefault(m["h"], []).append(row)
        idx.setdefault(m["a"], []).append(row)
    for rows in idx.values():
        rows.sort(key=lambda x: -x[0])
    return idx


def _day(ko: int) -> str:
    return datetime.fromtimestamp(ko, timezone.utc).date().isoformat()


def _side(m: Match, player: str) -> Tuple[List[int], List[List[int]], str]:
    """(sets [for, against], games per set [for, against], opponent) from the player's side."""
    first = m[2] == player
    sets = m[4] if first else [m[4][1], m[4][0]]
    games = [g if first else [g[1], g[0]] for g in m[5]]
    return sets, games, (m[3] if first else m[2])


def _tournament(t: str) -> str:
    return t.split(" · ", 1)[1] if " · " in t else t


def form(rows: List[Match], player: str, before: Optional[int] = None, n: int = FORM_MATCHES,
         sport: str = "tennis") -> List[Dict[str, Any]]:
    out = []
    for m in rows:
        if before is not None and m[0] >= before:
            continue
        sets, games, opp = _side(m, player)
        out.append({"date": _day(m[0]), "opponent": opp, "outcome": "W" if sets[0] > sets[1] else "L",
                    "sets": sets, "score": " ".join(f"{g[0]}-{g[1]}" for g in games) or f"{sets[0]}-{sets[1]}",
                    "retired": m[6], "tournament": _tournament(m[1]), "surface": surface(m[1], sport)})
        if len(out) >= n:
            break
    return out


def averages(rows: List[Match], player: str, before: Optional[int] = None, n: int = AVG_MATCHES,
             on_surface: Optional[str] = None, sport: str = "tennis") -> Optional[Dict[str, Any]]:
    """A player's numbers over his last `n` matches (retirements count as
    played, as SportyBet settles the winner)."""
    recent = [m for m in rows if before is None or m[0] < before][:n]
    if not recent:
        return None
    won = sets_for = sets_against = straight = deciding = deciding_won = tiebreaks = with_games = 0
    games_for, games_against, totals = [], [], []
    for m in recent:
        sets, games, _ = _side(m, player)
        w = sets[0] > sets[1]
        won += w
        sets_for += sets[0]
        sets_against += sets[1]
        straight += w and sets[1] == 0
        if min(sets) >= 1 and max(sets) == min(sets) + 1:        # went the distance (3 of 3, 5 of 5)
            deciding += 1
            deciding_won += w
        if games:
            with_games += 1
            gf, ga = sum(g[0] for g in games), sum(g[1] for g in games)
            games_for.append(gf)
            games_against.append(ga)
            totals.append(gf + ga)
            tiebreaks += any(_tie(g, sport) for g in games)
    k = len(recent)
    avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else None
    surf = [m for m in recent if on_surface and surface(m[1], sport) == on_surface]
    surf_won = sum(1 for m in surf if _side(m, player)[0][0] > _side(m, player)[0][1])
    return {
        "played": k, "won": round(won / k, 3),
        "sets": {"for": round(sets_for / k, 2), "against": round(sets_against / k, 2)},
        "straight_sets_wins": round(straight / k, 3),
        "deciding_set": {"rate": round(deciding / k, 3), "won": round(deciding_won / deciding, 3) if deciding else None},
        "games": {"for": avg(games_for), "against": avg(games_against), "total": avg(totals), "matches": with_games},
        "tiebreak_matches": round(tiebreaks / with_games, 3) if with_games else None,
        "surface": {"name": on_surface, "played": len(surf), "won": round(surf_won / len(surf), 3) if surf else None}
        if on_surface else None,
    }


def head_to_head(idx: Dict[str, List[Match]], p1: str, p2: str, before: Optional[int] = None,
                 n: int = H2H_MATCHES, sport: str = "tennis") -> Tuple[List[Dict], Optional[Dict]]:
    meetings = [m for m in idx.get(p1, []) if {m[2], m[3]} == {p1, p2} and (before is None or m[0] < before)][:n]
    rows = [{"date": _day(m[0]), "home": m[2], "away": m[3], "sets": m[4],
             "score": " ".join(f"{g[0]}-{g[1]}" for g in m[5]), "retired": m[6],
             "tournament": _tournament(m[1]), "surface": surface(m[1], sport)} for m in meetings]
    if not rows:
        return [], None
    won = sum(1 for m in meetings if _side(m, p1)[0][0] > _side(m, p1)[0][1])
    return rows, {"won": won, "lost": len(rows) - won}


def facts(idx: Dict[str, List[Match]], pred: Dict, sport: str = "tennis") -> Dict[str, Any]:
    p1, p2 = pred["home"], pred["away"]
    try:
        before = int(datetime.fromisoformat(f"{pred['date']}T{pred.get('time') or '12:00'}:00+00:00").timestamp())
    except (KeyError, ValueError):
        before = None
    surf = (pred.get("surface") or surface(pred.get("league") or "", sport)) if sport == "tennis" else None
    h2h, summary = head_to_head(idx, p1, p2, before, sport=sport)
    return {"home": form(idx.get(p1, []), p1, before, sport=sport), "away": form(idx.get(p2, []), p2, before, sport=sport),
            "h2h": h2h, "summary": {"h2h": summary}, "surface": surf, "sport": sport,
            "averages": {"n": AVG_MATCHES, "home": averages(idx.get(p1, []), p1, before, on_surface=surf, sport=sport),
                         "away": averages(idx.get(p2, []), p2, before, on_surface=surf, sport=sport)}}


def form_string(idx: Dict[str, List[Match]], player: str) -> str:
    return "".join(r["outcome"] for r in reversed(form(idx.get(player, []), player)))
