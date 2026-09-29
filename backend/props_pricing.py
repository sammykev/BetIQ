"""
Player props priced from box scores (props_collect.py) against SportyBet's
own player lines, each with its ids so it can be booked.

Basketball (EuroLeague, EuroCup, NBA, WNBA): points, rebounds, assists and
3-pointers — SportyBet's "over/under X.5" (markets 921, 923, 922, 924) and
"X+" (768, 772, 770, 774) lines. Expected count: player_props.project, the
game factor from our basketball model's expected points for his team
tonight against its usual.

Football (Premier League, LaLiga, Bundesliga, Serie A, Ligue 1): anytime
goalscorer (market 40). His goals tonight ~ Poisson(λ):
    λ = minutes/90 x (0.7 npxG + 0.3 non-penalty goals, per 90, pulled to a prior)
        x team factor (our expected goals for his team / its usual)
      + his share of the team's penalties x the chance of one x 0.76 scored
Props are void on SportyBet when the player doesn't play, so minutes are
his minutes when he plays.
"""

import re
from math import exp, log
from typing import Any, Dict, List, Optional, Tuple

import player_props as pp

# SportyBet competition -> our box-score league
BB_LEAGUES = {"International · Euroleague": "Euroleague", "International · Eurocup": "Eurocup",
              "USA · NBA": "NBA", "USA · WNBA": "WNBA"}
FB_LEAGUES = {"England · Premier League": "Premier League", "Spain · LaLiga": "LaLiga",
              "Germany · Bundesliga": "Bundesliga", "Italy · Serie A": "Serie A", "France · Ligue 1": "Ligue 1"}

# Basketball stat -> (column in a game row, SportyBet over/under market, "X+" market, name)
BB_STATS = {"pts": (5, "921", "768", "points"), "reb": (6, "923", "772", "rebounds"),
            "ast": (7, "922", "770", "assists"), "tpm": (8, "924", "774", "3-pointers")}
BB_BY_MARKET = {m: stat for stat, (_, ou, plus, _) in BB_STATS.items() for m in (ou, plus)}
# How much a stat follows the game's expected points (points fully; the rest less)
GAME_EXPONENT = {"pts": 1.0, "reb": 0.5, "ast": 0.7, "tpm": 0.8}
DEFAULT_DISPERSION = {"pts": 12.0, "reb": 10.0, "ast": 8.0, "tpm": 6.0}
MIN_PROB, MAX_PROB = 0.5, 0.985
SCORER_MARKET = "40"
PEN_SCORED = 0.76
PRIOR_NPXG90 = 0.12         # a typical outfield player's non-penalty xG per 90
PRIOR_90S = 6.0             # pull of that prior, in full games
MIN_APPS = 4


def _rows_to_games(rows: List[List], col: int) -> List[pp.Game]:
    """Box-score rows (oldest first) as player_props games, newest first."""
    return [pp.Game(r[0], float(r[4]), {"x": float(r[col])}, bool(r[9])) for r in reversed(rows)]


def league_rates(players: Dict[str, Dict], col: int) -> float:
    """The league's rate per minute for a stat (the prior for players with few games)."""
    mins = sum(float(r[4]) for p in players.values() for r in p["games"][-40:])
    count = sum(float(r[col]) for p in players.values() for r in p["games"][-40:])
    return count / mins if mins else 0.3


def _sb_name(desc: str) -> str:
    """ "Ntilikina, Frank total points (incl. overtime)" / "Warren, T.J. 13+" / "Stach, Anton (Leeds United)" -> the name."""
    s = re.sub(r"\s+(total\s.*|\d+\+)$", "", desc or "", flags=re.I)
    return re.sub(r"\s*\([^)]*\)\s*$", "", s).strip()


