"""
Bet slip → bookmaker booking code.

The slip holds bookmaker-neutral selections — one per match, in the model's
own market vocabulary (market "double_chance", code "1X"). Converting to a
platform means finding the match there and translating the market:

    {"home", "away", "date", "market", "code", "label"?, "prob"?}

SportyBet prices football through Betradar, whose market and outcome ids are
fixed (1X2 is market 1, outcomes 1/2/3), so a selection maps to an id
triple without guessing; only the event has to be found by name and date.
Markets without a mapping in sportybet_ids are reported as unsupported rather than
approximated.
"""

import re
import time
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
_OVER, _UNDER = "12", "13"
_YES = "74"
# Over/under markets: model market → SportyBet market (lines go in the specifier)
_LINE_MARKETS = {"goals_ou": "18", "corners_ou": "166", "cards_ou": "139",
                 "home_goals_ou": "19", "away_goals_ou": "20",
                 "home_corners_ou": "169", "away_corners_ou": "170",
                 # Shots: SportyBet's own ids (sportybet.SHOT_MARKETS), confirmed by name (VERIFIED)
                 "shots_ou": "900394", "sot_ou": "900393", "home_shots_ou": "900552",
                 "away_shots_ou": "900553", "home_sot_ou": "900546", "away_sot_ou": "900547"}
# Markets SportyBet offers on some matches and lines only: a pick is bookable
# only when its match's SportyBet listing carries that very line
LISTED_ONLY = {"shots_ou", "sot_ou", "home_shots_ou", "away_shots_ou", "home_sot_ou", "away_sot_ou"}
_OU_CODE = re.compile(r"^([OU])(\d{1,2})5$")  # O25 → over 2.5, U105 → under 10.5
_HANDICAP_CODE = re.compile(r"^([HA])([+-])(\d)\.5$")  # H-1.5 → home -1.5
_DC_GOALS_CODE = re.compile(r"^(1X|X2|12)&([OU])(\d)5$")  # 1X&O15 → home or draw & over 1.5
# Double chance & total (Betradar 547) outcome ids
_DC_GOALS_OUTCOMES = {("1X", "U"): "794", ("1X", "O"): "796", ("12", "U"): "798",
                      ("12", "O"): "800", ("X2", "U"): "802", ("X2", "O"): "804"}
_OTHER_FIXED: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("clean_sheet", "CS-H"): ("31", _YES), ("clean_sheet", "CS-A"): ("32", _YES),
    ("win_to_nil", "WTN-H"): ("33", _YES), ("win_to_nil", "WTN-A"): ("34", _YES),
    ("corners_1x2", "CR-1"): ("162", "1"), ("corners_1x2", "CR-X"): ("162", "2"),
    ("corners_1x2", "CR-2"): ("162", "3"),
}

