"""
Basketball predictions from SportyBet's listing: for every match, our
expected points (the league's ratings, blended with the market's main
lines — basketball_model.blend) and our chance of every line SportyBet
offers on it (basketball_markets), each at SportyBet's price and with its
ids, so any of them can go in a booking code or the optimizer.
"""

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import basketball_data as bd
import basketball_markets as bmk
import basketball_model as bm

MODEL_WEIGHT = 0.35      # our expectation's share where we know both teams (the market's lines are sharp)
KEEP_MIN, KEEP_MAX = 0.50, 0.985   # the lines kept with a prediction, by our chance
MAX_LINES = 90           # per match, likeliest first

# Families of markets, as the optimizer and the pages group them
FAMILIES = {
    "bb_winner": "Winner", "bb_1x2": "1X2 (regulation)", "bb_handicap": "Handicap", "bb_total": "Total Points",
    "bb_team_total": "Team Points", "bb_halves": "Halves", "bb_quarters": "Quarters", "bb_overtime": "Overtime",
    "bb_player": "Player props",
}


def family(market: str) -> str:
    if market in ("bb_winner", "bb_1x2", "bb_handicap", "bb_total", "bb_overtime"):
        return market
    if market in ("bb_home_total", "bb_away_total"):
        return "bb_team_total"
    if market.startswith(("bb_h1_", "bb_h2_")):
        return "bb_halves"
    if market.startswith("bb_q"):
        return "bb_quarters"
    if market.startswith("bb_player_"):
        return "bb_player"
    return market


def _expectation(ev: Dict, offs: List[Dict], lg: Optional[bm.League]) -> Optional[bm.Match]:
    home, away = ev.get("homeTeamName") or "", ev.get("awayTeamName") or ""
    sigma = dict(lg.sigma) if lg else dict(bm.DEFAULT_SIGMA)
    lines = bmk.main_lines(offs)
    margin = bm.market_margin(sigma["margin"], lines["winner"], lines["handicap"])
    total = bm.market_total(sigma["total"], lines["total"])
    if total is not None:
        # SportyBet's total counts overtime; ours is regulation's
        total -= 0.05 * bm.OT_SHARE * total
    ours = bm.expect(lg, home, away) if lg else None
    if ours is None and total is None and lg is not None and margin is not None:
        total = 2 * lg.avg
    return bm.blend(ours, sigma, margin, total, MODEL_WEIGHT,
                    lg.h1_share if lg else 0.5, tuple(lg.q_shares) if lg else (0.25,) * 4)


def predict(ev: Dict, lg: Optional[bm.League]) -> Optional[Dict[str, Any]]:
    """One match's prediction, or None (no prices to go on)."""
    home, away = ev.get("homeTeamName") or "", ev.get("awayTeamName") or ""
    k = bd.kickoff(ev)
    offs = bmk.offers(ev)
    if not home or not away or not k or not offs:
        return None
    match = _expectation(ev, offs, lg)
    lines = bmk.main_lines(offs)
    if match is None:
        return None
    priced = []
    for o in offs:
        p = bmk.probability(match, o)
        if p is None:
            continue
        priced.append({"market": o["market"], "family": family(o["market"]), "market_name": bmk.market_name(o["market"]),
                       "code": o["code"], "label": bmk.label(o, home, away), "prob": round(p, 4),
                       "odds": o["odds"], "edge": round(p * o["odds"] - 1, 3), "sb": o["sb"]})
    winner = {x["code"]: x for x in priced if x["market"] == "bb_winner"}
    p_home = match.p_win(True)
    keep = sorted((x for x in priced if KEEP_MIN <= x["prob"] <= KEEP_MAX), key=lambda x: -x["prob"])[:MAX_LINES]
    keep += [x for x in winner.values() if x not in keep]
    t = ev.get("_tournament") or ""
    country, name = bd.split_tournament(t)
    tip_home = p_home >= 0.5
    total_line = lines["total"][0] if lines["total"] else None
    over_p = match.p_total_ot(total_line) if total_line else None
    return {
        "sport": "basketball", "home": home, "away": away,
        "date": k.strftime("%Y-%m-%d"), "time": k.strftime("%H:%M"),
        "league": t, "league_name": name or t, "country": country, "flag": bd.flag(t),
        "home_logo": bd.crest(ev.get("homeTeamId")), "away_logo": bd.crest(ev.get("awayTeamId")),
        "sportybet_event_id": str(ev.get("eventId") or ""),
        "p_home": round(p_home, 3), "p_draw": 0, "p_away": round(1 - p_home, 3),
        "tip_1x2": f"{home if tip_home else away} Win", "tip_code": "1" if tip_home else "2",
        "tip_confidence": round(max(p_home, 1 - p_home), 3),
        "goals_confidence": round(max(p_home, 1 - p_home), 3),
        "odds_home": (winner.get("1") or {}).get("odds"), "odds_away": (winner.get("2") or {}).get("odds"),
        "total_line": total_line,
        "tip_goals": (f"{'Over' if over_p >= 0.5 else 'Under'} {total_line:g}" if total_line and over_p is not None else ""),
        "p_over_line": round(over_p, 3) if over_p is not None else None,
        "handicap_line": lines["handicap"][0] if lines["handicap"] else None,
        "exp_home_pts": round(match.home_pts, 1), "exp_away_pts": round(match.away_pts, 1),
        "model": match.source, "model_detail": match.detail,
        "rated": bool(lg and lg.known(home) and lg.known(away)),
        "bb_markets": keep,
    }


def build(events: List[Dict], leagues: Dict[str, bm.League], now: Optional[datetime] = None) -> List[Dict]:
    """Predictions for the listed matches not started yet, soonest first."""
    now = now or datetime.now(timezone.utc)
    out = []
    for ev in events:
        k = bd.kickoff(ev)
        if not k or k <= now:
            continue
        try:
            p = predict(ev, leagues.get(ev.get("_tournament") or ""))
        except Exception as e:
            print(f"[Basketball] couldn't price {ev.get('homeTeamName')} v {ev.get('awayTeamName')}: {e}")
            continue
        if p:
            out.append(p)
    return sorted(out, key=lambda p: (p["date"], p["time"], p["home"]))


def options(pred: Dict, min_prob: float, families: Optional[set] = None,
            allowed: Optional[Callable[[str, str], bool]] = None) -> List:
    """The optimizer's choices on one match: its priced lines at or above
    min_prob (at most one is picked a match), each with SportyBet's ids."""
    import optimizer
    out = []
    for x in pred.get("bb_markets") or []:
        if families and x["family"] not in families:
            continue
        if allowed and not allowed(x["market"], x["code"]):
            continue
        if not min_prob <= x["prob"] < 0.995 or x["odds"] <= 1.01:
            continue
        out.append(optimizer.Option(pred["home"], pred["away"], pred["date"], pred.get("time") or "",
                                    pred.get("league_name") or "", x["market"], x["market_name"], x["code"],
                                    x["label"], x["prob"], x["odds"], "sportybet", sb=x["sb"], sport="basketball"))
    return out
