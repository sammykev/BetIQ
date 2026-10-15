"""
Admin and debug endpoints: uploads, model status, stats, revenue, traffic, audit, jobs, banner.

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import httpx
import json
import match_cache
import os
import re
import security
import time
import traffic
from data_fetcher import LEAGUES, FootballDataClient
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import Any, Dict, List, Optional

import main

router = APIRouter()


@router.post("/api/admin/upload/basketball-csv")
async def upload_basketball_csv(request: Request):
    """
    Upload a Kaggle NBA/basketball CSV to train the Elo model.
    Multipart form: file field named 'file'.
    Protected by ADMIN_SECRET header.
    """
    from fastapi import UploadFile, File
    import shutil

    await main.require_admin(request)

    form = await request.form()
    upload = form.get("file")
    if not upload:
        raise HTTPException(status_code=400, detail="No file provided")

    os.makedirs(main.DATA_DIR, exist_ok=True)
    dest = os.path.join(main.DATA_DIR, "basketball_games.csv")
    try:
        content = await upload.read()
        with open(dest, "wb") as f:
            f.write(content)
        size_mb = len(content) / (1024 * 1024)

        # Retrain immediately
        from basketball_predictor import train_from_csv, _bball_elo
        import basketball_predictor as bp_mod
        elo = train_from_csv(dest)
        bp_mod._bball_elo = elo  # reset singleton
        teams = len(elo.ratings) if elo else 0

        return {
            "ok": True,
            "size_mb": round(size_mb, 2),
            "teams_trained": teams,
            "message": f"Basketball Elo trained on {teams} teams. Delete cache to refresh predictions.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/debug/basketball-provider")
async def debug_basketball_provider(date: str = "", league: str = "nba", _admin: str = Depends(main.require_admin)):
    """
    Raw diagnostic dump for the ESPN basketball integration.
    No API key required — the endpoints are public.

    Hits the scoreboard endpoint directly (bypasses fetch_scoreboard so
    zero-results vs HTTP error can be distinguished) and returns both the
    raw response and a normalized parse of the first few events.

    Example: /api/debug/basketball-provider
             /api/debug/basketball-provider?date=20260710
             /api/debug/basketball-provider?league=wnba
             /api/debug/basketball-provider?league=ncaam&date=20260301
    """
    from basketball_data_fetcher import ESPN_BASE, parse_completed_games
    from datetime import date as _date
    import httpx as _httpx

    dates_param = date or _date.today().strftime("%Y%m%d")
    url = f"{ESPN_BASE}/{league}/scoreboard"

    out: Dict = {
        "provider": "ESPN unofficial API (no key required)",
        "league": league,
        "dates_queried": dates_param,
        "url": url,
    }

    async with _httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, params={"dates": dates_param, "limit": 20})
        out["http_status"] = r.status_code
        try:
            body = r.json()
            events = body.get("events", [])
            out["event_count"] = len(events)
            out["parsed_sample"] = parse_completed_games(events[:5])
            raw_str = str(body)
            out["raw_sample"] = body if len(raw_str) < 4000 else raw_str[:4000]
        except Exception as exc:
            out["parse_error"] = str(exc)
            out["raw_sample"] = r.text[:2000]

    return out


@router.post("/api/admin/upload/tennis-csv")
async def upload_tennis_csv(request: Request):
    """
    Upload Jeff Sackmann ATP/WTA CSV files to train the tennis Elo model.
    Can upload multiple files — all go into data/tennis/ folder.
    Protected by ADMIN_SECRET header.
    Form fields: file (required), tour (atp|wta, optional)
    """
    await main.require_admin(request)

    form = await request.form()
    upload = form.get("file")
    if not upload:
        raise HTTPException(status_code=400, detail="No file provided")

    tennis_dir = os.path.join(main.DATA_DIR, "tennis")
    os.makedirs(tennis_dir, exist_ok=True)

    # The uploaded name picks the file on disk: keep only a plain *.csv name
    filename = os.path.basename(getattr(upload, "filename", None) or "") or "tennis_matches.csv"
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}\.csv", filename):
        raise HTTPException(status_code=400, detail="Upload a .csv file with a plain name")
    dest = os.path.join(tennis_dir, filename)
    try:
        content = await upload.read()
        with open(dest, "wb") as f:
            f.write(content)
        size_mb = len(content) / (1024 * 1024)

        # Retrain with all CSVs in the tennis folder
        from tennis_predictor import train_tennis_elo
        import tennis_predictor as tp_mod
        elo = train_tennis_elo(tennis_dir)
        tp_mod._tennis_elo = elo
        players = len(elo.ratings) if elo else 0

        return {
            "ok": True,
            "file": filename,
            "size_mb": round(size_mb, 2),
            "players_trained": players,
            "message": f"Tennis Elo trained on {players} players. Predictions will now use this model.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/admin/model-status")
async def model_status(_admin: str = Depends(main.require_admin)):
    """Return status of all trained models."""
    status: Dict[str, Any] = {}

    # Football
    status["football"] = {
        "ready": main._predictor is not None,
        "predictions": len(main._predictions_cache),
    }

    # Basketball
    try:
        from basketball_predictor import get_basketball_elo
        belo = get_basketball_elo()
        status["basketball"] = {
            "ready": belo is not None,
            "teams": len(belo.ratings) if belo else 0,
            "games": sum(belo.games_played.values()) // 2 if belo else 0,
        }
    except Exception:
        status["basketball"] = {"ready": False, "teams": 0}

    # Tennis
    try:
        from tennis_predictor import get_tennis_elo
        telo = get_tennis_elo()
        status["tennis"] = {
            "ready": telo is not None,
            "players": len(telo.ratings) if telo else 0,
            "matches": sum(telo.matches.values()) // 2 if telo else 0,
        }
    except Exception:
        status["tennis"] = {"ready": False, "players": 0}

    return status


@router.get("/api/debug/calendar-status")
async def debug_calendar_status(_admin: str = Depends(main.require_admin)):
    """Quick diagnostic: shows what the calendar will return and what's in cache."""
    from datetime import date as _date
    month = _date.today().strftime("%Y-%m")
    r = main._get_redis()
    cache_dates = sorted({p.get("date", p.get("Date",""))[:10] for p in main._predictions_cache if p.get("date") or p.get("Date")})
    redis_keys = []
    if r:
        try:
            redis_keys = [k.decode() if isinstance(k, bytes) else k
                          for k in r.keys("betiq:md:*")]
        except Exception:
            pass
    return {
        "current_month": month,
        "predictions_in_cache": len(main._predictions_cache),
        "prediction_dates": cache_dates[:10],
        "history_redis_keys": sorted(redis_keys)[:20],
        "sample_prediction_keys": list(main._predictions_cache[0].keys()) if main._predictions_cache else [],
    }


