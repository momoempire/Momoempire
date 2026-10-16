"""QA (Oct 13): the public demo never echoes internal errors (exception text, provider bodies)."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "qa-demo-test")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "qa_demo_test")

from routers import marketing as m  # noqa: E402


def _client():
    app = FastAPI()
    app.include_router(m.router, prefix="/api")
    return TestClient(app)


def test_turn_error_is_generic_and_localized():
    c = _client()
    for lang, expect in (("en", "try that again"), ("es", "intentarlo de nuevo")):
        sid = c.post("/api/public/demo/start", json={"industry": "hvac", "lang": lang}).json()["session_id"]
        with patch.object(m, "receptionist_reply", AsyncMock(side_effect=RuntimeError("SECRET_DB_URL=mongodb://x"))):
            r = c.post("/api/public/demo/turn", json={"session_id": sid, "text": "hi"})
        body = r.json()
        assert r.status_code == 200
        assert expect in body["reply"]
        assert "SECRET" not in body["reply"] and "mongodb" not in body["reply"]
        # the failed utterance isn't kept, so the next turn starts clean
        assert m._SESSIONS[sid]["history"][-1]["role"] == "ai"


def test_voice_provider_error_body_not_returned(monkeypatch):
    monkeypatch.setenv("PUBLIC_VOICE_DEMO_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk_test_not_real")
    resp = MagicMock(status_code=401, text='{"error":"Incorrect API key provided: sk_test_not_real org-123"}')
    hc = AsyncMock()
    hc.__aenter__.return_value = hc
    hc.__aexit__.return_value = None
    hc.post = AsyncMock(return_value=resp)
    with patch.object(m.httpx, "AsyncClient", return_value=hc):
        body = _client().post("/api/public/demo/voice-token", json={"industry": "hvac"}).json()
    assert body["available"] is False
    assert "sk_test" not in str(body) and "org-123" not in str(body)
    with patch.object(m.httpx, "AsyncClient", side_effect=RuntimeError("dns fail internal-host")):
        body = _client().post("/api/public/demo/voice-token", json={"industry": "hvac"}).json()
    assert body["available"] is False and "internal-host" not in str(body)
