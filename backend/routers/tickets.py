"""
Tickets: booking codes per account, settled from the match days; result feedback.

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import json
import os
import pandas as pd
import re
import secrets
import time
from auth import require_user
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Any, Dict, List, Optional, Tuple

import main

router = APIRouter()


def _record_ticket(uid: str, ticket: Dict[str, Any]) -> None:
    import tickets
    r = main._get_redis()
    if not r or not ticket.get("legs"):
        return
    key = main._ukey(uid, "tickets")
    raw = r.get(key)
    items: List[Dict] = [t for t in (json.loads(raw) if raw else []) if t.get("code") != ticket["code"]]
    items.insert(0, ticket)
    cap = main.ANON_MAX_TICKETS if uid == main.ANON_UID else tickets.MAX_TICKETS
    r.set(key, json.dumps(items[:cap], separators=(",", ":")), ex=365 * 86400)
    r.sadd(main.TICKETS_OPEN_KEY, uid)
    r.hincrby(main.TICKETS_STATS_KEY, "created", 1)
    r.hincrby(main.TICKETS_STATS_KEY, f"source:{ticket.get('source') or 'other'}", 1)
    r.incr(f"betiq:tickets:day:{date.today().isoformat()}")
    r.expire(f"betiq:tickets:day:{date.today().isoformat()}", 90 * 86400)


def _md_entry_for(r, leg: Dict, days: Dict[str, Dict]) -> Optional[Dict]:
    """A ticket leg's match in the match-day store (its date, or a day either
    side for kick-offs near midnight); `days` caches the loaded days."""
    import matchday
    k = matchday.key(leg.get("home", ""), leg.get("away", ""))
    try:
        d0 = date.fromisoformat(leg.get("date") or "")
    except ValueError:
        return None
    today = date.today()
    for d in (d0, d0 - timedelta(days=1), d0 + timedelta(days=1)):
        ds = d.isoformat()
        if ds not in days:
            days[ds] = main._md_load(r, ds) if d <= today else {}
        e = days[ds].get(k)
        if e:
            return e
    return None


def _leg_results(r, days: Dict[str, Dict]):
    """result_for(leg) for grading a ticket leg or daily pick: player props
    from the box scores, basketball by its SportyBet event, football from
    the match-day store; tennis and table tennis by their SportyBet event."""
    bb_days: Dict[str, Dict] = {}
    rk_days: Dict[Tuple[str, str], Dict] = {}
    md_days: Dict[Tuple[str, str], Dict] = {}

    def result_for(leg: Dict) -> Optional[Dict]:
        m = str(leg.get("market") or "")
        if m.startswith("bb_player_") or m == "anytime_scorer":
            return main._props_result_for(leg)
        # Basketball, tennis, table tennis: SportyBet's results feed, else the
        # match-day store's final (some matches never reach the feed)
        if m.startswith("bb_"):
            if not leg.get("event_id") and leg.get("sb"):   # a daily pick: its SportyBet ids
                leg = {**leg, "event_id": leg["sb"].get("eventId")}
            return (main._bb_result_for(r, leg, bb_days) or main._other_sport_md_result(r, leg, "basketball", md_days)
                    or main._sr_result_for(r, leg, "basketball") or main._fs_result_for(r, leg, "basketball"))
        if m.startswith("rk_"):
            return (main._rk_result_for(r, leg, rk_days) or main._other_sport_md_result(r, leg, main._leg_sport(leg), md_days)
                    or main._sr_result_for(r, leg, main._leg_sport(leg)) or main._fs_result_for(r, leg, main._leg_sport(leg)))
        e = main._md_entry_for(r, leg, days)
        return e.get("result") if e else None
    return result_for


def _sr_result_for(r, leg: Dict, sport: str) -> Optional[Dict]:
    """Last resort: Sportradar's final for the leg's event (sr_results), read
    by the sweep; a leg with none yet is noted for the next sweep to read."""
    import sr_results
    eid = leg.get("event_id") or (leg.get("sb") or {}).get("eventId")
    res = sr_results.stored(r, eid)
    if res is None:
        sr_results.want(r, eid, sport)
    return res


def _fs_result_for(r, leg: Dict, sport: str) -> Optional[Dict]:
    """After Sportradar: Flashscore's final for the leg's teams and day
    (fs_results), found by the sweep; a leg with none yet is noted."""
    import fs_results
    if not leg.get("date") or not leg.get("home") or not leg.get("away"):
        return None
    res = fs_results.stored(r, fs_results.leg_key(sport, leg["date"], leg["home"], leg["away"]))
    if res is None:
        fs_results.want(r, leg, sport)
    return res


def _ticket_needs_settling(t: Dict, today: date) -> bool:
    """A ticket still being played, or a settled one (a lost leg settles it)
    whose other legs aren't graded yet, up to SETTLE_LEGS_DAYS after its last match."""
    legs = t.get("legs") or []
    last = max((l.get("date") or "" for l in legs), default="")
    recent = last >= (today - timedelta(days=main.SETTLE_LEGS_DAYS)).isoformat()
    if t.get("status") == "pending":
        return True
    if t.get("status") == "open":
        return last >= (today - timedelta(days=3)).isoformat()
    return recent and any(l.get("status") in (None, "pending") for l in legs)


