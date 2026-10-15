"""
The weekly drift check per league: a league whose picks came in clearly less
often than the model said over the last WINDOW_DAYS days is flagged and (in
auto mode) paused, so the optimizer and the daily odds slips leave its
matches out until it recovers.

It is the market review (market_review.py) cut the other way: every market's
picks in one league together, judged with the same test (the shortfall both
large and more than chance explains, picks of one match counted together).
A league has fewer matches a week than a market has, so the window is longer.

A newly failing league is reported to the admin chat on Telegram
(TELEGRAM_ADMIN_CHAT_ID, never the public channel) and to Sentry. Pausing
only stops a league being picked: its predictions are still made and
settled, so the next check sees whether it has recovered.
"""

from typing import Any, Dict, Iterable, List, Optional, Set

import market_accuracy
import market_review

WINDOW_DAYS = 56
MIN_MATCHES = 20          # matches with picks in the league before it can fail
MIN_WATCH = 12
PAUSE_Z, WATCH_Z = -2.5, -1.5
PAUSE_GAP, WATCH_GAP = -0.06, -0.04
DEFAULT_MODE = "flag"
MODES = ("auto", "flag")
OVERRIDES = ("on", "off")
HISTORY = 12
SHOWN = 40                # leagues kept in a review: the failing ones first, then by matches


def verdict(c: Dict[str, Any]) -> str:
    """pause / watch / ok / few."""
    if c["matches"] >= MIN_MATCHES and c["z"] <= PAUSE_Z and c["gap"] <= PAUSE_GAP:
        return "pause"
    if c["matches"] >= MIN_WATCH and c["z"] <= WATCH_Z and c["gap"] <= WATCH_GAP:
        return "watch"
    return "few" if c["matches"] < MIN_WATCH else "ok"


def _why(c: Dict[str, Any], status: str) -> str:
    pct = lambda x: f"{round(x * 100)}%"
    if status == "few":
        return f"only {c['matches']} matches with picks"
    said = f"came in {pct(c['hit_rate'])} vs {pct(c['model_said'])} said, over {c['matches']} matches"
    return said + (": short, but not clearly yet" if status == "watch" else "")


def review(entries: Iterable[Dict], failing_before: Iterable[str] = ()) -> Dict[str, Any]:
    """Every league's verdict over `entries` (match-day entries). A league
    failed before stays failing until it reviews ok (a watch or thin window
    keeps it), as with markets."""
    before = set(failing_before)
    rows: Dict[str, List[market_review.Row]] = {}
    names: Dict[str, str] = {}
    matches = 0
    for i, e in enumerate(entries):
        lg = str(e.get("league") or "")
        settled = market_accuracy.settle(e) if lg else []
        if not settled:
            continue
        matches += 1
        names[lg] = e.get("league_name") or names.get(lg) or lg
        rows.setdefault(lg, []).extend((i, prob, won) for _m, _c, prob, won in settled)
    leagues = []
    for lg in set(rows) | before:
        c = market_review.check(rows.get(lg, []))
        status = verdict(c) if c["picks"] else "few"
        failing = status == "pause" or (lg in before and status in ("watch", "few"))
        leagues.append({"league": lg, "name": names.get(lg, lg), **c, "status": status,
                        "why": _why(c, status) if c["picks"] else "no settled picks", "failing": failing})
    leagues.sort(key=lambda x: (not x["failing"], x["status"] != "watch", -x["matches"]))
    now = {x["league"] for x in leagues if x["failing"]}
    return {"matches": matches, "window_days": WINDOW_DAYS, "leagues": leagues[:max(SHOWN, len(now))],
            "failing": sorted(now), "newly_failing": sorted(now - before), "recovered": sorted(before - now)}


def blocked(state: Dict[str, Any]) -> Set[str]:
    """Leagues not to pick from: pinned off, plus (in auto mode) the failing
    ones that aren't pinned on."""
    overrides = state.get("overrides") or {}
    out = {lg for lg, v in overrides.items() if v == "off"}
    if state.get("mode", DEFAULT_MODE) == "auto":
        out |= {lg for lg in ((state.get("latest") or {}).get("failing") or []) if overrides.get(lg) != "on"}
    return out


def settings(body: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    """The mode and overrides from an admin's update (ValueError if bad)."""
    mode = body.get("mode", current.get("mode", DEFAULT_MODE))
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    overrides = body.get("overrides", current.get("overrides") or {})
    if not isinstance(overrides, dict):
        raise ValueError("overrides must be {league: on|off}")
    clean = {}
    for lg, v in overrides.items():
        if not str(lg).strip() or len(str(lg)) > 60:
            raise ValueError("Unknown league")
        if v is None or v == "auto":
            continue
        if v not in OVERRIDES:
            raise ValueError(f"{lg}: override must be on, off or auto")
        clean[str(lg)] = v
    return {"mode": mode, "overrides": clean}


def remember(state: Dict[str, Any], result: Dict[str, Any], at: str) -> Dict[str, Any]:
    line = {"at": at, "matches": result["matches"], "failing": result["failing"],
            "newly_failing": result["newly_failing"], "recovered": result["recovered"]}
    return {**state, "latest": {**result, "at": at}, "history": ([line] + list(state.get("history") or []))[:HISTORY]}


def _names(result: Dict[str, Any], leagues: Iterable[str]) -> str:
    by = {x["league"]: x["name"] for x in result.get("leagues") or []}
    return ", ".join(by.get(lg, lg) for lg in leagues)


def message(result: Dict[str, Any], mode: str) -> Optional[str]:
    """The admin alert (plain text) for a check that found newly failing or
    recovered leagues; None when nothing changed."""
    new, back = result.get("newly_failing") or [], result.get("recovered") or []
    if not new and not back:
        return None
    by = {x["league"]: x for x in result.get("leagues") or []}
    lines = ["BetIQ league drift check"]
    for lg in new:
        x = by.get(lg) or {}
        lines.append(f"• {x.get('name', lg)}: {x.get('why', 'falling short')}")
    if new:
        lines.append("Paused: the optimizer and daily odds leave them out." if mode == "auto" else
                     "Flag mode: still being picked. Switch to auto or pin them off in Admin → Predictions.")
    if back:
        lines.append(f"Recovered: {_names(result, back)}")
    return "\n".join(lines)


def alert(state: Dict[str, Any], age_days: Optional[float]) -> Optional[Dict[str, str]]:
    """The admin banner for a fresh check with failing leagues."""
    latest = state.get("latest") or {}
    failing = latest.get("failing") or []
    if age_days is None or age_days > 7 or not failing:
        return None
    auto = state.get("mode", DEFAULT_MODE) == "auto"
    return {"level": "warn",
            "title": f"League drift: {len(failing)} league{'s' if len(failing) != 1 else ''} falling short",
            "detail": f"{_names(latest, failing)}: picks came in clearly less often than the model said. "
                      + ("They're paused." if auto else "Flag mode is on, so they're still being picked.")
                      + " See Predictions → League drift check."}
