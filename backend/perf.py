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
import os
import sys
import threading
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List, Optional, Tuple

KEY = "betiq:perf"
WINDOW_SECONDS = 15 * 60
FLUSH_SECONDS = 120
LAG_TICK = 0.1            # the loop-lag probe sleeps this long; any overshoot is time the loop was held

_requests: Dict[str, Deque[Tuple[float, float, int]]] = defaultdict(deque)   # route -> (at, ms, bytes)
_lag: Deque[Tuple[float, float]] = deque()                                   # (at, ms late)
_jobs: Dict[str, Deque[Tuple[float, float]]] = defaultdict(deque)            # job id -> (at, seconds)
# What holds the loop: while it's stuck, a watchdog thread samples the loop
# thread's stack every WATCH_EVERY; each sample counts under the code line
# that was running (≈ WATCH_EVERY seconds of blocking each)
WATCH_EVERY = 0.2
STUCK_AFTER = 0.5
_beat = [time.monotonic()]
_loop_thread: List[Optional[int]] = [None]
_blocking: Dict[str, int] = defaultdict(int)
# Long stalls (over STALL_NOTE s): when, how long, and the process's CPU time
# in them (≈ the stall: code running; ≈ 0: the process was paused, e.g. swap),
# and the longest the watchdog itself overslept (it can't run then either)
STALL_NOTE = 5.0
_stalls: Deque[Dict[str, Any]] = deque(maxlen=12)
_watchdog_late = [0.0]
_HERE = os.path.dirname(os.path.abspath(__file__))


def _where(frame) -> str:
    """The innermost lines of our own code on a stack (and the library call
    it was in), e.g. "main.py:5620 _rk_live_tick > basketball_data.py:88 encode | zlib"."""
    ours, lib = [], None
    while frame is not None:
        code = frame.f_code
        if code.co_filename.startswith(_HERE) and "site-packages" not in code.co_filename:
            ours.append(f"{os.path.basename(code.co_filename)}:{frame.f_lineno} {code.co_name}")
        elif lib is None and not ours:
            lib = f"{os.path.basename(code.co_filename)}:{code.co_name}"
        frame = frame.f_back
    return " > ".join(reversed(ours[:3])) + (f" | {lib}" if lib else "")


def _watchdog() -> None:
    while True:
        before = time.monotonic()
        time.sleep(WATCH_EVERY)
        _watchdog_late[0] = max(_watchdog_late[0], time.monotonic() - before - WATCH_EVERY)
        tid = _loop_thread[0]
        if tid is None or time.monotonic() - _beat[0] < STUCK_AFTER:
            continue
        frame = sys._current_frames().get(tid)
        if frame is not None:
            _blocking[_where(frame)] += 1


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


def _recent(q: Deque, now: float) -> list:
    # list() copies in one step (safe while the loop appends); no trimming
    # here, as this runs off the loop
    return [x for x in list(q) if now - x[0] <= WINDOW_SECONDS]


def _memory() -> Dict[str, Any]:
    """The process's memory and the machine's free memory (MB), from /proc."""
    out: Dict[str, Any] = {}
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith(("VmRSS:", "VmHWM:", "VmSwap:")):
                    out[line.split(":")[0]] = round(int(line.split()[1]) / 1024)
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith(("MemTotal:", "MemAvailable:", "SwapTotal:", "SwapFree:")):
                    out[line.split(":")[0]] = round(int(line.split()[1]) / 1024)
    except OSError:
        pass
    return out


def summary() -> Dict[str, Any]:
    now = time.time()
    routes = {}
    for route, q in list(_requests.items()):
        q = _recent(q, now)
        if not q:
            continue
        ms = [x[1] for x in q]
        routes[route] = {"n": len(q), "p50_ms": round(_pct(ms, 0.5)), "p95_ms": round(_pct(ms, 0.95)),
                         "max_ms": round(max(ms)), "kb": round(sum(x[2] for x in q) / len(q) / 1024, 1)}
    lag = [x[1] for x in _recent(_lag, now)]
    jobs = {}
    for job, q in list(_jobs.items()):
        q = _recent(q, now)
        if q:
            s = [x[1] for x in q]
            jobs[job] = {"runs": len(q), "avg_s": round(sum(s) / len(s), 1), "max_s": round(max(s), 1)}
    top = sorted(list(_blocking.items()), key=lambda kv: -kv[1])[:25]
    return {
        "blocking_s": {k: round(n * WATCH_EVERY, 1) for k, n in top},
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "window_min": WINDOW_SECONDS // 60,
        "loop_lag": {"p50_ms": round(_pct(lag, 0.5)), "p95_ms": round(_pct(lag, 0.95)),
                     "max_ms": round(max(lag)) if lag else 0,
                     "stalls_over_1s": sum(1 for x in lag if x > 1000)},
        "routes": dict(sorted(routes.items(), key=lambda kv: -kv[1]["p95_ms"] * kv[1]["n"])),
        "jobs": dict(sorted(jobs.items(), key=lambda kv: -kv[1]["max_s"])),
        "stalls": list(_stalls),
        "watchdog_late_s": round(_watchdog_late[0], 1),
        "memory_mb": _memory(),
    }


def _flush(get_redis) -> None:
    r = get_redis()
    if r:
        try:
            r.set(KEY, json.dumps(summary(), separators=(",", ":")), ex=24 * 3600)
        except Exception:
            pass


async def watch_loop(get_redis) -> None:
    """Runs for the server's life: measures loop lag every LAG_TICK and
    writes the summary to Redis every FLUSH_SECONDS."""
    last_flush = time.monotonic()
    _loop_thread[0] = threading.get_ident()
    threading.Thread(target=_watchdog, name="perf-watchdog", daemon=True).start()
    while True:
        start, cpu = time.monotonic(), time.process_time()
        _beat[0] = start
        await asyncio.sleep(LAG_TICK)
        _beat[0] = time.monotonic()
        late = (time.monotonic() - start - LAG_TICK) * 1000
        now = time.time()
        _lag.append((now, max(0.0, late)))
        _trim(_lag, now)
        if late >= STALL_NOTE * 1000:
            _stalls.append({"at": time.strftime("%H:%M:%S", time.gmtime(now - late / 1000)),
                            "s": round(late / 1000, 1), "cpu_s": round(time.process_time() - cpu, 1)})
        if time.monotonic() - last_flush >= FLUSH_SECONDS:
            last_flush = time.monotonic()
            # Off the loop: the summary and the Redis write take a moment
            asyncio.get_running_loop().run_in_executor(None, _flush, get_redis)


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
