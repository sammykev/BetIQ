"""
Match days: pre-match predictions, results, grades (matchday.py), accuracy and history.

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import httpx
import international_fixtures as intl
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import Any, Dict, List, Optional, Tuple

import main

router = APIRouter()


def _md_load(r, d: str) -> Dict[str, Dict]:
    """One date's entries ({match key: entry}); the old history format
    (betiq:history:{date}) is converted on the fly."""
    import matchday
    raw = r.get(main.MD_KEY.format(d))
    if raw:
        day = json.loads(raw)
        matchday.flag_constant(day)
        return day
    legacy = r.get(f"betiq:history:{d}") if d < matchday.LEGACY_UNTIL else None
    if legacy:
        day = {}
        for p in json.loads(legacy):
            if p.get("home") and p.get("away"):
                day[matchday.key(p["home"], p["away"])] = matchday.from_legacy({**p, "date": p.get("date") or d})
        matchday.flag_constant(day)
        return day
    return {}


def _md_save(r, d: str, day: Dict[str, Dict]) -> None:
    r.set(main.MD_KEY.format(d), json.dumps(day, separators=(",", ":")), ex=main.MD_TTL)
    main._md_read_cache.pop(d, None)


def _md_many(r, dates: List[str]) -> Dict[str, Dict[str, Dict]]:
    """Several dates in one request (MGET); dates only in the old format are
    read one by one."""
    raws = r.mget([main.MD_KEY.format(d) for d in dates]) if dates else []
    out = {}
    for d, raw in zip(dates, raws):
        out[d] = json.loads(raw) if raw else (main._md_load(r, d) if d < date.today().isoformat() else {})
    return out


def _snapshot_matchdays(r) -> int:
    """Store each football prediction as its match day's pre-match entry
    (refreshed until kick-off, then locked). Returns dates written."""
    import matchday
    import price_book
    by_date: Dict[str, List[Dict]] = {}
    sb_ids: Dict[str, Dict[str, str]] = {}   # date -> match key -> SportyBet event id (its stats come by it)
    for p in main._predictions_cache:
        if p.get("sport") in (None, "football") and p.get("date"):
            ev = main._linked_event(p)
            try:
                priced = price_book.prices(p, ev)
            except Exception:
                priced = None
            by_date.setdefault(p["date"], []).append({**p, "_sb_prices": priced} if priced else p)
            if ev and ev.get("eventId") and p.get("home") and p.get("away"):
                sb_ids.setdefault(p["date"], {})[matchday.key(p["home"], p["away"])] = str(ev["eventId"])
    now = datetime.now(timezone.utc)
    written, failed = 0, []
    for d, preds in sorted(by_date.items()):
        # Each date on its own: one failed write mustn't leave the later dates unsaved
        try:
            day = main._md_load(r, d)
            changed = matchday.merge_predictions(day, preds, now)
            for k, eid in (sb_ids.get(d) or {}).items():
                if k in day and not day[k].get("sb_id"):
                    day[k]["sb_id"] = eid
                    changed = True
            if changed:
                main._md_save(r, d, day)
                written += 1
        except Exception as e:
            failed.append(f"{d}: {type(e).__name__}: {e}"[:200])
    if failed:
        print(f"[MatchDay] snapshot failed for {len(failed)} date(s): {failed[:3]}")
        main._record_job("football_snapshot", {"written": written, "failed": failed[:10]})
    return written


def _md_day_view(d: str) -> Dict[str, Dict]:
    """A date's entries for the site: stored (cached a minute), with the
    current predictions merged in for matches not yet started."""
    import matchday
    hit = main._md_read_cache.get(d)
    if hit and time.time() - hit[0] < 60:
        day = hit[1]
    else:
        r = main._get_redis()
        try:
            day = main._md_load(r, d) if r else {}
        except Exception:
            day = {}
        main._md_read_cache[d] = (time.time(), day)
    if d >= date.today().isoformat():
        day = json.loads(json.dumps(day))  # the cached copy stays as stored
        matchday.merge_predictions(day, [p for p in main._predictions_cache
                                         if p.get("date") == d and p.get("sport") in (None, "football")],
                                   datetime.now(timezone.utc))
    return day


def _record_job(name: str, report: Any) -> None:
    """A live job's last run (time, and its report or error) in Redis, for
    the probe and the admin page: these jobs otherwise fail silently."""
    r = main._get_redis()
    if not r:
        return
    try:
        r.hset(main.LIVE_JOBS_KEY, name, json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                "report": report}, default=str)[:4000])
        r.expire(main.LIVE_JOBS_KEY, 7 * 86400)
    except Exception:
        pass


async def _guarded_tick(name: str, run, limit: float) -> Dict[str, Any]:
    """A live job that can't hang: given up after `limit` seconds, so the next
    minute's run isn't skipped behind a stuck request forever (the jobs allow
    one run at a time), and its outcome recorded either way."""
    try:
        report = await asyncio.wait_for(run(), limit)
    except asyncio.TimeoutError:
        report = {"error": f"gave up after {limit:.0f}s"}
        print(f"[Live] {name}: gave up after {limit:.0f}s")
    except Exception as e:
        report = {"error": f"{type(e).__name__}: {e}"[:300]}
        print(f"[Live] {name} failed: {report['error']}")
    await asyncio.to_thread(main._record_job, name, report)
    return report


async def _sportybet_scores(stale: Dict[str, Dict], now: datetime) -> Tuple[List[Dict], Dict[str, Any]]:
    """Live scores and finals from SportyBet for the matches ESPN doesn't
    score: its live list (one request) each run, and the results of their
    days at most every SB_RESULTS_EVERY."""
    import results_feed
    out = await results_feed.fetch_sportybet_live()
    report: Dict[str, Any] = {"live_events": len(out)}
    days = sorted({e["date"] for e in stale.values() if e.get("date")})
    for d in days:
        if time.time() - main._sb_results_at.get(d, 0) < main.SB_RESULTS_EVERY:
            continue
        main._sb_results_at[d] = time.time()
        got = await results_feed.fetch_sportybet_results(d)
        report[f"results {d}"] = len(got)
        out += got
    return out, report


def _unscored(need: Dict[str, Dict[str, Dict]], espn: List[Dict], now: datetime) -> Dict[str, Dict]:
    """{entry key: entry} for matches kicked off 5+ minutes ago that ESPN
    gives no live or final score for (and that we don't have final)."""
    import matchday
    out = {}
    for entries in need.values():
        got = dict(matchday.match_results(entries, espn))
        for k, e in entries.items():
            ko = matchday.kickoff(e)
            if not ko or ko > now - timedelta(minutes=5):
                continue
            if (e.get("result") or {}).get("status") in ("finished", "postponed"):
                continue
            if (got.get(k) or {}).get("status") in ("live", "finished", "postponed"):
                continue
            out[k] = e
    return out


async def _backup_results(r, stale: Dict[str, Dict]) -> Tuple[List[Dict], Dict[str, Any]]:
    """API-Football results for the days of `stale` matches: at most every
    AF_LIVE_EVERY and AF_LIVE_DAILY_CAP requests a day."""
    import results_feed
    key = os.getenv("APIFOOTBALL_KEY", "").strip()
    if not key:
        return [], {"skipped": "set APIFOOTBALL_KEY to score matches ESPN doesn't"}
    if time.time() - main._af_live_last[0] < main.AF_LIVE_EVERY:
        return [], {"skipped": "asked recently", "unscored": len(stale)}
    count_key = f"betiq:af:live:{date.today().isoformat()}"
    try:
        used = int(r.get(count_key) or 0)
    except Exception:
        used = 0
    days = sorted({e["date"] for e in stale.values() if e.get("date")})
    if used + len(days) > main.AF_LIVE_DAILY_CAP:
        return [], {"skipped": f"used today's {main.AF_LIVE_DAILY_CAP} requests", "unscored": len(stale)}
    main._af_live_last[0] = time.time()
    out: List[Dict] = []
    report: Dict[str, Any] = {"requests": 0, "errors": [], "unscored": len(stale)}
    async with httpx.AsyncClient(timeout=20) as client:
        for d in days:
            got, err = await results_feed.fetch_api_football(client, date.fromisoformat(d), key)
            report["requests"] += 1
            if err:
                report["errors"].append(f"{d}: {err}")
            out += got
    try:
        r.incrby(count_key, report["requests"])
        r.expire(count_key, 2 * 86400)
    except Exception:
        pass
    report["results"] = len(out)
    return out, report


async def _guarded_refresh(days_back: int, trigger: str) -> None:
    """A results run that can't hang: given up after MD_RUN_LIMIT, so the
    next scheduled run isn't skipped behind it forever."""
    try:
        await asyncio.wait_for(main._refresh_matchdays(days_back, trigger), main.MD_RUN_LIMIT.get(trigger, 600))
    except asyncio.TimeoutError:
        print(f"[MatchDay] {trigger} run gave up after {main.MD_RUN_LIMIT.get(trigger)}s at: {main._md_status.get('stage')}")
        main._md_status.update(failed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          error=f"timed out at {main._md_status.get('stage')}")
    except Exception as e:
        print(f"[MatchDay] {trigger} run failed at {main._md_status.get('stage')}: {type(e).__name__}: {e}")
        main._md_status.update(failed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          error=f"{type(e).__name__} at {main._md_status.get('stage')}")


def _learn_finished_matches(days: int = 1) -> Optional[Dict[str, Any]]:
    """Feed every finished match of the last `days` days (and today) that the
    models don't hold yet into them, then redo the upcoming predictions of the
    teams that played. Returns {matches, teams, repredicted} or None when
    nothing was new."""
    import live_learning
    if main._predictor is None:
        return None
    r = main._get_redis()
    if not r:
        return None
    today = date.today()
    stored = main._md_many(r, [(today - timedelta(days=o)).isoformat() for o in range(days, -1, -1)])
    moved: set = set()
    learned = []
    for day in stored.values():
        for e in (day or {}).values():
            row = live_learning.row_from_entry(e)
            if not row:
                continue
            international = intl.is_international(row["league"])
            got = live_learning.learn(row, predictor=main._predictor, set_pieces=main._set_pieces, shots=main._shots,
                                      international=international,
                                      competition=intl.LEAGUE_CODE if international else row["league"])
            if got:
                moved |= got
                learned.append(f"{row['HomeTeam']} {row['FTHG']}-{row['FTAG']} {row['AwayTeam']}")
    if not learned:
        return None
    redone = main._repredict(moved)
    main._learn_status.update(at=datetime.now(timezone.utc).isoformat(timespec="seconds"), matches=len(learned),
                         total=main._learn_status["total"] + len(learned), last=(learned + main._learn_status["last"])[:20],
                         repredicted=redone)
    print(f"[Learn] {len(learned)} finished match(es) learned; {redone} upcoming prediction(s) redone: "
          + "; ".join(learned[:5]))
    return {"matches": len(learned), "teams": sorted(moved), "repredicted": redone}


def _repredict(teams: set) -> int:
    """Redo the not-yet-started predictions involving `teams` (model names)
    with the models as they now stand, keeping their odds; returns how many."""
    import live_learning
    if not teams or main._predictor is None or not main._predictions_cache:
        return 0
    now = datetime.now(timezone.utc)
    unknown = dict(main._unknown_clubs)           # _build_predictions starts that list afresh
    out, redone = [], 0
    try:
        for p in main._predictions_cache:
            if (p.get("sport") in (None, "football") and not live_learning.kicked_off(p, now)
                    and (main._predictor.canon(p.get("home", "")) in teams or main._predictor.canon(p.get("away", "")) in teams)):
                odds = {"1": p.get("odds_home") or 0, "X": p.get("odds_draw") or 0, "2": p.get("odds_away") or 0}
                new = main._build_predictions(main._predictor, [p], {f"{p['home']}:{p['away']}:{p.get('date', '')}": odds})
                if new:
                    out.append({**{k: v for k, v in p.items() if k != "thin_history"}, **new[0]})
                    redone += 1
                    continue
            out.append(p)
    finally:
        main._unknown_clubs.update(unknown)
    if redone:
        main._predictions_cache = out
        main._save_predictions_cache()
    return redone


async def _matchday_live() -> None:
    await main._guarded_refresh(1, "live")
    await asyncio.to_thread(main._record_job, "football_live",
                            {k: main._md_status.get(k) for k in ("at", "stage", "error", "failed_at", "report")})
    # Matches that just finished: into the models, and their teams' next predictions redone
    try:
        await asyncio.to_thread(main._learn_finished_matches, 1)
    except Exception as e:
        print(f"[Learn] failed: {e}")


async def _matchday_sweep() -> None:
    await main._guarded_refresh(main.MD_DAYS_BACK, "sweep")


def _legacy_view(e: Dict) -> Dict[str, Any]:
    """A match-day entry in the old history shape (admin stats, /api/history)."""
    import grading
    res, g = e.get("result") or {}, e.get("grades") or {}
    finished = res.get("status") == "finished" and res.get("hg") is not None and not res.get("aet")
    tip = (g.get("tip") or {}).get("verdict")
    return {**{f: e.get(f) for f in ("home", "away", "date", "time", "league", "league_name", "flag")},
            **(e.get("pred") or {}),
            "outcome": tip or ("void" if finished else "pending"),
            "actual_result": grading.result_from_score(int(res["hg"]), int(res["ag"])) if finished else None,
            "score": f"{res['hg']}-{res['ag']}" if finished else None,
            "goals_outcome": (g.get("goals") or {}).get("verdict")}


def _read_history(r, d: str) -> List[Dict]:
    return [main._legacy_view(e) for e in main._md_load(r, d).values()]


def _client_today(value: str = "") -> date:
    """The viewer's date, as their device gives it ("YYYY-MM-DD"): any
    timezone is within a day of UTC's, so anything further off (or missing)
    falls back to the server's date."""
    server = date.today()
    try:
        d = date.fromisoformat(value) if value else server
    except (TypeError, ValueError):
        return server
    return d if abs((d - server).days) <= 1 else server


def _date_param(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")


@router.get("/api/matchday")
async def get_matchday(date_: str = Query("", alias="date")):
    """One day's matches: the pre-match prediction, the score (live or
    final) and how each market's pick did. From 90 days back to the last
    predicted day."""
    import matchday
    today = date.today()
    d = main._date_param(date_ or today.isoformat())
    if not (today - timedelta(days=90) <= d <= today + timedelta(days=main.PREDICTION_DAYS + 1)):
        raise HTTPException(status_code=400, detail="date out of range")
    day = main._md_day_view(d.isoformat())
    matches = sorted((matchday.public(e) for e in day.values()),
                     key=lambda m: (m.get("league_name") or "", m.get("time") or "", m.get("home") or ""))
    await asyncio.to_thread(main._with_football_stats, d.isoformat(), day, matches)
    return {"date": d.isoformat(), "today": today.isoformat(), "matches": matches,
            "summary": matchday.day_summary(day.values()),
            "updated": main._md_status.get("at"),
            "check": {**{k: main._md_status.get(k) for k in ("started", "stage", "error", "failed_at")},
                      "backup": ((main._md_status.get("report") or {}).get("backup"))}}


@router.get("/api/matchday/strip")
async def get_matchday_strip(today_: str = Query("", alias="today", max_length=10)):
    """The date strip: 7 days back to 14 ahead of the viewer's date (their
    device's, `today`), each with its match count, how many are live /
    finished, and how our tips did."""
    import matchday
    today = main._client_today(today_)
    hit = main._strip_cache.get(today.isoformat())
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    dates = [(today + timedelta(days=o)).isoformat() for o in range(-main.MD_DAYS_BACK, main.MD_DAYS_AHEAD + 1)]
    r = main._get_redis()
    try:
        stored = await asyncio.to_thread(main._md_many, r, dates) if r else {}
    except Exception:
        stored = {}
    upcoming: Dict[str, int] = {}
    for p in main._predictions_cache:
        if p.get("sport") in (None, "football") and p.get("date"):
            upcoming[p["date"]] = upcoming.get(p["date"], 0) + 1
    days = []
    for d in dates:
        s = matchday.day_summary((stored.get(d) or {}).values())
        if d >= today.isoformat():
            s["total"] = max(s["total"], upcoming.get(d, 0))
        days.append({"date": d, **s})
    out = {"today": today.isoformat(), "days": days}
    main._strip_cache[today.isoformat()] = (time.time(), out)
    return out


@router.get("/api/accuracy")
async def get_accuracy(days: int = 30):
    """The model's track record over the last `days` days (7–90): each
    market's hit rate next to the probability we gave, calibration, the
    1X2 Brier score against the bookmaker's, by league and by day."""
    import matchday
    days = max(1, min(int(days), 90))
    hit = main._accuracy_cache.get(days)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    r = main._get_redis()
    if not r:
        return {"days": days, "matches": 0, "markets": {}, "calibration": [], "brier": {}, "leagues": [], "daily": []}
    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(days, -1, -1)]
    out = {"days": days, **matchday.accuracy(await asyncio.to_thread(main._md_many, r, dates))}
    main._accuracy_cache[days] = (time.time(), out)
    return out


@router.get("/api/accuracy/{sport}")
async def get_sport_accuracy(sport: str, request: Request, days: int = 30):
    """The track record of basketball, tennis or table tennis over the last
    `days` days, in football's shape (sport_accuracy.py): our tips, the
    total line, our best line; calibration; the winner Brier score against
    the bookmaker's; by competition and by day."""
    import basketball_matchday as bbmd
    import racket_matchday as rmd
    import sport_accuracy
    sport = "table_tennis" if sport == "table-tennis" else sport
    if sport not in ("basketball", "tennis", "table_tennis"):
        raise HTTPException(status_code=404, detail="Unknown sport")
    await main._check_sport_access(request, "basketball" if sport == "basketball" else main._rk_url(sport))
    days = max(1, min(int(days), 90))
    hit = main._sport_accuracy_cache.get((sport, days))
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    r = main._get_redis()
    empty = {"days": days, "matches": 0, "markets": {}, "calibration": [], "brier": {}, "leagues": [], "daily": []}
    if not r:
        return empty
    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(days, -1, -1)]
    keys = [bbmd.KEY.format(d) if sport == "basketball" else rmd.key(sport, d) for d in dates]

    def load() -> Dict[str, List[Dict]]:
        out: Dict[str, List[Dict]] = {}
        for d, raw in zip(dates, r.mget(keys)):
            try:
                out[d] = list(json.loads(raw).values()) if raw else []
            except Exception:
                out[d] = []
        return out
    res = {"days": days, "sport": sport, **sport_accuracy.accuracy(await asyncio.to_thread(load), sport)}
    main._sport_accuracy_cache[(sport, days)] = (time.time(), res)
    return res


