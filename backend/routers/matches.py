"""
Predictions, leagues, head-to-head, match facts and the AI analysis.

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import glob
import hashlib
import international_fixtures as intl
import json
import match_cache
import os
import pandas as pd
import time
from data_fetcher import LEAGUES, FootballDataClient
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from team_names import UCL_ALIASES, TeamResolver
from typing import Any, Dict, Optional, Tuple

import main

router = APIRouter()


def _last_backup():
    r = main._get_redis()
    if r is None:
        return None
    try:
        raw = r.get("betiq:backup:last")
        return json.loads(raw) if raw else None
    except Exception:
        return None


@router.get("/api/leagues")
async def get_leagues():
    # International competitions with upcoming matches first (they're only
    # busy during international windows), then the club leagues
    live = {p.get("league", "") for p in main._predictions_cache if main._within_window(p.get("date", ""))}
    international = [
        {"code": code, "name": name, "country": "World", "flag": flag}
        for code, name, flag, _ in intl.COMPETITIONS if code in live
    ]
    if intl.LEAGUE_CODE in live:
        international.append({"code": intl.LEAGUE_CODE, **intl.LEAGUE_INFO})
    return international + [
        {"code": code, **info}
        for code, info in LEAGUES.items()
    ]


@router.get("/api/predictions")
async def get_predictions(
    request: Request,
    league: Optional[str] = None,
    date_str: Optional[str] = None,
    min_confidence: float = 0.0,
    limit: int = 500,
):
    """The upcoming football predictions, most confident first. Each variant is
    built once per predictions update (and per minute, as the window moves)
    and served with an ETag: a browser holding the same list gets a 304."""
    key = (id(main._predictions_cache), main._last_updated, (league or "").upper(), date_str or "", min_confidence, limit,
           int(time.time() // 60))
    hit = main._predictions_response.get(key)
    if hit is None:
        # Predictions cached before the window shrank stay hidden
        data = [p for p in main._predictions_cache if main._within_window(p.get("date", ""))]
        if league and league.upper() != "ALL":
            data = [p for p in data if p.get("league", "").upper() == league.upper()]
        if date_str:
            data = [p for p in data if p.get("date") == date_str]
        if min_confidence > 0:
            data = [p for p in data if main._pick_confidence(p) >= min_confidence]
        # Sort by confidence desc, then date
        data = sorted(data, key=lambda x: (-main._pick_confidence(x), x.get("date", "")))
        body = json.dumps({"predictions": data[:limit], "total": len(data), "last_updated": main._last_updated},
                          separators=(",", ":"), default=str).encode()
        hit = (body, '"' + hashlib.sha1(body).hexdigest()[:20] + '"')
        if len(main._predictions_response) > 32:
            main._predictions_response.clear()
        main._predictions_response[key] = hit
    body, etag = hit
    headers = {"ETag": etag, "Cache-Control": "public, max-age=30, stale-while-revalidate=600"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


def _parse_api_h2h(data: Dict, home: str, away: str) -> Dict:
    """Convert football-data.org H2H response into our standard format."""
    agg = data.get("aggregates", {})
    home_agg = agg.get("homeTeam", {})
    away_agg = agg.get("awayTeam", {})
    total = agg.get("numberOfMatches", 0)
    total_goals = agg.get("totalGoals", 0)

    # The API returns stats from the perspective of the fixture's home team.
    # We normalise to our requested home team.
    api_home_name = home_agg.get("name", "")
    our_home_is_api_home = api_home_name.lower() == home.lower()

    if our_home_is_api_home:
        home_w = home_agg.get("wins", 0)
        away_w = away_agg.get("wins", 0)
    else:
        home_w = away_agg.get("wins", 0)
        away_w = home_agg.get("wins", 0)

    draws = total - home_w - away_w

    rows = []
    for m in data.get("matches", []):
        sc = m.get("score", {}).get("fullTime", {})
        hg = sc.get("home")
        ag = sc.get("away")
        if hg is None or ag is None:
            continue
        result = "H" if hg > ag else ("A" if ag > hg else "D")
        w = m.get("winner")
        rows.append({
            "date": m["utcDate"][:10],
            "home_team": m["homeTeam"]["name"],
            "away_team": m["awayTeam"]["name"],
            "score": f"{hg}-{ag}",
            "result": result,
            "winner": m["homeTeam"]["name"] if w == "HOME_TEAM" else (
                      m["awayTeam"]["name"] if w == "AWAY_TEAM" else None),
            "competition": m.get("competition", {}).get("name", ""),
        })

    home_gf = sum(
        int(r["score"].split("-")[0]) if r["home_team"].lower() == home.lower()
        else int(r["score"].split("-")[1])
        for r in rows
    )
    home_ga = sum(
        int(r["score"].split("-")[1]) if r["home_team"].lower() == home.lower()
        else int(r["score"].split("-")[0])
        for r in rows
    )
    n = len(rows)
    btts = sum(1 for r in rows if int(r["score"].split("-")[0]) > 0 and int(r["score"].split("-")[1]) > 0)

    return {
        "meetings": rows,
        "summary": {
            "total": total,
            "home_wins": home_w,
            "draws": draws,
            "away_wins": away_w,
            "home_goals": home_gf,
            "away_goals": home_ga,
            "avg_goals": round(total_goals / total, 1) if total else 0,
            "btts_count": btts,
        },
        "source": "api",
        "fetched_at": datetime.utcnow().isoformat(),
    }


def _parse_csv_h2h(home: str, away: str, limit: int = 10) -> Dict:
    """Fall back to CSV history when API data unavailable."""
    if main._history_df is None:
        return {"meetings": [], "summary": None, "source": "none"}

    df = main._history_df
    if main._predictor is not None:  # the CSVs use the model's names, not the API's
        home, away = main._predictor.canon(home), main._predictor.canon(away)
    mask = (
        ((df["HomeTeam"] == home) & (df["AwayTeam"] == away)) |
        ((df["HomeTeam"] == away) & (df["AwayTeam"] == home))
    )
    meetings = df[mask].sort_values("Date", ascending=False).head(limit)
    if meetings.empty:
        return {"meetings": [], "summary": None, "source": "csv"}

    rows, home_w, draw, away_w, home_gf, home_ga = [], 0, 0, 0, 0, 0
    for _, r in meetings.iterrows():
        is_h = r["HomeTeam"] == home
        gf = int(r["FTHG"]) if is_h else int(r["FTAG"])
        ga = int(r["FTAG"]) if is_h else int(r["FTHG"])
        res_for_home = r["Result"] if is_h else {"H": "A", "A": "H", "D": "D"}.get(r["Result"], r["Result"])
        if res_for_home == "H": home_w += 1
        elif res_for_home == "A": away_w += 1
        else: draw += 1
        home_gf += gf; home_ga += ga
        rows.append({
            "date": r["Date"].strftime("%d %b %Y"),
            "home_team": r["HomeTeam"], "away_team": r["AwayTeam"],
            "score": f"{int(r['FTHG'])}-{int(r['FTAG'])}",
            "result": r["Result"],
            "winner": r["HomeTeam"] if r["Result"] == "H" else (r["AwayTeam"] if r["Result"] == "A" else None),
            "competition": "Historical",
        })

    n = len(rows)
    btts = sum(1 for r in rows if int(r["score"].split("-")[0]) > 0 and int(r["score"].split("-")[1]) > 0)
    return {
        "meetings": rows,
        "summary": {
            "total": n, "home_wins": home_w, "draws": draw, "away_wins": away_w,
            "home_goals": home_gf, "away_goals": home_ga,
            "avg_goals": round((home_gf + home_ga) / n, 1) if n else 0,
            "btts_count": btts,
        },
        "source": "csv",
        "fetched_at": datetime.utcnow().isoformat(),
    }


async def _fetch_live_odds(home: str, away: str, date_str: str = "") -> Dict:
    """
    Fetch truly live odds from The Odds API at request time.
    Called when a match modal opens — not from cache.
    Returns {home_odds, draw_odds, away_odds, btts_yes, btts_no, bookie}.
    """
    api_key = os.getenv("ODDS_API_KEY", "")
    if not api_key:
        return {}

    from difflib import SequenceMatcher
    def sim(a, b): return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    SPORT_KEYS = [
        "soccer_epl", "soccer_italy_serie_a", "soccer_germany_bundesliga",
        "soccer_spain_la_liga", "soccer_france_ligue_one",
        "soccer_uefa_champs_league", "soccer_uefa_europa_league",
        "soccer_portugal_primeira_liga", "soccer_netherlands_eredivisie",
        "soccer_england_efl_champ", "soccer_germany_bundesliga2",
        "soccer_fifa_world_cup", "soccer_africa_cup_of_nations",
        "soccer_uefa_nations_league", "soccer_conmebol_copa_america",
    ]

    try:
        import httpx as _hx
        async with _hx.AsyncClient(timeout=10) as client:
            for sport_key in SPORT_KEYS:
                r = await client.get(
                    f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/",
                    params={"apiKey": api_key, "regions": "eu",
                            "markets": "h2h,btts", "oddsFormat": "decimal"},
                )
                if r.status_code != 200:
                    continue
                for ev in r.json():
                    h = ev.get("home_team","")
                    a = ev.get("away_team","")
                    if (sim(home, h) + sim(away, a)) / 2 < 0.55:
                        continue
                    # Found the match — extract best odds
                    best: Dict[str, float] = {}
                    btts: Dict[str, float] = {}
                    for bk in (ev.get("bookmakers") or []):
                        for mkt in (bk.get("markets") or []):
                            if mkt["key"] == "h2h":
                                for o in mkt.get("outcomes",[]):
                                    n = o["name"]; p = float(o.get("price",0))
                                    if n == h:    best["1"] = max(best.get("1",0), p)
                                    elif n == a:  best["2"] = max(best.get("2",0), p)
                                    else:         best["X"] = max(best.get("X",0), p)
                            elif mkt["key"] == "btts":
                                for o in mkt.get("outcomes",[]):
                                    if o["name"].lower() == "yes":
                                        btts["yes"] = max(btts.get("yes",0), float(o.get("price",0)))
                                    else:
                                        btts["no"]  = max(btts.get("no",0),  float(o.get("price",0)))
                    if best:
                        print(f"[LiveOdds] {home} vs {away}: 1={best.get('1')} X={best.get('X')} 2={best.get('2')}")
                        return {**best, **btts, "bookie": "market (live)"}
    except Exception as e:
        print(f"[LiveOdds] fetch error: {e}")
    return {}


def _sanitize(obj):
    """Recursively replace NaN/Inf with None so FastAPI can JSON-serialize the response."""
    import math
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: main._sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [main._sanitize(v) for v in obj]
    return obj


def _build_results_index(combined: pd.DataFrame) -> None:
    """The training matches plus every European/cup match collected (not
    only the sets the model trains on), as one table."""
    import club_cups
    import match_facts
    clubs = combined[combined.get("league", pd.Series("", index=combined.index)) != intl.LEAGUE_CODE]
    names = set(clubs["HomeTeam"].dropna()) | set(clubs["AwayTeam"].dropna())
    cups = main._club_cup_rows(names, club_cups.EUROPE_CODES | club_cups.CUP_CODES)
    # The Champions League CSVs carry no competition (named rows win duplicates)
    ucl = main._load_ucl_csv()
    if not ucl.empty:
        ucl = TeamResolver(names, aliases=UCL_ALIASES).resolve_frame(ucl).assign(league="CL")
    extra = main._load_extra_leagues(names)      # every extra league, for their clubs' last five
    main._results_idx = match_facts.index([match_facts.frame(ucl), match_facts.frame(combined),
                                      match_facts.frame(cups), match_facts.frame(extra)])
    main._facts_names[0] = TeamResolver(set(main._results_idx["HomeTeam"]) | set(main._results_idx["AwayTeam"]),
                                   aliases=UCL_ALIASES)
    print(f"[Pipeline] Results index: {len(main._results_idx)} matches.")


def _recent_results_frame() -> pd.DataFrame:
    """Finished matches from the last RECENT_MD_DAYS match days (the site's
    live scores; newer than the CSVs), in the model's names. Cached 10 min."""
    import match_facts
    import matchday
    if main._recent_md[1] is not None and time.time() - main._recent_md[0] < 600:
        return main._recent_md[1]
    rows = []
    today = date.today()
    for o in range(main.RECENT_MD_DAYS + 1):
        d = (today - timedelta(days=o)).isoformat()
        for e in main._md_day_view(d).values():
            res = e.get("result") or {}
            if res.get("status") != matchday.FINISHED or res.get("hg") is None or res.get("ag") is None:
                continue
            if e.get("sport") not in (None, "football"):
                continue
            canon = main._predictor.canon if main._predictor is not None else (lambda n: n)
            # The live feed's stats too, so a team's averages include the match just played
            stats = {}
            for key, (hc, ac) in (("corners", ("HC", "AC")), ("bookings", ("HB", "AB")),
                                  ("shots", ("HS", "AS")), ("sot", ("HST", "AST"))):
                pair = res.get(key)
                if isinstance(pair, (list, tuple)) and len(pair) == 2 and None not in pair:
                    stats[hc], stats[ac] = pair
            rows.append({"Date": e.get("date") or d, "HomeTeam": canon(e.get("home", "")),
                         "AwayTeam": canon(e.get("away", "")), "FTHG": res["hg"], "FTAG": res["ag"],
                         "comp": e.get("league_name") or match_facts.comp_name(e.get("league")), **stats})
    df = match_facts.index([pd.DataFrame(rows)]) if rows else pd.DataFrame(columns=match_facts.COLUMNS)
    main._recent_md = (time.time(), df)
    return df


