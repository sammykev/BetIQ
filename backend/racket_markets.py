"""
Tennis and table tennis markets: SportyBet's (Betradar ids, read by the
server's web probe), our chance of each of its lines, and how each settles.

One set of codes serves both sports. Tennis's words are used ("sets",
"games"); in table tennis a "set" is a game and a "game" is a point, and
the pages say so (WORDS).

Our codes (stored with tickets, used to settle):
    rk_winner          1 / 2
    rk_set_handicap    H-1.5 / A+1.5           sets (TT: games)
    rk_games_handicap  H-3.5 / A+3.5           games (TT: points), the whole match
    rk_total_games     O22.5 / U22.5           games (TT: points), the whole match
    rk_total_sets      O2.5 / U2.5             sets played
    rk_correct_score   2:0, 2:1, 3:1 …         sets (TT: games)
    rk_odd_even        ODD / EVEN              total games (TT: points)
    rk_home_set / rk_away_set   Y / N          that player wins at least one set
    rk_home_games / rk_away_games   O7.5 / U7.5   that player's games (TT: points)
    rk_s{n}_winner     1 / 2                   set n (TT: game n)
    rk_s{n}_total      O9.5 / U9.5             games in set n (TT: points in game n)
    rk_s{n}_handicap   H-1.5 / A+1.5           games in set n (TT: points in game n)
"""

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

# SportyBet market id -> (our market, kind); "set" markets carry the set in
# the specifier (setnr / gamenr). From the server's probe of SportyBet's
# tennis and table tennis match pages (web_probe.py).
MARKETS: Dict[str, Dict[str, Tuple[str, str]]] = {
    "tennis": {
        "186": ("rk_winner", "winner"),
        "188": ("rk_set_handicap", "set_handicap"),
        "187": ("rk_games_handicap", "games_handicap"),
        "189": ("rk_total_games", "total_games"),
        "190": ("rk_home_games", "home_games"),
        "191": ("rk_away_games", "away_games"),
        "192": ("rk_home_set", "home_set"),
        "193": ("rk_away_set", "away_set"),
        "199": ("rk_correct_score", "correct_score"),
        "202": ("winner", "s_winner"),          # setnr
        "203": ("handicap", "s_handicap"),      # setnr, hcp
        "204": ("total", "s_total"),            # setnr, total
    },
    "table_tennis": {
        "186": ("rk_winner", "winner"),
        "199": ("rk_correct_score", "correct_score"),
        "245": ("winner", "s_winner"),          # gamenr
    },
}
_OUT = {"4": "1", "5": "2", "1": "1", "2": "2", "1714": "H", "1715": "A", "12": "O", "13": "U",
        "74": "Y", "76": "N", "70": "ODD", "72": "EVEN"}

WORDS = {"tennis": {"set": "set", "sets": "sets", "game": "game", "games": "games", "Set": "Set", "Games": "Games"},
         "table_tennis": {"set": "game", "sets": "games", "game": "point", "games": "points", "Set": "Game",
                          "Games": "Points"}}
KIND_NAMES = {"winner": "Winner", "set_handicap": "{Set} Handicap", "games_handicap": "{Games} Handicap",
              "total_games": "Total {Games}", "total_sets": "Total {sets}", "correct_score": "Correct Score",
              "odd_even": "Odd/Even {Games}", "home_set": "Home to Win a {Set}", "away_set": "Away to Win a {Set}",
              "home_games": "Home {Games}", "away_games": "Away {Games}",
              "s_winner": "{n} {Set} Winner", "s_total": "{n} {Set} Total {Games}", "s_handicap": "{n} {Set} Handicap"}
_ORD = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th", 6: "6th", 7: "7th"}


def _spec(spec: Optional[str]) -> Dict[str, str]:
    out = {}
    for part in (spec or "").split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _num(x: Any) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _fmt(x: float) -> str:
    return f"{x:+g}" if x else "0"


def listing(sport: str) -> str:
    """The market ids asked for in SportyBet's listing (every line of each)."""
    return ",".join(MARKETS.get(sport) or {"186": None})


def split_market(market: str) -> Tuple[Optional[int], str]:
    """ "rk_s2_total" -> (2, "s_total"); "rk_winner" -> (None, "winner")."""
    m = re.match(r"^rk_s(\d)_(.+)$", market)
    if m:
        return int(m.group(1)), "s_" + m.group(2)
    return None, market[3:] if market.startswith("rk_") else market


def market_name(market: str, sport: str = "tennis") -> str:
    n, kind = split_market(market)
    w = WORDS.get(sport, WORDS["tennis"])
    name = KIND_NAMES.get(kind, kind)
    return name.format(n=_ORD.get(n, n) if n else "", **w).replace("  ", " ").strip()


