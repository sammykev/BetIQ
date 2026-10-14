"""
Probe for wider European league data (reads only): ClubElo's ratings, the
API-Football account's quota and its European top flights, and how far
back SportyBet's results list goes.

    python europe_data_probe.py    (GitHub Actions: "Basketball data", job europe_probe)
"""

import asyncio
import csv
import io
import os
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import httpx

UEFA = ["Albania", "Andorra", "Armenia", "Austria", "Azerbaijan", "Belarus", "Belgium", "Bosnia", "Bulgaria",
        "Croatia", "Cyprus", "Czech-Republic", "Denmark", "England", "Estonia", "Faroe-Islands", "Finland", "France",
        "Georgia", "Germany", "Gibraltar", "Greece", "Hungary", "Iceland", "Ireland", "Israel", "Italy",
        "Kazakhstan", "Kosovo", "Latvia", "Lithuania", "Luxembourg", "Macedonia", "Malta", "Moldova", "Montenegro",
        "Netherlands", "Northern-Ireland", "Norway", "Poland", "Portugal", "Romania", "Russia", "San-Marino",
        "Scotland", "Serbia", "Slovakia", "Slovenia", "Spain", "Sweden", "Switzerland", "Turkey", "Ukraine", "Wales"]


async def clubelo(c):
    d = date.today().isoformat()
    for url in (f"http://api.clubelo.com/{d}", f"https://api.clubelo.com/{d}"):
        try:
            r = await c.get(url, timeout=30)
            rows = list(csv.DictReader(io.StringIO(r.text)))
            print(f"ClubElo {url}: {r.status_code}, {len(rows)} clubs; columns {list(rows[0]) if rows else '-'}")
            if rows:
                by = Counter(x.get("Country") for x in rows)
                print(f"  countries {len(by)}: {dict(by.most_common())}")
                for name in ("Pafos", "Kaunas", "Hajduk", "CSKA Sofia", "Crvena Zvezda", "Celje", "Kairat",
                             "Lincoln", "Iberia", "Egnatia", "Borac", "Ararat", "Riga", "KuPS", "Mjaellby"):
                    hit = [f"{x['Club']} {x['Elo'][:6]}" for x in rows if name.lower() in x["Club"].lower()]
                    print(f"  {name}: {hit[:3]}")
                old = await c.get(url.replace(d, "2024-08-01"), timeout=30)
                print(f"  history 2024-08-01: {old.status_code}, {len(old.text.splitlines())} lines")
                return
        except Exception as e:
            print(f"ClubElo {url}: {type(e).__name__}: {e}")


async def api_football(c):
    key = os.getenv("APIFOOTBALL_KEY", "").strip()
    if not key:
        print("APIFOOTBALL_KEY not set")
        return
    h = {"x-apisports-key": key}
    st = (await c.get("https://v3.football.api-sports.io/status", headers=h, timeout=30)).json()
    print(f"\nAPI-Football status: {st.get('response')}")
    lg = (await c.get("https://v3.football.api-sports.io/leagues", params={"current": "true", "type": "league"},
                      headers=h, timeout=60)).json()
    rows = lg.get("response") or []
    print(f"current leagues: {len(rows)} (errors {lg.get('errors')})")
    for country in UEFA:
        cand = [x for x in rows if (x.get("country") or {}).get("name", "").replace(" ", "-") == country]
        print(f"  {country}: " + "; ".join(f"{x['league']['id']}={x['league']['name']}" for x in cand[:6]))


async def sportybet_depth():
    import results_feed
    for back in (30, 90, 180, 365):
        d = (datetime.now(timezone.utc).date() - timedelta(days=back)).isoformat()
        try:
            got = await results_feed.fetch_sportybet_results(d, max_pages=3)
            print(f"SportyBet results {back} days back ({d}): {len(got)} (first 3 pages)")
        except Exception as e:
            print(f"SportyBet results {back} days back: {type(e).__name__}: {e}")


def stored() -> bool:
    """What the server and the collectors have stored, when Redis is reachable."""
    import model_store
    r = model_store._client()
    if r is None:
        return False
    import clubelo as ce
    import collect_sb_leagues as sbl
    print(f"ClubElo (fetched by the server): {r.get(ce.STATUS_KEY)}; {len(ce.latest(r))} clubs in the latest")
    print(f"SportyBet leagues: {sbl.summary(sbl.load(r))}")
    return True


async def main():
    if stored():
        return
    async with httpx.AsyncClient(follow_redirects=True) as c:
        await clubelo(c)
        await api_football(c)
    print()
    await sportybet_depth()


if __name__ == "__main__":
    asyncio.run(main())