def _rescan_open_tickets(r, today: date) -> int:
    """Every account with a ticket that still needs settling, back in the
    open set (accounts can drop out of it; this puts them back)."""
    added = 0
    for key in r.scan_iter(match="betiq:user:*:tickets", count=500):
        key = key.decode() if isinstance(key, bytes) else key
        try:
            items = json.loads(r.get(key) or "[]")
        except ValueError:
            continue
        if any(main._ticket_needs_settling(t, today) for t in items if isinstance(t, dict)):
            added += int(r.sadd(main.TICKETS_OPEN_KEY, key[len("betiq:user:"):-len(":tickets")]) or 0)
    return added


def _recheck_corrected_tickets(r, today: date, result_for) -> Optional[Dict[str, int]]:
    """After a score correction: every account's tickets with a leg in the
    last RECHECK_TICKET_DAYS, their football legs graded again on the
    corrected scores (and the win/loss tallies moved to match)."""
    import tickets
    corrected = r.get(main.MD_CORRECTED_KEY)
    done = r.get(main.TICKETS_RECHECKED_KEY)
    if not corrected or (done and done >= corrected):
        return None
    since = (today - timedelta(days=main.RECHECK_TICKET_DAYS)).isoformat()
    out = {"tickets": 0, "legs": 0}
    for key in r.scan_iter(match="betiq:user:*:tickets", count=500):
        try:
            items = json.loads(r.get(key) or "[]")
        except ValueError:
            continue
        changed = False
        for t in items:
            if not isinstance(t, dict) or max((l.get("date") or "" for l in t.get("legs") or []), default="") < since:
                continue
            was = t.get("status")
            before = [l.get("status") for l in t.get("legs") or []]
            if tickets.settle(t, result_for, recheck=True):
                changed = True
                out["legs"] += sum(1 for a, l in zip(before, t.get("legs") or []) if a != l.get("status"))
                if t.get("status") != was:
                    out["tickets"] += 1
                    if was in ("won", "lost", "void"):
                        r.hincrby(main.TICKETS_STATS_KEY, was, -1)
                    if t["status"] in ("won", "lost", "void"):
                        r.hincrby(main.TICKETS_STATS_KEY, t["status"], 1)
        if changed:
            r.set(key, json.dumps(items, separators=(",", ":")), ex=365 * 86400)
    r.set(main.TICKETS_RECHECKED_KEY, corrected)
    return out


def _settle_tickets(r) -> Dict[str, int]:
    """Grade the legs of every ticket still being played, and the legs left
    pending on settled ones (_ticket_needs_settling), whose matches have finished;
    after a score correction, the legs graded on the old score again."""
    import tickets
    today = date.today()
    report: Dict[str, Any] = {"settled": 0, "legs": 0}
    if time.time() - main._tickets_rescan_at[0] > main.TICKETS_RESCAN_HOURS * 3600:
        main._tickets_rescan_at[0] = time.time()
        report["rescan_added"] = main._rescan_open_tickets(r, today)
    uids = [u.decode() if isinstance(u, bytes) else u for u in (r.smembers(main.TICKETS_OPEN_KEY) or [])]
    report["accounts"] = len(uids)
    days: Dict[str, Dict] = {}
    result_for = main._leg_results(r, days)
    try:
        rechecked = main._recheck_corrected_tickets(r, today, result_for)
        if rechecked is not None:
            report["rechecked_after_correction"] = rechecked
    except Exception as e:
        report.setdefault("errors", []).append(f"recheck: {e}"[:200])

    for uid in uids:
        try:
            key = main._ukey(uid, "tickets")
            raw = r.get(key)
            items: List[Dict] = json.loads(raw) if raw else []
            changed = False
            for t in items:
                if not main._ticket_needs_settling(t, today):
                    continue
                was = t.get("status")
                before = sum(1 for l in t.get("legs") or [] if l.get("status") != "pending")
                if tickets.settle(t, result_for):
                    changed = True
                    report["legs"] += sum(1 for l in t.get("legs") or [] if l.get("status") != "pending") - before
                    if t["status"] != was and t["status"] in ("won", "lost", "void"):
                        report["settled"] += 1
                        r.hincrby(main.TICKETS_STATS_KEY, t["status"], 1)
            if changed:
                r.set(key, json.dumps(items, separators=(",", ":")), ex=365 * 86400)
            # Nothing left to settle (or only legs we can't settle, all played)
            if not any(main._ticket_needs_settling(t, today) for t in items):
                r.srem(main.TICKETS_OPEN_KEY, uid)
        except Exception as e:   # one account's tickets can't stop the others'
            report.setdefault("errors", []).append(f"{uid[:8]}…: {e}"[:200])
    return report


