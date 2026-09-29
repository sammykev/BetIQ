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

import re

from team_names import ALIASES, normalise

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

def _collect_events(node: Any, out: List[Dict], tournament: Optional[str] = None) -> None:
    """Every event dict in a response, however it is grouped (tournaments,
    pages…). Events are tagged with their tournament's name (`_tournament`)
    when a parent group has one."""
    if isinstance(node, dict):
        if node.get("eventId") and node.get("homeTeamName") and node.get("awayTeamName"):
            if tournament and "_tournament" not in node:
                node["_tournament"] = tournament
            out.append(node)
            return
        if isinstance(node.get("events"), list) and node.get("name"):
            tournament = " · ".join(str(x) for x in (node.get("categoryName"), node.get("name")) if x)
        for v in node.values():
            _collect_events(v, out, tournament)
    elif isinstance(node, list):
        for v in node:
            _collect_events(v, out, tournament)


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
# shows which of them SportyBet offers. 166/139 (total corners / bookings)
# are asked for separately so a listing that refuses them still loads.
BASE_MARKETS = "1,18,10,29,11,26,60"
# Shots / shots on target over/under (total, home, away): SportyBet's own ids,
# offered on some matches only (bigger ones) — booking_slip.LISTED_ONLY
SHOT_MARKETS = "900394,900393,900552,900553,900546,900547"
MARKETS = BASE_MARKETS + ",166,139," + SHOT_MARKETS
# Asked for too, only so each market's SportyBet name can confirm it
# (booking_slip.VERIFIED): team totals, handicap, double chance & total,
# clean sheets, win to nil, corner markets. Not stored with the links.
LABEL_MARKETS = MARKETS + ",19,20,16,547,31,32,33,34,162,169,170"


def _now_ms() -> int:
    return int(time.time() * 1000)


async def _paged(session: AsyncSession, path: str, params: Dict[str, Any],
                 max_pages: int) -> Tuple[List[Dict], int, Optional[int]]:
    """(events, pages fetched, SportyBet's totalNum) for a paged listing:
    pages until one adds no new events (pages hold ~40–100 events)."""
    events: List[Dict] = []
    seen: set = set()
    total: Optional[int] = None
    page = 0
    for page in range(1, max_pages + 1):
        data = (await _request(session, "GET", path, params={
            **params, "pageNum": page, "_t": _now_ms()})).get("data") or {}
        found: List[Dict] = []
        _collect_events(data, found)
        new = [e for e in found if e["eventId"] not in seen]
        seen.update(e["eventId"] for e in new)
        events += new
        if isinstance(data, dict) and str(data.get("totalNum", "")).isdigit():
            total = int(data["totalNum"])
        if not new:
            break
    return events, page, total


async def _pc_upcoming(session: AsyncSession, max_pages: int = 80) -> List[Dict]:
    """The desktop site's "Upcoming" football list, page by page."""
    # The parameters sportybet.com's Upcoming page (and public scrapers) use
    events, _, _ = await _paged(session, "/factsCenter/pcUpcomingEvents", {
        "sportId": FOOTBALL, "marketId": BASE_MARKETS, "pageSize": 100, "todayGames": "false"}, max_pages)
    return events


async def _wap_upcoming(session: AsyncSession, max_pages: int = 1) -> List[Dict]:
    """The mobile site's upcoming list."""
    events, _, _ = await _paged(session, "/factsCenter/wapConfigurableUpcomingEvents", {
        "sportId": FOOTBALL, "marketId": BASE_MARKETS, "pageSize": 100, "option": 1}, max_pages)
    return events