@router.get("/api/debug/pipeline")
async def debug_pipeline(_admin: str = Depends(main.require_admin)):
    """
    Pipeline / cache state — browser-friendly. Shows whether a refresh is
    running, when it last published, how many predictions are cached, the
    per-league breakdown, and any World Cup entries currently served.
    """
    by_league: Dict[str, int] = {}
    for p in main._predictions_cache:
        lg = p.get("league", "?")
        by_league[lg] = by_league.get(lg, 0) + 1
    wc = [
        {"home": p.get("home"), "away": p.get("away"), "date": p.get("date")}
        for p in main._predictions_cache if p.get("league") == "WC"
    ]
    return {
        "is_training": main._is_training,
        "model_ready": main._predictor is not None,
        "last_updated": main._last_updated,
        "total_predictions": len(main._predictions_cache),
        "predictions_by_league": dict(sorted(by_league.items())),
        "wc_predictions": wc,
    }


@router.get("/api/debug/team-form")
async def debug_team_form(team: str, live: bool = False, _admin: str = Depends(main.require_admin)):
    """
    Inspect (or force-refresh) the web-searched form/xG for one team — this is
    the pathway that supplies real xG for teams with sparse local history,
    e.g. World Cup national teams, since Understat only covers the top-5
    European club leagues and predictor.py's own "xG_Home"/"xG_Away" is a
    goals-based Dixon-Coles estimate, not real shot-based xG.

    By default reads whatever is cached (no Groq spend). Pass live=true to
    force a fresh Groq web search + extraction right now — useful
    to check whether the pathway works at all for a given team, or to see
    a fresh error, but costs one Groq call.

    Example: /api/debug/team-form?team=Argentina
             /api/debug/team-form?team=Argentina&live=true
    """
    from llm_service import GROQ_API_KEY

    cached = main._get_web_form_cache(team)
    local_pts = len(main._predictor.team_stats.get(main._predictor.canon(team), {}).get("pts", [])) if main._predictor else None

    out: Dict = {
        "team": team,
        "groq_api_key_set": bool(GROQ_API_KEY),
        "local_match_count": local_pts,
        "cached_web_form": cached,
    }
    if not cached:
        out["note"] = ("No cached web form for this team yet. It's fetched during the "
                       "pipeline's background prefetch for any team without one already "
                       "cached — NOT gated on local match count, since historical W/D/L "
                       "records (which national teams often have plenty of) contain no xG "
                       "at all. Capped at 25 teams per run, World Cup/major-tournament teams "
                       "prioritised. Pass live=true to fetch it right now instead of waiting.")

    if live:
        if not GROQ_API_KEY:
            out["live_fetch_error"] = "GROQ_API_KEY not set on the server"
        else:
            from llm_service import fetch_team_form_web
            fixture_comp = next(
                (p.get("league_name", "") for p in main._predictions_cache
                 if p.get("home") == team or p.get("away") == team),
                "",
            )
            fresh = await fetch_team_form_web(team, competition=fixture_comp, debug=True)
            out["live_fetch_competition_hint"] = fixture_comp or None
            if fresh.get("_debug_stage"):
                # Failed — surface exactly where/why instead of pointing at
                # server logs the free-tier dashboard may not expose usefully.
                out["live_fetch_result"] = None
                out["live_fetch_failed_at"] = fresh["_debug_stage"]
                out["live_fetch_error"] = fresh["_debug_detail"]
                if "search_text" in fresh:
                    out["live_fetch_search_text_sample"] = fresh["search_text"]
            else:
                out["live_fetch_result"] = fresh or None
                if fresh and fresh.get("matches") and main._predictor is not None:
                    # A successful live check is now also a "warm this team's
                    # cache right now" action — otherwise the data this call
                    # just fetched would be thrown away and the match page
                    # still wouldn't show it until the next full pipeline run.
                    main._apply_web_form(main._predictor, team, fresh)
                    out["cached"] = True
                elif not fresh:
                    out["live_fetch_error"] = "Unexpected empty result with no debug info."

    return out


