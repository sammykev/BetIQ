"""
Tennis and table tennis predictions from SportyBet's listing: for every
match, our match distribution (tennis_model / table_tennis_model — the
fitted ratings blended with SportyBet's winner prices) and our chance of
every line SportyBet offers on it (racket_markets), each at SportyBet's
price and with its ids, so any of them can go in a booking code or the
optimizer.

The model's share of the level (MODEL_WEIGHT) is small: SportyBet's winner
prices are sharp; our ratings move them where they know both players, and
the point model prices the other markets consistently with the level.
"""

import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import racket_markets as rkm

MODEL_WEIGHT = {"tennis": 0.35, "table_tennis": 0.35}
KEEP_MIN, KEEP_MAX = 0.50, 0.985
MAX_LINES = 60
FLAGS = {"tennis": "🎾", "table_tennis": "🏓"}
SLAMS = ("australian open", "roland garros", "french open", "wimbledon", "us open")

FAMILIES = {
    "rk_winner": "Winner", "rk_set_handicap": "Set Handicap", "rk_games_handicap": "Games Handicap",
    "rk_total_games": "Total Games", "rk_total_sets": "Total Sets", "rk_correct_score": "Correct Score",
    "rk_odd_even": "Odd/Even", "rk_to_win_set": "To Win a Set", "rk_sets": "Set Markets",
}


# Which walk-forward check calibrates each kind of line (tennis_fit /
# table_tennis_fit "calibration_maps"; the winner is calibrated by the shrink)
CALIBRATED_BY = {
    "tennis": {"s_winner": "first_set", "total_games": "total_games", "games_handicap": "games_handicap",
               "set_handicap": "set_handicap", "home_set": "set_handicap", "away_set": "set_handicap",
               "total_sets": "total_sets", "s_total": "set_total"},
    "table_tennis": {"s_winner": "game_winner", "total_games": "total_points", "games_handicap": "points_handicap",
                     "set_handicap": "games_handicap", "home_set": "games_handicap", "away_set": "games_handicap",
                     "total_sets": "total_games", "s_total": "game_points", "odd_even": "odd_even"},
}


def calibrated(p: float, kind: str, sport: str, model: Optional[Dict], code: str = "") -> float:
    """Our chance through the check's map for this kind of line. Totals have
    a map for each side ("…:O", "…:U", each fitted where that side is the
    likelier): the likelier side is calibrated, the other is what's left."""
    import player_props as pp
    maps = (model or {}).get("calibration") or {}
    name = CALIBRATED_BY.get(sport, {}).get(kind, "")
    if f"{name}:O" in maps and code[:1] in ("O", "U"):
        over = p if code[0] == "O" else 1 - p
        cal = pp.calibrate(over, maps[f"{name}:O"]) if over >= 0.5 else 1 - pp.calibrate(1 - over, maps.get(f"{name}:U"))
        return cal if code[0] == "O" else 1 - cal
    return pp.calibrate(p, maps.get(name))


def family(market: str) -> str:
    if market in ("rk_home_set", "rk_away_set"):
        return "rk_to_win_set"
    if re.match(r"^rk_s\d_", market):
        return "rk_sets"
    return market


