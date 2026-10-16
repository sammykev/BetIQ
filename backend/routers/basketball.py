"""
Basketball: history, model, match days, live scores and facts.

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import json
import time
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import Any, Dict, Iterable, List, Optional

import main

router = APIRouter()


def _bb_save_status(**kw) -> None:
    main._bb_status.update(kw, at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    r = main._get_redis()
    if r:
        try:
            r.set(main.BB_STATUS_KEY, json.dumps(main._bb_status, default=str))
        except Exception:
            pass


def _bb_results(r, days: Optional[Iterable[str]] = None) -> List[Dict]:
    """Stored results: every day's, or the given days'."""
    import basketball_data as bd
    if not r:
        return []
    try:
        if days is None:
            raw = r.hgetall(bd.RESULTS_KEY) or {}
            blobs = list(raw.values())
        else:
            blobs = r.hmget(bd.RESULTS_KEY, list(days)) or []
    except Exception as e:
        print(f"[Basketball] couldn't read results: {e}")
        return []
    out: List[Dict] = []
    for b in blobs:
        try:
            out += bd.decode(b)
        except Exception:
            continue
    return out


def _bb_index_set(results: List[Dict]) -> None:
    import basketball_facts as bf
    idx = bf.build_index(results)
    main._bb_team_games.clear()
    main._bb_team_games.update(idx)


async def _bb_index() -> Dict[str, List]:
    """The team index; built from the stored results the first time it's asked for."""
    if not main._bb_team_games:
        r = main._get_redis()
        if r:
            await asyncio.to_thread(lambda: main._bb_index_set(main._bb_results(r)))
    return main._bb_team_games


def _bb_fit_sync() -> Dict[str, Any]:
    import basketball_data as bd
    import basketball_model as bm
    r = main._get_redis()
    results = main._bb_results(r)
    try:
        backtest = json.loads(r.get("betiq:bb:backtest") or "null") if r else None   # backtest_basketball.py
    except Exception:
        backtest = None
    # Elo (basketball_elo.py) over the long history (bb_history.py), weighted
    # in each league as the Elo check found best (elo_check_basketball.py)
    elo_report, history = None, None
    try:
        import bb_history
        import elo_check_basketball as ec
        elo_report = json.loads(r.get(ec.REPORT_KEY) or "null") if r else None
        history = bb_history.combine(results, bb_history.load(r)) if r and elo_report else None
    except Exception as e:
        print(f"[Basketball] Elo not used: {e}")
    # Each league's half-life and ridge where the tuning beat the defaults (tune_basketball.py)
    try:
        import tune_basketball as tb
        bm.TUNED.clear()
        bm.TUNED.update(tb.load(r))
    except Exception as e:
        print(f"[Basketball] tuning not used: {e}")
    leagues = bd.fit_all(results, backtest=backtest, elo_report=elo_report, history=history)
    main._bb_leagues.clear()
    main._bb_leagues.update(leagues)
    # Quarter trends (highest / lowest scoring quarter): every quarter-scored
    # game of each league, the long history included, and its teams' meetings
    quarters = 0
    try:
        import basketball_quarters as bq
        if r:
            bq.CONFIG.update(bq.load_config(r))
        long_games = bd.games_by_league(history if history is not None else results)
        models = {}
        for name in leagues:
            m = bq.fit(name, long_games.get(name) or [])
            if m:
                models[name] = m
        bq.MODELS.clear()
        bq.MODELS.update(models)
        quarters = len(models)
        if r and models:
            bq.save(r, models)
    except Exception as e:
        print(f"[Basketball] quarter models not fitted: {e}")
    main._bb_index_set(results)
    if r and leagues:
        r.set(main.BB_MODEL_KEY, bd.encode([lg.to_json() for lg in leagues.values()]))
    return {"games": len(results), "leagues": len(leagues), "quarter_models": quarters,
            "teams": sum(len(lg.attack) for lg in leagues.values())}


