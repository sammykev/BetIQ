"""
Tennis and table tennis: form, models, match days, live scores.

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import Any, Dict, Iterable, List, Optional

import main

router = APIRouter()


def _racket_results(r, sport: str) -> List[Dict]:
    import basketball_data as bd
    import tennis_facts as tf
    out: List[Dict] = []
    for blob in (r.hgetall(tf.SPORTS[sport]["key"]) or {}).values() if r else []:
        try:
            out += bd.decode(blob)
        except Exception:
            continue
    return out


def _tennis_results(r) -> List[Dict]:
    return main._racket_results(r, "tennis")


def _racket_index_build(sport: str) -> int:
    import tennis_facts as tf
    rows = main._tennis_results(main._get_redis()) if sport == "tennis" else main._racket_results(main._get_redis(), sport)
    idx = tf.build_index(rows)
    main._racket_players[sport].clear()
    main._racket_players[sport].update(idx)
    return len(idx)


async def _racket_collect(sport: str) -> Dict[str, Any]:
    """Yesterday's and today's results, a few older days (the backfill), and
    days past the sport's window dropped."""
    import basketball_data as bd
    import tennis_facts as tf
    r = main._get_redis()
    if not r:
        return {"error": "no database"}
    key = tf.SPORTS[sport]["key"]
    have = [k.decode() if isinstance(k, bytes) else k for k in (r.hkeys(key) or [])]
    old = tf.stale_days(have, date.today(), sport)
    if old:
        r.hdel(key, *old)
    fetched, matches, failed = 0, 0, 0
    for day in tf.days_to_collect(have, date.today(), sport):
        try:
            found = await tf.fetch_results_day(day, sport=sport)
        except Exception as e:
            failed += 1
            print(f"[{sport}] results for {day}: {e}")
            continue
        r.hset(key, day, bd.encode(found))
        fetched += 1
        matches += len(found)
        await asyncio.sleep(0.5)
    players = await asyncio.to_thread(main._racket_index_build, sport)
    main._racket_status[sport].update(at=datetime.now(timezone.utc).isoformat(timespec="seconds"), days=fetched,
                                 matches=matches, failed=failed, days_stored=r.hlen(key), players=players)
    print(f"[{sport}] results: {fetched} days, {matches} matches "
          f"({main._racket_status[sport]['days_stored']} days stored, {players} players)")
    return dict(main._racket_status[sport])


async def _live_probe() -> Dict[str, Any]:
    """What SportyBet's live listings return per sport (fields and a few
    events), and each live job's last run: for the read_probe job."""
    import basketball_data as bd
    import sportybet
    import tennis_facts as tf
    out: Dict[str, Any] = {"basketball_tick": dict(main._bbmd_status),
                           "racket_ticks": {s: dict(v) for s, v in main._rkmd_status.items()}}
    session = sportybet.shared_session()
    for name, sid in (("basketball", bd.BASKETBALL), ("tennis", tf.SPORTS["tennis"]["id"]),
                      ("table_tennis", tf.SPORTS["table_tennis"]["id"])):
        part = {}
        for path, params in bd.LIVE_LISTS:
            try:
                data = await sportybet._request(session, "GET", path,
                                                params={**params, "sportId": sid, "_t": sportybet._now_ms()})
                found: List[Dict] = []
                sportybet._collect_events(data.get("data"), found)
                part[path] = {"events": len(found), "sample": [
                    {k: e.get(k) for k in ("eventId", "homeTeamName", "matchStatus", "status", "setScore", "gameScore",
                                           "pointScore", "playedSeconds", "remainingTimeInPeriod", "period")}
                    for e in found[:3]]}
            except Exception as e:
                part[path] = {"error": str(e)[:200]}
        out[name] = part
    return out


async def _racket_index(sport: str) -> Dict[str, List]:
    if not main._racket_players[sport]:
        await asyncio.to_thread(main._racket_index_build, sport)
    return main._racket_players[sport]


def _with_racket_form(preds: List[Dict], sport: str) -> List[Dict]:
    """Each player's last 5 on the list (for the cards), where we have his results."""
    idx = main._racket_players.get(sport)
    if not idx:
        return preds
    import tennis_facts as tf
    return [{**p, "home_form": tf.form_string(idx, p.get("home") or ""),
             "away_form": tf.form_string(idx, p.get("away") or "")} for p in preds]


