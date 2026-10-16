"""
Shots models, international set pieces, and our shot lines vs SportyBet's.

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import international_fixtures as intl
import json
import pandas as pd
import set_pieces
import shots
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import main


def _fit_shots(history: pd.DataFrame) -> None:
    """Fit the club shots model, and check each stat on the last 12 months'
    matches (predicted from the ones before them) against the league average."""
    # Per-league settings where a league clearly beat the defaults (tune_football.py)
    params = {"by_league": main._football_tuning().get("shots") or {}}
    model = shots.ShotModel.fit(history, params)
    if model is None:
        main._shots, main._shots_info = None, {"reason": "no shots data in the league CSVs"}
        return
    last = history["Date"].max()
    verdict = shots.check(history, str((last - pd.Timedelta(days=365)).date()), params=params)
    main._shots = model
    main._shots_info = {"check": verdict, "use": {k: v["use"] for k, v in verdict.items()},
                   "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    print(f"[Pipeline] Shots model: {', '.join(k for k, v in verdict.items() if v['use']) or 'no stat beat the average'}")


def _football_tuning() -> Dict[str, Dict]:
    """The per-league corners / bookings / shots settings saved by tune_football.py ({} without)."""
    try:
        import model_store
        import tune_football
        return tune_football.load(model_store._client())
    except Exception as e:
        print(f"[Pipeline] No football tuning ({e}); defaults")
        return {}


def _shot_markets(home: str, away: str, league: Optional[str]) -> Optional[Dict]:
    """Shots / shots-on-target lines for a club fixture: the stats the check approved."""
    if main._shots is None:
        return None
    got = main._shots.markets(home, away, league)
    use = main._shots_info.get("use") or {}
    keep = {k: v for k, v in (got or {}).items() if use.get(k)}
    return keep or None


def _index_intl_stats(frame: pd.DataFrame) -> None:
    import match_facts
    if frame is None or frame.empty:
        return
    ours = match_facts.frame(frame)
    table: Dict[Tuple[str, str, str], Dict[str, float]] = {}
    for rec in ours.to_dict("records"):
        try:
            day = pd.Timestamp(rec["Date"]).date().isoformat()
        except (TypeError, ValueError):
            continue
        stats = {k: float(rec[k]) for k in match_facts.STAT_COLS if rec.get(k) == rec.get(k) and rec.get(k) is not None}
        if stats:
            table[(day, intl.team_key(rec["HomeTeam"]), intl.team_key(rec["AwayTeam"]))] = stats
    main._intl_stats_by_key.clear()
    main._intl_stats_by_key.update(table)


def _intl_stats_for(day: str, home: str, away: str) -> Optional[Dict[str, float]]:
    """A national-team match's stats from the international dataset (a day either side)."""
    if not main._intl_stats_by_key:
        return None
    h, a = intl.team_key(home), intl.team_key(away)
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return None
    for o in (0, -1, 1):
        got = main._intl_stats_by_key.get(((d + timedelta(days=o)).isoformat(), h, a))
        if got:
            return got
    return None


def _load_international_set_pieces() -> None:
    """Fit the international corners/bookings model from the collected data,
    with the settings the nightly check chose — if it beat the competition
    average there. CPU-light (well under a second); run in a thread."""
    import international_stats
    import model_store
    try:
        data = international_stats.load(model_store._client())
    except Exception as e:
        main._intl_sp_info = {"error": str(e)}
        return
    verdict = data.get("model") or {}
    shot_verdict = data.get("shots_model") or {}
    main._intl_sp_info = {"dataset": international_stats.summary(data), "check": verdict, "shots_check": shot_verdict}
    frame = international_stats.rows_frame(data)
    main._index_intl_stats(frame)
    main._intl_shots = None
    if any((shot_verdict.get("use") or {}).values()) and shot_verdict.get("params"):
        # The check chose to start teams' shot ratings from their Elo: the same
        # ratings here, from the synced results history
        if (shot_verdict["params"] or {}).get("elo_weight"):
            import intl_elo
            import football_data_sync
            elo = intl_elo.EloTimeline.from_file(football_data_sync.INTERNATIONAL_PATH)
            set_pieces.STRENGTH = elo.strength if elo else None
            main._intl_sp_info["elo"] = {"teams": elo.teams()} if elo else {"error": "no results history file"}
        main._intl_shots = shots.ShotModel.fit(frame.dropna(subset=list(shots.ShotModel.REQUIRED)), shot_verdict["params"])
    use = verdict.get("use") or {}
    if not any(use.values()) or not verdict.get("params"):
        main._intl_set_pieces = None
        return
    main._intl_set_pieces = set_pieces.SetPieceModel.fit(frame, verdict["params"])


