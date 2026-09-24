"""
International fixtures and results: national-team matches (Nations League,
qualifiers, friendlies) that football-data.org's free tier doesn't carry.

Sources, merged so a match listed by more than one appears once:
  1. ESPN's public scoreboard (no key): fixtures, final scores and flags.
  2. SofaScore's daily schedule (no key): every competition, so friendlies
     and qualifiers still come through if ESPN refuses the server.
  3. The Odds API events list (ODDS_API_KEY; listing sports and events
     doesn't use quota): every international competition it is pricing,
     and matched fixtures learn which sport key their odds live under.

ESPN and SofaScore sit behind bot filters that refuse ordinary server HTTP
clients ("Access Denied"), so requests go through curl_cffi imitating
Chrome, as for SportyBet.

Each fixture is filed under its competition (friendlies, Nations League,
AFCON qualifiers…; see competition()), so the site can list them
separately. The model still sees every international as league "INT"
(`model_league`), the tag its international training results carry.
"""

import asyncio
import os
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

import re

import httpx
from curl_cffi.requests import AsyncSession

from team_names import ALIASES, normalise

LEAGUE_CODE = "INT"   # the model's league for every international match
LEAGUE_INFO = {"name": "Other internationals", "country": "World", "flag": "🌍"}

# Competitions shown separately, in tab order. Names differ between sources
# ("AFCON Qualifying" on ESPN, "Africa Cup of Nations, Qualification" on
# SofaScore), so each is recognised by keywords in its lowercased name.
COMPETITIONS: List[Tuple[str, str, str, str]] = [
    # (code, name, flag, pattern)
    ("INT-WCQ",   "World Cup Qualifiers",       "🏆", r"world cup.*qualif|qualif.*world cup"),
    ("INT-UNL",   "UEFA Nations League",        "🇪🇺", r"^(?!.*concacaf).*nations league"),
    ("INT-ECQ",   "Euro Qualifiers",            "🇪🇺", r"(euro\b|european championship).*qualif|qualif.*(euro\b|european championship)"),
    ("INT-AFCQ",  "AFCON Qualifiers",           "🌍", r"(africa cup|afcon).*qualif|qualif.*(africa cup|afcon)"),
    ("INT-AFCON", "Africa Cup of Nations",      "🌍", r"africa cup|afcon"),
    ("INT-CNL",   "CONCACAF Nations League",    "🌎", r"concacaf.*nations league"),
    ("INT-ACQ",   "Asian Cup Qualifiers",       "🌏", r"asian cup.*qualif|qualif.*asian cup"),
    ("INT-FRI",   "International Friendlies",   "🤝", r"^(?!.*club).*friendl"),
]
_COMPETITION_RE = [(code, name, flag, re.compile(pat)) for code, name, flag, pat in COMPETITIONS]


def competition(name: str) -> Tuple[str, str, str]:
    """(league code, display name, flag) for a source's competition name;
    unrecognised competitions share "Other internationals"."""
    low = (name or "").lower()
    for code, display, flag, pattern in _COMPETITION_RE:
        if pattern.search(low):
            return code, display, flag
    return LEAGUE_CODE, LEAGUE_INFO["name"], LEAGUE_INFO["flag"]


def is_international(league_code: str) -> bool:
    return league_code == LEAGUE_CODE or league_code.startswith(LEAGUE_CODE + "-")

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SOFASCORE_BASE = "https://api.sofascore.com/api/v1/sport/football/scheduled-events"
# A competition's badge, by SofaScore's uniqueTournament id
SOFASCORE_LOGO = "https://api.sofascore.app/api/v1/unique-tournament/{id}/image"
IMPERSONATE = os.getenv("SPORTYBET_IMPERSONATE", "chrome131")
# Days of SofaScore schedule fetched around today (one request per day)
SOFASCORE_DAYS_AHEAD, SOFASCORE_DAYS_BACK = 7, 3

# ESPN league slug → (competition name, The Odds API sport key when known)
ESPN_COMPETITIONS: Dict[str, Tuple[str, Optional[str]]] = {
    "fifa.friendly":           ("International Friendly", None),
    "uefa.nations":            ("UEFA Nations League", "soccer_uefa_nations_league"),
    "fifa.worldq.uefa":        ("World Cup Qualifying · UEFA", "soccer_fifa_world_cup_qualifiers_europe"),
    "fifa.worldq.conmebol":    ("World Cup Qualifying · CONMEBOL", "soccer_fifa_world_cup_qualifiers_south_america"),
    "fifa.worldq.concacaf":    ("World Cup Qualifying · CONCACAF", None),
    "fifa.worldq.caf":         ("World Cup Qualifying · CAF", None),
    "fifa.worldq.afc":         ("World Cup Qualifying · AFC", None),
    "uefa.euroq":              ("Euro Qualifying", None),
    "caf.nations_qual":        ("AFCON Qualifying", None),
    "concacaf.nations.league": ("CONCACAF Nations League", None),
}

