"""
Bet slip → bookmaker booking code.

The slip holds bookmaker-neutral selections — one per match, in the model's
own market vocabulary (market "double_chance", code "1X"). Converting to a
platform means finding the match there and translating the market:

    {"home", "away", "date", "market", "code", "label"?, "prob"?}

SportyBet prices football through Betradar, whose market and outcome ids are
fixed (1X2 is market 1, outcomes 1/2/3), so a selection maps to an id
triple without guessing; only the event has to be found by name and date.
Markets outside SPORTYBET_MARKETS are reported as unsupported rather than
approximated.
"""

import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

# (model market id, option code) → (SportyBet market id, outcome id); specifier added for lines
_FIXED: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("1x2", "1"): ("1", "1"), ("1x2", "X"): ("1", "2"), ("1x2", "2"): ("1", "3"),
    ("double_chance", "1X"): ("10", "9"), ("double_chance", "12"): ("10", "10"),
    ("double_chance", "X2"): ("10", "11"), ("double_chance", "2X"): ("10", "11"),
    ("btts", "BTTS-Y"): ("29", "74"), ("btts", "BTTS-N"): ("29", "76"),
    ("draw_no_bet", "DNB-H"): ("11", "4"), ("draw_no_bet", "DNB-A"): ("11", "5"),
    ("goals_odd_even", "GOE-ODD"): ("26", "70"), ("goals_odd_even", "GOE-EVEN"): ("26", "72"),
    ("half_time", "HT1"): ("60", "1"), ("half_time", "HTX"): ("60", "2"), ("half_time", "HT2"): ("60", "3"),
}
_TOTAL_MARKET, _OVER, _UNDER = "18", "12", "13"
_OU_CODE = re.compile(r"^([OU])(\d)(\d)$")  # O25 → over 2.5

MAX_SELECTIONS = 30


def sportybet_ids(market: str, code: str) -> Optional[Dict[str, str]]:
    """SportyBet {marketId, specifier, outcomeId} for a model selection, or None."""
    fixed = _FIXED.get((market, code))
    if fixed:
        return {"marketId": fixed[0], "specifier": "", "outcomeId": fixed[1]}
    if market == "goals_ou":
        m = _OU_CODE.match(code or "")
        if m and m.group(3) == "5":  # only half-goal lines exist as plain totals
            return {"marketId": _TOTAL_MARKET, "specifier": f"total={m.group(2)}.5",
                    "outcomeId": _OVER if m.group(1) == "O" else _UNDER}
    return None


def selection_key(s: Dict[str, Any]) -> str:
    return f"{s.get('home', '')}:{s.get('away', '')}:{s.get('date', '')}"


def _event_odds(event: Dict, ids: Dict[str, str]) -> Optional[float]:
    """The live price for this outcome, when the event listing includes the market."""
    for m in event.get("markets") or []:
        if str(m.get("id")) != ids["marketId"]:
            continue
        if (m.get("specifier") or "") != ids["specifier"]:
            continue
        for o in m.get("outcomes") or []:
            if str(o.get("id")) == ids["outcomeId"]:
                if not o.get("isActive", 1):
                    return None
                try:
                    return float(o.get("odds"))
                except (TypeError, ValueError):
                    return None
    return None


def validate(selections: Any) -> List[Dict[str, Any]]:
    """Well-formed selections, at most one per match (the last one wins)."""
    if not isinstance(selections, list):
        raise ValueError("selections must be a list")
    by_match: Dict[str, Dict[str, Any]] = {}
    for s in selections:
        if not isinstance(s, dict):
            raise ValueError("each selection must be an object")
        for field in ("home", "away", "date", "market", "code"):
            if not isinstance(s.get(field), str) or not s[field].strip():
                raise ValueError(f"selection is missing '{field}'")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s["date"]):
            raise ValueError("date must be YYYY-MM-DD")
        by_match[selection_key(s)] = s
    if not by_match:
        raise ValueError("the slip is empty")
    if len(by_match) > MAX_SELECTIONS:
        raise ValueError(f"at most {MAX_SELECTIONS} selections")
    return list(by_match.values())


async def to_sportybet(
    selections: List[Dict[str, Any]],
    fetch_events: Callable[[str], Awaitable[List[Dict]]],
    find_event: Callable[[str, str, List[Dict]], Optional[Dict]],
    post_share: Callable[[List[Dict]], Awaitable[Optional[Dict[str, str]]]],
) -> Dict[str, Any]:
    """
    Book the slip on SportyBet. Every selection comes back with a status:
    "booked", "unsupported" (market SportyBet can't take from us) or
    "not_found" (match not listed on SportyBet for that date).
    """
    picks: List[Dict[str, Any]] = []
    booked: List[Dict[str, str]] = []
    events_by_date: Dict[str, List[Dict]] = {}

    for s in selections:
        pick = {"key": selection_key(s), "home": s["home"], "away": s["away"],
                "label": s.get("label") or s["code"], "odds": None}
        ids = sportybet_ids(s["market"], s["code"])
        if not ids:
            picks.append({**pick, "status": "unsupported",
                          "reason": "This market can't be booked on SportyBet automatically"})
            continue
        if s["date"] not in events_by_date:
            events_by_date[s["date"]] = await fetch_events(s["date"])
        event = find_event(s["home"], s["away"], events_by_date[s["date"]])
        event_id = event and str(event.get("eventId") or event.get("id") or "")
        if not event_id:
            picks.append({**pick, "status": "not_found", "reason": "Match not found on SportyBet"})
            continue
        booked.append({"eventId": event_id, **ids})
        picks.append({**pick, "status": "booked", "odds": _event_odds(event, ids)})

    result: Dict[str, Any] = {"platform": "sportybet", "code": None, "share_url": None,
                              "picks": picks, "total_odds": None, "error": None}
    if not booked:
        result["error"] = "None of these picks could be booked on SportyBet."
        return result

    share = await post_share(booked)
    if not share or not share.get("code"):
        result["error"] = "SportyBet didn't return a booking code. Try again in a minute."
        return result

    odds = [p["odds"] for p in picks if p["status"] == "booked"]
    total = 1.0
    for o in odds:
        total *= o if o else 1.0
    result.update(code=share["code"], share_url=share.get("url"),
                  total_odds=round(total, 2) if all(odds) else None)
    return result