# ── Markets confirmed against SportyBet itself ─────────────────────────────
# These ids come from Betradar's numbering but couldn't be checked against
# its documentation, so a pick in one is only booked once SportyBet's own
# match page confirms it (main._link_sportybet_events, every 30 minutes):
# the guessed id's label — team names read as "home"/"away" — must match,
# else the one market whose label does is used; outcome labels, where the
# page has them, must match too. Unconfirmed markets are never booked.
_NOT_TOTAL = r"1st|first|2nd|second|half|handicap|asian|exact|range|odd|even|most|1x2|race|next|min|&|\band\b"
VERIFIED: Dict[str, Dict[str, Any]] = {
    "corners_ou": {"guess": "166", "name": "corners", "must": [r"corner"], "not": _NOT_TOTAL + r"|home|away|team",
                   "outcomes": {_OVER: r"over", _UNDER: r"under"}},
    "cards_ou": {"guess": "139", "name": "bookings", "must": [r"booking|card"],
                 "not": _NOT_TOTAL + r"|home|away|team|point|sending|red|player",
                 "outcomes": {_OVER: r"over", _UNDER: r"under"}},
    "home_goals_ou": {"guess": "19", "name": "home team goals", "must": [r"\bhome\b", r"total|over|under|o/u|goals"],
                      "not": _NOT_TOTAL + r"|away|corner|card|booking|clean|nil|both|btts|win|shot",
                      "outcomes": {_OVER: r"over", _UNDER: r"under"}},
    "away_goals_ou": {"guess": "20", "name": "away team goals", "must": [r"\baway\b", r"total|over|under|o/u|goals"],
                      "not": _NOT_TOTAL + r"|\bhome\b|corner|card|booking|clean|nil|both|btts|win|shot",
                      "outcomes": {_OVER: r"over", _UNDER: r"under"}},
    "home_corners_ou": {"guess": "169", "name": "home team corners", "must": [r"\bhome\b", r"corner"],
                        "not": _NOT_TOTAL + r"|away", "outcomes": {_OVER: r"over", _UNDER: r"under"}},
    "away_corners_ou": {"guess": "170", "name": "away team corners", "must": [r"\baway\b", r"corner"],
                        "not": _NOT_TOTAL + r"|\bhome\b", "outcomes": {_OVER: r"over", _UNDER: r"under"}},
    "clean_sheet:H": {"guess": "31", "name": "home clean sheet", "must": [r"\bhome\b", r"clean sheet"],
                      "not": r"away|1st|first|half|win|&", "outcomes": {_YES: r"yes"}},
    "clean_sheet:A": {"guess": "32", "name": "away clean sheet", "must": [r"\baway\b", r"clean sheet"],
                      "not": r"\bhome\b|1st|first|half|win|&", "outcomes": {_YES: r"yes"}},
    "win_to_nil:H": {"guess": "33", "name": "home win to nil", "must": [r"\bhome\b", r"win to nil"],
                     "not": r"away|1st|first|half", "outcomes": {_YES: r"yes"}},
    "win_to_nil:A": {"guess": "34", "name": "away win to nil", "must": [r"\baway\b", r"win to nil"],
                     "not": r"\bhome\b|1st|first|half", "outcomes": {_YES: r"yes"}},
    "handicap": {"guess": "16", "name": "handicap", "must": [r"handicap"],
                 "not": r"corner|card|booking|1st|first|half|european|3.?way|1x2|draw|range|min",
                 "outcomes": {"1714": r"\bhome\b|^1\b", "1715": r"\baway\b|^2\b"}},
    "dc_goals": {"guess": "547", "name": "double chance & goals", "must": [r"double chance", r"total|over|under|goals"],
                 "not": r"1st|first|half|corner|card|booking|both|btts",
                 "outcomes": {oid: (r"(home\W*(or\W*)?draw|draw\W*(or\W*)?home|\b1x\b|\b1/x\b)" if dc == "1X" else
                                    r"(draw\W*(or\W*)?away|away\W*(or\W*)?draw|\bx2\b|\bx/2\b)" if dc == "X2" else
                                    r"(home\W*(or\W*)?away|away\W*(or\W*)?home|\b12\b|\b1/2\b)")
                                   + r".*" + ("over" if side == "O" else "under")
                              for (dc, side), oid in _DC_GOALS_OUTCOMES.items()}},
    "corners_1x2": {"guess": "162", "name": "most corners", "must": [r"corner", r"1x2|most|race|winner|result"],
                    "not": r"1st|first|half|handicap|total|over|under|odd|even|range|next|min",
                    "outcomes": {"1": r"\bhome\b|^1$", "2": r"draw|^x$", "3": r"\baway\b|^2$"}},
    # Shots and shots on target: SportyBet's "Shots Over/Under", "Home Team
    # Shots on Target Over/Under", … (ids 9003xx/9005xx), on some matches only
    **{kind: {"guess": _LINE_MARKETS[kind], "name": name, "must": must,
              "not": _NOT_TOTAL + extra + r"|player|anytime|blocked|woodwork|post|\bbar\b|foul|offside"
                     r"|outside|inside|\bbox\b",
              "outcomes": {_OVER: r"over", _UNDER: r"under"}}
       for kind, name, must, extra in (
           ("shots_ou", "total shots", [r"\bshots?\b"], r"|target|\bhome\b|\baway\b|team"),
           ("sot_ou", "total shots on target", [r"shots? on target"], r"|\bhome\b|\baway\b|team"),
           ("home_shots_ou", "home team shots", [r"\bhome\b", r"\bshots?\b"], r"|target|\baway\b"),
           ("away_shots_ou", "away team shots", [r"\baway\b", r"\bshots?\b"], r"|target|\bhome\b"),
           ("home_sot_ou", "home team shots on target", [r"\bhome\b", r"shots? on target"], r"|\baway\b"),
           ("away_sot_ou", "away team shots on target", [r"\baway\b", r"shots? on target"], r"|\bhome\b"))},
}
_KIND_BY_GUESS = {spec["guess"]: kind for kind, spec in VERIFIED.items()}


