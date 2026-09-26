"""
A shared store for the slow parts of a match page (the AI analysis, its web
news, the model's full market breakdown), kept until the match has been
played so only the first visitor waits.

Each entry holds its value, when it was built and a fingerprint of what it
was built from (the model, the odds, the news...). A request:

- no entry yet → builds it now; visitors arriving meanwhile wait for that
  same build instead of starting their own;
- an entry built from the same inputs, younger than `fresh_for` → served;
- older, or built from inputs that have since changed (new information) →
  served straight away while a fresh build runs in the background, which
  replaces it for the next visitor. (A value can set its own `_fresh_for`.)

Entries live in Redis (so every server process and restart shares them)
until a few hours after kick-off, with an in-process copy as a fallback
when Redis isn't there.
"""

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

PREFIX = "betiq:match:"
AFTER_KICKOFF = timedelta(hours=3)
DEFAULT_TTL = timedelta(hours=12)       # kick-off unknown
MAX_TTL = timedelta(days=14)
MIN_TTL = timedelta(minutes=10)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def match_key(home: str, away: str, day: str = "") -> str:
    return f"{(day or '')[:10]}:{_norm(home)}:{_norm(away)}"


def fingerprint(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def ttl_seconds(kickoff: Optional[datetime], now: Optional[datetime] = None) -> int:
    """Until a few hours after kick-off (the result is in by then)."""
    now = now or datetime.now(timezone.utc)
    ttl = DEFAULT_TTL if kickoff is None else kickoff + AFTER_KICKOFF - now
    return int(max(MIN_TTL, min(MAX_TTL, ttl)).total_seconds())


def news_fresh_for(kickoff: Optional[datetime], now: Optional[datetime] = None) -> int:
    """How long team news counts as current: line-ups land about an hour
    before kick-off, injury news through match week."""
    now = now or datetime.now(timezone.utc)
    if kickoff is None:
        return 4 * 3600
    left = kickoff - now
    if left <= timedelta(hours=3):
        return 20 * 60
    if left <= timedelta(hours=24):
        return 2 * 3600
    return 6 * 3600


class MatchCache:
    def __init__(self, redis_getter: Callable[[], Any]):
        self._redis = redis_getter
        self._mem: Dict[str, Tuple[float, Dict]] = {}   # key → (expires, entry)
        self._locks: Dict[str, asyncio.Lock] = {}
        self._background: Dict[str, asyncio.Task] = {}

    # ── storage ──────────────────────────────────────────────────────────
    def read(self, kind: str, key: str) -> Optional[Dict]:
        k = PREFIX + kind + ":" + key
        r = self._redis()
        if r is not None:
            try:
                raw = r.get(k)
                if raw:
                    return json.loads(raw)
            except Exception:
                pass
        hit = self._mem.get(k)
        if hit and hit[0] > time.time():
            return hit[1]
        return None

    def write(self, kind: str, key: str, entry: Dict, ttl: int) -> None:
        k = PREFIX + kind + ":" + key
        r = self._redis()
        if r is not None:
            try:
                r.set(k, json.dumps(entry, separators=(",", ":"), default=str), ex=ttl)
            except Exception:
                pass
        self._mem[k] = (time.time() + ttl, entry)
        if len(self._mem) > 500:   # drop the expired
            now = time.time()
            self._mem = {a: b for a, b in self._mem.items() if b[0] > now}

    def forget(self, kind: str, key: str) -> None:
        k = PREFIX + kind + ":" + key
        r = self._redis()
        if r is not None:
            try:
                r.delete(k)
            except Exception:
                pass
        self._mem.pop(k, None)

    # ── get or build ─────────────────────────────────────────────────────
    async def _build(self, kind: str, key: str, build: Callable[[], Awaitable[Any]],
                     fp: str, ttl: int) -> Optional[Dict]:
        value = await build()
        if value is None:
            return None
        entry = {"value": value, "fp": fp, "at": time.time()}
        self.write(kind, key, entry, ttl)
        return entry

    def _refresh(self, kind: str, key: str, build, fp: str, ttl: int) -> None:
        k = kind + ":" + key
        task = self._background.get(k)
        if task is not None and not task.done():
            return

        async def run():
            try:
                async with self._locks.setdefault(k, asyncio.Lock()):
                    await self._build(kind, key, build, fp, ttl)
            except Exception as e:
                print(f"[MatchCache] {kind} refresh failed for {key}: {type(e).__name__}: {e}")
            finally:
                self._background.pop(k, None)
        self._background[k] = asyncio.create_task(run())

    async def get(self, kind: str, key: str, build: Callable[[], Awaitable[Any]], *,
                  fp: str = "", fresh_for: int = 3600, ttl: int = 12 * 3600) -> Tuple[Any, Dict]:
        """(value, info): info has `at` (when it was built, ISO), `cached`
        and `refreshing` (a newer one is being built)."""
        entry = self.read(kind, key)
        if entry is not None:
            value = entry.get("value")
            # A value can shorten its own life (a failed web search: retry soon)
            ff = value.get("_fresh_for", fresh_for) if isinstance(value, dict) else fresh_for
            stale = entry.get("fp") != fp or time.time() - entry.get("at", 0) >= ff
            if stale:
                self._refresh(kind, key, build, fp, ttl)
            return entry["value"], self._info(entry, cached=True, refreshing=stale)
        k = kind + ":" + key
        async with self._locks.setdefault(k, asyncio.Lock()):
            entry = self.read(kind, key)       # built while this one waited
            if entry is None:
                entry = await self._build(kind, key, build, fp, ttl)
                if entry is None:
                    return None, {"at": None, "cached": False, "refreshing": False}
                return entry["value"], self._info(entry, cached=False, refreshing=False)
        return entry["value"], self._info(entry, cached=True, refreshing=False)

    @staticmethod
    def _info(entry: Dict, cached: bool, refreshing: bool) -> Dict:
        at = datetime.fromtimestamp(entry.get("at", 0), timezone.utc).isoformat(timespec="seconds")
        return {"at": at, "cached": cached, "refreshing": refreshing}
