"""
Daily odds: five slips a day at about 10x, 15x, 20x, 50x and 100x, built by the
optimizer only from the day's own matches (football and basketball: see
BB_FAMILIES for basketball's markets), and only from picks the model
rates 80% or more, none of them priced at 2.0 odds or more (a long price on
one leg means a riskier slip, whatever the model says).

Every pick being 80%+ doesn't make the slip 80%: the chances multiply, so a
10x slip lands far less often than any one of its picks. Each slip carries that
honest combined chance (the optimizer's win_chance), and every slip is kept
and graded, so the record shows how they really do.

A day is a Lagos day (midnight to midnight, WAT). Its slips are made just
before it starts (BUILD_EARLY_MINUTES before midnight), so they're out by
12am, and the server books each on SportyBet straight away: everyone gets the
same booking code with the slip. A slip SportyBet couldn't book then is tried
again every RETRY_MINUTES until its matches start.

When a slip is cut (a pick loses), a new slip for the same target is made
from that day's matches that haven't kicked off, and booked; the cut slip is
kept, and both count in the record. At most MAX_REMAKES a slip a day.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import tickets

TARGETS = (10, 15, 20, 50, 100)   # the 50x and 100x need many legs: some days have no slip for them
MIN_PROB = 0.80
MAX_LEG_ODDS = 2.0      # every pick priced under this
SPREAD = 0.07          # total odds within ±7% of the target
KEEP_DAYS = 120
TZ = timezone(timedelta(hours=1))   # Lagos (WAT: no daylight saving)
BUILD_EARLY_MINUTES = 10  # the next day's slips are made this long before midnight: out by 12am
RETRY_MINUTES = 15      # a slip without a booking code is tried again after this
MAX_REMAKES = 3         # new slips for one target in a day, after cuts

# Today's matches only. Tried in order until one gives a slip: the ones
# SportyBet lists first (so the slip can be booked), then all of today's
ATTEMPTS = ({"days": 1, "bookable_only": True}, {"days": 1, "bookable_only": False})

# Football, basketball, tennis and table tennis. Basketball's overtime lines are left out (a 1.03
# "no overtime" adds a leg for almost nothing), and player props on either
# sport until the walk-forward check has measured them
BB_FAMILIES = ("bb_winner", "bb_1x2", "bb_handicap", "bb_total", "bb_team_total", "bb_halves", "bb_quarters")
# Tennis and table tennis (where open to everyone): the whole-match lines
RK_FAMILIES = ("rk_winner", "rk_set_handicap", "rk_games_handicap", "rk_total_games", "rk_total_sets", "rk_to_win_set")

PICK_FIELDS = ("home", "away", "date", "time", "league", "market", "market_name", "code", "label",
               "prob", "odds", "odds_source", "bookable", "sport", "sb")


def request(target: float, attempt: Dict[str, Any]) -> Dict[str, Any]:
    """The optimizer request for one target."""
    return {"target_odds": target, "min_odds": round(target * (1 - SPREAD), 2),
            "max_odds": round(target * (1 + SPREAD), 2), "min_prob": MIN_PROB, "max_leg_odds": MAX_LEG_ODDS,
            "max_games": 30, "sport": "all", "bb_markets": list(BB_FAMILIES), "tn_markets": list(RK_FAMILIES),
            "tt_markets": list(RK_FAMILIES), "no_props": True, **attempt}


def _selection(p: Dict[str, Any]) -> Dict[str, Any]:
    """A stored pick as a booking selection (booking_slip.validate's shape);
    basketball's carries SportyBet's ids, which book it and settle it."""
    sel = {"home": p["home"], "away": p["away"], "date": p["date"], "time": p.get("time") or "",
           "league": p.get("league") or "", "market": p["market"], "marketName": p.get("market_name") or "",
           "code": p["code"], "label": p.get("label") or p["code"], "prob": p.get("prob")}
    if p.get("sb"):
        sel["sb"] = p["sb"]
    return sel


def slip(target: float, result: Dict[str, Any], attempt: Dict[str, Any]) -> Dict[str, Any]:
    """A stored slip from the optimizer's answer (or why there isn't one)."""
    if result.get("error") or not result.get("picks"):
        return {"target": target, "error": result.get("error") or "No slip today.", "picks": [], "status": "none"}
    picks = [{**{k: p.get(k) for k in PICK_FIELDS}, "status": "pending"} for p in result["picks"]]
    return {"target": target, "total_odds": result["total_odds"], "win_chance": result["win_chance"],
            "games": len(picks), "within_target": result.get("within_target", True),
            "days": attempt["days"], "bookable": all(p.get("bookable") for p in picks),
            "picks": picks, "status": "pending"}


def status(picks: List[Dict]) -> str:
    states = [p.get("status", "pending") for p in picks]
    if "lost" in states:
        return "lost"
    if states and all(s in ("won", "void") for s in states):
        return "void" if all(s == "void" for s in states) else "won"
    return "pending"


def grade(s: Dict[str, Any], result_for: Callable[[Dict], Optional[Dict]]) -> bool:
    """Settle a slip's picks from the results; True if anything changed."""
    if s.get("status") not in ("pending",):
        return False
    changed = False
    for p in s["picks"]:
        if p.get("status") != "pending":
            continue
        verdict = tickets.grade_leg(p.get("market", ""), p.get("code", ""), result_for(p))
        if verdict != "pending":
            p["status"] = verdict
            changed = True
    new = status(s["picks"])
    if new != s["status"]:
        s["status"] = new
        changed = True
    return changed