def _with_tennis_form(preds: List[Dict]) -> List[Dict]:
    return main._with_racket_form(preds, "tennis")


async def _racket_facts(request: Request, sport: str, home: str, away: str, date_: str, time_: str, league: str):
    import tennis_facts as tf
    await main._check_sport_access(request, "tennis" if sport == "tennis" else "table-tennis")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_) or (time_ and not re.fullmatch(r"\d{2}:\d{2}", time_)):
        raise HTTPException(status_code=400, detail="Invalid match")
    idx = await main._racket_index(sport)
    return {**tf.facts(idx, {"home": home, "away": away, "date": date_, "time": time_ or "12:00", "league": league}, sport),
            "results_days": main._racket_status[sport].get("days_stored")}


@router.get("/api/tennis/facts")
async def get_tennis_facts(request: Request, home: str = Query(..., max_length=80), away: str = Query(..., max_length=80),
                           date_: str = Query(..., alias="date"), time_: str = Query("", alias="time", max_length=5),
                           league: str = Query("", max_length=120)):
    """One tennis match's form: each player's last 5, their meetings, and
    each player's numbers over his last 20 matches (tennis_facts.py)."""
    return await main._racket_facts(request, "tennis", home, away, date_, time_, league)


@router.get("/api/table-tennis/facts")
async def get_table_tennis_facts(request: Request, home: str = Query(..., max_length=80),
                                 away: str = Query(..., max_length=80), date_: str = Query(..., alias="date"),
                                 time_: str = Query("", alias="time", max_length=5), league: str = Query("", max_length=120)):
    """The same for table tennis: games and points in place of sets and games."""
    return await main._racket_facts(request, "table_tennis", home, away, date_, time_, league)


def _rk_url(sport: str) -> str:
    """The sport as the site's URLs and access switches name it."""
    return "table-tennis" if sport == "table_tennis" else sport


def _rk_load_models_sync() -> Dict[str, int]:
    import basketball_data as bd
    import table_tennis_fit
    import tennis_fit
    r = main._get_redis()
    if not r:
        return {}
    for sport, mod in (("tennis", tennis_fit), ("table_tennis", table_tennis_fit)):
        try:
            raw = r.get(mod.MODEL_KEY)
            if raw:
                main._rk_models[sport] = mod.load_model(raw)
        except Exception as e:
            print(f"[{sport}] couldn't load the model: {e}")
        try:
            raw = r.get(main.RK_PRED_KEY.format(sport=sport))
            if raw and not main._rk_predictions[sport]:
                main._rk_predictions[sport][:] = bd.decode(raw)
        except Exception:
            pass
    return {s: len(m.get("players") or {}) for s, m in main._rk_models.items()}


async def _fb_calibration_load() -> int:
    """Football's calibration maps (fitted nightly, GitHub Actions) from Redis."""
    import football_calibration

    def load() -> int:
        r = main._get_redis()
        if r is None:
            return 0
        football_calibration.set_maps(football_calibration.load(r))
        import calibration
        calibration.set_maps(calibration.load(r))
        # and each market's blend with SportyBet's prices (check_market_blend.py)
        try:
            raw = r.get(market_blend.REPORT_KEY)
            got = (json.loads(raw) or {}).get("weights") if raw else {}
            market_blend.weights = {str(k): float(v) for k, v in (got or {}).items()
                                    if isinstance(v, (int, float)) and 0 <= v < 1}
        except Exception as e:
            print(f"[Calibration] market blend weights not loaded: {e}")
        return len(football_calibration.maps())
    import market_blend
    got = await asyncio.to_thread(load)
    import calibration
    print(f"[Calibration] corrected markets: {len(calibration.maps())} (all sports; football-only maps: {got}); "
          f"blended with SportyBet: {market_blend.weights or 'none'}")
    return got


def _rk_slim(p: Dict) -> Dict:
    """A prediction for the list: its likeliest lines at useful prices; the
    rest from /api/{sport}/match."""
    lines = p.get("rk_markets") or []
    top = sorted((x for x in lines if x["odds"] >= 1.15), key=lambda x: -x["prob"])[:main.RK_SLIM_LINES]
    return {**{k: v for k, v in p.items() if k != "rk_markets"}, "top_lines": top, "lines": len(lines)}