async def _pc_events(session: AsyncSession) -> List[Dict]:
    """Our main tournaments in one call, the way the site's league filter asks."""
    data = await _request(session, "POST", "/factsCenter/pcEvents", json=[{
        "sportId": FOOTBALL, "marketId": BASE_MARKETS,
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
BOOKED_MARKETS = {"1", "10", "11", "18", "26", "29", "60", "166", "139", *SHOT_MARKETS.split(",")}
# Kept with their SportyBet name, which booking_slip.label_ok checks
LABELLED_MARKETS = {"166", "139", *SHOT_MARKETS.split(",")}


def _label(market: Dict) -> str:
    return str(market.get("desc") or market.get("name") or "")


def market_labels(events: Iterable[Dict]) -> Dict[str, str]:
    """{market id: SportyBet's label} over a listing's events."""
    labels: Dict[str, str] = {}
    for ev in events:
        for m in ev.get("markets") or []:
            mid, label = str(m.get("id") or ""), _label(m)
            if mid and label and mid not in labels:
                labels[mid] = label
    return labels


def market_details(events: Iterable[Dict]) -> Dict[str, Dict[str, Any]]:
    """{market id: {"label", "norm", "outcomes": {outcome id: label}}} over
    events, labels normalised with each event's own team names ("Arsenal
    Total" → "home total") so booking_slip.resolve_markets can check them."""
    from booking_slip import _normalise_label
    out: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        home, away = ev.get("homeTeamName") or "", ev.get("awayTeamName") or ""
        for m in ev.get("markets") or []:
            mid, label = str(m.get("id") or ""), _label(m)
            if not mid or not label:
                continue
            entry = out.setdefault(mid, {"label": label, "norm": _normalise_label(label, home, away), "outcomes": {}})
            for o in m.get("outcomes") or []:
                oid, desc = str(o.get("id") or ""), str(o.get("desc") or "")
                if oid and desc and oid not in entry["outcomes"]:
                    entry["outcomes"][oid] = _normalise_label(desc, home, away)
    return out


async def event_market_details(event_id: str, session: Optional[AsyncSession] = None) -> Dict[str, Dict[str, Any]]:
    """market_details for every market on one event's own page."""
    data = await _request(session or shared_session(), "GET", "/factsCenter/event",
                          params={"eventId": event_id, "productId": 3})
    found: List[Dict] = []
    _collect_events(data.get("data"), found)
    return market_details(found)


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
             **({"desc": _label(m)} if str(m.get("id")) in LABELLED_MARKETS else {}),
             "outcomes": [{"id": o.get("id"), "odds": o.get("odds"), "isActive": o.get("isActive", 1)}
                          for o in m.get("outcomes") or []]}
            for m in ev.get("markets") or [] if str(m.get("id")) in BOOKED_MARKETS
        ],
    }


async def fetch_catalog(session: Optional[AsyncSession] = None) -> Tuple[List[Dict], List[str]]:
    """Every upcoming football event SportyBet lists (for linking predictions
    ahead of booking), merged from its desktop, mobile and highlights feeds,
    and one report line per feed."""
    session = session or shared_session()
    report: List[str] = []
    merged: Dict[str, Dict] = {}
    feeds = [
        ("pcUpcomingEvents", "/factsCenter/pcUpcomingEvents",
         {"sportId": FOOTBALL, "marketId": MARKETS, "pageSize": 100, "todayGames": "false"}, 80),
        ("wapConfigurableUpcomingEvents", "/factsCenter/wapConfigurableUpcomingEvents",
         {"sportId": FOOTBALL, "marketId": MARKETS, "pageSize": 100, "option": 1}, 40),
    ]
    for name, path, params, max_pages in feeds:
        note, events, pages, total, error = "", [], 0, None, None
        # Every market we'd like names for first; fewer if SportyBet refuses the list
        for markets, fallback in ((LABEL_MARKETS, ""), (MARKETS, " — without the extra markets"),
                                  (BASE_MARKETS, " — without corners/bookings markets")):
            try:
                events, pages, total = await _paged(session, path, {**params, "marketId": markets}, max_pages)
            except Exception as e:
                error = e
                continue
            if events:
                note = fallback
                break
        if not events and error is not None:
            report.append(f"{name}: {error}")
            continue
        added = sum(1 for e in events if e["eventId"] not in merged)
        for e in events:
            merged.setdefault(e["eventId"], e)
        report.append(f"{name}: {len(events)} events in {pages} pages"
                      + (f" (SportyBet says {total})" if total is not None else "")
                      + (f", {added} new" if merged and added != len(events) else "") + note)
    try:
        extra = await _thumbnail(session)
        added = sum(1 for e in extra if e["eventId"] not in merged)
        for e in extra:
            merged.setdefault(e["eventId"], e)
        report.append(f"commonThumbnailEvents: {len(extra)} events, {added} new")
    except Exception as e:
        report.append(f"commonThumbnailEvents: {e}")
    return list(merged.values()), report


