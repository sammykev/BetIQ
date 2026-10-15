"""
SportyBet links: predictions matched to SportyBet events ahead of time.

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import international_fixtures as intl
import json
import os
import re
import set_pieces
import shots
import time
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter
from typing import Any, Dict, List, Optional, Tuple

import main

router = APIRouter()


def _link_lock() -> asyncio.Lock:
    """One linking run at a time (a lock belongs to one event loop)."""
    loop = asyncio.get_running_loop()
    if main._sb_link_lock is None or main._sb_link_lock[0] is not loop:
        main._sb_link_lock = (loop, asyncio.Lock())
    return main._sb_link_lock[1]


def _sb_key(home: str, away: str, day: str) -> str:
    return f"{home}|{away}|{day}"


def _kickoff_ms(p: Dict) -> Optional[int]:
    try:
        dt = datetime.strptime(f"{p['date']} {p.get('time') or ''}", "%Y-%m-%d %H:%M")
    except (KeyError, ValueError):
        return None
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _match_predictions_to_events(preds: List[Dict], events: List[Dict],
                                 unlinked: Optional[List[Dict]] = None) -> Dict[str, Dict]:
    """{prediction key: slim SportyBet event}: by both team names, else by
    kick-off time plus one clear name. Unmatched predictions (with the
    closest SportyBet event) go to `unlinked`. CPU-bound: run in a thread."""
    import sportybet
    by_day: Dict[str, List[Dict]] = {}
    for ev in events:
        by_day.setdefault(sportybet._utc_day(ev) or "", []).append(ev)
    links: Dict[str, Dict] = {}

    def near(p: Dict) -> List[Dict]:
        d = date.fromisoformat(p["date"])
        return [ev for k in {(d + timedelta(days=o)).isoformat() for o in (-1, 0, 1)} for ev in by_day.get(k, [])]
    # Both names first, for every prediction; the kick-off pass then only
    # takes events nobody claimed, so one SportyBet match never serves two of ours
    found = {id(p): sportybet.find_event(p["home"], p["away"], near(p)) for p in preds}
    taken = {ev.get("eventId") for ev in found.values() if ev}
    for p in preds:
        ev = found[id(p)]
        kickoff = main._kickoff_ms(p)
        if ev is None and kickoff is not None:
            ev = sportybet.find_event_by_kickoff(p["home"], p["away"], kickoff,
                                                 [e for e in near(p) if e.get("eventId") not in taken])
            if ev:
                taken.add(ev.get("eventId"))
        if ev and ev.get("eventId"):
            links[main._sb_key(p["home"], p["away"], p["date"])] = sportybet.slim_event(ev)
        elif unlinked is not None:
            guess, score = sportybet.closest_event(p["home"], p["away"], near(p))
            unlinked.append({
                "match": f"{p['home']} vs {p['away']}", "date": p["date"], "time": p.get("time") or "",
                "league": p.get("league_name") or p.get("league") or "",
                "closest": f"{guess['homeTeamName']} vs {guess['awayTeamName']}" if guess else None,
                "score": round(score, 2),
            })
    return links


def _catalog_summary(events: List[Dict]) -> Dict[str, Any]:
    """How SportyBet's listing is spread over the coming days, and which
    national-team competitions it carries (to tell "not offered yet" from
    "not fetched")."""
    import sportybet
    days: Dict[str, int] = {}
    tournaments: Dict[str, int] = {}
    for ev in events:
        day = sportybet._utc_day(ev) or "?"
        days[day] = days.get(day, 0) + 1
        name = ev.get("_tournament") or ""
        if name and main._INTL_TOURNAMENT.search(name):
            tournaments[name] = tournaments.get(name, 0) + 1
    return {"days": dict(sorted(days.items())[:21]),
            "international": dict(sorted(tournaments.items(), key=lambda kv: -kv[1])[:20])}


async def _market_map(events: List[Dict], links: Dict[str, Dict], report: List[str]) -> Tuple[Dict, Dict]:
    """(booking_slip.resolve_markets result, {market id: label}): SportyBet's
    markets from the listing plus one linked club match's own page, which
    lists every market with its outcomes."""
    import booking_slip
    import sportybet
    details = sportybet.market_details(events)
    league = {main._sb_key(p.get("home", ""), p.get("away", ""), p.get("date", "")): p.get("league", "")
              for p in main._predictions_cache}
    # The biggest leagues carry the most markets: try those first
    rank = {"PL": 0, "PD": 1, "SA": 2, "BL1": 3, "FL1": 4}
    club = [(key, ev) for key, ev in links.items() if not intl.is_international(league.get(key, ""))]
    club.sort(key=lambda kv: rank.get(league.get(kv[0], ""), 9))
    probes = [ev for _, ev in club[:4]]
    # None linked (an international break): any top-league match SportyBet lists
    top = re.compile(r"premier league|laliga|la liga|serie a|bundesliga|ligue 1", re.I)
    probes += [ev for ev in events if top.search(ev.get("_tournament") or "") and "women" not in (ev.get("_tournament") or "").lower()][:4]
    # Several pages, merged: specials such as shots are only on some matches
    seen, read, from_pages = set(), 0, set()
    for probe in probes:
        if read >= main.MARKET_PAGES or str(probe.get("eventId")) in seen:
            continue
        seen.add(str(probe.get("eventId")))
        try:
            page = await sportybet.event_market_details(str(probe["eventId"]))
            for mid, d in page.items():   # a page's entry beats the listing's; the first page's wins
                if mid not in from_pages:
                    details[mid] = d
                    from_pages.add(mid)
            read += 1
            report.append(f"match page {probe.get('homeTeamName')} v {probe.get('awayTeamName')}: {len(page)} markets")
        except Exception as e:
            report.append(f"match page: {type(e).__name__}: {e}")
    labels = {mid: d["label"] for mid, d in sorted(details.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0)}
    return booking_slip.resolve_markets({"markets": details}), labels


def _market_coverage(links: Dict[str, Dict]) -> Dict[str, int]:
    """How many linked matches carry each market we book, with a price."""
    counts: Dict[str, int] = {}
    for ev in links.values():
        for m in {str(m.get("id")) for m in ev.get("markets") or []}:
            counts[m] = counts.get(m, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0))


def _save_link_status() -> None:
    r = main._get_redis()
    if r:
        try:
            r.set(main.SB_LINK_STATUS_KEY, json.dumps(main._sb_link_status, default=str), ex=3 * 24 * 3600)
        except Exception as e:
            print(f"[SportyBet] Could not save link status: {e}")


def _restore_link_status() -> None:
    """The last run's result (from before this restart), until this server's first run."""
    r = main._get_redis()
    if r and not main._sb_link_status.get("at"):
        try:
            saved = json.loads(r.get(main.SB_LINK_STATUS_KEY) or "{}")
            if saved:
                main._sb_link_status.update(saved)
        except Exception:
            pass


def _sb_market_map() -> Dict[str, Dict[str, Any]]:
    if not main._sb_link_status.get("market_map"):
        main._restore_link_status()
    return main._sb_link_status.get("market_map") or {}


async def _link_sportybet_events_now(trigger: str) -> Dict[str, Any]:
    import sportybet
    today = date.today().isoformat()
    preds = [p for p in main._predictions_cache
             if p.get("sport") in (None, "football") and p.get("home") and p.get("away")
             and today <= p.get("date", "") and main._within_window(p.get("date", ""))]
    have = {main._sb_key(p["home"], p["away"], p.get("date", "")) for p in preds}
    preds += [fx for fx in main._europe_waiting if today <= fx.get("date", "") and main._within_window(fx.get("date", ""))
              and main._sb_key(fx["home"], fx["away"], fx.get("date", "")) not in have]
    started = time.monotonic()
    status: Dict[str, Any] = {"at": datetime.now(timezone.utc).isoformat(), "trigger": trigger,
                              "deploy": (os.getenv("RENDER_GIT_COMMIT") or "")[:7] or None,
                              "predictions": len(preds), "events": 0, "linked": len(main._sb_links), "report": []}
    if preds:
        try:
            events, status["report"] = await sportybet.fetch_catalog()
        except Exception as e:
            events, status["report"] = [], [f"{type(e).__name__}: {e}"]
        status["events"] = len(events)
        status["catalog"] = main._catalog_summary(events)
        if events:
            try:
                preds += await asyncio.to_thread(main._publish_sb_leagues, events)
            except Exception as e:
                status["report"].append(f"SportyBet-only leagues: {type(e).__name__}: {e}")
        if events:  # an empty catalog (SportyBet unreachable) keeps the last links
            unlinked: List[Dict] = []
            links = await asyncio.to_thread(main._match_predictions_to_events, preds, events, unlinked)
            # Worth checking first: closest candidates, highest score first
            status["unlinked"] = sorted(unlinked, key=lambda u: -u["score"])[:40]
            status["market_map"], status["market_labels"] = await main._market_map(events, links, status["report"])
            status["market_coverage"] = main._market_coverage(links)
            main._sb_links.clear()
            main._sb_links.update(links)
            status["linked"] = len(links)
            r = main._get_redis()
            if r:
                try:
                    await asyncio.to_thread(lambda: r.set(main.SB_LINKS_KEY, json.dumps(links), ex=6 * 3600))
                except Exception as e:
                    print(f"[SportyBet] Could not save links: {e}")
        else:
            status["error"] = "Couldn't load SportyBet's match list — kept the previous links."
            status["market_labels"] = main._sb_link_status.get("market_labels") or {}
            status["market_map"] = main._sb_link_status.get("market_map") or {}
    else:
        status["error"] = "No upcoming predictions to link yet."
    try:
        status["europe_priced"] = await asyncio.to_thread(main._publish_waiting_europe)
    except Exception as e:
        status["report"].append(f"Europa/Conference from SportyBet's odds: {type(e).__name__}: {e}")
    try:
        status["leagues_priced"] = await asyncio.to_thread(main._price_unpriced)
    except Exception as e:
        status["report"].append(f"More European leagues from SportyBet's odds: {type(e).__name__}: {e}")
    status["seconds"] = round(time.monotonic() - started, 1)
    for p in main._predictions_cache:
        p["sportybet"] = main._sb_key(p.get("home", ""), p.get("away", ""), p.get("date", "")) in main._sb_links
    main._sb_link_status.clear()
    main._sb_link_status.update(status)
    await asyncio.to_thread(main._save_link_status)
    # The fresh prices into each match day, so the last before kick-off (its
    # closing price, for price_book.clv_report) is at most a run old
    r = main._get_redis()
    if r and main._sb_links:
        try:
            await asyncio.to_thread(main._snapshot_matchdays, r)
        except Exception as e:
            print(f"[SportyBet] Snapshot after linking failed: {e}")
    print(f"[SportyBet] Linked {status['linked']}/{status['predictions']} predictions "
          f"({status['events']} SportyBet events, {trigger}) — {' · '.join(status['report']) or 'no fetch'}")
    return main._sb_link_status


async def _link_on_startup() -> None:
    """After a deploy or restart: show the last result straight away, then
    link the cached predictions without waiting for the pipeline."""
    main._restore_link_status()
    if main._predictions_cache:
        await main._link_sportybet_events("startup")


@router.get("/api/sportybet/status")
async def sportybet_status():
    """Public: how many upcoming matches can be booked on SportyBet, and when that was checked."""
    import booking_slip
    main._restore_link_status()
    s = main._sb_link_status
    confirmed = s.get("market_map") or {}
    # How many linked matches SportyBet prices each market on (shots, corners
    # and cards appear about a day before kick-off, on some matches only)
    coverage = s.get("market_coverage") or {}
    ids = {**booking_slip._LINE_MARKETS, **{k: (v or {}).get("id") for k, v in confirmed.items() if (v or {}).get("id")}}
    return {"at": s.get("at"), "trigger": s.get("trigger"), "linked": s.get("linked", 0),
            "predictions": s.get("predictions", 0), "every_minutes": main.SB_LINK_MINUTES,
            "markets": {kind: bool((confirmed.get(kind) or {}).get("ok")) for kind in booking_slip.VERIFIED},
            "coverage": {kind: coverage.get(str(mid), 0) for kind, mid in ids.items() if mid}}


def _sb_1x2(fx: Dict[str, Any]) -> Dict[str, float]:
    """SportyBet's 1X2 prices for a linked fixture ({"1", "X", "2"}), or {}."""
    import booking_slip
    ev = main._linked_event(fx)
    if not ev:
        return {}
    out = {}
    for code in ("1", "X", "2"):
        ids = booking_slip.sportybet_ids("1x2", code)
        o = booking_slip._event_odds(ev, ids) if ids else None
        if o and o > 1:
            out[code] = float(o)
    return out if len(out) == 3 else {}