def _rk_upcoming(sport: str) -> List[Dict]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return [p for p in main._rk_predictions[sport] if f"{p['date']} {p['time']}" > now]


async def _rk_refresh(sport: str) -> List[Dict]:
    """Price every match SportyBet lists in the sport (every line of our markets)."""
    import basketball_data as bd
    import racket_markets as rkm
    import racket_predictions as rp
    import sportybet
    if not main._rk_models:
        await main._rk_load_models()
    try:
        events, report = await sportybet.fetch_sport_events(sport, markets=rkm.listing(sport))
    except Exception as e:
        print(f"[{sport}] listing failed: {e}")
        events, report = [], [str(e)]
    preds = await asyncio.to_thread(rp.build, events, sport, main._rk_models.get(sport))
    if not preds:
        main._rk_status[sport].update(listing={"events": len(events), "predictions": 0, "report": report})
        return main._rk_predictions[sport]
    main._rk_predictions[sport][:] = preds
    r = main._get_redis()
    if r:
        try:
            def save():
                r.set(main.RK_PRED_KEY.format(sport=sport), bd.encode(preds), ex=6 * 3600)
                r.setex(f"betiq:sports:{main._rk_url(sport)}", 3600, json.dumps([main._rk_slim(p) for p in preds]))
            await asyncio.to_thread(save)
        except Exception as e:
            print(f"[{sport}] couldn't save predictions: {e}")
    try:
        if await asyncio.to_thread(main._rkmd_merge, sport, preds):
            main._rk_strip_cache.pop(sport, None)
    except Exception as e:
        print(f"[{sport}] couldn't keep the match days: {e}")
    rated = sum(1 for p in preds if p.get("rated"))
    main._rk_status[sport].update(at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             listing={"events": len(events), "predictions": len(preds), "rated": rated,
                                      "lines": sum(len(p.get("rk_markets") or []) for p in preds), "report": report})
    print(f"[{sport}] {len(preds)} matches priced ({rated} with both players rated) · {' · '.join(report)}")
    return preds


def _rkmd_load(r, sport: str, d: str) -> Dict[str, Dict]:
    import racket_matchday as rmd
    try:
        raw = r.get(rmd.key(sport, d)) if r else None
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _rkmd_save(r, sport: str, d: str, day: Dict[str, Dict]) -> None:
    import racket_matchday as rmd
    if r:
        r.set(rmd.key(sport, d), json.dumps(day, separators=(",", ":")), ex=rmd.TTL)


def _rkmd_merge(sport: str, preds: List[Dict]) -> int:
    import racket_matchday as rmd
    r = main._get_redis()
    if not r:
        return 0
    now, changed = datetime.now(timezone.utc), 0
    for d, day_preds in rmd.by_date(preds).items():
        day = main._rkmd_load(r, sport, d)
        if rmd.merge_predictions(day, day_preds, now):
            main._rkmd_save(r, sport, d, day)
            changed += 1
    return changed


def _rk_results_days(r, sport: str, days: Iterable[str]) -> Dict[str, Dict]:
    """SportyBet's finished matches on these days (the results store), by event id."""
    import basketball_data as bd
    import tennis_facts as tf
    key = tf.SPORTS[sport]["key"]
    out: Dict[str, Dict] = {}
    days = list(days)
    for raw in (r.hmget(key, days) if r and days else []):
        if raw:
            try:
                out.update({m["id"]: m for m in bd.decode(raw)})
            except Exception:
                continue
    return out


def _apply_live_reading(e: Dict, eid: str, overdue: bool, live: Dict[str, Dict], ended: Dict[str, Dict],
                        checked: set, paged: set, now: datetime, apply_result, apply_live, stale_live) -> bool:
    """What one live read means for a started match; True if it changed.
    A final wins. A match past its live window takes no live score (its page
    can still show the last in-play one): it waits for its final. A score
    from the match's own page that hasn't moved in PAGE_FROZEN_MINUTES is
    taken as a match that has ended."""
    if eid in ended:
        return apply_result(e, ended[eid])
    res = e.get("result") or {}

    def final_soon() -> bool:
        if res.get("status") != "live":
            return False
        e["result"] = {**res, "status": "scheduled", "minute": "Final soon"}
        return True
    if overdue:
        return final_soon()
    if eid in live:
        if apply_live(e, live[eid]):
            return True
        if eid in paged and main._older_than(res.get("at"), now, main.PAGE_FROZEN_MINUTES):
            return final_soon()
        return False
    return stale_live(e, live, checked)