@router.get("/api/debug/odds")
async def debug_odds(probe_sportybet: bool = True, probe_odds_api: bool = False, _admin: str = Depends(main.require_admin)):
    """
    Diagnose why market odds might be missing. There are TWO independent odds
    systems and either can fail on its own:
      1. The Odds API (the-odds-api.com) — powers the small 1X2 odds badges on
         prediction cards and value-bet detection. Free tier: 500 req/month.
      2. SportyBet (scraped, no quota) — powers the full market list + live
         odds shown in the Bet Builder / match analysis screen.

    By default this reports what's already cached (no extra Odds API calls,
    since that quota is scarce) and does one fresh SportyBet lookup (free).
    Pass probe_odds_api=true to force one live Odds API call — only do this
    if you need to see a fresh error message, since it spends quota.
    """
    out: Dict = {}

    # ── The Odds API — read from what's already cached, no extra spend ──────
    with_odds = sum(1 for p in main._predictions_cache if p.get("odds_home"))
    with_value_flag = sum(1 for p in main._predictions_cache if p.get("value_edge") is not None)
    out["odds_api"] = {
        "api_key_set": bool(os.getenv("ODDS_API_KEY", "")),
        "cached_predictions": len(main._predictions_cache),
        "cached_predictions_with_odds_badge": with_odds,
        "cached_predictions_with_value_edge_computed": with_value_flag,
        "note": ("0 with odds while api_key_set=true usually means quota "
                 "exhausted (500/month) or no bookmaker coverage for these "
                 "leagues/dates yet.") if with_odds == 0 else None,
    }
    if probe_odds_api:
        try:
            from odds_fetcher import _fetch_odds_for_sport
            events = await _fetch_odds_for_sport("soccer_fifa_world_cup")
            out["odds_api"]["live_probe"] = {
                "sport": "soccer_fifa_world_cup",
                "events_returned": len(events),
            }
        except Exception as e:
            out["odds_api"]["live_probe"] = {"error": str(e)}

    # ── SportyBet — free to probe, no quota ──────────────────────────────────
    if probe_sportybet:
        try:
            from sportybet import fetch_events_for_date, find_event
            from datetime import date as _date, timedelta as _td
            found_any = {}
            wc_matches = [p for p in main._predictions_cache if p.get("league") == "WC"][:3]
            for offset in range(0, 3):
                d = str(_date.today() + _td(days=offset))
                events = await fetch_events_for_date(d)
                found_any[d] = len(events)
                if events and wc_matches:
                    for m in wc_matches:
                        if m.get("date") == d:
                            ev = find_event(m["home"], m["away"], events)
                            found_any[f"match_{m['home']}_vs_{m['away']}"] = bool(ev)
            out["sportybet"] = {
                "events_by_date": found_any,
                "wc_fixtures_checked": [f"{m['home']} vs {m['away']} ({m['date']})" for m in wc_matches],
            }
        except Exception as e:
            out["sportybet"] = {"error": str(e)}

    return out


