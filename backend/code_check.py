"""
Check a SportyBet booking code against the model, then improve it.

Each leg (sportybet.parse_share) is matched to our prediction for that match
(the caller finds it) and to the model's own pick for that exact outcome, so
it gets our probability beside SportyBet's price. Then:

- a verdict per leg: strong (≥75%), fair (≥55%), risky, or not modelled;
- a better pick on the same match when the model rates one clearly higher;
- "same odds": the slip with the best chance of winning whose total stays
  near the code's (optimizer.optimize over the code's matches only);
- "safest": each match's likeliest pick at odds ≥ 1.15;
- three tickets to book (the "Refine" button on the site), each the
  highest odds whose chance of every leg winning is at least:
      safe 50%, conservative 25%, risky 12.5%
  digging into every market we price on each game, and keeping as many of
  the code's games as that chance allows (the rest are listed as left out).

Every leg gets a chance: our model's where we price the match (football,
basketball, tennis, table tennis: matched by SportyBet's event), else
SportyBet's own odds less their margin (ODDS_MARGIN / odds). A model chance
counts at no more than its price's (routers.optimizer.price_checked: where
we rated legs well over their price they came in far under). Every pick is
booked by SportyBet's own ids.
"""

import json
import math
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import booking_slip
from optimizer import _PICKS, Option, candidates, optimize_safe

STRONG, FAIR = 0.75, 0.55
BETTER_BY = 0.08          # a suggestion must beat the leg by this much
# Each ticket's least chance of every leg winning (Nov 2026: halved each step)
TICKETS = (("safe", 0.50), ("conservative", 0.25), ("risky", 0.125))
MIN_OPTION_PROB = 0.30    # options looked at on each game (a risky ticket takes longer prices)
ODDS_MARGIN = 0.95        # a leg we don't price: chance ≈ ODDS_MARGIN / its odds
PRICE_MARGIN = 1.05       # a model chance counts at no more than PRICE_MARGIN / its odds
MAX_TICKET_ODDS = 1e6
MAX_OPTIONS = 80          # per game on a ticket (a basketball game can have hundreds of lines)

# ── the refine settings (the "Refine settings" panel) ────────────────────
# How strict: each ticket's least chance of every leg winning
STRICTNESS = {"strict": (0.60, 0.30, 0.15), "balanced": (0.50, 0.25, 0.125), "loose": (0.40, 0.20, 0.10)}
# "Main markets only": the result, goals and winner lines everyone knows
MAIN_MARKETS = {"1x2", "double_chance", "dnb", "goals_ou", "btts", "handicap", "home_goals_ou", "away_goals_ou",
                "bb_winner", "bb_1x2", "bb_handicap", "bb_total", "winner", "rk_winner", "rk_set_handicap",
                "rk_games_handicap", "rk_total_games", "rk_total_sets"}
DEFAULTS = {"strictness": "balanced", "min_odds": None, "max_odds": None, "max_leg_odds": None,
            "keep_all": False, "keep_strong": None, "main_markets": False, "value": False}
# Value mode: only picks worth more than their price (our checked chance × odds
# at least VALUE_MIN_RETURN) in markets whose picks have made money at their
# odds, and few legs (SportyBet's ~5% is taken on every leg)
VALUE_MIN_RETURN = 1.01
VALUE_MAX_LEGS = 4


def bonus(picks: List[Dict[str, Any]]) -> float:
    """SportyBet's multi-bet bonus on a ticket (a share of the winnings), from
    multi_bonus.py's table; 0 without one."""
    try:
        import multi_bonus
        return multi_bonus.share(picks)
    except Exception:
        return 0.0


def expected_return(slip: Dict[str, Any]) -> Optional[float]:
    """What 1 staked returns on average: chance × odds, with the bonus."""
    if not slip.get("win_chance") or not slip.get("total_odds"):
        return None
    return round(slip["win_chance"] * slip["total_odds"] * (1 + bonus(slip.get("picks") or [])), 3)


def settings_of(raw: Any) -> Dict[str, Any]:
    """The refine settings from a request, each checked; anything missing or
    out of range falls back to the default."""
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS)
    if raw.get("strictness") in STRICTNESS:
        out["strictness"] = raw["strictness"]

    def num(k, lo, hi):
        try:
            v = float(raw.get(k))
        except (TypeError, ValueError):
            return None
        return v if lo <= v <= hi else None
    out["min_odds"] = num("min_odds", 1.01, 1e6)
    out["max_odds"] = num("max_odds", 1.01, 1e6)
    if out["min_odds"] and out["max_odds"] and out["min_odds"] > out["max_odds"]:
        out["min_odds"], out["max_odds"] = out["max_odds"], out["min_odds"]
    out["max_leg_odds"] = num("max_leg_odds", 1.05, 100)
    out["keep_strong"] = num("keep_strong", 0.5, 0.99)
    out["keep_all"] = raw.get("keep_all") is True
    out["main_markets"] = raw.get("main_markets") is True
    out["value"] = raw.get("value") is True
    return out