def _publish_waiting_europe() -> int:
    """The held-back Europa / Conference League matches SportyBet now prices,
    predicted and added to the published list; returns how many."""
    if not main._europe_waiting or main._predictor is None:
        return 0
    have = {main._sb_key(p.get("home", ""), p.get("away", ""), p.get("date", "")) for p in main._predictions_cache}
    todo = [fx for fx in main._europe_waiting if main._within_window(fx.get("date", ""))
            and main._sb_key(fx["home"], fx["away"], fx.get("date", "")) not in have and main._sb_1x2(fx)]
    if not todo:
        return 0
    unknown = dict(main._unknown_clubs)           # _build_predictions starts that list afresh
    try:
        new = main._build_predictions(main._predictor, todo, {})
    finally:
        main._unknown_clubs.update(unknown)
    for p in new:
        p["sportybet"] = True
    if new:
        main._predictions_cache = main._predictions_cache + new
        main._last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        main._save_predictions_cache()
    return len(new)


def _load_sb_fixtures() -> None:
    r = main._get_redis()
    try:
        main._sb_fixtures[:] = json.loads(r.get(main.SB_FIXTURES_KEY) or "[]") if r else []
    except Exception as e:
        print(f"[SB leagues] stored fixtures not read: {e}")


def _publish_sb_leagues(events: List[Dict]) -> List[Dict]:
    """Group B's upcoming matches from the linker's SportyBet catalog
    (sb_league_fixtures.py), predicted and published in place of the last
    run's not-yet-started ones; returns the new predictions (for linking)."""
    import collect_sb_leagues
    import league_fixtures
    import live_learning
    import sb_league_fixtures
    r = main._get_redis()
    if r is None:
        return []
    picked = sb_league_fixtures.leagues(collect_sb_leagues.load(r))
    fixtures = sb_league_fixtures.from_catalog(events, picked, days_ahead=main.PREDICTION_DAYS)
    if not fixtures:
        return []
    main._sb_fixtures[:] = fixtures
    try:
        r.set(main.SB_FIXTURES_KEY, json.dumps(fixtures), ex=3 * 86400)
    except Exception as e:
        print(f"[SB leagues] fixtures not saved: {e}")
    main._unproven_leagues = (main._unproven_leagues - set(picked)) | league_fixtures.unproven(r, set(picked))
    if main._predictor is None:
        return []
    unknown = dict(main._unknown_clubs)           # _build_predictions starts that list afresh
    try:
        new = main._build_predictions(main._predictor, fixtures, {})
    finally:
        main._unknown_clubs.update(unknown)
    now = datetime.now(timezone.utc)
    keep = [p for p in main._predictions_cache if not (str(p.get("league", "")).startswith("SB-")
                                                  and not live_learning.kicked_off(p, now))]
    started = {main._sb_key(p.get("home", ""), p.get("away", ""), p.get("date", "")) for p in keep}
    new = [p for p in new if main._sb_key(p["home"], p["away"], p.get("date", "")) not in started]
    main._predictions_cache = keep + new
    main._last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    main._save_predictions_cache()
    print(f"[SB leagues] {len(new)} matches published from {len(picked)} leagues")
    return new


