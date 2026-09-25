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
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import international_fixtures as intl

KEY = "betiq:club_cups:v1"
SEASONS_BACK = 4          # the current season and the four before it
RECHECK_DAYS = 3
CALENDAR_VERSION = 2       # bump when calendar_days changes: calendars are read again
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


_MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
                                          "Oct", "Nov", "Dec"], 1)}
_DETAIL = re.compile(r"([A-Z][a-z]{2})\s+(\d{1,2})(?:\s*-\s*(?:([A-Z][a-z]{2})\s+)?(\d{1,2}))?")


def _span(lo: date, hi: date) -> List[date]:
    return [lo + timedelta(days=i) for i in range((hi - lo).days + 1)]


def _detail_days(detail: str, lo: date, hi: date) -> Optional[List[date]]:
    """The days in a round's label ("Nov 1-4", "Nov 29-Dec 1", "Oct 9",
    "Sep 17-Jan 29"), the year taken from the round's date range."""
    m = _DETAIL.fullmatch((detail or "").strip())
    if not m or m.group(1) not in _MONTHS or (m.group(3) and m.group(3) not in _MONTHS):
        return None
    m1, d1 = _MONTHS[m.group(1)], int(m.group(2))
    m2, d2 = (_MONTHS[m.group(3)] if m.group(3) else m1), int(m.group(4) or d1)
    try:
        # The year whose date sits in (or nearest) the round's range
        start = min((date(y, m1, d1) for y in (lo.year - 1, lo.year, lo.year + 1)),
                    key=lambda d: 0 if lo <= d <= hi else min(abs((d - lo).days), abs((d - hi).days)))
        end = date(start.year, m2, d2)
        if end < start:
            end = date(start.year + 1, m2, d2)
    except ValueError:
        return None
    return _span(start, end) if (end - start).days <= 200 else None


def calendar_days(page: Any, season: int, kind: str = "cup") -> List[str]:
    """Match days from a scoreboard's league calendar, within the season
    (July to June). ESPN sends either a list of days, or (calendarType
    "list") the season's rounds, each with a label of its days ("Nov 29-Dec
    1") and the range it covers. Long European rounds (the league phase) are
    narrowed to Tuesday-Thursday."""
    lo_s, hi_s = f"{season}-07-01", f"{season + 1}-06-30"
    out = set()

    def add(days: List[date]):
        if kind == "europe" and len(days) > 7:
            days = [d for d in days if d.weekday() in (1, 2, 3)]
        out.update(d.isoformat() for d in days)

    for league in (page or {}).get("leagues") or []:
        for item in league.get("calendar") or []:
            if not isinstance(item, dict):
                out.add(str(item)[:10])
                continue
            entries = item.get("entries")
            if not entries:
                out.add(str(item.get("startDate") or item.get("date") or item.get("value") or "")[:10])
                continue
            for e in entries:
                try:
                    lo = date.fromisoformat(str(e.get("startDate"))[:10])
                    hi = date.fromisoformat(str(e.get("endDate"))[:10])
                except ValueError:
                    continue
                add(_detail_days(e.get("detail"), lo, hi) or _span(lo, hi))
    return sorted(d for d in out if lo_s <= d <= hi_s)


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
    if data.get("calendar_version") != CALENDAR_VERSION:
        calendars.clear()
        data["calendar_version"] = CALENDAR_VERSION
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
                days = calendar_days(page, season, kind)
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


# A day each competition certainly had matches (for probe())
PROBE_DAYS = {
    "uefa.champions": "2025-03-11", "uefa.europa": "2025-03-13", "uefa.europa.conf": "2025-03-13",
    "eng.fa": "2025-03-01", "eng.league_cup": "2024-09-17", "esp.copa_del_rey": "2025-01-15",
    "ita.coppa_italia": "2024-12-04", "ger.dfb_pokal": "2024-12-03", "fra.coupe_de_france": "2025-01-15",
    "ned.cup": "2025-01-15", "por.taca.portugal": "2025-01-15",
}


async def probe(session) -> Dict[str, Any]:
    """What ESPN returns for each competition on a known match day: the
    calendar's shape and how many events, finished results and training
    rows come out of it. For diagnosing a competition that collects nothing."""
    import results_feed
    out: Dict[str, Any] = {}
    for slug, day in PROBE_DAYS.items():
        errors: List[str] = []
        page, status = await _get(session, f"{intl.ESPN_BASE}/{slug}/scoreboard",
                                  {"dates": day.replace("-", ""), "limit": 200}, errors)
        leagues = (page or {}).get("leagues") or [{}]
        cal = leagues[0].get("calendar") or []
        parsed = results_feed.parse_espn(page) if page else []
        out[slug] = {
            "day": day, "status": status, "errors": errors,
            "events": len((page or {}).get("events") or []),
            "event_dates": sorted({str(e.get("date"))[:16] for e in (page or {}).get("events") or []})[:4],
            "finished": sum(1 for p in parsed if p["status"] == "finished"),
            "rows": sum(1 for p in parsed if row_from(p, slug)),
            "calendar_type": leagues[0].get("calendarType"), "calendar_len": len(cal),
            "calendar_head": json.dumps(cal[:2])[:400],
            "calendar_days_2024": len(calendar_days(page, 2024, COMPETITIONS[slug][2])),
            "season": leagues[0].get("season"),
        }
        await asyncio.sleep(PAUSE)
    return out


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


