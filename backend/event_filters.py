"""
Small, dependency-free helpers for filtering sport prediction lists.
Kept out of main.py so it's testable without importing the full FastAPI app.
"""

from datetime import datetime
from typing import Dict, List


def drop_started_events(preds: List[Dict]) -> List[Dict]:
    """
    Filter out events whose scheduled start (UTC "date"/"time" fields) has
    already passed. Applied both right after fetching AND every time cached
    results are served — a long-lived response cache otherwise keeps showing
    a game as "upcoming" well after it tipped off / finished, since a filter
    applied only at fetch time reflects nothing but the moment the cache was
    written.
    """
    now = datetime.utcnow()
    kept = []
    for p in preds:
        d, t = p.get("date", ""), p.get("time", "")
        if not d or not t or t == "TBD":
            kept.append(p)  # no reliable start time — keep rather than risk dropping a real game
            continue
        try:
            start = datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M")
        except Exception:
            kept.append(p)
            continue
        if start > now:
            kept.append(p)
    return kept
