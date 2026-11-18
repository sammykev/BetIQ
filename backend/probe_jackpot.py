"""
Find SportyBet's weekly jackpot: where its matches and rules come from.

1. Load the jackpot pages and the JavaScript bundles they pull in, and print
   every string in them that mentions a jackpot (API paths, keys, labels).
2. Try the likeliest API paths and print what each answers.

Read-only: nothing is booked or saved.

    python probe_jackpot.py      (GitHub Actions: "Jackpot probe", by hand)
"""

import asyncio
import json
import re
from typing import List, Set

import sportybet

PAGES = ["jackpot", "m/jackpot", "sport/jackpot", "jackpot/football", "m/sport/jackpot"]
CANDIDATES = [
    "/factsCenter/jackpot", "/factsCenter/jackpots", "/factsCenter/jackpotList", "/factsCenter/jackpotInfo",
    "/factsCenter/jackpot/list", "/factsCenter/jackpot/current", "/factsCenter/jackpotEvents",
    "/factsCenter/jackpotResult", "/factsCenter/jackpotHistory",
    "/jackpot/list", "/jackpot/current", "/jackpot/info", "/jackpot/events", "/jackpot/rounds",
    "/orders/jackpot/list", "/orders/jackpot", "/jackpot", "/jackpots",
    "/marketing/jackpot", "/promotion/jackpot", "/act/jackpot", "/sportsbook/jackpot",
]
STRINGS = re.compile(r"""["'`]([^"'`\s]{0,120}[Jj]ackpot[^"'`\s]{0,120})["'`]""")
SCRIPTS = re.compile(r"""<script[^>]+src=["']([^"']+)["']""")
MAX_BUNDLE = 6_000_000


def absolute(src: str) -> str:
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return "https://www.sportybet.com" + src
    return src if src.startswith("http") else sportybet.SITE + src


async def strings_in_pages(session) -> None:
    seen_scripts: Set[str] = set()
    found: Set[str] = set()
    for page in PAGES:
        url = sportybet.SITE + page
        try:
            r = await session.get(url, headers={"Accept": "text/html"})
        except Exception as e:
            print(f"page {url}: {type(e).__name__}: {e}")
            continue
        html = r.text or ""
        print(f"page {url}: HTTP {r.status_code}, {len(html)} chars, title "
              f"{(re.search(r'<title>(.*?)</title>', html, re.S) or [None, '?'])[1][:80]!r}")
        found.update(STRINGS.findall(html))
        for src in SCRIPTS.findall(html):
            seen_scripts.add(absolute(src))
    print(f"\n{len(seen_scripts)} scripts")
    for src in sorted(seen_scripts):
        try:
            r = await session.get(src, headers={"Accept": "*/*"})
        except Exception as e:
            print(f"  {src}: {type(e).__name__}")
            continue
        text = (r.text or "")[:MAX_BUNDLE]
        hits = STRINGS.findall(text)
        if hits:
            print(f"  {src}: {len(text)} chars, {len(hits)} jackpot strings")
        found.update(hits)
        # API paths near a jackpot mention (the bundles build URLs from parts)
        for m in re.finditer(r"[Jj]ackpot", text):
            near = text[max(0, m.start() - 200):m.end() + 200]
            for p in re.findall(r"""["'`](/[a-zA-Z0-9_/\-{}$.]{3,80})["'`]""", near):
                found.add("path near jackpot: " + p)
    print(f"\n== {len(found)} strings mentioning a jackpot ==")
    for s in sorted(found)[:400]:
        print("  " + s)


async def try_candidates(session) -> List[str]:
    print("\n== candidate API paths ==")
    ok = []
    for path in CANDIDATES:
        for params in ({}, {"sportId": sportybet.FOOTBALL}):
            try:
                r = await session.get(sportybet.BASE + path, params=params)
            except Exception as e:
                print(f"  {path} {params}: {type(e).__name__}")
                continue
            body = (r.text or "").strip().replace("\n", " ")
            biz = None
            try:
                biz = json.loads(body).get("bizCode")
            except Exception:
                pass
            print(f"  {path} {params}: HTTP {r.status_code} bizCode {biz} · {body[:300]}")
            if r.status_code == 200 and biz == sportybet.OK:
                ok.append(path)
    return ok


async def main() -> None:
    session = sportybet.shared_session()
    await strings_in_pages(session)
    ok = await try_candidates(session)
    print(f"\nanswered OK: {ok or 'none'}")


if __name__ == "__main__":
    asyncio.run(main())
