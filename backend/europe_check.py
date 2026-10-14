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
            for p in (pages or [])[:1]:
                for lg in p.get("leagues") or []:
                    for l in lg.get("logos") or []:
                        r = await c.get(l.get("href"))
                        print(f"  ESPN logo {l.get('rel')} {l.get('width')}x{l.get('height')}: {l.get('href')} "
                              f"→ {r.status_code}, {len(r.content)} bytes")
        import os
        base = os.getenv("SITE_URL", "https://13-140-188-212.sslip.io").rstrip("/")
        for name in ("Europa League", "Conference League", "Champions League"):
            try:
                got = (await c.get(f"{base}/api/competition-logo", params={"name": name})).json()
                img = await c.get(got.get("logo")) if got.get("logo") else None
                print(f"\nSite badge for {name}: {got} → {img.status_code if img else '-'}, "
                      f"{len(img.content) if img else 0} bytes")
            except Exception as e:
                print(f"\nSite badge for {name}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
