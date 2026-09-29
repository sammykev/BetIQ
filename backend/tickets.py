"""
Booking codes as tickets: every code a signed-in account generates is kept
with its legs, and each leg is settled from the match's final score (and
corners / bookings for those markets) once matchday.py has the result.

A leg is "won", "lost", "void" (match postponed or abandoned), "pending"
(not played yet), or "unknown" (a market we don't model, kept from a pasted
code: shown, never settled here). A ticket is lost as soon as one leg
loses, won when every leg is won (void legs count at odds 1, as bookmakers
settle them), otherwise pending.
"""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

MAX_TICKETS = 200  # per account

_LINE = re.compile(r"^([OU])(\d{1,2})(\d)$")        # O25 → over 2.5, U105 → under 10.5
_HCP = re.compile(r"^([HA])([+-])(\d(?:\.\d)?)$")      # H-1.5
_DC = {"1X": {"H", "D"}, "X2": {"D", "A"}, "12": {"H", "A"}}
# market → (result stat, team: 0 home / 1 away / None the match total)
_SHOT_MARKETS = {"shots_ou": ("shots", None), "sot_ou": ("sot", None), "home_shots_ou": ("shots", 0),
                 "away_shots_ou": ("shots", 1), "home_sot_ou": ("sot", 0), "away_sot_ou": ("sot", 1)}


def _line(code: str) -> Optional[tuple]:
    m = _LINE.match(code or "")
    return (m.group(1) == "O", float(f"{m.group(2)}.{m.group(3)}")) if m else None


def _over_under(code: str, total: Optional[float]) -> Optional[str]:
    parsed = _line(code)
    if not parsed or total is None:
        return None
    over, line = parsed
    return "won" if (total > line if over else total < line) else "lost"


def grade_leg(market: str, code: str, result: Optional[Dict]) -> str:
    """Settle one leg from a matchday result ({"status", "hg", "ag",
    "corners", "bookings", "shots", "sot", "aet"}); basketball legs (bb_…)
    from a basketball result (basketball_markets.settle)."""
    if market.startswith("bb_"):
        import basketball_markets
        return basketball_markets.settle(market, code, result)
    if not result:
        return "pending"
    status = result.get("status")
    if status == "postponed":
        return "void"
    if status != "finished" or result.get("aet") or result.get("hg") is None or result.get("ag") is None:
        return "pending"
    hg, ag = int(result["hg"]), int(result["ag"])
    outcome = "H" if hg > ag else "A" if hg < ag else "D"
    corners, bookings = result.get("corners"), result.get("bookings")
    verdict: Optional[str] = None
    if market == "1x2":
        verdict = {"1": "H", "X": "D", "2": "A"}.get(code) == outcome
    elif market == "double_chance":
        verdict = outcome in _DC.get(code, set()) if code in _DC else None
    elif market == "goals_ou":
        return _over_under(code, hg + ag) or "pending"
    elif market == "home_goals_ou":
        return _over_under(code, hg) or "pending"
    elif market == "away_goals_ou":
        return _over_under(code, ag) or "pending"
    elif market == "btts":
        verdict = (hg > 0 and ag > 0) == (code == "BTTS-Y") if code in ("BTTS-Y", "BTTS-N") else None
    elif market == "clean_sheet":
        verdict = {"CS-H": ag == 0, "CS-A": hg == 0}.get(code)
    elif market == "win_to_nil":
        verdict = {"WTN-H": hg > ag and ag == 0, "WTN-A": ag > hg and hg == 0}.get(code)
    elif market == "handicap":
        m = _HCP.match(code or "")
        if m:
            margin = (hg - ag) if m.group(1) == "H" else (ag - hg)
            adj = margin + (float(m.group(3)) if m.group(2) == "+" else -float(m.group(3)))
            verdict = None if adj == 0 else adj > 0
    elif market == "dc_goals":
        dc, _, line = (code or "").partition("&")
        ou = _over_under(line, hg + ag)
        verdict = None if dc not in _DC or ou is None else (outcome in _DC[dc] and ou == "won")
    elif market in ("corners_ou", "cards_ou"):
        counts = corners if market == "corners_ou" else bookings
        return _over_under(code, sum(counts)) if isinstance(counts, list) else "pending"
    elif market in ("home_corners_ou", "away_corners_ou"):
        if isinstance(corners, list):
            return _over_under(code, corners[0 if market.startswith("home") else 1]) or "pending"
        return "pending"
    elif market in _SHOT_MARKETS:
        stat, side = _SHOT_MARKETS[market]
        pair = result.get(stat)
        if not isinstance(pair, list):
            return "pending"
        return _over_under(code, sum(pair) if side is None else pair[side]) or "pending"
    elif market == "corners_1x2":
        if isinstance(corners, list):
            most = "1" if corners[0] > corners[1] else "2" if corners[1] > corners[0] else "X"
            verdict = code == f"CR-{most}"
    if verdict is None:
        return "pending"
    return "won" if verdict else "lost"


