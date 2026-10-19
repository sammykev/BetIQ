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
from datetime import datetime, timedelta, timezone
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


def settle_prop(market: str, code: str, result: Optional[Dict]) -> str:
    """A player prop from his box score ({"status", "value"}): basketball
    codes "<player>|O14.5", "|U14.5" or "|9+"; a goalscorer (code: the
    player) won with a goal. Void when he didn't play, as on SportyBet."""
    if not result:
        return "pending"
    if result.get("status") == "void":
        return "void"
    if result.get("status") != "finished" or result.get("value") is None:
        return "pending"
    v = float(result["value"])
    if market == "anytime_scorer":
        return "won" if v >= 1 else "lost"
    line = code.split("|", 1)[-1]
    m = re.match(r"^(\d+)\+$", line)
    if m:
        return "won" if v >= int(m.group(1)) else "lost"
    m = re.match(r"^([OU])(\d+(?:\.\d+)?)$", line)
    if not m:
        return "void"
    n = float(m.group(2))
    if v == n:
        return "void"
    return "won" if (v > n) == (m.group(1) == "O") else "lost"


def grade_leg(market: str, code: str, result: Optional[Dict]) -> str:
    """Settle one leg from a matchday result ({"status", "hg", "ag",
    "corners", "bookings", "shots", "sot", "aet"}); basketball legs (bb_…)
    from a basketball result (basketball_markets.settle)."""
    if market.startswith("bb_player_") or market == "anytime_scorer":
        return settle_prop(market, code, result)
    if market.startswith("bb_"):
        import basketball_markets
        return basketball_markets.settle(market, code, result)
    if market.startswith("rk_"):
        import racket_markets
        return racket_markets.settle(market, code, result)
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
        elif str(s.get("market") or "").startswith("rk_"):
            # Tennis and table tennis: the same (either sport's results are searched)
            leg["sport"] = s.get("sport") if s.get("sport") in ("tennis", "table_tennis") else "racket"
            leg["event_id"] = (s.get("sb") or {}).get("eventId")
        legs.append(leg)
    return {"code": code, "created_at": created_at, "source": source, "share_url": share_url,
            "legs": legs, "total_odds": total_odds, "status": "pending", "settled_at": None}


def _football(market: str) -> bool:
    return not (market.startswith(("bb_", "rk_")) or market == "anytime_scorer")


def _score_mark(result: Optional[Dict]) -> Optional[str]:
    """The football score a leg was graded on ("2-1"), to notice a correction."""
    if not result or result.get("hg") is None or result.get("ag") is None:
        return None
    return f"{result['hg']}-{result['ag']}" + ("aet" if result.get("aet") else "")


EXPIRE_DAYS = 7     # a leg still ungradeable this long after kick-off: "unknown" (no source has its result or stat)


def expired(leg: Dict, now: Optional[datetime] = None) -> bool:
    """Kicked off more than EXPIRE_DAYS ago (by its date and time, UTC)."""
    try:
        ko = datetime.fromisoformat(f"{leg['date']}T{(leg.get('time') or '23:59')[:5]}:00+00:00")
    except (KeyError, TypeError, ValueError):
        return False
    return (now or datetime.now(timezone.utc)) - ko > timedelta(days=EXPIRE_DAYS)


