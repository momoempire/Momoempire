"""EMP-FIX-034: every /api/twilio/* webhook needs a valid X-Twilio-Signature.

Runs against a THROWAWAY MongoDB named by TWILIO_TEST_MONGO_URL (each test makes and
drops its own database); skipped when it is unset. No Twilio, LLM or SMS calls: the
signatures are computed locally with twilio's RequestValidator and the AI reply is stubbed.
"""
from __future__ import annotations

import logging
import os
import uuid
from urllib.parse import urlencode

import pytest

os.environ.setdefault("JWT_SECRET", "twilio-sig-test")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "twilio_sig")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from twilio.request_validator import RequestValidator  # noqa: E402

import db as dbmod  # noqa: E402
from routers import twilio_webhook as tw  # noqa: E402

MONGO_URL = os.environ.get("TWILIO_TEST_MONGO_URL", "")
pytestmark = pytest.mark.skipif(not MONGO_URL, reason="set TWILIO_TEST_MONGO_URL to a throwaway MongoDB")

ENV_TOKEN = "env-token-0123456789abcdef"
TENANT_TOKEN = "tenant-token-fedcba9876543210"
PUBLIC = "https://api.example.test"
TENANT_NUMBER = "+15550001111"
CALLER = "+15557654321"
ROUTES = ["/voice", "/voice-turn", "/sms", "/outbound-callback", "/missed-call"]


def sign(token: str, url: str, params: dict) -> str:
    return RequestValidator(token).compute_signature(url, params)


