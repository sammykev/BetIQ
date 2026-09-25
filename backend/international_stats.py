"""
Corners and cards for past international matches — the training data for
the international corners/bookings model (set_pieces.fit_international).

Three sources, run from GitHub Actions (collect_international_stats.py):

- ESPN's public scoreboards (the main source: SofaScore answers GitHub and
  Render with a 403): each national-team competition a month at a time,
  finished matches with corners (team statistics) and cards (match
  events). Matches ESPN lists without stats go to API-Football's list.
- SofaScore: each day's national-team matches (the same filter as the
  fixtures, international_fixtures.sofascore_internationals), then each
  finished match's statistics. The backfill walks back from yesterday to
  START a chunk at a time, newest first; the last few days are re-checked
  every run (stats arrive after the final whistle).
- API-Football (APIFOOTBALL_KEY): fills matches ESPN or SofaScore listed
  without stats, within a daily request budget (the free plan allows 100).

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
SOFA_EVENT = "https://api.sofascore.com/api/v1/event/{id}"
# Our club leagues, by SofaScore uniqueTournament id: their referees (the
# league CSVs name them only for England). From CLUB_REFEREES_FROM on.
CLUB_TOURNAMENTS = {17: "PL", 18: "ELC", 8: "PD", 23: "SA", 35: "BL1", 44: "BL2", 34: "FL1", 37: "DED", 238: "PPL"}
CLUB_REFEREES_FROM = date(2021, 7, 1)
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


def parse_sofa_referee(data: Any) -> Optional[Dict[str, Any]]:
    """{name, games, yellow, red} from a SofaScore event page's referee
    (career totals at the time it's read), or None."""
    ref = ((data or {}).get("event") or {}).get("referee") or {}
    name = (ref.get("name") or "").strip()
    if not name:
        return None
    def n(k):
        try:
            return int(ref.get(k) or 0)
        except (TypeError, ValueError):
            return 0
    return {"name": name, "games": n("games"), "yellow": n("yellowCards"),
            "red": n("redCards") + n("yellowRedCards")}


def parse_af_fixtures(data: Any) -> List[Dict[str, Any]]:
    """API-Football fixtures that are national-team matches (country "World")."""
    out = []
    for f in (data or {}).get("response") or []:
        league, teams = f.get("league") or {}, f.get("teams") or {}
        if league.get("country") != "World":
            continue
        referee = ((f.get("fixture") or {}).get("referee") or "").split(",")[0].strip() or None
        out.append({"id": (f.get("fixture") or {}).get("id"), "league": league.get("name") or "", "referee": referee,
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
    # club_refs: {date|home|away (SofaScore names): referee}; referees: {name: career}
    return {"rows": {}, "sofa_days": [], "af_tried": [], "runs": [], "club_refs": {}, "referees": {}}


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
             "league": intl.competition(r.get("competition", ""))[0], "Referee": r.get("referee"),
             **{k: r[k] for k in ("HC", "AC", "HY", "AY", "HR", "AR")}}
            for r in data.get("rows", {}).values()]
    return pd.DataFrame(rows).sort_values("Date").reset_index(drop=True) if rows else pd.DataFrame()


def add_club_referees(history, club_refs: Dict[str, str]):
    """The club history (league CSV rows) with referees filled in from
    club_refs where the CSV names none. Matched by date (±1 day: SofaScore
    dates are UTC) and both team names, clearly (sportybet.team_similarity)."""
    import pandas as pd
    from sportybet import team_similarity
    if history is None or history.empty or not club_refs:
        return history
    by_day: Dict[str, List[Tuple[str, str, str]]] = {}
    for key, name in club_refs.items():
        day, _, teams = key.partition("|")
        home, _, away = teams.partition("|")
        if name and home and away:
            by_day.setdefault(day, []).append((home, away, name))
    out = history.copy()
    if "Referee" not in out.columns:
        out["Referee"] = None
    missing = out["Referee"].isna() | (out["Referee"].astype(str).str.strip() == "")
    missing &= out["Date"] >= pd.Timestamp(CLUB_REFEREES_FROM - timedelta(days=1))
    seen: Dict[Tuple[str, str], float] = {}  # few distinct names: compare each pair once

    def similar(a: str, b: str) -> float:
        if (a, b) not in seen:
            seen[(a, b)] = team_similarity(a, b)
        return seen[(a, b)]

    filled = []
    for i, row in out[missing].iterrows():
        d = pd.Timestamp(row["Date"]).date()
        best, best_score = None, 0.0
        for day in (d, d - timedelta(days=1), d + timedelta(days=1)):
            for home, away, name in by_day.get(day.isoformat(), []):
                sh, sa = similar(row["HomeTeam"], home), similar(row["AwayTeam"], away)
                if min(sh, sa) >= 0.8 and sh + sa > best_score:
                    best, best_score = name, sh + sa
        if best:
            filled.append((i, best))
    for i, name in filled:
        out.at[i, "Referee"] = name
    return out


def summary(data: Dict[str, Any]) -> Dict[str, Any]:
    rows = list(data.get("rows", {}).values())
    days = sorted(data.get("sofa_days") or [])
    sources: Dict[str, int] = {}
    for r in rows:
        sources[r.get("source", "?")] = sources.get(r.get("source", "?"), 0) + 1
    club_refs = data.get("club_refs") or {}
    espn = data.get("espn_months") or []
    # How far back the backfill has got, by either source
    espn_oldest = min((m.split("|")[1] + "-01" for m in espn
                       if all(f"{slug}|{m.split('|')[1]}" in espn for slug in intl.ESPN_COMPETITIONS)), default=None)
    oldest = min((d for d in (days[0] if days else None, espn_oldest) if d), default=None)
    return {"matches": len(rows), "sources": sources,
            "with_referee": sum(1 for r in rows if r.get("referee")),
            "club_referees": sum(1 for v in club_refs.values() if v), "referees_known": len(data.get("referees") or {}),
            "first": min((r["date"] for r in rows), default=None), "last": max((r["date"] for r in rows), default=None),
            "days_scanned": len(days), "oldest_day_scanned": oldest,
            "espn_months_scanned": len(espn),
            "backfill_complete": bool(oldest) and oldest <= START.isoformat(),
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
                referee = await _referee(session, ev.get("id"), data, pause)
                data["rows"][key] = {"date": kickoff.date().isoformat(), "home": home, "away": away,
                                     "competition": comp, "source": "sofascore", "id": ev.get("id"),
                                     "referee": referee, **row}
                report["matches"] += 1
            else:
                report["no_stats"] += 1
                if key not in {m["key"] for m in missing}:
                    missing.append({"key": key, "date": kickoff.date().isoformat(), "home": home, "away": away,
                                    "competition": comp})
        if day >= CLUB_REFEREES_FROM:
            report["club_referees"] = report.get("club_referees", 0) + await _club_referees(session, listing, data, pause)
        done.add(day.isoformat())
        report["days"] += 1
    data["sofa_days"] = sorted(done)
    data["missing"] = [m for m in missing if m["key"] not in data["rows"]]
    return report


async def _referee(session, event_id: Any, data: Dict[str, Any], pause: float) -> Optional[str]:
    """The referee of one SofaScore event (their career record kept too)."""
    page, _ = await _get(session, SOFA_EVENT.format(id=event_id), intl._SOFASCORE_HEADERS)
    await asyncio.sleep(pause)
    ref = parse_sofa_referee(page) if page else None
    if not ref:
        return None
    data.setdefault("referees", {})[ref["name"]] = {k: ref[k] for k in ("games", "yellow", "red")}
    return ref["name"]


async def _club_referees(session, listing: Dict, data: Dict[str, Any], pause: float) -> int:
    """Referees of one day's finished matches in our club leagues."""
    found = 0
    refs = data.setdefault("club_refs", {})
    for ev in (listing or {}).get("events") or []:
        tournament = ((ev.get("tournament") or {}).get("uniqueTournament") or {}).get("id")
        if tournament not in CLUB_TOURNAMENTS or (ev.get("status") or {}).get("type") != "finished":
            continue
        try:
            day = datetime.fromtimestamp(int(ev["startTimestamp"]), timezone.utc).date().isoformat()
        except (KeyError, TypeError, ValueError):
            continue
        key = f"{day}|{(ev.get('homeTeam') or {}).get('name', '')}|{(ev.get('awayTeam') or {}).get('name', '')}"
        if key in refs:
            continue
        name = await _referee(session, ev.get("id"), data, pause)
        refs[key] = name or ""  # "" = none named: don't ask again
        found += bool(name)
    return found


ESPN_RECHECK_MONTHS = 2   # this month and last are re-read every run (stats arrive late)
ESPN_PAUSE = 0.3


def espn_months(data: Dict[str, Any], today: date) -> List[Tuple[str, str, date, date]]:
    """(progress key, competition slug, first day, last day) to read,
    newest month first: the recent months always, older ones until done."""
    done = set(data.get("espn_months") or [])
    out = []
    m, i = date(today.year, today.month, 1), 0
    while m >= date(START.year, START.month, 1):
        nxt = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
        end = min(nxt - timedelta(days=1), today - timedelta(days=1))
        if end >= m:
            for slug in intl.ESPN_COMPETITIONS:
                k = f"{slug}|{m:%Y-%m}"
                if i < ESPN_RECHECK_MONTHS or k not in done:
                    out.append((k, slug, m, end))
        m = date(m.year - (m.month == 1), (m.month - 2) % 12 + 1, 1)
        i += 1
    return out


async def collect_espn(session, data: Dict[str, Any], deadline: float, today: date,
                       pause: float = ESPN_PAUSE) -> Dict[str, Any]:
    """Finished national-team matches with corners and cards from ESPN, a
    competition-month per request, newest first, until the deadline."""
    import results_feed
    report: Dict[str, Any] = {"requests": 0, "matches": 0, "no_stats": 0, "stopped": None}
    done = set(data.get("espn_months") or [])
    missing = data.setdefault("missing", [])
    missing_keys = {m["key"] for m in missing}
    for k, slug, first, last in espn_months(data, today):
        if time.monotonic() > deadline:
            report["stopped"] = "time"
            break
        page, status = await _get(session, f"{intl.ESPN_BASE}/{slug}/scoreboard", intl._ESPN_HEADERS,
                                  {"dates": f"{first:%Y%m%d}-{last:%Y%m%d}", "limit": 1000})
        report["requests"] += 1
        await asyncio.sleep(pause)
        if page is None:
            if status in (403, 429):
                report["stopped"] = f"HTTP {status}"
                break
            continue  # try this month again next run
        comp = intl.ESPN_COMPETITIONS[slug][0]
        for res in results_feed.parse_espn(page):
            if res["status"] != "finished" or res.get("aet") or res.get("hg") is None:
                continue
            key = match_key(res["date"], res["home"], res["away"])
            if key in data["rows"]:
                continue
            if res.get("corners") and res.get("bookings"):
                data["rows"][key] = {"date": res["date"], "home": res["home"], "away": res["away"],
                                     "competition": comp, "source": "espn",
                                     "HC": res["corners"][0], "AC": res["corners"][1],
                                     # Booking points (yellow 1, red 2) as yellows: the model adds HY + 2·HR
                                     "HY": res["bookings"][0], "AY": res["bookings"][1], "HR": 0, "AR": 0}
                report["matches"] += 1
            else:
                report["no_stats"] += 1
                if key not in missing_keys:
                    missing.append({"key": key, "date": res["date"], "home": res["home"], "away": res["away"],
                                    "competition": comp})
                    missing_keys.add(key)
        done.add(k)
    data["espn_months"] = sorted(done)
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
                                      "competition": m["competition"], "source": "api-football", "id": best["id"],
                                      "referee": best.get("referee"), **row}
            report["matches"] += 1
    data["af_tried"] = sorted(tried)
    data["missing"] = [m for m in data.get("missing", []) if m["key"] not in data["rows"]]
    return report


async def run(r, minutes: float, api_key: str = "", af_budget: int = 90,
              today: Optional[date] = None, session=None) -> Dict[str, Any]:
    """One collection run: SofaScore, then ESPN, until the time's up; then
    API-Football for matches listed without stats."""
    today = today or datetime.now(timezone.utc).date()
    data = load(r)
    if session is None:
        from curl_cffi.requests import AsyncSession
        session = AsyncSession(impersonate=intl.IMPERSONATE, timeout=30)
    started = time.monotonic()
    report: Dict[str, Any] = {"at": datetime.now(timezone.utc).isoformat()}
    deadline = started + minutes * 60
    report["sofascore"] = await collect_sofascore(session, data, deadline, today)
    report["espn"] = await collect_espn(session, data, deadline, today)
    if api_key:
        report["api_football"] = await collect_api_football(session, data, api_key, af_budget)
    report["seconds"] = round(time.monotonic() - started)
    report["total_matches"] = len(data["rows"])
    data["runs"] = (data.get("runs") or [])[-19:] + [report]
    if r is not None:
        report["bytes"] = save(r, data)
    return report
