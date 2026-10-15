"""
Match days: snapshots, scores and grades (matchday.py), refreshed on a schedule.

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import international_fixtures as intl
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

import main


async def _refresh_matchdays(days_back: int = 1, trigger: str = "schedule") -> Dict[str, Any]:
    """Scores for matches that have kicked off in the last `days_back` days
    (ESPN live/final, then the league CSVs for stats and anything missed);
    grades them, then settles booked tickets."""
    import matchday
    import results_feed
    r = main._get_redis()
    if not r:
        return {"skipped": "no Redis"}
    now = datetime.now(timezone.utc)
    main._md_status.update(started=now.isoformat(timespec="seconds"), stage="loading match days")
    today = now.date()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(days_back + 1)]
    days = await asyncio.to_thread(main._md_many, r, dates)
    # A day the snapshot never wrote (or a match added since) would never be
    # scored: its predictions go into the store here, before asking for scores
    added = []
    for d in dates:
        preds = [p for p in main._predictions_cache if p.get("date") == d and p.get("sport") in (None, "football")]
        if not preds:
            continue
        try:
            if matchday.merge_predictions(days[d], preds, now):
                await asyncio.to_thread(main._md_save, r, d, days[d])
                added.append(d)
        except Exception as e:
            print(f"[MatchDay] couldn't store {d}'s predictions: {e}")
    # Finals from SportyBet or the backups are checked against ESPN by the
    # 3-hourly sweeps (not the 3-minute live runs)
    need = {d: {k: e for k, e in day.items() if matchday.needs_result(e, now, recheck=trigger != "live")}
            for d, day in days.items()}
    need = {d: v for d, v in need.items() if v}
    report: Dict[str, Any] = {"dates": sorted(need), "matches": sum(len(v) for v in need.values()),
                              "updated": 0, "requests": 0, "errors": [], "unmatched": 0}
    if added:
        report["stored_from_predictions"] = added
    if need:
        pairs = set()
        for entries in need.values():
            for e in entries.values():
                day0 = date.fromisoformat(e["date"])
                for slug in results_feed.slugs_for(e.get("league") or ""):
                    pairs.add((day0, slug))
                    if (e.get("time") or "12:00") < "06:00":  # ESPN files it under the US date
                        pairs.add((day0 - timedelta(days=1), slug))
        espn: List[Dict] = []
        main._md_status["stage"] = f"asking ESPN ({len(pairs)} scoreboards)"
        if pairs:
            from curl_cffi.requests import AsyncSession
            try:
                async with AsyncSession(impersonate=intl.IMPERSONATE, timeout=20) as client:
                    for d0, slug in sorted(pairs):
                        got = await results_feed.fetch_espn(client, [d0], [slug])
                        espn += got["results"]
                        report["requests"] += got["requests"]
                        report["errors"] += got["errors"]
            except Exception as e:
                report["errors"].append(f"ESPN: {type(e).__name__}")
        # Backup: matches under way that ESPN still shows as not started
        # (it lists some small friendlies but never scores them)
        backup: List[Dict] = []
        stale = main._unscored(need, espn, now)
        if stale:
            # SportyBet first (its live list every run, its results now and then), then API-Football
            main._md_status["stage"] = f"asking SportyBet ({len(stale)} unscored)"
            try:
                sb, report["sportybet"] = await asyncio.wait_for(main._sportybet_scores(stale, now), 60)
                backup += sb
            except asyncio.TimeoutError:
                report["sportybet"] = {"error": "over 60s"}
            except Exception as e:
                report["sportybet"] = {"error": f"{type(e).__name__}: {e}"[:200]}
            still = main._unscored(need, espn + backup, now)
            if still:
                main._md_status["stage"] = f"asking API-Football ({len(still)} unscored)"
                af, report["backup"] = await main._backup_results(r, still)
                backup += af
        near = {(date.fromisoformat(d) + timedelta(days=o)).isoformat() for d in need for o in (-1, 0, 1)}
        csv = []
        # The league CSVs arrive a day or two after a match: the 3-hourly sweep
        # reads them; the 3-minute live runs only ask ESPN
        if trigger != "live":
            try:
                csv = await asyncio.to_thread(results_feed.csv_results, near)
            except Exception as e:
                report["errors"].append(f"CSV: {type(e).__name__}")
        main._md_status["stage"] = "saving scores"
        for d, entries in need.items():
            day, changed = days[d], False
            matched = set()
            for source in (espn, backup, csv):
                for k, res in matchday.match_results(entries, source):
                    matched.add(k)
                    before = {f: (day[k].get("result") or {}).get(f) for f in ("status", "hg", "ag")}
                    was_corrected = (day[k].get("result") or {}).get("corrected")
                    if matchday.apply_result(day[k], res):
                        changed = True
                        report["updated"] += 1
                        fix = day[k]["result"].get("corrected")
                        if fix and fix != was_corrected:
                            report.setdefault("corrected", []).append(
                                f"{day[k]['home']} v {day[k]['away']}: {fix['was'][0]}-{fix['was'][1]} → "
                                f"{day[k]['result']['hg']}-{day[k]['result']['ag']}")
                            r.set(main.MD_CORRECTED_KEY, now.isoformat(timespec="seconds"))
                        if before != {f: day[k]["result"].get(f) for f in ("status", "hg", "ag")}:
                            report["scores"] = report.get("scores", 0) + 1
            report["unmatched"] += sum(1 for k in entries if k not in matched
                                       and (matchday.kickoff(entries[k]) or now) < now - timedelta(hours=3))
            if changed:
                await asyncio.to_thread(main._md_save, r, d, day)
    report["errors"] = report["errors"][:20]
    # Settling reads every account with open tickets (a Redis command each):
    # live runs do it when a score changed, or every MD_SETTLE_MINUTES
    # (live stats change every run; only a new score or status can settle a ticket)
    if trigger == "live" and not report.get("scores") and time.time() - main._md_last_settle[0] < main.MD_SETTLE_MINUTES * 60:
        report["tickets"] = {"skipped": "no new scores"}
    else:
        main._md_last_settle[0] = time.time()
        if trigger != "live":
            # Finals for the basketball / racket legs no other source scored
            main._md_status["stage"] = "asking Sportradar for missing finals"
            try:
                import sr_results
                report["sportradar"] = await asyncio.wait_for(sr_results.refresh(r), 60)
            except Exception as e:
                report["sportradar"] = {"error": f"{type(e).__name__}: {e}"[:200]}
            # …and Flashscore's day feeds for the ones Sportradar has nothing for
            main._md_status["stage"] = "asking Flashscore for missing finals"
            try:
                import fs_results
                report["flashscore"] = await asyncio.wait_for(fs_results.refresh(r), 60)
            except Exception as e:
                report["flashscore"] = {"error": f"{type(e).__name__}: {e}"[:200]}
        main._md_status["stage"] = "settling tickets"
        try:
            report["tickets"] = await asyncio.wait_for(asyncio.to_thread(main._settle_tickets, r), 90)
        except asyncio.TimeoutError:
            report["tickets"] = {"error": "settling took over 90s; next run retries"}
        except Exception as e:
            report["tickets"] = {"error": str(e)}
    main._md_status.update(at=now.isoformat(timespec="seconds"), trigger=trigger, report=report,
                      stage="done", error=None)
    if report["updated"]:
        print(f"[MatchDay] {trigger}: {report['updated']} results updated over {report['dates']}")
    return report
