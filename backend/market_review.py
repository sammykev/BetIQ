"""
The weekly accuracy review: every market whose picks came in clearly less
often than the model said over the last WINDOW_DAYS days is paused, so the
optimizer, the daily odds slips and the code check stop using it until it
recovers.

A market is judged on its picks (outcomes rated ≥ 50%, market_accuracy.py),
all together and at 80%+ (the picks the daily slips are built from): how many
came in against how many the model expected. The shortfall must be both
large (PAUSE_GAP) and more than chance explains (PAUSE_Z), over enough
matches. Picks from one match move together (over 1.5 and over 2.5 goals),
so the chance allowance counts matches, not picks.

Pausing only stops a market being picked: its predictions are still made and
settled, so the next review sees whether it has recovered. A paused market
comes back once it reviews "ok" (a "watch" or thin week keeps it paused).

The admin chooses the mode ("auto" pauses, "flag" only reports) and can pin
any market on or off (overrides).
"""

import math
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import market_accuracy
import optimizer

WINDOW_DAYS = 28
TOP = 0.8                 # the high-confidence picks, judged on their own too
PAUSE_Z, WATCH_Z = -2.5, -1.5
PAUSE_GAP, WATCH_GAP = -0.05, -0.03
BETTER_Z = 2.5
MIN_MATCHES = 30          # matches with picks in the market before it can be paused
MIN_WATCH = 15
HISTORY = 12              # past reviews kept
MODES = ("auto", "flag")
OVERRIDES = ("on", "off")

Row = Tuple[int, float, bool]   # (match index, prob, won)


def check(rows: List[Row]) -> Dict[str, Any]:
    """How a set of picks did: count, hit rate, what the model said, the gap
    and z (the shortfall in standard errors; picks grouped by match)."""
    n = len(rows)
    if not n:
        return {"picks": 0, "matches": 0, "hit_rate": None, "model_said": None, "gap": None, "z": None}
    won = sum(1 for r in rows if r[2])
    expected = sum(r[1] for r in rows)
    by_match: Dict[int, List[float]] = {}
    for m, p, w in rows:
        e = by_match.setdefault(m, [0.0, 0.0, 0.0])
        e[0] += w
        e[1] += p
        e[2] += p * (1 - p)
    # The larger of the model's own variance and the spread between matches
    # (which allows for picks of one match winning or losing together)
    k = len(by_match)
    mean = (won - expected) / k
    spread = sum((e[0] - e[1] - mean) ** 2 for e in by_match.values()) * k / (k - 1) if k > 1 else 0.0
    var = max(sum(e[2] for e in by_match.values()), spread)
    z = (won - expected) / math.sqrt(var) if var > 0 else 0.0
    return {"picks": n, "matches": len(by_match), "hit_rate": round(won / n, 3), "model_said": round(expected / n, 3),
            "gap": round((won - expected) / n, 3), "z": round(z, 2)}


def _short(c: Dict[str, Any], z: float, gap: float, min_matches: int) -> bool:
    return c["picks"] > 0 and c["matches"] >= min_matches and c["z"] <= z and c["gap"] <= gap


def verdict(overall: Dict[str, Any], top: Dict[str, Any]) -> Tuple[str, str]:
    """(status, why): pause / watch / better / ok / few."""
    pct = lambda x: f"{round(x * 100)}%"
    for c, where in ((overall, ""), (top, " at 80%+")):
        if _short(c, PAUSE_Z, PAUSE_GAP, MIN_MATCHES):
            return "pause", f"came in {pct(c['hit_rate'])}{where} vs {pct(c['model_said'])} said, over {c['matches']} matches"
    for c, where in ((overall, ""), (top, " at 80%+")):
        if _short(c, WATCH_Z, WATCH_GAP, MIN_WATCH):
            return "watch", f"came in {pct(c['hit_rate'])}{where} vs {pct(c['model_said'])} said: short, but not clearly yet"
    if overall["matches"] < MIN_WATCH:
        return "few", f"only {overall['matches']} matches with picks"
    if overall["z"] >= BETTER_Z:
        return "better", f"came in {pct(overall['hit_rate'])} vs {pct(overall['model_said'])} said: the model undersells it"
    return "ok", f"came in {pct(overall['hit_rate'])} vs {pct(overall['model_said'])} said"


