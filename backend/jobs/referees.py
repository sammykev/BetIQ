"""
Referees appointed to upcoming matches, and past referees for the cards model.

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import httpx
import international_fixtures as intl
import json
import os
import pandas as pd
from data_fetcher import FootballDataClient
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import main


def _appointments() -> Dict[str, Dict]:
    """{home|away|date: {"name", "career"}}, from Redis after a restart."""
    if main._referees["at"] is None:
        import referees
        saved = referees.load(main._get_redis())
        if saved:
            main._referees.update(saved)
        else:
            main._referees["at"] = ""  # looked: nothing saved
    return main._referees.get("appointments") or {}


def _set_piece_extras(fx: Dict, predictor) -> Tuple[Optional[Dict], Optional[Dict]]:
    """(corners/bookings/shots markets, referee shown on the match) for a fixture.
    Clubs from the league match stats; internationals from the international
    model where it earned it. An appointed referee scales the bookings."""
    ref = main._appointments().get(main._sb_key(fx["home"], fx["away"], fx.get("date", ""))) or {}
    name, career = ref.get("name"), ref.get("career")
    extras, model = None, None
    if not fx.get("model_league"):
        if main._set_pieces is None and main._shots is None:
            return None, ({"name": name, **({"games": career.get("games")} if career else {})} if name else None)
        home, away = predictor.canon(fx["home"]), predictor.canon(fx["away"])
        if main._set_pieces is not None:
            model = main._set_pieces
            extras = main._set_pieces.markets(home, away, fx.get("league"), name, career)
        shot = main._shot_markets(home, away, fx.get("league"))
        if shot:
            extras = {**(extras or {}), **shot}
    else:
        model = main._intl_set_pieces
        extras = main._international_set_pieces(fx, name, career)
    if not name:
        return extras, None
    referee: Dict[str, Any] = {"name": name, **({"games": career.get("games")} if career else {})}
    if model is not None and extras and "bookings" in extras:
        # >1: more cards than these teams usually get (the model's view of them)
        referee["cards_factor"] = round(model.referee_factor(name, career), 2)
    return extras, referee


def _apply_referees() -> int:
    """Re-price the cached predictions' corners/bookings with the current
    appointments. Returns how many predictions have a referee."""
    if main._predictor is None or not main._predictions_cache:
        return 0
    out, n = [], 0
    for p in main._predictions_cache:
        if p.get("sport") not in (None, "football"):
            out.append(p)
            continue
        try:
            extras, referee = main._set_piece_extras(p, main._predictor)
        except Exception:
            out.append(p)
            continue
        q = {k: v for k, v in p.items() if k not in ("set_pieces", "referee")}
        if extras:
            q["set_pieces"] = extras
        if referee:
            q["referee"] = referee
            n += 1
        out.append(q)
    main._predictions_cache = out
    return n


def _af_referee_calls(add: int = 0) -> int:
    """API-Football requests the referee lookup made today (Redis-backed, so
    restarts count too). `add` records more."""
    day = datetime.now(timezone.utc).date().isoformat()
    r = main._get_redis()
    if r:
        try:
            key = f"betiq:referees:af_calls:{day}"
            if add:
                n = int(r.incrby(key, add))
                r.expire(key, 2 * 86400)
                return n
            return int(r.get(key) or 0)
        except Exception:
            pass
    calls = main._referees.setdefault("_af_calls", {})
    calls[day] = calls.get(day, 0) + add
    return calls[day]


async def _football_data_referees(preds: List[Dict], today: date) -> Dict[str, Any]:
    """Appointed referees from football-data.org for our predictions in the
    next few days: {"listed", "with_referee", "found", "found_map"} or why not."""
    import referee_sources
    import referees
    if not main.API_KEY:
        return {"skipped": "FOOTBALL_DATA_API_KEY not set"}
    try:
        fd = FootballDataClient(main.API_KEY)
        async with httpx.AsyncClient() as hc:
            listed = await referee_sources.upcoming(lambda url: fd._get(hc, url), today)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    events = [{"homeTeamName": m["home"], "awayTeamName": m["away"], "day": m["date"], "referee": m["referee"]}
              for m in listed if m["referee"]]
    last = (today + timedelta(days=4)).isoformat()
    soon = [p for p in preds if today.isoformat() <= (p.get("date") or "") <= last]
    found = {k: {"name": ev["referee"], "career": None, "source": "football-data"}
             for k, ev in referees.match(soon, events).items()}
    return {"listed": len(listed), "with_referee": len(events), "found": len(found), "found_map": found}


def _fd_refs_load(r) -> Dict[str, Any]:
    import gzip
    import referee_sources
    raw = r.get(referee_sources.REFS_KEY) if r else None
    return json.loads(gzip.decompress(raw)) if raw else {}


def _referee_status() -> Dict[str, Any]:
    import referees
    appointed = main._appointments()
    shown = []
    for k, v in appointed.items():
        home, away, day = (k.split("|") + ["", "", ""])[:3]
        shown.append({"match": f"{home} vs {away}", "date": day, "referee": v.get("name"),
                      "games": (v.get("career") or {}).get("games"), "source": v.get("source") or "sofascore"})
    shown.sort(key=lambda x: x["date"])
    return {"at": main._referees.get("at") or None, "checked": main._referees.get("checked"),
            "trigger": main._referees.get("trigger"), "report": main._referees.get("report"),
            "appointments": shown[:60], "count": len(appointed),
            "api_football_calls_today": main._af_referee_calls(), "api_football_cap": referees.AF_DAILY_CAP,
            "past": main._fd_refs_status,
            "on_predictions": sum(1 for p in main._predictions_cache if p.get("referee"))}


def _with_club_referees(history: pd.DataFrame) -> pd.DataFrame:
    """The club history with referees from football-data.org and the
    nightly collector (the league CSVs name them only for England).
    Unchanged on any error."""
    import international_stats
    import model_store
    refs: Dict[str, str] = {}
    try:
        refs.update(international_stats.load(model_store._client()).get("club_refs") or {})
    except Exception as e:
        print(f"[Pipeline] SofaScore club referees not loaded: {e}")
    try:
        state = main._fd_refs_load(model_store._client())
        refs.update(state.get("refs") or {})
        main._fd_refs_status.update(at=state.get("at"), report=state.get("report"), refs=len(state.get("refs") or {}),
                               seasons_done=len(state.get("done") or []))
    except Exception as e:
        print(f"[Pipeline] football-data.org referees not loaded: {e}")
    try:
        return international_stats.add_club_referees(history, refs)
    except Exception as e:
        print(f"[Pipeline] Club referees not added: {e}")
        return history


async def _refresh_referees(trigger: str = "schedule") -> Dict[str, Any]:
    """Look up the referees of the coming days' matches and re-price the
    cached predictions' bookings. SofaScore first; when it blocks this
    server, API-Football (APIFOOTBALL_KEY). The GitHub job
    (collect_referees.py) fills the same Redis key from GitHub's machines,
    so what it found is picked up here too."""
    import httpx
    import referees
    loop = asyncio.get_running_loop()
    if main._referee_lock is None or main._referee_lock[0] is not loop:
        main._referee_lock = (loop, asyncio.Lock())
    async with main._referee_lock[1]:
        preds = [p for p in main._predictions_cache if p.get("sport") in (None, "football")]
        if not preds:
            return {"skipped": "no predictions yet"}
        saved = referees.load(main._get_redis())
        if saved.get("at") and saved["at"] > (main._referees.get("at") or ""):
            main._referees.update(saved)  # newer, from the GitHub job
        known = dict(main._appointments())
        today = datetime.now(timezone.utc).date()
        from curl_cffi.requests import AsyncSession
        try:
            async with AsyncSession(impersonate=intl.IMPERSONATE, timeout=20) as client:
                found, report = await referees.fetch(client, preds, known, today)
        except Exception as e:
            found = {k: v for k, v in known.items() if k.rsplit("|", 1)[-1] >= today.isoformat()}
            report = {"errors": [f"{type(e).__name__}: {e}"]}
        report["source"] = "sofascore"

        # football-data.org: every competition it covers, in one request
        # (a SofaScore find, with the referee's career record, still wins)
        report["football_data"] = await main._football_data_referees(preds, today)
        fd = report["football_data"].pop("found_map", {})
        found = {**fd, **{k: v for k, v in found.items() if v.get("source") not in ("football-data", "api-football")}}

        # API-Football for what's still missing, when SofaScore is blocked
        still = [p for p in preds if referees.key(p["home"], p["away"], p["date"]) not in found]
        api_key = os.getenv("APIFOOTBALL_KEY", "").strip()
        if still and (referees.blocked(report) or not report.get("days")):
            if not api_key:
                report["api_football"] = {"skipped": "set APIFOOTBALL_KEY on Render to use API-Football too"}
            elif main._af_referee_calls() + referees.AF_DAYS > referees.AF_DAILY_CAP:
                report["api_football"] = {"skipped": f"used today's {referees.AF_DAILY_CAP} requests"}
            else:
                async with httpx.AsyncClient(timeout=30) as client:
                    af, af_report = await referees.fetch_api_football(client, still, today, api_key)
                main._af_referee_calls(af_report["requests"])
                report["api_football"] = af_report
                # A SofaScore find (with the referee's career record) wins
                found = {**af, **{k: v for k, v in found.items() if v.get("source") != "api-football"}}
        report["found"] = len(found)

        main._referees.update({"at": datetime.now(timezone.utc).isoformat(), "checked": None,
                          "appointments": found, "report": report, "trigger": trigger})
        try:
            referees.save(main._get_redis(), {k: main._referees[k] for k in ("at", "appointments", "report", "trigger")})
        except Exception as e:
            print(f"[Referees] Could not save: {e}")
        report["on_predictions"] = main._apply_referees()
        await asyncio.to_thread(main._save_predictions_cache)
        print(f"[Referees] {trigger}: {len(found)} referees; errors {report.get('errors') or 'none'}")
        return report


async def _collect_fd_referees(trigger: str = "schedule") -> Dict[str, Any]:
    """Referees of past matches in every football-data.org competition (the
    current season and four before), kept in Redis for _with_club_referees.
    A first run reads ~55 seasons at the free plan's pace (~7 minutes)."""
    import gzip
    import model_store
    import referee_sources
    if not main.API_KEY:
        return {"skipped": "FOOTBALL_DATA_API_KEY not set"}
    r = model_store._client()  # binary: the blob is gzip'd
    if r is None:
        return {"skipped": "no Redis"}
    state = await asyncio.to_thread(main._fd_refs_load, r)
    fd = FootballDataClient(main.API_KEY)
    def save():
        r.set(referee_sources.REFS_KEY, gzip.compress(json.dumps(state, separators=(",", ":")).encode()))
    try:
        async with httpx.AsyncClient() as hc:
            report = await referee_sources.collect_past(lambda url: fd._get(hc, url), state, date.today(), asyncio.sleep)
    except asyncio.CancelledError:                # the server is stopping (a redeploy): keep the seasons read so far
        save()
        raise
    await asyncio.to_thread(save)
    main._fd_refs_status.update(at=state.get("at"), report=report, refs=len(state.get("refs") or {}),
                           seasons_done=len(state.get("done") or []), trigger=trigger)
    print(f"[Referees] football-data.org: {report['found']} new past referees, {report['requests']} requests")
    return report
