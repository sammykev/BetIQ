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
        azp = str(claims.get("azp", "")).rstrip("/")
        if azp not in AUTHORIZED_PARTIES:
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


# ── Premium ────────────────────────────────────────────────────────────────
# Premium lives in the Clerk user's public metadata ({subscription:
# "premium", subscription_expires}), set by the site's /api/subscribe after
# Paystack confirms a payment. Reading it needs CLERK_SECRET_KEY (Clerk
# Dashboard → API keys); without it premium isn't enforced here.

CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY", "").strip()
PREMIUM_CACHE_SECONDS = 300
_premium_cache: dict = {}  # uid -> (checked at, premium)


def premium_enforced() -> bool:
    return auth_enforced() and bool(CLERK_SECRET_KEY)


def is_premium_metadata(meta: Optional[dict], now: Optional[float] = None) -> bool:
    import time
    from datetime import datetime
    meta = meta or {}
    if meta.get("subscription") != "premium":
        return False
    try:
        expires = datetime.fromisoformat(str(meta.get("subscription_expires")).replace("Z", "+00:00"))
    except ValueError:
        return False
    return expires.timestamp() > (time.time() if now is None else now)


async def user_is_premium(uid: str) -> bool:
    """Whether a Clerk user has an unexpired premium subscription (cached briefly)."""
    import time
    import httpx
    hit = _premium_cache.get(uid)
    if hit and time.time() - hit[0] < PREMIUM_CACHE_SECONDS:
        return hit[1]
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"https://api.clerk.com/v1/users/{uid}",
                             headers={"Authorization": f"Bearer {CLERK_SECRET_KEY}"})
    if r.status_code == 404:
        premium = False
    elif r.status_code != 200:
        raise HTTPException(status_code=503, detail="Couldn't check your subscription. Try again.")
    else:
        premium = is_premium_metadata(r.json().get("public_metadata"))
    _premium_cache[uid] = (time.time(), premium)
    if len(_premium_cache) > 5000:
        _premium_cache.clear()
    return premium