@router.get("/api/debug/fixtures")
async def debug_fixtures(league: str = "WC", days: int = 90, _admin: str = Depends(main.require_admin)):
    """
    Diagnostic: hit football-data.org directly for one competition and report
    what actually comes back — HTTP reachability, per-status counts, a sample,
    and how many survive our upcoming-fixtures filter. Use this to tell apart
    "no API access" vs "matches all TBD/finished" vs "working".
    Example: /api/debug/fixtures?league=WC
    """
    from datetime import date as _date, timedelta as _td
    league = league.upper()
    out: Dict = {
        "league": league,
        "known_league": league in LEAGUES,
        "api_key_set": bool(main.API_KEY),
        "date_from": str(_date.today()),
        "date_to": str(_date.today() + _td(days=days)),
    }
    if not main.API_KEY:
        out["error"] = "FOOTBALL_DATA_API_KEY not set on the server"
        return out

    client = FootballDataClient(main.API_KEY)
    raw = await client.fetch_matches_raw(league, days)
    if raw is None:
        out["error"] = ("football-data.org returned no data — the competition may "
                        "not be available on this API key's plan (or it was rate-limited).")
        out["raw_matches"] = 0
        return out

    matches = raw.get("matches", []) or []
    status_counts: Dict[str, int] = {}
    tbd = 0
    _SKIP = {"tbd", "tba", "to be announced", "", "none"}
    for m in matches:
        st = m.get("status", "UNKNOWN")
        status_counts[st] = status_counts.get(st, 0) + 1
        hn = (m.get("homeTeam", {}).get("name") or "").strip().lower()
        an = (m.get("awayTeam", {}).get("name") or "").strip().lower()
        if hn in _SKIP or an in _SKIP:
            tbd += 1

    # What our real fetch_upcoming would return after filtering
    upcoming = await client.fetch_upcoming(league, days)

    out.update({
        "competition": raw.get("competition", {}).get("name"),
        "raw_matches": len(matches),
        "status_breakdown": status_counts,
        "matches_with_tbd_teams": tbd,
        "fixtures_after_filter": len(upcoming),
        "sample": [
            {
                "home": m.get("homeTeam", {}).get("name"),
                "away": m.get("awayTeam", {}).get("name"),
                "date": m.get("utcDate", "")[:10],
                "status": m.get("status"),
            }
            for m in matches[:8]
        ],
    })
    return out


@router.get("/api/debug/predict")
async def debug_predict(home: str, away: str, date_str: Optional[str] = None, _admin: str = Depends(main.require_admin)):
    """
    Explain a single prediction: the raw feature values the model actually
    saw (Elo, xG, market-implied odds, form, etc.) alongside the resulting
    probabilities. Use this to check whether a surprising pick (e.g. away
    favoured despite a big home Elo/xG edge) is a real bug or the market-odds
    features legitimately outweighing Elo/xG — the model uses ~27 features,
    not just those two, and Impl_Home/Impl_Draw/Impl_Away (derived from
    bookmaker odds) are typically the single strongest signal. If those odds
    got fuzzy-matched to the wrong fixture, this is exactly where it'd show.

    Example: /api/debug/predict?home=Arsenal&away=Chelsea
    """
    if main._predictor is None or not getattr(main._predictor, "_ready", False):
        raise HTTPException(status_code=503, detail="Model not ready")

    # Pull whatever odds we have cached for this exact fixture, same as the
    # live pipeline would have used, so the feature values shown here match
    # what's actually being served — not a fresh, potentially different fetch.
    cached_odds, odds_fetched_at = main._load_cached_live_odds()
    key_candidates = [k for k in cached_odds if k.startswith(f"{home}:{away}:")]
    matched_odds = cached_odds.get(key_candidates[0]) if key_candidates else {}

    odds_home = float(matched_odds.get("1") or 0)
    odds_draw = float(matched_odds.get("X") or 0)
    odds_away = float(matched_odds.get("2") or 0)

    feats = main._predictor._feats(home, away, odds_home, odds_draw, odds_away, match_date=date_str)
    prediction = main._predictor.predict_match(home, away, odds_home, odds_draw, odds_away, match_date=date_str)

    elo_gap = feats["HomeElo"] - feats["AwayElo"]
    xg_gap = feats["xG_Home"] - feats["xG_Away"]

    # Flag cases where Elo/xG clearly favour one side but the model's win
    # probability favours the other — the thing the user actually asked about.
    flags = []
    if elo_gap > 50 and xg_gap > 0.2 and prediction and prediction["p_away"] > prediction["p_home"]:
        flags.append("Home leads on both Elo and xG, but the model favours Away — "
                      "check whether Impl_Home/Impl_Draw/Impl_Away below explain it.")
    if elo_gap < -50 and xg_gap < -0.2 and prediction and prediction["p_home"] > prediction["p_away"]:
        flags.append("Away leads on both Elo and xG, but the model favours Home — "
                      "check whether Impl_Home/Impl_Draw/Impl_Away below explain it.")
    if not key_candidates:
        flags.append("No cached live odds matched this exact fixture key — "
                      "Impl_Home/Draw/Away below are the league-average fallback, "
                      "not real market odds. This can happen for a new/renamed team "
                      "name or if this fixture hasn't been through the live-odds pass yet.")

    return {
        "home": home,
        "away": away,
        "prediction": prediction,
        "features": feats,
        "elo_gap_home_minus_away": round(elo_gap, 1),
        "xg_gap_home_minus_away": round(xg_gap, 3),
        "matched_live_odds": matched_odds or None,
        "live_odds_cache_age": odds_fetched_at,
        "flags": flags,
    }


