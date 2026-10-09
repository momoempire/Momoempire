"""Stripe configuration helpers — never use placeholder API keys."""
from __future__ import annotations

import logging
import os

import stripe
from fastapi import HTTPException

log = logging.getLogger("stripe_config")

_PLACEHOLDER_KEYS = frozenset({
    "sk_test_emergent",
    "sk_test_placeholder",
    "sk_test_replace_me",
})


def app_env() -> str:
    return (os.getenv("APP_ENV") or "development").lower().strip()


def is_production() -> bool:
    return app_env() in {"production", "prod"}


def configure_stripe_api_key() -> bool:
    """Bind stripe.api_key from STRIPE_SECRET_KEY.

    Never falls back to a placeholder sk_test_* value.
    Returns True when a non-empty, non-placeholder key is configured.
    In production, logs an error if the key is missing.
    """
    raw = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    if raw and raw not in _PLACEHOLDER_KEYS:
        stripe.api_key = raw
        return True
    stripe.api_key = ""
    if raw in _PLACEHOLDER_KEYS:
        log.error("Refusing placeholder STRIPE_SECRET_KEY value")
    if is_production():
        log.error(
            "STRIPE_SECRET_KEY is missing or invalid in production (APP_ENV=%s) — Stripe calls will fail",
            app_env(),
        )
    else:
        log.warning("STRIPE_SECRET_KEY unset — Stripe demo mode (no real charges / no paid marks via unsigned webhooks)")
    return False


def require_stripe_api_key() -> None:
    """Call-site guard: fail loudly when Stripe is needed but not configured."""
    if not configure_stripe_api_key():
        raise HTTPException(503, "Stripe is not configured (STRIPE_SECRET_KEY missing)")


def require_webhook_secret(*env_names: str) -> str:
    """Return the first non-empty webhook signing secret, or raise 503.

    Unsigned / empty-secret processing is never allowed in any environment.
    """
    for name in env_names:
        secret = (os.environ.get(name) or "").strip()
        if secret:
            return secret
    names = " or ".join(env_names) if env_names else "webhook secret"
    raise HTTPException(503, f"Stripe webhook secret not configured ({names})")


def construct_stripe_event(payload: bytes, sig_header: str, *secret_env_names: str):
    """Verify Stripe-Signature via construct_event; reject missing secret or bad sig."""
    secret = require_webhook_secret(*secret_env_names)
    if not (sig_header or "").strip():
        raise HTTPException(400, "Missing Stripe-Signature header")
    try:
        return stripe.Webhook.construct_event(payload, sig_header, secret)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(400, "Invalid signature") from None
    except Exception as e:
        raise HTTPException(400, f"Bad webhook payload: {e}") from e
