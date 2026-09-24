"""
Referees appointed to upcoming matches, from SofaScore.

SofaScore's day listings give the coming days' matches; each match's own
page names its referee once one is appointed (usually two to four days
before kick-off), with their career record. We match those listings to our
predictions by team names (sportybet.find_event, the same matching as the
SportyBet links) and keep {prediction key: referee}. The cards model then
scales its bookings by that referee (set_pieces.SetPieceModel.referee_factor).

Stored in Redis (APPOINTED_KEY) so a restart keeps them. A referee once
found isn't asked for again; matches still without one are re-checked each
run, up to MAX_PAGES match pages a run.

SofaScore blocks some servers (Render gets a 403 "challenge"). Two ways
round it: a GitHub Actions job (collect_referees.py) runs the same lookup
from GitHub's machines into the same Redis key, and the API server falls
back to API-Football's fixture lists (fetch_api_football), which name the
referee but not their career record.
"""

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Tuple

import international_fixtures as intl
from international_stats import AF_BASE, SOFA_EVENT, parse_sofa_referee

APPOINTED_KEY = "betiq:referees:appointed"
SOFA_DAY = "https://api.sofascore.com/api/v1/sport/football/scheduled-events/{day}"
DAYS_AHEAD = 3
MAX_PAGES = 150
PAUSE = 0.5  # seconds between match pages


def key(home: str, away: str, day: str) -> str:
    """Same shape as main._sb_key: home|away|date (our names)."""
    return f"{home}|{away}|{day}"


def _upcoming(listing: Any) -> Iterable[Dict]:
    """A day listing's not-yet-started matches as {homeTeamName, awayTeamName, day, id}."""
    for ev in (listing or {}).get("events") or []:
        if (ev.get("status") or {}).get("type") != "notstarted":
            continue
        try:
            day = datetime.fromtimestamp(int(ev["startTimestamp"]), timezone.utc).date().isoformat()
        except (KeyError, TypeError, ValueError):
            continue
        home, away = (ev.get("homeTeam") or {}).get("name"), (ev.get("awayTeam") or {}).get("name")
        if home and away and ev.get("id"):
            yield {"homeTeamName": home, "awayTeamName": away, "day": day, "id": ev["id"]}


def match(preds: List[Dict], events: List[Dict]) -> Dict[str, Dict]:
    """{prediction key: SofaScore event} for the predictions SofaScore lists
    (both teams clearly matched, kick-off within a day)."""
    import sportybet
    by_day: Dict[str, List[Dict]] = {}
    for ev in events:
        by_day.setdefault(ev["day"], []).append(ev)
    out = {}
    for p in preds:
        try:
            d = date.fromisoformat(p["date"])
        except (KeyError, TypeError, ValueError):
            continue
        near = [ev for o in (0, -1, 1) for ev in by_day.get((d + timedelta(days=o)).isoformat(), [])]
        ev = sportybet.find_event(p["home"], p["away"], near)
        if ev:
            out[key(p["home"], p["away"], p["date"])] = ev
    return out