@router.get("/api/admin/tickets")
async def admin_tickets(_admin: str = Depends(main.require_admin)):
    """Booking codes made by signed-in accounts: totals, by source, settled
    win rate, and codes per day for the last 14 days."""
    r = main._get_redis()
    if not r:
        return {"error": "no_redis"}
    stats = {(k.decode() if isinstance(k, bytes) else k): int(v) for k, v in (r.hgetall(main.TICKETS_STATS_KEY) or {}).items()}
    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    counts = r.mget([f"betiq:tickets:day:{d}" for d in dates])
    won, lost = stats.get("won", 0), stats.get("lost", 0)
    return {"created": stats.get("created", 0), "won": won, "lost": lost, "void": stats.get("void", 0),
            "hit_rate": round(won / (won + lost), 3) if won + lost else None,
            "sources": {k.split(":", 1)[1]: v for k, v in stats.items() if k.startswith("source:")},
            "daily": [{"date": d, "codes": int(c or 0)} for d, c in zip(dates, counts)],
            "open_accounts": r.scard(main.TICKETS_OPEN_KEY)}


def _all_tickets(r) -> List[Dict[str, Any]]:
    """Every account's tickets, newest first, each with its account id
    (cached a minute: it reads every account that has made a code)."""
    if main._all_tickets_cache[1] is not None and time.time() - main._all_tickets_cache[0] < 60:
        return main._all_tickets_cache[1]
    keys = [k.decode() if isinstance(k, bytes) else k for k in r.scan_iter(match="betiq:user:*:tickets", count=500)]
    out: List[Dict[str, Any]] = []
    for i in range(0, len(keys), 100):
        chunk = keys[i:i + 100]
        for key, raw in zip(chunk, r.mget(chunk)):
            uid = key[len("betiq:user:"):-len(":tickets")]
            try:
                items = json.loads(raw) if raw else []
            except ValueError:
                continue
            out += [{**t, "uid": uid} for t in items if isinstance(t, dict)]
    out.sort(key=lambda t: t.get("created_at") or "", reverse=True)
    main._all_tickets_cache[:] = [time.time(), out]
    return out


def _watch_state(r) -> Dict[str, Any]:
    import ticket_watch
    raw = r.get(ticket_watch.KEY)
    try:
        watch = json.loads(raw) if raw else None
    except ValueError:
        watch = None
    return ticket_watch._migrate(watch)


def _save_watch(r, watch: Dict[str, Any]) -> None:
    import ticket_watch
    r.set(ticket_watch.KEY, json.dumps(watch, separators=(",", ":")), ex=120 * 86400)


def _watch_emails(r) -> Tuple[List[str], str]:
    """(the watched accounts' emails, where the list came from: "setting",
    "env", or "admins" when neither is set and the admins are watched)."""
    import ticket_watch
    try:
        saved = json.loads(r.get(ticket_watch.EMAILS_KEY) or "null")
    except ValueError:
        saved = None
    if isinstance(saved, list) and saved:
        return [str(e).strip().lower() for e in saved if str(e).strip()], "setting"
    env = [e.strip().lower() for e in os.getenv("TICKET_WATCH_EMAILS", "").split(",") if e.strip()]
    return (env, "env") if env else ([], "admins")


