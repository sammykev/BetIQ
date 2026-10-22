"""
Expected goals (xG) for past club matches in our leagues, from SofaScore —
the data for the goals model's xG features (predictor.XG_COLS), tested by
xg_check.py before the model uses them.

Collected from GitHub Actions (collect_club_xg.py; SofaScore answers the VM
with a 403). Per league, the last SEASONS seasons' finished matches (paged
from SofaScore's season listings, the current season's newest page re-read
every run), then each match's xG, newest first: the statistics' "Expected
goals", else the shot map's summed shot xG.

Matches and progress live in Redis as one gzip'd JSON blob (~60 bytes a
match), read when the model trains (attach).
"""

import asyncio
import gzip
import json
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import international_fixtures as intl
from international_stats import CLUB_TOURNAMENTS, _get

KEY = "betiq:club_xg:v1"
CHECK_KEY = "betiq:club_xg:check"
SEASONS = 4             # the current season and the three before it
RECENT_DAYS = 3         # a match this recent without xG is tried again next run
PAUSE = 0.6             # seconds between SofaScore requests
SOFA = "https://api.sofascore.com/api/v1"
SEASONS_URL = SOFA + "/unique-tournament/{t}/seasons"
EVENTS_URL = SOFA + "/unique-tournament/{t}/season/{s}/events/last/{page}"
STATS_URL = SOFA + "/event/{id}/statistics"
SHOTMAP_URL = SOFA + "/event/{id}/shotmap"


# ── parsing ──────────────────────────────────────────────────────────────
def parse_seasons(data: Any, keep: int = SEASONS) -> List[Dict[str, Any]]:
    """[{id, name}] of a tournament's newest `keep` seasons (SofaScore lists
    them newest first)."""
    out = []
    for s in (data or {}).get("seasons") or []:
        if s.get("id") is not None:
            out.append({"id": int(s["id"]), "name": str(s.get("year") or s.get("name") or "")})
    return out[:keep]


def parse_events(data: Any, league: str) -> Tuple[Dict[str, List], bool]:
    """({event id: [league, date, home, away, None, None, hasXg]}, has next
    page) from a season listing page: finished matches only."""
    found: Dict[str, List] = {}
    for e in (data or {}).get("events") or []:
        if ((e.get("status") or {}).get("type")) != "finished" or e.get("id") is None:
            continue
        home, away = (e.get("homeTeam") or {}).get("name"), (e.get("awayTeam") or {}).get("name")
        ts = e.get("startTimestamp")
        if not home or not away or not ts:
            continue
        day = datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
        found[str(e["id"])] = [league, day, home, away, None, None, e.get("hasXg")]
    return found, bool((data or {}).get("hasNextPage"))


def parse_stats_xg(data: Any) -> Optional[Tuple[float, float]]:
    """(home xG, away xG) from an event's statistics (the whole match), or None."""
    periods = (data or {}).get("statistics") or []
    whole = next((p for p in periods if p.get("period") == "ALL"), periods[0] if periods else None)
    for group in (whole or {}).get("groups") or []:
        for item in group.get("statisticsItems") or []:
            key = str(item.get("key") or "").lower()
            name = str(item.get("name") or "").lower()
            if key != "expectedgoals" and name != "expected goals":
                continue
            try:
                return (round(float(item.get("homeValue", item.get("home"))), 3),
                        round(float(item.get("awayValue", item.get("away"))), 3))
            except (TypeError, ValueError):
                return None
    return None


def parse_shotmap_xg(data: Any) -> Optional[Tuple[float, float]]:
    """(home xG, away xG) summed over an event's shot map, or None without
    shot xG."""
    shots = (data or {}).get("shotmap") or []
    totals, seen = [0.0, 0.0], False
    for s in shots:
        try:
            xg = float(s.get("xg"))
        except (TypeError, ValueError):
            continue
        seen = True
        totals[0 if s.get("isHome") else 1] += xg
    return (round(totals[0], 3), round(totals[1], 3)) if seen else None


# ── storage ──────────────────────────────────────────────────────────────
def empty() -> Dict[str, Any]:
    # matches: {event id: [league, date, home, away, home xG, away xG, hasXg]}
    # seasons: {tournament id: {season id: {name, page, done}}}
    # no_xg: {event id: date tried}
    return {"matches": {}, "seasons": {}, "no_xg": {}, "runs": []}