async def fetch(client, preds: List[Dict], known: Dict[str, Dict], today: date,
                pause: float = PAUSE, max_pages: int = MAX_PAGES) -> Tuple[Dict[str, Dict], Dict[str, Any]]:
    """({prediction key: {"name", "career", "event_id"}}, report) for our
    predictions over the next DAYS_AHEAD days. `known` = the last run's
    result: referees already found are kept without asking again."""
    report: Dict[str, Any] = {"days": {}, "matched": 0, "pages": 0, "found": 0, "new": 0, "errors": []}
    events: List[Dict] = []
    for offset in range(DAYS_AHEAD + 1):
        day = today + timedelta(days=offset)
        data, err = await intl._get_json(client, SOFA_DAY.format(day=day.isoformat()), {}, intl._SOFASCORE_HEADERS)
        if err:
            report["errors"].append(f"{day}: {err}")
            if err.startswith(("HTTP 403", "HTTP 429")):
                break  # blocked: asking again won't help
            continue
        found = list(_upcoming(data))
        report["days"][day.isoformat()] = len(found)
        events += found
    last = (today + timedelta(days=DAYS_AHEAD)).isoformat()
    upcoming = [p for p in preds if today.isoformat() <= (p.get("date") or "") <= last]
    matched = match(upcoming, events)
    report["matched"] = len(matched)

    out: Dict[str, Dict] = {}
    for k, ev in sorted(matched.items(), key=lambda kv: kv[1]["day"]):  # soonest first
        old = known.get(k)
        if old and old.get("name"):
            out[k] = old
            continue
        if report["pages"] >= max_pages:
            continue
        page, err = await intl._get_json(client, SOFA_EVENT.format(id=ev["id"]), {}, intl._SOFASCORE_HEADERS)
        report["pages"] += 1
        if pause:
            await asyncio.sleep(pause)
        if err:
            if err.startswith(("HTTP 403", "HTTP 429")):
                report["errors"].append(f"match pages: {err}")
                break
            continue
        ref = parse_sofa_referee(page)
        if ref:
            out[k] = {"name": ref["name"], "career": {x: ref[x] for x in ("games", "yellow", "red")},
                      "event_id": ev["id"]}
            report["new"] += 1
    # A listing that failed this time doesn't lose referees found before
    for k, v in known.items():
        if k not in out and k.rsplit("|", 1)[-1] >= today.isoformat() and v.get("name"):
            out[k] = v
    report["found"] = len(out)
    return out, report


AF_DAYS = 3          # today and the next two days: one request a day
AF_DAILY_CAP = 12    # of the key's 100 a day (the nightly collector uses up to 85)


def blocked(report: Dict[str, Any]) -> bool:
    """Whether SofaScore refused us (rather than just listing no referee)."""
    return any(" 403" in e or " 429" in e for e in report.get("errors") or [])


async def fetch_api_football(client, preds: List[Dict], today: date, api_key: str,
                             days: int = AF_DAYS) -> Tuple[Dict[str, Dict], Dict[str, Any]]:
    """({prediction key: {"name", "career": None, "source"}}, report) from
    API-Football's fixture lists for the next `days` days."""
    report: Dict[str, Any] = {"requests": 0, "fixtures": 0, "errors": []}
    events: List[Dict] = []
    for offset in range(days):
        day = (today + timedelta(days=offset)).isoformat()
        try:
            res = await client.get(f"{AF_BASE}/fixtures", params={"date": day}, headers={"x-apisports-key": api_key})
            data = res.json()
        except Exception as e:
            report["errors"].append(f"{day}: {type(e).__name__}")
            continue
        finally:
            report["requests"] += 1
        if data.get("errors") and not data.get("response"):
            report["errors"].append(f"{day}: {data['errors']}")
            if isinstance(data["errors"], dict) and (data["errors"].get("requests") or data["errors"].get("token")):
                break  # out of requests, or a bad key
            continue
        for f in data.get("response") or []:
            referee = ((f.get("fixture") or {}).get("referee") or "").split(",")[0].strip()
            teams = f.get("teams") or {}
            if referee:
                events.append({"homeTeamName": (teams.get("home") or {}).get("name") or "",
                               "awayTeamName": (teams.get("away") or {}).get("name") or "", "day": day,
                               "referee": referee})
        report["fixtures"] += len(data.get("response") or [])
    last = (today + timedelta(days=days - 1)).isoformat()
    upcoming = [p for p in preds if today.isoformat() <= (p.get("date") or "") <= last]
    out = {k: {"name": ev["referee"], "career": None, "source": "api-football"}
           for k, ev in match(upcoming, events).items()}
    report["found"] = len(out)
    return out, report


def load(r) -> Dict[str, Any]:
    """{"at", "appointments", "report"} from Redis ({} when there's none)."""
    if r is None:
        return {}
    try:
        raw = r.get(APPOINTED_KEY)
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def save(r, state: Dict[str, Any]) -> None:
    if r is not None:
        r.set(APPOINTED_KEY, json.dumps(state, separators=(",", ":")), ex=7 * 86400)
