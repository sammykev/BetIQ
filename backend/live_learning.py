"""
Learning from each match as it finishes.

The server's models are built from training data (the goals/result model
nightly on GitHub, the corners/cards and shots models on each pipeline run).
Between those, every finished match is fed into them once, as soon as the
live scores have it: ratings, form, goals, shots on target, corners and
bookings move, and the teams' upcoming predictions are redone.

Once is the point. Each model remembers which matches it already holds
(`_applied_keys`: the recent rows it was built from, plus every match fed in
since), so a match that arrives again (the next live check, the 3-hourly
results fetch, a day later from another source) is never counted twice.
A model without that memory is left alone rather than risk double counting.
"""

from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, Iterable, Optional, Set

import pandas as pd

APPLIED_DAYS = 45       # training rows this recent are remembered (older ones can't arrive again)
FINISHED = "finished"


def key(day: str, home: str, away: str) -> str:
    return f"{str(day)[:10]}|{home}|{away}"


def seen(applied: Set[str], day: str, home: str, away: str) -> bool:
    """Whether the match is already held, allowing a day either side (sources
    date a late kick-off by UTC or by local time)."""
    try:
        d = date.fromisoformat(str(day)[:10])
    except ValueError:
        return key(day, home, away) in applied
    return any(key((d + timedelta(days=o)).isoformat(), home, away) in applied for o in (-1, 0, 1))


def mark_trained(model: Any, frame: Optional[pd.DataFrame], canon: Callable[[str], str] = lambda n: n,
                 today: Optional[date] = None) -> int:
    """Remember the recent matches `model` was built from; returns how many."""
    if model is None:
        return 0
    keys: Set[str] = set()
    if frame is not None and not frame.empty and {"Date", "HomeTeam", "AwayTeam"} <= set(frame.columns):
        cut = pd.Timestamp((today or date.today()) - timedelta(days=APPLIED_DAYS))
        dates = pd.to_datetime(frame["Date"], errors="coerce")
        recent = frame[dates >= cut]
        for d, h, a in zip(pd.to_datetime(recent["Date"], errors="coerce"), recent["HomeTeam"], recent["AwayTeam"]):
            if pd.notna(d) and isinstance(h, str) and isinstance(a, str):
                keys.add(key(d.date().isoformat(), canon(h), canon(a)))
    model._applied_keys = keys
    return len(keys)


def _pair(v) -> Optional[tuple]:
    try:
        h, a = float(v[0]), float(v[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    return (h, a) if h == h and a == a else None


def row_from_entry(e: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A finished football match from the match-day store as a result row, or None."""
    res = e.get("result") or {}
    if res.get("status") != FINISHED or res.get("aet") or e.get("sport") not in (None, "football"):
        return None
    try:
        hg, ag = int(res["hg"]), int(res["ag"])
    except (KeyError, TypeError, ValueError):
        return None
    if not e.get("home") or not e.get("away") or not e.get("date"):
        return None
    return {"Date": str(e["date"])[:10], "HomeTeam": e["home"], "AwayTeam": e["away"], "FTHG": hg, "FTAG": ag,
            "Result": "H" if hg > ag else "A" if hg < ag else "D", "league": e.get("league") or "",
            "corners": _pair(res.get("corners")), "bookings": _pair(res.get("bookings")),
            "shots": _pair(res.get("shots")), "sot": _pair(res.get("sot"))}


def learn(row: Dict[str, Any], predictor: Any = None, set_pieces: Any = None, shots: Any = None,
          international: bool = False, competition: str = "") -> Set[str]:
    """Feed one finished match into each model that doesn't hold it yet.
    Returns the teams (model names) whose numbers moved."""
    moved: Set[str] = set()
    canon = predictor.canon if predictor is not None and hasattr(predictor, "canon") else (lambda n: n)
    home, away = canon(row["HomeTeam"]), canon(row["AwayTeam"])
    day = row["Date"]

    def fresh(model) -> bool:
        applied = getattr(model, "_applied_keys", None)
        return model is not None and applied is not None and not seen(applied, day, home, away)

    if fresh(predictor):
        sot = row.get("sot") or (None, None)
        books = row.get("bookings")
        predictor._update(home, away, row["Result"], float(row["FTHG"]), float(row["FTAG"]),
                          hyc=books[0] if books else None, ayc=books[1] if books else None, hrc=0, arc=0,
                          match_date=day, competition=competition or row.get("league") or "",
                          hst=sot[0], ast=sot[1])
        predictor._applied_keys.add(key(day, home, away))
        moved |= {home, away}
    if not international:
        if fresh(set_pieces) and row.get("corners") and row.get("bookings"):
            set_pieces.update(home, away, row.get("league") or "",
                              {"corners": row["corners"], "bookings": row["bookings"]}, None)
            set_pieces._applied_keys.add(key(day, home, away))
            moved |= {home, away}
        if fresh(shots) and row.get("shots") and row.get("sot"):
            shots.update(home, away, row.get("league") or "", {"shots": row["shots"], "sot": row["sot"]}, None)
            shots._applied_keys.add(key(day, home, away))
            moved |= {home, away}
    return moved


def result_row(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A fetched result (league CSV shape) as a learn() row."""
    try:
        d = pd.to_datetime(r["Date"]).date().isoformat()
        hg, ag = int(r["FTHG"]), int(r["FTAG"])
    except (KeyError, TypeError, ValueError):
        return None
    return {"Date": d, "HomeTeam": r["HomeTeam"], "AwayTeam": r["AwayTeam"], "FTHG": hg, "FTAG": ag,
            "Result": r.get("Result") or ("H" if hg > ag else "A" if hg < ag else "D"),
            "league": r.get("league") or ""}


def kicked_off(p: Dict[str, Any], now: datetime) -> bool:
    try:
        return datetime.fromisoformat(f"{p['date']}T{p.get('time') or '00:00'}") <= now.replace(tzinfo=None)
    except (KeyError, ValueError):
        return True