def _older_than(at: Optional[str], now: datetime, minutes: int) -> bool:
    try:
        return now - datetime.fromisoformat(at) > timedelta(minutes=minutes)
    except (TypeError, ValueError):
        return False


async def _rk_results_refresh(sport: str) -> Dict[str, Any]:
    """SportyBet's finals for the days with matches still open (and today),
    stored for the live ticks: a separate job, as a day is many pages."""
    import basketball_data as bd
    import racket_matchday as rmd
    import tennis_facts as tf
    r = main._get_redis()
    if not r:
        return {"skipped": "no Redis"}
    now = datetime.now(timezone.utc)
    dates = [(now.date() - timedelta(days=i)).isoformat() for i in (2, 1, 0)]
    loaded = await asyncio.to_thread(lambda: {d: main._rkmd_load(r, sport, d) for d in dates})
    open_days = {d for d in dates if any(rmd.needs_result(e, now) for e in loaded[d].values())}
    got = {}
    for d in sorted(open_days | {now.date().isoformat()}):
        try:
            found = await asyncio.wait_for(tf.fetch_results_day(d, sport=sport), 120)
            if found:
                await asyncio.to_thread(r.hset, tf.SPORTS[sport]["key"], d, bd.encode(found))
            got[d] = len(found)
        except Exception as e:
            got[d] = f"{type(e).__name__} {e}"[:120]
    return {"results": got}


async def _bb_results_refresh() -> Dict[str, Any]:
    import basketball_data as bd
    import basketball_matchday as bbmd
    r = main._get_redis()
    if not r:
        return {"skipped": "no Redis"}
    now = datetime.now(timezone.utc)
    dates = [(now.date() - timedelta(days=i)).isoformat() for i in (2, 1, 0)]
    loaded = await asyncio.to_thread(lambda: {d: main._bbmd_load(r, d) for d in dates})
    open_days = {d for d in dates if any(bbmd.needs_result(e, now) for e in loaded[d].values())}
    got = {}
    for d in sorted(open_days | {now.date().isoformat()}):
        try:
            found = await asyncio.wait_for(bd.fetch_results_day(d), 120)
            if found:
                await asyncio.to_thread(r.hset, bd.RESULTS_KEY, d, bd.encode(found))
            got[d] = len(found)
        except Exception as e:
            got[d] = f"{type(e).__name__} {e}"[:120]
    return {"results": got}


async def _tennis_results_job() -> Dict[str, Any]:
    return await main._guarded_tick("tennis_results", lambda: main._rk_results_refresh("tennis"), 400)


async def _table_tennis_results_job() -> Dict[str, Any]:
    return await main._guarded_tick("table_tennis_results", lambda: main._rk_results_refresh("table_tennis"), 400)


async def _bb_results_job() -> Dict[str, Any]:
    return await main._guarded_tick("basketball_results", main._bb_results_refresh, 400)


def _overdue(sport: str, ids: List[str]) -> List[str]:
    """Of the started matches past their live window without a final, the
    ones whose page is due a read (each at most every OVERDUE_EVERY; the
    longest-waiting first, OVERDUE_PER_TICK a run)."""
    now = time.time()
    due = sorted((i for i in ids if now - main._overdue_at.get(f"{sport}:{i}", 0) >= main.OVERDUE_EVERY),
                 key=lambda i: main._overdue_at.get(f"{sport}:{i}", 0))[:main.OVERDUE_PER_TICK]
    for i in due:
        main._overdue_at[f"{sport}:{i}"] = now
    return due