def _team_results(teams: set) -> pd.DataFrame:
    import match_facts
    base = main._results_idx
    if base is None and main._history_df is not None:
        base = match_facts.frame(main._history_df)
    parts = []
    try:
        rec = main._recent_results_frame()
        parts.append(rec[rec["HomeTeam"].isin(teams) | rec["AwayTeam"].isin(teams)])
    except Exception as e:
        print(f"[Facts] Recent results unavailable: {e}")
    if base is not None and not base.empty:
        parts.append(base[base["HomeTeam"].isin(teams) | base["AwayTeam"].isin(teams)])
    return match_facts.index(parts)


async def _facts_for(home: str, away: str, day: str = "") -> Dict[str, Any]:
    import match_facts
    resolver = main._facts_names[0]
    if resolver is not None:
        h, a = resolver.resolve(home), resolver.resolve(away)
    elif main._predictor is not None:
        h, a = main._predictor.canon(home), main._predictor.canon(away)
    else:
        h, a = home, away
    idx = await asyncio.to_thread(main._team_results, {h, a})
    return match_facts.facts(idx, h, a, day or None, stats_for=main._intl_stats_for)


@router.get("/api/match/facts")
async def get_match_facts(home: str, away: str, day: str = Query("", alias="date")):
    """Each side's last five results and the last five meetings between them
    (empty when they've never met), before the match date."""
    day = day or main._fixture_of(home, away).get("date", "")
    return main._sanitize(await main._facts_for(home, away, day))


