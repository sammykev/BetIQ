"""
European competitions and domestic cups from ESPN, as extra training data
for the club model (main._assemble_training_data).

The league CSVs cover each league on its own; these matches are where clubs
from different leagues (and divisions) meet, which is most of the evidence
for how strong one league is against another. Each match brings its score
and, where ESPN has them, shots, shots on target, corners and cards.

Collected by GitHub Actions (collect_club_cups.py), a competition-day per
request (ESPN refuses date ranges) on the days in each season's calendar,
newest first; progress lives in Redis (KEY). Whether the matches go into
training is decided by check(): a walk-forward test on the latest season's
league matches, with and without them — separately for European
competitions and domestic cups.
"""

import asyncio
import gzip
import json
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import international_fixtures as intl

KEY = "betiq:club_cups:v1"
SEASONS_BACK = 4          # the current season and the four before it
RECHECK_DAYS = 3
PAUSE = 0.3

# ESPN slug → (our code, name, kind)
COMPETITIONS: Dict[str, Tuple[str, str, str]] = {
    "uefa.champions": ("CL", "Champions League", "europe"),
    "uefa.europa": ("EL", "Europa League", "europe"),
    "uefa.europa.conf": ("UECL", "Conference League", "europe"),
    "eng.fa": ("FAC", "FA Cup", "cup"),
    "eng.league_cup": ("EFLC", "EFL Cup", "cup"),
    "esp.copa_del_rey": ("CDR", "Copa del Rey", "cup"),
    "ita.coppa_italia": ("CIT", "Coppa Italia", "cup"),
    "ger.dfb_pokal": ("DFBP", "DFB-Pokal", "cup"),
    "fra.coupe_de_france": ("CDF", "Coupe de France", "cup"),
    "ned.cup": ("KNVB", "KNVB Beker", "cup"),
    "por.taca.portugal": ("TDP", "Taça de Portugal", "cup"),
}
EUROPE_CODES = {c for c, _, k in COMPETITIONS.values() if k == "europe"}
CUP_CODES = {c for c, _, k in COMPETITIONS.values() if k == "cup"}


def season_start(today: date) -> int:
    return today.year if today.month >= 7 else today.year - 1


def empty() -> Dict[str, Any]:
    return {"rows": {}, "days": [], "calendars": {}, "runs": [], "check": None}


def load(r) -> Dict[str, Any]:
    raw = r.get(KEY) if r is not None else None
    data = empty()
    if raw:
        data.update(json.loads(gzip.decompress(raw)))
    return data


def save(r, data: Dict[str, Any]) -> int:
    blob = gzip.compress(json.dumps(data, separators=(",", ":")).encode())
    r.set(KEY, blob)
    return len(blob)


def calendar_days(page: Any, season: int) -> List[str]:
    """Match days from a scoreboard's league calendar (strings or objects),
    within the season (July to June)."""
    lo, hi = f"{season}-07-01", f"{season + 1}-06-30"
    out = set()
    for league in (page or {}).get("leagues") or []:
        for item in league.get("calendar") or []:
            if isinstance(item, dict):
                item = item.get("startDate") or item.get("date") or item.get("value") or ""
            d = str(item)[:10]
            if lo <= d <= hi:
                out.add(d)
    return sorted(out)


def fallback_days(season: int, kind: str) -> List[str]:
    """Without a calendar: Tuesday to Thursday for European competitions,
    every day for cups."""
    d, end, out = date(season, 7, 1), date(season + 1, 6, 30), []
    while d <= end:
        if kind != "europe" or d.weekday() in (1, 2, 3):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def row_from(res: Dict[str, Any], code: str) -> Optional[Dict[str, Any]]:
    """A training row (the league CSVs' columns) from an ESPN result."""
    if res.get("status") != "finished" or res.get("aet") or res.get("hg") is None or res.get("ag") is None:
        return None
    hg, ag = int(res["hg"]), int(res["ag"])
    row = {"Date": res["date"], "HomeTeam": res["home"], "AwayTeam": res["away"], "FTHG": hg, "FTAG": ag,
           "Result": "H" if hg > ag else "A" if hg < ag else "D", "league": code}
    for cols, key in ((("HS", "AS"), "shots"), (("HST", "AST"), "sot"), (("HC", "AC"), "corners")):
        if res.get(key):
            row[cols[0]], row[cols[1]] = res[key]
    if res.get("bookings"):
        # Booking points (yellow 1, red 2) as yellows: the models add HY + 2·HR
        row["HY"], row["AY"], row["HR"], row["AR"] = res["bookings"][0], res["bookings"][1], 0, 0
    return row


