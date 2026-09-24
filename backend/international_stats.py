"""
Corners and cards for past international matches — the training data for
the international corners/bookings model (set_pieces.fit_international).

Two sources, run from GitHub Actions (collect_international_stats.py):

- SofaScore: each day's national-team matches (the same filter as the
  fixtures, international_fixtures.sofascore_internationals), then each
  finished match's statistics. The backfill walks back from yesterday to
  START a chunk at a time, newest first; the last few days are re-checked
  every run (stats arrive after the final whistle).
- API-Football (APIFOOTBALL_KEY): fills matches SofaScore had no stats
  for, within a daily request budget (the free plan allows 100).

Rows and progress live in Redis as one gzip'd JSON blob (small: ~100 bytes
a match), read by the API server when it fits the model.
"""

import asyncio
import gzip
import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import international_fixtures as intl

KEY = "betiq:intl_stats:v1"
START = date(2019, 1, 1)
RECHECK_DAYS = 3
SOFA_DAY = "https://api.sofascore.com/api/v1/sport/football/scheduled-events/{day}"
SOFA_STATS = "https://api.sofascore.com/api/v1/event/{id}/statistics"
AF_BASE = "https://v3.football.api-sports.io"
SOFA_PAUSE = 0.8  # seconds between SofaScore requests


# ── parsing ──────────────────────────────────────────────────────────────
def parse_sofa_stats(data: Any) -> Optional[Dict[str, int]]:
    """{HC, AC, HY, AY, HR, AR} from SofaScore's event statistics (the whole
    match), or None without corners and yellow cards. SofaScore leaves out
    red cards when there were none."""
    periods = (data or {}).get("statistics") or []
    whole = next((p for p in periods if p.get("period") == "ALL"), periods[0] if periods else None)
    if not whole:
        return None
    found: Dict[str, Tuple[int, int]] = {}
    names = {"cornerkicks": "C", "corner kicks": "C", "yellowcards": "Y", "yellow cards": "Y",
             "redcards": "R", "red cards": "R"}
    for group in whole.get("groups") or []:
        for item in group.get("statisticsItems") or []:
            stat = names.get(str(item.get("key") or "").lower()) or names.get(str(item.get("name") or "").lower())
            if not stat:
                continue
            try:
                found[stat] = (int(item.get("homeValue", item.get("home"))), int(item.get("awayValue", item.get("away"))))
            except (TypeError, ValueError):
                continue
    if "C" not in found or "Y" not in found:
        return None
    r = found.get("R", (0, 0))
    return {"HC": found["C"][0], "AC": found["C"][1], "HY": found["Y"][0], "AY": found["Y"][1],
            "HR": r[0], "AR": r[1]}


def parse_af_fixtures(data: Any) -> List[Dict[str, Any]]:
    """API-Football fixtures that are national-team matches (country "World")."""
    out = []
    for f in (data or {}).get("response") or []:
        league, teams = f.get("league") or {}, f.get("teams") or {}
        if league.get("country") != "World":
            continue
        out.append({"id": (f.get("fixture") or {}).get("id"), "league": league.get("name") or "",
                    "home": (teams.get("home") or {}).get("name") or "", "away": (teams.get("away") or {}).get("name") or "",
                    "home_id": (teams.get("home") or {}).get("id"), "away_id": (teams.get("away") or {}).get("id")})
    return out


def parse_af_stats(data: Any, home_id: Any, away_id: Any) -> Optional[Dict[str, int]]:
    """{HC, AC, HY, AY, HR, AR} from API-Football's fixture statistics."""
    by_team: Dict[Any, Dict[str, Any]] = {}
    for team in (data or {}).get("response") or []:
        by_team[(team.get("team") or {}).get("id")] = {s.get("type"): s.get("value") for s in team.get("statistics") or []}
    if home_id not in by_team or away_id not in by_team:
        return None

    def val(team, name, required=True):
        v = by_team[team].get(name)
        if v is None:
            return None if required else 0
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    row = {"HC": val(home_id, "Corner Kicks"), "AC": val(away_id, "Corner Kicks"),
           "HY": val(home_id, "Yellow Cards"), "AY": val(away_id, "Yellow Cards"),
           "HR": val(home_id, "Red Cards", False), "AR": val(away_id, "Red Cards", False)}
    return row if all(v is not None for v in row.values()) else None