def _fixture_of(home: str, away: str, day: str = "") -> Dict[str, Any]:
    """The prediction for a fixture (date, time, league, odds, referee), or {}."""
    h, a = home.lower(), away.lower()
    found = [p for p in main._predictions_cache
             if p.get("home", "").lower() == h and p.get("away", "").lower() == a]
    return next((p for p in found if not day or p.get("date") == day), found[0] if found else {})


def _match_timing(home: str, away: str, day: str = "") -> Tuple[Dict[str, Any], str, Optional[datetime]]:
    """(fixture, cache key, kick-off)."""
    import matchday
    fx = main._fixture_of(home, away, day)
    day = day or fx.get("date", "")
    ko = matchday.kickoff({**fx, "date": day}) if day else None
    return fx, match_cache.match_key(home, away, day), ko


async def _match_news(home: str, away: str, day: str = "") -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """The fixture's current team news (dated, last week only) and the
    model adjustments read from it, shared by every visitor and refreshed
    more often as kick-off nears."""
    import llm_service
    fx, key, ko = main._match_timing(home, away, day)

    async def build():
        news = await llm_service.fetch_match_news(home, away, day or fx.get("date", ""),
                                                  fx.get("league_name", ""))
        news["adjustments"] = (await llm_service.extract_model_adjustments(home, away, news["text"])
                               if news["text"] else {})
        if not news["searched"] and llm_service.GROQ_API_KEY:
            news["_fresh_for"] = 600   # the search failed: try again soon
        return news

    return await main._mcache.get("news", key, build, fp="v1",
                             fresh_for=match_cache.news_fresh_for(ko), ttl=match_cache.ttl_seconds(ko))