@router.get("/api/explain")
async def explain_match(home: str, away: str, day: str = Query("", alias="date"),
                        _access=Depends(main.require_feature("ai_preview"))):
    """
    A plain-language AI preview of a match: the model's numbers (the same
    analysis the page shows), the teams' recent results and meetings from our
    own data, and team news published in the last week. Built once per match
    and kept until it has been played; rebuilt in the background when the
    news, the model or the results change.
    """
    from llm_service import explain_match as _explain
    import match_facts

    if main._predictor is None:
        raise HTTPException(status_code=503, detail="Model not ready")
    fx, key, ko = main._match_timing(home, away, day)
    analysis, a_info = await main._analysis_cached(home, away, day)
    if not analysis:
        raise HTTPException(status_code=404, detail="Could not generate prediction")
    news, n_info = await main._match_news(home, away, day)
    facts = await main._facts_for(home, away, day or fx.get("date", ""))

    probs = {o["code"]: o["prob"] for m in analysis.get("markets", []) if m.get("id") == "1x2"
             for o in m.get("options", [])}
    prediction = {"p_home": probs.get("1", 0), "p_draw": probs.get("X", 0), "p_away": probs.get("2", 0)}
    facts_text = match_facts.text(facts, home, away)
    fp = match_cache.fingerprint(n_info.get("at"), {k: round(v, 2) for k, v in probs.items()}, facts_text)

    async def build():
        result = await _explain(home, away, analysis, prediction, news=news, facts=facts_text,
                                kickoff=day or fx.get("date", ""), competition=fx.get("league_name", ""))
        if not result.get("explanation"):
            return None
        r = main._get_redis()
        if r:
            try:
                today = date.today().isoformat()
                r.incr(f"betiq:stats:explain:{today}")
                r.expire(f"betiq:stats:explain:{today}", 86400 * 7)
                r.incr("betiq:stats:explain:total")
            except Exception:
                pass
        return result

    result, info = await main._mcache.get("explain", key, build, fp=fp, fresh_for=24 * 3600,
                                     ttl=match_cache.ttl_seconds(ko))
    if result is None:
        result = {"explanation": None, "sources": [], "model": None, "error": "all_models_failed"}
    return {**result, "news": news.get("items") or [], "news_checked_at": n_info.get("at"),
            "updated_at": info.get("at"),
            "refreshing": bool(info.get("refreshing") or n_info.get("refreshing"))}


@router.get("/api/admin/whoami")
async def admin_whoami(request: Request):
    """Whether this request is an admin (the admin page's sign-in check).
    A signed-in non-admin sees their Clerk user id, to add to ADMIN_USER_IDS."""
    via, uid = await main._admin_identity(request)
    return {"admin": bool(via), "via": via, "user_id": uid,
            "clerk_admins_configured": bool(main.ADMIN_USER_IDS)}