async def _watched_uids(r) -> Tuple[List[str], Dict[str, Optional[str]], str]:
    """(the watched account ids, {email: id or None when no account has
    it}, where the list came from). Ids found are kept in the watch."""
    import auth
    emails, source = _watch_emails(r)
    if not emails:
        return sorted(main.ADMIN_USER_IDS), {}, source
    watch = _watch_state(r)
    known = {e: u for e, u in (watch.get("uids") or {}).items() if e in emails}
    missing = [e for e in emails if e not in known]
    if missing:
        try:
            known.update(await auth.clerk_uids_by_email(missing))
        except Exception as e:
            print(f"[TicketWatch] Clerk lookup failed: {type(e).__name__}: {e}")
        watch["uids"] = known
        _save_watch(r, watch)
    return sorted(set(known.values())), {e: known.get(e) for e in emails}, source


def _check_ticket_watch(r, uids: List[str]) -> List[str]:
    """Adds the watched accounts' new tickets to the round, and returns the
    messages: tickets added, tickets finished since the last check, and the
    round's comparison once all are done. Saves the watch."""
    import ticket_watch
    from routers.optimizer import MIN_HIT_RATE
    watch = _watch_state(r)
    main._all_tickets_cache[:] = [0.0, None]   # maybe just settled: read them fresh
    items = main._all_tickets(r)
    messages = [ticket_watch.added_text(t) for t in ticket_watch.add_new(watch, items, uids)]
    by_code = {t.get("code"): t for t in reversed(items)}
    messages += ticket_watch.check(watch, by_code, date.today(), main._ranked_codes(),
                                   MIN_HIT_RATE, main._leg_sport)
    watch["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _save_watch(r, watch)
    return messages


async def _send_watch_messages(messages: List[str]) -> Dict[str, Any]:
    import telegram_poster
    sent, errors = 0, []
    for m in messages:
        res = await telegram_poster.notify_admin(m)
        if isinstance(res, dict) and res.get("ok") is False:
            errors.append(str(res.get("error"))[:120])
        else:
            sent += 1
    return {"messages": len(messages), "sent": sent, **({"errors": errors} if errors else {})}


async def _ticket_watch_tick() -> Dict[str, Any]:
    """One check of the ticket watch, its messages sent (on every deploy,
    hourly, and after the match-day job settles tickets)."""
    r = main._get_redis()
    if not r:
        return {"error": "no_redis"}
    uids, _, _ = await _watched_uids(r)
    if not uids:
        return {"error": "no watched accounts found"}
    msgs = await asyncio.to_thread(_check_ticket_watch, r, uids)
    return await _send_watch_messages(msgs) if msgs else {"messages": 0}


@router.get("/api/admin/tickets/watch")
async def get_ticket_watch(_admin: str = Depends(main.require_admin)):
    """Who is watched, the current round with how each ticket stands now,
    and the finished rounds."""
    import ticket_watch
    from routers.optimizer import MIN_HIT_RATE
    r = main._get_redis()
    if not r:
        return {"error": "no_redis"}
    uids, by_email, source = await _watched_uids(r)
    watch = _watch_state(r)
    rnd, perfs = watch["round"], []
    if rnd:
        by_code = {t.get("code"): t for t in reversed(main._all_tickets(r))}
        ranked, today = main._ranked_codes(), date.today()
        for w in rnd.get("tickets") or []:
            t = by_code.get(w["code"])
            if t is not None:
                perfs.append({**ticket_watch.performance(t, ranked, MIN_HIT_RATE, main._leg_sport),
                              "finished": ticket_watch.finished(t, today)})
    names = await main._account_names(uids) if uids else {}
    return {"source": source, "emails": by_email,
            "accounts": [{"uid": u, **names.get(u, {})} for u in uids],
            "round": rnd and {**rnd, "tickets": perfs}, "history": watch["history"],
            "checked_at": watch.get("checked_at")}


@router.put("/api/admin/tickets/watch/emails")
async def set_ticket_watch_emails(body: Dict[str, Any], admin: str = Depends(main.require_admin)):
    """The accounts to watch, by email; an empty list goes back to
    TICKET_WATCH_EMAILS (or the admins). Checks straight away."""
    import ticket_watch
    r = main._get_redis()
    if not r:
        raise HTTPException(503, "no_redis")
    emails = [str(e).strip().lower() for e in body.get("emails") or [] if str(e).strip()]
    if any(not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e) for e in emails):
        raise HTTPException(400, "Not an email address")
    if emails:
        r.set(ticket_watch.EMAILS_KEY, json.dumps(emails))
    else:
        r.delete(ticket_watch.EMAILS_KEY)
    main._audit(admin, "ticket_watch_emails", emails=emails)
    return {"ok": True, "check": await _ticket_watch_tick()}


@router.post("/api/admin/tickets/watch/check")
async def check_ticket_watch(_admin: str = Depends(main.require_admin)):
    return await _ticket_watch_tick()


