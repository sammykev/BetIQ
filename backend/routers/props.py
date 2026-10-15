"""
Player props (props_collect → props_backtest → props_pricing).

Moved out of main.py; main's shared state is read as `main.<name>` at call time.
"""

import asyncio
import json
from datetime import date, timedelta
from fastapi import APIRouter, Depends, Query
from typing import Dict, List, Optional

import main

router = APIRouter()


def _props_load_sync() -> Dict[str, int]:
    import basketball_data as bd
    import props_backtest
    import props_collect as pc
    r = main._get_redis()
    if not r:
        return {}
    got = {}
    for sport, leagues in (("bb", ("NBA", "WNBA", "Euroleague", "Eurocup")), ("fb", tuple(pc.UNDERSTAT.values()))):
        for league in leagues:
            raw = r.get(pc.PLAYERS_KEY.format(sport=sport, league=league))
            if raw:
                main._props_players[(sport, league)] = bd.decode(raw)
                got[f"{sport}:{league}"] = len(main._props_players[(sport, league)])
    try:
        main._props_calib.clear()
        main._props_calib.update(json.loads(r.get(props_backtest.CALIB_KEY) or "{}"))
        main._props_fb.update(json.loads(r.get(main.PROPS_FB_KEY) or "{}"))
    except Exception:
        pass
    return got


def _bb_prop_lines(pred: Dict, page: Dict) -> List[Dict]:
    """A basketball match's player lines, priced (props_pricing.bb_price)."""
    import basketball_model as bm
    import props_pricing as pr
    import sportybet
    league = pr.BB_LEAGUES.get(pred.get("league") or "")
    players = main._props_players.get(("bb", league)) if league else None
    if not players:
        return []
    # Tonight's expected points against each team's usual (our basketball ratings)
    lg = main._bb_leagues.get(pred["league"])
    factor: Dict[str, float] = {}
    if lg:
        for side, exp_pts in (("home", pred.get("exp_home_pts")), ("away", pred.get("exp_away_pts"))):
            name = pred[side]
            usual = lg.avg + lg.attack.get(name, 0.0) + (lg.home_court / 2)
            if exp_pts and usual > 0:
                # Box scores name teams their own way: every team name close to this side's
                for t in {p["team"] for p in players.values()}:
                    if sportybet.team_similarity(t, name) >= 0.75:
                        factor[t] = exp_pts / usual
    calib = (main._props_calib.get("bb") or {}).get(league) or {}
    disp = dict(calib.get("dispersion") or {})
    return pr.bb_price(page, players, factor, disp, teams=(pred["home"], pred["away"]),
                       calibration=calib.get("calibration_map") or {})


async def _bb_add_props(preds: List[Dict]) -> int:
    """Player lines on the matches of leagues we have box scores for."""
    import props_pricing as pr
    import sportybet
    todo = [p for p in preds if p.get("league") in pr.BB_LEAGUES][:main.PROPS_BB_MAX_PAGES]
    n = 0
    for p in todo:
        try:
            page = await sportybet.event_page(p["sportybet_event_id"])
        except Exception as e:
            print(f"[Props] page {p['sportybet_event_id']}: {e}")
            continue
        lines = main._bb_prop_lines(p, page or {})
        if lines:
            p["bb_markets"] = (p.get("bb_markets") or []) + lines
            p["props"] = len(lines)
            n += len(lines)
        await asyncio.sleep(0.3)
    return n


@router.get("/api/props/status")
async def get_props_status(_admin: str = Depends(main.require_admin)):
    """Admin: players loaded per league, and the walk-forward check's numbers."""
    return {"players": {f"{s}:{l}": len(v) for (s, l), v in main._props_players.items()},
            "check": main._props_calib or None,
            "football_matches_priced": len(main._props_fb)}


@router.get("/api/props/football")
async def get_football_props(home: str = Query(..., max_length=80), away: str = Query(..., max_length=80),
                             date_: str = Query("", alias="date", max_length=10)):
    """A football match's anytime goalscorers: our chance, SportyBet's price and ids."""
    for lines in main._props_fb.values():
        if lines and lines[0]["home"] == home and lines[0]["away"] == away and (not date_ or lines[0]["date"] == date_):
            return {"scorers": lines}
    return {"scorers": []}


def _props_result_for(leg: Dict) -> Optional[Dict]:
    """A player-prop leg's result from the box scores: {"status", "value"}
    (his count of the stat, or goals), "void" when he didn't play."""
    import player_props as pp
    import props_pricing as pr
    market = str(leg.get("market") or "")
    key = str(leg.get("code") or "").split("|")[0]
    if market.startswith("bb_player_"):
        stat = market[len("bb_player_"):]
        col = pr.BB_STATS.get(stat, (None,))[0]
        pools = [v for (sport, _), v in main._props_players.items() if sport == "bb"]
    elif market == "anytime_scorer":
        col, pools = 5, [v for (sport, _), v in main._props_players.items() if sport == "fb"]
    else:
        return None
    try:
        d0 = date.fromisoformat(leg.get("date") or "")
    except ValueError:
        return None
    days = {(d0 + timedelta(days=i)).isoformat() for i in (-1, 0, 1)}
    for players in pools:
        p = players.get(key)
        if not p or col is None:
            continue
        rows = [r for r in p["games"] if r[0] in days]
        if rows:
            return {"status": "finished", "value": float(rows[-1][col])}
        # His team's game is in but he isn't: he didn't play (void)
        if any(r[0] in days and r[1] == p["team"] for q in players.values() for r in q["games"][-5:]):
            return {"status": "void"}
    return None


def _bb_result_for(r, leg: Dict, cache: Dict[str, Dict]) -> Optional[Dict]:
    """A basketball ticket leg's result, by the SportyBet event it was booked on."""
    import basketball_data as bd
    try:
        d0 = date.fromisoformat(leg.get("date") or "")
    except ValueError:
        return None
    days = [(d0 + timedelta(days=i)).isoformat() for i in (-1, 0, 1)]
    for d in days:
        if d not in cache:
            cache[d] = {g["id"]: g for g in main._bb_results(r, [d])}
    games = {k: v for d in days for k, v in cache[d].items()}
    return bd.result_for(games, leg)
