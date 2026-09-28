"""
Clerk session authentication for user-scoped endpoints.

The frontend sends the signed-in user's Clerk session token as
`Authorization: Bearer <token>`. We verify it against the Clerk instance's
public keys and take the user id from the token — never from the request
body or query string, which anyone can set.

Configuration (Render env vars):
- CLERK_ISSUER: the Clerk Frontend API URL, e.g. https://clerk.example.com or
  https://your-app.clerk.accounts.dev (Clerk Dashboard → API keys). Required
  to enforce auth. Only tokens issued by exactly this URL are accepted.
- CLERK_AUTHORIZED_PARTIES (optional): comma-separated frontend origins the
  token must come from (its `azp` claim), e.g. https://predict-withbetiq.vercel.app

Until CLERK_ISSUER is set, requests fall back to the legacy `uid` the client
sends (the previous, unauthenticated behaviour) and a warning is logged, so
deploying this doesn't lock users out before the variable is configured.
"""

import asyncio
import os
from typing import List, Optional

import jwt
from fastapi import HTTPException, Request

CLERK_ISSUER = os.getenv("CLERK_ISSUER", "").strip().rstrip("/")
AUTHORIZED_PARTIES: List[str] = [
    p.strip().rstrip("/") for p in os.getenv("CLERK_AUTHORIZED_PARTIES", "").split(",") if p.strip()
]

_jwks_client: Optional[jwt.PyJWKClient] = None
_warned_legacy = False


def auth_enforced() -> bool:
    return bool(CLERK_ISSUER)


def _jwks() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        # Keys are cached; Clerk rotates rarely, and an unknown `kid` refetches.
        _jwks_client = jwt.PyJWKClient(f"{CLERK_ISSUER}/.well-known/jwks.json", cache_keys=True, lifespan=3600)
    return _jwks_client


def verify_session_token(token: str) -> str:
    """Return the Clerk user id (`sub`) for a valid session token, else raise HTTPException(401)."""
    try:
        signing_key = _jwks().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=CLERK_ISSUER,
            leeway=10,  # small allowance for clock skew
            options={"require": ["exp", "iat", "sub", "iss"], "verify_aud": False},
        )
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"invalid_session: {type(e).__name__}")
    if AUTHORIZED_PARTIES:
        # A browser's token names the site it was made for (azp): it must be
        # ours. The mobile app's tokens have none (there's no web origin), and
        # are signed by the same Clerk instance, so they pass.
        azp = str(claims.get("azp", "") or "").rstrip("/")
        if azp and azp not in AUTHORIZED_PARTIES:
            raise HTTPException(status_code=401, detail="invalid_session: unauthorized_party")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise HTTPException(status_code=401, detail="invalid_session: no_subject")
    return sub


def _bearer(request: Request) -> Optional[str]:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


async def optional_user(request: Request) -> Optional[str]:
    """The verified user id if a valid token is present, else None (public endpoints)."""
    token = _bearer(request)
    if not token or not auth_enforced():
        return None
    try:
        return await asyncio.to_thread(verify_session_token, token)
    except HTTPException:
        return None


async def require_user(request: Request, claimed_uid: str = "") -> str:
    """
    The user id this request is allowed to act as.

    Enforced mode: requires a valid token; a `uid` the client also sent must
    match it (403 otherwise). Legacy mode: returns the client's `uid`.
    """
    global _warned_legacy
    if not auth_enforced():
        if not _warned_legacy:
            print("[Auth] WARNING: CLERK_ISSUER is not set — user endpoints trust the "
                  "client-supplied uid. Set CLERK_ISSUER to enforce authentication.")
            _warned_legacy = True
        if not claimed_uid:
            raise HTTPException(status_code=400, detail="Missing uid")
        return claimed_uid

    token = _bearer(request)
    if not token:
        raise HTTPException(status_code=401, detail="not_authenticated")
    uid = await asyncio.to_thread(verify_session_token, token)
    if claimed_uid and claimed_uid != uid:
        raise HTTPException(status_code=403, detail="uid_mismatch")
    return uid


# ── Subscriptions ──────────────────────────────────────────────────────────
# The tier lives in the Clerk user's public metadata ({subscription: "lite" |
# "premium", subscription_expires}), set by the site's /api/subscribe after
# Paystack confirms a payment. Reading it needs CLERK_SECRET_KEY (Clerk
# Dashboard → API keys); without it tiers aren't enforced here.

CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY", "").strip()
PREMIUM_CACHE_SECONDS = 60
FREE_CACHE_SECONDS = 10
TIERS = ("free", "lite", "premium")   # lowest to highest
_tier_cache: dict = {}  # uid -> (checked at, tier)


def premium_enforced() -> bool:
    return auth_enforced() and bool(CLERK_SECRET_KEY)


def tier_from_metadata(meta: Optional[dict], now: Optional[float] = None) -> str:
    """"lite" or "premium" while the subscription hasn't expired, else "free"."""
    import time
    from datetime import datetime
    meta = meta or {}
    tier = meta.get("subscription")
    if tier not in ("lite", "premium"):
        return "free"
    try:
        expires = datetime.fromisoformat(str(meta.get("subscription_expires")).replace("Z", "+00:00"))
    except ValueError:
        return "free"
    return tier if expires.timestamp() > (time.time() if now is None else now) else "free"


