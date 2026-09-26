"""
Scores for the match days (matchday.py): live and final, with corners and
bookings where the source has them.

1. ESPN's public scoreboards (no key; the server already reads them for
   internationals): one request per competition per day, with the match
   status (not started / minute / half-time / full time / postponed), the
   score, corners (team statistics) and cards (match events).
2. football-data.co.uk's league CSVs (synced daily into data/football/):
   final scores with corners and cards, a day or two after the match —
   fills anything ESPN missed and the stats it doesn't carry.

Every result: {"date" (UTC kick-off date), "home", "away", "status",
"minute", "hg", "ag", "aet", "corners": [h, a] | None, "bookings": [h, a] |
None (yellow 1, red 2 — SportyBet's booking points), "shots" / "sot" (shots,
shots on target): [h, a] | None (finished matches, ESPN only), "source"}.
"""

import glob
import os
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Set

import international_fixtures as intl

ESPN_SLUGS = {
    "PL": "eng.1", "ELC": "eng.2", "PD": "esp.1", "SA": "ita.1", "BL1": "ger.1", "BL2": "ger.2",
    "FL1": "fra.1", "DED": "ned.1", "PPL": "por.1", "BSA": "bra.1",
    "CL": "uefa.champions", "EL": "uefa.europa", "UECL": "uefa.europa.conf", "EC": "uefa.euro", "WC": "fifa.world",
}
CSV_DIR = os.path.join(os.path.dirname(__file__), "data", "football")


def slugs_for(league: str) -> List[str]:
    """ESPN competitions to look in for a league code (every national-team
    competition for internationals)."""
    if intl.is_international(league or ""):
        return list(intl.ESPN_COMPETITIONS)
    slug = ESPN_SLUGS.get(league or "")
    return [slug] if slug else []


def _int(v) -> Optional[int]:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _stat(team: Dict, *names: str) -> Optional[int]:
    for s in team.get("statistics") or []:
        if s.get("name") in names:
            return _int(s.get("displayValue", s.get("value")))
    return None