async def _get(session, url: str, params: Dict, errors: List[str]) -> Tuple[Optional[Any], Optional[int]]:
    try:
        r = await session.get(url, headers=intl._ESPN_HEADERS, params=params, timeout=30)
    except Exception as e:
        errors.append(f"{type(e).__name__}: {str(e)[:100]}")
        return None, None
    if r.status_code != 200:
        errors.append(f"HTTP {r.status_code}: {(getattr(r, 'text', '') or '')[:100]}")
        return None, r.status_code
    try:
        return r.json(), 200
    except ValueError:
        return None, r.status_code


async def collect(session, data: Dict[str, Any], deadline: float, today: date,
                  pause: float = PAUSE) -> Dict[str, Any]:
    """Read competition-days newest first until the deadline: first each
    season's calendar (one request), then its match days."""
    import results_feed
    report: Dict[str, Any] = {"requests": 0, "matches": 0, "with_shots": 0, "stopped": None,
                              "statuses": {}, "errors": [], "fallback_calendars": 0}
    errors: List[str] = []
    done = set(data.get("days") or [])
    calendars = data.setdefault("calendars", {})
    recent = {(today - timedelta(days=i)).isoformat() for i in range(1, RECHECK_DAYS + 1)}
    current = season_start(today)

    async def fetch(slug: str, day: str) -> Tuple[Optional[Any], Optional[int]]:
        page, status = await _get(session, f"{intl.ESPN_BASE}/{slug}/scoreboard",
                                  {"dates": day.replace("-", ""), "limit": 200}, errors)
        report["requests"] += 1
        report["statuses"][str(status)] = report["statuses"].get(str(status), 0) + 1
        await asyncio.sleep(pause)
        return page, status

    for season in range(current, current - SEASONS_BACK - 1, -1):
        for slug, (code, _, kind) in COMPETITIONS.items():
            ck = f"{slug}|{season}"
            if ck not in calendars or season == current:
                if time.monotonic() > deadline:
                    report["stopped"] = "time"
                    break
                probe = f"{season}-10-15" if kind == "europe" else f"{season + 1}-01-15"
                page, status = await fetch(slug, probe)
                if status in (403, 429):
                    report["stopped"] = f"HTTP {status}"
                    break
                days = calendar_days(page, season)
                if not days:
                    days = fallback_days(season, kind)
                    report["fallback_calendars"] += 1
                calendars[ck] = days
            todo = [d for d in reversed(calendars[ck])
                    if d < today.isoformat() and (d in recent or f"{d}|{slug}" not in done)]
            for day in todo:
                if time.monotonic() > deadline:
                    report["stopped"] = "time"
                    break
                if report["requests"] >= 30 and not report["statuses"].get("200"):
                    report["stopped"] = "every request failed (see errors)"
                    break
                page, status = await fetch(slug, day)
                if page is None:
                    if status in (403, 429):
                        report["stopped"] = f"HTTP {status}"
                        break
                    if status == 400:
                        done.add(f"{day}|{slug}")  # nothing on that day
                    continue
                for res in results_feed.parse_espn(page):
                    row = row_from(res, code)
                    if row:
                        k = f"{row['Date']}|{row['HomeTeam']}|{row['AwayTeam']}"
                        if k not in data["rows"]:
                            report["matches"] += 1
                            report["with_shots"] += "HST" in row
                        data["rows"][k] = row
                done.add(f"{day}|{slug}")
            if report["stopped"]:
                break
        if report["stopped"]:
            break
    data["days"] = sorted(done)
    report["errors"] = list(dict.fromkeys(errors))[:5]
    return report