def _bb_load() -> None:
    """At startup: the rated leagues and the last predictions, from Redis."""
    import basketball_data as bd
    import basketball_model as bm
    r = main._get_redis()
    if not r:
        return
    try:
        for d in bd.decode(r.get(main.BB_MODEL_KEY)):
            lg = bm.League.from_json(d)
            main._bb_leagues[lg.name] = lg
        preds = bd.decode(r.get(main.BB_PREDICTIONS_KEY))
        import basketball_predictions as bp
        for p in preds:                 # saved before a family split: group each line afresh
            for x in p.get("bb_markets") or []:
                x["family"] = bp.family(x.get("market") or "")
        main._bb_predictions[:] = preds
        main._bb_status.update(json.loads(r.get(main.BB_STATUS_KEY) or "{}"))
    except Exception as e:
        print(f"[Basketball] couldn't load: {e}")
    print(f"[Basketball] loaded {len(main._bb_leagues)} rated leagues, {len(main._bb_predictions)} predictions")


def _bb_slim(p: Dict) -> Dict:
    """A prediction for the list: its likeliest lines (at useful prices) and
    the middle handicap and total; the rest from /api/basketball/match."""
    lines = p.get("bb_markets") or []
    useful = [x for x in lines if x["odds"] >= 1.15 and x["market"] != "bb_overtime"]
    top = sorted(useful, key=lambda x: -x["prob"])[:main.BB_SLIM_LINES]
    out = {**{k: v for k, v in p.items() if k != "bb_markets"}, "top_lines": top, "lines": len(lines)}
    if main._bb_team_games:
        import basketball_facts as bf
        out["home_form"] = bf.form_string(main._bb_team_games, p["home"])
        out["away_form"] = bf.form_string(main._bb_team_games, p["away"])
    return out


def _bb_upcoming() -> List[Dict]:
    """The priced matches not started yet (full lines)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return [p for p in main._bb_predictions if f"{p['date']} {p['time']}" > now]


@router.get("/api/basketball/match")
async def get_basketball_match(request: Request, event: str = Query(..., max_length=40)):
    """One basketball match: every line SportyBet offers on it that we priced."""
    await main._check_sport_access(request, "basketball")
    p = next((x for x in main._bb_predictions if x.get("sportybet_event_id") == event), None)
    if not p:
        raise HTTPException(status_code=404, detail="Match not found")
    return p


@router.get("/api/basketball/facts")
async def get_basketball_facts(request: Request, event: str = Query(..., max_length=40)):
    """One match's form: each team's last 5, their meetings, and each team's
    averages over its last 10 (basketball_facts.py), like football's match page."""
    import basketball_facts as bf
    await main._check_sport_access(request, "basketball")
    p = next((x for x in main._bb_predictions if x.get("sportybet_event_id") == event), None)
    if not p:
        # Started or finished: the match-day store keeps it (with the prediction it had)
        p = main._bbmd_find(event)
    if not p:
        raise HTTPException(status_code=404, detail="Match not found")
    idx = await main._bb_index()
    return bf.facts(idx, p)


def _bbmd_find(event: str) -> Optional[Dict]:
    """A basketball match from the last week's match days, in a prediction's shape."""
    r = main._get_redis()
    if not r:
        return None
    today = date.today()
    for i in range(0, 8):
        e = main._bbmd_load(r, (today - timedelta(days=i)).isoformat()).get(event)
        if e:
            pred = e.get("pred") or {}
            return {"home": e.get("home"), "away": e.get("away"), "date": e.get("date"), "time": e.get("time"),
                    "total_line": pred.get("total_line"), "handicap_line": pred.get("handicap_line")}
    return None


def _bbmd_load(r, d: str) -> Dict[str, Dict]:
    import basketball_matchday as bbmd
    try:
        raw = r.get(bbmd.KEY.format(d)) if r else None
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _bbmd_save(r, d: str, day: Dict[str, Dict]) -> None:
    import basketball_matchday as bbmd
    if r:
        r.set(bbmd.KEY.format(d), json.dumps(day, separators=(",", ":")), ex=bbmd.TTL)


