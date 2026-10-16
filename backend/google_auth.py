"""Direct Google OAuth helpers (operator-owned credentials).

Google sign-in talks to Google directly — no third-party auth broker.
Enabled only when the operator sets GOOGLE_CLIENT_ID (and, for the
authorization-code exchange, GOOGLE_CLIENT_SECRET). When unset, callers
hide the Google buttons in the UI and the session-exchange endpoints
return a clean 503.

Two verification paths, both validated server-side by Google itself:
  - `exchange_code`: authorization-code flow. The browser is redirected
    to accounts.google.com and returns with `?code=...`; we exchange the
    code at Google's token endpoint (proves possession + client secret)
    and verify the resulting id_token.
  - `verify_id_token`: verifies an id_token (e.g. from Google Identity
    Services) via Google's tokeninfo endpoint, checking audience and
    email_verified.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


def google_client_id() -> str:
    return (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()


def google_client_secret() -> str:
    return (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()


def google_configured() -> bool:
    """Google sign-in is offered when a client id is configured."""
    return bool(google_client_id())


def public_config() -> dict:
    """Non-secret config the frontend needs to render/start the flow."""
    return {
        "google_enabled": google_configured(),
        "google_client_id": google_client_id() or None,
    }


async def verify_id_token(id_token: str) -> Optional[dict]:
    """Verify a Google id_token via Google's tokeninfo endpoint.

    Returns {email, name, picture} on success, None on any failure
    (bad signature/expiry, wrong audience, unverified email)."""
    if not id_token or not google_client_id():
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as hc:
            r = await hc.get(_TOKENINFO_URL, params={"id_token": id_token})
            if r.status_code != 200:
                return None
            info = r.json()
    except Exception:
        return None
    if info.get("aud") != google_client_id():
        return None
    email = (info.get("email") or "").lower()
    if not email or str(info.get("email_verified")).lower() != "true":
        return None
    return {
        "email": email,
        "name": info.get("name") or email.split("@")[0],
        "picture": info.get("picture") or "",
    }


async def exchange_code(code: str, redirect_uri: str) -> Optional[dict]:
    """Exchange an authorization code for tokens and verify the id_token.

    Returns {email, name, picture} on success, None on any failure."""
    if not code or not google_client_id() or not google_client_secret():
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as hc:
            r = await hc.post(_TOKEN_URL, data={
                "code": code,
                "client_id": google_client_id(),
                "client_secret": google_client_secret(),
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            })
            if r.status_code != 200:
                return None
            tokens = r.json()
    except Exception:
        return None
    id_token = tokens.get("id_token")
    if not id_token:
        return None
    return await verify_id_token(id_token)


async def resolve_google_identity(*, code: str = "", id_token: str = "",
                                  redirect_uri: str = "") -> Optional[dict]:
    """One entry point for the session-exchange endpoints: prefer the
    authorization-code exchange, fall back to a bare id_token."""
    if code:
        return await exchange_code(code, redirect_uri)
    if id_token:
        return await verify_id_token(id_token)
    return None
