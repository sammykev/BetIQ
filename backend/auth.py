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
