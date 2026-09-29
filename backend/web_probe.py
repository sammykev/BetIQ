"""
What the production server can see (it alone reaches SportyBet and, we
expect, football.com; GitHub's runners are refused). Run as the server's
"web_probe" job; the findings go to Redis (PROBE_KEY) and the "Basketball
data" workflow's read_probe job prints them.

1. football.com: its site, its event listing (football, tennis, table
   tennis), one event's markets, and a booking code (share) for a pick —
   whether its API is SportyBet's (same paths, Sportradar event ids).
2. SportyBet tennis and table tennis: a day's results (their fields, set
   and point scores) and the markets on a listed match.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

PROBE_KEY = "betiq:probe:web"
FOOTBALL_COM = "https://www.football.com"
SPORTS = {"football": "sr:sport:1", "tennis": "sr:sport:5", "table_tennis": "sr:sport:20"}


def _short(x: Any, n: int = 400) -> str:
    s = x if isinstance(x, str) else json.dumps(x, default=str)
    return s[:n]


async def _get(session, url: str, **kw) -> Dict[str, Any]:
    try:
        r = await session.get(url, **kw)
        body = r.text or ""
        out: Dict[str, Any] = {"url": url, "status": r.status_code, "bytes": len(body), "head": body[:300]}
        if body.lstrip().startswith("{"):
            try:
                out["json"] = r.json()
            except Exception:
                pass
        return out
    except Exception as e:
        return {"url": url, "error": str(e)[:300]}


async def _post(session, url: str, payload: Dict) -> Dict[str, Any]:
    try:
        r = await session.post(url, json=payload)
        body = r.text or ""
        return {"url": url, "status": r.status_code, "bytes": len(body), "head": body[:500]}
    except Exception as e:
        return {"url": url, "error": str(e)[:300]}


def _events(data: Any) -> List[Dict]:
    import sportybet
    found: List[Dict] = []
    sportybet._collect_events(data, found)
    return found


async def football_com() -> Dict[str, Any]:
    from curl_cffi.requests import AsyncSession
    out: Dict[str, Any] = {}
    headers = {"Accept": "application/json, text/plain, */*", "Origin": FOOTBALL_COM, "Referer": f"{FOOTBALL_COM}/ng/"}
    async with AsyncSession(impersonate="chrome131", timeout=20, headers=headers) as s:
        home = await _get(s, f"{FOOTBALL_COM}/ng/")
        out["site"] = {k: home.get(k) for k in ("status", "bytes", "error")}
        for base in (f"{FOOTBALL_COM}/api/ng", f"{FOOTBALL_COM}/api"):
            listed: Dict[str, Any] = {}
            for name, sid in SPORTS.items():
                got = await _get(s, f"{base}/factsCenter/pcUpcomingEvents",
                                 params={"sportId": sid, "marketId": "1,18,186", "pageSize": 20, "pageNum": 1,
                                         "todayGames": "false"})
                evs = _events(got.get("json", {}).get("data")) if got.get("json") else []
                listed[name] = {"status": got.get("status"), "error": got.get("error"), "events": len(evs),
                                "bizCode": (got.get("json") or {}).get("bizCode"), "head": None if evs else got.get("head"),
                                "sample": [{k: e.get(k) for k in ("eventId", "homeTeamName", "awayTeamName", "_tournament")}
                                           for e in evs[:3]],
                                "markets": sorted({str(m.get("id")) for e in evs[:10] for m in e.get("markets") or []})}
                if name == "football" and evs:
                    ev = evs[0]
                    page = await _get(s, f"{base}/factsCenter/event", params={"eventId": ev["eventId"], "productId": 3})
                    pe = _events((page.get("json") or {}).get("data")) if page.get("json") else []
                    listed["event_page"] = {"status": page.get("status"),
                                            "markets": [f"{m.get('id')}:{m.get('desc')}:{m.get('specifier') or ''}"
                                                        for m in (pe[0].get("markets") or [])[:40]] if pe else [],
                                            "head": None if pe else page.get("head")}
                    share = await _post(s, f"{base}/orders/share", {"selections": [
                        {"eventId": ev["eventId"], "marketId": "1", "outcomeId": "1"}]})
                    listed["share"] = share
            out[base] = listed
            if any(v.get("events") for k, v in listed.items() if k in SPORTS):
                break
    return out


async def sportybet_racket() -> Dict[str, Any]:
    import sportybet
    out: Dict[str, Any] = {}
    session = sportybet.shared_session()
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = datetime(yesterday.year, yesterday.month, yesterday.day, tzinfo=timezone.utc)
    for name in ("tennis", "table_tennis"):
        part: Dict[str, Any] = {}
        try:
            evs, pages, total = await sportybet._paged(session, "/factsCenter/eventResultList", {
                "pageSize": 100, "sportId": SPORTS[name], "startTime": int(start.timestamp() * 1000),
                "endTime": int((start + timedelta(days=1)).timestamp() * 1000)}, 5)
            part["results"] = {"events_in_5_pages": len(evs), "pages": pages, "total": total,
                               "keys": sorted(evs[0].keys()) if evs else [],
                               "sample": [{k: e.get(k) for k in ("homeTeamName", "awayTeamName", "matchStatus", "status",
                                                                 "setScore", "gameScore", "pointScore", "_tournament")}
                                          for e in evs[:4]]}
        except Exception as e:
            part["results"] = {"error": str(e)[:300]}
        try:
            listing, report = await sportybet.fetch_sport_events(name)
            part["listing"] = {"events": len(listing), "report": report}
            if listing:
                ev = listing[0]
                data = await sportybet._request(session, "GET", "/factsCenter/event",
                                                params={"eventId": ev["eventId"], "productId": 3})
                pe = _events(data.get("data"))
                part["markets"] = [f"{m.get('id')}:{m.get('desc')}:{m.get('specifier') or ''}:"
                                   + ",".join(f"{o.get('id')}={o.get('desc')}" for o in (m.get("outcomes") or [])[:4])
                                   for m in (pe[0].get("markets") or [])[:60]] if pe else []
        except Exception as e:
            part["listing"] = {"error": str(e)[:300]}
        out[name] = part
    return out


async def run() -> Dict[str, Any]:
    report: Dict[str, Any] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    for name, fn in (("football_com", football_com), ("sportybet_racket", sportybet_racket)):
        try:
            report[name] = await fn()
        except Exception as e:
            report[name] = {"error": str(e)[:300]}
    return report