# ------------------------------------------------------------------ #
# Other sports (tennis, table tennis): SportyBet's own listings
# ------------------------------------------------------------------ #

SPORT_IDS = {"tennis": "sr:sport:5", "table_tennis": "sr:sport:20"}
WINNER_MARKET = "186"  # Betradar "Winner": outcome 4 = first player, 5 = second


async def fetch_sport_events(sport: str, session: Optional[AsyncSession] = None,
                             max_pages: int = 12, markets: Optional[str] = None) -> Tuple[List[Dict], List[str]]:
    """Every upcoming (today included) event SportyBet lists for a sport,
    with its match-winner prices (or every line of `markets`, comma-separated
    ids), and one report line per feed."""
    session = session or shared_session()
    merged: Dict[str, Dict] = {}
    report: List[str] = []
    for today in ("true", "false"):
        try:
            events, pages, total = await _paged(session, "/factsCenter/pcUpcomingEvents", {
                "sportId": SPORT_IDS[sport], "marketId": markets or WINNER_MARKET, "pageSize": 100, "todayGames": today},
                max_pages)
        except Exception as e:
            report.append(f"{sport} {'today' if today == 'true' else 'upcoming'}: {e}")
            continue
        for e in events:
            merged.setdefault(e["eventId"], e)
        report.append(f"{sport} {'today' if today == 'true' else 'upcoming'}: {len(events)} events"
                      + (f" (SportyBet says {total})" if total is not None else ""))
    return list(merged.values()), report


def winner_prices(ev: Dict) -> Optional[Tuple[float, float]]:
    """(first player's odds, second player's) from an event's winner market."""
    for m in ev.get("markets") or []:
        outs = [o for o in m.get("outcomes") or [] if o.get("isActive", 1)]
        if str(m.get("id")) != WINNER_MARKET or len(outs) != 2:
            continue
        by_id = {str(o.get("id")): o for o in outs}
        first, second = (by_id["4"], by_id["5"]) if {"4", "5"} <= set(by_id) else (outs[0], outs[1])
        try:
            a, b = float(first.get("odds")), float(second.get("odds"))
        except (TypeError, ValueError):
            return None
        return (a, b) if a > 1 and b > 1 else None
    return None


async def event_page(event_id: str, session: Optional[AsyncSession] = None) -> Optional[Dict]:
    """One event with every market SportyBet offers on it (its match page)."""
    data = await _request(session or shared_session(), "GET", "/factsCenter/event",
                          params={"eventId": event_id, "productId": 3})
    found: List[Dict] = []
    _collect_events(data.get("data"), found)
    return found[0] if found else None


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
    "inter milano": "internazionale",
    # "Paris FC" normalises to "paris", which is inside "Paris Saint-Germain"
    "paris": "paris fc",
}


def _keys(name: str) -> set:
    """The comparable forms of a name: as written (with SportyBet's aliases),
    and via the training-data aliases ("Czechia" → Czech Republic, "USA" →
    United States), so either spelling on either side can match."""
    n = normalise(name)
    keys = {_SB_ALIASES.get(n, n)}
    if n in ALIASES:
        a = normalise(ALIASES[n])
        keys.add(_SB_ALIASES.get(a, a))
    return {k for k in keys if k}