def _kickoff(p: Dict) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(f"{p['date']}T{p.get('time') or '23:59'}:00+00:00")
    except (KeyError, TypeError, ValueError):
        return None


def open_picks(s: Dict[str, Any], now: datetime) -> List[Dict]:
    """The slip's picks that can still go on a code: unsettled, not kicked off."""
    return [p for p in s.get("picks") or []
            if p.get("status") == "pending" and not ((k := _kickoff(p)) and k <= now)]


def selections(s: Dict[str, Any], now: datetime) -> List[Dict[str, Any]]:
    """The booking request (booking_slip.validate's shape) for a slip's open picks."""
    return [_selection(p) for p in open_picks(s, now)]


def booking(result: Dict[str, Any], sent: List[Dict[str, Any]], at: str) -> Dict[str, Any]:
    """What's stored of a booking attempt: the code, and which picks it holds."""
    picks = result.get("picks") or []
    on_code = [{"home": sel["home"], "away": sel["away"], "market": sel["market"], "code": sel["code"]}
               for sel, p in zip(sent, picks) if p.get("status") == "booked"]
    return {"code": result.get("code"), "share_url": result.get("share_url"),
            "total_odds": result.get("total_odds"), "booked": len(on_code), "of": len(sent),
            "on_code": on_code, "error": None if result.get("code") else (result.get("error") or "No code"),
            "at": at, "tries": 1}


def needs_booking(s: Dict[str, Any], now: datetime) -> bool:
    """A slip with no code yet, picks still to play, and no try in the last
    RETRY_MINUTES."""
    if s.get("status") != "pending" or (s.get("booking") or {}).get("code"):
        return False
    if not open_picks(s, now):
        return False
    last = (s.get("booking") or {}).get("at")
    try:
        return not last or (now - datetime.fromisoformat(last)).total_seconds() >= RETRY_MINUTES * 60
    except ValueError:
        return True


def today(now: Optional[datetime] = None) -> str:
    """The Lagos date: whose slips are shown as today's."""
    return (now or now_utc()).astimezone(TZ).date().isoformat()


def window(day: str) -> Tuple[datetime, datetime]:
    """The UTC start and end of a Lagos day: the kick-offs its slips take."""
    start = datetime.combine(date.fromisoformat(day), datetime.min.time(), TZ)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def days_to_run(now: datetime) -> List[str]:
    """Today, and tomorrow from BUILD_EARLY_MINUTES before midnight: the
    days whose slips should exist (and be booked, graded, remade) by now."""
    days = [today(now)]
    nxt = today(now + timedelta(minutes=BUILD_EARLY_MINUTES))
    return days + [nxt] if nxt != days[0] else days


def cut_by(s: Dict[str, Any]) -> Optional[str]:
    """The match that cut a slip (its first lost pick)."""
    p = next((p for p in s.get("picks") or [] if p.get("status") == "lost"), None)
    return f"{p['home']} v {p['away']}" if p else None


def needs_remake(doc: Dict[str, Any], s: Dict[str, Any]) -> bool:
    """A cut slip not yet dealt with, whose target hasn't had MAX_REMAKES."""
    if s.get("status") != "lost" or s.get("remade"):
        return False
    return sum(1 for c in doc.get("cut") or [] if c.get("target") == s.get("target")) < MAX_REMAKES


def remake(doc: Dict[str, Any], i: int, new: Dict[str, Any], at: str) -> bool:
    """Put `new` in place of the cut slip doc["slips"][i] (kept in doc["cut"]).
    Without a new slip (no matches left), the cut slip stays, marked. True if
    a new slip went in."""
    old = doc["slips"][i]
    if new.get("status") == "none":
        old["remade"] = "none"
        return False
    old["remade"] = at
    doc.setdefault("cut", []).append(old)
    new["replaces"] = {"code": (old.get("booking") or {}).get("code"), "cut_by": cut_by(old),
                       "total_odds": old.get("total_odds")}
    new["made_at"] = at
    doc["slips"][i] = new
    return True


def ticket_legs(s: Dict[str, Any]):
    """(selections, booked outcomes) for the slip's picks on its booking
    code: what tickets.new_ticket takes when an account tracks the slip."""
    on = {(p["home"], p["away"], p["market"], p["code"]) for p in (s.get("booking") or {}).get("on_code") or []}
    picks = [p for p in s.get("picks") or [] if (p["home"], p["away"], p["market"], p["code"]) in on]
    return [_selection(p) for p in picks], [{"status": "booked", "odds": p.get("odds")} for p in picks]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