async def _rk_live_tick(sport: str) -> Dict[str, Any]:
    """Live scores for matches under way, and finals (graded) from SportyBet's results."""
    import basketball_data as bd
    import racket_matchday as rmd
    import tennis_facts as tf
    r = main._get_redis()
    if not r:
        return {"skipped": "no Redis"}
    now = datetime.now(timezone.utc)
    dates = [(now.date() - timedelta(days=i)).isoformat() for i in (2, 1, 0)]
    # Redis (a network round trip each) off the event loop: page requests don't wait on it
    days = await asyncio.to_thread(lambda: {d: main._rkmd_load(r, sport, d) for d in dates})
    open_ = {e["id"]: (d, e) for d, day in days.items() for e in day.values() if rmd.needs_result(e, now)}
    if not open_:
        main._rkmd_status[sport].update(at=now.isoformat(timespec="seconds"), open=0)
        return {"open": 0}
    changed = set()
    # Finals from the stored results (read by _rk_results_refresh every few minutes)
    finals = await asyncio.to_thread(main._rk_results_days, r, sport, dates + [(now.date() + timedelta(days=1)).isoformat()])
    for eid, (d, e) in open_.items():
        if eid in finals and rmd.apply_result(e, finals[eid]):
            changed.add(d)
    playing = [eid for eid, (d, e) in open_.items() if (e.get("result") or {}).get("status") != rmd.FINISHED
               and now - (rmd.kickoff(e) or now) < timedelta(hours=6)]
    # Past the live window with no final in the results list: their own pages,
    # now and then (a final there is taken; otherwise they stop showing "live")
    overdue = main._overdue(sport, [eid for eid, (d, e) in open_.items() if eid not in playing
                               and (e.get("result") or {}).get("status") != rmd.FINISHED])
    how = "none started"
    ended: Dict[str, Dict] = {}
    if playing or overdue:
        paged: set = set()
        try:
            live, how, checked = await bd.fetch_live(playing + overdue, sport_id=tf.SPORTS[sport]["id"],
                                                     parse=lambda ev: rmd.parse_live(ev, sport),
                                                     final=tf.parse_result, finals=ended, from_pages=paged)
        except Exception as e:
            live, how, checked = {}, f"failed: {e}", set()
        for eid in playing + overdue:
            d, e = open_[eid]
            if main._apply_live_reading(e, eid, eid in overdue, live, ended, checked, paged, now,
                                   rmd.apply_result, rmd.apply_live, rmd.stale_live):
                changed.add(d)
    if changed:
        await asyncio.to_thread(lambda: [main._rkmd_save(r, sport, d, days[d]) for d in changed])
    if changed:
        main._rk_strip_cache.pop(sport, None)
    main._rkmd_status[sport].update(at=now.isoformat(timespec="seconds"), open=len(open_), playing=len(playing),
                               overdue=len(overdue), finals_from_pages=len(ended), live_source=how)
    return dict(main._rkmd_status[sport])


async def _live_stats_run() -> Dict[str, Any]:
    """Match stats (live_stats.py) for basketball, tennis and table tennis:
    matches in play every EVERY seconds, and each finished match of today
    and yesterday once for its full-match numbers."""
    import live_stats as ls
    r = main._get_redis()
    if not r:
        return {"skipped": "no Redis"}
    now = datetime.now(timezone.utc)
    dates = [(now.date() - timedelta(days=i)).isoformat() for i in (1, 0)]
    report: Dict[str, Any] = {}
    for sport in ("basketball", "tennis", "table_tennis", "football"):
        if sport == "basketball":
            days = await asyncio.to_thread(lambda: {d: main._bbmd_load(r, d) for d in dates})
        elif sport == "football":
            # By SportyBet's event id, for matches ESPN gives no stats for
            loaded = await asyncio.to_thread(lambda: {d: main._md_load(r, d) for d in dates})
            days = {d: {e["sb_id"]: {"id": e["sb_id"], "result": e.get("result")} for e in day.values()
                        if e.get("sb_id") and not (e.get("result") or {}).get("stats")}
                    for d, day in loaded.items()}
        else:
            days = await asyncio.to_thread(lambda sp=sport: {d: main._rkmd_load(r, sp, d) for d in dates})
        stored = await asyncio.to_thread(lambda sp=sport: {d: ls.load(r, sp, d) for d in dates})
        todo = {eid: d for d in dates for eid in ls.due(days[d].values(), stored[d], now)}
        if not todo:
            report[sport] = {"due": 0}
            continue
        got = await ls.fetch(list(todo), sport)
        at = ls.now_iso()
        by_day: Dict[str, Dict[str, Dict]] = {}
        for eid, rows in got.items():
            d = todo[eid]
            have = stored[d].get(eid) or {}
            final = ((days[d].get(eid) or {}).get("result") or {}).get("status") == "finished"
            if rows is None:
                if final:  # unreadable: tried again next time, up to ls.MAX_TRIES
                    by_day.setdefault(d, {})[eid] = {**have, "tries": int(have.get("tries") or 0) + 1}
                continue
            if rows or final:
                # Nothing for a finished match: keep what was read while it was live
                by_day.setdefault(d, {})[eid] = {"at": at, "rows": rows or have.get("rows") or [], "final": final}
        await asyncio.to_thread(lambda sp=sport: [ls.save(r, sp, d, v) for d, v in by_day.items()])
        report[sport] = {"due": len(todo), "read": sum(1 for v in got.values() if v is not None),
                         "with_stats": sum(1 for v in got.values() if v), "failed": sum(1 for v in got.values() if v is None)}
    report["at"] = now.isoformat(timespec="seconds")
    return report


