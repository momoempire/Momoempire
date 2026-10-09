"""EMP-FIX-038 / EMP-W-CF-022 (PR #9 follow-ups): drafts are not public, expires_at is enforced,
and the page can tell when Accept would fail (can_accept).

Runs the real public quote routes against a THROWAWAY MongoDB named by QUOTE_TEST_MONGO_URL
(each test makes and drops its own database); skipped when unset. Email is stubbed.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("JWT_SECRET", "quote-fu-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "quote_fu")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import db as dbmod  # noqa: E402
from routers import call_to_payment as c2p  # noqa: E402

MONGO_URL = os.environ.get("QUOTE_TEST_MONGO_URL", "")
pytestmark = pytest.mark.skipif(not MONGO_URL, reason="set QUOTE_TEST_MONGO_URL to a throwaway MongoDB")
NOW = datetime.now(timezone.utc)
Q = "/api/public/c2p/quotes"


def _quote(token, **kw):
    doc = {"id": f"q_{token}", "tenant_id": "t1", "customer_id": "c1", "customer_name": "Ana", "customer_email": None,
           "customer_phone": "", "title": "Repair", "notes": "internal note",
           "lines": [{"description": "Fix", "quantity": 1, "unit_price": 50.0, "service_id": None}],
           "total": 50.0, "status": "sent", "owner_approved": True, "public_token": token,
           "expires_at": (NOW + timedelta(days=7)).isoformat(), "created_at": NOW.isoformat()}
    doc.update(kw)
    return doc


@pytest.fixture
def env(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    from pymongo import MongoClient

    name = f"quotefu_{uuid.uuid4().hex[:10]}"
    sync = MongoClient(MONGO_URL)[name]
    sync.tenants.insert_one({"id": "t1", "name": "Acme", "slug": "acme"})
    sync.estimates.insert_many([
        _quote("ok"),
        _quote("draft", status="draft", owner_approved=False),
        _quote("c2pdraft", status="draft", owner_approved=False),
        _quote("crm", owner_approved=None, expires_at=None),          # dashboard estimate marked sent
        _quote("old", expires_at=(NOW - timedelta(minutes=1)).isoformat()),
        _quote("naive", expires_at=(NOW - timedelta(days=1)).replace(tzinfo=None).isoformat()),
        _quote("junk", expires_at="not a date"),
    ])

    async def no_email(**k):
        return None

    monkeypatch.setattr(c2p, "send_email", no_email)
    motor = AsyncIOMotorClient(MONGO_URL)
    monkeypatch.setattr(dbmod, "_client", motor)
    monkeypatch.setattr(dbmod, "_db", motor[name])
    app = FastAPI()
    app.include_router(c2p.public_router, prefix="/api")
    with TestClient(app) as client:
        yield client, sync
    motor.close()
    MongoClient(MONGO_URL).drop_database(name)


@pytest.mark.parametrize("token", ["draft", "c2pdraft"])
def test_drafts_are_not_public(env, token):
    client, sync = env
    unknown = client.get(f"{Q}/no-such-token")
    r = client.get(f"{Q}/{token}")
    assert r.status_code == 404 and r.json() == unknown.json()  # indistinguishable from a bad token
    assert client.post(f"{Q}/{token}/accept", json={}).status_code == 404
    assert sync.invoices.count_documents({}) == 0


def test_approved_quote_can_be_accepted(env):
    client, sync = env
    r = client.get(f"{Q}/ok")
    assert r.status_code == 200
    body = r.json()
    assert body["expired"] is False and body["can_accept"] is True and "notes" not in body
    a = client.post(f"{Q}/ok/accept", json={})
    assert a.status_code == 200 and a.json()["ok"] is True
    after = client.get(f"{Q}/ok").json()
    assert after["can_accept"] is False  # already accepted


def test_dashboard_estimate_is_viewable_but_cannot_be_accepted(env):
    client, _ = env
    body = client.get(f"{Q}/crm").json()
    assert body["can_accept"] is False and body["expired"] is False
    r = client.post(f"{Q}/crm/accept", json={})
    assert r.status_code == 400 and "not approved" in r.json()["detail"]


@pytest.mark.parametrize("token", ["old", "naive"])
def test_expired_quote_is_marked_and_cannot_be_accepted(env, token):
    client, sync = env
    body = client.get(f"{Q}/{token}").json()
    assert body["expired"] is True and body["can_accept"] is False
    r = client.post(f"{Q}/{token}/accept", json={})
    assert r.status_code == 400 and r.json()["detail"] == "Quote has expired"
    assert sync.invoices.count_documents({}) == 0 and sync.appointments.count_documents({}) == 0


def test_accepted_quote_still_returns_its_invoice_after_expiry(env):
    client, sync = env
    first = client.post(f"{Q}/ok/accept", json={}).json()
    sync.estimates.update_one({"public_token": "ok"}, {"$set": {"expires_at": (NOW - timedelta(days=1)).isoformat()}})
    again = client.post(f"{Q}/ok/accept", json={})
    assert again.status_code == 200 and again.json()["idempotent"] is True
    assert again.json()["invoice"]["id"] == first["invoice"]["id"]


def test_unparseable_expiry_does_not_block(env):
    client, _ = env
    assert client.get(f"{Q}/junk").json()["can_accept"] is True
    assert client.post(f"{Q}/junk/accept", json={}).status_code == 200


def test_quote_expired_helper_handles_datetimes():
    past = datetime(2020, 1, 1)
    assert c2p._quote_expired({"expires_at": past}) is True
    assert c2p._quote_expired({"expires_at": datetime(2999, 1, 1, tzinfo=timezone.utc)}) is False
    assert c2p._quote_expired({}) is False