@router.get("/api/history")
async def get_history(date: str):
    """A date's predictions in the old shape (outcome / actual_result / score)."""
    return [main._legacy_view(e) for e in main._md_day_view(main._date_param(date).isoformat()).values()]


@router.get("/api/admin/edge-report")
async def edge_report(days: int = 60, _admin: str = Depends(main.require_admin)):
    """Flat-stake profit at SportyBet's pre-match prices over the last `days`
    days, by market and by the edge the model claimed (price_book.py)."""
    import price_book
    days = max(1, min(int(days), 118))
    r = main._get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(1, days + 1)]
    stored = await asyncio.to_thread(main._md_many, r, dates)
    entries = [e for d in dates for e in (stored.get(d) or {}).values()]
    return {"days": days, **price_book.report(entries)}


@router.get("/api/admin/clv")
async def clv_report(days: int = 60, _admin: str = Depends(main.require_admin)):
    """Closing-line value: each pick's first price against SportyBet's last
    one before kick-off, over the last `days` days (price_book.clv_report)."""
    import price_book
    days = max(1, min(int(days), 118))
    r = main._get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(0, days + 1)]
    stored = await asyncio.to_thread(main._md_many, r, dates)
    entries = [e for d in dates for e in (stored.get(d) or {}).values()]
    return {"days": days, **price_book.clv_report(entries)}