def _bbmd_merge(preds: List[Dict]) -> int:
    """Keep each priced match's prediction in its day (until tip-off)."""
    import basketball_matchday as bbmd
    r = main._get_redis()
    if not r:
        return 0
    now, changed = datetime.now(timezone.utc), 0
    for d, day_preds in bbmd.by_date(preds).items():
        day = main._bbmd_load(r, d)
        if bbmd.merge_predictions(day, day_preds, now):
            main._bbmd_save(r, d, day)
            changed += 1
    return changed


async def _bb_live_guarded() -> Dict[str, Any]:
    return await main._guarded_tick("basketball_live", main._bb_live_tick, 110)


@router.get("/api/basketball/matchday/strip")
async def get_basketball_strip(request: Request, today_: str = Query("", alias="today", max_length=10)):
    """The basketball date strip: 7 days back to 14 ahead of the viewer's date, like football's."""
    import basketball_matchday as bbmd
    await main._check_sport_access(request, "basketball")
    today = main._client_today(today_)
    hit = main._bb_strip_cache.get(today.isoformat())
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    dates = [(today + timedelta(days=o)).isoformat() for o in range(-main.MD_DAYS_BACK, main.MD_DAYS_AHEAD + 1)]
    r = main._get_redis()
    upcoming: Dict[str, int] = {}
    for p in main._bb_upcoming():
        upcoming[p["date"]] = upcoming.get(p["date"], 0) + 1
    raws = r.mget([bbmd.KEY.format(d) for d in dates]) if r else [None] * len(dates)
    days = []
    for d, raw in zip(dates, raws):
        try:
            entries = json.loads(raw).values() if raw else []
        except Exception:
            entries = []
        s = bbmd.day_summary(entries)
        if d >= today.isoformat():
            s["total"] = max(s["total"], upcoming.get(d, 0))
        days.append({"date": d, **s})
    out = {"today": today.isoformat(), "days": days}
    main._bb_strip_cache[today.isoformat()] = (time.time(), out)
    return out


@router.get("/api/basketball/matchday")
async def get_basketball_matchday(request: Request, date_: str = Query("", alias="date")):
    """One day's basketball: each game's prediction from before tip-off, its
    live or final score, and how our picks did."""
    import basketball_matchday as bbmd
    await main._check_sport_access(request, "basketball")
    today = date.today()
    d = main._date_param(date_ or today.isoformat())
    if not (today - timedelta(days=90) <= d <= today + timedelta(days=main.MD_DAYS_AHEAD + 1)):
        raise HTTPException(status_code=400, detail="date out of range")
    day = await asyncio.to_thread(main._bbmd_load, main._get_redis(), d.isoformat())
    matches = sorted((bbmd.public(e) for e in day.values()),
                     key=lambda m: (m.get("league_name") or "", m.get("time") or "", m.get("home") or ""))
    await asyncio.to_thread(main._with_live_stats, "basketball", d.isoformat(), matches)
    return {"date": d.isoformat(), "today": today.isoformat(), "matches": matches,
            "summary": bbmd.day_summary(day.values()), "updated": main._bbmd_status.get("at")}


@router.get("/api/basketball/status")
async def get_basketball_status(_admin: str = Depends(main.require_admin)):
    """Admin: results stored, leagues rated, the last listing."""
    import basketball_data as bd
    r = main._get_redis()
    leagues = sorted(((lg.name, lg.n, len(lg.attack), round(lg.home_court, 1), round(lg.sigma["margin"], 1))
                      for lg in main._bb_leagues.values()), key=lambda t: -t[1])
    try:
        bt = json.loads(r.get("betiq:bb:backtest") or "null") if r else None
    except Exception:
        bt = None
    return {**main._bb_status, "matchday": dict(main._bbmd_status), "days_stored": r.hlen(bd.RESULTS_KEY) if r else 0, "result_days": bd.RESULT_DAYS,
            "leagues": [{"league": n, "games": g, "teams": t, "home_court": hc, "margin_sd": sd}
                        for n, g, t, hc, sd in leagues],
            "backtest": {"at": bt.get("at"), "overall": bt.get("overall"), "leagues": len(bt.get("leagues") or {})}
            if bt else None}
