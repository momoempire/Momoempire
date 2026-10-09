"""EMP-DEV-007 — public WebRTC voice-token endpoint (OpenAI mocked, no network)."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "emp-dev-007-test")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "emp_dev_007_test")
os.environ.pop("PUBLIC_VOICE_DEMO_ENABLED", None)
os.environ.pop("OPENAI_API_KEY", None)

from routers import marketing as m  # noqa: E402


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(m.router, prefix="/api")
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def test_voice_token_disabled_by_default(client, monkeypatch):
    monkeypatch.delenv("PUBLIC_VOICE_DEMO_ENABLED", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk_test_should_not_leak")
    r = client.post("/api/public/demo/voice-token", json={"industry": "hvac", "lang": "en"})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["fallback"] == "web-speech"
    assert "sk_" not in str(body)


def test_voice_token_requires_api_key(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_VOICE_DEMO_ENABLED", "true")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = client.post("/api/public/demo/voice-token", json={"industry": "dental", "lang": "en"})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert "OPENAI_API_KEY" in body["reason"]


def test_voice_token_returns_ephemeral_only(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_VOICE_DEMO_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk_test_REAL_KEY_DO_NOT_RETURN")
    monkeypatch.setenv("PUBLIC_VOICE_DEMO_MAX_SECONDS", "45")
    monkeypatch.setenv("PUBLIC_VOICE_DEMO_MODEL", "gpt-realtime")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "value": "ek_test_ephemeral_secret",
        "expires_at": 1_700_000_000,
    }

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=mock_resp)

    with patch.object(m.httpx, "AsyncClient", return_value=mock_client):
        r = client.post("/api/public/demo/voice-token", json={"industry": "hvac", "lang": "es"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert body["value"] == "ek_test_ephemeral_secret"
    assert body["value"].startswith("ek_")
    assert "sk_test_REAL_KEY_DO_NOT_RETURN" not in str(body)
    assert body["max_duration_seconds"] == 45
    assert body["model"] == "gpt-realtime"
    assert body["webrtc_url"].endswith("/v1/realtime/calls")
    assert body["ai_name"]  # from preset
    # Ensure OpenAI call used API key server-side only
    call_kwargs = mock_client.post.await_args.kwargs
    assert "sk_test_REAL_KEY_DO_NOT_RETURN" in call_kwargs["headers"]["Authorization"]
    assert call_kwargs["json"]["session"]["instructions"]
    assert "español" in call_kwargs["json"]["session"]["instructions"].lower() or "Alex" in call_kwargs["json"]["session"]["instructions"]


def test_voice_token_refuses_sk_in_provider_response(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_VOICE_DEMO_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk_test_server")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"value": "sk_should_never_return"}
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.post = AsyncMock(return_value=mock_resp)
    with patch.object(m.httpx, "AsyncClient", return_value=mock_client):
        r = client.post("/api/public/demo/voice-token", json={"industry": "salon"})
    body = r.json()
    assert body["available"] is False
    assert "sk_should_never_return" not in str(body)


def test_voice_token_rate_limited(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_VOICE_DEMO_ENABLED", "false")
    # Exhaust the shared IP limiter
    with patch.object(m, "_RATE_LIMIT", 3), patch.object(m, "_IP_RATE", {}):
        codes = []
        for _ in range(5):
            resp = client.post("/api/public/demo/voice-token", json={"industry": "hvac"})
            codes.append(resp.status_code)
        assert 429 in codes


def test_voice_instructions_grounded():
    preset = m.INDUSTRY_PRESETS["dental"]
    en = m._voice_instructions(preset, "en")
    es = m._voice_instructions(preset, "es")
    assert "Bright Smiles" in en and "Mia" in en
    assert "Delta Dental" in en or "insurance" in en.lower() or "Cleaning" in en
    assert "Bright Smiles" in es and "Mia" in es
