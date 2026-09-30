"""
Corners and cards for past international matches — the training data for
the international corners/bookings model (set_pieces.fit_international).

Three sources, run from GitHub Actions (collect_international_stats.py):

- ESPN's public scoreboards (the main source: SofaScore answers GitHub and
  Render with a 403): a competition-day per request, on the days the
  international results file had matches, finished matches with corners
  (team statistics) and cards (match events). Matches ESPN lists without
  stats go to API-Football's list.
- SofaScore: each day's national-team matches (the same filter as the
  fixtures, international_fixtures.sofascore_internationals), then each
  finished match's statistics. The backfill walks back from yesterday to
  START a chunk at a time, newest first; the last few days are re-checked
  every run (stats arrive after the final whistle).
- API-Football (APIFOOTBALL_KEY): fills matches ESPN or SofaScore listed
  without stats, within a daily request budget (the free plan allows 100
  requests a day, for yesterday to tomorrow only).

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


SHOT_KEYS = ("HS", "AS", "HST", "AST")


def _shots_of(res: Dict[str, Any]) -> Dict[str, int]:
    """{HS, AS, HST, AST} from an ESPN result, or {} when it has no shot counts."""
    if not (res.get("shots") and res.get("sot")):
        return {}
    return {"HS": res["shots"][0], "AS": res["shots"][1], "HST": res["sot"][0], "AST": res["sot"][1]}


def rows_frame(data: Dict[str, Any]):
    """Rows as a DataFrame in the league CSVs' shape (set_pieces reads it):
    teams by their one-per-nation key, league = our competition code."""
    import pandas as pd
    rows = [{"Date": pd.Timestamp(r["date"]), "HomeTeam": intl.team_key(r["home"]), "AwayTeam": intl.team_key(r["away"]),
             "league": intl.competition(r.get("competition", ""))[0], "Referee": r.get("referee"),
             **{k: r[k] for k in ("HC", "AC", "HY", "AY", "HR", "AR")},
             **{k: r.get(k, float("nan")) for k in SHOT_KEYS}}
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
    todo = out[missing]
    for i, when, row_home, row_away in zip(todo.index, todo["Date"], todo["HomeTeam"], todo["AwayTeam"]):
        d = pd.Timestamp(when).date()
        best, best_score = None, 0.0
        for day in (d, d - timedelta(days=1), d + timedelta(days=1)):
            for home, away, name in by_day.get(day.isoformat(), []):
                sh, sa = similar(row_home, home), similar(row_away, away)
                if min(sh, sa) >= 0.8 and sh + sa > best_score:
                    best, best_score = name, sh + sa
        if best:
            filled.append((i, best))
    if filled:
        out.loc[[i for i, _ in filled], "Referee"] = [name for _, name in filled]
    return out


def summary(data: Dict[str, Any]) -> Dict[str, Any]:
    rows = list(data.get("rows", {}).values())
    days = sorted(data.get("sofa_days") or [])
    sources: Dict[str, int] = {}
    for r in rows:
        sources[r.get("source", "?")] = sources.get(r.get("source", "?"), 0) + 1
    club_refs = data.get("club_refs") or {}
    espn = data.get("espn_days") or []
    oldest = min((d for d in (days[0] if days else None, min(espn, default=None)) if d), default=None)
    oldest = oldest[:10] if oldest else None
    return {"matches": len(rows), "sources": sources,
            "with_referee": sum(1 for r in rows if r.get("referee")),
            "club_referees": sum(1 for v in club_refs.values() if v), "referees_known": len(data.get("referees") or {}),
            "first": min((r["date"] for r in rows), default=None), "last": max((r["date"] for r in rows), default=None),
            "days_scanned": len(days), "oldest_day_scanned": oldest,
            "espn_days_scanned": len(espn),
            "with_shots": sum(1 for r in rows if "HS" in r),
            "backfill_complete": bool(data.get("espn_backfill_done")) or (bool(days) and days[0] <= START.isoformat()),
            "last_run": (data.get("runs") or [None])[-1]}


# ── collection ───────────────────────────────────────────────────────────
async def _get(session, url: str, headers: Optional[Dict] = None, params: Optional[Dict] = None,
               errors: Optional[List[str]] = None) -> Tuple[Optional[Any], Optional[int]]:
    """(JSON, HTTP status); (None, status or None) on failure. `errors`, if
    given, collects a short description of each failure for the run log."""
    try:
        r = await session.get(url, headers=headers, params=params, timeout=30)
    except Exception as e:
        if errors is not None:
            errors.append(f"{type(e).__name__}: {str(e)[:120]}")
        return None, None
    if r.status_code != 200:
        if errors is not None:
            errors.append(f"HTTP {r.status_code}: {(getattr(r, 'text', '') or '')[:120]}")
        return None, r.status_code
    try:
        return r.json(), 200
    except ValueError:
        if errors is not None:
            errors.append(f"not JSON: {(getattr(r, 'text', '') or '')[:120]}")
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


ESPN_RECHECK_DAYS = 3     # recent days are re-read every run (stats arrive late)
ESPN_PAUSE = 0.3
# The international results file's competitions → ESPN scoreboards. ESPN
# refuses date ranges ("Failed to get events endpoint"), so it's read a day
# at a time — only the days that file says had matches in that competition.
ESPN_TOURNAMENTS = {
    "Friendly": ["fifa.friendly"],
    "FIFA World Cup qualification": ["fifa.worldq.uefa", "fifa.worldq.conmebol", "fifa.worldq.concacaf",
                                     "fifa.worldq.caf", "fifa.worldq.afc"],
    "UEFA Nations League": ["uefa.nations"],
    "UEFA Euro qualification": ["uefa.euroq"],
    "African Cup of Nations qualification": ["caf.nations_qual"],
    "CONCACAF Nations League": ["concacaf.nations.league"],
    "FIFA World Cup": ["fifa.world"],
    "UEFA Euro": ["uefa.euro"],
    "African Cup of Nations": ["caf.nations"],
    "Copa América": ["conmebol.america"],
    "Gold Cup": ["concacaf.gold"],
    "AFC Asian Cup": ["afc.asian.cup"],
}
ESPN_NAMES = {**{slug: name for slug, (name, _) in intl.ESPN_COMPETITIONS.items()},
              "fifa.world": "FIFA World Cup", "uefa.euro": "UEFA Euro", "caf.nations": "Africa Cup of Nations",
              "conmebol.america": "Copa América", "concacaf.gold": "CONCACAF Gold Cup", "afc.asian.cup": "AFC Asian Cup"}


def espn_calendar_from_csv(text: str, today: date) -> Dict[str, set]:
    """{date: ESPN competitions to read} from the international results
    file, plus the last few days in every competition (not in it yet)."""
    import csv
    import io
    cal: Dict[str, set] = {}
    for row in csv.DictReader(io.StringIO(text or "")):
        d = (row.get("date") or "")[:10]
        slugs = ESPN_TOURNAMENTS.get((row.get("tournament") or "").strip())
        if slugs and START.isoformat() <= d < today.isoformat():
            cal.setdefault(d, set()).update(slugs)
    for i in range(1, ESPN_RECHECK_DAYS + 1):
        cal.setdefault((today - timedelta(days=i)).isoformat(), set()).update(intl.ESPN_COMPETITIONS)
    return cal


async def espn_calendar(session, today: date) -> Dict[str, set]:
    """The calendar from the latest international results file (the local
    copy if the download fails)."""
    from football_data_sync import INTERNATIONAL_PATH, INTERNATIONAL_URL
    text = None
    try:
        r = await session.get(INTERNATIONAL_URL, timeout=60)
        if r.status_code == 200:
            text = r.text
    except Exception:
        pass
    if not text and os.path.exists(INTERNATIONAL_PATH):
        with open(INTERNATIONAL_PATH, encoding="utf-8") as f:
            text = f.read()
    return espn_calendar_from_csv(text or "", today)


def espn_todo(data: Dict[str, Any], calendar: Dict[str, set], today: date) -> List[Tuple[str, str, date]]:
    """(progress key, competition, day) to read, newest first: recent days
    always, older ones until done."""
    done = set(data.get("espn_days") or [])
    recent = {(today - timedelta(days=i)).isoformat() for i in range(1, ESPN_RECHECK_DAYS + 1)}
    out = []
    for d in sorted(calendar, reverse=True):
        for slug in sorted(calendar[d]):
            k = f"{d}|{slug}"
            if d in recent or k not in done:
                out.append((k, slug, date.fromisoformat(d)))
    return out


async def collect_espn(session, data: Dict[str, Any], deadline: float, today: date,
                       pause: float = ESPN_PAUSE, calendar: Optional[Dict[str, set]] = None) -> Dict[str, Any]:
    """Finished national-team matches with corners and cards from ESPN, one
    competition-day per request, newest first, until the deadline."""
    import results_feed
    if calendar is None:
        calendar = await espn_calendar(session, today)
    todo = espn_todo(data, calendar, today)
    report: Dict[str, Any] = {"to_read": len(todo), "requests": 0, "matches": 0, "no_stats": 0, "stopped": None,
                              "statuses": {}, "errors": []}
    errors: List[str] = []
    done = set(data.get("espn_days") or [])
    missing = data.setdefault("missing", [])
    missing_keys = {m["key"] for m in missing}
    for k, slug, day in todo:
        if time.monotonic() > deadline:
            report["stopped"] = "time"
            break
        if report["requests"] >= 30 and not report["statuses"].get("200"):
            report["stopped"] = "every request failed (see errors)"
            break
        page, status = await _get(session, f"{intl.ESPN_BASE}/{slug}/scoreboard", intl._ESPN_HEADERS,
                                  {"dates": f"{day:%Y%m%d}", "limit": 200}, errors)
        report["requests"] += 1
        report["statuses"][str(status)] = report["statuses"].get(str(status), 0) + 1
        await asyncio.sleep(pause)
        if page is None:
            if status in (403, 429):
                report["stopped"] = f"HTTP {status}"
                break
            if status == 400:
                done.add(k)  # ESPN's answer for a competition with nothing that day
            continue  # anything else: try again next run
        comp = ESPN_NAMES.get(slug, slug)
        for res in results_feed.parse_espn(page):
            if res["status"] != "finished" or res.get("aet") or res.get("hg") is None:
                continue
            key = match_key(res["date"], res["home"], res["away"])
            if key in data["rows"]:
                if "HS" not in data["rows"][key] and _shots_of(res):
                    data["rows"][key].update(_shots_of(res))
                    report["shots_added"] = report.get("shots_added", 0) + 1
                continue
            if res.get("corners") and res.get("bookings"):
                data["rows"][key] = {"date": res["date"], "home": res["home"], "away": res["away"],
                                     "competition": comp, "source": "espn",
                                     "HC": res["corners"][0], "AC": res["corners"][1],
                                     # Booking points (yellow 1, red 2) as yellows: the model adds HY + 2·HR
                                     "HY": res["bookings"][0], "AY": res["bookings"][1], "HR": 0, "AR": 0,
                                     **_shots_of(res)}
                report["matches"] += 1
            elif key not in missing_keys:
                report["no_stats"] += 1
                missing.append({"key": key, "date": res["date"], "home": res["home"], "away": res["away"],
                                "competition": comp})
                missing_keys.add(key)
        done.add(k)
    data["espn_days"] = sorted(done)
    if not report["stopped"] and todo:
        data["espn_backfill_done"] = True
    shots_done = set(data.get("shots_days") or []) | {k for k, _, _ in todo if k in done}
    if not report["stopped"]:
        report["shots_backfill"] = await _backfill_shots(session, data, done, shots_done, deadline, pause, errors)
    data["shots_days"] = sorted(shots_done)
    report["errors"] = list(dict.fromkeys(errors))[:5]
    return report


async def _backfill_shots(session, data: Dict[str, Any], done: set, shots_done: set, deadline: float,
                          pause: float, errors: List[str]) -> Dict[str, Any]:
    """Read again the competition-days collected before shots were kept, for
    the stored matches that lack them, newest first until the deadline."""
    import results_feed
    need = set()
    for row in data["rows"].values():
        if "HS" not in row:
            d = date.fromisoformat(row["date"])
            need |= {d.isoformat(), (d - timedelta(days=1)).isoformat()}  # ESPN dates by US time
    todo = sorted((k for k in done - shots_done if k.split("|", 1)[0] in need), reverse=True)
    rep: Dict[str, Any] = {"to_read": len(todo), "requests": 0, "added": 0, "stopped": None}
    for k in todo:
        if time.monotonic() > deadline:
            rep["stopped"] = "time"
            break
        day, slug = k.split("|", 1)
        page, status = await _get(session, f"{intl.ESPN_BASE}/{slug}/scoreboard", intl._ESPN_HEADERS,
                                  {"dates": day.replace("-", ""), "limit": 200}, errors)
        rep["requests"] += 1
        await asyncio.sleep(pause)
        if page is None:
            if status in (403, 429):
                rep["stopped"] = f"HTTP {status}"
                break
            if status == 400:
                shots_done.add(k)
            continue
        for res in results_feed.parse_espn(page):
            row = data["rows"].get(match_key(res["date"], res["home"], res["away"]))
            if row is not None and "HS" not in row and _shots_of(res):
                row.update(_shots_of(res))
                rep["added"] += 1
        shots_done.add(k)
    return rep


AF_FREE_DAYS_BACK = 1  # API-Football's free plan only allows yesterday to tomorrow


async def collect_api_football(session, data: Dict[str, Any], api_key: str, budget: int,
                               today: Optional[date] = None) -> Dict[str, Any]:
    """Fill matches listed without stats, newest first, within `budget`
    requests — only ones the free plan can reach (from yesterday on)."""
    from difflib import SequenceMatcher
    headers = {"x-apisports-key": api_key}
    tried = set(data.get("af_tried") or [])
    report = {"requests": 0, "matches": 0, "stopped": None}
    by_date: Dict[str, List[Dict]] = {}
    earliest = ((today or datetime.now(timezone.utc).date()) - timedelta(days=AF_FREE_DAYS_BACK)).isoformat()
    todo = sorted((m for m in data.get("missing", [])
                   if m["key"] not in tried and m["key"] not in data["rows"] and m["date"] >= earliest),
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
        report["api_football"] = await collect_api_football(session, data, api_key, af_budget, today)
    report["seconds"] = round(time.monotonic() - started)
    report["total_matches"] = len(data["rows"])
    data["runs"] = (data.get("runs") or [])[-19:] + [report]
    if r is not None:
        report["bytes"] = save(r, data)
    return report
