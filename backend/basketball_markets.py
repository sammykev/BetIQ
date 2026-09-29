"""
Basketball markets: SportyBet's (Betradar ids, from probe_basketball.py),
our probability for each of its lines, and how each settles.

Every pick is one SportyBet offers on the match, at its price, with its own
ids — so it can always go in a booking code (booking_slip.raw_ids).

Our codes (stored with tickets, used to settle):
    bb_winner          1 / 2                        winner, overtime included
    bb_1x2             1 / X / 2                    regulation
    bb_handicap        H-5.5 / A+5.5                overtime included
    bb_total           O160.5 / U160.5              overtime included
    bb_home_total …    O80.5 / U80.5                one team's points, overtime included
    bb_{h1,h2}_1x2 / _handicap / _total             a half (regulation)
    bb_h1_home_total / bb_h1_away_total
    bb_q{1-4}_1x2 / _handicap / _total / _home_total / _away_total
    bb_overtime        OT-Y / OT-N
"""

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from basketball_model import Match

# SportyBet market id -> (our market, part, kind, overtime counted)
#   kind: winner | 1x2 | handicap | total | home_total | away_total | overtime
MARKETS: Dict[str, Tuple[str, str, str, bool]] = {
    "219": ("bb_winner", "full", "winner", True),
    "1": ("bb_1x2", "full", "1x2", False),
    "223": ("bb_handicap", "full", "handicap", True),
    "225": ("bb_total", "full", "total", True),
    "227": ("bb_home_total", "full", "home_total", True),
    "228": ("bb_away_total", "full", "away_total", True),
    "220": ("bb_overtime", "full", "overtime", False),
    "60": ("bb_h1_1x2", "h1", "1x2", False),
    "66": ("bb_h1_handicap", "h1", "handicap", False),
    "68": ("bb_h1_total", "h1", "total", False),
    "69": ("bb_h1_home_total", "h1", "home_total", False),
    "70": ("bb_h1_away_total", "h1", "away_total", False),
    "83": ("bb_h2_1x2", "h2", "1x2", False),
    "88": ("bb_h2_handicap", "h2", "handicap", False),
    "90": ("bb_h2_total", "h2", "total", False),
    # Quarter markets: the quarter is in the specifier (quarternr=n)
    "235": ("1x2", "q", "1x2", False),
    "303": ("handicap", "q", "handicap", False),
    "236": ("total", "q", "total", False),
    "756": ("home_total", "q", "home_total", False),
    "757": ("away_total", "q", "away_total", False),
}
# Asked for in SportyBet's listing (every line of each)
LISTING_MARKETS = ",".join(MARKETS)

PART_NAMES = {"full": "", "h1": "1st half", "h2": "2nd half", "q1": "1st quarter", "q2": "2nd quarter",
              "q3": "3rd quarter", "q4": "4th quarter"}
KIND_NAMES = {"winner": "Winner", "1x2": "1X2", "handicap": "Handicap", "total": "Total Points",
              "home_total": "Home Team Points", "away_total": "Away Team Points", "overtime": "Overtime"}
_OUT = {"4": "1", "5": "2", "1": "1", "2": "X", "3": "2", "1714": "H", "1715": "A",
        "12": "O", "13": "U", "74": "Y", "76": "N"}


def _spec(spec: Optional[str]) -> Dict[str, str]:
    out = {}
    for part in (spec or "").split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _num(x: str) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _fmt(x: float) -> str:
    return f"{x:+g}" if x else "0"


def market_name(market: str) -> str:
    m = re.match(r"^bb_(?:(h1|h2|q[1-4])_)?(.+)$", market)
    if not m:
        return market
    part, kind = m.group(1) or "full", m.group(2)
    name = KIND_NAMES.get("winner" if kind == "winner" else kind, kind)
    return f"{PART_NAMES[part]} {name}".strip() if part != "full" else name


def offers(ev: Dict) -> List[Dict[str, Any]]:
    """Every line of our markets on a SportyBet event: our market and code,
    the SportyBet ids and its price."""
    out = []
    for m in ev.get("markets") or []:
        mid = str(m.get("id"))
        if mid not in MARKETS:
            continue
        market, part, kind, ot = MARKETS[mid]
        spec = m.get("specifier") or ""
        sp = _spec(spec)
        if part == "q":
            n = sp.get("quarternr")
            if n not in ("1", "2", "3", "4"):
                continue
            part = f"q{n}"
            market = f"bb_{part}_{market}"
        line = _num(sp.get("hcp") or sp.get("total") or "")
        if kind in ("handicap", "total", "home_total", "away_total") and line is None:
            continue
        for o in m.get("outcomes") or []:
            if not o.get("isActive", 1):
                continue
            oid, odds = str(o.get("id")), _num(o.get("odds"))
            side = _OUT.get(oid)
            if not side or not odds or odds <= 1.0:
                continue
            if kind == "handicap":
                # The specifier is the home side's handicap
                code = f"H{_fmt(line)}" if side == "H" else f"A{_fmt(-line)}"
            elif kind in ("total", "home_total", "away_total"):
                code = f"{side}{line:g}"
            elif kind == "overtime":
                code = f"OT-{side}"
            else:
                code = side
            out.append({"market": market, "part": part, "kind": kind, "ot": ot, "line": line, "code": code,
                        "odds": odds, "sb": {"eventId": str(ev.get("eventId")), "marketId": mid,
                                             "specifier": spec, "outcomeId": oid}})
    return out


