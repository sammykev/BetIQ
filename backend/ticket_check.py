"""
Why are ticket legs still pending? For every unsettled leg of the last few
days' tickets: its match as the match-day store has it (status, score), the
results store's final (basketball, tennis, table tennis), and how the leg
would settle from each. Reads only; prints no account ids.

    python ticket_check.py          (GitHub Actions: "Basketball data", job tickets)
"""

import json
import os
from collections import Counter
from datetime import date, datetime, timedelta, timezone

DAYS = 4


def main() -> None:
    import redis
    import main as m
    import racket_matchday as rmd
    import tickets

    if not os.getenv("UPSTASH_REDIS_URL"):
        raise SystemExit("UPSTASH_REDIS_URL isn't set")
    r = redis.from_url(os.environ["UPSTASH_REDIS_URL"], decode_responses=True)
    since = (date.today() - timedelta(days=DAYS)).isoformat()
    days, other, stats = {}, {}, {}
    result_for = m._leg_results(r, days)
    counts = Counter()
    for key in r.scan_iter(match="betiq:user:*:tickets", count=500):
        for t in json.loads(r.get(key) or "[]"):
            if (t.get("created_at") or "") < since:
                continue
            counts[f"ticket {t.get('status')}"] += 1
            for leg in t.get("legs") or []:
                counts[f"leg {leg.get('status')}"] += 1
                if leg.get("status") != "pending":
                    continue
                sport = m._leg_sport(leg)
                res = result_for(leg)
                verdict = tickets.grade_leg(leg.get("market", ""), leg.get("code", ""), res)
                line = (f"{leg.get('date')} {leg.get('time') or ''} [{sport}] {leg.get('home')} v {leg.get('away')} · "
                        f"{leg.get('market')} {leg.get('code')} · results store: "
                        f"{'none' if res is None else res.get('status')} → {verdict}")
                if sport == "football":
                    e = m._md_entry_for(r, leg, days)
                    rr = (e or {}).get("result") or {}
                    line += f" · match day: {rr.get('status')} {rr.get('hg')}-{rr.get('ag')} {rr.get('minute') or ''}"
                else:
                    live = m._other_sport_live(r, leg, sport, other, stats)
                    line += f" · match day: {(live or {}).get('status')} {(live or {}).get('score')} {(live or {}).get('periods')}"
                    if live and live.get("status") == "finished" and sport != "basketball":
                        alt = tickets.grade_leg(leg.get("market", ""), leg.get("code", ""),
                                                rmd._settled({"score": live["score"], "periods": live.get("periods")}))
                        line += f" → from the match day: {alt}"
                    line += f" · event {leg.get('event_id') or (leg.get('sb') or {}).get('eventId')}"
                counts[f"pending {sport}"] += 1
                print(line)
    print(f"\n{datetime.now(timezone.utc).isoformat(timespec='seconds')} tickets since {since}: {dict(counts)}")


if __name__ == "__main__":
    main()
