"""
Basketball match facts, as the football match page has them: each team's
last 5 games, their head-to-head meetings, and each team's averages over its
last games. From SportyBet's own results (basketball_data), which name teams
exactly as its fixtures do, so no name matching is needed.

The index (build_index) is made when the server rates the leagues and kept
in memory: {team: [game, ...]} newest first, slim tuples.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import basketball_data as bd

FORM_GAMES = 5
AVG_GAMES = 10
H2H_GAMES = 6
# (kick-off, competition, home, away, home points, away points, quarters, overtime)
Game = Tuple[int, str, str, str, int, int, Optional[List[List[int]]], bool]


def build_index(results: Iterable[Dict]) -> Dict[str, List[Game]]:
    """Every team's games, newest first (friendlies and exhibitions too:
    they're games a team played)."""
    idx: Dict[str, List[Game]] = {}
    seen = set()
    for g in results:
        if g.get("id") in seen or not g.get("h") or not g.get("a"):
            continue
        seen.add(g.get("id"))
        row: Game = (int(g["ko"]), g.get("t") or "", g["h"], g["a"], int(g["hs"]), int(g["as"]), g.get("q"), bool(g.get("ot")))
        idx.setdefault(g["h"], []).append(row)
        idx.setdefault(g["a"], []).append(row)
    for games in idx.values():
        games.sort(key=lambda x: -x[0])
    return idx


def _day(ko: int) -> str:
    return datetime.fromtimestamp(ko, timezone.utc).date().isoformat()


def _comp(t: str) -> str:
    return bd.split_tournament(t)[1] or t


def _side(g: Game, team: str) -> Tuple[bool, int, int, str]:
    """(at home, points for, points against, opponent) from the team's side."""
    home = g[2] == team
    return home, (g[4] if home else g[5]), (g[5] if home else g[4]), (g[3] if home else g[2])


def form(games: List[Game], team: str, before: Optional[int] = None, n: int = FORM_GAMES) -> List[Dict[str, Any]]:
    out = []
    for g in games:
        if before is not None and g[0] >= before:
            continue
        home, pf, pa, opp = _side(g, team)
        out.append({"date": _day(g[0]), "opponent": opp, "venue": "H" if home else "A", "for": pf, "against": pa,
                    "outcome": "W" if pf > pa else "L", "ot": g[7], "comp": _comp(g[1])})
        if len(out) >= n:
            break
    return out


def averages(games: List[Game], team: str, before: Optional[int] = None, n: int = AVG_GAMES,
             total_line: Optional[float] = None, handicap: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """A team's numbers per game over its last `n` games. total_line: the
    match's main total (how often its games went over it); handicap: this
    team's line (how often it covered)."""
    rows = [g for g in games if before is None or g[0] < before][:n]
    if not rows:
        return None
    pf, pa, wins, ot, over, cover = [], [], 0, 0, 0, 0
    qf: List[List[int]] = [[], [], [], []]
    qa: List[List[int]] = [[], [], [], []]
    h1f, h1a, h2f, h2a = [], [], [], []
    home_pf, away_pf = [], []
    for g in rows:
        home, f, a, _ = _side(g, team)
        pf.append(f)
        pa.append(a)
        wins += f > a
        ot += g[7]
        (home_pf if home else away_pf).append(f)
        if total_line is not None:
            over += f + a > total_line
        if handicap is not None:
            cover += f + handicap > a
        q = g[6]
        if q and len(q) >= 4:
            for i in range(4):
                qf[i].append(q[i][0] if home else q[i][1])
                qa[i].append(q[i][1] if home else q[i][0])
            h1f.append(qf[0][-1] + qf[1][-1])
            h1a.append(qa[0][-1] + qa[1][-1])
            h2f.append(qf[2][-1] + qf[3][-1])
            h2a.append(qa[2][-1] + qa[3][-1])
    n_ = len(rows)
    avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else None
    return {
        "played": n_,
        "points": {"for": avg(pf), "against": avg(pa), "total": avg([x + y for x, y in zip(pf, pa)]),
                   "margin": avg([x - y for x, y in zip(pf, pa)])},
        "results": {"won": round(wins / n_, 3), "lost": round(1 - wins / n_, 3)},
        "overtime": round(ot / n_, 3),
        "home_points": avg(home_pf), "away_points": avg(away_pf),
        "halves": {"first_for": avg(h1f), "first_against": avg(h1a), "second_for": avg(h2f),
                   "second_against": avg(h2a), "matches": len(h1f)},
        "quarters": {"for": [avg(x) for x in qf], "against": [avg(x) for x in qa], "matches": len(qf[0])},
        "over_line": {"line": total_line, "rate": round(over / n_, 3)} if total_line is not None else None,
        "cover": {"line": handicap, "rate": round(cover / n_, 3)} if handicap is not None else None,
    }


def head_to_head(idx: Dict[str, List[Game]], home: str, away: str, before: Optional[int] = None,
                 n: int = H2H_GAMES) -> Tuple[List[Dict], Optional[Dict]]:
    """Their last meetings (either venue) and a summary from `home`'s side."""
    meetings = [g for g in idx.get(home, []) if {g[2], g[3]} == {home, away} and (before is None or g[0] < before)][:n]
    rows = [{"date": _day(g[0]), "home": g[2], "away": g[3], "hs": g[4], "as": g[5], "ot": g[7], "comp": _comp(g[1])}
            for g in meetings]
    if not rows:
        return [], None
    won = sum(1 for g in meetings if _side(g, home)[1] > _side(g, home)[2])
    return rows, {"won": won, "lost": len(rows) - won,
                  "avg_total": round(sum(g[4] + g[5] for g in meetings) / len(meetings), 1),
                  "avg_margin": round(sum(_side(g, home)[1] - _side(g, home)[2] for g in meetings) / len(meetings), 1)}


def facts(idx: Dict[str, List[Game]], pred: Dict) -> Dict[str, Any]:
    """Everything the match view shows, for one prediction."""
    home, away = pred["home"], pred["away"]
    try:
        before = int(datetime.fromisoformat(f"{pred['date']}T{pred.get('time') or '12:00'}:00+00:00").timestamp())
    except (KeyError, ValueError):
        before = None
    hg, ag = idx.get(home, []), idx.get(away, [])
    line, hcp = pred.get("total_line"), pred.get("handicap_line")
    h2h, summary = head_to_head(idx, home, away, before)
    return {
        "home": form(hg, home, before), "away": form(ag, away, before),
        "h2h": h2h, "summary": {"h2h": summary},
        "averages": {"n": AVG_GAMES,
                     "home": averages(hg, home, before, total_line=line, handicap=hcp),
                     "away": averages(ag, away, before, total_line=line, handicap=-hcp if hcp is not None else None)},
    }


def form_string(idx: Dict[str, List[Game]], team: str, before: Optional[int] = None) -> str:
    """ "WWLWL", oldest to newest, for the match card."""
    return "".join(r["outcome"] for r in reversed(form(idx.get(team, []), team, before)))