def is_premium_metadata(meta: Optional[dict], now: Optional[float] = None) -> bool:
    return tier_from_metadata(meta, now) == "premium"


def tier_at_least(tier: str, needed: str) -> bool:
    return TIERS.index(tier if tier in TIERS else "free") >= TIERS.index(needed if needed in TIERS else "free")


async def clerk_user(uid: str):
    """(HTTP status, public metadata) for a Clerk user, from Clerk's API."""
    status, body = await _get_json(f"https://api.clerk.com/v1/users/{uid}",
                                   {"Authorization": f"Bearer {CLERK_SECRET_KEY}"})
    return status, (body or {}).get("public_metadata") or {}


async def clerk_record(uid: str):
    """(HTTP status, the whole Clerk user record) from Clerk's API."""
    status, body = await _get_json(f"https://api.clerk.com/v1/users/{uid}",
                                   {"Authorization": f"Bearer {CLERK_SECRET_KEY}"})
    return status, body or {}


async def clerk_names(uids: list) -> dict:
    """{user id: {"name", "email"}} for Clerk users, 100 per request."""
    import httpx
    out: dict = {}
    ids = [u for u in dict.fromkeys(uids) if u]
    async with httpx.AsyncClient(timeout=15) as client:
        for i in range(0, len(ids), 100):
            chunk = ids[i:i + 100]
            r = await client.get("https://api.clerk.com/v1/users", params=[("user_id", u) for u in chunk] + [("limit", "100")],
                                 headers={"Authorization": f"Bearer {CLERK_SECRET_KEY}"})
            if r.status_code != 200:
                continue
            for u in r.json() or []:
                emails = u.get("email_addresses") or []
                email = next((e.get("email_address") for e in emails if e.get("id") == u.get("primary_email_address_id")),
                             emails[0].get("email_address") if emails else "")
                name = " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x) or u.get("username") or ""
                out[u["id"]] = {"name": name, "email": email or ""}
    return out


async def set_public_metadata(uid: str, values: dict) -> int:
    """Merge `values` into a Clerk user's public metadata; the HTTP status."""
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.patch(f"https://api.clerk.com/v1/users/{uid}/metadata",
                               headers={"Authorization": f"Bearer {CLERK_SECRET_KEY}"},
                               json={"public_metadata": values})
    if r.status_code == 200:
        _tier_cache.pop(uid, None)
    return r.status_code


async def user_tier(uid: str) -> str:
    """A Clerk user's current tier (cached briefly)."""
    import time
    hit = _tier_cache.get(uid)
    # "free" is kept briefly: a new account's trial (or a payment) should show within seconds
    if hit and time.time() - hit[0] < (PREMIUM_CACHE_SECONDS if hit[1] != "free" else FREE_CACHE_SECONDS):
        return hit[1]
    status, meta = await clerk_user(uid)
    if status == 404:
        # A signed-in user Clerk doesn't know: almost always a secret key from
        # another Clerk application than CLERK_ISSUER (see check_clerk_keys)
        print(f"[Auth] WARNING: Clerk has no user {uid} — is CLERK_SECRET_KEY from the same "
              "Clerk application as CLERK_ISSUER?")
        tier = "free"
    elif status != 200:
        raise HTTPException(status_code=503, detail="Couldn't check your subscription. Try again.")
    else:
        tier = tier_from_metadata(meta)
    _tier_cache[uid] = (time.time(), tier)
    if len(_tier_cache) > 5000:
        _tier_cache.clear()
    return tier


async def user_is_premium(uid: str) -> bool:
    """Whether a Clerk user has an unexpired premium subscription."""
    return await user_tier(uid) == "premium"


async def _get_json(url: str, headers: Optional[dict] = None):
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url, headers=headers or {})
    return r.status_code, (r.json() if r.status_code == 200 else None)


async def check_clerk_keys() -> dict:
    """Whether CLERK_SECRET_KEY belongs to the same Clerk application as
    CLERK_ISSUER: both publish the same signing keys. {"ok", "reason"}."""
    if not CLERK_ISSUER or not CLERK_SECRET_KEY:
        return {"ok": False, "reason": "CLERK_ISSUER and CLERK_SECRET_KEY must both be set."}
    try:
        status, mine = await _get_json("https://api.clerk.com/v1/jwks",
                                       {"Authorization": f"Bearer {CLERK_SECRET_KEY}"})
        if status in (401, 403):
            return {"ok": False, "reason": "Clerk rejected CLERK_SECRET_KEY: copy it again from Clerk → API keys."}
        if status != 200:
            return {"ok": False, "reason": f"Couldn't reach Clerk to check the key (HTTP {status})."}
        _, site = await _get_json(f"{CLERK_ISSUER}/.well-known/jwks.json")
    except Exception as e:
        return {"ok": False, "reason": f"Couldn't reach Clerk to check the key ({type(e).__name__})."}
    kids = lambda d: {k.get("kid") for k in (d or {}).get("keys") or []}
    if kids(mine) and kids(site) and not (kids(mine) & kids(site)):
        return {"ok": False, "reason": "CLERK_SECRET_KEY is from a different Clerk application than the site "
                                       "signs in with (e.g. Development vs Production): every subscriber looks Free."}
    return {"ok": True, "reason": ""}