async def _account_names(uids: List[str]) -> Dict[str, Dict[str, str]]:
    """Name and email per account, from Clerk (kept 10 minutes)."""
    import auth
    now = time.time()
    need = [u for u in set(uids) if u not in main._names_cache or now - main._names_cache[u][0] > 600]
    if need and auth.CLERK_SECRET_KEY:
        try:
            found = await auth.clerk_names(need)
        except Exception as e:
            print(f"[Tickets] couldn't read names from Clerk: {e}")
            found = {}
        for u in need:
            if u in found:
                main._names_cache[u] = (now, found[u])
    return {u: main._names_cache[u][1] for u in uids if u in main._names_cache}


@router.get("/api/admin/tickets/all")
async def admin_all_tickets(limit: int = 200, status: str = "", source: str = "", q: str = "",
                            _admin: str = Depends(main.require_admin)):
    """Every booking code made on the site, newest first, with the account
    that made it (name, email) and its legs. Filters: status
    (open|won|lost|void), source, q (code, name or email)."""
    r = main._get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="The database isn't connected")
    limit = max(1, min(int(limit), 1000))
    items = await asyncio.to_thread(main._all_tickets, r)
    if status:
        items = [t for t in items if t.get("status") == status]
    if source:
        items = [t for t in items if (t.get("source") or "other") == source]
    names = await main._account_names([t["uid"] for t in items if t["uid"] != main.ANON_UID])
    if q:
        ql = q.strip().lower()
        items = [t for t in items if ql in (t.get("code") or "").lower()
                 or ql in (names.get(t["uid"], {}).get("name") or "").lower()
                 or ql in (names.get(t["uid"], {}).get("email") or "").lower()]
    page = items[:limit]
    return {"total": len(items), "tickets": [
        {**{k: t.get(k) for k in ("code", "created_at", "source", "share_url", "status", "total_odds", "settled_at", "legs")},
         "uid": t["uid"], "name": "Not signed in" if t["uid"] == main.ANON_UID else names.get(t["uid"], {}).get("name") or "",
         "email": names.get(t["uid"], {}).get("email") or ""}
        for t in page]}


@router.get("/api/admin/tickets/markets")
async def admin_ticket_markets(source: str = "", part: str = "", days: int = 0,
                               _admin: str = Depends(main.require_admin)):
    """Every booked leg that has settled, by sport and market: picks, hit
    rate, what the model said and the return on the odds booked. Filters:
    source (optimizer|daily|slip|code_check), part (quarter|half|set), days."""
    import tickets
    r = main._get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="The database isn't connected")
    if part not in ("", "quarter", "half", "set"):
        raise HTTPException(status_code=400, detail="part is quarter, half or set")
    items = await asyncio.to_thread(main._all_tickets, r)
    since = (date.today() - timedelta(days=int(days))).isoformat() if days and int(days) > 0 else ""
    rows = tickets.leg_markets(items, source, part, since)
    return {"markets": rows, "legs": sum(x["n"] for x in rows), "sources": tickets.SOURCE_NAMES}


def _leg_sport(leg: Dict) -> str:
    """football, basketball or a racket sport ("tennis", "table_tennis", or
    "racket" when the leg doesn't say which)."""
    m = str(leg.get("market") or "")
    if m.startswith("bb_") and not m.startswith("bb_player_"):
        return "basketball"
    if m.startswith("rk_"):
        sp = leg.get("sport")
        return sp if sp in ("tennis", "table_tennis") else "racket"
    return "football"


def _other_sport_entry(r, leg: Dict, sport: str,
                       days: Dict[Tuple[str, str], Dict]) -> Optional[Tuple[str, str, Dict]]:
    """A basketball, tennis or table tennis leg's match-day entry, by the
    SportyBet event it was booked on: (sport, date, entry), or None."""
    eid = str(leg.get("event_id") or (leg.get("sb") or {}).get("eventId") or "")
    try:
        d0 = date.fromisoformat(leg.get("date") or "")
    except ValueError:
        return None
    if not eid:
        return None
    if sport == "basketball":
        sports = ["basketball"]
    else:   # the leg's racket sport first, then the other (a leg may not say which)
        sports = sorted(("tennis", "table_tennis"), key=lambda x: x != sport)
    for sp in sports:
        for d in (d0, d0 - timedelta(days=1), d0 + timedelta(days=1)):
            ds = d.isoformat()
            if (sp, ds) not in days:
                days[(sp, ds)] = (main._bbmd_load(r, ds) if sp == "basketball" else main._rkmd_load(r, sp, ds)) \
                    if d <= date.today() else {}
            e = days[(sp, ds)].get(eid)
            if e:
                return sp, ds, e
    return None


