"""
Read-only report: how the watched tickets (booked with the new logic) are
doing next to the same accounts' tickets from before the watch started.
Prints codes and results only, no account ids, emails or secrets.

On the server:  docker compose exec -T api python watch_report.py
"""

from typing import Any, Dict, List

import main
import ticket_watch as tw
from routers.optimizer import MIN_HIT_RATE
from routers.tickets import _watch_state


def _perf(t: Dict) -> Dict[str, Any]:
    return tw.performance(t, main._ranked_codes(), MIN_HIT_RATE, main._leg_sport)


def _block(title: str, perfs: List[Dict[str, Any]]) -> List[str]:
    if not perfs:
        return [f"{title}: no tickets"]
    s = tw.summary(perfs)
    settled = [p for p in perfs if p.get("status") in ("won", "lost")]
    per_leg = f"{s['legs_won'] / s['legs']:.0%}" if s["legs"] else "n/a"
    exp = f"{s['expected'] / s['legs']:.0%}" if s.get("expected") is not None and s["legs"] else "n/a"
    return [f"{title}: {len(perfs)} tickets, {s['won']} won of {len(settled)} settled",
            f"    legs won {tw._pct(s['legs_won'], s['legs'])} vs model {exp} (hit rate {per_leg})",
            f"    70%+ markets {tw._pct(s['top']['won'], s['top']['n'])} · "
            f"fallback {tw._pct(s['fallback']['won'], s['fallback']['n'])}"]


def report(r) -> str:
    w = _watch_state(r)
    items = main._all_tickets(r)
    by_code = {t.get("code"): t for t in reversed(items)}
    uids = set((w.get("uids") or {}).values()) - {None}
    rnd = w.get("round") or {}
    for t in rnd.get("tickets") or []:
        uids.add(t.get("uid"))
    uids = uids or set(main.ADMIN_USER_IDS)

    starts = [h.get("started_at") for h in w.get("history") or []] + [rnd.get("started_at")]
    start = min((s for s in starts if s), default=None)
    watched = list(dict.fromkeys(
        [c for h in reversed(w.get("history") or []) for c in h.get("codes") or []]
        + [t["code"] for t in rnd.get("tickets") or []]))

    out = [f"Watch started {start or 'never'}; {len(watched)} watched tickets"]
    if rnd:
        out += ["", f"Current round (from {rnd.get('started_at')}):"]
        for t in rnd.get("tickets") or []:
            if t["code"] in by_code:
                out += [tw.ticket_text(_perf(by_code[t["code"]]))]
    for h in w.get("history") or []:
        s = h.get("summary") or {}
        out += ["", f"Finished round {h.get('started_at')} → {h.get('done_at')}: "
                    f"{s.get('won')} of {s.get('tickets')} won, legs {s.get('legs_won')}/{s.get('legs')}"
                    f" (model {s.get('expected')}), underperformer {s.get('underperformer')}"]

    now = [_perf(by_code[c]) for c in watched if c in by_code]
    before = [_perf(t) for t in items
              if t.get("uid") in uids and t.get("code") not in watched
              and t.get("status") in ("won", "lost", "void")
              and start and (t.get("created_at") or "") < start]
    out += ["", "Comparison (same accounts):"]
    out += _block("  New logic (watched)", now)
    out += _block("  Before the watch (settled)", before)
    return "\n".join(out)


if __name__ == "__main__":
    print(report(main._get_redis()))
