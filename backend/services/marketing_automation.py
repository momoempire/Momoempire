"""Marketing automation delivery layer: email/SMS providers + unsubscribe tokens.

Dual-purpose engine (roadmap item #31): the SAME campaign engine serves
  (a) SHOP context — shops market to homeowners (campaigns, segments,
      nurture drip sequences, seasonal blasts, win-backs), and
  (b) PLATFORM context — we market to prospective shops (trial drip,
      feature announcements, case-study blasts).

Providers are abstracted behind small ABCs. The defaults are log-only
providers (zero network): campaigns run end-to-end in demo mode.
Real ESP / Twilio wiring is marked TODO(LIVE) and stays off unless the
operator sets the env vars listed below.

Env vars (operator-facing; coordinator adds to .env.example):
  MARKETING_EMAIL_PROVIDER=log|resend      (default: log)
  MARKETING_SMS_PROVIDER=log|twilio       (default: log)
  RESEND_API_KEY=                          TODO(LIVE) real ESP
  RESEND_FROM_EMAIL=
  JWT_SECRET=                              reused for unsubscribe HMAC tokens
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger("marketing_automation")


# ---------------------------------------------------------------------------
# Provider abstractions
# ---------------------------------------------------------------------------

class EmailProvider(ABC):
    @abstractmethod
    async def send(self, *, to: str, subject: str, body: str,
                   reply_to: Optional[str] = None) -> dict:
        """Send one email. Returns {provider, message_id, status, demo}."""


class SmsProvider(ABC):
    @abstractmethod
    async def send(self, *, to: str, body: str) -> dict:
        """Send one SMS. Returns {provider, message_id, status, demo}."""


class LogEmailProvider(EmailProvider):
    """Demo default: log the send, zero network."""

    async def send(self, *, to: str, subject: str, body: str,
                   reply_to: Optional[str] = None) -> dict:
        logger.info("[MARKETING email-log to=%s subject=%r] %s", to, subject, body[:200])
        return {"provider": "log", "message_id": f"log-email-{_short_id()}", "status": "sent", "demo": True}


class LogSmsProvider(SmsProvider):
    """Demo default: log the send, zero network."""

    async def send(self, *, to: str, body: str) -> dict:
        logger.info("[MARKETING sms-log to=%s] %s", to, body[:200])
        return {"provider": "log", "message_id": f"log-sms-{_short_id()}", "status": "sent", "demo": True}


def _short_id() -> str:
    return base64.urlsafe_b64encode(os.urandom(6)).decode("ascii").rstrip("=")


# ---------------------------------------------------------------------------
# TODO(LIVE) — real providers. Kept as skeletons: they raise until wired.
# ---------------------------------------------------------------------------

class ResendEmailProvider(EmailProvider):
    """TODO(LIVE): real ESP send via Resend (swap for SendGrid by changing
    the HTTP call). Requires RESEND_API_KEY + RESEND_FROM_EMAIL."""

    def __init__(self, api_key: str, from_email: str):
        self.api_key = api_key
        self.from_email = from_email

    async def send(self, *, to: str, subject: str, body: str,
                   reply_to: Optional[str] = None) -> dict:
        # TODO(LIVE): implement via httpx POST https://api.resend.com/emails
        raise NotImplementedError("ResendEmailProvider is not wired yet (TODO(LIVE))")


class TwilioSmsProvider(SmsProvider):
    """Live SMS via the existing services.twilio adapter (graceful demo
    fallback built in: logs + returns a demo id when creds are missing)."""

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id

    async def send(self, *, to: str, body: str) -> dict:
        from services.twilio import send_sms  # local import: keeps import light
        result = await send_sms(tenant_id=self.tenant_id, to=to, body=body)
        return {
            "provider": "twilio",
            "message_id": result.get("sid", ""),
            "status": result.get("status", "sent"),
            "demo": bool(result.get("demo")),
        }


def get_email_provider(tenant_id: str | None = None) -> EmailProvider:
    """Factory: log provider unless the operator opts into a real ESP."""
    choice = (os.environ.get("MARKETING_EMAIL_PROVIDER") or "log").lower()
    if choice == "resend":
        api_key = os.environ.get("RESEND_API_KEY") or ""
        from_email = os.environ.get("RESEND_FROM_EMAIL") or ""
        if not api_key or not from_email:
            logger.warning("MARKETING_EMAIL_PROVIDER=resend but RESEND_API_KEY/RESEND_FROM_EMAIL missing; using log provider")
            return LogEmailProvider()
        return ResendEmailProvider(api_key, from_email)  # TODO(LIVE)
    return LogEmailProvider()


def get_sms_provider(tenant_id: str | None = None) -> SmsProvider:
    """Factory: log provider unless the operator opts into Twilio."""
    choice = (os.environ.get("MARKETING_SMS_PROVIDER") or "log").lower()
    if choice == "twilio":
        return TwilioSmsProvider(tenant_id or "")
    return LogSmsProvider()


# ---------------------------------------------------------------------------
# Unsubscribe tokens (per-campaign, per-contact)
# ---------------------------------------------------------------------------

_TOKEN_SEP = "."


def _signing_key() -> bytes:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        # Never sign with a hardcoded fallback: anyone could forge
        # unsubscribe tokens with the publicly-known dev string.
        raise RuntimeError("JWT_SECRET is not set — cannot sign marketing tokens")
    return secret.encode("utf-8")


def make_unsubscribe_token(*, campaign_id: str, contact: str) -> str:
    """contact = the email or phone the message was sent to."""
    payload = json.dumps({"c": campaign_id, "k": contact}, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(_signing_key(), payload, hashlib.sha256).hexdigest()
    body = base64.urlsafe_b64encode(payload).decode("ascii")
    return f"{body}{_TOKEN_SEP}{sig}"


def verify_unsubscribe_token(token: str) -> Optional[dict]:
    """Returns {campaign_id, contact} or None when invalid/tampered."""
    try:
        body, sig = token.rsplit(_TOKEN_SEP, 1)
        payload = base64.urlsafe_b64decode(body.encode("ascii"))
        expected = hmac.new(_signing_key(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        data = json.loads(payload.decode("utf-8"))
        return {"campaign_id": data["c"], "contact": data["k"]}
    except Exception:
        return None


def unsubscribe_url(*, base_url: str, token: str) -> str:
    """Public unsubscribe link stamped into campaign footers."""
    base = (base_url or "").rstrip("/")
    return f"{base}/api/tenants/marketing/unsubscribe/{token}"