def parse_espn(data: Dict) -> List[Dict]:
    """Every match on one ESPN scoreboard, with its status."""
    out = []
    for ev in (data or {}).get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        sides = {c.get("homeAway"): c for c in comp.get("competitors") or []}
        home, away = sides.get("home"), sides.get("away")
        if not home or not away:
            continue
        names = [((s.get("team") or {}).get("displayName") or "").strip() for s in (home, away)]
        try:
            ko = datetime.fromisoformat((ev.get("date") or comp.get("date") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if not all(names):
            continue
        st = (comp.get("status") or ev.get("status") or {})
        t = st.get("type") or {}
        state, name = t.get("state"), (t.get("name") or "").upper()
        if "POSTPON" in name or "CANCEL" in name or "ABANDON" in name or "SUSPEND" in name:
            status = "postponed"
        elif state == "post" and t.get("completed", True):
            status = "finished"
        elif state == "in":
            status = "live"
        else:
            status = "scheduled"
        res = {"date": ko.astimezone(timezone.utc).date().isoformat(), "home": names[0], "away": names[1],
               "status": status, "minute": t.get("shortDetail") if status == "live" else None,
               "hg": None, "ag": None, "aet": any(x in name for x in intl._NOT_REGULATION),
               "corners": None, "bookings": None, "source": "espn"}
        if status in ("live", "finished"):
            res["hg"], res["ag"] = _int(home.get("score")), _int(away.get("score"))
        if status == "finished":
            corners = [_stat(home, "wonCorners", "cornerKicks"), _stat(away, "wonCorners", "cornerKicks")]
            if None not in corners:
                res["corners"] = corners
            res["bookings"] = _bookings(comp, home, away)
            for key, names in (("shots", ("totalShots",)), ("sot", ("shotsOnTarget",))):
                pair = [_stat(home, *names), _stat(away, *names)]
                res[key] = pair if None not in pair else None
        out.append(res)
    return out


def _bookings(comp: Dict, home: Dict, away: Dict) -> Optional[List[int]]:
    """Booking points per team (yellow 1, red 2) from the team statistics,
    else from the match's card events."""
    y = [_stat(home, "yellowCards"), _stat(away, "yellowCards")]
    r = [_stat(home, "redCards"), _stat(away, "redCards")]
    if None not in y and None not in r:
        return [y[0] + 2 * r[0], y[1] + 2 * r[1]]
    details = comp.get("details")
    if not isinstance(details, list):
        return None
    ids = [str((s.get("team") or {}).get("id")) for s in (home, away)]
    points = [0, 0]
    for d in details:
        side = ids.index(str((d.get("team") or {}).get("id"))) if str((d.get("team") or {}).get("id")) in ids else None
        if side is None:
            continue
        if d.get("redCard"):
            points[side] += 2
        elif d.get("yellowCard"):
            points[side] += 1
    return points


async def fetch_espn(client, days: Iterable[date], slugs: Iterable[str]) -> Dict[str, object]:
    """{"results": [...], "requests": n, "errors": [...]} — one scoreboard
    per competition per day (ESPN dates by US Eastern time, so a late UTC
    kick-off can sit on the day before: callers ask for both)."""
    results: List[Dict] = []
    errors: List[str] = []
    requests = 0
    for d in sorted(set(days)):
        for slug in sorted(set(slugs)):
            data, err = await intl._get_json(client, f"{intl.ESPN_BASE}/{slug}/scoreboard",
                                             {"dates": d.strftime("%Y%m%d"), "limit": 200}, intl._ESPN_HEADERS)
            requests += 1
            if err:
                errors.append(f"{slug} {d}: {err}")
                continue
            results += parse_espn(data)
    return {"results": results, "requests": requests, "errors": errors}


_csv_cache: Dict[str, object] = {"key": None, "rows": []}


def csv_results(days: Set[str], csv_dir: str = CSV_DIR) -> List[Dict]:
    """Final scores with corners and cards from the synced league CSVs, for
    the given dates (the current and last season's files)."""
    import pandas as pd
    files = sorted(glob.glob(os.path.join(csv_dir, "*.csv")))[-40:]
    stamp = tuple((f, os.path.getmtime(f)) for f in files)
    if _csv_cache["key"] != stamp:
        rows = []
        for f in files:
            try:
                df = pd.read_csv(f, encoding="utf-8", on_bad_lines="skip", low_memory=False)
            except Exception:
                continue
            df.columns = [c.strip() for c in df.columns]
            if not {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"} <= set(df.columns):
                continue
            df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
            df = df[df["Date"] >= pd.Timestamp(date.today() - timedelta(days=120))]
            for _, r in df.iterrows():
                hg, ag = _int(r.get("FTHG")), _int(r.get("FTAG"))
                if hg is None or ag is None or pd.isna(r["Date"]):
                    continue
                stats = {k: _int(r.get(k)) for k in ("HC", "AC", "HY", "AY", "HR", "AR")}
                shots = {k: _int(r.get(k)) for k in ("HS", "AS", "HST", "AST")}
                rows.append({
                    "date": r["Date"].date().isoformat(), "home": str(r["HomeTeam"]), "away": str(r["AwayTeam"]),
                    "status": "finished", "minute": None, "hg": hg, "ag": ag, "aet": False,
                    "corners": [stats["HC"], stats["AC"]] if None not in (stats["HC"], stats["AC"]) else None,
                    "bookings": ([stats["HY"] + 2 * stats["HR"], stats["AY"] + 2 * stats["AR"]]
                                 if None not in stats.values() else None),
                    "shots": [shots["HS"], shots["AS"]] if None not in (shots["HS"], shots["AS"]) else None,
                    "sot": [shots["HST"], shots["AST"]] if None not in (shots["HST"], shots["AST"]) else None,
                    "source": "football-data.co.uk"})
        _csv_cache.update(key=stamp, rows=rows)
    return [r for r in _csv_cache["rows"] if r["date"] in days]