async def _analysis_cached(home: str, away: str, day: str = "") -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """The full market breakdown, rebuilt when the model, the odds or the
    team news change (the visitor meanwhile gets the previous one)."""
    fx, key, ko = main._match_timing(home, away, day)
    news, n_info = await main._match_news(home, away, day)
    fp = match_cache.fingerprint(id(main._model_for(fx)), n_info.get("at"), fx.get("odds_home"), fx.get("odds_draw"),
                                 fx.get("odds_away"), (fx.get("referee") or {}).get("name"))
    return await main._mcache.get("analysis", key, lambda: main._build_analysis(home, away, fx, news),
                             fp=fp, fresh_for=3600, ttl=match_cache.ttl_seconds(ko))


@router.get("/api/analysis")
async def get_match_analysis(home: str, away: str, day: str = Query("", alias="date"),
                             _access=Depends(main.require_feature("match_analysis"))):
    if main._predictor is None:
        raise HTTPException(status_code=503, detail="Model not ready yet")
    result, info = await main._analysis_cached(home, away, day)
    if result is None:
        raise HTTPException(status_code=404, detail="Could not generate analysis")
    return {**result, "updated_at": info["at"], "refreshing": info["refreshing"]}


async def _build_analysis(home: str, away: str, cached_fx: Dict[str, Any], news: Dict[str, Any]) -> Optional[Dict]:
    P = main._model_for(cached_fx)   # the Europe model for European fixtures, when adopted
    if P is None:
        return None
    fx_date = cached_fx.get("date", "")

    # Fetch LIVE odds right now from The Odds API
    live_odds = await main._fetch_live_odds(home, away, fx_date)

    # Model adjustments read from the current team news (_match_news)
    news_sources = news.get("sources") or []
    adjustments = news.get("adjustments") or {}

    # Apply web-search adjustments to xG before running the model
    # This makes injury news actually move the prediction numbers
    adj_xg_h = 1.0 + adjustments.get("home_attack_modifier", 0.0)
    adj_xg_a = 1.0 + adjustments.get("away_attack_modifier", 0.0)
    adj_def_h = 1.0 + adjustments.get("home_defense_modifier", 0.0)
    adj_def_a = 1.0 + adjustments.get("away_defense_modifier", 0.0)
    # The model's names for these teams (display names stay as given)
    h_key, a_key = P.canon(home), P.canon(away)
    if adjustments:
        P._init(h_key)
        P._init(a_key)
        # Temporarily scale goal lists so Dixon-Coles xG reflects the news adjustment.
        # We scale both overall gf and the venue-specific home_gf/away_gf lists.
        orig_home_gf      = P.team_stats[h_key].get("gf", [])
        orig_home_gf_home = P.team_stats[h_key].get("home_gf", [])
        orig_away_gf      = P.team_stats[a_key].get("gf", [])
        orig_away_gf_away = P.team_stats[a_key].get("away_gf", [])
        if adj_xg_h != 1.0:
            if orig_home_gf:
                P.team_stats[h_key]["gf"]      = [v * adj_xg_h for v in orig_home_gf]
            if orig_home_gf_home:
                P.team_stats[h_key]["home_gf"] = [v * adj_xg_h for v in orig_home_gf_home]
        if adj_xg_a != 1.0:
            if orig_away_gf:
                P.team_stats[a_key]["gf"]      = [v * adj_xg_a for v in orig_away_gf]
            if orig_away_gf_away:
                P.team_stats[a_key]["away_gf"] = [v * adj_xg_a for v in orig_away_gf_away]

    # If live odds available, re-run prediction with them for better accuracy
    if live_odds:
        result = P.predict_match_full(
            home, away,
            odds_home=live_odds.get("1", 0),
            odds_draw=live_odds.get("X", 0),
            odds_away=live_odds.get("2", 0),
            league=cached_fx.get("model_league") or cached_fx.get("league", ""),
        )
    else:
        result = P.predict_match_full(home, away, league=cached_fx.get("model_league") or cached_fx.get("league", ""))

    # Restore original stats after prediction (don't permanently alter training data)
    if adjustments:
        if adj_xg_h != 1.0:
            P.team_stats[h_key]["gf"]      = orig_home_gf
            P.team_stats[h_key]["home_gf"] = orig_home_gf_home
        if adj_xg_a != 1.0:
            P.team_stats[a_key]["gf"]      = orig_away_gf
            P.team_stats[a_key]["away_gf"] = orig_away_gf_away

    # Apply confidence modifier from web search
    conf_mod = adjustments.get("confidence_modifier", 0.0)
    if conf_mod and result:
        result["web_confidence_modifier"] = conf_mod
        result["web_adjustment_flags"] = adjustments.get("flags", [])
        result["web_adjustment_reason"] = adjustments.get("reasoning", "")
        result["web_news_sources"] = news_sources

    if result is None:
        return None

    # Tag live odds onto 1X2 market options
    if live_odds:
        for mkt in result["markets"]:
            if mkt["id"] == "1x2":
                for opt in mkt["options"]:
                    if opt["code"] == "1" and live_odds.get("1"):
                        opt["odds"] = str(live_odds["1"]); opt["bookie"] = live_odds.get("bookie","")
                    elif opt["code"] == "X" and live_odds.get("X"):
                        opt["odds"] = str(live_odds["X"]); opt["bookie"] = live_odds.get("bookie","")
                    elif opt["code"] == "2" and live_odds.get("2"):
                        opt["odds"] = str(live_odds["2"]); opt["bookie"] = live_odds.get("bookie","")
            elif mkt["id"] == "btts":
                for opt in mkt["options"]:
                    if opt["code"] == "BTTS-Y" and live_odds.get("yes"):
                        opt["odds"] = str(live_odds["yes"]); opt["bookie"] = live_odds.get("bookie","")
                    elif opt["code"] == "BTTS-N" and live_odds.get("no"):
                        opt["odds"] = str(live_odds["no"]); opt["bookie"] = live_odds.get("bookie","")
        result["live_odds_fetched"] = True
        result["odds_bookie"] = live_odds.get("bookie", "")

    # Show team form stats in the response
    result["team_form"] = {
        "home": main._predictor_form_summary(home, P),
        "away": main._predictor_form_summary(away, P),
    }

    # Inject cards + corners markets if data is available
    extra = main._predictor.predict_cards(home, away, main._cards_df, main._corners_df)
    for market_id in ("cards", "corners", "corners_race"):
        if market_id in extra:
            result["markets"].append(extra[market_id])

    # Blend H2H win rates into model probabilities if cached data exists
    key = main._h2h_cache_key(home, away)
    cached = main._h2h_cache.get(key)
    if cached and cached.get("summary") and cached["summary"]["total"] >= 3:
        s = cached["summary"]
        n = s["total"]
        h2h_ph = s["home_wins"] / n
        h2h_pd = s["draws"] / n
        h2h_pa = s["away_wins"] / n
        W = 0.15  # 15% weight on H2H, 85% on XGBoost
        for mkt in result["markets"]:
            if mkt["id"] == "1x2":
                for opt in mkt["options"]:
                    if opt["code"] == "1":
                        opt["prob"] = round((1 - W) * opt["prob"] + W * h2h_ph, 3)
                    elif opt["code"] == "X":
                        opt["prob"] = round((1 - W) * opt["prob"] + W * h2h_pd, 3)
                    elif opt["code"] == "2":
                        opt["prob"] = round((1 - W) * opt["prob"] + W * h2h_pa, 3)
        result["h2h_blended"] = True
        result["h2h_matches_used"] = n

    # Inject live SportyBet odds into each market (best-effort)
    # SportyBet market IDs confirmed from network capture:
    #   1=1X2, 10=Double Chance, 11=Draw No Bet, 18=O/U Goals,
    #   29=GG/NG(BTTS), 36=Result+BTTS combo
    CODE_TO_SB: dict = {
        # 1X2
        "1":       ("1",  "home"),           "X":       ("1",  "draw"),
        "2":       ("1",  "away"),
        # Double Chance
        "1X":      ("10", "home or draw"),   "X2":      ("10", "draw or away"),
        "12":      ("10", "home or away"),
        # Draw No Bet
        "DNB-H":   ("11", "home"),           "DNB-A":   ("11", "away"),
        # BTTS
        "BTTS-Y":  ("29", "yes"),            "BTTS-N":  ("29", "no"),
        # Result + BTTS (market 36, specifier total=2.5 based on network capture)
        "RB-H-Y":  ("36", "home win & yes"), "RB-D-Y":  ("36", "draw & yes"),
        "RB-A-Y":  ("36", "away win & yes"),
        "RB-H-N":  ("36", "home win & no"),  "RB-D-N":  ("36", "draw & no"),
        "RB-A-N":  ("36", "away win & no"),
    }

    try:
        from sportybet import fetch_events_for_date, find_event
        from datetime import date as _date
        today = _date.today().isoformat()
        sb_events = await fetch_events_for_date(today)
        sb_event  = find_event(home, away, sb_events) if sb_events else None
        if sb_event:
            odds_lookup: dict = {}
            for m in (sb_event.get("markets") or []):
                mid = str(m.get("id",""))
                spec = str(m.get("specifier","") or "")
                for o in (m.get("outcomes") or []):
                    desc = (o.get("desc") or "").lower().strip()
                    # Key with and without specifier
                    odds_lookup[f"{mid}:{desc}"] = str(o.get("odds",""))
                    if spec:
                        odds_lookup[f"{mid}:{spec}:{desc}"] = str(o.get("odds",""))

            for mkt in result["markets"]:
                for opt in mkt.get("options", []):
                    code = opt.get("code","")
                    sb = CODE_TO_SB.get(code)
                    if sb:
                        odds_val = odds_lookup.get(f"{sb[0]}:{sb[1]}")
                        if odds_val and float(odds_val) > 1:
                            opt["odds"] = odds_val
                            opt["bookie"] = "SportyBet"
    except Exception:
        pass

    # Also try The Odds API for markets SportyBet doesn't cover (Win to Nil, Clean Sheet)
    try:
        if os.getenv("ODDS_API_KEY"):
            import httpx as _hx
            # Find the prediction with odds already fetched for this match
            cached_odds = next(
                (p for p in main._predictions_cache
                 if p.get("home","").lower() == home.lower()
                 and p.get("away","").lower() == away.lower()),
                {}
            )
            # Inject pre-fetched odds from prediction cache onto 1x2 options
            o1 = cached_odds.get("odds_home")
            ox = cached_odds.get("odds_draw")
            o2 = cached_odds.get("odds_away")
            if o1 and ox and o2:
                for mkt in result["markets"]:
                    if mkt["id"] == "1x2":
                        for opt in mkt["options"]:
                            if opt["code"] == "1" and not opt.get("odds"):
                                opt["odds"] = str(o1); opt["bookie"] = "market"
                            elif opt["code"] == "X" and not opt.get("odds"):
                                opt["odds"] = str(ox); opt["bookie"] = "market"
                            elif opt["code"] == "2" and not opt.get("odds"):
                                opt["odds"] = str(o2); opt["bookie"] = "market"
    except Exception:
        pass

    return main._sanitize(result)


