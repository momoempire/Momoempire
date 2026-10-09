"""Waitlist POST guard (EMP-WL-029): block cross-site "simple request" signups.

A browser can send a cross-site POST with Content-Type text/plain (or a form type) WITHOUT a CORS
preflight, so CORS alone doesn't stop another site from submitting signups. This pure-ASGI
middleware runs in front of POST /api/public/waitlist only:
- Content-Type must be application/json (any charset), otherwise 415. JSON forces a preflight,
  which the CORS allow-list then checks.
- If the request has an Origin header and CORS_ORIGINS is an explicit allow-list, the Origin must
  be on it, otherwise 403. With no allow-list (dev), the Origin isn't checked: set CORS_ORIGINS.
Requests without an Origin (curl, server-to-server) aren't browser cross-site requests and pass.

It sits outside the route handler on purpose: it works with today's handler on Cloudflare-2 and
with PR #11's hardened handler (which reads the raw body regardless of Content-Type), with no
merge conflict. KEEP IT when #11 merges (covered by tests/test_waitlist_only_mode.py).
"""
from __future__ import annotations

import json
import os

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
            if origin is not None and allowed is not None and origin.rstrip("/") not in allowed:
                return await _reply(send, 403, "Origin not allowed")
        await self.app(scope, receive, send)


async def _reply(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
