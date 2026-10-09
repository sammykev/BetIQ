"""
Which crest does each football club get? For every club in the current
predictions: the crest its fixture came with (home_crest/away_crest), the
crest cached for its name, and what the live /api/team-logo answers; then
the frontend's static crest ids (frontend/lib/teamAssets.ts) checked
against the fixtures' own. Reads only.

    python crest_check.py      (GitHub Actions: "Basketball data", job crests)
"""

import json
import os
import re
from pathlib import Path

import httpx

BASE = os.getenv("SITE_URL", "https://13-140-188-212.sslip.io").rstrip("/")
WATCH = ("espanyol", "barcelona", "dortmund", "bremen", "malaga", "málaga")


def main() -> None:
    import redis
    r = redis.from_url(os.environ["UPSTASH_REDIS_URL"], decode_responses=True)
    raw = r.get("betiq:predictions")
    preds = (json.loads(raw) or {}).get("predictions", []) if raw else []
    crests = {}
    for p in preds:
        if p.get("sport") not in (None, "football"):
            continue
        for side in ("home", "away"):
            crests.setdefault(p.get(side), (p.get(f"{side}_crest"), p.get("league")))
    print(f"{len(crests)} football clubs; {sum(1 for c, _ in crests.values() if c)} with a fixture crest\n")
    with httpx.Client(timeout=20) as c:
        for name, (crest, lg) in sorted(crests.items(), key=lambda kv: (kv[1][1] or "", kv[0] or "")):
            if not any(w in (name or "").lower() for w in WATCH):
                continue
            cached = r.get(f"betiq:team_crest:{name.strip().lower()}")
            try:
                live = c.get(f"{BASE}/api/team-logo", params={"name": name}).json()
            except Exception as e:
                live = str(e)
            print(f"{name!r} [{lg}] fixture crest: {crest} · cached: {cached} · /api/team-logo: {live}")
    print("\nThe live site (/api/predictions), as a visitor's phone gets it:")
    with httpx.Client(timeout=60) as c:
        live = c.get(f"{BASE}/api/predictions", params={"limit": 500}).json().get("predictions", [])
        print(f"  {len(live)} predictions")
        seen = set()
        for p in live:
            for side in ("home", "away"):
                n = p.get(side) or ""
                if n in seen or not any(w in n.lower() for w in WATCH + ("werder",)):
                    continue
                seen.add(n)
                logo = c.get(f"{BASE}/api/team-logo", params={"name": n}).json()
                print(f"  {n!r} [{p.get('league')}] {side}_crest: {p.get(side + '_crest')!r} · /api/team-logo: {logo.get('logo')} ({logo.get('source')})")
        names = ["Werder Bremen", "SV Werder Bremen", "Borussia Dortmund", "Dortmund", "RCD Espanyol de Barcelona",
                 "Espanyol", "FC Barcelona", "Málaga CF", "Ath Madrid", "Man United", "Nott'm Forest", "Crystal Palace",
                 "Sunderland", "Metz", "Montpellier", "Toulouse", "Strasbourg", "Hamburger SV", "Real Sociedad"]
        got = c.get(f"{BASE}/api/team-logos", params={"names": "|".join(names)})
        print(f"  /api/team-logos: {got.status_code}")
        for n, url in (got.json().get("logos") or {}).items() if got.status_code == 200 else []:
            print(f"  {n!r}: {url}")
        n_idx = r.hlen("betiq:crests")
        print(f"  crest index: {n_idx} names")
    print("\nAll fixture crests:")
    for name, (crest, lg) in sorted(crests.items(), key=lambda kv: (kv[1][1] or "", kv[0] or "")):
        print(f"  {lg:<5} {name!r}: {crest}")
    ts = Path(__file__).resolve().parent.parent / "frontend" / "lib" / "teamAssets.ts"
    if ts.exists():
        s = ts.read_text()
        blk = s[s.index("const CLUB_IDS"):]
        blk = blk[:blk.index("};")]
        ids = dict(re.findall(r'"([^"]+)":(\d+)', blk))
        print("\nStatic crest ids vs the fixtures' own (fixture names containing the key):")
        for key, cid in ids.items():
            hits = {n: cr for n, (cr, _) in crests.items()
                    if n and cr and re.search(rf"\b{re.escape(key.lower())}\b", n.lower())}
            for n, cr in hits.items():
                m = re.search(r"/(\d+)\.(png|svg)", cr)
                fid = m.group(1) if m else cr
                print(f"  {'OK ' if fid == cid else 'BAD'} {key}: static {cid} · {n!r} fixture {fid}")


if __name__ == "__main__":
    main()