@router.get("/api/h2h")
async def get_h2h(home: str, away: str):
    key = main._h2h_cache_key(home, away)

    # Return fresh cache if available
    if key in main._h2h_cache and main._h2h_is_fresh(main._h2h_cache[key]):
        return main._h2h_cache[key]

    # Try live API using match_id from predictions cache
    match_id = next(
        (p.get("match_id") for p in main._predictions_cache
         if p.get("home", "").lower() == home.lower()
         and p.get("away", "").lower() == away.lower()),
        None
    )

    if match_id and main.API_KEY:
        try:
            client = FootballDataClient(main.API_KEY)
            raw = await client.fetch_h2h(match_id, limit=20)
            print(f"[H2H] API response for match {match_id}: keys={list(raw.keys()) if raw else None}, matches={len(raw.get('matches', [])) if raw else 0}")
            if raw is not None:
                parsed = main._parse_api_h2h(raw, home, away)
                main._h2h_cache[key] = parsed
                main._save_h2h_cache()
                print(f"[H2H] Stored {parsed['summary']['total']} meetings for {home} vs {away} (source=api)")
                # If API gave us data (even 0 meetings), return it — don't fall to CSV
                if parsed["summary"]["total"] > 0 or len(raw.get("matches", [])) == 0:
                    return parsed
        except Exception as e:
            print(f"[H2H] API fetch failed for match {match_id}: {e}")
    else:
        print(f"[H2H] No match_id found for {home} vs {away} (cache size={len(main._predictions_cache)})")

    # Fall back to CSV history
    csv_result = main._parse_csv_h2h(home, away)
    if csv_result["meetings"]:
        main._h2h_cache[key] = csv_result
        main._save_h2h_cache()

    return csv_result


