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
shots on target): [h, a] | None (finished matches, ESPN only), "source"};
ESPN's also carry "stats" ({possession, shots, sot, corners, fouls, ...:
[h, a]}) and "events" (goals and cards) while live and at full time.
"""

import glob
import os
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Set, Tuple

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


# Team statistics shown live on the site: our name → ESPN's names
LIVE_STATS = {"possession": ("possessionPct",), "shots": ("totalShots",), "sot": ("shotsOnTarget",),
              "corners": ("wonCorners", "cornerKicks"), "fouls": ("foulsCommitted",), "offsides": ("offsides",),
              "saves": ("saves",), "yellow": ("yellowCards",), "red": ("redCards",)}
MAX_EVENTS = 30


def _num(team: Dict, *names: str) -> Optional[float]:
    for s in team.get("statistics") or []:
        if s.get("name") in names:
            try:
                return round(float(str(s.get("displayValue", s.get("value"))).rstrip("%")), 1)
            except (TypeError, ValueError):
                return None
    return None


def live_stats(home: Dict, away: Dict) -> Optional[Dict[str, List[float]]]:
    """{stat: [home, away]} for the stats ESPN gives both teams, or None."""
    out = {}
    for key, names in LIVE_STATS.items():
        pair = [_num(home, *names), _num(away, *names)]
        if None not in pair:
            out[key] = pair
    return out or None


def key_events(comp: Dict, home: Dict, away: Dict) -> List[Dict]:
    """Goals and cards in match order: {"minute", "side", "kind", "player"}."""
    ids = {str((home.get("team") or {}).get("id")): "home", str((away.get("team") or {}).get("id")): "away"}
    out = []
    details = comp.get("details")
    for d in details if isinstance(details, list) else []:
        side = ids.get(str((d.get("team") or {}).get("id")))
        if side is None:
            continue
        text = str((d.get("type") or {}).get("text") or "").lower()
        if d.get("ownGoal") or "own goal" in text:
            kind = "own_goal"
        elif d.get("scoringPlay") or "goal" in text:
            kind = "penalty_goal" if d.get("penaltyKick") or "penalty" in text else "goal"
        elif d.get("redCard") or "red card" in text:
            kind = "red"
        elif d.get("yellowCard") or "yellow card" in text:
            kind = "yellow"
        else:
            continue
        who = [str(a.get("displayName") or a.get("shortName") or "")
               for a in d.get("athletesInvolved") or [] if isinstance(a, dict)]
        out.append({"minute": str((d.get("clock") or {}).get("displayValue") or "").strip(),
                    "side": side, "kind": kind, "player": next((w for w in who if w), None)})
    return out[:MAX_EVENTS]


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
            # For the site's live panel (possession, shots... and a goals/cards timeline)
            res["stats"] = live_stats(home, away)
            res["events"] = key_events(comp, home, away) or None
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


# ── API-Football: the backup for matches ESPN lists but never scores ─────────
# (small friendlies, some confederation games). One request lists every match
# of a day worldwide with its score; the free plan allows 100 a day, shared
# with the referee lookup and the nightly stats collector.
AF_BASE = "https://v3.football.api-sports.io"
_AF_LIVE = {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "INT", "SUSP"}
_AF_DONE = {"FT", "AET", "PEN", "AWD", "WO"}
_AF_OFF = {"PST", "CANC", "ABD"}


def parse_api_football(data: Dict) -> List[Dict]:
    """API-Football's fixtures for a day as feed results (live and final only)."""
    out = []
    for f in (data or {}).get("response") or []:
        fx, teams, goals = f.get("fixture") or {}, f.get("teams") or {}, f.get("goals") or {}
        st = (fx.get("status") or {})
        short = (st.get("short") or "").upper()
        if short in _AF_LIVE:
            status = "live"
        elif short in _AF_DONE:
            status = "finished"
        elif short in _AF_OFF:
            status = "postponed"
        else:
            continue
        try:
            ko = datetime.fromisoformat(str(fx.get("date")).replace("Z", "+00:00"))
        except ValueError:
            continue
        home = ((teams.get("home") or {}).get("name") or "").strip()
        away = ((teams.get("away") or {}).get("name") or "").strip()
        if not home or not away:
            continue
        # Bets settle on 90 minutes: after extra time, the regulation score
        ft = (f.get("score") or {}).get("fulltime") or {}
        aet = short in ("AET", "PEN")
        hg, ag = (ft.get("home"), ft.get("away")) if aet else (goals.get("home"), goals.get("away"))
        minute = None
        if status == "live":
            minute = "HT" if short == "HT" else (f"{st.get('elapsed')}'" if st.get("elapsed") is not None else "Live")
        out.append({"date": ko.astimezone(timezone.utc).date().isoformat(), "home": home, "away": away,
                    "status": status, "minute": minute,
                    "hg": _int(hg) if status != "postponed" else None, "ag": _int(ag) if status != "postponed" else None,
                    "aet": aet, "corners": None, "bookings": None, "source": "api-football"})
    return out


async def fetch_api_football(client, day: date, api_key: str) -> Tuple[List[Dict], Optional[str]]:
    """(results, error) for one day from API-Football (dates in UTC)."""
    try:
        r = await client.get(f"{AF_BASE}/fixtures", params={"date": day.isoformat()},
                             headers={"x-apisports-key": api_key})
        data = r.json()
    except Exception as e:
        return [], type(e).__name__
    if data.get("errors") and not data.get("response"):
        return [], str(data["errors"])[:120]
    return parse_api_football(data), None


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