def _other_sport_md_result(r, leg: Dict, sport: str, days: Dict[Tuple[str, str], Dict]) -> Optional[Dict]:
    """A finished basketball/racket leg's result from its match-day entry, in
    the settle format: for matches SportyBet's results feed never had."""
    import basketball_matchday as bbmd
    import racket_matchday as rmd
    found = main._other_sport_entry(r, leg, sport, days)
    if not found:
        return None
    sp, _, e = found
    res = e.get("result") or {}
    if res.get("status") != "finished" or not res.get("score"):
        return None
    return bbmd._settle_result(res) if sp == "basketball" else rmd._settled(res)


def _other_sport_live(r, leg: Dict, sport: str, days: Dict[Tuple[str, str], Dict],
                      stats: Dict[Tuple[str, str], Dict]) -> Optional[Dict]:
    """A basketball, tennis or table tennis leg's match as it stands, by the
    SportyBet event it was booked on: score, the period/set scores and the
    live stats rows (live_stats.py)."""
    import basketball_matchday as bbmd
    import live_stats as ls
    import racket_matchday as rmd
    found = main._other_sport_entry(r, leg, sport, days)
    if not found:
        return None
    sp, ds, e = found
    eid = e.get("id") or str(leg.get("event_id") or (leg.get("sb") or {}).get("eventId") or "")
    m = (bbmd if sp == "basketball" else rmd).public(e)
    if m.get("status") not in ("live", "finished"):
        return None
    if (sp, ds) not in stats:
        stats[(sp, ds)] = ls.load(r, sp, ds)
    rows = (stats[(sp, ds)].get(eid) or {}).get("rows")
    if not rows and not ls.sr_number(eid):
        rows = ls.from_score(m.get("periods"), sp)
    return {"status": m["status"], "minute": m.get("minute"), "score": m.get("score"),
            "aet": bool(m.get("aet")), "stats": None, "events": None, "sport": sp,
            "periods": m.get("periods"), "rows": rows or None}


def _attach_live(r, items: List[Dict]) -> None:
    """Each leg's match as it stands (score, minute, live stats; the final
    score and stats once it's over) and, in play, whether the pick would win
    if it ended now. Open tickets, and settled ones for TICKET_LIVE_DAYS.
    For the response only: nothing is saved."""
    import matchday
    import tickets
    days: Dict[str, Dict] = {}
    other_days: Dict[Tuple[str, str], Dict] = {}
    other_stats: Dict[Tuple[str, str], Dict] = {}
    oldest = (date.today() - timedelta(days=main.TICKET_LIVE_DAYS)).isoformat()
    for t in items:
        open_ = t.get("status") in ("pending", "open")
        for leg in t.get("legs") or []:
            if not open_ and (leg.get("date") or "") < oldest:
                continue
            sport = main._leg_sport(leg)
            if sport != "football":
                try:
                    live = main._other_sport_live(r, leg, sport, other_days, other_stats)
                except Exception:
                    live = None
                if live:
                    leg["live"] = live
                continue
            try:
                e = main._md_entry_for(r, leg, days)
            except Exception:
                e = None
            m = matchday.public(e) if e else None
            if not m or m.get("status") not in ("live", "finished"):
                continue
            live = {k: m.get(k) for k in ("status", "minute", "score", "aet", "stats", "events")}
            res = (e or {}).get("result") or {}
            if m["status"] == "live" and leg.get("market") != "sportybet" and res.get("hg") is not None:
                now = tickets.grade_leg(leg.get("market", ""), leg.get("code", ""), {**res, "status": "finished", "aet": False})
                live["as_it_stands"] = now if now in ("won", "lost", "push", "half_won", "half_lost", "void") else None
            leg["live"] = live


@router.get("/api/user/tickets")
async def get_tickets(request: Request, uid: str = ""):
    """The account's booking codes, each leg settled from the result, plus
    codes saved before tracking (no legs to settle)."""
    import tickets
    uid = await require_user(request, uid)
    r = main._get_redis()
    if not r:
        return {"tickets": [], "summary": tickets.summary([]), "older": []}
    def build() -> Dict[str, Any]:
        raw = r.get(main._ukey(uid, "tickets"))
        items: List[Dict] = json.loads(raw) if raw else []
        main._attach_live(r, items)
        tracked = {t.get("code") for t in items}
        old_raw = r.get(main._ukey(uid, "codes"))
        older = [c for c in (json.loads(old_raw) if old_raw else []) if c.get("code") not in tracked]
        return {"tickets": items, "summary": tickets.summary(items), "older": older[:50]}
    return await asyncio.to_thread(build)


