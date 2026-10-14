"""
Why are legs still pending? Every booked ticket's legs and every daily
slip's picks whose match kicked off more than GRACE_HOURS ago and still
aren't graded, each with what the grader sees for it: no match-day entry,
an entry without a result, a result the market can't be graded from (no
corners, extra time…), or a basketball / racket event without a result.
Reads only.

    python pending_check.py      (GitHub Actions: "Basketball data", job pending)
"""

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

GRACE_HOURS = 4
DAYS_BACK = 30


def kickoff(leg: Dict) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(f"{leg['date']}T{(leg.get('time') or '23:59')[:5]}:00+00:00")
    except (KeyError, TypeError, ValueError):
        return None


def reason(main, r, leg: Dict, result_for, md_days: Dict) -> str:
    import tickets
    market = str(leg.get("market") or "")
    res = result_for(leg)
    if market.startswith(("bb_", "rk_")) or market == "anytime_scorer":
        if not res:
            return f"{market.split('_')[0]}: no result for its event"
        return f"{market.split('_')[0]}: result {res.get('status')} but grades {tickets.grade_leg(market, leg.get('code', ''), res)}"
    e = main._md_entry_for(r, leg, md_days)
    if not e:
        return "football: no match-day entry for these teams/date"
    res = e.get("result") or {}
    if not res:
        return "football: entry without a result"
    if res.get("status") != "finished":
        return f"football: result status {res.get('status')}"
    if res.get("aet"):
        return "football: finished after extra time (graded on 90')"
    g = tickets.grade_leg(market, leg.get("code", ""), res)
    if g == "pending":
        stat = {"corners": "corners", "cards": "bookings", "shots": "shots", "sot": "sot"}
        need = next((v for k, v in stat.items() if k in market), None)
        if need and res.get(need) is None:
            return f"football: no {need} in the result"
        return f"football: {market} can't be graded from the result"
    return f"football: would grade {g} now (not re-run yet)"


def main_() -> None:
    import main
    import model_store
    r = model_store._client()
    if r is None:
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    now = datetime.now(timezone.utc)
    result_for = main._leg_results(r, {})
    md_days: Dict = {}
    reasons: Counter = Counter()
    examples: Dict[str, List[str]] = {}
    tickets_hit, legs_hit = set(), 0

    def look(leg, where, owner):
        nonlocal legs_hit
        if leg.get("status") not in (None, "pending"):
            return
        ko = kickoff(leg)
        if not ko or now - ko < timedelta(hours=GRACE_HOURS) or now - ko > timedelta(days=DAYS_BACK):
            return
        why = f"{where} · " + reason(main, r, leg, result_for, md_days)
        reasons[why] += 1
        legs_hit += 1
        tickets_hit.add(owner)
        ex = examples.setdefault(why, [])
        if len(ex) < 6:
            ex.append(f"{leg.get('date')} {leg.get('home')} v {leg.get('away')} · {leg.get('market')} {leg.get('code')}")

    for key in r.scan_iter(match="betiq:user:*:tickets", count=500):
        try:
            items = json.loads(r.get(key) or "[]")
        except ValueError:
            continue
        for t in items:
            for leg in t.get("legs") or []:
                look(leg, "ticket", (key, t.get("code")))
    today = now.date()
    for i in range(DAYS_BACK + 1):
        d = (today - timedelta(days=i)).isoformat()
        raw = r.get(f"betiq:daily:{d}")
        if not raw:
            continue
        doc = json.loads(raw)
        for s in (doc.get("slips") or []) + (doc.get("cut") or []):
            for p in s.get("picks") or []:
                look(p, "daily", (d, s.get("target"), (s.get("booking") or {}).get("code")))
    print(f"{legs_hit} legs past kick-off (+{GRACE_HOURS}h) still pending, on {len(tickets_hit)} tickets/slips:\n")
    for why, n in reasons.most_common():
        print(f"{n:5}  {why}")
        for ex in examples[why]:
            print(f"         {ex}")


if __name__ == "__main__":
    main_()