@pytest.fixture
def env(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    from pymongo import MongoClient

    name = f"twsig_{uuid.uuid4().hex[:10]}"
    sync = MongoClient(MONGO_URL)[name]
    sync.tenants.insert_one({"id": "t1", "name": "Acme", "human_fallback_number": TENANT_NUMBER})
    sync.integrations.insert_one({"tenant_id": "t1", "key": "twilio",
                                  "config": {"auth_token": TENANT_TOKEN, "from_number": TENANT_NUMBER}})
    sync.tenants.insert_one({"id": "t2", "name": "NoOwnToken", "human_fallback_number": "+15550002222"})
    sync.conversations.insert_one({"id": "conv1", "tenant_id": "t1", "channel": "call"})

    motor = AsyncIOMotorClient(MONGO_URL)
    monkeypatch.setattr(dbmod, "_client", motor)
    monkeypatch.setattr(dbmod, "_db", motor[name])

    async def fake_reply(*a, **k):
        return {"reply": "ok"}

    sms = []

    async def fake_send_sms(**k):
        sms.append(k)
        return {"sid": "SM_test", "status": "test", "demo": True}

    monkeypatch.setattr(tw, "receptionist_reply", fake_reply)
    monkeypatch.setattr(tw, "send_sms", fake_send_sms)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", ENV_TOKEN)
    monkeypatch.setenv("PUBLIC_BACKEND_URL", PUBLIC)
    monkeypatch.setenv("APP_ENV", "production")

    app = FastAPI()
    app.include_router(tw.router, prefix="/api")
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, sync, sms
    motor.close()
    MongoClient(MONGO_URL).drop_database(name)


def post(client, path, params, *, token=None, url=None, query="", headers=None, signature=None):
    full_path = f"/api/twilio{path}" + (f"?{query}" if query else "")
    h = dict(headers or {})
    if signature is not None:
        h["X-Twilio-Signature"] = signature
    elif token:
        h["X-Twilio-Signature"] = sign(token, url or PUBLIC + full_path, params)
    return client.post(full_path, data=params, headers=h)


# ---------- every route is guarded ----------

def test_every_twilio_route_carries_the_signature_guard():
    paths = {r.path for r in tw.router.routes}
    assert paths == {f"/twilio{p}" for p in ROUTES}, "new /api/twilio route? it is guarded router-wide; update ROUTES"
    for r in tw.router.routes:
        calls = [d.call for d in r.dependant.dependencies]
        assert tw.require_twilio_signature in calls, r.path


@pytest.mark.parametrize("path,query", [("/voice", ""), ("/voice-turn", "conv_id=conv1"), ("/sms", ""),
                                        ("/outbound-callback", "tenant_id=t1"), ("/missed-call", "")])
def test_unsigned_and_forged_requests_get_403_and_write_nothing(env, path, query):
    client, sync, sms = env
    params = {"To": TENANT_NUMBER, "From": CALLER, "CallSid": "CA1", "Body": "hi",
              "CallStatus": "no-answer", "SpeechResult": "hello"}
    before = (sync.conversations.count_documents({}), sync.conv_messages.count_documents({}))
    assert post(client, path, params, query=query).status_code == 403
    assert post(client, path, params, query=query, signature="AAAAAAAAAAAAAAAAAAAAAAAAAAA=").status_code == 403
    assert post(client, path, params, query=query, token="not-the-token").status_code == 403
    assert (sync.conversations.count_documents({}), sync.conv_messages.count_documents({})) == before
    assert sms == []


def test_valid_platform_signature_on_unresolved_number_passes(env):
    client, _, _ = env
    params = {"To": "+15559990000", "From": CALLER, "CallSid": "CA1"}
    r = post(client, "/voice", params, token=ENV_TOKEN)
    assert r.status_code == 200 and "not configured" in r.text


def test_tampered_param_fails(env):
    client, _, _ = env
    params = {"To": "+15559990000", "From": CALLER, "CallSid": "CA1"}
    sig = sign(ENV_TOKEN, PUBLIC + "/api/twilio/voice", params)
    assert post(client, "/voice", {**params, "From": "+15550000000"}, signature=sig).status_code == 403


# ---------- per-tenant token, env fallback ----------

def test_tenant_number_uses_the_tenants_own_token(env):
    client, sync, sms = env
    params = {"To": TENANT_NUMBER, "From": CALLER, "CallStatus": "no-answer"}
    assert post(client, "/missed-call", params, token=ENV_TOKEN).status_code == 403
    r = post(client, "/missed-call", params, token=TENANT_TOKEN)
    assert r.status_code == 200 and r.json()["status"] == "no-answer"
    assert len(sms) == 1


def test_tenant_without_own_token_falls_back_to_env_token(env):
    client, _, _ = env
    params = {"To": "+15550002222", "From": CALLER, "CallStatus": "completed"}
    assert post(client, "/missed-call", params, token=TENANT_TOKEN).status_code == 403
    assert post(client, "/missed-call", params, token=ENV_TOKEN).json() == {"ignored": True}


def test_voice_turn_uses_conversation_tenant_and_signed_query(env):
    client, sync, _ = env
    params = {"SpeechResult": "my sink is leaking"}
    # signature over the URL without its query string must not pass
    assert post(client, "/voice-turn", params, query="conv_id=conv1",
                url=PUBLIC + "/api/twilio/voice-turn", token=TENANT_TOKEN).status_code == 403
    assert post(client, "/voice-turn", params, query="conv_id=conv1", token=ENV_TOKEN).status_code == 403
    r = post(client, "/voice-turn", params, query="conv_id=conv1", token=TENANT_TOKEN)
    assert r.status_code == 200 and "ok" in r.text
    assert sync.conv_messages.count_documents({"conversation_id": "conv1"}) == 2


def test_outbound_callback_uses_tenant_from_query(env):
    client, sync, _ = env
    q = urlencode({"tenant_id": "t1", "name": "Ana", "note": "about the quote"})
    params = {"CallSid": "CA9", "To": CALLER}
    assert post(client, "/outbound-callback", params, query=q, token=ENV_TOKEN).status_code == 403
    r = post(client, "/outbound-callback", params, query=q, token=TENANT_TOKEN)
    assert r.status_code == 200 and "Ana" in r.text
    assert sync.conversations.count_documents({"twilio_call_sid": "CA9"}) == 1


# ---------- no token configured ----------

@pytest.mark.parametrize("app_env", ["production", "staging", None, ""])
def test_no_token_rejects_outside_dev(env, monkeypatch, app_env):
    client, _, _ = env
    monkeypatch.delenv("TWILIO_AUTH_TOKEN")
    if app_env is None:
        monkeypatch.delenv("APP_ENV")
    else:
        monkeypatch.setenv("APP_ENV", app_env)
    params = {"To": "+15559990000", "From": CALLER}
    assert post(client, "/voice", params).status_code == 403
    assert post(client, "/voice", params, token="anything").status_code == 403


def test_dev_demo_mode_only_without_any_token_and_logged(env, monkeypatch, caplog):
    client, _, _ = env
    monkeypatch.setenv("APP_ENV", "development")
    params = {"To": "+15559990000", "From": CALLER}
    # a token is configured -> still checked in dev
    assert post(client, "/voice", params).status_code == 403
    monkeypatch.delenv("TWILIO_AUTH_TOKEN")
    with caplog.at_level(logging.WARNING, logger="twilio.signature"):
        r = post(client, "/voice", params)
    assert r.status_code == 200
    assert "SIGNATURE NOT CHECKED" in caplog.text and "development" in caplog.text
    # tenant with its own token is never bypassed, even in dev
    assert post(client, "/sms", {"To": TENANT_NUMBER, "From": CALLER, "Body": "x"}).status_code == 403


def test_rejection_log_has_no_phone_numbers_or_signature(env, caplog):
    client, _, _ = env
    with caplog.at_level(logging.WARNING, logger="twilio.signature"):
        post(client, "/sms", {"To": TENANT_NUMBER, "From": CALLER, "Body": "secret"}, signature="SIGVALUE")
    assert "invalid signature" in caplog.text
    for leak in (CALLER, TENANT_NUMBER, "secret", "SIGVALUE"):
        assert leak not in caplog.text


# ---------- URL reconstruction behind proxies ----------

def test_public_url_is_pinned_and_forwarded_headers_ignored(env):
    client, _, _ = env
    params = {"To": "+15559990000", "From": CALLER}
    # Twilio signed a request for another host; spoofing X-Forwarded-Host must not make it valid here
    other = "https://other-app.example.test/api/twilio/voice"
    spoof = {"X-Forwarded-Host": "other-app.example.test", "X-Forwarded-Proto": "https"}
    assert post(client, "/voice", params, url=other, token=ENV_TOKEN, headers=spoof).status_code == 403
    # signed for the configured public URL -> valid although the app sees Host: testserver over http
    assert post(client, "/voice", params, token=ENV_TOKEN, headers=spoof).status_code == 200
    # Twilio sometimes signs with the explicit default port
    assert post(client, "/voice", params, url="https://api.example.test:443/api/twilio/voice",
                token=ENV_TOKEN).status_code == 200


def test_without_public_url_outside_dev_rejects(env, monkeypatch):
    client, _, _ = env
    monkeypatch.delenv("PUBLIC_BACKEND_URL")
    params = {"To": "+15559990000", "From": CALLER}
    assert post(client, "/voice", params, url="http://testserver/api/twilio/voice", token=ENV_TOKEN).status_code == 403


def test_without_public_url_in_dev_uses_sane_forwarded_headers(env, monkeypatch):
    client, _, _ = env
    monkeypatch.delenv("PUBLIC_BACKEND_URL")
    monkeypatch.setenv("APP_ENV", "dev")
    params = {"To": "+15559990000", "From": CALLER}
    fwd = {"X-Forwarded-Proto": "https, http", "X-Forwarded-Host": "tunnel.example.test"}
    assert post(client, "/voice", params, url="https://tunnel.example.test/api/twilio/voice",
                token=ENV_TOKEN, headers=fwd).status_code == 200
    # a malformed forwarded host / proto is ignored, the request's own scheme and Host are used
    bad = {"X-Forwarded-Proto": "javascript", "X-Forwarded-Host": "evil.test/x?y"}
    assert post(client, "/voice", params, url="http://testserver/api/twilio/voice",
                token=ENV_TOKEN, headers=bad).status_code == 200
    assert post(client, "/voice", params, url="https://evil.test/x?y/api/twilio/voice",
                token=ENV_TOKEN, headers=bad).status_code == 403


def test_bad_public_url_fails_closed(env, monkeypatch):
    client, _, _ = env
    monkeypatch.setenv("PUBLIC_BACKEND_URL", "api.example.test")  # no scheme
    params = {"To": "+15559990000", "From": CALLER}
    assert post(client, "/voice", params, url="http://testserver/api/twilio/voice", token=ENV_TOKEN).status_code == 403
