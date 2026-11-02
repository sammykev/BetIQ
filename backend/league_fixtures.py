"""
Fixtures for the European leagues we predict beyond football-data.org's free
plan (group A: Belgium, Scotland, Greece, Turkey, Austria, Switzerland,
Denmark, Norway, Sweden, Poland, Romania; and beyond Europe Argentina,
Mexico, MLS, Japan and China), from ESPN — as the Europa League's
come (europe_fixtures.py). Their history comes from football-data.co.uk's
CSVs (main._load_extra_leagues, as context rows of the main model), their
scores from ESPN (results_feed.ESPN_SLUGS) and their corners, cards and
shots from the same CSVs (results_feed.csv_results).

Whether our predictions hold up in each league is checked walk-forward
(check_extra_leagues.py); a league that doesn't pass is published with a
warning (league_unproven) and kept out of slips.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import international_fixtures as intl

# code: (ESPN slug, name, country, flag)
COMPETITIONS: Dict[str, Tuple[str, str, str, str]] = {
    "JPL": ("bel.1", "Belgian Pro League", "Belgium", "🇧🇪"),
    "SPL": ("sco.1", "Scottish Premiership", "Scotland", "🏴󠁧󠁢󠁳󠁣󠁴󠁿"),
    "GSL": ("gre.1", "Greek Super League", "Greece", "🇬🇷"),
    "TSL": ("tur.1", "Süper Lig", "Turkey", "🇹🇷"),
    "AUT": ("aut.1", "Austrian Bundesliga", "Austria", "🇦🇹"),
    "SUI": ("sui.1", "Swiss Super League", "Switzerland", "🇨🇭"),
    "DEN": ("den.1", "Danish Superliga", "Denmark", "🇩🇰"),
    "NOR": ("nor.1", "Eliteserien", "Norway", "🇳🇴"),
    "SWE": ("swe.1", "Allsvenskan", "Sweden", "🇸🇪"),
    "POL": ("pol.1", "Ekstraklasa", "Poland", "🇵🇱"),
    "ROU": ("rou.1", "Romanian Liga I", "Romania", "🇷🇴"),
    # Beyond Europe (football-data.co.uk/new/ history, ESPN fixtures)
    "ARG": ("arg.1", "Argentine Primera División", "Argentina", "🇦🇷"),
    "MEX": ("mex.1", "Liga MX", "Mexico", "🇲🇽"),
    "MLS": ("usa.1", "Major League Soccer", "USA", "🇺🇸"),
    "JPN": ("jpn.1", "J1 League", "Japan", "🇯🇵"),
    "CHN": ("chn.1", "Chinese Super League", "China", "🇨🇳"),
    # Second divisions (football-data.co.uk history alongside the top flights)
    "EL1": ("eng.3", "League One", "England", "🏴󠁧󠁢󠁥󠁮󠁧󠁿"),
    "EL2": ("eng.4", "League Two", "England", "🏴󠁧󠁢󠁥󠁮󠁧󠁿"),
    "ENL": ("eng.5", "National League", "England", "🏴󠁧󠁢󠁥󠁮󠁧󠁿"),
    "SD": ("esp.2", "LaLiga 2", "Spain", "🇪🇸"),
    "SB": ("ita.2", "Serie B", "Italy", "🇮🇹"),
    "FL2": ("fra.2", "Ligue 2", "France", "🇫🇷"),
}
UNPROVEN_KEY = "betiq:extra_leagues:check"


def to_fixture(f: Dict, code: str) -> Dict:
    slug, name, _country, flag = COMPETITIONS[code]
    out = {k: v for k, v in f.items() if k != "model_league"}
    return {**out, "league": code, "league_name": name, "flag": flag}


def _upcoming(pages: List[Dict], slug: str, code: str, today: date, end: date) -> List[Dict]:
    found: List[Dict] = []
    for page in pages or []:
        fixtures, _ = intl.parse_espn(page, slug)
        found += [to_fixture(f, code) for f in fixtures if today.isoformat() <= f["date"] <= end.isoformat()]
    return list({f["match_id"]: f for f in found}.values())


async def fetch(days_ahead: int, today: Optional[date] = None, client=None) -> Dict:
    """{"fixtures", "sources": {code: n}, "errors"} for today..today+days_ahead."""
    import europe_fixtures
    from curl_cffi.requests import AsyncSession
    today = today or datetime.now(timezone.utc).date()
    end = today + timedelta(days=days_ahead)
    report: Dict = {"fixtures": [], "sources": {}, "errors": []}
    own = client is None
    client = client or AsyncSession(impersonate=intl.IMPERSONATE, timeout=20)
    try:
        for code, (slug, *_r) in COMPETITIONS.items():
            pages, err = await intl._espn_pages(client, slug, today, end)
            found = _upcoming(pages, slug, code, today, end)
            if not found:
                pages, day_err = await europe_fixtures._daily_pages(client, slug, today, end)
                found = _upcoming(pages, slug, code, today, end)
                err = err if not pages else None
                err = err or (day_err if not pages else None)
            if err and not found:
                report["errors"].append(f"espn {slug}: {err}")
            report["fixtures"] += found
            report["sources"][code] = len(found)
    finally:
        if own:
            await client.close()
    return report


def unproven(r, codes: Optional[set] = None) -> set:
    """Of `codes` (default: COMPETITIONS), those whose walk-forward check
    didn't pass or hasn't run (published with a warning)."""
    import json
    codes = set(COMPETITIONS) if codes is None else set(codes)
    try:
        got = json.loads(r.get(UNPROVEN_KEY) or "{}") if r else {}
    except Exception:
        return codes
    leagues = got.get("leagues") or {}
    return {c for c in codes if not (leagues.get(c) or {}).get("pass")}
