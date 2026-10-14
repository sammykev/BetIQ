"""
Which Europa and Conference League fixtures ESPN has, read the two ways
europe_fixtures.fetch can (a date range, then day by day), for the next
days; and SportyBet's count for the same days. Reads only.

    python europe_check.py      (GitHub Actions: "Basketball data", job europe)
"""

import asyncio
from datetime import date, datetime, timedelta, timezone

import europe_fixtures as ef
import international_fixtures as intl


async def main() -> None:
    from curl_cffi.requests import AsyncSession
    today = datetime.now(timezone.utc).date()
    end = today + timedelta(days=3)
    async with AsyncSession(impersonate=intl.IMPERSONATE, timeout=20) as c:
        for code, (slug, name, *_r) in ef.COMPETITIONS.items():
            pages, err = await intl._espn_pages(c, slug, today, end)
            rng = ef._upcoming(pages, slug, code, today, end)
            dpages, derr = await ef._daily_pages(c, slug, today, end)
            daily = ef._upcoming(dpages, slug, code, today, end)
            print(f"\n{name} ({slug}): range {len(rng)} (pages {len(pages or [])}, err {err}) · "
                  f"day by day {len(daily)} (err {derr})")
            by_day = {}
            for f in daily:
                by_day.setdefault(f["date"], []).append(f)
            for d in sorted(by_day):
                print(f"  {d}: {len(by_day[d])} — " + "; ".join(f"{f['home']} v {f['away']}" for f in by_day[d]))
            missing = {f["match_id"] for f in daily} - {f["match_id"] for f in rng}
            if missing:
                print(f"  the range read missed {len(missing)}")
            for p in (pages or [])[:3]:
                print(f"  range page: {len(p.get('events') or [])} events, keys {list(p)[:6]}")


if __name__ == "__main__":
    asyncio.run(main())