def kickoff(ev: Dict) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(int(ev["estimateStartTime"]) / 1000, timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def tennis_best_of(tournament: str, offs: List[Dict]) -> int:
    """5 for a men's Grand Slam (or when SportyBet offers a 3-set win), else 3."""
    if any(o["market"] == "rk_correct_score" and "3" in o["code"].split(":") for o in offs):
        return 5
    t = (tournament or "").lower()
    women = any(x in t for x in ("women", "wta", "ladies", "girls", "boys", "junior", "double", "mixed"))
    return 5 if any(s in t for s in SLAMS) and not women else 3


def market_p1(offs: List[Dict]) -> Optional[float]:
    w = rkm.main_lines(offs)["winner"]
    if not w:
        return None
    a, b = 1 / w[0], 1 / w[1]
    return a / (a + b)


def distribution(ev: Dict, sport: str, model: Optional[Dict], offs: List[Dict]):
    """The match's distribution, or None (no prices and no ratings)."""
    p1, p2 = ev.get("homeTeamName") or "", ev.get("awayTeamName") or ""
    t = ev.get("_tournament") or ""
    mp = market_p1(offs)
    model = model or {}
    if sport == "tennis":
        import tennis_model as tm
        from sports_fetcher import _surface
        return tm.predict(model.get("players") or {}, model.get("avg") or dict(tm.DEFAULT_SPW), p1, p2, _surface(t),
                          tennis_best_of(t, offs), mp, MODEL_WEIGHT["tennis"], tm.tour_of(t),
                          model.get("shrink", 1.0), model.get("surface_weight"))
    import table_tennis_model as ttm
    bo = 7 if any(o["market"] == "rk_correct_score" and "4" in o["code"].split(":") for o in offs) else ttm.best_of(t)
    return ttm.predict(model.get("players") or {}, p1, p2, mp, MODEL_WEIGHT["table_tennis"], bo,
                       model.get("swing", ttm.SWING), shrink_s=model.get("shrink", 1.0))


def predict(ev: Dict, sport: str, model: Optional[Dict]) -> Optional[Dict[str, Any]]:
    home, away = ev.get("homeTeamName") or "", ev.get("awayTeamName") or ""
    k = kickoff(ev)
    offs = rkm.offers(ev, sport)
    if not home or not away or not k or "/" in home or "/" in away:
        return None
    md = distribution(ev, sport, model, offs)
    if md is None:
        return None
    priced = []
    for o in offs:
        p = rkm.probability(md, o)
        if p is None:
            continue
        p = calibrated(p, o["kind"], sport, model, o["code"])
        priced.append({"market": o["market"], "family": family(o["market"]), "market_name": rkm.market_name(o["market"], sport),
                       "code": o["code"], "label": rkm.label(o, home, away, sport), "prob": round(p, 4),
                       "odds": o["odds"], "edge": round(p * o["odds"] - 1, 3), "sb": o["sb"]})
    winner = {x["code"]: x for x in priced if x["market"] == "rk_winner"}
    keep = sorted((x for x in priced if KEEP_MIN <= x["prob"] <= KEEP_MAX), key=lambda x: -x["prob"])[:MAX_LINES]
    keep += [x for x in winner.values() if x not in keep]
    p_home = md.p_win
    tip_home = p_home >= 0.5
    t = ev.get("_tournament") or ("Tennis" if sport == "tennis" else "Table Tennis")
    lines = rkm.main_lines(offs)
    total_line = lines["total"][0] if lines["total"] else None
    over_p = (calibrated(md.p_total_over(total_line), "total_games", sport, model, "O")
              if total_line is not None else None)
    words = rkm.WORDS[sport]
    out = {
        "sport": sport, "home": home, "away": away,
        "date": k.strftime("%Y-%m-%d"), "time": k.strftime("%H:%M"),
        "league": t, "league_name": t, "flag": FLAGS[sport],
        "sportybet_event_id": str(ev.get("eventId") or ""),
        "p_home": round(p_home, 3), "p_draw": 0, "p_away": round(1 - p_home, 3),
        "tip_1x2": f"{home if tip_home else away} Win", "tip_code": "1" if tip_home else "2",
        "tip_confidence": round(max(p_home, 1 - p_home), 3),
        "goals_confidence": round(max(p_home, 1 - p_home), 3),
        "goals_type": "value" if max(p_home, 1 - p_home) > 0.65 else "normal",
        "odds_home": (winner.get("1") or {}).get("odds"), "odds_away": (winner.get("2") or {}).get("odds"),
        "total_line": total_line,
        "tip_goals": (f"{'Over' if over_p >= 0.5 else 'Under'} {total_line:g} {words['games']}"
                      if total_line is not None and over_p is not None else ""),
        "p_over_line": round(over_p, 3) if over_p is not None else None,
        "p_over15": 0, "p_over25": 0,
        "exp_games": round(sum(g * v for g, v in md.total_games.items()), 1),
        "set_scores": {f"{a}:{b}": round(v, 3) for (a, b), v in sorted(md.sets.items(), key=lambda kv: -kv[1])},
        "model": "ratings+market" if (md.detail or {}).get("known") else "market",
        "model_detail": md.detail, "rated": bool((md.detail or {}).get("known")),
        "source": "sportybet", "rk_markets": keep,
    }
    if sport == "tennis":
        from sports_fetcher import _surface
        out["surface"] = _surface(t)
    return out


def build(events: List[Dict], sport: str, model: Optional[Dict], now: Optional[datetime] = None) -> List[Dict]:
    """Predictions for the listed matches not started yet, soonest first."""
    now = now or datetime.now(timezone.utc)
    out = []
    for ev in events:
        k = kickoff(ev)
        if not k or k <= now:
            continue
        try:
            p = predict(ev, sport, model)
        except Exception as e:
            print(f"[{sport}] couldn't price {ev.get('homeTeamName')} v {ev.get('awayTeamName')}: {e}")
            continue
        if p:
            out.append(p)
    return sorted(out, key=lambda p: (p["date"], p["time"], p["home"]))


def options(pred: Dict, min_prob: float, families: Optional[set] = None,
            allowed: Optional[Callable[[str, str], bool]] = None) -> List:
    """The optimizer's choices on one match (at most one is picked a match)."""
    import optimizer
    out = []
    for x in pred.get("rk_markets") or []:
        if families and x["family"] not in families:
            continue
        if allowed and not allowed(x["market"], x["code"]):
            continue
        if not min_prob <= x["prob"] < 0.995 or x["odds"] <= 1.01:
            continue
        out.append(optimizer.Option(pred["home"], pred["away"], pred["date"], pred.get("time") or "",
                                    pred.get("league_name") or "", x["market"], x["market_name"], x["code"],
                                    x["label"], x["prob"], x["odds"], "sportybet", sb=x["sb"], sport=pred["sport"]))
    return out