def modes(data: Dict[str, Any]) -> Dict[str, str]:
    """{competition code: "full" | "strength"} — how the last check said each
    approved set goes into training (see check / CONFIGS)."""
    check = data.get("check") or {}
    sets = (check.get("config") or {}).get("sets")
    if sets is None:  # a check from before the ratings-only mode: full rows
        sets = {n: "full" for n, on in (check.get("use") or {}).items() if on}
    out: Dict[str, str] = {}
    for name, codes in (("europe", EUROPE_CODES), ("cups", CUP_CODES)):
        if sets.get(name):
            out.update({c: sets[name] for c in codes})
    return out


def approved(data: Dict[str, Any]) -> set:
    """Competition codes the last check said improve the league predictions."""
    return set(modes(data))


def league_strength(data: Dict[str, Any]) -> bool:
    """Whether the last check chose league strength (predictor.LEAGUE_STRENGTH)."""
    return bool(((data.get("check") or {}).get("config") or {}).get("league_strength"))


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

# The ways the matches can go into training, each tried against the league
# data alone: (name, sets as full training rows, sets as ratings-only rows,
# league strength on). "Ratings only" rows move team Elo and the leagues'
# strength offsets (predictor.LEAGUE_STRENGTH), not form or the training set.
CONFIGS = [
    ("league_strength", (), (), True),
    ("europe_full", ("europe",), (), False),
    ("cups_full", ("cups",), (), False),
    ("europe_strength", (), ("europe",), True),
    ("cups_strength", (), ("cups",), True),
    ("both_strength", (), ("europe", "cups"), True),
]


def _test_months(league) -> List[Tuple[str, str]]:
    """The latest CHECK_MONTHS months with at least 150 league matches."""
    counts = league.groupby(league["Date"].dt.to_period("M")).size()
    months = [p for p, n in counts.items() if n >= 150][-CHECK_MONTHS:]
    return [(str(p.start_time.date()), str(p.end_time.date())) for p in months]


def check(league, extras: Dict[str, Any], log=print) -> Dict[str, Any]:
    """Walk-forward on the latest league months: the model on the league data
    alone vs each way of adding the extra matches ({"europe": df, "cups": df},
    names already resolved to the league data's) — as full training rows, or
    as ratings-only rows with league strength on. Scored on the same league
    matches (1X2 log loss; value-bet profit at Bet365's prices reported too).
    The best way that beats the league data alone by MIN_GAIN is used."""
    import pandas as pd
    import backtest
    from predictor import LeaguePredictor
    months = _test_months(league)
    if not months:
        return {"use": {}, "reason": "no league months to test on", "at": datetime.now(timezone.utc).isoformat()}
    league_codes = set(league["league"].dropna().astype(str))
    have = {name for name, df in extras.items() if df is not None and not df.empty}

    def run(full: Tuple[str, ...], strength: Tuple[str, ...], ls: bool) -> Dict[tuple, Dict]:
        parts = [league] + [extras[n].assign(StrengthOnly=False) for n in full] \
            + [extras[n].assign(StrengthOnly=True) for n in strength]
        data = pd.concat(parts, ignore_index=True) if len(parts) > 1 else league

        def make():
            m = LeaguePredictor()
            m.use_league_strength = ls
            return m
        out = {}
        for start, end in months:
            for r in backtest.walk_forward(data, start, end, make_model=make, log=lambda *_: None):
                if r["league"] in league_codes:
                    out[(r["date"], r["home"], r["away"])] = r
        return out

    def logloss(recs: Dict[tuple, Dict], keys) -> float:
        return backtest._scores_1x2([backtest._model_1x2(recs[k]) for k in keys],
                                    [recs[k]["result"] for k in keys])["log_loss"]

    def money(recs: Dict[tuple, Dict], keys) -> Optional[float]:
        row = next(r for r in backtest.value_scan([recs[k] for k in keys])["1x2"] if r["min_ev"] == 0.05)
        return row["roi"]

    log(f"[Check] league only, months {months}")
    base = run((), (), False)
    result: Dict[str, Any] = {"months": months, "scores": {}, "config": None}
    best, best_ll = None, None
    for name, full, strength, ls in CONFIGS:
        if not set(full + strength) <= have:
            result["scores"][name] = {"skipped": "no matches"}
            continue
        log(f"[Check] {name}")
        recs = run(full, strength, ls)
        keys = sorted(set(base) & set(recs))
        b, w = logloss(base, keys), logloss(recs, keys)
        result["scores"][name] = {"league_only": round(b, 5), "with": round(w, 5), "matches": len(keys),
                                  "value_roi_league_only": money(base, keys), "value_roi_with": money(recs, keys)}
        if len(keys) >= 300 and w <= b - MIN_GAIN and (best_ll is None or w < best_ll):
            best, best_ll = (name, full, strength, ls), w
    if best:
        name, full, strength, ls = best
        result["config"] = {"name": name, "league_strength": ls,
                            "sets": {**{n: "full" for n in full}, **{n: "strength" for n in strength}}}
    # The older shape, read by approved() and the admin panel
    sets = (result["config"] or {}).get("sets") or {}
    result["use"] = {n: bool(sets.get(n)) for n in ("europe", "cups")}
    result["at"] = datetime.now(timezone.utc).isoformat()
    return result
