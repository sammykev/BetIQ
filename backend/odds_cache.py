"""
Pure cooldown-decision logic for throttling calls to The Odds API.

Kept dependency-free (no FastAPI, no redis client) so it's directly testable.
main.py owns the actual Redis/disk I/O and calls is_within_cooldown() to
decide whether a cached odds fetch is still fresh enough to reuse.
"""

from datetime import datetime
from typing import Optional

TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"


def is_within_cooldown(fetched_at_iso: Optional[str], cooldown_seconds: int,
                       now: Optional[datetime] = None) -> bool:
    """
    True if `fetched_at_iso` (a "%Y-%m-%dT%H:%M:%SZ" UTC timestamp) is recent
    enough that a fresh fetch should be skipped. False for missing/malformed
    timestamps — better to fetch than to silently skip forever on bad data.
    """
    if not fetched_at_iso:
        return False
    now = now or datetime.utcnow()
    try:
        fetched_at = datetime.strptime(fetched_at_iso, TIMESTAMP_FMT)
    except Exception:
        return False
    age = (now - fetched_at).total_seconds()
    return 0 <= age < cooldown_seconds
