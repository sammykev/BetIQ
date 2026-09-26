"""
Upcoming Europa League and Conference League fixtures from ESPN's public
scoreboard: neither is in football-data.org's free plan (403).

The model only knows a club through the matches it trains on: the league
CSVs (England, Spain, Italy, Germany, France, the Netherlands, Portugal),
the Champions League CSVs and saved results. A Europa League club from a
league outside those (Scotland, Turkey, Greece, Austria, Norway...) has few
or no matches there, and the model would rate it an average top-league
side. Such fixtures aren't published: known() keeps the ones where both
clubs played at least MIN_MATCHES training matches in the last RECENT_DAYS.

With the Europe model (europe_model.py) adopted, "known" is judged on its
data, which includes those extra leagues. Scores come in through
results_feed.py (ESPN), which grades the published predictions like any
other.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

import international_fixtures as intl

# code: (ESPN slug, name, flag, The Odds API sport key)
COMPETITIONS: Dict[str, Tuple[str, str, str, str]] = {
    "EL": ("uefa.europa", "Europa League", "🟠", "soccer_uefa_europa_league"),
    "UECL": ("uefa.europa.conf", "Conference League", "🟢", "soccer_uefa_europa_conference_league"),
}
MIN_MATCHES = 15
RECENT_DAYS = 400


def to_fixture(f: Dict, code: str) -> Dict:
    """intl.parse_espn's fixture (filed as an international) as a club one."""
    slug, name, flag, odds_sport = COMPETITIONS[code]
    out = {k: v for k, v in f.items() if k != "model_league"}
    return {**out, "league": code, "league_name": name, "flag": flag, "odds_sport": odds_sport}


async def fetch(days_ahead: int, today: Optional[date] = None, client=None) -> Dict:
    """{"fixtures", "sources": {code: n}, "errors"} for today..today+days_ahead."""
    from curl_cffi.requests import AsyncSession
    today = today or datetime.now(timezone.utc).date()
    end = today + timedelta(days=days_ahead)
    report: Dict = {"fixtures": [], "sources": {}, "errors": []}
    own = client is None
    client = client or AsyncSession(impersonate=intl.IMPERSONATE, timeout=20)
    try:
        for code, (slug, *_rest) in COMPETITIONS.items():
            pages, err = await intl._espn_pages(client, slug, today, end)
            if err:
                report["errors"].append(f"espn {slug}: {err}")
                continue
            found: List[Dict] = []
            for page in pages:
                fixtures, _ = intl.parse_espn(page, slug)
                found += [to_fixture(f, code) for f in fixtures
                          if today.isoformat() <= f["date"] <= end.isoformat()]
            found = list({f["match_id"]: f for f in found}.values())
            report["fixtures"] += found
            report["sources"][code] = len(found)
    finally:
        if own:
            await client.close()
    return report


def recent_counts(results, today: Optional[date] = None, days: int = RECENT_DAYS) -> Dict[str, int]:
    """{team: matches since today-days} from a results frame (training data)."""
    import pandas as pd
    if results is None or results.empty:
        return {}
    since = pd.Timestamp(today or date.today()) - pd.Timedelta(days=days)
    recent = results[pd.to_datetime(results["Date"], errors="coerce") >= since]
    return pd.concat([recent["HomeTeam"], recent["AwayTeam"]]).value_counts().to_dict()


def known(fixtures: List[Dict], canon: Callable[[str], str],
          counts: Dict[str, int]) -> Tuple[List[Dict], List[Dict]]:
    """(fixtures whose clubs the model knows, skipped ones with each side's
    recent match count)."""
    keep, skipped = [], []
    for f in fixtures:
        n = {side: counts.get(canon(f[side]), 0) for side in ("home", "away")}
        if min(n.values()) >= MIN_MATCHES:
            keep.append(f)
        else:
            skipped.append({"home": f["home"], "away": f["away"], "date": f["date"],
                            "home_matches": n["home"], "away_matches": n["away"]})
    return keep, skipped