def _sim_name(a: str, b: str) -> bool:
    from difflib import SequenceMatcher
    def n(s): return s.lower().replace(" fc","").replace(" united"," utd").strip()
    return SequenceMatcher(None, n(a), n(b)).ratio() >= 0.6


@router.get("/api/admin/sportybet-check")
async def sportybet_check(_admin: str = Depends(main.require_admin)):
    """Book a real one-pick SportyBet code from this server and report each step."""
    import sportybet
    today = date.today().isoformat()
    upcoming = [{"home": p.get("home"), "away": p.get("away")} for p in main._predictions_cache
                if p.get("date", "") >= today and p.get("sport") in (None, "football")]
    return await sportybet.diagnose(upcoming)


@router.get("/api/admin/sportybet-link")
async def sportybet_link_now(_admin: str = Depends(main.require_admin)):
    """Match upcoming predictions to SportyBet events now (normally every 30 min)."""
    main._audit(_admin, "sportybet_link")
    return await main._link_sportybet_events("manual")


@router.get("/api/admin/sportybet-link-status")
async def sportybet_link_status(_admin: str = Depends(main.require_admin)):
    """The last linking run's full result (the admin panel polls this)."""
    main._restore_link_status()
    return main._sb_link_status


@router.get("/api/admin/international-check")
async def international_check(_admin: str = Depends(main.require_admin)):
    """Fetch international fixtures now, report what each source returned,
    and publish them without waiting for the next pipeline run."""
    main._audit(_admin, "international_check")
    fixtures = await main._fetch_international_fixtures()
    published = 0
    if fixtures and main._predictor is not None:
        cached_odds, _ = main._load_cached_live_odds()
        fresh = main._build_predictions(main._predictor, fixtures, cached_odds or {})
        if fresh:
            main._predictions_cache = [p for p in main._predictions_cache
                                  if not intl.is_international(p.get("league", ""))] + fresh
            main._last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            main._save_predictions_cache()
            published = len(fresh)
            asyncio.create_task(main._link_sportybet_events("international"))
    by_competition: Dict[str, int] = {}
    for f in fixtures:
        by_competition[f["league_name"]] = by_competition.get(f["league_name"], 0) + 1
    return {"fixtures": len(fixtures), "published": published, "model_ready": main._predictor is not None,
            "by_competition": by_competition, "sources": main._intl_status["sources"],
            "errors": main._intl_status["errors"]}