def review(entries: Iterable[Dict], paused_before: Iterable[str] = ()) -> Dict[str, Any]:
    """Every market's verdict over `entries` (match-day entries), and which
    markets are paused after it: newly failing ones, plus those paused before
    that haven't reviewed ok since."""
    before = set(paused_before)
    rows: Dict[str, List[Row]] = {}
    matches = 0
    for i, e in enumerate(entries):
        settled = market_accuracy.settle(e)
        matches += bool(settled)
        for market, _code, prob, won in settled:
            rows.setdefault(market, []).append((i, prob, won))
    order = list(optimizer.MARKET_NAMES)
    markets = []
    for market in sorted(set(rows) | before, key=lambda m: order.index(m) if m in order else 99):
        rs = rows.get(market, [])
        overall, top = check(rs), check([r for r in rs if r[1] >= TOP])
        status, why = verdict(overall, top)
        paused = status == "pause" or (market in before and status in ("watch", "few"))
        markets.append({"market": market, "name": optimizer.MARKET_NAMES.get(market, market),
                        **overall, "top": top, "status": status, "why": why, "paused": paused})
    now = {m["market"] for m in markets if m["paused"]}
    return {"matches": matches, "window_days": WINDOW_DAYS, "markets": markets,
            "paused": sorted(now), "newly_paused": sorted(now - before), "restored": sorted(before - now)}


def blocked(state: Dict[str, Any]) -> Set[str]:
    """Markets not to pick from: pinned off, plus (in auto mode) the review's
    paused ones that aren't pinned on."""
    overrides = state.get("overrides") or {}
    out = {m for m, v in overrides.items() if v == "off"}
    if state.get("mode", "auto") == "auto":
        out |= {m for m in ((state.get("latest") or {}).get("paused") or []) if overrides.get(m) != "on"}
    return out


def settings(body: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    """The mode and overrides from an admin's update (ValueError if bad)."""
    mode = body.get("mode", current.get("mode", "auto"))
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    overrides = body.get("overrides", current.get("overrides") or {})
    if not isinstance(overrides, dict):
        raise ValueError("overrides must be {market: on|off}")
    clean = {}
    for m, v in overrides.items():
        if m not in optimizer.MARKET_NAMES:
            raise ValueError(f"Unknown market: {m}")
        if v is None or v == "auto":
            continue
        if v not in OVERRIDES:
            raise ValueError(f"{m}: override must be on, off or auto")
        clean[m] = v
    return {"mode": mode, "overrides": clean}


def remember(state: Dict[str, Any], result: Dict[str, Any], at: str) -> Dict[str, Any]:
    """The state after a review: it becomes the latest, and a line in the history."""
    line = {"at": at, "matches": result["matches"], "paused": result["paused"],
            "newly_paused": result["newly_paused"], "restored": result["restored"],
            "watch": [m["market"] for m in result["markets"] if m["status"] == "watch"]}
    return {**state, "latest": {**result, "at": at}, "history": ([line] + list(state.get("history") or []))[:HISTORY]}


def names(markets: Iterable[str]) -> str:
    return ", ".join(optimizer.MARKET_NAMES.get(m, m) for m in markets)


def alert(state: Dict[str, Any], age_days: Optional[float]) -> Optional[Dict[str, str]]:
    """The admin banner line for a fresh review that paused (or, in flag mode,
    would pause) markets."""
    latest = state.get("latest") or {}
    if age_days is None or age_days > 7:
        return None
    failing = [m["market"] for m in latest.get("markets") or [] if m["status"] == "pause"]
    if state.get("mode", "auto") == "flag":
        if not failing:
            return None
        return {"level": "warn", "title": f"Accuracy review: {len(failing)} market{'s' if len(failing) != 1 else ''} falling short",
                "detail": f"{names(failing)} came in clearly less often than the model said. Flag mode is on, so they're "
                          "still being picked: see Predictions → Weekly accuracy review."}
    new = latest.get("newly_paused") or []
    if not new:
        return None
    return {"level": "warn", "title": f"Accuracy review paused {names(new)}",
            "detail": "Their picks came in clearly less often than the model said, so the optimizer, daily odds and code "
                      "check leave them out until they recover. See Predictions → Weekly accuracy review."}