def bb_offers(ev: Dict) -> List[Dict[str, Any]]:
    """Every basketball player line on a SportyBet event page."""
    out = []
    for m in ev.get("markets") or []:
        mid = str(m.get("id"))
        stat = BB_BY_MARKET.get(mid)
        if not stat:
            continue
        spec = m.get("specifier") or ""
        for o in m.get("outcomes") or []:
            if not o.get("isActive", 1):
                continue
            try:
                odds = float(o.get("odds"))
            except (TypeError, ValueError):
                continue
            if odds <= 1.0:
                continue
            oid = str(o.get("id"))
            if mid == BB_STATS[stat][1]:            # over/under X.5
                line = float(re.search(r"total=([\d.]+)", spec).group(1)) if "total=" in spec else None
                if line is None or oid not in ("12", "13"):
                    continue
                name, side = _sb_name(m.get("desc") or ""), "O" if oid == "12" else "U"
                code = f"{side}{line:g}"
            else:                                  # "X+"
                mm = re.match(r"^(.*?)\s+(\d+)\+$", o.get("desc") or "")
                if not mm:
                    continue
                name, n = mm.group(1).strip(), int(mm.group(2))
                line, side, code = n - 0.5, "O", f"{n}+"
            out.append({"stat": stat, "player": name, "key": pp.name_key(name), "line": line, "side": side,
                        "code": code, "odds": odds,
                        "sb": {"eventId": str(ev.get("eventId")), "marketId": mid, "specifier": spec, "outcomeId": oid}})
    return out


def bb_price(ev: Dict, players: Dict[str, Dict], team_factor: Dict[str, float],
             dispersion: Optional[Dict[str, float]] = None, priors: Optional[Dict[str, float]] = None) -> List[Dict]:
    """Our chance of each of SportyBet's player lines on an event.
    team_factor: {team name: tonight's expected points / its usual}."""
    dispersion = {**DEFAULT_DISPERSION, **(dispersion or {})}
    out, cache = [], {}
    for o in bb_offers(ev):
        p = players.get(o["key"])
        if not p:
            continue
        col = BB_STATS[o["stat"]][0]
        ck = (o["key"], o["stat"])
        if ck not in cache:
            prior = (priors or {}).get(o["stat"]) or league_rates(players, col)
            factor = max(0.85, min(1.15, team_factor.get(p["team"], 1.0))) ** GAME_EXPONENT[o["stat"]]
            cache[ck] = pp.project(_rows_to_games(p["games"], col), "x", prior, dispersion[o["stat"]], factor)
        proj = cache[ck]
        if proj is None:
            continue
        prob = proj.p_over(o["line"]) if o["side"] == "O" else 1.0 - proj.p_over(o["line"])
        if not MIN_PROB <= prob <= MAX_PROB:
            continue
        stat_name = BB_STATS[o["stat"]][3]
        label = (f"{p['name']} {o['code']} {stat_name}" if o["code"].endswith("+")
                 else f"{p['name']} {'over' if o['side'] == 'O' else 'under'} {o['line']:g} {stat_name}")
        out.append({"market": f"bb_player_{o['stat']}", "family": "bb_player", "market_name": f"Player {stat_name}",
                    "code": f"{o['key']}|{o['code']}", "label": label, "prob": round(prob, 4), "odds": o["odds"],
                    "edge": round(prob * o["odds"] - 1, 3), "sb": o["sb"], "player": p["name"],
                    "expected": round(proj.mean, 1), "minutes": round(proj.minutes, 1), "games": proj.games})
    return out


# ── Football: anytime goalscorer ─────────────────────────────────────────────

def scorer_offers(ev: Dict) -> List[Dict[str, Any]]:
    out = []
    for m in ev.get("markets") or []:
        if str(m.get("id")) != SCORER_MARKET:
            continue
        for o in m.get("outcomes") or []:
            oid = str(o.get("id"))
            if not oid.startswith("sr:player:") or not o.get("isActive", 1):
                continue
            try:
                odds = float(o.get("odds"))
            except (TypeError, ValueError):
                continue
            if odds <= 1.0:
                continue
            desc = o.get("desc") or ""
            team = (re.search(r"\(([^)]*)\)\s*$", desc) or [None, ""])[1]
            name = _sb_name(desc)
            out.append({"player": name, "key": pp.name_key(name), "team": team, "odds": odds,
                        "sb": {"eventId": str(ev.get("eventId")), "marketId": SCORER_MARKET,
                               "specifier": m.get("specifier") or "", "outcomeId": oid}})
    return out