@router.get("/api/admin/stats")
async def admin_stats(_admin: str = Depends(main.require_admin)):
    r = main._get_redis()
    if not r:
        return {"error": "no_redis"}

    from datetime import timedelta
    today = date.today()
    daily = []
    overall = {"won": 0, "lost": 0, "pending": 0}
    league_stats: Dict[str, Any] = {}
    tip_stats: Dict[str, Any] = {}
    all_preds: List[Dict] = []

    for i in range(30):
        d = (today - timedelta(days=i)).isoformat()
        try:
            preds = main._read_history(r, d)
            if not preds:
                continue
            won     = sum(1 for p in preds if p.get("outcome") == "won")
            lost    = sum(1 for p in preds if p.get("outcome") == "lost")
            pending = sum(1 for p in preds if p.get("outcome") == "pending")
            daily.append({
                "date": d, "won": won, "lost": lost, "pending": pending,
                "accuracy": round(won / (won + lost) * 100, 1) if (won + lost) > 0 else None
            })
            overall["won"]     += won
            overall["lost"]    += lost
            overall["pending"] += pending
            all_preds.extend(preds)
        except Exception:
            pass

    daily.reverse()

    for p in all_preds:
        if p.get("outcome") not in ("won", "lost"):
            continue
        lg = p.get("league", "?")
        league_stats.setdefault(lg, {"won": 0, "lost": 0, "name": p.get("league_name", lg), "flag": p.get("flag", "")})
        league_stats[lg]["won" if p["outcome"] == "won" else "lost"] += 1
        tip = p.get("tip_1x2", "?")
        tip_stats.setdefault(tip, {"won": 0, "lost": 0})
        tip_stats[tip]["won" if p["outcome"] == "won" else "lost"] += 1

    for lg in league_stats:
        t = league_stats[lg]["won"] + league_stats[lg]["lost"]
        league_stats[lg]["accuracy"] = round(league_stats[lg]["won"] / t * 100, 1) if t else 0
        league_stats[lg]["total"] = t
    for tip in tip_stats:
        t = tip_stats[tip]["won"] + tip_stats[tip]["lost"]
        tip_stats[tip]["accuracy"] = round(tip_stats[tip]["won"] / t * 100, 1) if t else 0
        tip_stats[tip]["total"] = t

    settled = overall["won"] + overall["lost"]
    overall["accuracy"] = round(overall["won"] / settled * 100, 1) if settled else 0

    # AI usage
    today_str = today.isoformat()
    explain_today = int(r.get(f"betiq:stats:explain:{today_str}") or 0)
    explain_total = int(r.get("betiq:stats:explain:total") or 0)
    chat_queries  = r.lrange("betiq:stats:chat_queries", 0, 19) if hasattr(r, "lrange") else []

    return {
        "overall": overall,
        "daily": daily,
        "by_league": league_stats,
        "by_tip": tip_stats,
        "ai": {"explain_today": explain_today, "explain_total": explain_total, "recent_queries": chat_queries},
    }


