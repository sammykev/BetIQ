"""
Are the stored football scores right? For each day (yesterday and the day
before, or dates given), every match on the site with its stored result
and where that came from, next to what ESPN and SportyBet say now. Lists
the disagreements first. Reads only.

    python score_check.py [YYYY-MM-DD ...]   (GitHub Actions: "Basketball data", job scores)
"""

import asyncio
import json
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

import httpx

import matchday
import model_store
import results_feed

MD_KEY = "betiq:md:{}"


def _score(res: Optional[Dict]) -> str:
    if not res:
        return "—"
    s = f"{res.get('hg')}-{res.get('ag')}" if res.get("hg") is not None else "no score"
    return f"{s} {res.get('status')}" + (f" {res['minute']}'" if res.get("minute") else "") + (" aet" if res.get("aet") else "")


async def _sources(entries: Dict[str, Dict], d: str) -> Dict[str, List[Dict]]:
    leagues = {e.get("league") or "" for e in entries.values()}
    slugs = {s for lg in leagues for s in results_feed.slugs_for(lg)}
    day = date.fromisoformat(d)
    out: Dict[str, List[Dict]] = {}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        got = await results_feed.fetch_espn(c, [day - timedelta(days=1), day, day + timedelta(days=1)], slugs)
        out["espn"] = got["results"]
        if got["errors"]:
            print(f"  ESPN errors: {got['errors'][:5]}")
    try:
        out["sportybet"] = await results_feed.fetch_sportybet_results(d)
    except Exception as e:
        print(f"  SportyBet: {type(e).__name__}: {e}")
        out["sportybet"] = []
    return out


def check_day(r, d: str) -> None:
    raw = r.get(MD_KEY.format(d))
    entries = json.loads(raw) if raw else {}
    print(f"\n=== {d}: {len(entries)} matches on the site ===")
    if not entries:
        return
    src = asyncio.run(_sources(entries, d))
    print(f"  ESPN results: {len(src['espn'])} · SportyBet results: {len(src['sportybet'])}")
    found = {name: dict(matchday.match_results(entries, rows)) for name, rows in src.items()}
    rows = []
    for k, e in entries.items():
        res = e.get("result") or {}
        ours = (res.get("hg"), res.get("ag"))
        others = {n: found[n].get(k) for n in found}
        finals = {n: (o.get("hg"), o.get("ag")) for n, o in others.items() if o and o.get("status") == "finished"}
        wrong = res.get("status") == "finished" and any(v != ours for v in finals.values())
        missing = res.get("status") != "finished" and bool(finals)
        flag = "WRONG" if wrong else "MISSING" if missing else ("none" if not finals and res.get("status") != "finished" else "ok")
        rows.append((flag, e, res, others))
    order = {"WRONG": 0, "MISSING": 1, "none": 2, "ok": 3}
    for flag, e, res, others in sorted(rows, key=lambda t: (order[t[0]], t[1].get("league") or "", t[1].get("home") or "")):
        line = (f"  {flag:<7} [{e.get('league')}] {e.get('time') or ''} {e.get('home')} v {e.get('away')}: "
                f"ours {_score(res)} (from {res.get('source') or '?'})")
        for n, o in others.items():
            line += f" · {n} {_score(o)}" + (f" [{o.get('home')} v {o.get('away')}]" if o and
                                              (o.get('home'), o.get('away')) != (e.get('home'), e.get('away')) else "")
        print(line)
    counts = {f: sum(1 for t in rows if t[0] == f) for f in order}
    print(f"  → {counts}")


def main() -> None:
    r = model_store._client()
    if r is None:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    today = datetime.now(timezone.utc).date()
    days = sys.argv[1:] or [(today - timedelta(days=i)).isoformat() for i in (1, 2)]
    for d in days:
        check_day(r, d)


if __name__ == "__main__":
    main()
