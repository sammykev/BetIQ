"""
Basketball, tennis, table tennis and player props: collect, fit, price and live scores.

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import main


async def _bb_collect() -> Dict[str, Any]:
    """Yesterday's and today's results, and a few older days (the backfill)."""
    import basketball_data as bd
    r = main._get_redis()
    if not r:
        return {"error": "no database"}
    have = [k.decode() if isinstance(k, bytes) else k for k in (r.hkeys(bd.RESULTS_KEY) or [])]
    today = date.today()
    fetched, games, failed = 0, 0, 0
    for day in bd.days_to_collect(have, today):
        try:
            found = await bd.fetch_results_day(day)
        except Exception as e:
            failed += 1
            print(f"[Basketball] results for {day}: {e}")
            continue
        # A past day with nothing (older than SportyBet keeps) is stored too, so it isn't asked again
        r.hset(bd.RESULTS_KEY, day, bd.encode(found))
        fetched += 1
        games += len(found)
        await asyncio.sleep(0.5)
    total_days = r.hlen(bd.RESULTS_KEY)
    main._bb_save_status(collected={"days": fetched, "games": games, "failed": failed, "days_stored": total_days})
    print(f"[Basketball] results: {fetched} days, {games} games ({total_days} days stored)")
    if games:
        await main._bb_fit()
    return {"days": fetched, "games": games, "failed": failed}


async def _bb_fit() -> Dict[str, Any]:
    """Rate every league from the stored results."""
    try:
        got = await asyncio.to_thread(main._bb_fit_sync)
    except Exception as e:
        print(f"[Basketball] rating failed: {e}")
        return {"error": str(e)}
    main._bb_save_status(model=got)
    print(f"[Basketball] rated {got['leagues']} leagues ({got['teams']} teams) from {got['games']} games")
    return got


async def _bb_refresh() -> List[Dict]:
    """Price every basketball match SportyBet lists."""
    import basketball_data as bd
    import basketball_predictions as bp
    try:
        events, report = await bd.fetch_upcoming()
    except Exception as e:
        print(f"[Basketball] listing failed: {e}")
        events, report = [], [str(e)]
    preds = await asyncio.to_thread(bp.build, events, dict(main._bb_leagues))
    try:
        await main._bb_index()          # each team's recent games, for the form on the list
    except Exception as e:
        print(f"[Basketball] couldn't index teams: {e}")
    try:
        props = await main._bb_add_props(preds)
    except Exception as e:
        props = 0
        print(f"[Props] basketball player lines failed: {e}")
    if not preds and not events:
        main._bb_save_status(listing={"events": 0, "report": report})
        return main._bb_predictions
    main._bb_predictions[:] = preds
    r = main._get_redis()
    if r:
        try:
            r.set(main.BB_PREDICTIONS_KEY, bd.encode(preds), ex=6 * 3600)
            r.setex("betiq:sports:basketball", 3600, json.dumps([main._bb_slim(p) for p in preds]))
        except Exception as e:
            print(f"[Basketball] couldn't save predictions: {e}")
    try:
        if main._bbmd_merge(preds):
            main._bb_strip_cache.clear()
    except Exception as e:
        print(f"[Basketball] couldn't keep the match days: {e}")
    rated = sum(1 for p in preds if p.get("rated"))
    main._bb_save_status(listing={"events": len(events), "predictions": len(preds), "rated": rated, "report": report,
                             "player_lines": props})
    print(f"[Basketball] {len(preds)} matches priced ({rated} with both teams rated) · {' · '.join(report)}")
    return preds


async def _web_probe() -> Dict[str, Any]:
    """What this server can reach (web_probe.py), saved for the read_probe workflow job."""
    import web_probe
    report = await web_probe.run()
    try:
        report["live"] = await main._live_probe()
    except Exception as e:
        report["live"] = {"error": str(e)[:300]}
    r = main._get_redis()
    if r:
        r.set(web_probe.PROBE_KEY, json.dumps(report, default=str)[:900_000], ex=7 * 86400)
    print(f"[Probe] done: {list(report)}")
    return report


async def _tennis_collect() -> Dict[str, Any]:
    return await main._racket_collect("tennis")


async def _table_tennis_collect() -> Dict[str, Any]:
    return await main._racket_collect("table_tennis")


async def _rk_load_models() -> Dict[str, int]:
    """The fitted ratings (nightly, GitHub Actions) from Redis."""
    got = await asyncio.to_thread(main._rk_load_models_sync)
    print(f"[Racket] models loaded: {got} players")
    return got


async def _tennis_refresh() -> List[Dict]:
    return await main._rk_refresh("tennis")


async def _table_tennis_refresh() -> List[Dict]:
    return await main._rk_refresh("table_tennis")


async def _tennis_live_tick() -> Dict[str, Any]:
    return await main._guarded_tick("tennis_live", lambda: main._rk_live_tick("tennis"), 110)


async def _table_tennis_live_tick() -> Dict[str, Any]:
    return await main._guarded_tick("table_tennis_live", lambda: main._rk_live_tick("table_tennis"), 110)


async def _bb_live_tick() -> Dict[str, Any]:
    """Live scores for games under way, and finals (graded) from SportyBet's results."""
    import basketball_data as bd
    import basketball_matchday as bbmd
    r = main._get_redis()
    if not r:
        return {"skipped": "no Redis"}
    now = datetime.now(timezone.utc)
    dates = [(now.date() - timedelta(days=i)).isoformat() for i in (2, 1, 0)]
    days = await asyncio.to_thread(lambda: {d: main._bbmd_load(r, d) for d in dates})
    open_ = {e["id"]: (d, e) for d, day in days.items() for e in day.values() if bbmd.needs_result(e, now)}
    if not open_:
        return {"open": 0}
    changed = set()
    # Finals from the stored results (read by _bb_results_refresh every few minutes)
    finals = {g["id"]: g for g in await asyncio.to_thread(main._bb_results, r, dates + [(now.date() + timedelta(days=1)).isoformat()])}
    for eid, (d, e) in open_.items():
        if eid in finals and bbmd.apply_result(e, finals[eid]):
            changed.add(d)
    # In play: the rest that have tipped off
    playing = [eid for eid, (d, e) in open_.items() if (e.get("result") or {}).get("status") != bbmd.FINISHED
               and now - (bbmd.kickoff(e) or now) < timedelta(hours=4)]
    overdue = main._overdue("basketball", [eid for eid, (d, e) in open_.items() if eid not in playing
                                      and (e.get("result") or {}).get("status") != bbmd.FINISHED])
    how = "none started"
    ended: Dict[str, Dict] = {}
    if playing or overdue:
        paged: set = set()
        try:
            live, how, checked = await bd.fetch_live(playing + overdue, final=bd.parse_result, finals=ended,
                                                     from_pages=paged)
        except Exception as e:
            live, how, checked = {}, f"failed: {e}", set()
        for eid in playing + overdue:
            d, e = open_[eid]
            if main._apply_live_reading(e, eid, eid in overdue, live, ended, checked, paged, now,
                                   bbmd.apply_result, bbmd.apply_live, bbmd.stale_live):
                changed.add(d)
    if changed:
        await asyncio.to_thread(lambda: [main._bbmd_save(r, d, days[d]) for d in changed])
    if changed:
        main._bb_strip_cache.clear()
    main._bbmd_status.update(at=now.isoformat(timespec="seconds"), open=len(open_), playing=len(playing),
                        overdue=len(overdue), finals_from_pages=len(ended), live_source=how)
    return dict(main._bbmd_status)


