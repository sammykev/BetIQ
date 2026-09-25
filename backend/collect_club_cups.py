"""
Collect European competitions and domestic cups from ESPN (club_cups.py),
then check whether they improve the club model's league predictions and
store the verdict with the data, so training only uses what helps. Run by
.github/workflows/collect-international-stats.yml after the international
collection; needs UPSTASH_REDIS_URL.

    python collect_club_cups.py --minutes 100
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone

import club_cups
import model_store


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=float(os.getenv("CLUB_CUPS_MINUTES", "100")))
    ap.add_argument("--no-check", action="store_true")
    args = ap.parse_args()
    r = model_store._client()
    if r is None:
        print("UPSTASH_REDIS_URL isn't set — nothing to store to. Skipping.")
        return 0
    data = club_cups.load(r)
    today = datetime.now(timezone.utc).date()

    async def go():
        from curl_cffi.requests import AsyncSession
        import international_fixtures as intl
        async with AsyncSession(impersonate=intl.IMPERSONATE, timeout=30) as session:
            return await club_cups.collect(session, data, time.monotonic() + args.minutes * 60, today)

    started = time.monotonic()
    report = asyncio.run(go())
    report["at"] = datetime.now(timezone.utc).isoformat()
    report["seconds"] = round(time.monotonic() - started)
    data["runs"] = (data.get("runs") or [])[-19:] + [report]
    club_cups.save(r, data)
    print("Collection:", json.dumps(report, indent=1))

    # The check: once a week (it retrains the model 9 times), or when there's none yet
    last = (data.get("check") or {}).get("at")
    stale = not last or (datetime.now(timezone.utc) - datetime.fromisoformat(last)).days >= 7
    if not args.no_check and data["rows"] and stale:
        from main import _club_cup_rows, _load_football_data_csvs
        league = _load_football_data_csvs()
        names = set(league["HomeTeam"].dropna()) | set(league["AwayTeam"].dropna())
        extras = {"europe": _club_cup_rows(names, club_cups.EUROPE_CODES),
                  "cups": _club_cup_rows(names, club_cups.CUP_CODES)}
        data["check"] = club_cups.check(league, extras)
        club_cups.save(r, data)
        print("Model check:", json.dumps(data["check"], indent=1))
    print("Dataset:", json.dumps({k: v for k, v in club_cups.summary(data).items() if k != "last_run"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