def settle(ticket: Dict, result_for, recheck: bool = False) -> bool:
    """Grade a ticket's pending legs; `result_for(leg)` → the matchday
    result or None. With `recheck`, football legs already graded are graded
    again when their match's score was corrected since (matchday.corrects).
    True if anything changed."""
    changed = False
    for leg in ticket.get("legs") or []:
        market = leg.get("market", "")
        if leg.get("status") not in (None, "pending"):
            if not (recheck and _football(market)):
                continue
            res = result_for(leg)
            if not (res and res.get("corrected")) or leg.get("graded_on") == _score_mark(res):
                continue
            status = grade_leg(market, leg.get("code", ""), res)
            if status != "pending":
                if status != leg.get("status"):
                    leg["regraded"] = {"was": leg.get("status"), "score": _score_mark(res)}
                leg["status"], leg["graded_on"] = status, _score_mark(res)
                changed = True
            continue
        res = result_for(leg)
        status = grade_leg(market, leg.get("code", ""), res)
        if status == "pending" and expired(leg):
            status = "unknown"
            leg["unsettled"] = f"no result{' or stat' if res else ''} for it {EXPIRE_DAYS} days after kick-off"
        if status != "pending":
            leg["status"] = status
            if _football(market) and _score_mark(res):
                leg["graded_on"] = _score_mark(res)
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
    # Units over time: the running total after each settle day (oldest first)
    timeline: List[Dict[str, Any]] = []
    run = 0.0
    for t in sorted(priced, key=lambda t: t.get("settled_at") or t.get("created_at") or ""):
        run += t["total_odds"] - 1 if t["status"] == "won" else -1.0
        day = str(t.get("settled_at") or t.get("created_at") or "")[:10]
        if timeline and timeline[-1]["date"] == day:
            timeline[-1]["units"] = round(run, 2)
        else:
            timeline.append({"date": day, "units": round(run, 2)})
    # What the model gave the settled legs it priced, against how often they won
    rated = [l for l in legs if l.get("status") in ("won", "lost") and isinstance(l.get("prob"), (int, float))]
    said = ({"legs": len(rated), "model_said": round(sum(l["prob"] for l in rated) / len(rated), 3),
             "came_in": round(sum(1 for l in rated if l["status"] == "won") / len(rated), 3)} if rated else None)
    return {"tickets": len(tickets), "won": won, "lost": lost, "void": void,
            "pending": sum(1 for t in tickets if t.get("status") in ("pending", "open")),
            "hit_rate": round(won / (won + lost), 3) if won + lost else None,
            "legs_won": legs_won, "legs_lost": legs_lost,
            "leg_hit_rate": round(legs_won / (legs_won + legs_lost), 3) if legs_won + legs_lost else None,
            "units": round(units, 2), "roi": round(units / len(priced), 3) if priced else None,
            "streak": streak, "streak_type": streak_type,
            "best_win": {"code": best["code"], "odds": best["total_odds"]} if best else None,
            "by_market": sorted(({"market": m, "won": w, "lost": lo, "hit_rate": round(w / (w + lo), 3)}
                                 for m, (w, lo) in by_market.items()), key=lambda r: -(r["won"] + r["lost"])),
            "timeline": timeline[-TIMELINE_DAYS:], "said": said,
            "by_source": _groups(priced, lambda t: SOURCE_NAMES.get(t.get("source") or "", "Other")),
            "by_odds": _groups(priced, _odds_band, order=[b for _, b in ODDS_BANDS])}


TIMELINE_DAYS = 120
SOURCE_NAMES = {"daily": "Daily odds", "optimizer": "Optimizer", "slip": "Bet slip", "code_check": "Code check"}
ODDS_BANDS = [(3, "Under 3x"), (10, "3x–10x"), (30, "10x–30x"), (float("inf"), "30x+")]


def _odds_band(t: Dict) -> str:
    return next(name for top, name in ODDS_BANDS if t["total_odds"] < top)


def _groups(priced: List[Dict], key, order: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Settled, priced tickets grouped by `key`: won, lost, units and return at 1 a ticket."""
    rows: Dict[str, List[float]] = {}
    for t in priced:
        r = rows.setdefault(key(t), [0, 0, 0.0])
        r[0 if t["status"] == "won" else 1] += 1
        r[2] += t["total_odds"] - 1 if t["status"] == "won" else -1.0
    out = [{"name": k, "won": int(w), "lost": int(lo), "units": round(u, 2), "roi": round(u / (w + lo), 3)}
           for k, (w, lo, u) in rows.items()]
    return sorted(out, key=lambda r: order.index(r["name"]) if order else -(r["won"] + r["lost"]))

def leg_markets(items: List[Dict], source: str = "", part: str = "", since: str = "") -> List[Dict[str, Any]]:
    """Settled legs (won/lost) of `items` grouped by sport and market: picks,
    hit rate, what the model said (average prob) and the return at 1 a leg
    on the odds booked. source: a ticket source; part: quarter|half|set
    (part-of-the-match lines only); since: legs played on or after."""
    import market_ranking
    rows: Dict[tuple, List[Dict]] = {}
    for t in items:
        if source and (t.get("source") or "other") != source:
            continue
        for leg in t.get("legs") or []:
            m = str(leg.get("market") or "")
            if leg.get("status") not in ("won", "lost") or not m or m == "sportybet":
                continue
            if part and market_ranking.part_kind(m) != part:
                continue
            if since and (leg.get("date") or "") < since:
                continue
            sport = leg.get("sport") or ("basketball" if m.startswith("bb_") else "football")
            rows.setdefault((sport, m), []).append(leg)
    out = []
    for (sport, m), legs in rows.items():
        won = sum(1 for l in legs if l["status"] == "won")
        probs = [l["prob"] for l in legs if isinstance(l.get("prob"), (int, float))]
        priced = [l for l in legs if isinstance(l.get("odds"), (int, float)) and l["odds"] > 1]
        said = sum(probs) / len(probs) if probs else None
        out.append({"sport": sport, "market": m,
                    "name": market_ranking.line_name(sport, m) if m.startswith(("bb_", "rk_"))
                    else (legs[0].get("marketName") or m),
                    "n": len(legs), "won": won, "hit_rate": round(won / len(legs), 4),
                    "said": round(said, 4) if said is not None else None,
                    "gap": round(won / len(legs) - said, 4) if said is not None else None,
                    "roi": round(sum(l["odds"] - 1 if l["status"] == "won" else -1 for l in priced) / len(priced), 4)
                    if priced else None})
    return sorted(out, key=lambda r: (-r["n"], r["sport"], r["market"]))
