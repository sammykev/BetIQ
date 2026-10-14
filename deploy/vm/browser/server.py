"""
A headless Chrome that reads SofaScore's API for the API server. SofaScore
answers a datacenter address with 403 "challenge" (and GitHub's with
"Forbidden"); a real browser that has opened www.sofascore.com, and so
passed its check, may read the same API pages from inside that tab.

Only the API container reaches it (docker compose network, no published
port), and it reads only SofaScore API paths:

    GET /sofascore?path=/api/v1/sport/basketball/scheduled-events/2026-10-07
        -> {"status": 200, "body": "<the page's text>"}
    GET /health -> {"ok": true, "requests": N, "warm": bool}
"""

import asyncio
import json
import os
import re
import time
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import async_playwright

PORT = int(os.getenv("PORT", "8100"))
HOME = "https://www.sofascore.com/"
API = "https://www.sofascore.com"
PATH_OK = re.compile(r"^/api/v1/[A-Za-z0-9_\-/.]{1,200}$")
WARM_WAIT = 6.0             # seconds on the home page for its check to run
RESTART_AFTER = 300         # requests before a fresh browser
IDLE_CLOSE = 15 * 60        # close the browser after this long unused (memory)
PAUSE = 0.4                 # between requests
FETCH_JS = """async (url) => {
  const r = await fetch(url, {credentials: "include", headers: {"Accept": "application/json"}});
  return [r.status, await r.text()];
}"""


class Browser:
    def __init__(self):
        self.pw = None
        self.browser = None
        self.page = None
        self.used = 0
        self.total = 0
        self.last = 0.0
        self.lock = asyncio.Lock()

    async def _close(self):
        for x in (self.browser, self.pw):
            try:
                if x is self.pw and x:
                    await x.stop()
                elif x:
                    await x.close()
            except Exception:
                pass
        self.pw = self.browser = self.page = None

    async def _open(self):
        await self._close()
        self.pw = await async_playwright().start()
        # channel "chromium": Chrome's own headless mode, not the stripped-down shell
        self.browser = await self.pw.chromium.launch(
            channel="chromium", headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"])
        ua = f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) " \
             f"Chrome/{self.browser.version} Safari/537.36"
        ctx = await self.browser.new_context(user_agent=ua, locale="en-US", viewport={"width": 1366, "height": 900})
        await ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        self.page = await ctx.new_page()
        self.used = 0
        await self._warm()

    async def _warm(self):
        await self.page.goto(HOME, wait_until="domcontentloaded", timeout=45_000)
        await asyncio.sleep(WARM_WAIT)

    async def _fetch(self, path: str):
        return await self.page.evaluate(FETCH_JS, API + path)

    async def get(self, path: str):
        async with self.lock:
            try:
                if self.page is None or self.used >= RESTART_AFTER:
                    await self._open()
                status, body = await self._fetch(path)
                if status == 403:
                    # Its check again (a new cookie), then once more
                    await self._warm()
                    status, body = await self._fetch(path)
            except Exception as e:
                await self._close()
                return {"status": 0, "error": f"{type(e).__name__}: {e}"[:300]}
            finally:
                self.used += 1
                self.total += 1
                self.last = time.time()
                await asyncio.sleep(PAUSE)
            return {"status": status, "body": body}

    async def idle_closer(self):
        while True:
            await asyncio.sleep(60)
            if self.page is not None and time.time() - self.last > IDLE_CLOSE:
                async with self.lock:
                    await self._close()


B = Browser()


async def handle(reader, writer):
    try:
        line = (await asyncio.wait_for(reader.readline(), 10)).decode("latin-1")
        while (await asyncio.wait_for(reader.readline(), 10)) not in (b"\r\n", b"\n", b""):
            pass
        method, target = (line.split() + ["", ""])[:2]
        url = urlsplit(target)
        if method != "GET":
            code, out = 405, {"error": "GET only"}
        elif url.path == "/health":
            code, out = 200, {"ok": True, "requests": B.total, "warm": B.page is not None}
        elif url.path == "/sofascore":
            path = (parse_qs(url.query).get("path") or [""])[0]
            if not PATH_OK.match(path) or ".." in path:
                code, out = 400, {"error": "path must be a SofaScore /api/v1/ path"}
            else:
                code, out = 200, await B.get(path)
        else:
            code, out = 404, {"error": "not found"}
        body = json.dumps(out).encode()
        writer.write(f"HTTP/1.1 {code} X\r\nContent-Type: application/json\r\n"
                     f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()


async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", PORT)
    asyncio.create_task(B.idle_closer())
    print(f"SofaScore browser on :{PORT}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
