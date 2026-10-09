"""Fix #3 — enforce CORS allowlist (deploy_security) and stop health endpoints leaking.

No network: DB is faked, the real server is imported without running startup.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

os.environ.setdefault("JWT_SECRET", "fix003-test")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "fix003")

import deploy_security as ds  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
GOOD = "https://app.example.com"
EVIL = "https://evil.example"


# ---------------- helper: validate_origin ----------------
@pytest.mark.parametrize("origin,expected", [
    ("https://app.example.com", "https://app.example.com"),
    ("https://app.example.com/", "https://app.example.com"),
    ("http://localhost:3000", "http://localhost:3000"),
    ("https://APP.Example.com:8443", "https://app.example.com:8443"),
    ("http://127.0.0.1:8001", "http://127.0.0.1:8001"),
    ("http://[::1]:3000", "http://[::1]:3000"),
    ("https://my-site.pages.dev", "https://my-site.pages.dev"),
])
def test_valid_origins_pass(origin, expected):
    assert ds.validate_origin(origin) == expected


@pytest.mark.parametrize("origin", [
    "https://app.example.com:abc",      # non-numeric port
    "https://app.example.com:",         # empty port
    "https://app.example.com:0",        # out of range
    "https://app.example.com:65536",    # out of range
    "https://app.example.com:-1",
    "https://app.example.com:８０",      # non-ASCII digits
])
def test_malformed_port_rejected(origin):
    with pytest.raises(ValueError):
        ds.validate_origin(origin)


@pytest.mark.parametrize("origin", [
    "https://user@app.example.com",
    "https://user:pass@app.example.com",
    "https://evil.example@app.example.com",
    "https://:@app.example.com",
])
def test_userinfo_rejected(origin):
    with pytest.raises(ValueError):
        ds.validate_origin(origin)


@pytest.mark.parametrize("origin", [
    "https://app.example.com/path",
    "https://app.example.com/?q=1",
    "https://app.example.com?q=1",
    "https://app.example.com#frag",
    "https://*.example.com",
    "*",
    "https://app.example.com*",
    "ftp://app.example.com",
    "app.example.com",
    "https://",
    "https://app example.com",
    "javascript://app.example.com",
    "https://app..example.com",
])
def test_path_query_fragment_wildcard_and_junk_rejected(origin):
    with pytest.raises(ValueError):
        ds.validate_origin(origin)


def test_https_required_in_strict():
    with pytest.raises(ValueError):
        ds.validate_origin("http://app.example.com", require_https=True)


# ---------------- helper: cors_options ----------------
def test_cors_options_allowlist_with_credentials(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CORS_ORIGINS", f"{GOOD}, {GOOD}/,https://www.example.com")
    opts = ds.cors_options()
    assert opts["allow_origins"] == [GOOD, "https://www.example.com"]
    assert opts["allow_credentials"] is True
    assert "allow_origin_regex" not in opts


@pytest.mark.parametrize("value", ["", "*"])
def test_production_without_allowlist_fails_closed_and_logs(monkeypatch, caplog, value):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CORS_ORIGINS", value)
    with caplog.at_level(logging.ERROR, logger="deploy-security"):
        opts = ds.cors_options()
    assert opts["allow_origins"] == [] and opts["allow_credentials"] is False
    assert "allow_origin_regex" not in opts
    assert any("failing closed" in r.message for r in caplog.records)


def test_invalid_entries_dropped_and_logged(monkeypatch, caplog):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CORS_ORIGINS", f"https://u@x.com,https://a.com:abc,http://plain.com,{GOOD}")
    with caplog.at_level(logging.ERROR, logger="deploy-security"):
        opts = ds.cors_options()
    assert opts["allow_origins"] == [GOOD]
    assert len([r for r in caplog.records if "Ignoring" in r.message]) == 3


def test_all_invalid_fails_closed(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("CORS_ORIGINS", "http://insecure.example.com")
    opts = ds.cors_options()
    assert opts["allow_origins"] == [] and opts["allow_credentials"] is False


def test_dev_unset_is_wildcard_without_credentials(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    opts = ds.cors_options()
    assert opts["allow_origins"] == ["*"] and opts["allow_credentials"] is False


# ---------------- middleware behavior ----------------
def _app(monkeypatch, env, origins):
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setenv("CORS_ORIGINS", origins)
    app = FastAPI()

    @app.get("/api/thing")
    def thing():
        return {"ok": True}

    app.add_middleware(CORSMiddleware, **ds.cors_options())
    return TestClient(app)


def test_allowed_origin_gets_acao_with_credentials(monkeypatch):
    c = _app(monkeypatch, "production", GOOD)
    r = c.get("/api/thing", headers={"Origin": GOOD})
    assert r.headers.get("access-control-allow-origin") == GOOD
    assert r.headers.get("access-control-allow-credentials") == "true"


def test_disallowed_origin_gets_no_acao(monkeypatch):
    c = _app(monkeypatch, "production", GOOD)
    r = c.get("/api/thing", headers={"Origin": EVIL})
    # Starlette always adds Allow-Credentials on simple responses; without ACAO the browser
    # still blocks the read, so ACAO absence is the security property.
    assert "access-control-allow-origin" not in r.headers


def test_preflight_allowed_and_disallowed(monkeypatch):
    c = _app(monkeypatch, "production", GOOD)
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}
    ok = c.options("/api/thing", headers={"Origin": GOOD, **pre})
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == GOOD
    assert ok.headers["access-control-allow-credentials"] == "true"
    bad = c.options("/api/thing", headers={"Origin": EVIL, **pre})
    assert bad.status_code == 400
    assert "access-control-allow-origin" not in bad.headers


def test_production_unconfigured_allows_no_cross_origin(monkeypatch):
    c = _app(monkeypatch, "production", "")
    for origin in (GOOD, EVIL):
        assert "access-control-allow-origin" not in c.get("/api/thing", headers={"Origin": origin}).headers


# ---------------- real server wiring (clean subprocess so env is read at import) ----------------
_PROBE = r"""
import json
from fastapi.testclient import TestClient
import server
c = TestClient(server.app)
mw = [m for m in server.app.user_middleware if m.cls.__name__ == "CORSMiddleware"]
kw = mw[0].kwargs if hasattr(mw[0], "kwargs") else mw[0].options
pre = {"Access-Control-Request-Method": "POST"}
print(json.dumps({
  "n_cors": len(mw),
  "regex": kw.get("allow_origin_regex"),
  "good": c.options("/api/health", headers={"Origin": "https://app.example.com", **pre}).headers.get("access-control-allow-origin"),
  "evil": c.options("/api/health", headers={"Origin": "https://evil.example", **pre}).headers.get("access-control-allow-origin"),
  "evil_status": c.options("/api/health", headers={"Origin": "https://evil.example", **pre}).status_code,
}))
"""


def _run_server_probe(extra_env):
    env = {k: v for k, v in os.environ.items() if k not in ("APP_ENV", "CORS_ORIGINS")}
    env.update({"JWT_SECRET": "x", "MONGO_URL": "mongodb://localhost:1", "DB_NAME": "x", **extra_env})
    out = subprocess.run([sys.executable, "-c", _PROBE], cwd=BACKEND, env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_server_uses_allowlist_no_regex():
    r = _run_server_probe({"APP_ENV": "production", "CORS_ORIGINS": "https://app.example.com"})
    assert r["n_cors"] == 1 and r["regex"] is None
    assert r["good"] == "https://app.example.com"
    assert r["evil"] is None and r["evil_status"] == 400


def test_server_production_unconfigured_imports_and_fails_closed():
    r = _run_server_probe({"APP_ENV": "production"})
    assert r["regex"] is None and r["good"] is None and r["evil"] is None


def test_server_source_has_no_permissive_regex():
    src = (BACKEND / "server.py").read_text()
    assert "allow_origin_regex" not in src and "cors_options()" in src


# ---------------- health endpoints don't leak ----------------
SECRET_ERR = "auth failed for mongodb://admin:hunter2@db.internal:27017"


class _BoomDB:
    def __getattr__(self, name):
        return self

    def __getitem__(self, name):
        return self

    async def command(self, *a, **k):
        raise RuntimeError(SECRET_ERR)

    async def insert_one(self, *a, **k):
        raise RuntimeError(SECRET_ERR)

    async def find_one(self, *a, **k):
        raise RuntimeError(SECRET_ERR)

    async def count_documents(self, *a, **k):
        raise RuntimeError(SECRET_ERR)


@pytest.fixture
def server_client(monkeypatch):
    import server
    from routers import health_deploy
    monkeypatch.setattr(server, "get_db", lambda: _BoomDB())
    monkeypatch.setattr(health_deploy, "get_db", lambda: _BoomDB())
    server.app.dependency_overrides.clear()
    yield TestClient(server.app), server
    server.app.dependency_overrides.clear()


def test_health_minimal_and_generic_on_db_error(server_client):
    c, _ = server_client
    r = c.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert set(body) <= {"status", "detail"}
    assert "hunter2" not in r.text and "mongodb" not in r.text and "RuntimeError" not in r.text


def test_deployment_probe_requires_auth(server_client):
    c, _ = server_client
    r = c.get("/api/health/deployment")
    assert r.status_code in (401, 403)
    for leak in ("hunter2", "mongodb", "env", "counts", "JWT_SECRET", "STRIPE"):
        assert leak not in r.text


def test_deployment_probe_admin_never_returns_exception_text(server_client):
    c, server = server_client
    from security import require_platform_admin
    server.app.dependency_overrides[require_platform_admin] = lambda: {"id": "a", "role": "platform_admin"}
    r = c.get("/api/health/deployment")
    assert r.status_code == 200
    body = r.json()
    assert body["db_ok"] is False and body["ready"] is False
    assert "hunter2" not in r.text and "mongodb://" not in r.text and "RuntimeError" not in r.text
