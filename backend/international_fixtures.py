"""
International fixtures and results: national-team matches (Nations League,
qualifiers, friendlies) that football-data.org's free tier doesn't carry.

Sources, merged so a match listed by both appears once:
  1. ESPN's public scoreboard (no key): fixtures, final scores and flags.
  2. The Odds API events list (ODDS_API_KEY; listing sports and events
     doesn't use quota): every international competition it is pricing,
     so one ESPN doesn't carry still shows up, and matched fixtures learn
     which sport key their odds live under.

Every fixture is filed under one league, "INT" — the same tag the
international training results carry, so the model's league features line up.
"""

import asyncio
import os
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

import httpx

from team_names import ALIASES, normalise

LEAGUE_CODE = "INT"
LEAGUE_INFO = {"name": "Internationals", "country": "World", "flag": "🌍"}

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

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


def _fixture(match_id: str, home: str, away: str, kickoff: datetime, competition: str,
             odds_sport: Optional[str], home_crest=None, away_crest=None) -> Dict:
    return {
        "match_id": match_id,
        "home": home, "away": away,
        "home_crest": home_crest, "away_crest": away_crest,
        "date": kickoff.strftime("%Y-%m-%d"),
        "time": kickoff.strftime("%H:%M"),
        "league": LEAGUE_CODE,
        "league_name": competition,
        "flag": LEAGUE_INFO["flag"],
        "odds_sport": odds_sport,
    }


def parse_espn(data: Dict, slug: str) -> Tuple[List[Dict], List[Dict]]:
    """(upcoming fixtures, regulation-time results) from one ESPN scoreboard."""
    name, odds_sport = ESPN_COMPETITIONS.get(slug, (slug, None))
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
                (home.get("team") or {}).get("logo"), (away.get("team") or {}).get("logo"),
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


async def _get_json(client: httpx.AsyncClient, url: str, params: Dict) -> Tuple[Optional[object], Optional[str]]:
    try:
        r = await client.get(url, params=params)
    except httpx.HTTPError as e:
        return None, type(e).__name__
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    try:
        return r.json(), None
    except ValueError:
        return None, "not JSON"


async def fetch_international(days_ahead: int = 21, days_back: int = 0,
                              client: Optional[httpx.AsyncClient] = None,
                              today: Optional[date] = None,
                              odds_api_key: Optional[str] = None) -> Dict:
    """
    Upcoming international fixtures (today .. today+days_ahead) and final
    scores (today-days_back .. today). Returns
    {"fixtures", "results", "sources": {"espn": {slug: n}, "odds_api": {key: n}}, "errors"}.
    """
    today = today or datetime.now(timezone.utc).date()
    start, end = today - timedelta(days=days_back), today + timedelta(days=days_ahead)
    odds_api_key = os.getenv("ODDS_API_KEY", "") if odds_api_key is None else odds_api_key
    report: Dict = {"fixtures": [], "results": [], "sources": {"espn": {}, "odds_api": {}}, "errors": []}

    own_client = client is None
    client = client or httpx.AsyncClient(timeout=20, headers={"User-Agent": "Mozilla/5.0 BetIQ/1.0"})
    try:
        espn_fixtures: List[Dict] = []
        dates = f"{start:%Y%m%d}-{end:%Y%m%d}"
        for slug in ESPN_COMPETITIONS:
            data, err = await _get_json(client, f"{ESPN_BASE}/{slug}/scoreboard",
                                        {"dates": dates, "limit": 500})
            if err:
                report["errors"].append(f"espn {slug}: {err}")
                continue
            fixtures, results = parse_espn(data, slug)
            fixtures = [f for f in fixtures if f["date"] >= today.isoformat()]
            espn_fixtures += fixtures
            report["results"] += results
            report["sources"]["espn"][slug] = len(fixtures)

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

        report["fixtures"] = merge(espn_fixtures + odds_fixtures)
    finally:
        if own_client:
            await client.aclose()
    return report