def _take_code(r, key: str, code: str) -> Optional[Tuple[int, Dict]]:
    """Remove the entry with this code from a list stored at `key`: (its
    position, the entry), or None."""
    raw = r.get(key)
    items: List[Dict] = json.loads(raw) if raw else []
    pos = next((i for i, t in enumerate(items) if t.get("code") == code), None)
    if pos is None:
        return None
    hit = items.pop(pos)
    r.set(key, json.dumps(items, separators=(",", ":")), ex=365 * 86400)
    return pos, hit


@router.delete("/api/user/tickets")
async def delete_ticket(request: Request, code: str, uid: str = ""):
    """Remove one of the account's codes (tracked ticket or older saved
    code). It goes to a trash kept TICKETS_TRASH_DAYS so Undo can put it
    back; removing a code that isn't there is a no-op."""
    uid = await require_user(request, uid)
    r = main._get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    code = code.strip()[:40]
    def run() -> Dict[str, Any]:
        removed = [(kind, item) for kind in ("tickets", "codes")
                   if (item := main._take_code(r, main._ukey(uid, kind), code)) is not None]
        if removed:
            key = main._ukey(uid, "tickets_trash")
            raw = r.get(key)
            trash: List[Dict] = [e for e in (json.loads(raw) if raw else []) if e.get("code") != code]
            trash.insert(0, {"code": code, "at": datetime.now(timezone.utc).isoformat(),
                             "items": [{"kind": k, "pos": p, "item": i} for k, (p, i) in removed]})
            r.set(key, json.dumps(trash[:main.TICKETS_TRASH_MAX], separators=(",", ":")), ex=main.TICKETS_TRASH_DAYS * 86400)
            main._all_tickets_cache[:] = [0.0, None]
        return {"deleted": bool(removed)}
    return await asyncio.to_thread(run)


@router.post("/api/user/tickets/restore")
async def restore_ticket(request: Request, body: Dict[str, Any]):
    """Body: {code}. Undo a removal: puts the code back from the trash
    (only what this account removed; nothing sent by the client is stored)."""
    uid = await require_user(request, body.get("uid", ""))
    r = main._get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    code = str(body.get("code") or "").strip()[:40]
    def run() -> Dict[str, Any]:
        taken = main._take_code(r, main._ukey(uid, "tickets_trash"), code)
        if not taken:
            return {"restored": False}
        entry = taken[1]
        for e in entry.get("items") or []:
            key = main._ukey(uid, e["kind"])
            raw = r.get(key)
            items: List[Dict] = [t for t in (json.loads(raw) if raw else []) if t.get("code") != code]
            items.insert(min(int(e.get("pos") or 0), len(items)), e["item"])   # back where it was
            r.set(key, json.dumps(items, separators=(",", ":")), ex=365 * 86400)
            if e["kind"] == "tickets":
                r.sadd(main.TICKETS_OPEN_KEY, uid)      # settling picks it up again if it's still open
        main._all_tickets_cache[:] = [0.0, None]
        return {"restored": True}
    return await asyncio.to_thread(run)


SHARE_DAYS = 180
_SHARE_LEG_FIELDS = ("home", "away", "league", "date", "time", "market", "marketName", "code", "label",
                     "odds", "status", "live")


def _share_key(sid: str) -> str:
    return f"betiq:share:{sid}"


@router.post("/api/user/tickets/share")
async def share_ticket(request: Request, body: Dict[str, Any]):
    """Body: {code}. A public link id for one of the account's tickets
    (/t/<id>); the same ticket keeps the same id."""
    uid = await require_user(request, body.get("uid", ""))
    r = main._get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    code = str(body.get("code") or "").strip()[:40]
    def run() -> Dict[str, Any]:
        key = main._ukey(uid, "tickets")
        raw = r.get(key)
        items: List[Dict] = json.loads(raw) if raw else []
        t = next((t for t in items if t.get("code") == code), None)
        if t is None:
            raise HTTPException(status_code=404, detail="No such ticket")
        sid = t.get("share_id")
        if not sid or not r.get(_share_key(sid)):
            sid = secrets.token_urlsafe(6)
            t["share_id"] = sid
            r.set(key, json.dumps(items, separators=(",", ":")), ex=365 * 86400)
        r.set(_share_key(sid), json.dumps({"uid": uid, "code": code}), ex=SHARE_DAYS * 86400)
        return {"id": sid}
    return await asyncio.to_thread(run)