# The Odds API sport keys that are national-team competitions
_ODDS_API_HINTS = (
    "nations_league", "world_cup", "friendl", "africa_cup_of_nations",
    "copa_america", "euro_qualification", "european_championship",
    "gold_cup", "asian_cup",
)

# Sent along with the Chrome fingerprint (which brings its own User-Agent)
_BROWSER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
_ESPN_HEADERS = {**_BROWSER_HEADERS, "Referer": "https://www.espn.com/", "Origin": "https://www.espn.com"}
_SOFASCORE_HEADERS = {**_BROWSER_HEADERS, "Referer": "https://www.sofascore.com/",
                      "Origin": "https://www.sofascore.com"}

# SofaScore competitions that are national-team football (when the team
# objects don't say so themselves)
_SOFA_COMPETITIONS = re.compile(
    r"international friendl|nations league|world cup|qualification|africa cup|afcon|"
    r"euro\b|european championship|copa am[eé]rica|gold cup|asian cup", re.I)
_YOUTH = re.compile(r"\bU-?\d{2}\b|\bolympic", re.I)
# Longest span fetched day by day when the date-range request fails
ESPN_DAILY_MAX = 14

# Final scores after extra time or penalties don't grade 90-minute markets
_NOT_REGULATION = ("AET", "PEN", "EXTRA", "SHOOTOUT")


def team_key(name: str) -> str:
    """One key per national team across sources ("USA", "United States")."""
    norm = normalise(name)
    return normalise(ALIASES.get(norm, name))


def _parse_time(value: str) -> Optional[datetime]:
    """ESPN writes "2026-09-24T18:45Z", The Odds API "2026-09-24T18:45:00Z"."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _fixture(match_id: str, home: str, away: str, kickoff: datetime, source_competition: str,
             odds_sport: Optional[str], home_crest=None, away_crest=None,
             competition_logo: Optional[str] = None) -> Dict:
    code, name, flag = competition(source_competition)
    return {
        "match_id": match_id,
        "home": home, "away": away,
        "home_crest": home_crest, "away_crest": away_crest,
        "date": kickoff.strftime("%Y-%m-%d"),
        "time": kickoff.strftime("%H:%M"),
        "league": code,
        "league_name": name,
        "flag": flag,
        "model_league": LEAGUE_CODE,
        "odds_sport": odds_sport,
        "competition_logo": competition_logo,
    }


def competition_logos(fixtures: Iterable[Dict]) -> Dict[str, str]:
    """{league code: badge URL} from the fixtures' own sources. ESPN's CDN
    first (built for embedding), else SofaScore's."""
    logos: Dict[str, str] = {}
    for f in sorted(fixtures, key=lambda f: not str(f.get("match_id", "")).startswith("espn:")):
        if f.get("competition_logo") and f["league"] not in logos:
            logos[f["league"]] = f["competition_logo"]
    return logos


def parse_espn(data: Dict, slug: str) -> Tuple[List[Dict], List[Dict]]:
    """(upcoming fixtures, regulation-time results) from one ESPN scoreboard."""
    name, odds_sport = ESPN_COMPETITIONS.get(slug, (slug, None))
    league = ((data or {}).get("leagues") or [{}])[0]
    logo = next((l.get("href") for l in league.get("logos") or [] if l.get("href")), None)
    fixtures, results = [], []
    for ev in (data or {}).get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        sides = {c.get("homeAway"): c for c in comp.get("competitors") or []}
        home, away = sides.get("home"), sides.get("away")
        if not home or not away:
            continue
        h_name = ((home.get("team") or {}).get("displayName") or "").strip()
        a_name = ((away.get("team") or {}).get("displayName") or "").strip()
        kickoff = _parse_time(ev.get("date") or comp.get("date") or "")
        if not h_name or not a_name or kickoff is None or "TBD" in (h_name + a_name).upper():
            continue

        status = ((comp.get("status") or ev.get("status") or {}).get("type") or {})
        state = status.get("state")
        if state == "pre":
            fixtures.append(_fixture(
                f"espn:{ev.get('id')}", h_name, a_name, kickoff, name, odds_sport,
                (home.get("team") or {}).get("logo"), (away.get("team") or {}).get("logo"), logo,
            ))
        elif state == "post" and status.get("completed"):
            if any(tag in (status.get("name") or "").upper() for tag in _NOT_REGULATION):
                continue
            try:
                hg, ag = int(home.get("score")), int(away.get("score"))
            except (TypeError, ValueError):
                continue
            results.append({
                "Date": kickoff.strftime("%Y-%m-%d"), "HomeTeam": h_name, "AwayTeam": a_name,
                "Result": "H" if hg > ag else "A" if hg < ag else "D",
                "FTHG": hg, "FTAG": ag, "league": LEAGUE_CODE,
            })
    return fixtures, results