def _shared_model_status() -> Optional[Dict[str, Any]]:
    """The model published for this server (model_store), if any."""
    try:
        import model_store
        from predictor import MODEL_CACHE_VERSION
        meta = model_store.describe(MODEL_CACHE_VERSION)
    except Exception:
        return None
    if not meta:
        return None
    return {"source": meta.get("source"), "rows": meta.get("rows"), "size": meta.get("size"),
            "trained_at": datetime.fromtimestamp(meta["trained_at"], timezone.utc).isoformat(),
            "max_age_hours": model_store.MAX_AGE_HOURS}


@router.get("/api/admin/data-status")
async def data_status(_admin: str = Depends(main.require_admin)):
    """How fresh the training data is, and which upcoming teams the model
    knows little or nothing about (a name it couldn't match, or a new club)."""
    leagues: Dict[str, Dict[str, Any]] = {}
    for path in sorted(glob.glob(os.path.join(main.FOOTBALL_DATA_DIR, "*.csv"))):
        div = os.path.basename(path).split("_")[0]
        try:
            dates = pd.to_datetime(pd.read_csv(path, usecols=["Date"])["Date"], dayfirst=True, errors="coerce")
            latest = dates.max()
        except Exception:
            continue
        if pd.notna(latest) and (div not in leagues or str(latest.date()) > leagues[div]["latest_match"]):
            leagues[div] = {"latest_match": str(latest.date()), "file": os.path.basename(path)}

    teams: Dict[str, Dict[str, Any]] = {}
    if main._predictor is not None:
        for p in main._predictions_cache:
            if p.get("sport") not in (None, "football"):
                continue
            for name in (p.get("home"), p.get("away")):
                if not name or name in teams:
                    continue
                key = main._predictor.canon(name)
                teams[name] = {"model_name": key, "league": p.get("league", ""),
                               "matches": len(main._predictor.team_stats.get(key, {}).get("pts", []))}
    thin = sorted(({"team": k, **v} for k, v in teams.items() if v["matches"] < 5),
                  key=lambda t: (t["matches"], t["team"]))
    last = main._football_sync["at"]
    return {
        "leagues": leagues,
        "last_sync": {"at": last.isoformat() if last else None, "report": main._football_sync["report"]},
        "teams_checked": len(teams),
        "renamed": {k: v["model_name"] for k, v in sorted(teams.items()) if v["model_name"] != k},
        "thin_history": thin,
        "international": {**main._intl_status, "at": main._intl_status["at"].isoformat() if main._intl_status["at"] else None},
        "unknown_clubs": dict(sorted(main._unknown_clubs.items(), key=lambda kv: kv[1])),
        "europa_league": {**main._europe_status, "europe_model": {k: v for k, v in main._europe_model_info.items()
                                                            if k != "recent_counts"}},
        "shared_model": main._shared_model_status(),
        "sportybet_links": main._sb_link_status,
        "international_set_pieces": {**main._intl_sp_info, "active": main._intl_set_pieces is not None,
                                     "shots_active": main._intl_shots is not None},
        "shots": {**main._shots_info, "active": main._shots is not None},
        "shots_blend": main._shot_blend,
        "learning": main._learn_status,
        "referees": main._referee_status(),
        "matchday": main._md_status,
        "club_cups": main._club_cups_status(),
    }


@router.get("/api/admin/model-metrics")
async def model_metrics(_admin: str = Depends(main.require_admin)):
    """Walk-forward backtest results (generated offline by backtest.py). Admin only."""
    from backtest import METRICS_PATH
    try:
        with open(METRICS_PATH) as f:
            return {"available": True, **json.load(f)}
    except (OSError, ValueError):
        return {"available": False}