def ticket_status(legs: List[Dict]) -> str:
    states = [l.get("status", "pending") for l in legs]
    if "lost" in states:
        return "lost"
    if states and all(s in ("won", "void") for s in states):
        return "void" if all(s == "void" for s in states) else "won"
    if states and all(s in ("won", "void", "unknown") for s in states):
        return "open"  # everything we can settle won; the rest is up to SportyBet
    return "pending"


def new_ticket(code: str, selections: List[Dict], picks: List[Dict], source: str,
               share_url: Optional[str], total_odds: Optional[float], created_at: str) -> Dict[str, Any]:
    """A ticket from a booking: `selections` (booking_slip.validate's shape)
    and `picks` (to_sportybet's per-selection outcome, same order) — only
    the legs SportyBet booked, at the odds it gave."""
    legs = []
    for s, p in zip(selections, picks):
        if p.get("status") != "booked":
            continue
        leg = {k: s.get(k) for k in ("home", "away", "date", "time", "league", "market", "marketName",
                                    "code", "label", "prob")}
        # Legs kept from a pasted code (booked by SportyBet's own ids) are in
        # markets we may not model: they're shown, but can't be settled here
        leg["odds"] = p.get("odds")
        leg["status"] = "unknown" if s.get("market") == "sportybet" else "pending"
        if str(s.get("market") or "").startswith("bb_"):
            # Basketball: settled from the SportyBet event it was booked on
            leg["sport"] = "basketball"
            leg["event_id"] = (s.get("sb") or {}).get("eventId")
        legs.append(leg)
    return {"code": code, "created_at": created_at, "source": source, "share_url": share_url,
            "legs": legs, "total_odds": total_odds, "status": "pending", "settled_at": None}


def settle(ticket: Dict, result_for) -> bool:
    """Grade a ticket's pending legs; `result_for(leg)` → the matchday
    result or None. True if anything changed."""
    changed = False
    for leg in ticket.get("legs") or []:
        if leg.get("status") not in (None, "pending"):
            continue
        status = grade_leg(leg.get("market", ""), leg.get("code", ""), result_for(leg))
        if status != "pending":
            leg["status"] = status
            changed = True
    status = ticket_status(ticket.get("legs") or [])
    if status != ticket.get("status"):
        ticket["status"] = status
        if status in ("won", "lost", "void"):
            ticket["settled_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        changed = True
    return changed


def summary(tickets: List[Dict]) -> Dict[str, Any]:
    """An account's record from its booked codes: tickets and legs won,
    flat-stake return (1 unit a ticket at the odds SportyBet booked), the
    current streak, the best win, and pick accuracy by market."""
    won = sum(1 for t in tickets if t.get("status") == "won")
    lost = sum(1 for t in tickets if t.get("status") == "lost")
    void = sum(1 for t in tickets if t.get("status") == "void")
    legs = [l for t in tickets for l in t.get("legs") or []]
    legs_won = sum(1 for l in legs if l.get("status") == "won")
    legs_lost = sum(1 for l in legs if l.get("status") == "lost")
    settled = sorted((t for t in tickets if t.get("status") in ("won", "lost")),
                     key=lambda t: t.get("settled_at") or t.get("created_at") or "", reverse=True)
    priced = [t for t in settled if isinstance(t.get("total_odds"), (int, float)) and t["total_odds"] > 1]
    units = sum(t["total_odds"] - 1 if t["status"] == "won" else -1.0 for t in priced)
    streak, streak_type = 0, None
    for t in settled:
        if streak_type is None:
            streak_type = t["status"]
        if t["status"] != streak_type:
            break
        streak += 1
    best = max((t for t in priced if t["status"] == "won"), key=lambda t: t["total_odds"], default=None)
    by_market: Dict[str, List[int]] = {}
    for l in legs:
        if l.get("status") in ("won", "lost"):
            row = by_market.setdefault(l.get("marketName") or l.get("market") or "Other", [0, 0])
            row[0 if l["status"] == "won" else 1] += 1
    return {"tickets": len(tickets), "won": won, "lost": lost, "void": void,
            "pending": sum(1 for t in tickets if t.get("status") in ("pending", "open")),
            "hit_rate": round(won / (won + lost), 3) if won + lost else None,
            "legs_won": legs_won, "legs_lost": legs_lost,
            "leg_hit_rate": round(legs_won / (legs_won + legs_lost), 3) if legs_won + legs_lost else None,
            "units": round(units, 2), "roi": round(units / len(priced), 3) if priced else None,
            "streak": streak, "streak_type": streak_type,
            "best_win": {"code": best["code"], "odds": best["total_odds"]} if best else None,
            "by_market": sorted(({"market": m, "won": w, "lost": lo, "hit_rate": round(w / (w + lo), 3)}
                                 for m, (w, lo) in by_market.items()), key=lambda r: -(r["won"] + r["lost"]))}