def _expand_abbreviations(wa: set, wb: set) -> set:
    """`wa` with short abbreviations spelled out as `wb` has them ("faroe is"
    beside "faroe islands" → faroe islands); only 2–3 letters, so "inter"
    never becomes "internacional"."""
    out = set()
    for w in wa:
        full = [x for x in wb if 2 <= len(w) <= 3 and len(x) > len(w) and x.startswith(w)] if w not in wb else []
        out.add(full[0] if len(full) == 1 else w)
    return out


def _similarity(ka: str, kb: str) -> float:
    if ka == kb:
        return 1.0
    wa, wb = set(ka.split()), set(kb.split())
    wa, wb = _expand_abbreviations(wa, wb), _expand_abbreviations(wb, wa)
    if wa == wb:
        return 0.95
    shared = wa & wb
    if (wa <= wb or wb <= wa) and max(len(w) for w in shared or {""}) >= 3:
        return 0.95
    if shared:
        return SequenceMatcher(None, " ".join(sorted(wa - shared)), " ".join(sorted(wb - shared))).ratio()
    return SequenceMatcher(None, ka, kb).ratio()


def team_similarity(a: str, b: str) -> float:
    """
    0–1. Full credit when one name's words are all in the other's
    ("Brighton" / "Brighton & Hove Albion"). Names that share words are
    compared on the words that differ, so "Manchester City" and
    "Manchester United" (or Real / Atlético Madrid) come out far apart.
    """
    return max((_similarity(ka, kb) for ka in _keys(a) for kb in _keys(b)), default=0.0)


def shares_word(a: str, b: str) -> bool:
    """Whether two names have a word in common (after aliases and short
    abbreviations): "Faroe Is." / "Faroe Islands" do, "Faroe Islands" /
    "England" don't, however alike the letters."""
    for ka in _keys(a):
        for kb in _keys(b):
            wa, wb = set(ka.split()), set(kb.split())
            wa, wb = _expand_abbreviations(wa, wb), _expand_abbreviations(wb, wa)
            if any(len(w) >= 3 for w in wa & wb):
                return True
    return False


def find_event(home: str, away: str, events: Iterable[Dict]) -> Optional[Dict]:
    """The event for this fixture: each team must match its own side, clearly,
    and be the same kind of side (never a U21, women's or reserve game for a
    first-team fixture, whether the label is on the teams or the competition)."""
    best, best_score = None, 0.0
    for ev in events:
        h, a = ev.get("homeTeamName") or "", ev.get("awayTeamName") or ""
        if not same_kind(home, away, ev):
            continue
        sh, sa = team_similarity(home, h), team_similarity(away, a)
        if min(sh, sa) < 0.8:
            continue
        # A reversed fixture matches better crossed over — that's a different match
        if team_similarity(home, a) > sh or team_similarity(away, h) > sa:
            continue
        if sh + sa > best_score:
            best, best_score = ev, sh + sa
    return best


# Sides that share a club's or country's name but aren't its first team
_SIDE_MARKER = re.compile(r"\b(u\d{2}|w|women|ladies|fem|reserves?|ii|b|youth|amateurs?)\b", re.I)
KICKOFF_TOLERANCE_MS = 20 * 60 * 1000


def _markers(name: str) -> set:
    return {m.lower() for m in _SIDE_MARKER.findall(name or "")}


# Competitions for youth, women's or reserve sides (SportyBet sometimes says
# so only here: "U21 European Championship, Qualification" with plain names)
_NOT_FIRST_TEAM = re.compile(r"\bu-?\s?\d{2}\b|under[- ]?\d{2}|\byouth\b|\bwomen|\bfemin|\bolympic|\breserve|\bamateur", re.I)


def same_kind(home: str, away: str, ev: Dict) -> bool:
    """Whether the event is the same kind of side as our fixture: the teams
    carry the same markers (U21, W, II…), and a youth / women's / reserve
    competition only ever serves a fixture that is one too."""
    h, a = ev.get("homeTeamName") or "", ev.get("awayTeamName") or ""
    if _markers(home) != _markers(h) or _markers(away) != _markers(a):
        return False
    ours_first_team = not (_markers(home) or _markers(away))
    return not (ours_first_team and _NOT_FIRST_TEAM.search(str(ev.get("_tournament") or "")))