def chances_of(settings: Dict[str, Any]) -> List[tuple]:
    """[(ticket, least chance)] for the settings' strictness."""
    return list(zip(("safe", "conservative", "risky"), STRICTNESS[settings.get("strictness") or "balanced"]))


def _when(ms: Any) -> tuple:
    try:
        dt = datetime.fromtimestamp(int(ms) / 1000, timezone.utc)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")
    except (TypeError, ValueError):
        return "", ""


def model_pick(pred: Dict, sel: Dict, confirmed: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """The model's pick for exactly this SportyBet outcome, with its probability.
    `confirmed` = {booking_slip.VERIFIED kind: SportyBet market id} for markets
    SportyBet has confirmed; unconfirmed ones aren't matched (their guessed id
    could be another market)."""
    if pred.get("sport") in ("tennis", "table_tennis"):
        if sel["marketId"] == "186" and sel["outcomeId"] in ("4", "5"):
            home = sel["outcomeId"] == "4"
            prob = pred.get("p_home" if home else "p_away")
            if isinstance(prob, (int, float)):
                return {"market": "winner", "market_name": "Winner", "code": "1" if home else "2",
                        "label": f"{pred['home' if home else 'away']} to win", "prob": float(prob)}
        return None
    for market, market_name, code, label, prob_of in _PICKS:
        ids = booking_slip.sportybet_ids(market, code)
        if not ids:
            continue
        kind = booking_slip.verified_kind(market, code)
        if kind and kind not in confirmed:
            continue
        market_id = confirmed[kind] if kind else ids["marketId"]
        if (market_id, ids["specifier"], ids["outcomeId"]) != (sel["marketId"], sel["specifier"], sel["outcomeId"]):
            continue
        prob = prob_of(pred) if all(isinstance(pred.get(k), (int, float)) for k in ("p_home", "p_over25")) else None
        if not isinstance(prob, (int, float)):
            return None
        return {"market": market, "market_name": market_name, "code": code,
                "label": label.format(home=pred["home"], away=pred["away"]), "prob": float(prob)}
    return None


def _verdict(prob: Optional[float]) -> str:
    if prob is None:
        return "not_modelled"
    return "strong" if prob >= STRONG else "fair" if prob >= FAIR else "risky"


def odds_chance(odds: Optional[float]) -> Optional[float]:
    """A leg's chance from SportyBet's price alone, less its margin."""
    return min(0.97, ODDS_MARGIN / odds) if odds and odds > 1 else None


def _capped(prob: float, odds: Optional[float]) -> float:
    """A model chance counted at no more than its price says."""
    return min(prob, PRICE_MARGIN / odds) if odds and odds > 1 else prob


def _ids(sel: Dict) -> Dict[str, str]:
    return {k: sel[k] for k in ("eventId", "marketId", "specifier", "outcomeId")}


def _same_outcome(o: Option, sel: Dict) -> bool:
    sb = o.sb or {}
    return (str(sb.get("marketId")), str(sb.get("specifier") or ""), str(sb.get("outcomeId"))) == \
        (str(sel["marketId"]), str(sel.get("specifier") or ""), str(sel["outcomeId"]))


def _slip(picks: List[Dict[str, Any]]) -> Dict[str, Any]:
    known = [p["prob"] for p in picks if p.get("prob") is not None]
    odds = [p["odds"] for p in picks if p.get("odds")]
    return {"picks": picks, "games": len(picks), "total_odds": round(math.prod(odds), 2) if odds else None,
            "win_chance": round(math.prod(known), 4) if known else None,
            "from_odds": sum(1 for p in picks if p.get("odds_source") == "odds_only"),
            # older clients read these
            "unmodelled": sum(1 for p in picks if p.get("odds_source") == "odds_only"),
            "estimated_prices": sum(1 for p in picks if p.get("odds_source") == "estimated")}


def analyse(selections: List[Dict], find_pred: Callable[[Dict], Optional[Dict]],
            event_for: Callable[[Dict], Optional[Dict]], confirmed: Dict[str, str],
            allowed: Optional[Callable[[str, str], bool]] = None,
            find_other: Optional[Callable[[Dict], Optional[Dict]]] = None,
            adjust: Optional[Callable[[Option], Optional[Option]]] = None,
            settings: Optional[Dict[str, Any]] = None,
            market_paid: Optional[Callable[[str, str], bool]] = None) -> Dict[str, Any]:
    """Legs with verdicts and suggestions, the code as it stands, and the
    three tickets. `find_pred(sel)` → our football prediction for its match;
    `find_other(sel)` → our basketball / tennis / table tennis prediction for
    its SportyBet event; `event_for(pred)` → a football match's linked
    SportyBet event (prices); `adjust(option)` → the option as the optimizer
    would count it, or None to leave it out (held-out markets, the price check)."""
    settings = settings_of(settings)
    legs, groups, chances, games, kept_mine = [], [], [], [], 0
    for sel in selections:
        date, time = _when(sel.get("start"))
        odds = sel.get("odds")
        pred = find_pred(sel)
        other = find_other(sel) if find_other and not pred else None
        prob: Optional[float] = None
        options: List[Option] = []
        own_market, own_code = "sportybet", str(sel["outcomeId"])
        # The leg's sport from SportyBet (a kept football leg is graded from the score; others aren't)
        sport = {"sr:sport:1": "football", "sr:sport:2": "basketball", "sr:sport:5": "tennis",
                 "sr:sport:20": "table_tennis"}.get(str(sel.get("sport_id") or ""), "football" if not sel.get("sport_id") else "other")
        if pred and pred.get("sport") in (None, "football"):
            pick = model_pick(pred, sel, confirmed)
            options = candidates(pred, event_for(pred), MIN_OPTION_PROB, None, allowed)
            if pick:
                own_market, own_code = pick["market"], pick["code"]
                same = next((o for o in options if (o.market, o.code) == (own_market, own_code)), None)
                prob = same.prob if same else pick["prob"]       # through calibration where the optimizer has it
                options = [o for o in options if (o.market, o.code) != (own_market, own_code)]
        elif pred or other:
            import basketball_predictions
            import racket_predictions
            p = pred or other
            sport = p.get("sport") or "basketball"
            mod = basketball_predictions if sport == "basketball" else racket_predictions
            options = mod.options(p, MIN_OPTION_PROB) if (p.get("bb_markets") or p.get("rk_markets")) else []
            same = next((o for o in options if _same_outcome(o, sel)), None)
            if same:
                prob, own_market, own_code = same.prob, same.market, same.code
            elif not options:
                pick = model_pick(p, sel, confirmed)              # tennis winner from a bare prediction
                if pick:
                    prob, own_market, own_code = pick["prob"], pick["market"], pick["code"]
            options = [o for o in options if not _same_outcome(o, sel)]
            pred = p
        if adjust:
            options = [c for o in options for c in [adjust(o)] if c is not None]
        suggestable = options
        # On a ticket: SportyBet's price only (bookable), under the code's own names and kick-off
        options = [replace(o, home=sel["home"], away=sel["away"], date=date or o.date, time=time or o.time)
                   for o in options if o.odds_source == "sportybet"]
        chance = _capped(prob, odds) if prob is not None else odds_chance(odds)
        leg = {"home": sel["home"], "away": sel["away"], "date": date, "time": time,
               "league": (pred or {}).get("league_name") or sel.get("tournament", ""),
               "market": sel["market"], "pick": sel["outcome"], "odds": odds, "active": sel.get("active", True),
               "our_prob": round(prob, 3) if prob is not None else None,
               "chance": round(chance, 3) if chance is not None else None,
               "chance_from": "model" if prob is not None else "odds",
               "implied": round(1 / odds, 3) if odds else None,
               "value": round(prob * odds - 1, 3) if prob is not None and odds else None,
               "verdict": _verdict(prob), "matched": pred is not None, "suggestion": None, "sb": _ids(sel),
               # crests: ours (the fixture feed's) where we predict the match, else SportyBet's
               "home_crest": (pred or {}).get("home_crest") or sel.get("home_icon"),
               "away_crest": (pred or {}).get("away_crest") or sel.get("away_icon")}
        if not pred:
            leg["note"] = "We don't predict this match: its chance is from SportyBet's odds"
        elif prob is None:
            leg["note"] = "We don't price this market: its chance is from SportyBet's odds"
        better = max((o for o in suggestable if o.odds >= 1.2 and o.prob >= (prob or chance or 0) + BETTER_BY),
                     key=lambda o: (o.prob, o.odds), default=None)
        if better:
            leg["suggestion"] = {"market_name": better.market_name, "label": better.label,
                                 "prob": better.prob, "odds": better.odds, "odds_source": better.odds_source}
        legs.append(leg)
        chances.append(chance)
        if chance is None or not odds or odds <= 1.01 or not leg["active"]:
            continue                       # suspended or unpriced: can't go on a ticket
        if len(options) > MAX_OPTIONS:
            # The most even spread of prices to choose from: the likeliest pick at each price step
            by_step: Dict[float, Option] = {}
            for o in options:
                k = round(math.log(o.odds), 2)
                if k not in by_step or o.prob > by_step[k].prob:
                    by_step[k] = o
            options = sorted(by_step.values(), key=lambda o: -o.prob)[:MAX_OPTIONS]
        own = Option(sel["home"], sel["away"], date, time, leg["league"], own_market, sel["market"], own_code,
                     sel["outcome"], round(chance, 4), round(float(odds), 2),
                     "sportybet" if prob is not None else "odds_only", sb=_ids(sel), sport=sport)
        games.append(f"{sel['home']} v {sel['away']}")
        # The refine settings: main markets only, a strong pick of yours kept, the most odds a game
        if settings["main_markets"]:
            options = [o for o in options if o.market in MAIN_MARKETS]
        group = [own] + options
        if settings["keep_strong"] and prob is not None and chance >= settings["keep_strong"]:
            group = [own]
            kept_mine += 1
        if settings["max_leg_odds"]:
            group = [o for o in group if o.odds <= settings["max_leg_odds"]]
        if settings["value"]:
            # Worth more than its price, at SportyBet's real price, in a market that has paid
            group = [o for o in group if o.odds_source == "sportybet" and o.prob * o.odds >= VALUE_MIN_RETURN
                     and (market_paid is None or market_paid(o.sport, o.market))]
        if group:
            groups.append(group)

    known = [c for c in chances if c is not None]
    odds_all = [l["odds"] for l in legs if l["odds"]]
    report = {"legs": legs,
              "original": {"games": len(legs), "total_odds": round(math.prod(odds_all), 2) if odds_all else None,
                           "win_chance": round(math.prod(known), 4) if known else None,
                           "from_odds": sum(1 for l in legs if l["chance_from"] == "odds"),
                           "unmodelled": sum(1 for l in legs if l["chance_from"] == "odds")},
              "same_odds": None, "safest": None, "tickets": None, "ticket_notes": {},
              "settings": settings, "kept_strong": kept_mine}
    if games:      # (groups can be empty when the settings leave no pick: _tickets explains)
        report["tickets"], report["ticket_notes"] = _tickets(groups, settings, games)
    return report


def _pc(x: float) -> str:
    return f"{x * 100:.1f}".rstrip("0").rstrip(".") + "%"


def _tickets(groups: List[List[Option]], settings: Optional[Dict[str, Any]] = None,
             games: Optional[List[str]] = None) -> tuple:
    """Each ticket: the highest odds (up to the settings' most) at its least
    chance, on as many of the code's games as that chance allows. Returns
    ({ticket: slip or None}, {ticket: why there's none})."""
    settings = settings_of(settings)
    games = games or [f"{g[0].home} v {g[0].away}" for g in groups]
    hi = settings["max_odds"] or MAX_TICKET_ODDS
    out: Dict[str, Optional[Dict[str, Any]]] = {}
    notes: Dict[str, str] = {}
    surest = math.prod(max(o.prob for o in g) for g in groups)
    if settings["value"] and not groups:
        return ({n: None for n, _ in chances_of(settings)},
                {n: "No game here has a pick worth more than its odds in a market that has made money: "
                    "value mode needs one. Untick it to see the usual tickets." for n, _ in chances_of(settings)})
    for name, least in chances_of(settings):
        if settings["keep_all"] and (len(groups) < len(games) or surest < least - 1e-9):
            out[name] = None
            notes[name] = (f"Keeping all {len(games)} games, the best chance is {_pc(surest)}: under {_pc(least)}. "
                           "Untick \"keep all my games\" or pick a looser setting."
                           if len(groups) == len(games) else
                           f"{len(games) - len(groups)} of your games have no pick within your settings, "
                           "so they can't all be kept.")
            continue
        if settings["value"]:   # few legs, the highest odds at the chance
            res = optimize_safe(groups, least, hi, min(len(groups), VALUE_MAX_LEGS), min_edge=None)
        else:
            res = optimize_safe(groups, least, hi, len(groups), min_edge=None, most_games=True)
        if not res:
            out[name] = None
            notes[name] = f"No pick on these games is {_pc(least)} likely within your settings."
            continue
        if settings["min_odds"] and res["total_odds"] < settings["min_odds"] - 1e-9:
            out[name] = None
            notes[name] = (f"At {_pc(least)} these games pay at most {res['total_odds']:,.2f}x, "
                           f"under your {settings['min_odds']:,.2f}x minimum.")
            continue
        on = {(p["home"], p["away"]) for p in res["picks"]}
        left = [g for g in games if tuple(g.split(" v ", 1)) not in on]
        slip = {**_slip(res["picks"]), "target_chance": least, "left_out": left,
                "left_because": "value" if settings["value"] else "chance"}
        slip["bonus"] = round(bonus(slip["picks"]), 4)
        try:
            import multi_bonus
            slip["bonus_top"] = multi_bonus.share_top(slip["picks"])
        except Exception:
            slip["bonus_top"] = 0.0
        slip["expected_return"] = expected_return(slip)
        out[name] = slip
    return out, notes


# ── how the refined tickets do ────────────────────────────────────────────
# Every ticket the checker refines is kept (RECORD_KEY, apart from the
# accounts' tickets so the market ranking and calibration don't count them)
# and settled by the ticket job (settle_record), so each tier's record shows
# whether its chance is honest (safe aims at 50%: does it land half the time?).
RECORD_KEY = "betiq:codecheck:tickets"
RECORD_MAX = 3000


def record_tickets(r, code: str, report: Dict[str, Any], now: str) -> int:
    """Keep the report's three tickets (a code checked again replaces its
    tickets from before). Returns how many were kept."""
    import tickets as tk
    if r is None or not report.get("tickets"):
        return 0
    key = RECORD_KEY
    try:
        items = json.loads(r.get(key) or "[]")
    except ValueError:
        items = []
    items = [t for t in items if t.get("checked_code") != code or t.get("status") != "pending"]
    kept = 0
    for tier, least in TICKETS:
        t = report["tickets"].get(tier)
        if not t or not t.get("picks"):
            continue
        sels = [{**p, "marketName": p.get("market_name")} for p in t["picks"]]
        ticket = tk.new_ticket(f"{code}:{tier}", sels, [{"status": "booked", "odds": p.get("odds")} for p in t["picks"]],
                               "code_check", None, t.get("total_odds"), now)
        ticket.update(tier=tier, target_chance=least, said=t.get("win_chance"), checked_code=code)
        items.insert(0, ticket)
        kept += 1
    r.set(key, json.dumps(items[:RECORD_MAX], separators=(",", ":")), ex=365 * 86400)
    return kept


def settle_record(r, result_for: Callable[[Dict], Optional[Dict]]) -> int:
    """Settle the kept tickets' legs from the results (the ticket job's
    result_for). Returns how many tickets were settled."""
    import tickets as tk
    if r is None:
        return 0
    try:
        items = json.loads(r.get(RECORD_KEY) or "[]")
    except ValueError:
        return 0
    settled, changed = 0, False
    for t in items:
        if t.get("status") not in ("pending", "open"):
            continue
        was = t.get("status")
        if tk.settle(t, result_for):
            changed = True
            settled += t.get("status") != was and t.get("status") in ("won", "lost", "void")
    if changed:
        r.set(RECORD_KEY, json.dumps(items, separators=(",", ":")), ex=365 * 86400)
    return settled


def record(r) -> Dict[str, Dict[str, Any]]:
    """Per tier: tickets landed and lost, against the chance they were given."""
    out = {tier: {"aim": least, "landed": 0, "lost": 0, "open": 0, "said": 0.0} for tier, least in TICKETS}
    try:
        items = json.loads(r.get(RECORD_KEY) or "[]") if r is not None else []
    except ValueError:
        items = []
    for t in items:
        x = out.get(t.get("tier"))
        if not x:
            continue
        if t.get("status") in ("won", "lost"):
            x["landed" if t["status"] == "won" else "lost"] += 1
            x["said"] += float(t.get("said") or x["aim"])
        else:
            x["open"] += 1
    for x in out.values():
        n = x["landed"] + x["lost"]
        x["said"] = round(x["said"] / n, 4) if n else None
        x["rate"] = round(x["landed"] / n, 4) if n else None
    return out