def _revenue_summary(transactions: List[Dict], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Totals, a 30-day daily series and recent payments from Paystack's
    successful transactions (amounts in kobo)."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    by_day: Dict[str, float] = {}
    total = today_sum = week = month = 0.0
    customers = set()
    for t in transactions:
        naira = (t.get("amount") or 0) / 100
        total += naira
        email = ((t.get("customer") or {}).get("email") or "").lower()
        if email:
            customers.add(email)
        try:
            day = datetime.fromisoformat(str(t.get("paid_at") or t.get("created_at")).replace("Z", "+00:00")).date()
        except ValueError:
            continue
        age = (today - day).days
        today_sum += naira if age == 0 else 0
        week += naira if age < 7 else 0
        month += naira if age < 30 else 0
        if age < 30:
            by_day[day.isoformat()] = by_day.get(day.isoformat(), 0) + naira
    series = [{"date": (today - timedelta(days=i)).isoformat(),
               "amount": round(by_day.get((today - timedelta(days=i)).isoformat(), 0), 2)} for i in range(29, -1, -1)]
    return {
        "total_revenue": round(total, 2), "transaction_count": len(transactions),
        "today": round(today_sum, 2), "last_7_days": round(week, 2), "last_30_days": round(month, 2),
        "average": round(total / len(transactions), 2) if transactions else 0,
        "customers": len(customers), "series": series,
        "recent": [{"email": (t.get("customer") or {}).get("email", ""), "amount": (t.get("amount") or 0) / 100,
                    "date": (t.get("paid_at") or "")[:10], "reference": t.get("reference", ""),
                    "channel": t.get("channel") or ""} for t in transactions[:15]],
    }


@router.get("/api/admin/revenue")
async def admin_revenue(fresh: bool = False, _admin: str = Depends(main.require_admin)):
    paystack_key = os.getenv("PAYSTACK_SECRET_KEY", "")
    if not paystack_key:
        return {"error": "no_paystack_key"}
    if not fresh and main._revenue_cache["data"] and time.time() - main._revenue_cache["at"] < main.REVENUE_CACHE_SECONDS:
        return main._revenue_cache["data"]
    transactions: List[Dict] = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for page in range(1, 6):  # up to 500 payments
                r = await client.get("https://api.paystack.co/transaction",
                                     params={"status": "success", "perPage": 100, "page": page},
                                     headers={"Authorization": f"Bearer {paystack_key}"})
                if r.status_code != 200:
                    if page == 1:
                        return {"error": f"paystack_{r.status_code}"}
                    break
                batch = r.json().get("data") or []
                transactions += batch
                if len(batch) < 100:
                    break
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    data = main._revenue_summary(transactions)
    main._revenue_cache.update(at=time.time(), data=data)
    return data


def _traffic_key_ok(given: str) -> bool:
    import hmac
    expected = os.getenv("TRAFFIC_KEY") or main.ADMIN_SECRET
    return bool(expected and given) and hmac.compare_digest(given.encode(), expected.encode())


@router.post("/api/traffic/hit")
async def traffic_hit(request: Request, body: Dict[str, Any]):
    """A page view, sent by the site's own /api/t route (which adds the
    visitor's location) with the shared TRAFFIC_KEY (or ADMIN_SECRET)."""
    if not main._traffic_key_ok(request.headers.get("x-traffic-key", "").strip()):
        raise HTTPException(status_code=403, detail="Forbidden")
    hit = traffic.clean_hit(body)
    if hit:
        main._traffic.record(hit)
    return {"ok": bool(hit)}


@router.get("/api/admin/traffic")
async def admin_traffic(days: int = 30, _admin: str = Depends(main.require_admin)):
    days = min(90, max(1, days))
    await asyncio.to_thread(main._flush_traffic)
    data = await asyncio.to_thread(traffic.summary, main._get_redis(), days)
    return {**data, "live": main._traffic.live()}


@router.get("/api/admin/traffic/live")
async def admin_traffic_live(_admin: str = Depends(main.require_admin)):
    """Who's on the site in the last 5 minutes (memory only — cheap to poll)."""
    return main._traffic.live()


# ── Audit log, security overview, jobs ────────────────────────────────────
@router.get("/api/admin/audit")
async def admin_audit(_admin: str = Depends(main.require_admin)):
    r = main._get_redis()
    if not r:
        return {"entries": []}
    try:
        return {"entries": [json.loads(x) for x in r.lrange(main.AUDIT_KEY, 0, 99)]}
    except Exception:
        return {"entries": []}


@router.get("/api/admin/security")
async def admin_security(_admin: str = Depends(main.require_admin)):
    """How this server is protected, as checks the admin can act on, plus the
    recent security events (wrong admin secrets, rate limits)."""
    import auth
    events = security.recent_events(200)
    day_ago = time.time() - 86400
    recent = [e for e in events if e.get("at", 0) >= day_ago]
    keys = await main._clerk_keys() if auth.premium_enforced() else {"ok": False, "reason": ""}
    checks = [
        {"id": "clerk_issuer", "ok": auth.auth_enforced(), "label": "Signed-in users verified (CLERK_ISSUER)",
         "fix": "Set CLERK_ISSUER in the server's .env: without it, user data endpoints trust the uid the browser sends."},
        {"id": "premium", "ok": auth.premium_enforced() and keys["ok"], "label": "Paywall enforced on the server (CLERK_SECRET_KEY)",
         "fix": keys["reason"] or "Set CLERK_SECRET_KEY (Clerk dashboard → API keys → Secret key) in the server's .env so premium analysis can't be fetched directly."},
        {"id": "admin_ids", "ok": bool(main.ADMIN_USER_IDS), "label": "Admins sign in with Clerk (ADMIN_USER_IDS)",
         "fix": "Add your Clerk user id to ADMIN_USER_IDS in the server's .env and on Vercel, then you rarely need the secret."},
        {"id": "admin_secret", "ok": len(main.ADMIN_SECRET) >= 24, "label": "Admin secret is long (24+ characters)",
         "fix": "Use a long random ADMIN_SECRET (e.g. `openssl rand -hex 24`), the same value in the server's .env and on Vercel."},
        {"id": "traffic_key", "ok": bool(os.getenv("TRAFFIC_KEY")), "label": "Separate traffic key (TRAFFIC_KEY)",
         "fix": "Optional: set TRAFFIC_KEY, the same value in the server's .env and on Vercel, so page-view reporting doesn't reuse the admin secret."},
        {"id": "redis", "ok": main._get_redis() is not None, "label": "Redis connected",
         "fix": "Set UPSTASH_REDIS_URL in the server's .env."},
        {"id": "rate_limits", "ok": os.getenv("RATE_LIMITS", "1") != "0", "label": "Rate limits on",
         "fix": "Remove RATE_LIMITS=0 from the server's .env."},
        {"id": "cors", "ok": "*" not in main.ALLOWED_ORIGINS, "label": "Only our site may call the API from a browser",
         "fix": "Remove * from ALLOWED_ORIGINS."},
    ]
    counts: Dict[str, int] = {}
    for e in recent:
        counts[e.get("type", "?")] = counts.get(e.get("type", "?"), 0) + 1
    return {"checks": checks, "last_24h": counts, "events": events[:100], "allowed_origins": main.ALLOWED_ORIGINS,
            "rate_limits": [{"path": p, "limit": n, "per_seconds": w} for p, n, w in security.RATE_LIMITS]}


@router.get("/api/admin/jobs")
async def admin_jobs(_admin: str = Depends(main.require_admin)):
    jobs = {j.id: j for j in main.scheduler.get_jobs()} if main.scheduler.running else {}
    return {"training": main._is_training, "jobs": [
        {"id": jid, "name": name,
         "next_run": jobs[jid].next_run_time.isoformat() if jid in jobs and jobs[jid].next_run_time else None,
         "every": str(jobs[jid].trigger.interval) if jid in jobs and hasattr(jobs[jid].trigger, "interval") else None}
        for jid, (name, _) in main.ADMIN_JOBS.items()]}


@router.post("/api/admin/jobs/{job_id}/run")
async def admin_run_job(job_id: str, _admin: str = Depends(main.require_admin)):
    if job_id not in main.ADMIN_JOBS:
        raise HTTPException(status_code=404, detail="Unknown job")
    if job_id == "refresh" and main._is_training:
        return {"started": False, "message": "Already refreshing"}
    main._audit(_admin, "run_job", job=job_id)
    asyncio.create_task(main.ADMIN_JOBS[job_id][1]())
    return {"started": True, "message": f"{main.ADMIN_JOBS[job_id][0]} started"}


@router.get("/api/admin/banner")
async def get_banner():
    r = main._get_redis()
    if r:
        try:
            val = r.get("betiq:config:banner")
            if val:
                return {"banner": val}
        except Exception:
            pass
    return {"banner": None}


@router.post("/api/admin/banner")
async def set_banner(body: Dict[str, Any], _admin: str = Depends(main.require_admin)):
    r = main._get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    text = (body.get("text") or "").strip()[:300]
    if text:
        r.set("betiq:config:banner", text)
    else:
        r.delete("betiq:config:banner")
    main._audit(_admin, "banner", text=text or "(cleared)")
    return {"banner": text or None}


@router.get("/api/config/maintenance")
async def get_maintenance():
    r = main._get_redis()
    if r:
        try:
            val = r.get("betiq:config:maintenance")
            if val is not None:
                return {"enabled": val == "true"}
        except Exception:
            pass
    return {"enabled": False}


@router.post("/api/config/maintenance")
async def set_maintenance(body: Dict[str, Any], _admin: str = Depends(main.require_admin)):
    r = main._get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    enabled = bool(body.get("enabled", False))
    r.set("betiq:config:maintenance", "true" if enabled else "false")
    main._audit(_admin, "maintenance", enabled=enabled)
    return {"enabled": enabled}


@router.post("/api/admin/clear-cache")
async def clear_cache(body: Dict[str, Any], _admin: str = Depends(main.require_admin)):
    r = main._get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    try:
        r.delete("betiq:predictions")
        # Clear all explanation caches
        for key in r.scan_iter("betiq:explain:*"):
            r.delete(key)
        main._audit(_admin, "clear_cache")
        return {"cleared": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/admin/featured")
async def get_featured():
    r = main._get_redis()
    if r:
        try:
            val = r.get("betiq:config:featured")
            if val:
                return {"featured": json.loads(val)}
        except Exception:
            pass
    return {"featured": []}


@router.post("/api/admin/featured")
async def set_featured(body: Dict[str, Any], _admin: str = Depends(main.require_admin)):
    r = main._get_redis()
    if not r:
        raise HTTPException(status_code=503, detail="No Redis")
    picks = [p for p in body.get("picks", []) if isinstance(p, dict)][:3]
    if picks:
        r.set("betiq:config:featured", json.dumps(picks))
    else:
        r.delete("betiq:config:featured")
    main._audit(_admin, "featured", picks=[f"{p.get('home')} v {p.get('away')}" for p in picks] or "(cleared)")
    return {"featured": picks}


@router.post("/api/log/query")
async def log_query(body: Dict[str, Any]):
    """Log a chat query for admin analytics (called from Next.js chat route)."""
    query = (body.get("query") or "").strip()[:200]
    if not query:
        return {"ok": True}
    r = main._get_redis()
    if r:
        try:
            r.lpush("betiq:stats:chat_queries", query)
            r.ltrim("betiq:stats:chat_queries", 0, 99)
        except Exception:
            pass
    return {"ok": True}