def offers(ev: Dict, sport: str) -> List[Dict[str, Any]]:
    """Every line of our markets on a SportyBet event: our market and code,
    the SportyBet ids and its price."""
    table = MARKETS.get(sport) or {}
    out = []
    for m in ev.get("markets") or []:
        mid = str(m.get("id"))
        if mid not in table:
            continue
        market, kind = table[mid]
        spec = m.get("specifier") or ""
        sp = _spec(spec)
        n = None
        if kind.startswith("s_"):
            n = _num(sp.get("setnr") or sp.get("gamenr"))
            if not n or n > 7:
                continue
            n = int(n)
            market = f"rk_s{n}_{kind[2:]}"
        line = _num(sp.get("hcp") or sp.get("total"))
        if kind in ("set_handicap", "games_handicap", "total_games", "total_sets", "s_total", "s_handicap",
                    "home_games", "away_games") and line is None:
            continue
        for o in m.get("outcomes") or []:
            if not o.get("isActive", 1):
                continue
            oid, odds = str(o.get("id")), _num(o.get("odds"))
            if not odds or odds <= 1.0:
                continue
            if kind == "correct_score":
                cs = re.match(r"^\s*(\d+)\s*[:\-]\s*(\d+)\s*$", str(o.get("desc") or ""))
                if not cs:
                    continue
                code = f"{cs.group(1)}:{cs.group(2)}"
            else:
                side = _OUT.get(oid)
                if not side:
                    continue
                if kind in ("set_handicap", "games_handicap", "s_handicap"):
                    # The specifier is the home side's handicap
                    code = f"H{_fmt(line)}" if side == "H" else f"A{_fmt(-line)}"
                elif kind in ("total_games", "total_sets", "s_total", "home_games", "away_games"):
                    code = f"{side}{line:g}"
                else:
                    code = side
            out.append({"market": market, "kind": kind, "set": n, "line": line, "code": code, "odds": odds,
                        "sb": {"eventId": str(ev.get("eventId")), "marketId": mid, "specifier": spec, "outcomeId": oid}})
    return out


def label(o: Dict, home: str, away: str, sport: str = "tennis") -> str:
    """How a pick reads: "Sinner -1.5 sets", "Over 22.5 games", "2nd set: Alcaraz"…"""
    w = WORDS.get(sport, WORDS["tennis"])
    kind, code = o["kind"], o["code"]
    n = o.get("set")
    pre = f"{_ORD.get(n, n)} {w['set']}: " if n else ""
    if kind in ("winner", "s_winner"):
        body = f"{home if code == '1' else away}" + (" to win" if not n else "")
    elif kind in ("set_handicap", "games_handicap", "s_handicap"):
        unit = w["sets"] if kind == "set_handicap" else w["games"]
        body = f"{home if code[0] == 'H' else away} {code[1:]} {unit}"
    elif kind in ("total_games", "s_total"):
        body = f"{'Over' if code[0] == 'O' else 'Under'} {code[1:]} {w['games']}"
    elif kind == "total_sets":
        body = f"{'Over' if code[0] == 'O' else 'Under'} {code[1:]} {w['sets']}"
    elif kind in ("home_games", "away_games"):
        who = home if kind == "home_games" else away
        body = f"{who} {'over' if code[0] == 'O' else 'under'} {code[1:]} {w['games']}"
    elif kind == "correct_score":
        body = f"{w['Set']}s {code}"
    elif kind == "odd_even":
        body = f"{'Odd' if code == 'ODD' else 'Even'} total {w['games']}"
    elif kind in ("home_set", "away_set"):
        who = home if kind == "home_set" else away
        body = f"{who} {'to win' if code == 'Y' else 'not to win'} a {w['set']}"
    else:
        body = code
    return pre + body


