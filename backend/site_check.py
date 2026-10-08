"""
Is the site loading? Each endpoint the pages call, as a visitor would call
it (status, time, size, how many matches), plus the server's own report of
event-loop stalls and memory (perf.py, from Redis). Reads only.

    python site_check.py      (GitHub Actions: "Basketball data", job site)
"""

import json
import os
import time
from datetime import date, timedelta

import httpx

BASE = os.getenv("SITE_URL", "https://13-140-188-212.sslip.io").rstrip("/")


def count(body):
    if isinstance(body, list):
        return f"{len(body)} items"
    if isinstance(body, dict):
        for k in ("matches", "predictions", "days", "slips", "tickets"):
            if isinstance(body.get(k), list):
                return f"{len(body[k])} {k}"
        return "keys: " + ", ".join(list(body)[:8])
    return ""


def main() -> None:
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    paths = ["/api/health", "/api/features", "/api/predictions", "/api/matchday/strip",
             f"/api/matchday?date={today}", f"/api/matchday?date={yesterday}",
             "/api/sports/basketball", "/api/sports/tennis", "/api/sports/table-tennis",
             "/api/basketball/matchday/strip", f"/api/basketball/matchday?date={today}",
             "/api/tennis/matchday/strip", f"/api/tennis/matchday?date={today}",
             "/api/table-tennis/matchday/strip", f"/api/table-tennis/matchday?date={today}",
             "/api/daily-slips", "/api/leagues"]
    with httpx.Client(timeout=60, headers={"Origin": "https://betiq.app"}) as c:
        for rnd in (1, 2):
            print(f"\nRound {rnd} ({'cold' if rnd == 1 else 'warm'} caches):")
            for p in paths:
                t0 = time.time()
                try:
                    r = c.get(BASE + p)
                    dt = time.time() - t0
                    try:
                        what = count(r.json())
                    except ValueError:
                        what = r.text[:120].replace("\n", " ")
                    print(f"  {r.status_code} {dt:6.2f}s {len(r.content) / 1024:8.1f} KB  {p}  · {what}")
                    if r.status_code >= 400:
                        print(f"      {r.text[:300]}")
                except Exception as e:
                    print(f"  ERR {time.time() - t0:6.2f}s  {p}  · {type(e).__name__}: {e}")
    with httpx.Client(timeout=60) as c:
        print("\nHealth:", c.get(BASE + "/api/health").text)
        preds = c.get(BASE + "/api/predictions").json()
        preds = preds.get("predictions", preds) if isinstance(preds, dict) else preds
        from collections import Counter
        print("Football predictions by date:", sorted(Counter(p.get("date") for p in preds).items()))
        print("By league:", Counter(p.get("league_name") or p.get("league") for p in preds).most_common(40))
    url = os.getenv("UPSTASH_REDIS_URL")
    if url:
        import redis
        r = redis.from_url(url, decode_responses=True)
        print("\nServer boot:", r.get("betiq:server:boot"))
        raw = r.get("betiq:perf")
        print("Server timing report (perf.py):")
        print(json.dumps(json.loads(raw), indent=1)[:20000] if raw else "  none saved")
        for k in ("betiq:pipeline:status", "betiq:predictions:meta", "betiq:refresh:status"):
            v = r.get(k)
            if v:
                print(k, v[:1500])
        print("Prediction-ish keys:", sorted(k for k in r.scan_iter("betiq:*pred*", count=1000))[:30])
        from collections import Counter as _C
        for sport in ("basketball", "tennis", "table_tennis"):
            raw = r.get({"basketball": "betiq:bb:predictions"}.get(sport, f"betiq:{sport}:predictions"))
            try:
                import basketball_data as bd
                ps = bd.decode(raw) if raw else []
            except Exception as e:
                print(sport, "predictions unreadable:", e)
                continue
            leagues = _C(p.get("league") for p in ps if isinstance(p, dict))
            print(f"{sport}: {len(ps)} predictions in {len(leagues)} leagues: {leagues.most_common(12)}")
            if ps and isinstance(ps[0], dict):
                print("   one:", {k: ps[0].get(k) for k in ("league", "league_name", "flag", "home", "away")})
        md = r.get("betiq:md:status")
        if md:
            print("Match-day job status:", md[:2000])


if __name__ == "__main__":
    main()
