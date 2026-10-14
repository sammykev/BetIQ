"""
Final scores for basketball, tennis and table tennis legs that SportyBet's
results feed never gives a result for, from Sportradar's match info (the
same ids: SportyBet's event ids are "sr:match:N").

    https://stats.fn.sportradar.com/common/en/Etc:UTC/gismo/match_info/N

answers {"doc": [{"data": {"match": {"result": {"home", "away"}, "periods":
{"p1": {"home", "away"}, …, "ot": {…}}, "status": {"name"}, "canceled",
"postponed", "retired", "walkover", …}}}]}. For basketball the periods are
quarters; for tennis and table tennis, the games (points) of each set.

Grading never waits on this: a leg the grader finds no result for is
noted (want()); the sweep reads the noted matches (refresh()) and keeps
the finals (KEY), which the grader reads next time (stored()).
"""

import asyncio
import json
import time
from typing import Any, Callable, Dict, Optional

import live_stats

URL = "https://stats.fn.sportradar.com/common/en/Etc:UTC/gismo/match_info/{num}"
KEY = "betiq:sr:results"          # hash {event id: settle-format result}
WANTED_KEY = "betiq:sr:wanted"    # hash {event id: {"sport", "since", "tries"}}
TTL = 45 * 86400
MAX_TRIES = 40                    # reads before a match is given up (the sweep runs every 3h: ~5 days)
GIVE_UP_SECONDS = 30 * 86400
AT_ONCE = 6
MAX_PER_RUN = 60


def _pair(v: Any) -> Optional[tuple]:
    try:
        return int(v["home"]), int(v["away"])
    except (KeyError, TypeError, ValueError):
        return None


def _periods(m: Dict) -> Dict[str, tuple]:
    out = {}
    for k, v in (m.get("periods") or {}).items():
        p = _pair(v)
        if p is not None:
            out[str(k)] = p
    return out


def _numbered(ps: Dict[str, tuple]) -> list:
    nums = sorted((int(k[1:]), v) for k, v in ps.items() if k[:1] == "p" and k[1:].isdigit())
    return [list(v) for _, v in nums]


ENDED = {"ended", "aet", "aot", "ap", "after overtime", "after extra time", "finished",
         "retired", "walkover", "ft"}


def _ended(m: Dict) -> bool:
    st = m.get("status")
    names = [st.get("name") if isinstance(st, dict) else st, m.get("matchstatus")]
    return any(str(n or "").strip().lower() in ENDED or str(n or "").strip().lower().startswith("ended")
               for n in names)


def parse(body: Any, sport: str) -> Optional[Dict]:
    """The match's final in the grader's format for its sport; None when it
    isn't over (or was called off: those legs stay with the other sources)."""
    data = live_stats.doc_data(body)
    m = data.get("match") if isinstance(data, dict) else None
    if not isinstance(m, dict) or m.get("canceled") or m.get("postponed"):
        return None
    if not _ended(m):
        return None
    final = _pair(m.get("result") or {})
    ps = _periods(m)
    if sport == "basketball":
        if final is None:
            return None
        q = _numbered(ps)[:4]
        reg = (sum(h for h, _ in q), sum(a for _, a in q)) if len(q) == 4 else None
        ot = "ot" in ps or (reg is not None and reg != final) or (reg is None and final[0] == final[1])
        return {"status": "finished", "final": list(final), "periods": q, "ot": bool(ot)}
    # Tennis, table tennis: sets won from the result, games from each set
    if final is None:
        return None
    games = _numbered(ps)
    ret = bool(m.get("retired") or m.get("walkover"))
    if not ret and games and len(games) != final[0] + final[1]:
        games = None
    return {"status": "finished", "sets": list(final), "games": games or None, "ret": ret}


def _json(raw) -> Optional[Dict]:
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def stored(r, event_id: Optional[str]) -> Optional[Dict]:
    if not r or not event_id:
        return None
    try:
        return _json(r.hget(KEY, event_id))
    except Exception:
        return None


def want(r, event_id: Optional[str], sport: str) -> None:
    """Note a match whose result the grader couldn't find (once)."""
    if not r or not str(event_id or "").startswith("sr:") or not live_stats.sr_number(event_id):
        return
    try:
        r.hsetnx(WANTED_KEY, event_id, json.dumps({"sport": sport, "since": int(time.time()), "tries": 0}))
    except Exception:
        pass


async def _read(num: str, get: Optional[Callable] = None) -> Any:
    try:
        if get is not None:
            return await get(URL.format(num=num))
        resp = await asyncio.wait_for(live_stats._http().get(URL.format(num=num)), live_stats.TIMEOUT)
        return resp.json() if resp.status_code == 200 else None
    except Exception:
        return None


async def refresh(r, get: Optional[Callable] = None, now: Optional[float] = None) -> Dict[str, int]:
    """Read the noted matches: finals kept, the rest tried again next time
    (given up after MAX_TRIES or GIVE_UP_SECONDS)."""
    now = time.time() if now is None else now
    raw = r.hgetall(WANTED_KEY) or {}
    wanted = {}
    for k, v in raw.items():
        k = k.decode() if isinstance(k, bytes) else k
        w = _json(v) or {}
        if now - w.get("since", now) > GIVE_UP_SECONDS or w.get("tries", 0) >= MAX_TRIES:
            r.hdel(WANTED_KEY, k)
            continue
        wanted[k] = w
    todo = sorted(wanted, key=lambda k: wanted[k].get("tries", 0))[:MAX_PER_RUN]
    sem = asyncio.Semaphore(AT_ONCE)

    async def one(eid):
        async with sem:
            return eid, await _read(live_stats.sr_number(eid), get)

    out = {"asked": len(todo), "found": 0, "unread": 0}
    for eid, body in await asyncio.gather(*(one(e) for e in todo)):
        if body is None:
            out["unread"] += 1
        res = parse(body, wanted[eid].get("sport", "basketball")) if body is not None else None
        if res:
            r.hset(KEY, eid, json.dumps(res, separators=(",", ":")))
            r.hdel(WANTED_KEY, eid)
            out["found"] += 1
        else:
            w = wanted[eid]
            r.hset(WANTED_KEY, eid, json.dumps({**w, "tries": w.get("tries", 0) + 1}))
    if out["found"]:
        r.expire(KEY, TTL)
    return out
