"""Waitlist-only deploy mode (EMP-WL-002).

WAITLIST_ONLY=true makes `server:app` a minimal app that serves ONLY:
    GET/HEAD /api/health      -> {"status": "ok" | "degraded"}  (no details)
    POST /api/public/waitlist -> the existing waitlist handler (routers/marketing.py)
Everything else (/docs, /redoc, /openapi.json, /api/auth/*, /api/admin/*, other /api/public/*,
/api/cron/*, webhooks, ...) is 404. The full app's startup hook (admin/industry/plan seeding,
index creation) is NOT run: it is attached to the full app object, which uvicorn never serves in
this mode; only the waitlist unique-index build (PR #18) runs at startup. Crons are HTTP-triggered (/api/cron/*), so they 404 here; there is no in-process scheduler.

POST /api/public/waitlist is behind WaitlistPostGuard (waitlist_guard.py): JSON only (415) and,
when CORS_ORIGINS is an allow-list, Origin must be on it (403). The full app gets the same guard.

Default (unset/false): server.py behaves as before, plus that guard on the waitlist POST.
"""
import logging
import os

from fastapi import APIRouter, FastAPI
from starlette.middleware.cors import CORSMiddleware

log = logging.getLogger("waitlist-mode")


def waitlist_only_enabled() -> bool:
    return (os.environ.get("WAITLIST_ONLY") or "").strip().lower() in ("1", "true", "yes", "on")


def _cors_kwargs_from(full_app: FastAPI) -> dict | None:
    """Reuse whatever CORS config server.py built (inline today, deploy_security after PR #10)."""
    for m in getattr(full_app, "user_middleware", []):
        if m.cls is CORSMiddleware:
            kw = getattr(m, "kwargs", None)
            if kw is None:
                kw = getattr(m, "options", None)
            return dict(kw or {})
    return None


def build_waitlist_app(full_app: FastAPI) -> FastAPI:
    from db import get_db, close_db
    from routers import marketing

    app = FastAPI(title="Waitlist", docs_url=None, redoc_url=None, openapi_url=None)
    api = APIRouter(prefix="/api")

    # EMP-WL-040 in waitlist mode (gap noted on PR #18): with PR #18's marketing.py, health reports
    # "degraded" while the unique email index is missing, and the index is built at startup (same
    # hook as the full app: retried, and the app refuses to start in production/staging if it
    # fails). Looked up with getattr so this file also works before #18 is merged.
    index_healthy = getattr(marketing, "waitlist_index_healthy", None)
    waitlist_startup = getattr(marketing, "_waitlist_startup", None)

    @api.api_route("/health", methods=["GET", "HEAD"])  # HEAD for uptime monitors (EMP-WL-032)
    async def health():
        try:
            await get_db().command("ping")
            if index_healthy is not None and not index_healthy():
                return {"status": "degraded"}
            return {"status": "ok"}
        except Exception:
            log.exception("health: database ping failed")
            return {"status": "degraded"}

    api.add_api_route("/public/waitlist", marketing.waitlist, methods=["POST"])
    app.include_router(api)

    cors = _cors_kwargs_from(full_app)
    if cors is not None:
        app.add_middleware(CORSMiddleware, **cors)
    else:
        log.error("WAITLIST_ONLY: no CORS middleware found on the full app; cross-origin calls will fail")

    # Inside CORS, like the full app (EMP-WL-036): guard errors carry CORS headers for allowed origins.
    from waitlist_guard import install_waitlist_guard
    install_waitlist_guard(app)

    if waitlist_startup is not None:
        app.add_event_handler("startup", waitlist_startup)  # no seeding: index build only

    @app.on_event("shutdown")
    async def _shutdown():
        await close_db()

    log.warning("WAITLIST_ONLY is on: serving only /api/health and POST /api/public/waitlist")
    return app
