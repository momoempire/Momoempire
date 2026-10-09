"""EMP-W-CF-023: cross-site POST/PUT/PATCH/DELETE carrying our SameSite=None auth cookies are blocked.

No database, network or browser needed: a small app behind the real middleware, plus checks that
server.py installs it and that it blocks before a real handler (logout) runs.
"""
from __future__ import annotations

import logging
import os

import pytest

os.environ.setdefault("JWT_SECRET", "csrf-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "csrf_test")

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import csrf_guard  # noqa: E402
from csrf_guard import CSRFOriginMiddleware  # noqa: E402

FRONT = "https://app.example.test"
EVIL = "https://evil.example.net"
UNSAFE = ["POST", "PUT", "PATCH", "DELETE"]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", f"{FRONT}, https://other-front.example.test:8443/")
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    monkeypatch.delenv("PUBLIC_BACKEND_URL", raising=False)
    hits = []
    app = FastAPI()

    @app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def anything(rest: str, request: Request):
        hits.append((request.method, rest))
        return {"ok": True}

    app.add_middleware(CSRFOriginMiddleware)
    c = TestClient(app, base_url="https://api.example.test")
    c.hits = hits
    return c


def req(c, method, path, *, cookie="access_token", origin=None, referer=None, **kw):
    headers = kw.pop("headers", {})
    if origin is not None:
        headers["Origin"] = origin
    if referer is not None:
        headers["Referer"] = referer
    if cookie:
        c.cookies.set(cookie, "tok")
    try:
        return c.request(method, path, headers=headers, **kw)
    finally:
        c.cookies.clear()


@pytest.mark.parametrize("method", UNSAFE)
@pytest.mark.parametrize("cookie", list(csrf_guard.AUTH_COOKIES))
def test_cross_site_unsafe_with_auth_cookie_blocked(client, method, cookie):
    r = req(client, method, "/api/tenants/me", cookie=cookie, origin=EVIL)
    assert r.status_code == 403 and r.json() == {"detail": "Cross-site request blocked"}
    assert client.hits == []


def test_cross_site_form_upload_and_empty_post_blocked(client):
    assert req(client, "POST", "/api/crm/import", origin=EVIL, data={"a": "1"}).status_code == 403
    assert req(client, "POST", "/api/knowledge/upload", origin=EVIL,
               files={"file": ("x.txt", b"hello", "text/plain")}).status_code == 403
    assert req(client, "POST", "/api/auth/logout", origin=EVIL).status_code == 403
    assert req(client, "POST", "/api/x", origin="null").status_code == 403
    assert client.hits == []


@pytest.mark.parametrize("origin", [FRONT, FRONT + "/", "HTTPS://APP.example.test:443",
                                    "https://other-front.example.test:8443"])
def test_allowed_origins_pass(client, origin):
    assert req(client, "POST", "/api/x", origin=origin, json={}).status_code == 200


def test_port_and_scheme_must_match(client):
    assert req(client, "POST", "/api/x", origin="https://app.example.test:8443").status_code == 403
    assert req(client, "POST", "/api/x", origin="http://app.example.test").status_code == 403
    assert req(client, "POST", "/api/x", origin="https://app.example.test.evil.net").status_code == 403


def test_frontend_and_public_backend_url_are_allowed(client, monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "*")
    monkeypatch.setenv("FRONTEND_URL", "https://front.example.test")
    monkeypatch.setenv("PUBLIC_BACKEND_URL", "https://pub.example.test")
    assert req(client, "POST", "/api/x", origin="https://front.example.test").status_code == 200
    assert req(client, "POST", "/api/x", origin="https://pub.example.test").status_code == 200
    # "*" never means "any origin" here
    assert req(client, "POST", "/api/x", origin=EVIL).status_code == 403


def test_same_origin_passes(client):
    assert req(client, "POST", "/api/x", origin="https://api.example.test").status_code == 200


def test_referer_used_when_origin_missing(client):
    assert req(client, "POST", "/api/x", referer=EVIL + "/page?x=1").status_code == 403
    assert req(client, "POST", "/api/x", referer=FRONT + "/dashboard/settings").status_code == 200
    assert req(client, "POST", "/api/x", referer="not a url").status_code == 403


def test_no_origin_no_referer_is_non_browser_and_passes(client):
    assert req(client, "POST", "/api/x", json={}).status_code == 200


def test_requests_without_auth_cookie_untouched(client):
    assert req(client, "POST", "/api/public/widget/acme/lead", cookie=None, origin=EVIL).status_code == 200
    assert req(client, "POST", "/api/x", cookie="some_other_cookie", origin=EVIL).status_code == 200
    r = client.post("/api/x", headers={"Origin": EVIL, "Authorization": "Bearer abc"})
    assert r.status_code == 200


def test_safe_methods_untouched(client):
    assert req(client, "GET", "/api/auth/me", origin=EVIL).status_code == 200


@pytest.mark.parametrize("path", ["/api/stripe/webhook", "/api/stripe/connect-webhook",
                                  "/api/payments/stripe/webhook", "/api/public/c2p/stripe/connect-webhook",
                                  "/api/twilio/sms", "/api/cron/followups", "/api/public/waitlist"])
def test_exempt_routes(client, path):
    assert req(client, "POST", path, origin=EVIL).status_code == 200


def test_exempt_prefix_is_not_a_loose_startswith(client):
    assert req(client, "POST", "/api/twilio-admin/x", origin=EVIL).status_code == 403
    assert req(client, "POST", "/api/public/waitlist-export", origin=EVIL).status_code == 403


def test_block_is_logged_without_cookie_values(client, caplog):
    with caplog.at_level(logging.WARNING, logger="csrf"):
        req(client, "POST", "/api/x", origin=EVIL)
    assert "csrf blocked POST /api/x" in caplog.text and EVIL in caplog.text and "tok" not in caplog.text


def test_server_installs_guard_and_blocks_before_handler(monkeypatch):
    import server
    assert any(m.cls is CSRFOriginMiddleware for m in server.app.user_middleware)
    monkeypatch.setenv("CORS_ORIGINS", FRONT)
    c = TestClient(server.app, base_url="https://api.example.test")  # no startup: no DB touched
    c.cookies.set("access_token", "tok")
    r = c.post("/api/auth/logout", headers={"Origin": EVIL})
    assert r.status_code == 403
    # an allowed origin gets past the guard (then auth fails: the token is not a real JWT)
    assert c.post("/api/auth/logout", headers={"Origin": FRONT}).status_code == 401