def parse_sofascore(data: Dict) -> Tuple[List[Dict], List[Dict]]:
    """(upcoming fixtures, regulation-time results) from one SofaScore day:
    senior men's national-team matches only."""
    fixtures, results = [], []
    for ev in (data or {}).get("events") or []:
        home, away = ev.get("homeTeam") or {}, ev.get("awayTeam") or {}
        h_name, a_name = (home.get("name") or "").strip(), (away.get("name") or "").strip()
        unique = (ev.get("tournament") or {}).get("uniqueTournament") or {}
        comp = (unique or ev.get("tournament") or {}).get("name") or ""
        logo = SOFASCORE_LOGO.format(id=unique["id"]) if unique.get("id") else None
        if not h_name or not a_name or "club" in comp.lower():
            continue
        if "national" in home or "national" in away:
            if not (home.get("national") and away.get("national")):
                continue
        elif not _SOFA_COMPETITIONS.search(comp):
            continue
        if "F" in (home.get("gender"), away.get("gender")) or _YOUTH.search(f"{h_name} {a_name} {comp}"):
            continue
        try:
            kickoff = datetime.fromtimestamp(int(ev["startTimestamp"]), timezone.utc)
        except (KeyError, TypeError, ValueError):
            continue

        status = ev.get("status") or {}
        if status.get("type") == "notstarted":
            fixtures.append(_fixture(f"sofa:{ev.get('id')}", h_name, a_name, kickoff, comp, None,
                                     competition_logo=logo))
        elif status.get("type") == "finished" and (status.get("description") or "Ended") == "Ended":
            hs, as_ = ev.get("homeScore") or {}, ev.get("awayScore") or {}
            try:
                hg = int(hs.get("normaltime", hs.get("current")))
                ag = int(as_.get("normaltime", as_.get("current")))
            except (TypeError, ValueError):
                continue
            results.append({
                "Date": kickoff.strftime("%Y-%m-%d"), "HomeTeam": h_name, "AwayTeam": a_name,
                "Result": "H" if hg > ag else "A" if hg < ag else "D",
                "FTHG": hg, "FTAG": ag, "league": LEAGUE_CODE,
            })
    return fixtures, results


def international_sport_keys(sports: Iterable[Dict]) -> Dict[str, str]:
    """{sport key: title} for The Odds API's national-team soccer competitions."""
    out = {}
    for s in sports or []:
        key = s.get("key") or ""
        if (s.get("group") == "Soccer" and not s.get("has_outrights") and s.get("active", True)
                and any(h in key for h in _ODDS_API_HINTS)):
            out[key] = s.get("title") or key
    return out


def parse_odds_events(events: Iterable[Dict], sport_key: str, title: str,
                      start: date, end: date) -> List[Dict]:
    fixtures = []
    for ev in events or []:
        kickoff = _parse_time(ev.get("commence_time") or "")
        home, away = (ev.get("home_team") or "").strip(), (ev.get("away_team") or "").strip()
        if kickoff is None or not home or not away or not (start <= kickoff.date() <= end):
            continue
        fixtures.append(_fixture(f"odds:{ev.get('id')}", home, away, kickoff, title, sport_key))
    return fixtures


def merge(fixtures: List[Dict]) -> List[Dict]:
    """One fixture per match, earlier sources first. A later duplicate still
    lends its odds sport key to the kept copy."""
    out: List[Dict] = []
    index: Dict[tuple, Dict] = {}
    for f in fixtures:
        key = (f["date"], team_key(f["home"]), team_key(f["away"]))
        if key in index:
            if not index[key].get("odds_sport"):
                index[key]["odds_sport"] = f.get("odds_sport")
            continue
        index[key] = dict(f)
        out.append(index[key])
    return sorted(out, key=lambda f: (f["date"], f["time"], f["home"]))


async def _get_json(client, url: str, params: Dict,
                    headers: Optional[Dict] = None) -> Tuple[Optional[object], Optional[str]]:
    """GET through httpx or curl_cffi (same call shape). Returns (json, error)."""
    try:
        r = await client.get(url, params=params, headers=headers)
    except Exception as e:  # network errors differ between the two clients
        return None, type(e).__name__
    if r.status_code != 200:
        body = (r.text or "").strip()[:80]
        return None, f"HTTP {r.status_code}" + (f" {body!r}" if body else "")
    try:
        return r.json(), None
    except ValueError:
        return None, "not JSON"


