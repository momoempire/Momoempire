"""Waitlist POST guard (EMP-WL-029): block cross-site "simple request" signups.

A browser can send a cross-site POST with Content-Type text/plain (or a form type) WITHOUT a CORS
preflight, so CORS alone doesn't stop another site from submitting signups. This pure-ASGI
middleware runs in front of POST /api/public/waitlist only:
- Content-Type must be application/json (any charset), otherwise 415. JSON forces a preflight,
  which the CORS allow-list then checks.
- If the request has an Origin header and CORS_ORIGINS is an explicit allow-list, the Origin must
  be on it, otherwise 403. With no allow-list (dev), the Origin isn't checked: set CORS_ORIGINS.
Requests without an Origin (curl, server-to-server) aren't browser cross-site requests and pass.

Placement (EMP-WL-036): install it with install_waitlist_guard(app) so it runs INSIDE
CORSMiddleware. Then its 415/403 replies go back out through CORS: an allowed Origin gets the
normal Access-Control-Allow-Origin header (the browser can read the error message), and a
disallowed Origin gets none (CORS decides, same as for every other response).

It sits outside the route handler on purpose: it works with today's handler on Cloudflare-2 and
with PR #11's hardened handler (which reads the raw body regardless of Content-Type), with no
merge conflict. KEEP IT when #11 merges (covered by tests/test_waitlist_only_mode.py).
"""
from __future__ import annotations

import json
import os

from starlette.middleware import Middleware

WAITLIST_PATH = "/api/public/waitlist"


def allowed_origins() -> list[str] | None:
    """Explicit CORS_ORIGINS allow-list, or None when unset/'*' (no Origin check)."""
    raw = (os.environ.get("CORS_ORIGINS") or "").strip()
    if not raw or raw == "*":
        return None
    out = [o.strip().rstrip("/") for o in raw.split(",") if o.strip() and o.strip() != "*"]
    return out or None


class WaitlistPostGuard:
    def __init__(self, app, path: str = WAITLIST_PATH):
        self.app = app
        self.path = path.rstrip("/")

    async def __call__(self, scope, receive, send):
        if (scope.get("type") == "http" and scope.get("method") == "POST"
                and (scope.get("path") or "").rstrip("/") == self.path):
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
            ctype = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if ctype != "application/json":
                return await _reply(send, 415, "Content-Type must be application/json")
            origin = headers.get("origin")
            allowed = allowed_origins()
            # Scheme and host are case-insensitive; PR #10's CORS config lower-cases them too, so a
            # CORS_ORIGINS entry like "https://App.example.com" matches the browser's lower-case Origin.
            if (origin is not None and allowed is not None
                    and origin.rstrip("/").lower() not in {a.lower() for a in allowed}):
                return await _reply(send, 403, "Origin not allowed")
        await self.app(scope, receive, send)


class DropOrphanCredentialsHeader:
    """EMP-WL-052: drop `Access-Control-Allow-Credentials` from responses that carry no
    `Access-Control-Allow-Origin`.

    Starlette's CORSMiddleware adds `Access-Control-Allow-Credentials: true` to every response when
    credentials are allowed, including 403/415s and preflights for DISALLOWED origins. Without
    Allow-Origin the browser blocks the read anyway, so the header means nothing there; this just
    stops advertising it. Allowed origins (which get Allow-Origin) are untouched. Must sit OUTSIDE
    CORSMiddleware to see its headers; install_waitlist_guard() puts it outermost.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        async def _send(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                names = {k.lower() for k, _ in headers}
                if b"access-control-allow-credentials" in names and b"access-control-allow-origin" not in names:
                    message = dict(message)
                    message["headers"] = [(k, v) for k, v in headers if k.lower() != b"access-control-allow-credentials"]
            await send(message)

        await self.app(scope, receive, _send)


async def _reply(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def install_waitlist_guard(app) -> None:
    """Register WaitlistPostGuard as the INNERMOST user middleware (EMP-WL-036).

    Starlette wraps user_middleware in list order (index 0 = outermost), and add_middleware()
    inserts at index 0, so a guard added after CORSMiddleware would sit OUTSIDE it and its 415/403
    replies would carry no CORS headers (the browser shows a network error instead of the reason).
    Appending puts it inside CORS whatever order the middlewares are registered in.

    Also puts DropOrphanCredentialsHeader OUTERMOST (index 0), outside CORS (EMP-WL-052). Call this
    after CORSMiddleware is registered (server.py and waitlist_mode.py both do).
    """
    if getattr(app, "middleware_stack", None) is not None:
        raise RuntimeError("install_waitlist_guard must run before the app starts")
    if not any(m.cls is DropOrphanCredentialsHeader for m in app.user_middleware):
        app.user_middleware.insert(0, Middleware(DropOrphanCredentialsHeader))
    if any(m.cls is WaitlistPostGuard for m in app.user_middleware):
        return
    app.user_middleware.append(Middleware(WaitlistPostGuard))
