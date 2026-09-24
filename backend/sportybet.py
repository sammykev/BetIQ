"""
SportyBet — upcoming events and booking codes, through the same public web
API sportybet.com's own pages call (no account or key; a booking code only
loads selections into a betslip, it never places a bet).

SportyBet's firewall fingerprints the TLS handshake and challenges anything
that isn't a real browser — plain Python HTTP clients (httpx, requests) get an
empty 202 or a 403. curl_cffi performs the handshake exactly like Chrome, so
every request here goes through it.

Markets and outcomes use Betradar's fixed ids (1X2 is market 1, outcomes
1/2/3), so booking needs only the event id — see booking_slip.py.
"""

import asyncio
import os
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Tuple

from curl_cffi.requests import AsyncSession

from team_names import normalise

COUNTRY = os.getenv("SPORTYBET_COUNTRY", "ng")
BASE = f"https://www.sportybet.com/api/{COUNTRY}"
SITE = f"https://www.sportybet.com/{COUNTRY}/"
IMPERSONATE = os.getenv("SPORTYBET_IMPERSONATE", "chrome131")
# Optional, e.g. http://user:pass@ng.proxy.example:8000 — only if SportyBet
# starts refusing this server's IP (the self-test's first step says so)
PROXY = os.getenv("SPORTYBET_PROXY") or None
FOOTBALL = "sr:sport:1"
OK = 10000  # SportyBet's bizCode for success

_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.sportybet.com",
    "Referer": SITE,
}

TOURNAMENT_IDS = [
    "sr:tournament:17", "sr:tournament:23", "sr:tournament:35", "sr:tournament:8",
    "sr:tournament:34", "sr:tournament:7", "sr:tournament:679", "sr:tournament:238",
    "sr:tournament:37", "sr:tournament:44", "sr:tournament:18",  # … Championship
    "sr:tournament:1091", "sr:tournament:1090", "sr:tournament:133", "sr:tournament:1049",
    "sr:tournament:42", "sr:tournament:143", "sr:tournament:68", "sr:tournament:191",
]


class SportyBetError(Exception):
    """A request SportyBet refused or answered with something unusable."""


def _session() -> AsyncSession:
    return AsyncSession(impersonate=IMPERSONATE, timeout=20, headers=_HEADERS, proxy=PROXY)


# One long-lived session: its connection to SportyBet stays open, so a
# booking skips the TLS handshake (the slowest part of a fresh request).
_shared: Optional[AsyncSession] = None


def shared_session() -> AsyncSession:
    global _shared
    if _shared is None:
        _shared = _session()
    return _shared


async def reset_shared_session() -> None:
    """Drop the shared session (after a connection error); the next call opens a new one."""
    global _shared
    old, _shared = _shared, None
    if old is not None:
        try:
            await old.close()
        except Exception:
            pass


async def _request(session: AsyncSession, method: str, path: str, **kw) -> Dict[str, Any]:
    """One API call. Returns the parsed body when bizCode is 10000, raises otherwise."""
    r = await session.request(method, f"{BASE}{path}", **kw)
    text = (r.text or "").strip()
    if r.status_code not in (200, 202) or not text.startswith("{"):
        raise SportyBetError(f"{method} {path}: HTTP {r.status_code}, {len(text)} bytes"
                             + (" (blocked by SportyBet's firewall)" if r.status_code in (202, 403) else "")
                             + (f" — {text[:120]!r}" if text else ""))
    data = r.json()
    if data.get("bizCode") != OK:
        raise SportyBetError(f"{method} {path}: bizCode {data.get('bizCode')} {data.get('message', '')}".strip())
    return data


# ------------------------------------------------------------------ #
# Events
# ------------------------------------------------------------------ #

def _collect_events(node: Any, out: List[Dict]) -> None:
    """Every event dict in a response, however it is grouped (tournaments, pages…)."""
    if isinstance(node, dict):
        if node.get("eventId") and node.get("homeTeamName") and node.get("awayTeamName"):
            out.append(node)
            return
        for v in node.values():
            _collect_events(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_events(v, out)


def _utc_day(ev: Dict) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(ev["estimateStartTime"]) / 1000, timezone.utc).date().isoformat()
    except (KeyError, TypeError, ValueError):
        return None


def _near(day: Optional[str], date_str: str) -> bool:
    """Same UTC day, or the next/previous one (late kick-offs cross midnight)."""
    if not day:
        return True
    a, b = datetime.fromisoformat(day), datetime.fromisoformat(date_str)
    return abs((a - b).days) <= 1