async def _props_load() -> Dict[str, int]:
    """The players' box scores and the walk-forward check's numbers, from Redis."""
    try:
        got = await asyncio.to_thread(main._props_load_sync)
    except Exception as e:
        print(f"[Props] couldn't load: {e}")
        return {}
    print(f"[Props] players loaded: {got}")
    return got


async def _fb_props_refresh() -> Dict[str, int]:
    """Anytime goalscorers on our football matches in the next three days (top five leagues)."""
    import props_pricing as pr
    import sportybet
    now = datetime.now(timezone.utc)
    last = (now + timedelta(days=3)).strftime("%Y-%m-%d")
    scale_by = {lg: (v or {}).get("scale") or 1.0 for lg, v in (main._props_calib.get("fb") or {}).items()}
    priced: Dict[str, List[Dict]] = {}
    for p in main._predictions_cache:
        league = main.FB_PROP_LEAGUES.get(p.get("league") or "")
        players = main._props_players.get(("fb", league)) if league else None
        if not players or not (now.strftime("%Y-%m-%d") <= p.get("date", "") <= last):
            continue
        ev = main._linked_event(p)
        if not ev or not ev.get("eventId"):
            continue
        try:
            page = await sportybet.event_page(str(ev["eventId"]))
        except Exception as e:
            print(f"[Props] scorer page {ev.get('eventId')}: {e}")
            continue
        tt = (p.get("goal_markets") or {}).get("team_totals") or {}
        lam = {"home": pr.team_goals((tt.get("home") or {}).get("0.5")),
               "away": pr.team_goals((tt.get("away") or {}).get("0.5"))}
        team_exp: Dict[str, Optional[float]] = {}
        for o in pr.scorer_offers(page or {}):
            if o["team"] not in team_exp:
                side = "home" if sportybet.team_similarity(o["team"], p["home"]) >= sportybet.team_similarity(o["team"], p["away"]) else "away"
                team_exp[o["team"]] = lam[side]
        lines = pr.scorer_price(page or {}, players, team_exp, scale_by.get(league, 1.0))
        if lines:
            priced[str(ev["eventId"])] = [{**x, "home": p["home"], "away": p["away"], "date": p["date"],
                                           "time": p.get("time") or "", "league": p.get("league_name") or league}
                                          for x in lines]
        await asyncio.sleep(0.3)
    main._props_fb.clear()
    main._props_fb.update(priced)
    r = main._get_redis()
    if r:
        try:
            r.set(main.PROPS_FB_KEY, json.dumps(priced), ex=6 * 3600)
        except Exception:
            pass
    print(f"[Props] goalscorers priced on {len(priced)} matches")
    return {"matches": len(priced), "players": sum(len(v) for v in priced.values())}