def rows_frame(data: Dict[str, Any], codes: Optional[set] = None):
    """The collected matches as a DataFrame in the league CSVs' shape
    (optionally only some competitions)."""
    import pandas as pd
    rows = [r for r in (data.get("rows") or {}).values() if codes is None or r.get("league") in codes]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["Date"])
    return df.sort_values("Date").reset_index(drop=True)


def approved(data: Dict[str, Any]) -> set:
    """Competition codes the last check said improve the league predictions."""
    check = data.get("check") or {}
    out = set()
    if (check.get("use") or {}).get("europe"):
        out |= EUROPE_CODES
    if (check.get("use") or {}).get("cups"):
        out |= CUP_CODES
    return out


def summary(data: Dict[str, Any]) -> Dict[str, Any]:
    rows = list((data.get("rows") or {}).values())
    by: Dict[str, int] = {}
    for r in rows:
        by[r.get("league", "?")] = by.get(r.get("league", "?"), 0) + 1
    return {"matches": len(rows), "with_shots": sum(1 for r in rows if "HST" in r), "by_competition": by,
            "first": min((r["Date"] for r in rows), default=None), "last": max((r["Date"] for r in rows), default=None),
            "days_read": len(data.get("days") or []), "check": data.get("check"),
            "last_run": (data.get("runs") or [None])[-1]}


# ── The check: do these matches improve the league predictions? ──────────
CHECK_MONTHS = 3          # test months (the latest full ones in the league data)
MIN_GAIN = 0.001          # 1X2 log loss must drop by at least this much


def _test_months(league) -> List[Tuple[str, str]]:
    """The latest CHECK_MONTHS months with at least 150 league matches."""
    import pandas as pd
    counts = league.groupby(league["Date"].dt.to_period("M")).size()
    months = [p for p, n in counts.items() if n >= 150][-CHECK_MONTHS:]
    return [(str(p.start_time.date()), str(p.end_time.date())) for p in months]


def check(league, extras: Dict[str, Any], log=print) -> Dict[str, Any]:
    """Walk-forward on the latest league months: the model trained on the
    league data alone vs with each set of extra matches ({"europe": df,
    "cups": df}, names already resolved to the league data's). Scored on the
    same league matches (1X2 log loss). A set is used if it lowers it."""
    import pandas as pd
    import backtest
    months = _test_months(league)
    if not months:
        return {"use": {}, "reason": "no league months to test on", "at": datetime.now(timezone.utc).isoformat()}
    league_codes = set(league["league"].dropna().astype(str))

    def run(extra) -> Dict[tuple, Dict]:
        data = league if extra is None or extra.empty else pd.concat([league, extra], ignore_index=True)
        out = {}
        for start, end in months:
            for r in backtest.walk_forward(data, start, end, log=lambda *_: None):
                if r["league"] in league_codes:
                    out[(r["date"], r["home"], r["away"])] = r
        return out

    def logloss(recs: Dict[tuple, Dict], keys) -> float:
        return backtest._scores_1x2([backtest._model_1x2(recs[k]) for k in keys],
                                    [recs[k]["result"] for k in keys])["log_loss"]

    log(f"[Check] league only, months {months}")
    base = run(None)
    result: Dict[str, Any] = {"months": months, "use": {}, "scores": {}}
    for name, extra in extras.items():
        if extra is None or extra.empty:
            result["scores"][name] = {"skipped": "no matches"}
            continue
        log(f"[Check] + {name} ({len(extra)} matches)")
        with_extra = run(extra)
        keys = sorted(set(base) & set(with_extra))
        b, w = logloss(base, keys), logloss(with_extra, keys)
        result["scores"][name] = {"league_only": round(b, 5), "with": round(w, 5), "matches": len(keys),
                                  "extra_rows": len(extra)}
        result["use"][name] = len(keys) >= 300 and w <= b - MIN_GAIN
    result["at"] = datetime.now(timezone.utc).isoformat()
    return result