async def _live_stats_tick() -> Dict[str, Any]:
    return await main._guarded_tick("live_stats", main._live_stats_run, 100)


def _with_football_stats(date_: str, day: Dict[str, Dict], matches: List[Dict]) -> None:
    """Football matches ESPN gives no stats for: Sportradar's (live_stats.py), by SportyBet's event id."""
    import live_stats as ls
    import matchday
    ids = {matchday.key(e.get("home", ""), e.get("away", "")): e.get("sb_id") for e in day.values() if e.get("sb_id")}
    if not ids:
        return
    stored = ls.load(main._get_redis(), "football", date_)
    for m in matches:
        got = stored.get(ids.get(m.get("key")) or "") or {}
        if not m.get("stats") and got.get("rows"):
            m["stats"] = ls.football_stats(got["rows"])


def _with_live_stats(sport: str, date_: str, matches: List[Dict]) -> List[Dict]:
    """Each match with its latest stats (live_stats.py) as `live_stats`."""
    import live_stats as ls
    stored = ls.load(main._get_redis(), sport, date_)
    for m in matches:
        got = stored.get(m.get("id") or "")
        rows = (got or {}).get("rows")
        if not rows and not ls.sr_number(m.get("id") or "") and m.get("status") in ("live", "finished"):
            # SportyBet's own events (e.g. the Setka Cup): Sportradar has none; the score's own numbers
            rows = ls.from_score(m.get("periods"), sport)
        m["live_stats"] = rows or None
    return matches


def _rk_strip(sport: str, today_: str = "") -> Dict[str, Any]:
    import racket_matchday as rmd
    today = main._client_today(today_)
    hit = main._rk_strip_cache.get((sport, today.isoformat()))
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    dates = [(today + timedelta(days=o)).isoformat() for o in range(-main.MD_DAYS_BACK, main.MD_DAYS_AHEAD + 1)]
    r = main._get_redis()
    upcoming: Dict[str, int] = {}
    for p in main._rk_upcoming(sport):
        upcoming[p["date"]] = upcoming.get(p["date"], 0) + 1
    raws = r.mget([rmd.key(sport, d) for d in dates]) if r else [None] * len(dates)
    days = []
    for d, raw in zip(dates, raws):
        try:
            entries = json.loads(raw).values() if raw else []
        except Exception:
            entries = []
        s = rmd.day_summary(entries)
        if d >= today.isoformat():
            s["total"] = max(s["total"], upcoming.get(d, 0))
        days.append({"date": d, **s})
    out = {"today": today.isoformat(), "days": days}
    main._rk_strip_cache[(sport, today.isoformat())] = (time.time(), out)
    return out


def _rk_day(sport: str, date_: str) -> Dict[str, Any]:
    import racket_matchday as rmd
    today = date.today()
    d = main._date_param(date_ or today.isoformat())
    if not (today - timedelta(days=90) <= d <= today + timedelta(days=main.MD_DAYS_AHEAD + 1)):
        raise HTTPException(status_code=400, detail="date out of range")
    day = main._rkmd_load(main._get_redis(), sport, d.isoformat())
    idx = main._racket_players.get(sport) or {}
    matches = []
    for e in day.values():
        m = rmd.public(e)
        if idx:
            import tennis_facts as tf
            m["home_form"], m["away_form"] = tf.form_string(idx, m["home"] or ""), tf.form_string(idx, m["away"] or "")
        matches.append(m)
    matches.sort(key=lambda m: (m.get("league_name") or "", m.get("time") or "", m.get("home") or ""))
    main._with_live_stats(sport, d.isoformat(), matches)
    return {"date": d.isoformat(), "today": today.isoformat(), "matches": matches,
            "summary": rmd.day_summary(day.values()), "updated": main._rkmd_status[sport].get("at")}


