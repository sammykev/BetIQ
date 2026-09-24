"""
Find the referees of the coming days' matches from GitHub's machines —
SofaScore blocks the API server on Render — and store them in Redis, where
the API server picks them up on its next referee check (every 3 hours) and
re-prices the cards forecast. Run by .github/workflows/find-referees.yml;
needs UPSTASH_REDIS_URL.

    python collect_referees.py
"""

import asyncio
import json
import sys
from datetime import datetime, timezone

import international_fixtures as intl
import model_store
import referees

PREDICTIONS_KEY = "betiq:predictions"  # main._save_predictions_cache


def main() -> int:
    r = model_store._client()
    if r is None:
        print("UPSTASH_REDIS_URL isn't set — nothing to read or store. Skipping.")
        return 0
    raw = r.get(PREDICTIONS_KEY)
    preds = [p for p in (json.loads(raw).get("predictions") or []) if p.get("sport") in (None, "football")] if raw else []
    if not preds:
        print("No predictions in Redis yet (the API server saves them after each rebuild). Skipping.")
        return 0
    state = referees.load(r)
    known = state.get("appointments") or {}
    today = datetime.now(timezone.utc).date()

    async def lookup():
        from curl_cffi.requests import AsyncSession
        async with AsyncSession(impersonate=intl.IMPERSONATE, timeout=20) as client:
            return await referees.fetch(client, preds, known, today)

    found, report = asyncio.run(lookup())
    report["source"] = "sofascore (GitHub)"
    print("Report:", json.dumps(report, indent=1))
    if not report.get("days") or (referees.blocked(report) and not report.get("new")):
        print("Couldn't read SofaScore from GitHub either — leaving the stored referees as they are.")
        return 0
    # Keep the server's API-Football finds for matches SofaScore didn't cover
    current = {k: v for k, v in known.items() if k.rsplit("|", 1)[-1] >= today.isoformat()}
    referees.save(r, {"at": datetime.now(timezone.utc).isoformat(), "appointments": {**current, **found},
                      "report": report, "trigger": "github"})
    print(f"Stored {len(current) + len([k for k in found if k not in current])} referees "
          f"({report['new']} new) for {len(preds)} predictions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