@router.get("/api/admin/calibration")
async def calibration_report(_admin: str = Depends(main.require_admin)):
    """The proof behind each market's correction (calibration.py, nightly):
    picks before the cut fit the maps, picks after it judge them — hit rate,
    what we said raw and corrected, log loss and Brier score — and the
    review of every ticket booked on the site (ticket_review.py)."""
    import calibration
    import ticket_review
    r = main._get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")

    def load(k):
        try:
            raw = r.get(k)
            return json.loads(raw) if raw else None
        except Exception:
            return None
    rep, tickets = await asyncio.to_thread(lambda: (load(calibration.REPORT_KEY), load(ticket_review.REPORT_KEY)))
    if rep:
        rep = {**{k: v for k, v in rep.items() if k != "maps"}, "corrected_live": len(rep.get("maps") or {}),
               "live_now": len(calibration.maps())}
    if tickets:
        tickets = {k: tickets.get(k) for k in ("at", "first_ticket", "last_ticket", "tickets", "legs", "tickets_by_legs",
                                               "legs_by_sport", "legs_by_prob_given")}
    return {"calibration": rep, "tickets": tickets}


@router.get("/api/admin/market-accuracy")
async def market_accuracy_report(days: int = 7, _admin: str = Depends(main.require_admin)):
    """Every market's picks (outcomes the model rated ≥ 50%) over the last
    `days` days, settled: hit rate vs what the model said, by market and
    confidence band (market_accuracy.py)."""
    import market_accuracy
    days = max(1, min(int(days), 60))
    r = main._get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(0, days + 1)]
    stored = await asyncio.to_thread(main._md_many, r, dates)
    entries = [e for d in dates for e in (stored.get(d) or {}).values()]
    return {"days": days, **await asyncio.to_thread(market_accuracy.report, entries)}


@router.get("/api/calendar")
async def get_calendar(month: str = ""):
    """Per-date summary for a month (YYYY-MM), in the old shape."""
    import calendar as cal_lib
    import matchday
    month = month or datetime.utcnow().strftime("%Y-%m")
    try:
        year, m = map(int, month.split("-"))
        days_in_month = cal_lib.monthrange(year, m)[1]
    except (ValueError, cal_lib.IllegalMonthError):
        raise HTTPException(status_code=400, detail="month must be YYYY-MM")
    dates = [f"{year:04d}-{m:02d}-{d:02d}" for d in range(1, days_in_month + 1)]
    r = main._get_redis()
    stored = main._md_many(r, dates) if r else {}
    summary: Dict[str, Any] = {}
    for d in dates:
        entries = list((stored.get(d) or {}).values())
        if not entries:
            continue
        s = matchday.day_summary(entries)
        summary[d] = {"total": s["total"], "won": s["tip"][0], "lost": s["tip"][1],
                      "pending": s["total"] - s["finished"], "goals_won": s["goals"][0], "goals_lost": s["goals"][1]}
    return summary