# Markets the slip books (booking_slip.sportybet_ids) — listed so each event
# shows which of them SportyBet offers
MARKETS = "1,18,10,29,11,26,60"


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _pc_upcoming(session: AsyncSession, max_pages: int = 10) -> List[Dict]:
    """The desktop site's "Upcoming" football list, page by page."""
    events: List[Dict] = []
    for page in range(1, max_pages + 1):
        data = (await _request(session, "GET", "/factsCenter/pcUpcomingEvents", params={
            "sportId": FOOTBALL, "marketId": MARKETS, "pageSize": 100, "pageNum": page,
            "option": 1, "_t": _now_ms()})).get("data") or {}
        found: List[Dict] = []
        _collect_events(data, found)
        seen = {e["eventId"] for e in events}
        new = [e for e in found if e["eventId"] not in seen]
        events += new
        total = data.get("totalNum") if isinstance(data, dict) else None
        if not new or (total and len(events) >= int(total)):
            break
    return events


async def _wap_upcoming(session: AsyncSession) -> List[Dict]:
    """The mobile site's upcoming list."""
    data = await _request(session, "GET", "/factsCenter/wapConfigurableUpcomingEvents", params={
        "sportId": FOOTBALL, "marketId": MARKETS, "pageSize": 100, "pageNum": 1,
        "option": 1, "_t": _now_ms()})
    found: List[Dict] = []
    _collect_events(data.get("data"), found)
    return found


async def _pc_events(session: AsyncSession) -> List[Dict]:
    """Our main tournaments in one call, the way the site's league filter asks."""
    data = await _request(session, "POST", "/factsCenter/pcEvents", json=[{
        "sportId": FOOTBALL, "marketId": MARKETS,
        "tournamentId": [[tid] for tid in TOURNAMENT_IDS]}])
    found: List[Dict] = []
    _collect_events(data.get("data"), found)
    return found


async def _thumbnail(session: AsyncSession) -> List[Dict]:
    data = await _request(session, "GET", "/factsCenter/commonThumbnailEvents",
                          params={"sportId": FOOTBALL, "marketId": "1"})
    found: List[Dict] = []
    _collect_events(data.get("data"), found)
    return found


# Tried in order. SportyBet has moved listings before (getScheduled now
# 404s), so the self-test reports each one separately.
LISTINGS = [
    ("pcUpcomingEvents", _pc_upcoming),
    ("wapConfigurableUpcomingEvents", _wap_upcoming),
    ("pcEvents", _pc_events),
    ("commonThumbnailEvents", _thumbnail),
]


async def probe_listings(session: AsyncSession, stop_at_first: bool = False) -> Tuple[List[Dict], List[str]]:
    """Events from the first listing that has any, and one line per listing tried."""
    events: List[Dict] = []
    report: List[str] = []
    for name, fetch in LISTINGS:
        try:
            found = await fetch(session)
            report.append(f"{name}: {len(found)} events")
        except Exception as e:
            report.append(f"{name}: {e}")
            continue
        if found and not events:
            events = found
            if stop_at_first:
                break
    return events, report


_cache: Dict[str, Tuple[float, List[Dict]]] = {}
CACHE_SECONDS = 300


async def fetch_events_for_date(date_str: str, session: Optional[AsyncSession] = None) -> List[Dict]:
    """
    Upcoming football events around a date (YYYY-MM-DD), each with eventId,
    homeTeamName, awayTeamName, estimateStartTime and the listed markets.
    Returns [] when SportyBet can't be reached (logged, never raised).
    """
    hit = _cache.get(date_str)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]

    session = session or shared_session()
    events: List[Dict] = []
    for name, fetch in LISTINGS:
        try:
            events = [e for e in await fetch(session) if _near(_utc_day(e), date_str)]
        except Exception as e:
            print(f"[SportyBet] {name}: {e}")
            continue
        if events:
            break

    print(f"[SportyBet] {len(events)} events around {date_str}")
    if events:
        _cache[date_str] = (time.time(), events)
    return events


# The markets booking_slip books; the catalog keeps only these per event
BOOKED_MARKETS = {"1", "10", "11", "18", "26", "29", "60"}


def slim_event(ev: Dict) -> Dict:
    """What booking needs from an event: its id, teams, kick-off, and the
    prices of the markets we book (for the slip's odds)."""
    return {
        "eventId": ev.get("eventId"),
        "homeTeamName": ev.get("homeTeamName"),
        "awayTeamName": ev.get("awayTeamName"),
        "estimateStartTime": ev.get("estimateStartTime"),
        "markets": [
            {"id": m.get("id"), "specifier": m.get("specifier") or "",
             "outcomes": [{"id": o.get("id"), "odds": o.get("odds"), "isActive": o.get("isActive", 1)}
                          for o in m.get("outcomes") or []]}
            for m in ev.get("markets") or [] if str(m.get("id")) in BOOKED_MARKETS
        ],
    }


