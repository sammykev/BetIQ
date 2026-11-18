"""
SportyBet's weekly jackpot (jackpot.py): this week's analysis and the
record of past weeks, as the Jackpot workflow stored them.
"""

import json
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends

import main

router = APIRouter()
_cache: Dict[str, Any] = {"at": 0.0, "data": None}
CACHE_SECONDS = 120


def record(history: Dict[str, Dict]) -> Dict[str, Any]:
    """Past weeks, newest first, with how many the favourites and our picks
    got right, and the averages over the weeks scored."""
    weeks: List[Dict[str, Any]] = []
    for period, h in sorted(history.items(), reverse=True):
        if h.get("results") is None and h.get("favourites_right") is None:
            continue
        weeks.append({"period": period, "results": h.get("results"),
                      "favourites_right": h.get("favourites_right"), "ours_right": h.get("ours_right")})
    fav = [w["favourites_right"] for w in weeks if isinstance(w["favourites_right"], int)]
    ours = [w["ours_right"] for w in weeks if isinstance(w["ours_right"], int)]
    avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None
    return {"weeks": weeks, "favourites_avg": avg(fav), "favourites_best": max(fav) if fav else None,
            "ours_avg": avg(ours), "ours_weeks": len(ours), "weeks_scored": len(fav)}


def _load() -> Optional[Dict[str, Any]]:
    if _cache["data"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["data"]
    import jackpot
    r = main._get_redis()
    try:
        raw = r.get(jackpot.KEY) if r else None
        data = json.loads(raw) if raw else None
    except Exception:
        data = None
    _cache.update(at=time.time(), data=data)
    return data


@router.get("/api/jackpot")
async def get_jackpot(_access=Depends(main.require_feature("jackpot"))):
    """This week's jackpot: each match's chances, the best ticket per number
    of lines and each prize's chance, and how past weeks went."""
    data = _load()
    if not data:
        return {"current": None, "record": record({}), "at": None}
    return {"current": data.get("current"), "banner": data.get("banner"), "at": data.get("at"),
            "record": record(data.get("history") or {})}
