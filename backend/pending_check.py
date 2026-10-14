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
    no_result: Dict[str, str] = {}      # event id -> sport, for the Sportradar check
    no_result_legs: Dict[tuple, str] = {}   # (date, home, away) -> sport, for the Flashscore check

    def look(leg, where, owner):
        nonlocal legs_hit
        if leg.get("status") not in (None, "pending"):
            return
        ko = kickoff(leg)
        if not ko or now - ko < timedelta(hours=GRACE_HOURS) or now - ko > timedelta(days=DAYS_BACK):
            return
        why = f"{where} · " + reason(main, r, leg, result_for, md_days)
        if why.endswith("no result for its event"):
            eid = leg.get("event_id") or (leg.get("sb") or {}).get("eventId")
            if eid:
                no_result[eid] = main._leg_sport(leg)
            no_result_legs[(leg.get("date"), leg.get("home"), leg.get("away"))] = main._leg_sport(leg)
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
    if no_result:
        sportradar(no_result)
    if no_result_legs:
        flashscore(no_result_legs, now)


def sportradar(events: Dict[str, str]) -> None:
    """What Sportradar's match info says for the events nothing else scored."""
    import asyncio
    import live_stats
    import sr_results

    async def go():
        out = {}
        for eid, sport in events.items():
            num = live_stats.sr_number(eid) if str(eid).startswith("sr:") else None
            out[eid] = (sport, await sr_results._read(num) if num else "not a Sportradar id")
        return out
    print(f"\nSportradar match info for {len(events)} events with no result:")
    shown = False
    for eid, (sport, body) in asyncio.run(go()).items():
        if isinstance(body, str) or body is None:
            print(f"  {eid} ({sport}): {body or 'unreadable'}")
            continue
        data = live_stats.doc_data(body)
        m = data.get("match") if isinstance(data, dict) else None
        if not isinstance(m, dict):
            print(f"  {eid} ({sport}): no match in {str(body)[:300]}")
            continue
        if not shown:
            shown = True
            print(f"  (match keys: {sorted(m)})")
        st = m.get("status")
        print(f"  {eid} ({sport}): status={st if not isinstance(st, dict) else st.get('name')} "
              f"matchstatus={m.get('matchstatus')} result={m.get('result')} periods={m.get('periods')} "
              f"→ {sr_results.parse(body, sport)}")


def flashscore(legs: Dict[tuple, str], now: datetime) -> None:
    """Probe: Flashscore's day feeds, and the records in them for the legs nothing scored."""
    import asyncio
    import re
    import httpx

    hosts = ["https://local-global.flashscore.ninja/2/x/feed/", "https://global.flashscore.ninja/2/x/feed/",
             "https://d.flashscore.com/x/feed/", "https://www.flashscore.com/x/feed/"]
    head = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/131.0 Safari/537.36",
            "x-fsign": "SW9D1eZo", "Referer": "https://www.flashscore.com/", "Accept": "*/*"}
    sports = {"basketball": [3], "tennis": [2], "table_tennis": [25], "racket": [2, 25]}

    def toks(name):
        return {t for t in re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower()).split() if len(t) >= 3}

    async def go():
        async with httpx.AsyncClient(timeout=15, headers=head, follow_redirects=True) as c:
            base = None
            for h in hosts:
                try:
                    resp = await c.get(h + "f_3_-1_0_en_1")
                    print(f"  {h}: {resp.status_code} {len(resp.text)} bytes · {resp.text[:200]!r}")
                    if resp.status_code == 200 and "AA÷" in resp.text and base is None:
                        base = h
                except Exception as exc:
                    print(f"  {h}: {type(exc).__name__} {exc}")
            if not base:
                return
            print(f"  using {base}")
            feeds = {}
            for (day, home, away), sport in legs.items():
                try:
                    off = (datetime.fromisoformat(day).date() - now.date()).days
                except (TypeError, ValueError):
                    continue
                found = False
                for sid in sports.get(sport, [3]):
                    for o in (off - 1, off, off + 1):
                        if (sid, o) not in feeds:
                            try:
                                resp = await c.get(f"{base}f_{sid}_{o}_0_en_1")
                                feeds[(sid, o)] = resp.text if resp.status_code == 200 else ""
                                if resp.status_code != 200:
                                    print(f"  f_{sid}_{o}: {resp.status_code}")
                            except Exception as exc:
                                feeds[(sid, o)] = ""
                                print(f"  f_{sid}_{o}: {type(exc).__name__}")
                        for rec in feeds[(sid, o)].split("~"):
                            f = dict(x.split("÷", 1) for x in rec.split("¬") if "÷" in x)
                            if "AA" not in f:
                                continue
                            if toks(home) & toks(f.get("AE", "") + " " + f.get("CX", "")) and \
                                    toks(away) & toks(f.get("AF", "") + " " + f.get("FK", "")):
                                found = True
                                print(f"  {day} {home} v {away} ({sport}) · f_{sid}_{o}: {rec[:700]}")
                if not found:
                    print(f"  {day} {home} v {away} ({sport}): not in the feeds")
            for (sid, o), text in sorted(feeds.items()):
                print(f"  f_{sid}_{o}: {len(text)} bytes, {text.count('~AA÷')} events")

    print(f"\nFlashscore for {len(legs)} matches with no result:")
    asyncio.run(go())


if __name__ == "__main__":
    main_()