async def fetch_catalog(session: Optional[AsyncSession] = None) -> Tuple[List[Dict], List[str]]:
    """Every upcoming football event SportyBet lists (for linking predictions
    ahead of booking), and one report line per listing tried."""
    session = session or shared_session()
    report: List[str] = []
    try:
        events = await _pc_upcoming(session, max_pages=30)
        report.append(f"pcUpcomingEvents: {len(events)} events")
        if events:
            return events, report
    except Exception as e:
        report.append(f"pcUpcomingEvents: {e}")
    rest, more = await probe_listings(session, stop_at_first=True)
    return rest, report + [line for line in more if not line.startswith("pcUpcomingEvents")]


# ------------------------------------------------------------------ #
# Matching our fixtures to SportyBet events
# ------------------------------------------------------------------ #

# Names Betradar writes differently from football-data.org / our CSVs
_SB_ALIASES = {
    "man utd": "manchester united", "man united": "manchester united", "man city": "manchester city",
    "psg": "paris saint germain", "paris sg": "paris saint germain", "inter": "internazionale",
    "atletico madrid": "atletico madrid", "ath madrid": "atletico madrid", "atleti": "atletico madrid",
    "bayern munich": "bayern munchen", "wolves": "wolverhampton wanderers",
    "spurs": "tottenham hotspur", "nottm forest": "nottingham forest",
    "internazionale milano": "internazionale", "inter milan": "internazionale",
    "cologne": "koln", "olympique lyonnais": "lyon", "olympique lyon": "lyon",
    "sporting lisbon": "sporting", "sporting portugal": "sporting", "sp lisbon": "sporting",
    "rasenballsport leipzig": "rb leipzig", "stade rennais": "rennes", "stade brestois": "brest",
    "saint etienne": "st etienne", "az": "az alkmaar", "nec": "nec nijmegen", "nijmegen": "nec nijmegen",
    # "Paris FC" normalises to "paris", which is inside "Paris Saint-Germain"
    "paris": "paris fc",
}


def _key(name: str) -> str:
    n = normalise(name)
    return _SB_ALIASES.get(n, n)


def team_similarity(a: str, b: str) -> float:
    """
    0–1. Full credit when one name's words are all in the other's
    ("Brighton" / "Brighton & Hove Albion"). Names that share words are
    compared on the words that differ, so "Manchester City" and
    "Manchester United" (or Real / Atlético Madrid) come out far apart.
    """
    ka, kb = _key(a), _key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    wa, wb = set(ka.split()), set(kb.split())
    shared = wa & wb
    if (wa <= wb or wb <= wa) and max(len(w) for w in shared or {""}) >= 3:
        return 0.95
    if shared:
        return SequenceMatcher(None, " ".join(sorted(wa - shared)), " ".join(sorted(wb - shared))).ratio()
    return SequenceMatcher(None, ka, kb).ratio()


def find_event(home: str, away: str, events: Iterable[Dict]) -> Optional[Dict]:
    """The event for this fixture: each team must match its own side, clearly."""
    best, best_score = None, 0.0
    for ev in events:
        h, a = ev.get("homeTeamName") or "", ev.get("awayTeamName") or ""
        sh, sa = team_similarity(home, h), team_similarity(away, a)
        if min(sh, sa) < 0.8:
            continue
        # A reversed fixture matches better crossed over — that's a different match
        if team_similarity(home, a) > sh or team_similarity(away, h) > sa:
            continue
        if sh + sa > best_score:
            best, best_score = ev, sh + sa
    return best


# ------------------------------------------------------------------ #
# Booking codes
# ------------------------------------------------------------------ #

SHARE_URL = "https://www.sportybet.com/?shareCode={code}&c=" + COUNTRY


def _outcome_keys(node: Any, event_id: str = "", market: Optional[Dict] = None) -> Iterable[Tuple[Tuple[str, str, str], Dict]]:
    """(eventId, marketId, outcomeId) for each outcome in a share response, flat or nested."""
    if isinstance(node, list):
        for v in node:
            yield from _outcome_keys(v, event_id, market)
    elif isinstance(node, dict):
        event_id = str(node.get("eventId") or event_id)
        if "markets" in node:
            for m in node.get("markets") or []:
                yield from _outcome_keys(m.get("outcomes") or [], event_id, m)
        elif node.get("outcomeId") and node.get("marketId"):
            yield (event_id, str(node["marketId"]), str(node["outcomeId"])), node
        elif market is not None and node.get("id"):
            yield (event_id, str(market.get("id")), str(node["id"])), node