def find_event_by_kickoff(home: str, away: str, kickoff_ms: int, events: Iterable[Dict]) -> Optional[Dict]:
    """
    Second pass for names the strict match misses (club naming differences
    such as "SE Palmeiras" / "Palmeiras SP"): an event starting within 20
    minutes of our kick-off where one team matches clearly and the other is
    recognisably the same side — a close spelling, or a word in common.
    Letter-likeness alone isn't enough ("Faroe Islands" / "England" are 50%
    alike): that is a different opponent, and a link would price and book
    the wrong match. Reversed fixtures and youth / women's / reserve sides
    are never taken.
    """
    best, best_score = None, 0.0
    for ev in events:
        try:
            start = int(ev["estimateStartTime"])
        except (KeyError, TypeError, ValueError):
            continue
        if abs(start - kickoff_ms) > KICKOFF_TOLERANCE_MS:
            continue
        h, a = ev.get("homeTeamName") or "", ev.get("awayTeamName") or ""
        if not same_kind(home, away, ev):
            continue
        sh, sa = team_similarity(home, h), team_similarity(away, a)
        strong, weak = max(sh, sa), min(sh, sa)
        ours, theirs = (away, a) if sa <= sh else (home, h)
        if not (weak >= 0.7 or (strong >= 0.95 and weak >= 0.3 and shares_word(ours, theirs))):
            continue
        if team_similarity(home, a) > sh or team_similarity(away, h) > sa:
            continue
        if sh + sa > best_score:
            best, best_score = ev, sh + sa
    return best


def closest_event(home: str, away: str, events: Iterable[Dict]) -> Tuple[Optional[Dict], float]:
    """The nearest event by names (for diagnosing unlinked predictions)."""
    best, best_score = None, -1.0
    for ev in events:
        score = min(team_similarity(home, ev.get("homeTeamName") or ""),
                    team_similarity(away, ev.get("awayTeamName") or ""))
        if score > best_score:
            best, best_score = ev, score
    return best, max(best_score, 0.0)


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


SHARE_CODE = re.compile(r"^[A-Za-z0-9]{4,16}$")


def parse_share(data: Any) -> List[Dict[str, Any]]:
    """The selections in a loaded booking code: one per chosen outcome, with
    its event, market and SportyBet's current price."""
    events: List[Dict] = []
    _collect_events(data, events)
    out: List[Dict[str, Any]] = []
    for ev in events:
        for m in ev.get("markets") or []:
            for o in m.get("outcomes") or []:
                try:
                    odds = float(o.get("odds"))
                except (TypeError, ValueError):
                    odds = None
                out.append({
                    "eventId": str(ev.get("eventId")), "home": ev.get("homeTeamName") or "",
                    "away": ev.get("awayTeamName") or "", "start": ev.get("estimateStartTime"),
                    "tournament": ev.get("_tournament") or "",
                    "sport_id": str(sport.get("id") or "") if isinstance(sport := ev.get("sport"), dict) else "",
                    "marketId": str(m.get("id") or ""), "specifier": m.get("specifier") or "",
                    "market": _label(m), "outcomeId": str(o.get("id") or ""),
                    "outcome": str(o.get("desc") or o.get("id") or ""), "odds": odds,
                    "active": bool(o.get("isActive", 1)),
                })
    return out


async def load_share_code(code: str, session: Optional[AsyncSession] = None) -> List[Dict[str, Any]]:
    """A booking code's selections (see parse_share). Raises SportyBetError
    when SportyBet doesn't know the code."""
    if not SHARE_CODE.match(code or ""):
        raise SportyBetError("That doesn't look like a SportyBet booking code")
    data = await _request(session or shared_session(), "GET", f"/orders/share/{code.upper()}")
    selections = parse_share(data.get("data"))
    if not selections:
        raise SportyBetError("SportyBet returned no games for that code (expired or already started?)")
    return selections


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
