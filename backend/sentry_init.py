"""Sentry error monitoring — no-op unless SENTRY_DSN is set.

PII hygiene: before_send scrubs request bodies/cookies/headers that commonly
carry credentials, and drops events that look like they contain passwords or
tokens. The DSN itself is the only secret here and lives in the environment.
"""
from __future__ import annotations

import logging
import os
import re

log = logging.getLogger("aio.sentry")

_SCRUB_KEYS = re.compile(
    r"(password|passwd|secret|token|api[-_]?key|authorization|cookie|session)",
    re.IGNORECASE,
)
_REDACTED = "[redacted]"


def _scrub(obj):
    if isinstance(obj, dict):
        return {
            k: (_REDACTED if _SCRUB_KEYS.search(str(k)) else _scrub(v))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [_scrub(v) for v in obj]
    return obj


def _before_send(event, hint):
    try:
        req = event.get("request", {})
        if isinstance(req, dict):
            for section in ("data", "cookies", "headers"):
                if section in req:
                    req[section] = _scrub(req[section])
        # Drop the event entirely if a raw secret-looking value survived.
        blob = str(event)
        if re.search(r"(?i)(bearer\s+[A-Za-z0-9\-_]{20,}|sk_(live|test)_[A-Za-z0-9]+)", blob):
            return None
    except Exception:
        pass
    return event


def init_sentry(app=None) -> bool:
    """Initialize Sentry iff SENTRY_DSN is configured. Returns True when active."""
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        sentry_sdk.init(
            dsn=dsn,
            integrations=[StarletteIntegration(), FastApiIntegration()],
            before_send=_before_send,
            traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
            profiles_sample_rate=float(os.environ.get("SENTRY_PROFILES_SAMPLE_RATE", "0.0")),
            send_default_pii=False,
            environment=os.environ.get("APP_ENV", "production"),
        )
        log.info("Sentry initialized")
        return True
    except Exception as e:
        log.warning("Sentry init failed (%s); continuing without error monitoring", e)
        return False


def is_configured() -> bool:
    return bool(os.environ.get("SENTRY_DSN", "").strip())