async def share_selections(selections: List[Dict[str, str]],
                           session: Optional[AsyncSession] = None) -> Dict[str, Any]:
    """
    Create a booking code for {eventId, marketId, specifier, outcomeId}
    selections. Returns {"code", "url", "odds": {(event, market, outcome): float},
    "unavailable": {(event, market, outcome), …}}. Raises SportyBetError.
    """
    payload = {"selections": [
        {"eventId": s["eventId"], "marketId": s["marketId"], "outcomeId": s["outcomeId"],
         **({"specifier": s["specifier"]} if s.get("specifier") else {})}
        for s in selections
    ]}
    if session is not None:
        data = (await _request(session, "POST", "/orders/share", json=payload)).get("data") or {}
    else:
        try:
            data = (await _request(shared_session(), "POST", "/orders/share", json=payload)).get("data") or {}
        except SportyBetError:
            raise
        except Exception:
            # A kept-alive connection can go stale: reconnect once
            await reset_shared_session()
            data = (await _request(shared_session(), "POST", "/orders/share", json=payload)).get("data") or {}

    code = data.get("shareCode")
    if not code:
        raise SportyBetError("SportyBet accepted the request but returned no share code")
    odds: Dict[Tuple[str, str, str], float] = {}
    for key, o in _outcome_keys(data.get("outcomes") or []):
        try:
            odds[key] = float(o.get("odds"))
        except (TypeError, ValueError):
            pass
    unavailable = {key for key, _ in _outcome_keys(data.get("unavailableOutcomes") or [])}
    return {"code": str(code), "url": data.get("shareURL") or SHARE_URL.format(code=code),
            "odds": odds, "unavailable": unavailable}


# ------------------------------------------------------------------ #
# Live self-test (admin dashboard)
# ------------------------------------------------------------------ #

async def diagnose(fixtures: List[Dict[str, str]], today: Optional[str] = None) -> Dict[str, Any]:
    """
    Run the whole booking path against the real SportyBet and report each
    step: list events, match our upcoming fixtures, book a one-pick code.
    Nothing is cached or stored; a booking code places no bet.
    """
    steps: List[Dict[str, Any]] = []
    today = today or datetime.now(timezone.utc).date().isoformat()

    async def step(name, fn):
        t = time.perf_counter()
        try:
            ok, detail = await fn()
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        steps.append({"step": name, "ok": ok, "detail": detail, "ms": round((time.perf_counter() - t) * 1000)})
        return ok

    events: List[Dict] = []
    async with _session() as session:
        async def listing():
            found, report = await probe_listings(session)
            events.extend(found)
            if not events:
                return False, " · ".join(report)
            e = events[0]
            return True, (f"{len(events)} events, e.g. {e['homeTeamName']} vs {e['awayTeamName']} "
                          f"({e['eventId']}) — " + " · ".join(report))

        async def matching():
            upcoming = [f for f in fixtures if f.get("home") and f.get("away")][:30]
            if not upcoming:
                return True, "No upcoming fixtures to check yet"
            found = [f for f in upcoming if find_event(f["home"], f["away"], events)]
            missing = [f"{f['home']} vs {f['away']}" for f in upcoming if f not in found][:8]
            return len(found) > 0, (f"{len(found)} of {len(upcoming)} upcoming fixtures found on SportyBet"
                                    + (f"; not found: {', '.join(missing)}" if missing else ""))

        async def booking():
            ev = next((e for e in events if any(str(m.get("id")) == "1" for m in e.get("markets") or [])), events[0])
            data = (await _request(session, "POST", "/orders/share", json={"selections": [
                {"eventId": ev["eventId"], "marketId": "1", "outcomeId": "1"}]})).get("data") or {}
            code = data.get("shareCode")
            if not code:
                return False, f"No share code in the reply: {str(data)[:200]}"
            return True, f"Booked {ev['homeTeamName']} to win → code {code}"

        if await step("List SportyBet events", listing):
            await step("Match our fixtures", matching)
            await step("Create a booking code", booking)

    return {"ok": all(s["ok"] for s in steps) and len(steps) == 3, "impersonate": IMPERSONATE,
            "country": COUNTRY, "proxy": bool(PROXY), "steps": steps}