def load(r) -> Dict[str, Any]:
    """The dataset from Redis (a binary client: model_store._client())."""
    if r is None:
        return empty()
    try:
        raw = r.get(KEY)
    except Exception as e:
        print(f"[ClubXG] load failed: {e}")
        return empty()
    if not raw:
        return empty()
    return {**empty(), **json.loads(gzip.decompress(raw))}


def save(r, data: Dict[str, Any]) -> int:
    blob = gzip.compress(json.dumps(data, separators=(",", ":")).encode())
    r.set(KEY, blob)
    return len(blob)


def load_check(r) -> Dict[str, Any]:
    """xg_check.py's latest verdict ({} if none)."""
    try:
        raw = r.get(CHECK_KEY) if r is not None else None
        return json.loads(raw) if raw else {}
    except Exception as e:
        print(f"[ClubXG] check not loaded: {e}")
        return {}


def summary(data: Dict[str, Any]) -> Dict[str, Any]:
    per: Dict[str, Dict[str, int]] = {}
    for m in data["matches"].values():
        row = per.setdefault(m[0], {"matches": 0, "with_xg": 0})
        row["matches"] += 1
        row["with_xg"] += m[4] is not None
    return {"leagues": per, "matches": len(data["matches"]),
            "with_xg": sum(v["with_xg"] for v in per.values()), "no_xg": len(data["no_xg"])}


# ── collection ───────────────────────────────────────────────────────────
class Blocked(Exception):
    pass


async def _fetch(session, url: str, errors: List[str], pause: float) -> Optional[Any]:
    data, status = await _get(session, url, headers=intl._SOFASCORE_HEADERS, errors=errors)
    await asyncio.sleep(pause)
    if status in (403, 429):
        raise Blocked(f"HTTP {status} for {url}")
    return data


async def list_matches(session, data: Dict[str, Any], deadline: float, errors: List[str],
                       pause: float = PAUSE) -> int:
    """New finished matches from the season listings; returns how many."""
    added = 0
    for tid, league in CLUB_TOURNAMENTS.items():
        if time.monotonic() > deadline:
            break
        seasons = parse_seasons(await _fetch(session, SEASONS_URL.format(t=tid), errors, pause))
        known = data["seasons"].setdefault(str(tid), {})
        for i, s in enumerate(seasons):
            prog = known.setdefault(str(s["id"]), {"name": s["name"], "page": 0, "done": False})

            async def read(page: int) -> Optional[bool]:
                """Adds a listing page's matches; whether there's an older page."""
                nonlocal added
                listing = await _fetch(session, EVENTS_URL.format(t=tid, s=s["id"], page=page), errors, pause)
                if listing is None:
                    return None
                found, more = parse_events(listing, league)
                for eid, row in found.items():
                    if eid not in data["matches"]:
                        data["matches"][eid] = row
                        added += 1
                return more

            # The current season's newest two pages every run (new results;
            # they push older matches to later pages, so the backfill below
            # never skips any), then on back from where the backfill stopped
            if i == 0 and prog["page"] > 0:
                for page in (0, 1):
                    if not await read(page):
                        break
            while not prog["done"] and time.monotonic() < deadline:
                more = await read(prog["page"])
                if more is None:
                    break
                prog["page"] += 1
                prog["done"] = not more
    return added


async def fetch_xg(session, data: Dict[str, Any], deadline: float, today: date, errors: List[str],
                   pause: float = PAUSE) -> Dict[str, int]:
    """xG for listed matches without it, newest first, until the deadline."""
    todo = sorted((eid for eid, m in data["matches"].items()
                   if m[4] is None and eid not in data["no_xg"] and m[6] is not False),
                  key=lambda eid: data["matches"][eid][1], reverse=True)
    done = {"tried": 0, "stats": 0, "shotmap": 0, "none": 0}
    recent = (today - timedelta(days=RECENT_DAYS)).isoformat()
    for eid in todo:
        if time.monotonic() > deadline:
            break
        done["tried"] += 1
        m = data["matches"][eid]
        xg = parse_stats_xg(await _fetch(session, STATS_URL.format(id=eid), errors, pause))
        if xg:
            done["stats"] += 1
        else:
            xg = parse_shotmap_xg(await _fetch(session, SHOTMAP_URL.format(id=eid), errors, pause))
            done["shotmap"] += bool(xg)
        if xg:
            m[4], m[5] = xg
        else:
            done["none"] += 1
            if m[1] < recent:
                data["no_xg"][eid] = today.isoformat()
    return done