def match_key(day: str, home: str, away: str) -> str:
    return f"{day}|{intl.team_key(home)}|{intl.team_key(away)}"


# ── storage ──────────────────────────────────────────────────────────────
def empty() -> Dict[str, Any]:
    return {"rows": {}, "sofa_days": [], "af_tried": [], "runs": []}


def load(r) -> Dict[str, Any]:
    """The dataset from Redis (a binary client: model_store._client())."""
    if r is None:
        return empty()
    try:
        raw = r.get(KEY)
    except Exception as e:
        print(f"[IntlStats] load failed: {e}")
        return empty()
    if not raw:
        return empty()
    data = json.loads(gzip.decompress(raw))
    return {**empty(), **data}


def save(r, data: Dict[str, Any]) -> int:
    blob = gzip.compress(json.dumps(data, separators=(",", ":")).encode())
    r.set(KEY, blob)
    return len(blob)


def rows_frame(data: Dict[str, Any]):
    """Rows as a DataFrame in the league CSVs' shape (set_pieces reads it):
    teams by their one-per-nation key, league = our competition code."""
    import pandas as pd
    rows = [{"Date": pd.Timestamp(r["date"]), "HomeTeam": intl.team_key(r["home"]), "AwayTeam": intl.team_key(r["away"]),
             "league": intl.competition(r.get("competition", ""))[0],
             **{k: r[k] for k in ("HC", "AC", "HY", "AY", "HR", "AR")}}
            for r in data.get("rows", {}).values()]
    return pd.DataFrame(rows).sort_values("Date").reset_index(drop=True) if rows else pd.DataFrame()


def summary(data: Dict[str, Any]) -> Dict[str, Any]:
    rows = list(data.get("rows", {}).values())
    days = sorted(data.get("sofa_days") or [])
    sources: Dict[str, int] = {}
    for r in rows:
        sources[r.get("source", "?")] = sources.get(r.get("source", "?"), 0) + 1
    return {"matches": len(rows), "sources": sources,
            "first": min((r["date"] for r in rows), default=None), "last": max((r["date"] for r in rows), default=None),
            "days_scanned": len(days), "oldest_day_scanned": days[0] if days else None,
            "backfill_complete": bool(days) and days[0] <= START.isoformat(),
            "last_run": (data.get("runs") or [None])[-1]}


# ── collection ───────────────────────────────────────────────────────────
async def _get(session, url: str, headers: Optional[Dict] = None, params: Optional[Dict] = None) -> Tuple[Optional[Any], Optional[int]]:
    try:
        r = await session.get(url, headers=headers, params=params, timeout=30)
    except Exception:
        return None, None
    if r.status_code != 200:
        return None, r.status_code
    try:
        return r.json(), 200
    except ValueError:
        return None, r.status_code


def days_to_scan(data: Dict[str, Any], today: date) -> List[date]:
    """The last RECHECK_DAYS days, then every unscanned day back to START, newest first."""
    done = set(data.get("sofa_days") or [])
    recent = [today - timedelta(days=i) for i in range(1, RECHECK_DAYS + 1)]
    older, d = [], today - timedelta(days=RECHECK_DAYS + 1)
    while d >= START:
        if d.isoformat() not in done:
            older.append(d)
        d -= timedelta(days=1)
    return recent + older


