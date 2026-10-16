"""
The prediction pipeline, results fetching, FBref data and traffic flushes.

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import international_fixtures as intl
import os
import pandas as pd
import set_pieces
from data_fetcher import FootballDataClient, LEAGUES, not_in_plan
from datetime import datetime
from scrapers.fbref import CORNERS_CSV, load_cards, load_corners, refresh as scrape_fbref
from typing import Any, Dict, List

import main


async def _manual_football_sync() -> None:
    """Admin job: download the league files now, and rebuild if any changed."""
    report = await main._sync_football_data(force=True)
    if report and report["updated"] and not main._is_training:
        await main._run_pipeline()


async def _run_pipeline():

    if main._is_training:
        return
    main._is_training = True

    try:
        await main._sync_football_data()
        assembled = await asyncio.to_thread(main._assemble_training_data)
        if assembled is None:
            return
        history, combined, data_mtime = assembled
        main._history_df = history  # keep for H2H lookups
        try:
            await asyncio.to_thread(main._build_results_index, combined)
        except Exception as e:
            print(f"[Pipeline] Results index failed (non-fatal): {e}")
        try:
            # Per-league settings where a league clearly beat the defaults (tune_football.py)
            sp_tuned = main._football_tuning().get("set_pieces") or {}
            main._set_pieces = await asyncio.to_thread(
                lambda: set_pieces.SetPieceModel.fit(main._with_club_referees(history),
                                                     {**set_pieces.CLUB_GUARDS, "by_league": sp_tuned}))
        except Exception as e:
            print(f"[Pipeline] Corners/bookings model failed (non-fatal): {e}")
        try:
            await asyncio.to_thread(main._load_international_set_pieces)
        except Exception as e:
            print(f"[Pipeline] International corners/bookings model failed (non-fatal): {e}")
        try:
            await asyncio.to_thread(main._fit_shots, history)
        except Exception as e:
            print(f"[Pipeline] Shots model failed (non-fatal): {e}")
        # Fresh fits: they hold the history's matches (live_learning.py)
        import live_learning
        live_learning.mark_trained(main._set_pieces, history)
        live_learning.mark_trained(main._shots, history)
        print(f"[Pipeline] {len(combined)} training matches.")

        # Training takes minutes of CPU. On a worker thread the API keeps
        # answering (from the previous model) instead of timing out.
        print(f"[Memory] {main._rss_mb()} MB before loading the model")
        predictor = await asyncio.to_thread(main._load_or_train, combined, data_mtime)
        print(f"[Memory] {main._rss_mb()} MB with the model")

        # Make predictor available immediately so card analysis works during API calibration
        # A newly loaded model holds its training matches; the one already
        # running (kept when no fresh model was found) also holds every match
        # fed in since, so its memory stays
        if getattr(predictor, "_applied_keys", None) is None or predictor is not main._predictor:
            live_learning.mark_trained(predictor, combined, predictor.canon)
        main._predictor = predictor
        print("[Pipeline] Predictor ready — card analysis now available.")
        # Then every match finished since the training data was made
        try:
            learned = await asyncio.to_thread(main._learn_finished_matches, main.LEARN_DAYS)
            if learned:
                print(f"[Pipeline] Learned {learned['matches']} finished matches the models didn't hold.")
        except Exception as e:
            print(f"[Pipeline] Learning recent results failed (non-fatal): {e}")

        # Archive yesterday's predictions from the OLD cache before we start
        # overwriting it below.

        predictions = []
        fixtures: list = []  # pre-init so the block below is safe when API_KEY is unset

        # ── International fixtures (ESPN + The Odds API, no football-data key needed) ──
        # First, so an international break shows up within seconds.
        fixtures.extend(await main._fetch_international_fixtures())
        if fixtures:
            await asyncio.to_thread(main._cache_team_crests, fixtures)
            predictions = await asyncio.to_thread(main._build_predictions, predictor, fixtures, {})
            main._predictions_cache = predictions
            main._last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            await asyncio.to_thread(main._save_predictions_cache)
            print(f"[Pipeline] +INT: {len(fixtures)} international fixtures — "
                  f"{len(predictions)} predictions published.")

        # ── Europa and Conference League (ESPN; not in football-data.org's free plan) ──
        await asyncio.to_thread(main._load_europe_model)
        europe = await main._fetch_europe_fixtures(predictor, combined)
        if europe:
            fixtures.extend(europe)
            await asyncio.to_thread(main._cache_team_crests, europe)
            predictions = await asyncio.to_thread(main._build_predictions, predictor, fixtures, {})
            main._predictions_cache = predictions
            main._last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            await asyncio.to_thread(main._save_predictions_cache)
            print(f"[Pipeline] +EL/UECL: {len(europe)} Europa/Conference League fixtures published.")

        # ── More European leagues (ESPN fixtures; league_fixtures.py), and
        # the top flights only SportyBet covers (the linker's catalog; sb_league_fixtures.py) ──
        if not main._sb_fixtures:
            await asyncio.to_thread(main._load_sb_fixtures)
        more = await main._fetch_more_leagues()
        more += [fx for fx in main._sb_fixtures if main._within_window(fx.get("date", ""))]
        if more:
            fixtures.extend(more)
            await asyncio.to_thread(main._cache_team_crests, more)
            predictions = await asyncio.to_thread(main._build_predictions, predictor, fixtures, {})
            main._predictions_cache = predictions
            main._last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            await asyncio.to_thread(main._save_predictions_cache)
            print(f"[Pipeline] +{len(more)} fixtures from Belgium, Scotland, Greece, Turkey and the rest "
                  f"({len(main._unproven_leagues)} leagues still unproven).")

        if main.API_KEY:
            client = FootballDataClient(main.API_KEY)

            # ── Fetch + publish per league, incrementally ──────────────────────
            # football-data.org's free tier is rate-limited, so fetching all 12
            # leagues takes ~2 minutes minimum (10s pacing per league) and can take
            # much longer under 429 backoff. Waiting for all 12 to finish before
            # showing anything means the app looks broken for minutes at a time,
            # and on a free host that can sleep mid-run, a stall anywhere in the
            # loop means NONE of it ever gets served. Instead: publish predictions
            # after each league's fixtures come in, so the first league (World Cup)
            # is visible within seconds, and every completed league survives even
            # if a later one stalls or the run never finishes.
            print("[Pipeline] Fetching upcoming fixtures (publishing after each league)...")
            for code in list(LEAGUES.keys()):
                if not_in_plan(code):
                    continue
                try:
                    league_fixtures = await client.fetch_upcoming(code, days_ahead=main.PREDICTION_DAYS)
                except Exception as e:
                    print(f"[Pipeline] Fixture fetch error for {code}: {e}")
                    league_fixtures = []
                if league_fixtures:
                    fixtures.extend(league_fixtures)
                    # Team crests ride along for free on the fixtures response
                    # (no extra API call) — cache them for /api/team-logo.
                    await asyncio.to_thread(main._cache_team_crests, league_fixtures)
                    # No live odds yet on this fast pass — predict_match() falls back
                    # to league-average implied probabilities, which is fine for an
                    # initial publish; the odds pass below refines it.
                    predictions = await asyncio.to_thread(main._build_predictions, predictor, fixtures, {})
                    main._predictor = predictor
                    main._predictions_cache = predictions
                    main._last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                    await asyncio.to_thread(main._save_predictions_cache)
                    print(f"[Pipeline] +{code}: {len(league_fixtures)} fixtures — "
                          f"{len(predictions)} predictions published so far.")
                # Backfill this league's badge only if we don't already have a
                # confirmed one cached — a one-time cost per league, not worth
                # paying every run, and this is also what self-heals a
                # previously rate-limited/failed lookup (see
                # _has_cached_competition_emblem).
                if not await asyncio.to_thread(main._has_cached_competition_emblem, code):
                    try:
                        emblem = await client.fetch_competition_emblem(code)
                        await asyncio.to_thread(main._cache_competition_emblem, code, emblem)
                    except Exception as e:
                        print(f"[Pipeline] Emblem fetch failed for {code}: {e}")
                await asyncio.sleep(10)  # respect football-data.org rate limit

            # ── Live odds pass — improves accuracy + enables value-bet detection ──
            live_odds: dict = {}
            if fixtures:
                try:
                    stubs = [{"home": fx["home"], "away": fx["away"],
                              "date": fx.get("date",""), "league_name": fx.get("league_name",""),
                              **({"odds_sport": fx.get("odds_sport")} if "odds_sport" in fx else {})}
                             for fx in fixtures]
                    live_odds = await main._get_live_odds_throttled(stubs)
                    print(f"[Pipeline] Got live odds for {len(live_odds)}/{len(fixtures)} fixtures")
                except Exception as e:
                    print(f"[Pipeline] Live odds fetch error (non-fatal): {e}")

                predictions = await asyncio.to_thread(main._build_predictions, predictor, fixtures, live_odds)
                main._predictions_cache = predictions
                main._last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                await asyncio.to_thread(main._save_predictions_cache)
                print(f"[Pipeline] Republished {len(predictions)} predictions (with live odds).")
                if main._unknown_clubs:
                    print(f"[Pipeline] {len(main._unknown_clubs)} fixture clubs the model has no matches for: "
                          + ", ".join(f"{n} ({lg})" for n, lg in sorted(main._unknown_clubs.items(), key=lambda kv: kv[1])))

            # ── Recent-results Elo calibration (refinement) ───────────────────
            print("[Pipeline] Fetching recent API results to calibrate Elo...")
            calibrated = False
            for code in list(LEAGUES.keys()):
                if not_in_plan(code):
                    continue
                try:
                    recent = await client.fetch_recent_results(code, days_back=60)
                    if recent.empty or "HomeTeam" not in recent.columns:
                        await asyncio.sleep(6)  # still pace requests even on empty results
                        continue
                    for _, r in recent.iterrows():
                        predictor._update(r["HomeTeam"], r["AwayTeam"], r["Result"], r["FTHG"], r["FTAG"],
                                          competition=code)
                    calibrated = True
                    await asyncio.sleep(10)
                except Exception as e:
                    print(f"[Pipeline] Recent results error for {code}: {e}")

            # Re-predict with the calibrated Elo and republish.
            if calibrated and fixtures:
                predictions = await asyncio.to_thread(main._build_predictions, predictor, fixtures, live_odds)
                main._predictions_cache = predictions
                main._last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                await asyncio.to_thread(main._save_predictions_cache)
                print(f"[Pipeline] Republished {len(predictions)} predictions (calibrated).")
        else:
            print("[Pipeline] WARNING: No FOOTBALL_DATA_API_KEY set. Add your key to .env to get live fixtures.")

        # Pre-fetch web form for teams that have sparse local data
        # Runs as a background task so it doesn't block the pipeline
        if fixtures:
            asyncio.create_task(main._prefetch_web_forms(predictor, fixtures))

        # Fetch Understat xG in background (updates Redis cache for European clubs)
        asyncio.create_task(main._refresh_understat_xg())

        main._predictor = predictor
        print(f"[Pipeline] Done — {len(predictions)} predictions cached.")
        asyncio.create_task(main._link_sportybet_events("pipeline"))
        asyncio.create_task(main._refresh_referees("pipeline"))

        # Send push notifications for high-value picks
        value_picks = [p for p in predictions if p.get("is_value_bet") and p.get("value_edge", 0) > 0.08]
        if value_picks:
            asyncio.create_task(main._send_push_notifications(value_picks))

    except Exception as e:
        print(f"[Pipeline] Fatal error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        main._is_training = False


async def _fetch_and_save_results():
    """
    Fetch finished match results from football-data.org for the past 30 days,
    append to results CSV, and apply to the live model's Elo/form state so
    predictions stay fresh between full retrains. International results come
    from ESPN (international_fixtures.py) and need no key.
    """
    print("[Results] Fetching recent finished results...")
    all_rows: List[pd.DataFrame] = []
    try:
        intl_report = await intl.fetch_international(days_ahead=0, days_back=10)
        if intl_report["results"]:
            all_rows.append(pd.DataFrame(intl_report["results"]).assign(Date=lambda d: pd.to_datetime(d["Date"])))
            print(f"[Results] {len(intl_report['results'])} international results.")
    except Exception as e:
        print(f"[Results] International results error: {e}")

    client = FootballDataClient(main.API_KEY) if main.API_KEY else None
    for code in (LEAGUES if client else []):
        if not_in_plan(code):
            continue
        try:
            df = await client.fetch_recent_results(code, days_back=30)
            if not df.empty:
                df["league"] = code
                all_rows.append(df)
            await asyncio.sleep(10)
        except Exception as e:
            print(f"[Results] Error fetching {code}: {e}")

    if not all_rows:
        print("[Results] No results returned.")
        return

    new_df = pd.concat(all_rows, ignore_index=True)
    os.makedirs("data", exist_ok=True)

    if os.path.exists(main.RESULTS_CSV):
        existing = pd.read_csv(main.RESULTS_CSV, parse_dates=["Date"])
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["Date", "HomeTeam", "AwayTeam"])
        combined = combined.sort_values("Date").reset_index(drop=True)
    else:
        combined = new_df

    combined.to_csv(main.RESULTS_CSV, index=False)
    print(f"[Results] {len(combined)} results saved to {main.RESULTS_CSV}")

    # New results into the live model, each match once (live_learning.py):
    # most of the last 30 days are already in its training data or were
    # learned from the live scores, and must not count again
    if main._predictor is not None:
        import live_learning
        moved: set = set()
        for r in new_df.to_dict("records"):
            row = live_learning.result_row(r)
            if not row:
                continue
            try:
                moved |= live_learning.learn(row, predictor=main._predictor,
                                             competition=intl.LEAGUE_CODE if row["league"] == intl.LEAGUE_CODE
                                             else row["league"])
            except Exception as e:
                print(f"[Results] Couldn't learn {row['HomeTeam']} v {row['AwayTeam']}: {e}")
        print(f"[Results] {len(moved) // 2} new result(s) learned by the live model (it already held the rest).")
        if moved:
            main._repredict(moved)

    # Web search fallback: find results for past predictions still marked "pending"
    # that the API didn't return (e.g. international friendlies, cup games)
    await main._web_search_missing_results()


def _flush_traffic() -> int:
    return main._traffic.flush(main._get_redis())


async def _reload_set_pieces() -> Dict[str, Any]:
    """Load the international corners/cards/shots models with the latest
    nightly verdicts and re-price the current predictions — without a full
    rebuild."""
    await asyncio.to_thread(main._load_international_set_pieces)
    with_referee = main._apply_referees()
    return {"international_corners_cards": main._intl_set_pieces is not None,
            "international_shots": main._intl_shots is not None, "club_shots": main._shots is not None,
            "predictions": len(main._predictions_cache), "with_referee": with_referee}


async def _load_fbref_data():
    """Load cards + corners CSVs, rebuilding from EPL CSV if missing or >7 days old."""
    import time as _time

    needs_scrape = True
    if os.path.exists(CORNERS_CSV):
        age_days = (_time.time() - os.path.getmtime(CORNERS_CSV)) / 86400
        if age_days < 7:
            needs_scrape = False

    if needs_scrape:
        print("[fbref] Data stale or missing — building from EPL CSV...")
        try:
            await scrape_fbref(epl_csv_path=main.EPL_HISTORY)
        except Exception as e:
            print(f"[fbref] Build failed: {e}")

    main._cards_df = load_cards()
    main._corners_df = load_corners()
    print(f"[fbref] Loaded cards data ({len(main._cards_df)} teams), corners data ({len(main._corners_df)} teams)")