@router.get("/api/share/{sid}")
async def shared_ticket(sid: str):
    """A shared ticket, for anyone with the link: its legs, odds and how they
    stand. Nothing about the account that made it."""
    r = main._get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,20}", sid):
        raise HTTPException(status_code=404, detail="Not found")
    def run() -> Dict[str, Any]:
        raw = r.get(_share_key(sid))
        ref = json.loads(raw) if raw else None
        if not ref:
            raise HTTPException(status_code=404, detail="Not found")
        items = json.loads(r.get(main._ukey(ref["uid"], "tickets")) or "[]")
        t = next((t for t in items if t.get("code") == ref["code"]), None)
        if t is None:   # removed since it was shared
            raise HTTPException(status_code=404, detail="Not found")
        main._attach_live(r, [t])
        return {"code": t.get("code"), "created_at": t.get("created_at"), "total_odds": t.get("total_odds"),
                "status": t.get("status"), "settled_at": t.get("settled_at"),
                "legs": [{k: l.get(k) for k in _SHARE_LEG_FIELDS if l.get(k) is not None} for l in t.get("legs") or []]}
    return await asyncio.to_thread(run)


@router.post("/api/feedback/result")
async def submit_match_result(body: Dict[str, Any], _admin: str = Depends(main.require_admin)):
    """
    Submit a confirmed match result to update Elo and track prediction accuracy.
    Body: {home, away, date, result: "H"|"D"|"A", home_score?, away_score?}
    """
    home   = body.get("home", "")
    away   = body.get("away", "")
    date_s = body.get("date", "")
    result = body.get("result", "")  # H / D / A
    home_s = body.get("home_score")
    away_s = body.get("away_score")

    if not all([home, away, date_s, result]):
        raise HTTPException(status_code=400, detail="home, away, date, result required")
    if result not in ("H", "D", "A") or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date_s)):
        raise HTTPException(status_code=400, detail="result must be H/D/A and date YYYY-MM-DD")
    for score in (home_s, away_s):
        if score is not None and (not str(score).isdigit() or int(score) > 30):
            raise HTTPException(status_code=400, detail="scores must be whole numbers")
    if len(str(home)) > 80 or len(str(away)) > 80:
        raise HTTPException(status_code=400, detail="team names too long")
    main._audit(_admin, "result", match=f"{home} v {away} {date_s}", result=result,
           score=f"{home_s}-{away_s}" if home_s is not None and away_s is not None else None)

    # Grade the match day's entry (needs the score: 1X2 alone can't settle goals markets)
    r = main._get_redis()
    if r and home_s is not None and away_s is not None:
        import matchday
        try:
            day = main._md_load(r, date_s)
            hit = matchday.match_results(day, [{"date": date_s, "home": home, "away": away}])
            for k, _ in hit:
                matchday.apply_result(day[k], {"status": "finished", "hg": int(home_s), "ag": int(away_s),
                                               "source": "admin"})
            if hit:
                main._md_save(r, date_s, day)
                print(f"[Feedback] Graded {home} vs {away} ({date_s})")
        except Exception as e:
            print(f"[Feedback] Match day update error: {e}")

    # Also append to results CSV so next training run picks it up
    try:
        row = {
            "Date": date_s, "HomeTeam": home, "AwayTeam": away,
            "Result": result,
            "FTHG": home_s if home_s is not None else "",
            "FTAG": away_s if away_s is not None else "",
        }
        if os.path.exists(main.RESULTS_CSV):
            existing = pd.read_csv(main.RESULTS_CSV)
            # Avoid duplicates
            mask = (existing["HomeTeam"] == home) & (existing["AwayTeam"] == away) & (existing["Date"] == date_s)
            if not mask.any():
                existing = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
                existing.to_csv(main.RESULTS_CSV, index=False)
        else:
            pd.DataFrame([row]).to_csv(main.RESULTS_CSV, index=False)
    except Exception as e:
        print(f"[Feedback] CSV update error: {e}")

    # Trigger live Elo update if predictor is ready
    if main._predictor is not None and result in ("H", "D", "A"):
        try:
            main._predictor._update(home, away, result,
                               float(home_s) if home_s else 0,
                               float(away_s) if away_s else 0)
            print(f"[Feedback] Elo updated for {home} vs {away}: {result}")
        except Exception as e:
            print(f"[Feedback] Elo update error: {e}")

    return {"ok": True, "message": f"Result recorded: {home} vs {away} = {result}"}