def probability(md, o: Dict) -> Optional[float]:
    """Our chance of one offer, from a match distribution (tennis_model or
    table_tennis_model MatchDist: the same names)."""
    kind, code, n = o["kind"], o["code"], o.get("set")
    if kind == "winner":
        return md.p_win if code == "1" else 1 - md.p_win
    if kind == "set_handicap":
        line = float(code[1:])
        return sum(v for (a, b), v in md.sets.items() if ((a - b + line > 0) if code[0] == "H" else (b - a + line > 0)))
    if kind == "games_handicap":
        line = float(code[1:])
        return md.p_handicap(line) if code[0] == "H" else sum(v for d, v in md.games_diff.items() if -d + line > 0)
    if kind == "total_games":
        line = float(code[1:])
        return md.p_total_over(line) if code[0] == "O" else sum(v for t, v in md.total_games.items() if t < line)
    if kind == "total_sets":
        line = float(code[1:])
        over = sum(v for (a, b), v in md.sets.items() if a + b > line)
        return over if code[0] == "O" else sum(v for (a, b), v in md.sets.items() if a + b < line)
    if kind == "correct_score":
        a, b = (int(x) for x in code.split(":"))
        return md.sets.get((a, b), 0.0)
    if kind in ("home_games", "away_games"):
        line, i = float(code[1:]), 0 if kind == "home_games" else 1
        if not getattr(md, "games", None):
            return None
        return sum(v for g, v in md.games.items() if (g[i] > line if code[0] == "O" else g[i] < line))
    if kind == "odd_even":
        odd = sum(v for t, v in md.total_games.items() if t % 2)
        return odd if code == "ODD" else 1 - odd
    if kind in ("home_set", "away_set"):
        home = kind == "home_set"
        p = sum(v for (a, b), v in md.sets.items() if (a if home else b) > 0)
        return p if code == "Y" else 1 - p
    if kind.startswith("s_"):
        # Every set is priced as the first (a later set's server and form aren't known)
        fs = md.first_set
        if kind == "s_winner":
            p = sum(v for (a, b), v in fs.items() if a > b)
            return p if code == "1" else 1 - p
        line = float(code[1:])
        if kind == "s_total":
            return sum(v for (a, b), v in fs.items() if (a + b > line if code[0] == "O" else a + b < line))
        if kind == "s_handicap":
            return sum(v for (a, b), v in fs.items() if ((a - b + line > 0) if code[0] == "H" else (b - a + line > 0)))
    return None


def settle(market: str, code: str, result: Optional[Dict]) -> str:
    """won | lost | void | pending, from {"status": "finished", "sets": [h, a],
    "games": [[h, a] per set], "ret": bool}. A retirement voids every pick."""
    if not result or result.get("status") != "finished" or not result.get("sets"):
        return "pending"
    if result.get("ret"):
        return "void"
    sh, sa = result["sets"]
    games = result.get("games") or []
    n, kind = split_market(market)

    def ou(value: float) -> str:
        line = float(code[1:])
        if value == line:
            return "void"
        return "won" if (value > line) == (code[0] == "O") else "lost"

    def hcp(h: float, a: float) -> str:
        line = float(code[1:])
        d = (h - a if code[0] == "H" else a - h) + line
        return "won" if d > 0 else "lost" if d < 0 else "void"
    if kind == "winner":
        return "won" if (sh > sa) == (code == "1") else "lost"
    if kind == "set_handicap":
        return hcp(sh, sa)
    if kind == "total_sets":
        return ou(sh + sa)
    if kind == "correct_score":
        return "won" if code == f"{sh}:{sa}" else "lost"
    if kind in ("home_set", "away_set"):
        won_one = (sh if kind == "home_set" else sa) > 0
        return "won" if won_one == (code == "Y") else "lost"
    if kind in ("games_handicap", "total_games", "odd_even", "home_games", "away_games"):
        if len(games) != sh + sa:
            return "void"                         # set scores missing
        gh, ga = sum(g[0] for g in games), sum(g[1] for g in games)
        if kind in ("home_games", "away_games"):
            return ou(gh if kind == "home_games" else ga)
        if kind == "games_handicap":
            return hcp(gh, ga)
        if kind == "total_games":
            return ou(gh + ga)
        return "won" if ((gh + ga) % 2 == 1) == (code == "ODD") else "lost"
    if kind.startswith("s_"):
        if not n or len(games) < n:
            return "void" if sh + sa < (n or 0) else "pending" if not games else "void"
        g = games[n - 1]
        if kind == "s_winner":
            return "won" if (g[0] > g[1]) == (code == "1") else "lost"
        if kind == "s_total":
            return ou(g[0] + g[1])
        if kind == "s_handicap":
            return hcp(g[0], g[1])
    return "void"


def main_lines(offs: Iterable[Dict]) -> Dict[str, Any]:
    """SportyBet's winner prices and its most even total games line."""
    winner: Dict[str, float] = {}
    tot: Dict[float, Dict[str, float]] = {}
    for o in offs:
        if o["market"] == "rk_winner":
            winner[o["code"]] = o["odds"]
        elif o["market"] == "rk_total_games":
            tot.setdefault(o["line"], {})[o["code"][0]] = o["odds"]
    pairs = [(ln, v["O"], v["U"]) for ln, v in tot.items() if "O" in v and "U" in v]
    return {"winner": (winner["1"], winner["2"]) if {"1", "2"} <= set(winner) else None,
            "total": min(pairs, key=lambda t: abs(t[1] - t[2]), default=None)}