def team_goals(p_scores: Optional[float]) -> Optional[float]:
    """A team's expected goals from its chance of scoring at least once (Poisson)."""
    if p_scores is None or not 0 < p_scores < 1:
        return None
    return -log(1 - p_scores)


def team_xg_per_game(players: Dict[str, Dict], team: str, last: int = 20) -> Optional[float]:
    """His team's usual xG per game, from its players' rows."""
    by_day: Dict[str, float] = {}
    for p in players.values():
        for r in p["games"]:
            if r[1] == team:
                by_day[r[0]] = by_day.get(r[0], 0.0) + float(r[7])
    days = sorted(by_day)[-last:]
    return sum(by_day[d] for d in days) / len(days) if len(days) >= 5 else None


def scorer_lambda(p: Dict, team_exp: Optional[float], team_usual: Optional[float],
                  team_pens_per_game: float = 0.25) -> Optional[Tuple[float, Dict]]:
    """His expected goals tonight (conditional on playing), and how."""
    rows = [r for r in p["games"] if float(r[4]) > 0]
    if len(rows) < MIN_APPS:
        return None
    recent = rows[-30:]
    w = [0.5 ** (i / 12) for i in range(len(recent))][::-1]           # newest counts most
    mins = sum(wi * float(r[4]) for wi, r in zip(w, recent))
    games_w = sum(w)
    exp_min = min(90.0, mins / games_w)
    # Penalties: his xG from penalties isn't open play; ~0.76 xG each
    pens = sum(wi * float(r[10] if len(r) > 10 else 0) for wi, r in zip(w, recent))
    npxg = sum(wi * float(r[7]) for wi, r in zip(w, recent)) - PEN_SCORED * pens
    npg = sum(wi * float(r[5]) for wi, r in zip(w, recent)) - PEN_SCORED * pens
    per90 = (0.7 * max(0.0, npxg) + 0.3 * max(0.0, npg) + PRIOR_NPXG90 * PRIOR_90S) / (mins / 90 + PRIOR_90S)
    factor = 1.0
    if team_exp and team_usual:
        factor = max(0.6, min(1.6, team_exp / team_usual))
    lam = exp_min / 90 * per90 * factor
    # His share of his team's penalties (per game he plays)
    pen_share = min(1.0, pens / games_w / max(0.05, team_pens_per_game))
    lam += team_pens_per_game * factor * pen_share * PEN_SCORED
    return lam, {"minutes": round(exp_min), "npxg90": round(per90, 3), "factor": round(factor, 2),
                 "pen_share": round(pen_share, 2), "apps": len(rows)}


def scorer_price(ev: Dict, players: Dict[str, Dict], team_exp: Dict[str, Optional[float]]) -> List[Dict]:
    """Our chance of each anytime goalscorer SportyBet lists. team_exp:
    {SportyBet team name: our expected goals for it tonight}."""
    out = []
    for o in scorer_offers(ev):
        p = players.get(o["key"])
        if not p:
            continue
        usual = team_xg_per_game(players, p["team"])
        exp_goals = team_exp.get(o["team"])
        got = scorer_lambda(p, exp_goals, usual)
        if not got:
            continue
        lam, detail = got
        prob = 1 - exp(-lam)
        out.append({"market": "anytime_scorer", "family": "player", "market_name": "Anytime Goalscorer",
                    "code": o["key"], "label": f"{p['name']} to score", "prob": round(prob, 4), "odds": o["odds"],
                    "edge": round(prob * o["odds"] - 1, 3), "sb": o["sb"], "player": p["name"], **detail})
    return sorted(out, key=lambda x: -x["prob"])
