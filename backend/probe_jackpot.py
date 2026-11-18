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


BUNDLES = ["https://s.sporty.net/global/main/modules/main/mobile/jackpot/index.5628364638.js",
           "https://s.sporty.net/global/main/modules/main/desktop/jackpot/index.03ca866db4.js"]
PATHS = ["/jackpot/period", "/jackpot/periodOptions", "/jackpot/previous", "/jackpot/result",
         "/jackpot/combinations", "jackpot/combinations", "jackpot/result"]
PREFIXES = ["", "/factsCenter", "/games", "/sportygames", "/jackpot-api", "/orders", "/marketing", "/act"]


async def contexts(session) -> None:
    """The code around each jackpot API path: how its URL is built."""
    print("\n== code around the jackpot API paths ==")
    for url in BUNDLES:
        try:
            text = (await session.get(url, headers={"Accept": "*/*"})).text or ""
        except Exception as e:
            print(f"  {url}: {type(e).__name__}")
            continue
        print(f"-- {url} ({len(text)} chars)")
        for p in PATHS:
            for m in list(re.finditer(re.escape(p), text))[:2]:
                print(f"  [{p}] …{text[max(0, m.start() - 350):m.end() + 250]}…\n")
        # The API base the bundle uses
        for m in list(re.finditer(r"baseURL|apiPrefix|API_HOST|/api/|\$\{[a-zA-Z.]*country", text))[:12]:
            print(f"  [base] …{text[max(0, m.start() - 120):m.end() + 160]}…")


async def try_paths(session) -> None:
    print("\n== jackpot paths under likely prefixes ==")
    for pre in PREFIXES:
        for p in ("/jackpot/period", "/jackpot/periodOptions", "/jackpot/previous"):
            url = sportybet.BASE + pre + p
            try:
                r = await session.get(url)
            except Exception as e:
                print(f"  {url}: {type(e).__name__}")
                continue
            print(f"  {pre + p}: HTTP {r.status_code} · {(r.text or '').strip()[:400]}")


async def wrapper(session) -> None:
    """How the bundle's request helper (tB / ao) sends a jackpot call."""
    print("\n== the request helper ==")
    for url, name in ((BUNDLES[0], "tB"), (BUNDLES[1], "ao")):
        text = (await session.get(url, headers={"Accept": "*/*"})).text or ""
        for pat in (rf"function {name}\(", rf"[,;\s]{name}=function", rf"[,;\s]{name}=\(", rf"var {name}="):
            for m in list(re.finditer(pat, text))[:2]:
                print(f"  [{name} {pat}] …{text[max(0, m.start() - 200):m.end() + 900]}…\n")


async def try_posts(session) -> None:
    print("\n== jackpot endpoints by POST ==")
    for p in ("/jackpot/period", "/jackpot/periodOptions", "/jackpot/banner"):
        for kind, kw in (("json {}", {"json": {}}), ("no body", {}),
                         ("json sport", {"json": {"sportId": sportybet.FOOTBALL}})):
            try:
                r = await session.post(sportybet.BASE + p, headers={"Content-Type": "application/json"}, **kw)
            except Exception as e:
                print(f"  POST {p} ({kind}): {type(e).__name__}")
                continue
            print(f"  POST {p} ({kind}): HTTP {r.status_code} · {(r.text or '').strip()[:1500]}")


async def with_headers(session) -> None:
    """The page's own request: GET with OperId, Currency and CountryCode."""
    print("\n== GET with the page's headers ==")
    for base in (sportybet.BASE, "https://www.sportybet.com"):
        for oper in ("1", "2"):
            for cc in ("ng", "NG"):
                h = {"Content-Type": "application/json;charset=UTF-8", "OperId": oper, "Currency": "NGN",
                     "CountryCode": cc}
                url = base + "/jackpot/period"
                try:
                    r = await session.get(url, headers=h)
                except Exception as e:
                    print(f"  {url} oper {oper} cc {cc}: {type(e).__name__}")
                    continue
                print(f"  {url} oper {oper} cc {cc}: HTTP {r.status_code} · {(r.text or '').strip()[:600]}")
                if r.status_code == 200 and '"bizCode":10000' in (r.text or ""):
                    for p in ("/jackpot/period", "/jackpot/periodOptions", "/jackpot/banner"):
                        rr = await session.get(base + p, headers=h)
                        print(f"\n  ---- {p}: HTTP {rr.status_code}\n{(rr.text or '')[:12000]}\n")
                    return
    # The page's own settings for those headers
    html = (await session.get(sportybet.SITE + "jackpot", headers={"Accept": "text/html"})).text or ""
    for k in ("operId", "reqCountryShortCode", "currency"):
        for m in list(re.finditer(k, html))[:3]:
            print(f"  [{k}] …{html[max(0, m.start() - 80):m.end() + 120]}…")


async def main() -> None:
    session = sportybet.shared_session()
    await with_headers(session)


if __name__ == "__main__":
    asyncio.run(main())