async def run(r, minutes: float, today: Optional[date] = None, session=None) -> Dict[str, Any]:
    """One collection run: new matches from the listings (at most a third of
    the time), then their xG; saved even if SofaScore blocks us midway."""
    today = today or datetime.now(timezone.utc).date()
    data = load(r)
    own = session is None
    if own:
        from curl_cffi.requests import AsyncSession
        session = AsyncSession(impersonate=intl.IMPERSONATE, timeout=30)
    started = time.monotonic()
    deadline = started + minutes * 60
    errors: List[str] = []
    report: Dict[str, Any] = {"at": datetime.now(timezone.utc).isoformat()}
    try:
        report["listed"] = await list_matches(session, data, started + minutes * 20, errors)
        report["xg"] = await fetch_xg(session, data, deadline, today, errors)
    except Blocked as e:
        report["blocked"] = str(e)
    finally:
        if own:
            await session.close()
    report["errors"] = errors[-5:]
    report["seconds"] = round(time.monotonic() - started)
    report.update({k: v for k, v in summary(data).items() if k != "leagues"})
    data["runs"] = (data.get("runs") or [])[-19:] + [report]
    if r is not None:
        report["bytes"] = save(r, data)
    return report


# ── use ──────────────────────────────────────────────────────────────────
def attach(matches, data: Dict[str, Any]):
    """`matches` (league CSV rows: league, Date, HomeTeam, AwayTeam) with
    HXG/AXG columns from the dataset, NaN where there's none. SofaScore's
    names are matched to each league's CSV names; a match is the same
    league, home or away team, within a day (time zones)."""
    import numpy as np
    from team_names import TeamResolver

    out = matches.copy()
    out["HXG"], out["AXG"] = np.nan, np.nan
    if out.empty or "league" not in out.columns:
        return out
    by_key: Dict[Tuple[str, str, str], Tuple[float, float]] = {}
    for lg in {m[0] for m in data["matches"].values()}:
        rows = out[out["league"] == lg]
        known = set(rows["HomeTeam"].dropna()) | set(rows["AwayTeam"].dropna())
        if not known:
            continue
        resolver = TeamResolver(known)
        for m in data["matches"].values():
            if m[0] != lg or m[4] is None:
                continue
            home, away = resolver.resolve(m[2]), resolver.resolve(m[3])
            if home in known:
                by_key[(lg, m[1], "H:" + home)] = (m[4], m[5])
            if away in known:
                by_key[(lg, m[1], "A:" + away)] = (m[4], m[5])
    if not by_key:
        return out
    hxg, axg = out["HXG"].to_numpy(copy=True), out["AXG"].to_numpy(copy=True)
    for i, (lg, day, home, away) in enumerate(zip(out["league"], out["Date"], out["HomeTeam"], out["AwayTeam"])):
        if day is None or day != day:
            continue
        d = day.date() if hasattr(day, "date") else day
        for off in (0, -1, 1):
            ds = (d + timedelta(days=off)).isoformat()
            hit = by_key.get((lg, ds, "H:" + str(home))) or by_key.get((lg, ds, "A:" + str(away)))
            if hit:
                hxg[i], axg[i] = hit
                break
    out["HXG"], out["AXG"] = hxg, axg
    return out


def coverage(attached) -> Dict[str, Any]:
    """Per league: matches, and how many have xG attached (seasons we collect)."""
    rows = attached[attached["league"].isin(set(CLUB_TOURNAMENTS.values()))]
    per = {}
    for lg, g in rows.groupby("league"):
        recent = g[g["Date"] >= g["Date"].max() - timedelta(days=365 * SEASONS)]
        per[lg] = {"matches": int(len(recent)), "with_xg": int(recent["HXG"].notna().sum())}
    return per


def unmatched_teams(matches, data: Dict[str, Any], limit: int = 20) -> Dict[str, List[str]]:
    """SofaScore team names no CSV name resolves to, per league (to fix aliases)."""
    from team_names import TeamResolver
    out: Dict[str, List[str]] = {}
    for lg in {m[0] for m in data["matches"].values()}:
        rows = matches[matches["league"] == lg]
        known = set(rows["HomeTeam"].dropna()) | set(rows["AwayTeam"].dropna())
        resolver = TeamResolver(known)
        miss = Counter(n for m in data["matches"].values() if m[0] == lg
                       for n in (m[2], m[3]) if resolver.resolve(n) not in known)
        if miss:
            out[lg] = [n for n, _ in miss.most_common(limit)]
    return out
