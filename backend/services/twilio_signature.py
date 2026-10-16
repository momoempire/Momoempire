"""X-Twilio-Signature check for every /api/twilio/* webhook (EMP-FIX-034).

Twilio signs each webhook with the auth token of the account that owns the
number: HMAC-SHA1 over the exact URL it requested plus the sorted POST params.
We recompute that with twilio's RequestValidator and reject anything else.

Rules
- Token: the tenant's Twilio integration auth_token, else TWILIO_AUTH_TOKEN
  (same lookup outbound sends use, services/twilio._tenant_twilio_cfg).
- Missing / wrong signature -> 403.
- No token configured -> 403. The only exception is local development: APP_ENV
  explicitly set to dev/development/local AND no token anywhere; then the
  request is let through with a loud WARNING on every call. An unset APP_ENV is
  NOT dev here, so a production box that forgot APP_ENV still fails closed.
- URL Twilio signed: when PUBLIC_BACKEND_URL is set (it must be in staging and
  production; the callback URLs we hand Twilio are built from it) we use it as
  the origin and never look at Host / X-Forwarded-* headers, so a request
  Twilio signed for some other host cannot be replayed here by spoofing them.
  Without PUBLIC_BACKEND_URL, outside dev we reject; in dev we rebuild the URL
  from X-Forwarded-Proto (http/https only) and X-Forwarded-Host (hostname[:port]
  only), falling back to the request's own scheme and Host.
"""
import logging
import os
import re
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

logger = logging.getLogger("twilio.signature")

DEV_ENVS = {"dev", "development", "local"}
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+(:\d{1,5})?$")


def app_env() -> str:
    return (os.environ.get("APP_ENV") or "").strip().lower()


def is_dev() -> bool:
    return app_env() in DEV_ENVS


def _first(value: str | None) -> str:
    return (value or "").split(",")[0].strip()


def _configured_base() -> str | None:
    raw = (os.environ.get("PUBLIC_BACKEND_URL") or "").strip().rstrip("/")
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.query or parts.fragment:
        logger.error("PUBLIC_BACKEND_URL is not a plain http(s) origin; Twilio webhooks will be rejected")
        return None
    return raw


def signed_url(request: Request) -> str | None:
    """The full URL Twilio requested, or None when it cannot be known safely."""
    raw_path = request.scope.get("raw_path")
    # raw_path keeps Twilio's exact percent-encoding. Some ASGI servers (and the
    # httpx test client) put the query string in raw_path too, so cut it off and
    # always take the query from query_string.
    path = raw_path.decode("latin-1").split("?", 1)[0] if raw_path else request.url.path
    query = (request.scope.get("query_string") or b"").decode("latin-1")
    tail = path + (f"?{query}" if query else "")

    base = _configured_base()
    if base:
        return base + tail
    if not is_dev():
        return None
    proto = _first(request.headers.get("x-forwarded-proto")).lower()
    scheme = proto if proto in ("http", "https") else request.url.scheme
    fwd_host = _first(request.headers.get("x-forwarded-host"))
    host = fwd_host if fwd_host and _HOST_RE.match(fwd_host) else request.url.netloc
    return f"{scheme}://{host}{tail}"


def _reject(request: Request, reason: str) -> None:
    # Path + reason only: no phone numbers, bodies or signatures in logs.
    logger.warning("twilio webhook rejected path=%s reason=%s", request.url.path, reason)
    raise HTTPException(status_code=403, detail="Forbidden")


async def verify(request: Request, auth_token: str) -> None:
    """Raise 403 unless the request carries a valid X-Twilio-Signature."""
    form = await request.form()  # Starlette caches this; the endpoint reuses it
    if not auth_token:
        if is_dev():
            logger.warning(
                "TWILIO WEBHOOK SIGNATURE NOT CHECKED: APP_ENV=%s and no Twilio auth token "
                "configured (dev demo mode only) path=%s", app_env(), request.url.path)
            return
        _reject(request, "no auth token configured")
    signature = request.headers.get("x-twilio-signature") or ""
    if not signature:
        _reject(request, "missing signature")
    url = signed_url(request)
    if not url:
        _reject(request, "PUBLIC_BACKEND_URL not set")
    try:
        from twilio.request_validator import RequestValidator
    except Exception:  # fail closed if the SDK is missing
        _reject(request, "twilio sdk missing")
    if not RequestValidator(auth_token).validate(url, form, signature):
        _reject(request, "invalid signature")