def _price_unpriced() -> int:
    """The published matches not yet started that were predicted without
    odds (no feed for their league, or a cup tie it skipped) and SportyBet
    now prices, predicted again with its prices — so their chances are
    blended with the market's (_blend_1x2); returns how many."""
    import live_learning
    if main._predictor is None or not main._predictions_cache:
        return 0
    unknown = dict(main._unknown_clubs)           # _build_predictions starts that list afresh
    now = datetime.now(timezone.utc)
    out, redone = [], 0
    try:
        for p in main._predictions_cache:
            if not p.get("odds_home") and not live_learning.kicked_off(p, now):
                odds = main._sb_1x2(p)
                new = main._build_predictions(main._predictor, [p], {f"{p['home']}:{p['away']}:{p.get('date', '')}": odds}) \
                    if odds else []
                if new:
                    out.append({**{k: v for k, v in p.items() if k not in ("thin_history", "league_unproven")},
                                **new[0]})
                    redone += 1
                    continue
            out.append(p)
    finally:
        main._unknown_clubs.update(unknown)
    if redone:
        main._predictions_cache = out
        main._last_updated = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        main._save_predictions_cache()
    return redone


def _linked_event(selection: Dict[str, Any]) -> Optional[Dict]:
    if not main._sb_links:
        r = main._get_redis()
        if r:
            try:
                main._sb_links.update(json.loads(r.get(main.SB_LINKS_KEY) or "{}"))
            except Exception:
                pass
    return main._sb_links.get(main._sb_key(selection.get("home", ""), selection.get("away", ""), selection.get("date", "")))


def _with_priced_set_pieces(pred: Dict, event: Optional[Dict]) -> Dict:
    """A prediction with corners/bookings lines implied by SportyBet's own
    prices for whichever of the two our models don't cover for it
    (internationals, clubs outside the league data) — set_pieces.from_prices."""
    have = pred.get("set_pieces") or {}
    if not event:
        return pred
    priced = {} if ("corners" in have and "bookings" in have) else (
        set_pieces.from_prices(event, getattr(main._set_pieces, "size", None)) or {})
    # Shot lines too, for national teams we have too little data on
    if str(pred.get("league") or "").startswith("INT"):
        priced.update(shots.from_prices(event, getattr(main._intl_shots, "size", None)) or {})
    extra = {k: v for k, v in priced.items() if k not in have}
    return {**pred, "set_pieces": {**have, **extra}} if extra else pred
