"""
Collect past club matches' xG from SofaScore into Redis (club_xg.py). Run
by .github/workflows/collect-club-xg.yml: SofaScore blocks the VPS, not
GitHub's runners. The first runs backfill SEASONS seasons of the CSV
leagues; after that each run adds the latest matches. Needs
UPSTASH_REDIS_URL.

    python collect_club_xg.py --minutes 50
"""

import argparse
import asyncio
import json
import os
import sys

import club_xg
import model_store


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=float(os.getenv("CLUB_XG_MINUTES", "50")))
    args = ap.parse_args()
    r = model_store._client()
    if r is None:
        print("UPSTASH_REDIS_URL isn't set — nothing to store to. Skipping.")
        return 0
    report = asyncio.run(club_xg.run(r, args.minutes))
    print("Run:", json.dumps(report, indent=1))
    print("Dataset:", json.dumps(club_xg.summary(club_xg.load(r)), indent=1))
    if report.get("blocked"):
        print(f"::warning::SofaScore blocked this run ({report['blocked']}); what was collected is saved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
