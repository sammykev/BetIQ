"""
Fixtures for the European top flights only SportyBet covers (group B:
Serbia, Croatia, Bulgaria, Hungary, Ukraine and the rest of
collect_sb_leagues.COUNTRIES), taken from the catalog the SportyBet linker
already downloads, with SportyBet's 1X2 prices attached. Their history is
SportyBet's results (collect_sb_leagues.py, as context rows of the main
model), their scores SportyBet's too.

Only the result and goals markets are offered (goals_only): there are no
corners, cards or shots for these leagues. Whether our predictions hold up
in each is checked walk-forward (check_extra_leagues.py, without odds); a
league that doesn't pass is published with a warning and kept out of slips.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

import collect_sb_leagues as sb

KEY = "betiq:sb_league_fixtures:v1"
MIN_MATCHES = 60            # a league with less history isn't published

# our code (as in collect_sb_leagues) → (country, flag)
COUNTRIES: Dict[str, tuple] = {
    "ALB": ("Albania", "🇦🇱"), "AND": ("Andorra", "🇦🇩"), "ARM": ("Armenia", "🇦🇲"), "AZE": ("Azerbaijan", "🇦🇿"),
    "BLR": ("Belarus", "🇧🇾"), "BIH": ("Bosnia", "🇧🇦"), "BUL": ("Bulgaria", "🇧🇬"), "CRO": ("Croatia", "🇭🇷"),
    "EST": ("Estonia", "🇪🇪"), "FRO": ("Faroe Islands", "🇫🇴"), "GEO": ("Georgia", "🇬🇪"), "GIB": ("Gibraltar", "🇬🇮"),
    "HUN": ("Hungary", "🇭🇺"), "ISL": ("Iceland", "🇮🇸"), "KAZ": ("Kazakhstan", "🇰🇿"), "KOS": ("Kosovo", "🇽🇰"),
    "LVA": ("Latvia", "🇱🇻"), "LTU": ("Lithuania", "🇱🇹"), "LUX": ("Luxembourg", "🇱🇺"), "MKD": ("North Macedonia", "🇲🇰"),
    "MDA": ("Moldova", "🇲🇩"), "MNE": ("Montenegro", "🇲🇪"), "SMR": ("San Marino", "🇸🇲"), "SRB": ("Serbia", "🇷🇸"),
    "SVK": ("Slovakia", "🇸🇰"), "SVN": ("Slovenia", "🇸🇮"), "UKR": ("Ukraine", "🇺🇦"), "LIE": ("Liechtenstein", "🇱🇮"),
}


def leagues(data: Dict) -> Dict[str, str]:
    """{league code ("SB-SRB"): tournament} — each country's top flight with
    at least MIN_MATCHES matches of history."""
    keep = sb.selected(data)
    counts: Dict[str, int] = {}
    for r in (data.get("rows") or {}).values():
        if keep.get(r["league"]) == r.get("tournament"):
            counts[r["league"]] = counts.get(r["league"], 0) + 1
    return {code: name for code, name in keep.items() if counts.get(code, 0) >= MIN_MATCHES}


def league_name(code: str, tournament: str) -> str:
    country = COUNTRIES.get(code.removeprefix("SB-"), (code, ""))[0]
    return f"{country} {tournament}"


def flag(code: str) -> str:
    return COUNTRIES.get(code.removeprefix("SB-"), ("", "🏳️"))[1]


def _odds(ev: Dict) -> Dict[str, float]:
    """SportyBet's 1X2 prices on a listing event ({"1", "X", "2"}), or {}."""
    for m in ev.get("markets") or []:
        if str(m.get("id")) == "1" and not (m.get("specifier") or ""):
            out = {}
            for o in m.get("outcomes") or []:
                code = {"1": "1", "2": "X", "3": "2"}.get(str(o.get("id")))
                try:
                    price = float(o.get("odds") or 0)
                except (TypeError, ValueError):
                    price = 0.0
                if code and price > 1 and o.get("isActive", 1):
                    out[code] = price
            return out if len(out) == 3 else {}
    return {}


def from_catalog(events: Iterable[Dict], picked: Dict[str, str], today: Optional[date] = None,
                 days_ahead: int = 14) -> List[Dict]:
    """The picked leagues' upcoming matches in SportyBet's catalog, as fixtures."""
    import results_feed
    today = today or datetime.now(timezone.utc).date()
    end = today + timedelta(days=days_ahead)
    wanted = {(code.removeprefix("SB-"), name): code for code, name in picked.items()}
    out: Dict[str, Dict] = {}
    for ev in events:
        cat, name = sb.split_tournament(ev)
        code = wanted.get((sb.COUNTRIES.get(cat.lower(), ""), name))
        if not code or results_feed.not_senior(ev):
            continue
        try:
            ko = datetime.fromtimestamp(int(ev["estimateStartTime"]) / 1000, timezone.utc)
        except (KeyError, TypeError, ValueError):
            continue
        if not today <= ko.date() <= end:
            continue
        odds = _odds(ev)
        out[str(ev["eventId"])] = {
            "match_id": f"sb:{ev['eventId']}", "home": str(ev["homeTeamName"]).strip(),
            "away": str(ev["awayTeamName"]).strip(), "date": ko.strftime("%Y-%m-%d"), "time": ko.strftime("%H:%M"),
            "league": code, "league_name": league_name(code, name), "flag": flag(code),
            "goals_only": True, **({"sb_odds": odds} if odds else {}),
        }
    return list(out.values())
