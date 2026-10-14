"""
Everything the site publishes about one match, for checking a pick that
looks wrong: the prediction (our chances, the model's own, the odds), and
how the optimizer prices its picks.

    python match_check.py "Borac"          (GitHub Actions: "Basketball data", job match, parts=<team>)
"""

import json
import os
import sys

import httpx

API = os.getenv("SITE_URL", "https://13-140-188-212.sslip.io").rstrip("/")


def main() -> int:
    q = " ".join(sys.argv[1:]).strip().lower()
    if not q:
        print("Give part of a team name")
        return 1
    with httpx.Client(timeout=60, headers={"User-Agent": "betiq-check"}) as c:
        preds = c.get(API + "/api/predictions?limit=3000").json().get("predictions") or []
        found = [p for p in preds if q in (p.get("home", "") + " " + p.get("away", "")).lower()]
        print(f"{len(found)} prediction(s) matching {q!r}")
        for p in found:
            print(json.dumps(p, indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
