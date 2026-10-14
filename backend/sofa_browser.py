"""
SofaScore's API read through the headless Chrome beside the API server
(deploy/vm/browser). SofaScore answers this server's address with 403
"challenge" for plain and Chrome-impersonating requests; a real browser
tab that has opened www.sofascore.com may still read it.

Off unless SOFA_BROWSER_URL is set (docker compose sets it for the api).
"""

import json
import os
from typing import Any, Dict, Optional, Tuple

TIMEOUT = 90.0     # the first request opens Chrome and the home page


def url() -> Optional[str]:
    return (os.getenv("SOFA_BROWSER_URL") or "").rstrip("/") or None


def on() -> bool:
    return url() is not None


async def get(path: str) -> Dict[str, Any]:
    """{"status", "body"} or {"status": 0, "error"} for an /api/v1/... path."""
    base = url()
    if not base:
        return {"status": 0, "error": "SOFA_BROWSER_URL isn't set"}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            resp = await c.get(f"{base}/sofascore", params={"path": path})
        out = resp.json()
        return out if isinstance(out, dict) else {"status": 0, "error": "odd answer"}
    except Exception as e:
        return {"status": 0, "error": f"{type(e).__name__}: {e}"[:300]}


async def get_json(path: str) -> Tuple[Optional[Dict], Optional[str]]:
    """(the page's JSON, None) or (None, why not)."""
    res = await get(path)
    if res.get("status") != 200:
        return None, res.get("error") or f"{res.get('status')}: {(res.get('body') or '')[:120]}"
    try:
        return json.loads(res.get("body") or ""), None
    except ValueError:
        return None, f"not JSON: {(res.get('body') or '')[:120]}"
