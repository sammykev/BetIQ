"""
Daily odds (daily_slips.py).

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import json
import time
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import Any, Dict, List, Optional

import main

router = APIRouter()


def _daily_load(r, day: str) -> Optional[Dict[str, Any]]:
    if r:
        try:
            raw = r.get(main.DAILY_KEY.format(day))
            if raw:
                return json.loads(raw)
        except Exception:
            pass
    return main._daily_memory.get(day)


def _daily_save(r, doc: Dict[str, Any]) -> None:
    import daily_slips
    if doc["date"] >= daily_slips.today():
        for d in [d for d in main._daily_memory if d < daily_slips.today()]:
            del main._daily_memory[d]
        main._daily_memory[doc["date"]] = doc
    if r:
        try:
            r.set(main.DAILY_KEY.format(doc["date"]), json.dumps(doc), ex=daily_slips.KEEP_DAYS * 86400)
        except Exception as e:
            print(f"[Daily] couldn't save {doc['date']}: {e}")


async def _daily_slip(target: float, day: str, exclude: Optional[List] = None) -> Dict[str, Any]:
    """One slip for the Lagos day `day`, from its matches not kicked off yet
    and not on `exclude` ([home, away, date]: the day's other slips)."""
    import daily_slips
    built: Dict[str, Any] = {}
    for attempt in daily_slips.ATTEMPTS:
        try:
            import access
            feats = main._features()
            # Racket sports not open to everyone stay out of the daily slips
            off = [rk for rk in main.RK_SPORTS if feats[access.SPORT_FEATURES[rk]]["state"] != "on"]
            res = await main._optimize_request({**daily_slips.request(target, attempt), "_rk_off": off,
                                           "exclude_matches": exclude or []},
                                          window=daily_slips.window(day))
        except HTTPException as e:
            res = {"error": str(e.detail)}
        built = daily_slips.slip(target, res, attempt)
        if built["status"] != "none":
            break
    return built


async def _book_daily(doc: Dict[str, Any]) -> bool:
    """Book each slip that has no code yet (and picks still to play) on
    SportyBet; True if any slip's booking changed."""
    import booking_slip
    import daily_slips
    import sportybet
    now = daily_slips.now_utc()
    changed = False
    for s in doc.get("slips") or []:
        if not daily_slips.needs_booking(s, now):
            continue
        tries = (s.get("booking") or {}).get("tries", 0)
        sent: List[Dict[str, Any]] = []
        try:
            sent = booking_slip.validate(daily_slips.selections(s, now))
            res = await booking_slip.to_sportybet(
                sent, sportybet.fetch_events_for_date, sportybet.find_event, sportybet.share_selections,
                linked=main._linked_event, market_map=main._sb_market_map())
        except Exception as e:
            print(f"[Daily] booking the {s.get('target')}x slip failed: {e}")
            res = {"error": "Couldn't reach SportyBet to book this slip."}
        s["booking"] = {**daily_slips.booking(res, sent, now.isoformat(timespec="seconds")), "tries": tries + 1}
        # The same slip as a football.com code (SportyBet's platform: the same picks)
        if res.get("code"):
            try:
                fc = await booking_slip.to_sportybet(
                    sent, sportybet.fetch_events_for_date, sportybet.find_event, sportybet.share_on("football_com"),
                    linked=main._linked_event, market_map=main._sb_market_map(), platform="football_com")
                if fc.get("code"):
                    s["booking"]["football_com"] = {"code": fc["code"], "share_url": fc.get("share_url"),
                                                    "total_odds": fc.get("total_odds")}
            except Exception as e:
                print(f"[Daily] football.com code for the {s.get('target')}x slip failed: {e}")
        changed = True
    return changed


def _grade_daily(r, doc: Dict[str, Any]) -> bool:
    """Settle a day's slips from the results; a slip that settles counts
    towards the record once. True if anything changed."""
    import daily_slips
    if not r:
        return False
    result_for = main._leg_results(r, {})
    changed = False
    for s in (doc.get("slips") or []) + (doc.get("cut") or []):
        before = s.get("status")
        if daily_slips.grade(s, result_for):
            changed = True
            if before == "pending" and s["status"] in ("won", "lost"):
                r.hincrby(main.DAILY_RECORD_KEY, f"{int(s['target'])}:{s['status']}", 1)
    return changed


def _sweep_daily(r) -> int:
    """Every past day's slips graded again (results and stats arrive late;
    a cut slip's other picks), hourly. Returns the days that changed."""
    import daily_slips
    if not r or time.time() - main._daily_swept[0] < main.DAILY_SWEEP_MINUTES * 60:
        return 0
    main._daily_swept[0] = time.time()
    today = date.fromisoformat(daily_slips.today())
    n = 0
    for i in range(1, main.DAILY_SWEEP_DAYS + 1):
        doc = main._daily_load(r, (today - timedelta(days=i)).isoformat())
        if doc and main._grade_daily(r, doc):
            main._daily_save(r, doc)
            n += 1
    return n


async def _remake_cut(doc: Dict[str, Any]) -> bool:
    """A new slip in place of each cut one (a pick lost), from the day's
    matches that haven't kicked off; _book_daily then books it. True if
    anything changed."""
    import daily_slips
    changed = False
    for i, s in enumerate(doc.get("slips") or []):
        if not daily_slips.needs_remake(doc, s):
            continue
        at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        new = await main._daily_slip(s["target"], doc["date"], daily_slips.match_keys(doc))
        made = daily_slips.remake(doc, i, new, at)
        changed = True
        why = f"cut by {daily_slips.cut_by(s)}" if s.get("status") == "lost" else "no slip at 12am"
        print(f"[Daily] {doc['date']} 12pm: {int(s['target'])}x {why}: "
              + ("second slip made" if made else f"no second slip ({new.get('error') or 'no matches left'})"))
    # The rest (still running, or won) get no second slip today
    return daily_slips.second_decided(doc) or changed


def _daily_public(s: Dict[str, Any]) -> Dict[str, Any]:
    """A slip as the page gets it: the booking without its bookkeeping."""
    b = s.get("booking")
    if not b:
        return s
    return {**s, "booking": {k: b.get(k) for k in ("code", "share_url", "total_odds", "booked", "of", "error", "at", "football_com")}}


def slip_reliability(rep: Optional[Dict]) -> List[Dict[str, Any]]:
    """[{lo, hi, said, won, settled}] — for each band of the chance we gave a
    leg, how often legs in it really won (ticket_review.py's report), bands
    with RELIABILITY_MIN_SETTLED settled legs only."""
    import ticket_review
    bands = (rep or {}).get("legs_by_prob_given") or {}
    out = []
    for lo, hi, label in ticket_review.BANDS:
        b = bands.get(label) or {}
        if (b.get("settled") or 0) >= main.RELIABILITY_MIN_SETTLED and b.get("win_rate") is not None and b.get("avg_prob_given"):
            out.append({"lo": lo, "hi": min(hi, 1.0), "said": b["avg_prob_given"], "won": b["win_rate"],
                        "settled": b["settled"]})
    return out


@router.get("/api/slip-reliability")
async def get_slip_reliability():
    """How our legs really did by the chance we gave them, so the bet slip
    can show an honest chance for the whole slip. Refreshed nightly."""
    import ticket_review
    hit = main._reliability_cache.get("v")
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    r = main._get_redis()
    try:
        rep = json.loads(r.get(ticket_review.REPORT_KEY) or "null") if r else None
    except Exception:
        rep = None
    out = {"bands": main.slip_reliability(rep), "at": (rep or {}).get("at")}
    main._reliability_cache["v"] = (time.time(), out)
    return out


@router.get("/api/daily-slips")
async def get_daily_slips(day: str = Query("", alias="date"), _access=Depends(main.require_feature("daily_slips"))):
    """A day's 5x / 10x / 20x slips with their SportyBet booking codes, each
    pick graded and, while it's being played, its live score; and the record
    of every slip so far, and the day's cut slips (each replaced by a new
    one). Read-only: the slips and codes are made by the daily tick
    (_daily_tick) just before midnight in Lagos, not by visitors."""
    import daily_slips
    today = daily_slips.today()
    d = main._date_param(day or today).isoformat()
    r = main._get_redis()
    doc = await asyncio.to_thread(main._daily_load, r, d)
    if doc and r:
        async with main._daily_lock:
            if await asyncio.to_thread(main._grade_daily, r, doc):
                await asyncio.to_thread(main._daily_save, r, doc)
        await asyncio.to_thread(lambda: [main._attach_live(r, [{"status": "pending", "legs": s.get("picks") or []}])
                                         for s in doc.get("slips") or []])
    record: Dict[str, Dict[str, int]] = {}
    for k, v in ((r.hgetall(main.DAILY_RECORD_KEY) if r else None) or {}).items():
        k = k.decode() if isinstance(k, bytes) else str(k)
        target, _, outcome = k.partition(":")
        record.setdefault(target, {"won": 0, "lost": 0})[outcome] = int(v)
    return {"date": d, "today": today, "built_at": (doc or {}).get("built_at"),
            "slips": [main._daily_public(s) for s in (doc or {}).get("slips") or []],
            "cut": [main._daily_public(s) for s in (doc or {}).get("cut") or []], "record": record,
            "min_prob": daily_slips.MIN_PROB, "targets": list(daily_slips.TARGETS),
            # Midnight in Lagos, in UTC: when a day's slips are out
            "publish_at_utc": daily_slips.window(today)[0].strftime("%H:%M"),
            "retry_minutes": daily_slips.RETRY_MINUTES}


@router.get("/api/admin/daily-slips")
async def admin_daily_slips(days: int = 30, _admin: str = Depends(main.require_admin)):
    """Admin: every daily odds ticket of the last `days` days — the 12am
    slips, the 12pm second ones and the cut ones they replaced — with their
    booking codes, picks and how each settled; and the record by target."""
    import daily_slips
    r = main._get_redis()
    today = date.fromisoformat(daily_slips.today())
    out = []
    for i in range(-1, max(1, min(int(days), daily_slips.KEEP_DAYS))):
        d = (today - timedelta(days=i)).isoformat()
        doc = await asyncio.to_thread(main._daily_load, r, d)
        if not doc:
            continue
        rows = []
        for s in doc.get("slips") or []:
            rows.append({**s, "slot": "12pm" if s.get("replaces") or s.get("second") == "first try at 12pm" else "12am"})
        for s in doc.get("cut") or []:
            rows.append({**s, "slot": "12am", "was_cut": True})
        rows.sort(key=lambda s: (float(s.get("target") or 0), s["slot"] != "12am"))
        out.append({"date": d, "built_at": doc.get("built_at"), "slips": rows})
    record: Dict[str, Dict[str, int]] = {}
    for k, v in ((r.hgetall(main.DAILY_RECORD_KEY) if r else None) or {}).items():
        k = k.decode() if isinstance(k, bytes) else str(k)
        target, _, outcome = k.partition(":")
        record.setdefault(target, {"won": 0, "lost": 0})[outcome] = int(v)
    return {"days": out, "record": record, "targets": list(daily_slips.TARGETS),
            "second_at": f"{daily_slips.SECOND_AT_HOUR}:00 Lagos"}


@router.post("/api/daily-slips/track")
async def track_daily_slip(request: Request, body: Dict[str, Any], _access=Depends(main.require_feature("daily_slips"))):
    """Add a daily slip's booking code to the account's tickets (Dashboard →
    Tickets), graded leg by leg like any code it books. Body: {date, target}."""
    import auth
    import daily_slips
    import tickets
    uid = await auth.require_user(request, str(body.get("uid") or ""))
    d = main._date_param(str(body.get("date") or daily_slips.today())).isoformat()
    try:
        target = float(body.get("target"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="target must be a slip's odds, e.g. 5, 10 or 20")
    doc = main._daily_load(main._get_redis(), d)
    s = next((x for x in (doc or {}).get("slips") or [] if float(x.get("target", 0)) == target), None)
    code = ((s or {}).get("booking") or {}).get("code")
    if not code:
        raise HTTPException(status_code=404, detail="That slip has no booking code yet.")
    sels, booked = daily_slips.ticket_legs(s)
    b = s["booking"]
    main._record_ticket(uid, tickets.new_ticket(code, sels, booked, "daily", b.get("share_url"), b.get("total_odds"),
                                           datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return {"tracked": True, "code": code}


def _recheck_daily_after_correction(r) -> Optional[Dict[str, Any]]:
    """After a score correction (MD_CORRECTED_KEY newer than the last
    recheck): the last RECHECK_TICKET_DAYS days of slips graded again on the
    corrected scores, the record moved to match. Under _daily_lock."""
    import daily_slips
    corrected = r.get(main.MD_CORRECTED_KEY)
    done = r.get(main.DAILY_RECHECKED_KEY)
    if not corrected or (done and done >= corrected):
        return None
    result_for = main._leg_results(r, {})
    out: Dict[str, Any] = {"slips": 0}
    today = date.fromisoformat(daily_slips.today())
    for i in range(main.RECHECK_TICKET_DAYS + 1):
        doc = main._daily_load(r, (today - timedelta(days=i)).isoformat())
        if not doc:
            continue
        before = json.dumps(doc, sort_keys=True)
        for target, was, now in daily_slips.recheck_day(doc, result_for):
            out["slips"] += 1
            if was in ("won", "lost"):
                r.hincrby(main.DAILY_RECORD_KEY, f"{target}:{was}", -1)
            if now in ("won", "lost"):
                r.hincrby(main.DAILY_RECORD_KEY, f"{target}:{now}", 1)
        if json.dumps(doc, sort_keys=True) != before:
            main._daily_save(r, doc)
    r.set(main.DAILY_RECHECKED_KEY, corrected)
    return out


async def _daily_tick() -> None:
    """Every few minutes, for today and (from just before midnight in Lagos)
    tomorrow: make the day's slips if they aren't made yet, settle them, make
    a new slip in place of any that was cut, book any slip without a code,
    and post today's; after a score correction, past slips graded again."""
    import daily_slips
    r = main._get_redis()
    if r:
        try:
            async with main._daily_lock:
                swept = await asyncio.to_thread(main._sweep_daily, r)
            if swept:
                print(f"[Daily] graded again: {swept} past day(s) changed")
        except Exception as e:
            print(f"[Daily] sweep failed: {e}")
        try:
            async with main._daily_lock:
                got = await asyncio.to_thread(main._recheck_daily_after_correction, r)
            if got:
                print(f"[Daily] graded again after a score correction: {got}")
        except Exception as e:
            print(f"[Daily] recheck after correction failed: {e}")
    for day in daily_slips.days_to_run(daily_slips.now_utc()):
        try:
            r = main._get_redis()
            doc = main._daily_load(r, day)
            if doc is None:
                await main._build_daily(day)
                continue
            async with main._daily_lock:
                changed = main._grade_daily(r, doc)
                changed = await main._remake_cut(doc) or changed
                changed = await main._book_daily(doc) or changed
                if changed:
                    main._daily_save(r, doc)
            await main._maybe_post_daily(doc)
        except Exception as e:
            print(f"[Daily] tick for {day} failed: {e}")


async def _build_daily(day: str, force: bool = False) -> Optional[Dict[str, Any]]:
    """The day's slips, made once (just before its midnight, by the daily
    tick) and booked on SportyBet straight away, so everyone gets the same
    slips and codes all day. `force` (admin) makes them again."""
    import daily_slips
    r = main._get_redis()
    async with main._daily_lock:
        if not force:
            doc = main._daily_load(r, day)
            if doc:
                return doc
        if not main._predictions_cache:
            return None
        # In turn, each leaving out the matches the ones before took: no match
        # on two targets' slips, so one result can't cut them all
        slips: List[Dict[str, Any]] = []
        for target in daily_slips.TARGETS:
            slips.append(await main._daily_slip(target, day, daily_slips.match_keys({"slips": slips})))
        doc = {"date": day, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "slips": slips}
        await main._book_daily(doc)
        main._daily_save(r, doc)
        print(f"[Daily] {day}: " + ", ".join(
            f"{int(s['target'])}x " + ((s.get("booking") or {}).get("code") or ("no slip" if s["status"] == "none" else "no code yet"))
            for s in slips))
        return doc
