"""
Where the time goes on the server: how long each API route takes to answer
(and how big its answers are), how long the event loop stalls (one uvicorn
worker serves every request *and* runs the background jobs, so a job that
holds the loop delays every page), and how long each background job runs.

Kept in memory over a rolling window and written to Redis (KEY) every
FLUSH_SECONDS for the probe and the admin page.
"""

import asyncio
import json
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List, Optional, Tuple

KEY = "betiq:perf"
WINDOW_SECONDS = 15 * 60
FLUSH_SECONDS = 120
LAG_TICK = 0.5            # the loop-lag probe sleeps this long; any overshoot is time the loop was held

_requests: Dict[str, Deque[Tuple[float, float, int]]] = defaultdict(deque)   # route -> (at, ms, bytes)
_lag: Deque[Tuple[float, float]] = deque()                                   # (at, ms late)
_jobs: Dict[str, Deque[Tuple[float, float]]] = defaultdict(deque)            # job id -> (at, seconds)


def _trim(q: Deque, now: float) -> None:
    while q and now - q[0][0] > WINDOW_SECONDS:
        q.popleft()


def record_request(route: str, ms: float, size: int) -> None:
    now = time.time()
    q = _requests[route]
    q.append((now, ms, size))
    _trim(q, now)


def record_job(job_id: str, seconds: float) -> None:
    now = time.time()
    q = _jobs[job_id]
    q.append((now, seconds))
    _trim(q, now)


def _pct(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p * (len(values) - 1))))]


def summary() -> Dict[str, Any]:
    now = time.time()
    routes = {}
    for route, q in _requests.items():
        _trim(q, now)
        if not q:
            continue
        ms = [x[1] for x in q]
        routes[route] = {"n": len(q), "p50_ms": round(_pct(ms, 0.5)), "p95_ms": round(_pct(ms, 0.95)),
                         "max_ms": round(max(ms)), "kb": round(sum(x[2] for x in q) / len(q) / 1024, 1)}
    _trim(_lag, now)
    lag = [x[1] for x in _lag]
    jobs = {}
    for job, q in _jobs.items():
        _trim(q, now)
        if q:
            s = [x[1] for x in q]
            jobs[job] = {"runs": len(q), "avg_s": round(sum(s) / len(s), 1), "max_s": round(max(s), 1)}
    return {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "window_min": WINDOW_SECONDS // 60,
        "loop_lag": {"p50_ms": round(_pct(lag, 0.5)), "p95_ms": round(_pct(lag, 0.95)),
                     "max_ms": round(max(lag)) if lag else 0,
                     "stalls_over_1s": sum(1 for x in lag if x > 1000)},
        "routes": dict(sorted(routes.items(), key=lambda kv: -kv[1]["p95_ms"] * kv[1]["n"])),
        "jobs": dict(sorted(jobs.items(), key=lambda kv: -kv[1]["max_s"])),
    }


async def watch_loop(get_redis) -> None:
    """Runs for the server's life: measures loop lag every LAG_TICK and
    writes the summary to Redis every FLUSH_SECONDS."""
    last_flush = time.monotonic()
    while True:
        start = time.monotonic()
        await asyncio.sleep(LAG_TICK)
        late = (time.monotonic() - start - LAG_TICK) * 1000
        now = time.time()
        _lag.append((now, max(0.0, late)))
        _trim(_lag, now)
        if time.monotonic() - last_flush >= FLUSH_SECONDS:
            last_flush = time.monotonic()
            r = get_redis()
            if r:
                try:
                    r.set(KEY, json.dumps(summary(), separators=(",", ":")), ex=24 * 3600)
                except Exception:
                    pass


def route_of(scope: Dict[str, Any]) -> Optional[str]:
    """The route's template ("/api/matchday"), not the raw path (ids and
    dates would make every request its own entry)."""
    route = scope.get("route")
    return getattr(route, "path", None)


class TimingMiddleware:
    """Times every API request to its last byte (ASGI, so streaming answers
    are measured whole) and notes its size before compression."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        start = time.perf_counter()
        size = [0]

        async def counted(message):
            if message.get("type") == "http.response.body":
                size[0] += len(message.get("body") or b"")
            await send(message)
        try:
            await self.app(scope, receive, counted)
        finally:
            route = route_of(scope)
            if route and route.startswith("/api"):
                record_request(f"{scope.get('method', 'GET')} {route}", (time.perf_counter() - start) * 1000, size[0])