def verified_kind(market: str, code: str) -> Optional[str]:
    """Which VERIFIED entry a selection needs, or None for trusted markets."""
    if market in ("clean_sheet", "win_to_nil"):
        return f"{market}:{'H' if code.endswith('-H') else 'A'}"
    return market if market in VERIFIED else None


def _normalise_label(label: str, home: str = "", away: str = "") -> str:
    text = (label or "").lower()
    text = text.replace("{$competitor1}", "home").replace("{$competitor2}", "away")
    for name, word in ((home, "home"), (away, "away")):
        if name and len(name) > 2:
            text = text.replace(name.lower(), word)
    return text


def _label_matches(kind: str, label: str) -> bool:
    spec = VERIFIED[kind]
    return (all(re.search(rx, label) for rx in spec["must"])
            and not re.search(spec["not"], label))


def label_ok(market_id: str, label: Optional[str]) -> bool:
    """False when SportyBet's label for an unconfirmed market id doesn't
    match (so a price under that id isn't trusted)."""
    kind = _KIND_BY_GUESS.get(str(market_id))
    return kind is None or bool(label and _label_matches(kind, _normalise_label(label)))


def resolve_markets(details: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Confirm each VERIFIED market against one SportyBet match page:
    details = {"home", "away", "markets": {id: {"label", "norm"?, "outcomes": {id: label}}}}
    (sportybet.market_details gives the markets, labels already normalised).
    Returns {kind: {"id", "label", "ok", "why"}} — only ok entries are booked."""
    details = details or {}
    home, away = details.get("home") or "", details.get("away") or ""
    markets = {str(k): v for k, v in (details.get("markets") or {}).items()}
    labels = {mid: m.get("norm") or _normalise_label(m.get("label") or "", home, away) for mid, m in markets.items()}
    out: Dict[str, Dict[str, Any]] = {}
    for kind, spec in VERIFIED.items():
        mid = spec["guess"] if spec["guess"] in labels and _label_matches(kind, labels[spec["guess"]]) else None
        if mid is None:
            matches = [m for m, lab in labels.items() if _label_matches(kind, lab)]
            mid = matches[0] if len(matches) == 1 else None
            if mid is None:
                out[kind] = {"id": None, "label": markets.get(spec["guess"], {}).get("label"), "ok": False,
                             "why": "several markets match" if matches else "not on this match's page"}
                continue
        outcomes = markets[mid].get("outcomes") or {}
        bad = [oid for oid, rx in spec["outcomes"].items()
               if oid in outcomes and outcomes[oid]
               and not re.search(rx, _normalise_label(outcomes[oid], home, away))]
        missing = [oid for oid in spec["outcomes"] if oid not in outcomes]
        # Outcomes decide which side is booked: every one we'd book must be confirmed
        ok = not bad and not missing
        out[kind] = {"id": mid, "label": markets[mid].get("label"), "ok": ok,
                     "why": None if ok else f"outcome labels don't match ({', '.join(bad + missing)})"}
    return out


MAX_SELECTIONS = 30


def sportybet_ids(market: str, code: str) -> Optional[Dict[str, str]]:
    """SportyBet {marketId, specifier, outcomeId} for a model selection, or
    None. For VERIFIED markets the id is the guess: booking swaps in the
    confirmed one (see to_sportybet)."""
    code = code or ""
    fixed = _FIXED.get((market, code)) or _OTHER_FIXED.get((market, code))
    if fixed:
        return {"marketId": fixed[0], "specifier": "", "outcomeId": fixed[1]}
    if market in _LINE_MARKETS:
        m = _OU_CODE.match(code)
        if m:  # only half lines exist as plain totals
            return {"marketId": _LINE_MARKETS[market], "specifier": f"total={int(m.group(2))}.5",
                    "outcomeId": _OVER if m.group(1) == "O" else _UNDER}
    if market == "handicap":
        m = _HANDICAP_CODE.match(code)
        if m:  # the specifier is the home side's handicap
            side, sign, n = m.groups()
            home_hcp = f"{'-' if (side == 'H') == (sign == '-') else ''}{n}.5"
            return {"marketId": "16", "specifier": f"hcp={home_hcp}", "outcomeId": "1714" if side == "H" else "1715"}
    if market == "dc_goals":
        m = _DC_GOALS_CODE.match(code)
        if m:
            dc, side, n = m.groups()
            return {"marketId": "547", "specifier": f"total={n}.5", "outcomeId": _DC_GOALS_OUTCOMES[(dc, side)]}
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
        if not label_ok(ids["marketId"], m.get("desc")):
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


_SB_FIELDS = {"eventId": re.compile(r"^sr:[a-z_]+:\d{1,12}$"), "marketId": re.compile(r"^\d{1,6}$"),
              # Player props' outcomes are long and have colons ("sr:player:1021607",
              # "pre:playerprops:73262972:607880:9"), as are their specifiers
              "specifier": re.compile(r"^[\w=.|+:-]{0,120}$"), "outcomeId": re.compile(r"^[\w:.+-]{1,80}$")}


def raw_ids(s: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """A selection carrying SportyBet's own ids (a leg kept from a booking code
    we don't model), validated; None when it doesn't carry them."""
    sb = s.get("sb")
    if not isinstance(sb, dict):
        return None
    out = {k: str(sb.get(k) or "") for k in _SB_FIELDS}
    if not all(rx.match(out[k]) for k, rx in _SB_FIELDS.items()):
        raise ValueError("invalid SportyBet selection")
    return out


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
        raw_ids(s)
        by_match[selection_key(s)] = s
    if not by_match:
        raise ValueError("the slip is empty")
    if len(by_match) > MAX_SELECTIONS:
        raise ValueError(f"at most {MAX_SELECTIONS} selections")
    return list(by_match.values())


def _start_seconds(event: Optional[Dict]) -> Optional[float]:
    """An event's kick-off (SportyBet's estimateStartTime, in ms) in seconds;
    None when unknown (missing, or 0)."""
    try:
        ms = int((event or {})["estimateStartTime"])
    except (KeyError, TypeError, ValueError):
        return None
    return ms / 1000 if ms > 0 else None


# SportyBet refuses a whole code when one selection's market isn't open on
# its match ("19000 invalid event data, no market there")
MAX_SHARE_TRIES = 24


def _no_market(e: Exception) -> bool:
    text = str(e).lower()
    return "bizcode 19000" in text or "no market there" in text


async def _share_leaving_out(post_share: Callable[[List[Dict]], Awaitable[Dict[str, Any]]],
                             to_book: List[Tuple[int, Dict[str, str], Dict]]):
    """Share the selections; if SportyBet refuses them over a market it hasn't
    got, find the selections at fault by halving and share the rest. Returns
    (share or None when every selection was at fault, the ones left out).
    Other errors, or more than MAX_SHARE_TRIES requests, raise."""
    try:
        return await post_share([ids for _, ids, _ in to_book]), []
    except Exception as e:
        if not _no_market(e) or len(to_book) == 1:
            if _no_market(e):
                return None, list(to_book)
            raise
        first = e
    tries = [1]
    refused: List[Tuple[int, Dict[str, str], Dict]] = []

    async def probe(group: List[Tuple[int, Dict[str, str], Dict]]) -> None:
        if not group:
            return
        tries[0] += 1
        if tries[0] > MAX_SHARE_TRIES:
            raise first
        try:
            await post_share([ids for _, ids, _ in group])
        except Exception as e:
            if not _no_market(e):
                raise
            if len(group) == 1:
                refused.append(group[0])
                return
            await probe(group[:len(group) // 2])
            await probe(group[len(group) // 2:])

    half = len(to_book) // 2
    await probe(to_book[:half])
    await probe(to_book[half:])
    rest = [t for t in to_book if t not in refused]
    if not rest:
        return None, refused
    print(f"[Booking] SportyBet has no market for {len(refused)} of {len(to_book)} selections: left out")
    return await post_share([ids for _, ids, _ in rest]), refused


def name_platform(result: Dict[str, Any]) -> Dict[str, Any]:
    """Messages name SportyBet; on football.com (the same platform, the same
    listing) the code is football.com's."""
    if result.get("platform") == "football_com":
        for k in ("error",):
            if result.get(k):
                result[k] = result[k].replace("SportyBet's match list", "the match list").replace("SportyBet", "football.com")
        for p in result.get("picks") or []:
            if p.get("reason"):
                p["reason"] = p["reason"].replace("SportyBet", "football.com")
    return result


