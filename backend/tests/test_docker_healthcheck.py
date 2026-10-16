"""EMP-FIX-039: the Docker HEALTHCHECK must pass when /api/health is ok and fail when it is degraded.

5142da7 made the check require {"db": "ok"}, which #10 removed from the public response, so every
container was reported unhealthy. These tests run the exact command from backend/Dockerfile
against a local stub serving what the real endpoint returns (DB faked; no network, no Docker).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "fix039-test")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "fix039")

BACKEND = Path(__file__).resolve().parents[1]
SECRET_ERR = "auth failed for mongodb://admin:hunter2@db.internal:27017"


def healthcheck_code() -> str:
    """The python -c program from the Dockerfile HEALTHCHECK line."""
    text = (BACKEND / "Dockerfile").read_text()
    m = re.search(r"HEALTHCHECK[^\n]*\\\n\s*CMD python -c '([^']+)'", text)
    assert m, "HEALTHCHECK with `python -c '...'` not found in backend/Dockerfile"
    return m.group(1)


class _UpDB:
    async def command(self, *a, **k):
        return {"ok": 1}


class _DownDB:
    async def command(self, *a, **k):
        raise RuntimeError(SECRET_ERR)


def _health(monkeypatch, db):
    import server
    monkeypatch.setattr(server, "get_db", lambda: db)
    return TestClient(server.app).get("/api/health")


class _Stub:
    """Serves one canned (status, body) for GET /api/health on 127.0.0.1:<free port>."""

    def __init__(self, status: int, body: str):
        stub = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                stub.paths.append(self.path)
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.paths: list[str] = []
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def run_check(port: int) -> int:
    env = {"PATH": os.environ.get("PATH", ""), "PORT": str(port)}
    return subprocess.run([sys.executable, "-c", healthcheck_code()], env=env,
                          capture_output=True, timeout=15).returncode


def check_against(status: int, body: str) -> tuple[int, list[str]]:
    stub = _Stub(status, body)
    try:
        return run_check(stub.port), stub.paths
    finally:
        stub.close()


def test_dockerfile_check_does_not_need_db_field_and_proxy_line_unchanged():
    text = (BACKEND / "Dockerfile").read_text()
    code = healthcheck_code()
    assert '"db"' not in code and "/api/health" in code and "timeout=4" in code
    # 5142da7's trusted-proxy start line is untouched.
    assert ('--forwarded-allow-ips=\\"${FORWARDED_ALLOW_IPS:-127.0.0.1}\\"' in text)
    assert "--forwarded-allow-ips='*'" not in text


def test_db_up_endpoint_ok_and_container_healthy(monkeypatch):
    r = _health(monkeypatch, _UpDB())
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    code, paths = check_against(r.status_code, r.text)
    assert code == 0 and paths == ["/api/health"]


def test_db_down_endpoint_degraded_without_details_and_container_unhealthy(monkeypatch):
    r = _health(monkeypatch, _DownDB())
    body = r.json()
    assert body["status"] == "degraded" and set(body) <= {"status", "detail"}
    for leak in ("hunter2", "mongodb", "RuntimeError", "db.internal", '"db"'):
        assert leak not in r.text
    code, _ = check_against(r.status_code, r.text)
    assert code == 1


@pytest.mark.parametrize("status,body", [
    (503, json.dumps({"status": "degraded"})),
    (500, "Internal Server Error"),
    (200, "not json"),
    (200, json.dumps({"detail": "x"})),
    (200, json.dumps({"status": "OK "})),
])
def test_anything_but_status_ok_is_unhealthy(status, body):
    assert check_against(status, body)[0] == 1


def test_unreachable_backend_is_unhealthy():
    stub = _Stub(200, "{}")
    port = stub.port
    stub.close()
    assert run_check(port) == 1
