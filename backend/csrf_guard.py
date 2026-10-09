"""EMP-W-CF-023: block cross-site state-changing requests that ride on our auth cookies.

Why: access_token / refresh_token / session_token / portal_token are SameSite=None, so
a browser attaches them to a POST from ANY site: a plain <form> (urlencoded or multipart
upload) or a no-body fetch() needs no CORS preflight, and the handler runs even though the
attacker cannot read the reply.

SameSite=Lax was evaluated and NOT chosen: the frontend calls the API with XHR
(axios withCredentials), including the Google sign-in step (AuthCallback.jsx POSTs
/auth/google/session). Lax cookies are not sent on cross-site XHR, so any deployment where
the frontend and API are different sites (e.g. *.pages.dev + an API domain, the Emergent
preview) would be logged out. Lax only becomes an option once both live under one
registrable domain; that is a hosting decision for Brann.

Rule (POST/PUT/PATCH/DELETE carrying one of the auth cookies above):
- Origin present -> must be an allowed origin; "null" is never allowed.
- else Referer present -> its origin must be allowed.
- else (neither header) -> allowed: modern browsers always send Origin on cross-origin
  POSTs, so this is a non-browser client (scripts, test suites) that CSRF cannot drive.
Allowed origins = CORS_ORIGINS + FRONTEND_URL + PUBLIC_BACKEND_URL + the request's own host.
Requests without an auth cookie (public endpoints, the embeddable widget, Bearer-only API
clients) and safe methods are untouched.

Exempt (they never rely on cookies and have their own checks): Stripe platform + Connect
webhooks (signed), Twilio webhooks (signed, #37), cron (shared secret), the public waitlist
(own Origin/JSON guard, EMP-WL-029).
"""
import logging
import os
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("csrf")

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
AUTH_COOKIES = ("access_token", "refresh_token", "session_token", "portal_token")
EXEMPT_PATHS = (
    "/api/stripe/webhook",
    "/api/stripe/connect-webhook",
    "/api/payments/stripe/webhook",
    "/api/public/c2p/stripe/connect-webhook",
    "/api/twilio",
    "/api/cron",
    "/api/public/waitlist",
)
_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_origin(value: str | None) -> str | None:
    """scheme://host[:port] lower-cased, default port dropped; None if not an http(s) origin."""
    if not value:
        return None
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if scheme not in _DEFAULT_PORTS or not host:
        return None
    if port and port != _DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


def allowed_origins() -> set[str]:
    raw = [o for o in (os.environ.get("CORS_ORIGINS") or "").split(",") if o.strip() not in ("", "*")]
    raw += [os.environ.get("FRONTEND_URL") or "", os.environ.get("PUBLIC_BACKEND_URL") or ""]
    return {o for o in (normalize_origin(r) for r in raw) if o}


def _host_key(netloc: str, scheme_hint: str = "https") -> str:
    o = normalize_origin(f"{scheme_hint}://{netloc}")
    return o.split("://", 1)[1] if o else ""


def is_exempt(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in EXEMPT_PATHS)


def origin_allowed(origin: str, request: Request) -> bool:
    norm = normalize_origin(origin)
    if not norm:
        return False
    if norm in allowed_origins():
        return True
    # same-origin: the browser sets Host to the server it is talking to
    scheme = norm.split("://", 1)[0]
    host = request.headers.get("host") or ""
    return bool(host) and _host_key(host, scheme) == norm.split("://", 1)[1]


def check(request: Request) -> str | None:
    """None if the request may proceed, else the rejection reason."""
    if request.method.upper() in SAFE_METHODS or is_exempt(request.url.path):
        return None
    if not any(request.cookies.get(c) for c in AUTH_COOKIES):
        return None
    origin = request.headers.get("origin")
    if origin is not None:
        return None if origin_allowed(origin, request) else "origin not allowed"
    referer = request.headers.get("referer")
    if referer:
        return None if origin_allowed(referer, request) else "referer not allowed"
    return None


class CSRFOriginMiddleware:
    """Pure ASGI middleware (no body buffering, uploads stream through untouched)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request = Request(scope)
            reason = check(request)
            if reason:
                logger.warning("csrf blocked %s %s reason=%s origin=%s", request.method, request.url.path,
                               reason, normalize_origin(request.headers.get("origin")
                                                         or request.headers.get("referer")) or "-")
                await JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)
