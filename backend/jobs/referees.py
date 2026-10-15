"""
Referees appointed to upcoming matches, and past referees for the cards model.

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import httpx
import json
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