async def _espn_pages(client, slug: str,
                      start: date, end: date) -> Tuple[List[Dict], Optional[str]]:
    """
    Scoreboard pages for one competition. One date-range request normally;
    if that fails or comes back empty, ESPN's default (current matchday)
    view decides whether the competition is live, and if so each day is
    fetched on its own. Returns (pages, error when nothing worked).
    """
    url = f"{ESPN_BASE}/{slug}/scoreboard"
    data, err = await _get_json(client, url, {"dates": f"{start:%Y%m%d}-{end:%Y%m%d}", "limit": 500},
                                _ESPN_HEADERS)
    if not err and (data or {}).get("events"):
        return [data], None

    current, current_err = await _get_json(client, url, {}, _ESPN_HEADERS)
    if current_err or not (current or {}).get("events"):
        # A competition with nothing scheduled answers with no events: not an error
        return ([], None) if not err and not current_err else ([], err or current_err)

    pages, day = [current], start
    last = min(end, start + timedelta(days=ESPN_DAILY_MAX - 1))
    while day <= last:
        page, _ = await _get_json(client, url, {"dates": f"{day:%Y%m%d}", "limit": 200}, _ESPN_HEADERS)
        if (page or {}).get("events"):
            pages.append(page)
        day += timedelta(days=1)
    return pages, None


async def fetch_international(days_ahead: int = 21, days_back: int = 0,
                              client=None,
                              today: Optional[date] = None,
                              odds_api_key: Optional[str] = None) -> Dict:
    """
    Upcoming international fixtures (today .. today+days_ahead) and final
    scores (today-days_back .. today). Returns
    {"fixtures", "results",
     "sources": {"espn": {slug: n}, "sofascore": {day: n}, "odds_api": {key: n}}, "errors"}.
    `client` is anything with an async get(url, params=, headers=) (httpx in
    tests); by default a curl_cffi session imitating Chrome.
    """
    today = today or datetime.now(timezone.utc).date()
    start, end = today - timedelta(days=days_back), today + timedelta(days=days_ahead)
    odds_api_key = os.getenv("ODDS_API_KEY", "") if odds_api_key is None else odds_api_key
    report: Dict = {"fixtures": [], "results": [],
                    "sources": {"espn": {}, "sofascore": {}, "odds_api": {}}, "errors": []}

    own_client = client is None
    client = client or AsyncSession(impersonate=IMPERSONATE, timeout=20)
    try:
        espn_fixtures: List[Dict] = []
        for slug in ESPN_COMPETITIONS:
            pages, err = await _espn_pages(client, slug, start, end)
            if err:
                report["errors"].append(f"espn {slug}: {err}")
                continue
            fixtures: List[Dict] = []
            for page in pages:
                found, results = parse_espn(page, slug)
                fixtures += [f for f in found if today.isoformat() <= f["date"] <= end.isoformat()]
                report["results"] += results
            fixtures = merge(fixtures)
            espn_fixtures += fixtures
            report["sources"]["espn"][slug] = len(fixtures)

        sofa_fixtures: List[Dict] = []
        day = today - timedelta(days=min(days_back, SOFASCORE_DAYS_BACK))
        last = today + timedelta(days=min(days_ahead, SOFASCORE_DAYS_AHEAD))
        while day <= last:
            data, err = await _get_json(client, f"{SOFASCORE_BASE}/{day:%Y-%m-%d}", {}, _SOFASCORE_HEADERS)
            if err:
                report["errors"].append(f"sofascore {day:%Y-%m-%d}: {err}")
                if not report["sources"]["sofascore"] and day == today:
                    break  # refused on the first upcoming day: don't hammer it
            else:
                found, results = parse_sofascore(data)
                found = [f for f in found if today.isoformat() <= f["date"] <= end.isoformat()]
                sofa_fixtures += found
                report["results"] += results
                report["sources"]["sofascore"][f"{day:%Y-%m-%d}"] = len(found)
            day += timedelta(days=1)

        odds_fixtures: List[Dict] = []
        if odds_api_key:
            sports, err = await _get_json(client, f"{ODDS_API_BASE}/sports", {"apiKey": odds_api_key})
            if err:
                report["errors"].append(f"odds_api sports: {err}")
            for key, title in international_sport_keys(sports if isinstance(sports, list) else []).items():
                events, err = await _get_json(client, f"{ODDS_API_BASE}/sports/{key}/events",
                                              {"apiKey": odds_api_key})
                if err:
                    report["errors"].append(f"odds_api {key}: {err}")
                    continue
                found = parse_odds_events(events if isinstance(events, list) else [], key, title, today, end)
                odds_fixtures += found
                report["sources"]["odds_api"][key] = len(found)
                await asyncio.sleep(0.2)

        report["fixtures"] = merge(espn_fixtures + sofa_fixtures + odds_fixtures)
    finally:
        if own_client:
            await client.close()
    return report
