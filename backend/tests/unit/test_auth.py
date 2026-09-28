"""
Tests for auth.py — Clerk session-token verification and the uid it yields.

Tokens are real RS256 JWTs signed with a throwaway key; the JWKS lookup is
replaced with a stub that returns the matching public key.
"""

import asyncio
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

import auth

ISSUER = "https://clerk.betiq.test"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _StubJWKS:
    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=KEY.public_key())


def token(sub="user_alice", iss=ISSUER, exp_in=60, key=KEY, **extra):
    now = int(time.time())
    claims = {"sub": sub, "iss": iss, "iat": now, "nbf": now, "exp": now + exp_in, **extra}
    if sub is None:
        claims.pop("sub")
    return jwt.encode(claims, key, algorithm="RS256")


def request(bearer=None):
    headers = {"authorization": f"Bearer {bearer}"} if bearer else {}
    return SimpleNamespace(headers=headers)


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(auth, "CLERK_ISSUER", ISSUER)
    monkeypatch.setattr(auth, "AUTHORIZED_PARTIES", [])
    monkeypatch.setattr(auth, "_jwks_client", _StubJWKS())


def run(coro):
    return asyncio.run(coro)


class TestVerifySessionToken:
    def test_valid_token_returns_subject(self, enforced):
        assert auth.verify_session_token(token()) == "user_alice"

    def test_wrong_issuer_rejected(self, enforced):
        # e.g. a token from someone else's Clerk instance
        with pytest.raises(HTTPException) as e:
            auth.verify_session_token(token(iss="https://evil.clerk.accounts.dev"))
        assert e.value.status_code == 401

    def test_expired_token_rejected(self, enforced):
        with pytest.raises(HTTPException) as e:
            auth.verify_session_token(token(exp_in=-120))
        assert e.value.status_code == 401

    def test_forged_signature_rejected(self, enforced):
        with pytest.raises(HTTPException) as e:
            auth.verify_session_token(token(key=OTHER_KEY))
        assert e.value.status_code == 401

    def test_token_without_subject_rejected(self, enforced):
        with pytest.raises(HTTPException):
            auth.verify_session_token(token(sub=None))

    def test_authorized_party_enforced_when_configured(self, enforced, monkeypatch):
        monkeypatch.setattr(auth, "AUTHORIZED_PARTIES", ["https://predict-withbetiq.vercel.app"])
        assert auth.verify_session_token(token(azp="https://predict-withbetiq.vercel.app")) == "user_alice"
        with pytest.raises(HTTPException):
            auth.verify_session_token(token(azp="https://phishing.example"))

    def test_the_mobile_apps_tokens_have_no_site_and_pass(self, enforced, monkeypatch):
        monkeypatch.setattr(auth, "AUTHORIZED_PARTIES", ["https://predict-withbetiq.vercel.app"])
        assert auth.verify_session_token(token()) == "user_alice"


class TestRequireUser:
    def test_uses_verified_subject(self, enforced):
        assert run(auth.require_user(request(token()), "")) == "user_alice"

    def test_missing_token_is_401(self, enforced):
        with pytest.raises(HTTPException) as e:
            run(auth.require_user(request(None), "user_alice"))
        assert e.value.status_code == 401

    def test_claiming_another_user_is_403(self, enforced):
        with pytest.raises(HTTPException) as e:
            run(auth.require_user(request(token(sub="user_alice")), "user_bob"))
        assert e.value.status_code == 403

    def test_legacy_mode_trusts_client_uid(self, monkeypatch):
        monkeypatch.setattr(auth, "CLERK_ISSUER", "")
        assert run(auth.require_user(request(None), "user_bob")) == "user_bob"

    def test_legacy_mode_still_needs_a_uid(self, monkeypatch):
        monkeypatch.setattr(auth, "CLERK_ISSUER", "")
        with pytest.raises(HTTPException) as e:
            run(auth.require_user(request(None), ""))
        assert e.value.status_code == 400


class TestOptionalUser:
    def test_returns_none_without_token(self, enforced):
        assert run(auth.optional_user(request(None))) is None

    def test_returns_none_for_bad_token(self, enforced):
        assert run(auth.optional_user(request(token(key=OTHER_KEY)))) is None

    def test_returns_subject_for_good_token(self, enforced):
        assert run(auth.optional_user(request(token()))) == "user_alice"
