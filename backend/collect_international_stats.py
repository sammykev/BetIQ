"""
Collect corners/cards for international matches (international_stats.py),
then tune and test the international corners/bookings model on them
(set_pieces.tune_international) and store the verdict with the data, so
the API server knows whether to use it. Run by .github/workflows/
collect-international-stats.yml; needs UPSTASH_REDIS_URL, and
APIFOOTBALL_KEY for the API-Football top-up (85 requests a night by
default: the API server's referee lookup may use up to 12 more of the
free plan's 100 a day).

    python collect_international_stats.py --minutes 150 --af-budget 85
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

import international_stats
import model_store
import set_pieces


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=float(os.getenv("INTL_STATS_MINUTES", "150")))
    ap.add_argument("--af-budget", type=int, default=int(os.getenv("APIFOOTBALL_DAILY_BUDGET", "85")))
    args = ap.parse_args()
    r = model_store._client()
    if r is None:
        print("UPSTASH_REDIS_URL isn't set — nothing to store to. Skipping.")
        return 0
    api_key = os.getenv("APIFOOTBALL_KEY", "").strip()
    if not api_key:
        print("APIFOOTBALL_KEY isn't set — SofaScore only this run.")
    report = asyncio.run(international_stats.run(r, args.minutes, api_key, args.af_budget))
    print("Collection:", json.dumps(report, indent=1, default=str))

    data = international_stats.load(r)
    frame = international_stats.rows_frame(data)
    verdict = set_pieces.tune_international(frame)
    data["model"] = {**verdict, "at": datetime.now(timezone.utc).isoformat()}
    international_stats.save(r, data)
    print("Model check:", json.dumps(data["model"], indent=1, default=str))
    print("Dataset:", json.dumps(international_stats.summary(data), indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
