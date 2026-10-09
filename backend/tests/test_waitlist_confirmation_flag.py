"""EMP-WL-008: the waitlist confirmation email is off unless WAITLIST_CONFIRMATION_EMAIL is on.

The real `services.email.send_email` is replaced by a recorder, so nothing is ever sent. Fake DB
only; an optional real-MongoDB check runs when WAITLIST_TEST_MONGO_URL points at a throwaway DB.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "wl008-test")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "wl008")

from routers import marketing as mk  # noqa: E402
import services.email as email_svc  # noqa: E402
from tests.test_fix_waitlist_hardening import FakeDB  # noqa: E402

WL = "/api/public/waitlist"
FLAG = "WAITLIST_CONFIRMATION_EMAIL"
MONGO_URL = os.environ.get("WAITLIST_TEST_MONGO_URL", "")


def _app():
    app = FastAPI()
    app.include_router(mk.router, prefix="/api")
    return app


@pytest.fixture
def env(monkeypatch):
    """Real _send_waitlist_confirmation; only the low-level sender is a recorder."""
    db = FakeDB()
    calls = []

    async def fake_send_email(*, to, subject, html, **kw):
        calls.append({"to": to, "subject": subject})
        return {"ok": True}

    monkeypatch.setattr(email_svc, "send_email", fake_send_email)
    monkeypatch.setattr(mk, "get_db", lambda: db)
    monkeypatch.setattr(mk, "_WAITLIST_INDEX_READY", False)
    monkeypatch.delenv("TURNSTILE_ENABLED", raising=False)
    monkeypatch.delenv(FLAG, raising=False)
    for lim in (mk._DEMO_LIMIT, mk._WAITLIST_IP_MINUTE, mk._WAITLIST_IP_HOUR):
        lim.reset()
    return TestClient(_app()), db, calls, monkeypatch


def test_default_is_off_and_sends_nothing(env):
    c, db, calls, _ = env
    assert mk.confirmation_email_enabled() is False
    r = c.post(WL, json={"email": "new@example.com", "name": "Pat"})
    assert r.status_code == 200 and r.json() == {"ok": True, "status": "received"}
    assert len(db.waitlist.docs) == 1  # signup still saved
    assert calls == []  # no email at all
    assert "confirmation_sent_at" not in db.waitlist.docs[0]  # no claim either


@pytest.mark.parametrize("value", ["", "false", "0", "no", "off", "nope", "  "])
def test_anything_but_explicit_on_is_off(env, value):
    c, db, calls, mp = env
    mp.setenv(FLAG, value)
    assert mk.confirmation_email_enabled() is False
    c.post(WL, json={"email": f"x{uuid.uuid4().hex[:6]}@example.com"})
    assert calls == [] and "confirmation_sent_at" not in db.waitlist.docs[0]


def test_off_response_identical_to_on_response(env):
    c, _, _, mp = env
    off = c.post(WL, json={"email": "a@example.com"})
    mp.setenv(FLAG, "true")
    on = c.post(WL, json={"email": "b@example.com"})
    assert off.status_code == on.status_code == 200 and off.content == on.content


def test_off_estimator_signup_sends_nothing(env):
    c, db, calls, _ = env
    r = c.post(WL, json={"email": "est@example.com", "source_detail": "estimator", "estimated_tier": "growth"})
    assert r.status_code == 200 and calls == []
    assert db.waitlist.docs[0]["estimated_tier"] == "growth"


def test_off_sender_itself_is_a_no_op(env):
    _, _, calls, _ = env
    asyncio.run(mk._send_waitlist_confirmation("direct@example.com"))
    assert calls == []


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on", " true "])
def test_explicitly_on_sends_once_per_new_address(env, value):
    c, db, calls, mp = env
    mp.setenv(FLAG, value)
    assert mk.confirmation_email_enabled() is True
    c.post(WL, json={"email": "On@Example.com"})
    c.post(WL, json={"email": "on@example.com"})  # repeat: no second email
    assert [x["to"] for x in calls] == ["On@example.com"]
    assert calls[0]["subject"] == "You're on the AI Office waitlist"
    assert "confirmation_sent_at" in db.waitlist.docs[0]


def test_turning_on_later_does_not_email_earlier_signups(env):
    c, _, calls, mp = env
    c.post(WL, json={"email": "early@example.com"})
    mp.setenv(FLAG, "true")
    c.post(WL, json={"email": "early@example.com"})  # repeat of a row created while off
    assert calls == []


def test_env_example_documents_flag_default_off():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text()
    assert f"\n{FLAG}=\n" in text


@pytest.mark.skipif(not MONGO_URL, reason="set WAITLIST_TEST_MONGO_URL to a throwaway MongoDB")
def test_real_mongo_off_sends_nothing_on_sends_once(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    import httpx

    calls = []

    async def fake_send_email(*, to, subject, html, **kw):
        calls.append(to)

    async def run():
        client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=3000)
        name = f"wl008_{uuid.uuid4().hex[:8]}"
        db = client[name]
        try:
            monkeypatch.setattr(email_svc, "send_email", fake_send_email)
            monkeypatch.setattr(mk, "get_db", lambda: db)
            monkeypatch.setattr(mk, "_WAITLIST_INDEX_READY", False)
            monkeypatch.delenv("TURNSTILE_ENABLED", raising=False)
            monkeypatch.delenv(FLAG, raising=False)
            for lim in (mk._WAITLIST_IP_MINUTE, mk._WAITLIST_IP_HOUR):
                lim.reset()
                monkeypatch.setattr(lim, "limit", 1000)  # 10 posts from one test IP
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://t") as c:
                await asyncio.gather(*[c.post(WL, json={"email": "off@example.com"}) for _ in range(5)])
                monkeypatch.setenv(FLAG, "true")
                await asyncio.gather(*[c.post(WL, json={"email": "on@example.com"}) for _ in range(5)])
            off_claims = await db.waitlist.count_documents({"email": "off@example.com", "confirmation_sent_at": {"$exists": True}})
            return off_claims, await db.waitlist.count_documents({})
        finally:
            await client.drop_database(name)
            client.close()

    off_claims, rows = asyncio.run(run())
    assert off_claims == 0 and rows == 2
    assert calls == ["on@example.com"]