async def to_sportybet(
    selections: List[Dict[str, Any]],
    fetch_events: Callable[[str], Awaitable[List[Dict]]],
    find_event: Callable[[str, str, List[Dict]], Optional[Dict]],
    post_share: Callable[[List[Dict]], Awaitable[Dict[str, Any]]],
    linked: Optional[Callable[[Dict[str, Any]], Optional[Dict]]] = None,
    market_map: Optional[Dict[str, Dict[str, Any]]] = None,
    now: Optional[float] = None,
    platform: str = "sportybet",
) -> Dict[str, Any]:
    """
    Book the slip on SportyBet. Every selection comes back with a status:
    "booked"; "matched" (found, but no code was made); "unavailable" (SportyBet refused it — suspended or started);
    "unsupported" (a market SportyBet codes can't take from us); or
    "not_found" (match not listed on SportyBet around that date).

    `linked` returns the SportyBet event a selection was matched to ahead of
    time (see main._link_sportybet_events); only unlinked selections need
    the event listing, so a fully linked slip is a single request.

    `market_map` is resolve_markets' result: picks in a VERIFIED market are
    only booked once it's confirmed, under the confirmed id.
    """
    picks: List[Dict[str, Any]] = []
    to_book: List[Tuple[int, Dict[str, str], Dict]] = []  # (pick index, ids, event)
    events_by_date: Dict[str, List[Dict]] = {}
    listing_down = False  # SportyBet always lists football: an empty list means we couldn't load it

    for s in selections:
        pick = {"key": selection_key(s), "home": s["home"], "away": s["away"],
                "label": s.get("label") or s["code"], "odds": None}
        raw = raw_ids(s)
        if raw:  # booked exactly as SportyBet had it
            to_book.append((len(picks), raw, {"eventId": raw["eventId"], "markets": []}))
            picks.append({**pick, "status": "matched"})
            continue
        ids = sportybet_ids(s["market"], s["code"])
        if not ids:
            picks.append({**pick, "status": "unsupported",
                          "reason": "SportyBet codes can't include this market"})
            continue
        kind = verified_kind(s["market"], s["code"])
        if kind:
            confirmed = (market_map or {}).get(kind) or {}
            if not confirmed.get("ok"):
                picks.append({**pick, "status": "unsupported",
                              "reason": f"We haven't confirmed SportyBet's {VERIFIED[kind]['name']} market yet"})
                continue
            ids = {**ids, "marketId": str(confirmed["id"])}
        event = linked(s) if linked else None
        if event is None:
            if s["date"] not in events_by_date:
                events_by_date[s["date"]] = await fetch_events(s["date"])
            if not events_by_date[s["date"]]:
                listing_down = True
                picks.append({**pick, "status": "not_found",
                              "reason": "Couldn't load SportyBet's match list"})
                continue
            event = find_event(s["home"], s["away"], events_by_date[s["date"]])
        event_id = event and str(event.get("eventId") or "")
        if not event_id:
            picks.append({**pick, "status": "not_found", "reason": "SportyBet hasn't listed this match (yet)"})
            continue
        # Kicked off: SportyBet won't take it (and may refuse the whole code),
        # so it's left out and the rest still get booked
        start = _start_seconds(event)
        if start is not None and start <= (time.time() if now is None else now):
            picks.append({**pick, "status": "unavailable", "reason": "Already started"})
            continue
        to_book.append((len(picks), {"eventId": event_id, **ids}, event))
        picks.append({**pick, "status": "matched"})  # found; "booked" once SportyBet accepts it

    result: Dict[str, Any] = {"platform": platform, "code": None, "share_url": None,
                              "picks": picks, "total_odds": None, "error": None}
    if not to_book:
        result["error"] = ("Couldn't reach SportyBet's match list from our server. Try again in a few minutes."
                           if listing_down else
                           "Every match on this slip has already started."
                           if picks and all(p.get("reason") == "Already started" for p in picks) else
                           "SportyBet hasn't listed these matches yet. It adds most internationals "
                           "a few days before kick-off, and skips some smaller ones."
                           if any(p["status"] == "not_found" for p in picks)
                           else "None of these picks can go in a SportyBet code.")
        return result

    try:
        share, refused = await _share_leaving_out(post_share, to_book)
    except Exception as e:
        print(f"[Booking] SportyBet share failed: {e}")
        # SportyBet's own reason (bizCode and message) helps more than a generic line
        said = str(e).split("bizCode", 1)[-1].strip() if "bizCode" in str(e) else ""
        result["error"] = ("SportyBet didn't return a booking code. Try again in a minute."
                           + (f" (SportyBet said: {said[:120]})" if said else ""))
        return result
    for i, _, _ in refused:
        picks[i].update(status="unavailable", reason="SportyBet doesn't offer this market on this match right now")
    to_book = [t for t in to_book if t not in refused]
    if share is None:
        result["error"] = "SportyBet doesn't offer any of these picks' markets on these matches right now."
        return result

    booked_odds: List[Optional[float]] = []
    for i, ids, event in to_book:
        key = (ids["eventId"], ids["marketId"], ids["outcomeId"])
        if key in share.get("unavailable", set()):
            picks[i].update(status="unavailable", reason="SportyBet isn't offering this pick right now")
            continue
        picks[i]["status"] = "booked"
        picks[i]["odds"] = share.get("odds", {}).get(key) or _event_odds(event, ids)
        booked_odds.append(picks[i]["odds"])

    if not booked_odds:
        result["error"] = "SportyBet rejected every pick (suspended or already started)."
        return result
    total = 1.0
    for o in booked_odds:
        total *= o or 1.0
    result.update(code=share["code"], share_url=share.get("url"),
                  total_odds=round(total, 2) if all(booked_odds) else None)
    return result