def label(o: Dict, home: str, away: str) -> str:
    """How a pick reads: "Lakers -5.5", "Over 160.5 points", "1st quarter: Over 40.5"…"""
    kind, code, part = o["kind"], o["code"], o["part"]
    pre = f"{PART_NAMES[part]}: " if part != "full" else ""
    if kind == "winner":
        body = {"1": f"{home} win", "2": f"{away} win"}[code]
    elif kind == "1x2":
        # Regulation only (a tie goes to overtime): said so on the full game
        body = {"1": f"{home} win", "2": f"{away} win", "X": "Draw"}[code]
        if part == "full":
            body += " in regulation" if code != "X" else " after regulation"
    elif kind == "handicap":
        body = f"{home if code[0] == 'H' else away} {code[1:]}"
    elif kind == "total":
        body = f"{'Over' if code[0] == 'O' else 'Under'} {code[1:]} points"
    elif kind in ("home_total", "away_total"):
        team = home if kind == "home_total" else away
        body = f"{team} {'over' if code[0] == 'O' else 'under'} {code[1:]} points"
    else:
        body = "Overtime" if code == "OT-Y" else "No overtime"
    return pre + body


def probability(m: Match, o: Dict) -> Optional[float]:
    """Our chance of one offer."""
    kind, code, part, line = o["kind"], o["code"], o["part"], o["line"]
    if kind == "winner":
        return m.p_win(code == "1")
    if kind == "overtime":
        return m.p_tie() if code == "OT-Y" else 1.0 - m.p_tie()
    if part == "full" and o["ot"]:
        if kind == "handicap":
            return m.p_handicap_ot(code[0] == "H", float(code[1:]))
        if kind == "total":
            return m.p_total_ot(line, code[0] == "O")
        return m.p_team_total_ot(kind == "home_total", line, code[0] == "O")
    p = m.period(part)
    if kind == "1x2":
        return p.p_3way(code)
    if kind == "handicap":
        return p.p_handicap(code[0] == "H", float(code[1:]))
    if kind == "total":
        return p.p_total(line, code[0] == "O")
    if kind in ("home_total", "away_total"):
        return p.p_team_total(kind == "home_total", line, code[0] == "O")
    return None


# ── The market's own expectation: its main lines ─────────────────────────

def main_lines(offs: Iterable[Dict]) -> Dict[str, Any]:
    """SportyBet's winner prices and its most even handicap and total (the
    lines it sets as the middle): (line for home, home odds, away odds) and
    (line, over odds, under odds)."""
    winner: Dict[str, float] = {}
    hcp: Dict[float, Dict[str, float]] = {}
    tot: Dict[float, Dict[str, float]] = {}
    for o in offs:
        if o["market"] == "bb_winner":
            winner[o["code"]] = o["odds"]
        elif o["market"] == "bb_handicap":
            home_line = o["line"]
            hcp.setdefault(home_line, {})[o["code"][0]] = o["odds"]
        elif o["market"] == "bb_total":
            tot.setdefault(o["line"], {})[o["code"][0]] = o["odds"]

    def middle(lines: Dict[float, Dict[str, float]], a: str, b: str):
        pairs = [(ln, v[a], v[b]) for ln, v in lines.items() if a in v and b in v]
        return min(pairs, key=lambda t: abs(t[1] - t[2]), default=None)
    return {"winner": (winner["1"], winner["2"]) if {"1", "2"} <= set(winner) else None,
            "handicap": middle(hcp, "H", "A"), "total": middle(tot, "O", "U")}


# ── Settling a pick from the result ─────────────────────────────────────

def settle(market: str, code: str, result: Optional[Dict]) -> str:
    """won | lost | void | pending, from {"final": [h, a], "periods": [[h, a] x4],
    "ot": bool}. Half and quarter markets need the quarter scores."""
    if not result or result.get("status") != "finished" or not result.get("final"):
        return "pending"
    fh, fa = result["final"]
    periods = result.get("periods") or []
    m = re.match(r"^bb_(?:(h1|h2|q[1-4])_)?(.+)$", market)
    if not m:
        return "void"
    part, kind = m.group(1) or "full", m.group(2)
    if part == "full":
        reg = [sum(p[0] for p in periods), sum(p[1] for p in periods)] if len(periods) >= 4 else None
        if kind in ("winner", "handicap", "total", "home_total", "away_total"):
            h, a = fh, fa                              # overtime included
        elif kind in ("1x2", "overtime"):
            if reg is None:
                if result.get("ot") is None:
                    return "void"
                # No quarter scores: without overtime the final is regulation
                if result["ot"]:
                    return ("won" if code == "OT-Y" else "lost") if kind == "overtime" else (
                        "won" if code == "X" else "lost")
                h, a = fh, fa
            else:
                h, a = reg
        else:
            return "void"
    else:
        if len(periods) < 4:
            return "void"
        idx = {"h1": (0, 1), "h2": (2, 3), "q1": (0,), "q2": (1,), "q3": (2,), "q4": (3,)}[part]
        h, a = sum(periods[i][0] for i in idx), sum(periods[i][1] for i in idx)
    if kind == "overtime":
        tie = h == a
        return "won" if (code == "OT-Y") == tie else "lost"
    if kind in ("winner", "1x2"):
        got = "1" if h > a else "2" if a > h else "X"
        return "won" if got == code else "lost"
    if kind == "handicap":
        line = float(code[1:])
        diff = (h - a if code[0] == "H" else a - h) + line
        return "won" if diff > 0 else "lost" if diff < 0 else "void"
    value = {"total": h + a, "home_total": h, "away_total": a}.get(kind)
    if value is None:
        return "void"
    line = float(code[1:])
    if value == line:
        return "void"
    return "won" if (value > line) == (code[0] == "O") else "lost"