def _international_set_pieces(fx: Dict, referee: Optional[str] = None,
                              career: Optional[Dict] = None) -> Optional[Dict]:
    """Corners/bookings/shots for an international fixture, only the stats
    the nightly checks approved."""
    home, away = intl.team_key(fx["home"]), intl.team_key(fx["away"])
    keep: Dict[str, Any] = {}
    if main._intl_set_pieces is not None:
        use = (main._intl_sp_info.get("check") or {}).get("use") or {}
        got = main._intl_set_pieces.markets(home, away, fx.get("league"), referee, career) or {}
        keep = {k: v for k, v in got.items() if use.get(k)}
        if got and use.get("corners_home") and use.get("corners_away"):
            keep["corners_1x2"] = got["corners_1x2"]
        else:
            keep.pop("corners_1x2", None)
    if main._intl_shots is not None:
        shot_use = (main._intl_sp_info.get("shots_check") or {}).get("use") or {}
        got = main._intl_shots.markets(home, away, fx.get("league")) or {}
        keep.update({k: v for k, v in got.items() if shot_use.get(k)})
    # SportyBet's own shot lines, where it prices them: the fallback when we
    # have too little data on a side, mixed with ours when the settled
    # comparison (_shot_blend) showed the mix predicts better
    try:
        priced = shots.from_prices(main._linked_event(fx), getattr(main._intl_shots, "size", None)) or {}
    except Exception:
        priced = {}
    weight = float((main._shot_blend.get("weights") or {}).get("international") or 0.0)
    for stat, line in priced.items():
        if stat not in keep:
            keep[stat] = line
        elif weight:
            keep[stat] = shots.blend(keep[stat], line, weight)
    return keep or None


def _restore_shot_blend() -> None:
    r = main._get_redis()
    if r and not main._shot_blend.get("at"):
        try:
            main._shot_blend.update(json.loads(r.get(main.SHOT_BLEND_KEY) or "{}"))
        except Exception:
            pass


def _refresh_shot_blend() -> Dict[str, Any]:
    """Score our shot lines against SportyBet's on the settled matches of the
    last SHOT_BLEND_DAYS days and choose, per group, how much of SportyBet's
    to mix in (none unless the mix predicted better on enough lines)."""
    r = main._get_redis()
    if not r:
        return {"skipped": "no Redis"}
    today = date.today()
    days = main._md_many(r, [(today - timedelta(days=i)).isoformat() for i in range(1, main.SHOT_BLEND_DAYS + 1)])
    report = shots.blend_report(e for day in days.values() for e in day.values())
    main._shot_blend.update(weights={g: v["chosen"] for g, v in report.items()}, report=report,
                       at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    try:
        r.set(main.SHOT_BLEND_KEY, json.dumps(main._shot_blend))
    except Exception:
        pass
    print(f"[Shots] vs SportyBet: {json.dumps(report)}")
    return report


async def _shot_blend_job() -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(main._refresh_shot_blend)
    except Exception as e:
        print(f"[Shots] vs SportyBet failed: {e}")
        return {"error": str(e)}