async def collect_sofascore(session, data: Dict[str, Any], deadline: float, today: date,
                            pause: float = SOFA_PAUSE) -> Dict[str, Any]:
    """Scan days (days_to_scan) until the deadline. Returns this run's counts."""
    headers = intl._SOFASCORE_HEADERS
    done = set(data.get("sofa_days") or [])
    report = {"days": 0, "matches": 0, "no_stats": 0, "stopped": None}
    missing: List[Dict[str, Any]] = data.setdefault("missing", [])
    for day in days_to_scan(data, today):
        if time.monotonic() > deadline:
            report["stopped"] = "time"
            break
        listing, status = await _get(session, SOFA_DAY.format(day=day.isoformat()), headers)
        await asyncio.sleep(pause)
        if listing is None:
            if status in (403, 429):  # blocked or throttled: stop, try again next run
                report["stopped"] = f"HTTP {status}"
                break
            continue
        for ev, home, away, comp, kickoff in intl.sofascore_internationals(listing):
            status_type = (ev.get("status") or {}).get("type")
            key = match_key(kickoff.date().isoformat(), home, away)
            if status_type != "finished" or key in data["rows"]:
                continue
            stats, st = await _get(session, SOFA_STATS.format(id=ev.get("id")), headers)
            await asyncio.sleep(pause)
            row = parse_sofa_stats(stats) if stats else None
            if row:
                data["rows"][key] = {"date": kickoff.date().isoformat(), "home": home, "away": away,
                                     "competition": comp, "source": "sofascore", "id": ev.get("id"), **row}
                report["matches"] += 1
            else:
                report["no_stats"] += 1
                if key not in {m["key"] for m in missing}:
                    missing.append({"key": key, "date": kickoff.date().isoformat(), "home": home, "away": away,
                                    "competition": comp})
        done.add(day.isoformat())
        report["days"] += 1
    data["sofa_days"] = sorted(done)
    data["missing"] = [m for m in missing if m["key"] not in data["rows"]]
    return report


async def collect_api_football(session, data: Dict[str, Any], api_key: str, budget: int) -> Dict[str, Any]:
    """Fill matches SofaScore had no stats for, newest first, within `budget` requests."""
    from difflib import SequenceMatcher
    headers = {"x-apisports-key": api_key}
    tried = set(data.get("af_tried") or [])
    report = {"requests": 0, "matches": 0, "stopped": None}
    by_date: Dict[str, List[Dict]] = {}
    todo = sorted((m for m in data.get("missing", []) if m["key"] not in tried and m["key"] not in data["rows"]),
                  key=lambda m: m["date"], reverse=True)

    def sim(a: str, b: str) -> float:
        return SequenceMatcher(None, intl.team_key(a), intl.team_key(b)).ratio()

    for m in todo:
        if report["requests"] >= budget:
            report["stopped"] = "budget"
            break
        if m["date"] not in by_date:
            listing, status = await _get(session, f"{AF_BASE}/fixtures", headers, {"date": m["date"]})
            report["requests"] += 1
            if listing is None or (listing.get("errors") and not listing.get("response")):
                report["stopped"] = f"fixtures: {status} {listing.get('errors') if listing else ''}".strip()
                break
            by_date[m["date"]] = parse_af_fixtures(listing)
        best = max(by_date[m["date"]], key=lambda f: min(sim(f["home"], m["home"]), sim(f["away"], m["away"])), default=None)
        if not best or min(sim(best["home"], m["home"]), sim(best["away"], m["away"])) < 0.75:
            tried.add(m["key"])  # API-Football doesn't have it either
            continue
        if report["requests"] >= budget:
            report["stopped"] = "budget"
            break
        stats, _ = await _get(session, f"{AF_BASE}/fixtures/statistics", headers, {"fixture": best["id"]})
        report["requests"] += 1
        row = parse_af_stats(stats, best["home_id"], best["away_id"]) if stats else None
        tried.add(m["key"])
        if row:
            data["rows"][m["key"]] = {"date": m["date"], "home": m["home"], "away": m["away"],
                                      "competition": m["competition"], "source": "api-football", "id": best["id"], **row}
            report["matches"] += 1
    data["af_tried"] = sorted(tried)
    data["missing"] = [m for m in data.get("missing", []) if m["key"] not in data["rows"]]
    return report


async def run(r, minutes: float, api_key: str = "", af_budget: int = 90,
              today: Optional[date] = None, session=None) -> Dict[str, Any]:
    """One collection run: SofaScore until the time's up, then API-Football."""
    today = today or datetime.now(timezone.utc).date()
    data = load(r)
    if session is None:
        from curl_cffi.requests import AsyncSession
        session = AsyncSession(impersonate=intl.IMPERSONATE, timeout=30)
    started = time.monotonic()
    report: Dict[str, Any] = {"at": datetime.now(timezone.utc).isoformat()}
    report["sofascore"] = await collect_sofascore(session, data, started + minutes * 60, today)
    if api_key:
        report["api_football"] = await collect_api_football(session, data, api_key, af_budget)
    report["seconds"] = round(time.monotonic() - started)
    report["total_matches"] = len(data["rows"])
    data["runs"] = (data.get("runs") or [])[-19:] + [report]
    if r is not None:
        report["bytes"] = save(r, data)
    return report