def _rk_match(sport: str, event: str) -> Dict:
    p = next((x for x in main._rk_predictions[sport] if x.get("sportybet_event_id") == event), None)
    if not p:
        raise HTTPException(status_code=404, detail="Match not found")
    return p


@router.get("/api/tennis/matchday/strip")
async def get_tennis_strip(request: Request, today_: str = Query("", alias="today", max_length=10)):
    """The tennis date strip: 7 days back to 14 ahead of the viewer's date."""
    await main._check_sport_access(request, "tennis")
    return await asyncio.to_thread(main._rk_strip, "tennis", today_)


@router.get("/api/tennis/matchday")
async def get_tennis_matchday(request: Request, date_: str = Query("", alias="date")):
    """One day's tennis: each match's prediction from before it started, its
    live or final score, and how our picks did."""
    await main._check_sport_access(request, "tennis")
    return await asyncio.to_thread(main._rk_day, "tennis", date_)


@router.get("/api/tennis/match")
async def get_tennis_match(request: Request, event: str = Query(..., max_length=40)):
    """One tennis match: every line SportyBet offers on it that we priced."""
    await main._check_sport_access(request, "tennis")
    return main._rk_match("tennis", event)


@router.get("/api/table-tennis/matchday/strip")
async def get_table_tennis_strip(request: Request, today_: str = Query("", alias="today", max_length=10)):
    await main._check_sport_access(request, "table-tennis")
    return await asyncio.to_thread(main._rk_strip, "table_tennis", today_)


@router.get("/api/table-tennis/matchday")
async def get_table_tennis_matchday(request: Request, date_: str = Query("", alias="date")):
    await main._check_sport_access(request, "table-tennis")
    return await asyncio.to_thread(main._rk_day, "table_tennis", date_)


@router.get("/api/table-tennis/match")
async def get_table_tennis_match(request: Request, event: str = Query(..., max_length=40)):
    await main._check_sport_access(request, "table-tennis")
    return main._rk_match("table_tennis", event)


@router.get("/api/racket/status")
async def get_racket_status(_admin: str = Depends(main.require_admin)):
    """Admin: each racket sport's model, listing, match days and results, and the fits' checks."""
    import table_tennis_fit
    import tennis_fit
    r = main._get_redis()
    out: Dict[str, Any] = {}
    for sport, mod in (("tennis", tennis_fit), ("table_tennis", table_tennis_fit)):
        try:
            check = json.loads(r.get(mod.REPORT_KEY) or "null") if r else None
        except Exception:
            check = None
        m = main._rk_models.get(sport) or {}
        out[sport] = {"players": len(m.get("players") or {}), "as_of": m.get("as_of"),
                      "constants": {k: v for k, v in m.items() if k not in ("players", "avg")},
                      **main._rk_status[sport], "matchday": dict(main._rkmd_status[sport]), "results": dict(main._racket_status[sport]),
                      "check": {k: check.get(k) for k in ("at", "matches", "checked", "winner", "k_mult", "shrink",
                                                           "swing", "surface_weight")} if check else None}
    return out


def _rk_result_for(r, leg: Dict, cache: Dict[str, Dict]) -> Optional[Dict]:
    """A tennis or table tennis leg's result, by the SportyBet event it was booked on."""
    sport = "table_tennis" if leg.get("sport") == "table_tennis" else "tennis"
    try:
        d0 = date.fromisoformat(leg.get("date") or "")
    except ValueError:
        return None
    eid = str(leg.get("event_id") or (leg.get("sb") or {}).get("eventId") or "")
    days = [(d0 + timedelta(days=i)).isoformat() for i in (-1, 0, 1)]
    for s in (sport, "tennis" if sport == "table_tennis" else "table_tennis"):
        for d in days:
            if (s, d) not in cache:
                cache[(s, d)] = main._rk_results_days(r, s, [d])
            m = cache[(s, d)].get(eid)
            if m:
                return {"status": "finished", "sets": m["sets"], "games": m.get("games"), "ret": m.get("ret")}
    return None
