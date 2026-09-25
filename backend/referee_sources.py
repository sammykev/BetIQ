"""
Referees from football-data.org (the key the fixtures already use,
FOOTBALL_DATA_API_KEY). Its match data names the referee — for upcoming
matches once appointed, and for past ones — in every competition the free
plan covers: our club leagues plus the Champions League, Euros and World Cup.

- Upcoming: one request for the next few days across every competition
  (/v4/matches), matched to our predictions (main._refresh_referees).
- Past: each competition's seasons (/v4/competitions/{code}/matches?season=),
  collected once (the current season re-read), so the cards model can rate
  referees in every league, not only England's (main._with_club_referees).

SofaScore had both, with career records, but blocks our servers.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

FD_BASE = "https://api.football-data.org/v4"
# Free-plan competitions we predict (football-data.org codes)
FD_COMPETITIONS = ["PL", "ELC", "PD", "SA", "BL1", "FL1", "DED", "PPL", "CL", "EC", "WC"]
FD_SEASONS_BACK = 4        # the current season and the four before it
FD_PAUSE = 7.0             # seconds between requests: the free plan allows 10 a minute
REFS_KEY = "betiq:fd_referees:v1"


def season_start(today: date) -> int:
    """The current season's start year (a season starts in July)."""
    return today.year if today.month >= 7 else today.year - 1


def parse_matches(data: Any) -> List[Dict[str, Any]]:
    """[{date, home, away, referee, status}] from a /matches response; the
    referee is the one with type REFEREE (not assistants or VAR)."""
    out = []
    for m in (data or {}).get("matches") or []:
        referee = next((r.get("name") for r in m.get("referees") or []
                        if r.get("type") == "REFEREE" and r.get("name")), None)
        home, away = (m.get("homeTeam") or {}).get("name"), (m.get("awayTeam") or {}).get("name")
        try:
            day = datetime.fromisoformat((m.get("utcDate") or "").replace("Z", "+00:00")).astimezone(timezone.utc).date()
        except ValueError:
            continue
        if home and away:
            out.append({"date": day.isoformat(), "home": home, "away": away, "referee": referee,
                        "status": m.get("status")})
    return out


async def upcoming(get_json, today: date, days: int = 4) -> List[Dict[str, Any]]:
    """Scheduled matches for today .. today+days, every competition, one request.
    `get_json(url)` → parsed JSON or None (FootballDataClient._get)."""
    data = await get_json(f"{FD_BASE}/matches?dateFrom={today}&dateTo={today + timedelta(days=days)}")
    return parse_matches(data)


def seasons_to_read(state: Dict[str, Any], today: date) -> List[tuple]:
    """(competition, season) still to read, newest first; the current season always."""
    done = set(state.get("done") or [])
    current = season_start(today)
    out = []
    for season in range(current, current - FD_SEASONS_BACK - 1, -1):
        for code in FD_COMPETITIONS:
            if season == current or f"{code}|{season}" not in done:
                out.append((code, season))
    return out


async def collect_past(get_json, state: Dict[str, Any], today: date, sleep, pause: float = FD_PAUSE,
                       max_requests: int = 60) -> Dict[str, Any]:
    """Read the seasons still to do into state["refs"] ({date|home|away:
    referee}). `sleep` is asyncio.sleep (a stub in tests)."""
    refs = state.setdefault("refs", {})
    done = set(state.get("done") or [])
    report: Dict[str, Any] = {"requests": 0, "found": 0, "failed": []}
    current = season_start(today)
    for code, season in seasons_to_read(state, today):
        if report["requests"] >= max_requests:
            break
        if report["requests"]:
            await sleep(pause)
        data = await get_json(f"{FD_BASE}/competitions/{code}/matches?season={season}&status=FINISHED")
        report["requests"] += 1
        if data is None:
            report["failed"].append(f"{code} {season}")
            continue
        for m in parse_matches(data):
            if m["referee"]:
                k = f"{m['date']}|{m['home']}|{m['away']}"
                if k not in refs:
                    report["found"] += 1
                refs[k] = m["referee"]
        if season != current:
            done.add(f"{code}|{season}")
    state["done"] = sorted(done)
    state["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state["report"] = report
    return report
