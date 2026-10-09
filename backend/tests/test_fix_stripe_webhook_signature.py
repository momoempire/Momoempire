"""Fix #1 — Stripe webhooks must be signed; no placeholder API key fallback.

Uses real stripe.Webhook.construct_event with a local test secret (no network).
DB is mocked; no real Stripe API calls.
"""
from __future__ import annotations

import json
import logging
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "fix-stripe-test")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "fix_stripe_test")

from services import stripe_config  # noqa: E402
from routers import payments, call_to_payment  # noqa: E402

TEST_SECRET = "whsec_test_fix001_local_only"


def _sign(payload: bytes, secret: str = TEST_SECRET) -> str:
    ts = int(time.time())
    signed = f"{ts}.{payload.decode()}".encode()
    sig = stripe.WebhookSignature._compute_signature(signed.decode(), secret)
    return f"t={ts},v1={sig}"


def _invoice_db(paid_store: dict):
    """Fake DB: tracks whether invoice marked paid."""
    db = MagicMock()

    async def find_invoice(query, proj=None):
        if query.get("id") == "inv_1":
            return {"id": "inv_1", "tenant_id": "t1", "total_cents": 1000, "amount_paid_cents": 0, "status": "sent"}
        return None

    async def update_invoice(query, update, **kw):
        s = (update or {}).get("$set") or {}
        if s.get("status") == "paid":
            paid_store["paid"] = True
        return MagicMock()

    db.invoices.find_one = AsyncMock(side_effect=find_invoice)
    db.invoices.update_one = AsyncMock(side_effect=update_invoice)
    db.webhook_events.insert_one = AsyncMock()
    db.webhook_events.update_one = AsyncMock()
    # EMP-FIX-037 (PR #7 follow-ups): the Connect event must come from the invoice tenant's own
    # account and match the PaymentIntent we recorded; platform events go through a replay gate.
    db.tenants.find_one = AsyncMock(return_value={"stripe_connect_id": "acct_1"})
    db.invoice_payments.find_one = AsyncMock(return_value={
        "invoice_id": "inv_1", "tenant_id": "t1", "amount_cents": 1000, "connected_account": "acct_1"})
    db.invoice_payments.update_one = AsyncMock()
    db.platform_webhook_events.insert_one = AsyncMock()
    db.platform_webhook_events.delete_one = AsyncMock()
    db.payment_transactions.update_one = AsyncMock()
    db.tenants.update_one = AsyncMock()
    # any other collection calls in success path
    for name in ("payments", "jobs", "appointments", "leads", "activities", "notifications"):
        coll = getattr(db, name)
        coll.insert_one = AsyncMock()
        coll.update_one = AsyncMock()
        coll.find_one = AsyncMock(return_value=None)
    return db


def _pi_event(ev_id: str = "evt_1") -> bytes:
    return json.dumps({
        "id": ev_id,
        "object": "event",
        "type": "payment_intent.succeeded",
        "account": "acct_1",
        "data": {"object": {
            "id": "pi_1", "object": "payment_intent",
            "amount_received": 1000, "amount": 1000, "currency": "usd",
            "metadata": {"invoice_id": "inv_1", "tenant_id": "t1"},
        }},
    }).encode()


@pytest.fixture
def connect_client():
    app = FastAPI()
    app.add_api_route("/api/stripe/connect-webhook", call_to_payment.connect_webhook, methods=["POST"])
    return TestClient(app)


@pytest.fixture
def platform_client():
    app = FastAPI()
    app.add_api_route("/api/stripe/webhook", payments.stripe_webhook, methods=["POST"])
    return TestClient(app)


# ---------- Connect webhook (invoice payments) ----------

def test_connect_unsigned_rejected(connect_client, monkeypatch):
    monkeypatch.setenv("STRIPE_CONNECT_WEBHOOK_SECRET", TEST_SECRET)
    paid = {"paid": False}
    with patch.object(call_to_payment, "get_db", return_value=_invoice_db(paid)):
        r = connect_client.post("/api/stripe/connect-webhook", content=_pi_event())
    assert r.status_code == 400
    assert paid["paid"] is False


def test_connect_bad_signature_rejected(connect_client, monkeypatch):
    monkeypatch.setenv("STRIPE_CONNECT_WEBHOOK_SECRET", TEST_SECRET)
    paid = {"paid": False}
    payload = _pi_event()
    with patch.object(call_to_payment, "get_db", return_value=_invoice_db(paid)):
        r = connect_client.post(
            "/api/stripe/connect-webhook",
            content=payload,
            headers={"stripe-signature": _sign(payload, "whsec_wrong")},
        )
    assert r.status_code == 400
    assert paid["paid"] is False


def test_connect_empty_secret_rejected_invoice_stays_unpaid(connect_client, monkeypatch):
    monkeypatch.delenv("STRIPE_CONNECT_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    paid = {"paid": False}
    payload = _pi_event()
    db = _invoice_db(paid)
    with patch.object(call_to_payment, "get_db", return_value=db):
        r = connect_client.post(
            "/api/stripe/connect-webhook",
            content=payload,
            headers={"stripe-signature": _sign(payload)},
        )
    assert r.status_code == 503
    assert paid["paid"] is False
    db.webhook_events.insert_one.assert_not_called()


def test_connect_valid_signature_processes(connect_client, monkeypatch):
    monkeypatch.setenv("STRIPE_CONNECT_WEBHOOK_SECRET", TEST_SECRET)
    paid = {"paid": False}
    payload = _pi_event("evt_valid")
    with patch.object(call_to_payment, "get_db", return_value=_invoice_db(paid)):
        r = connect_client.post(
            "/api/stripe/connect-webhook",
            content=payload,
            headers={"stripe-signature": _sign(payload)},
        )
    assert r.status_code == 200, r.text
    assert paid["paid"] is True


# ---------- Platform webhook ----------

def _checkout_event() -> bytes:
    return json.dumps({
        "id": "evt_cs", "object": "event", "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_1", "payment_status": "paid", "metadata": {"tenant_id": "t1", "plan_id": "starter"}}},
    }).encode()


def test_platform_empty_secret_rejected(platform_client, monkeypatch):
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    db = _invoice_db({"paid": False})
    payload = _checkout_event()
    with patch.object(payments, "get_db", return_value=db):
        r = platform_client.post("/api/stripe/webhook", content=payload, headers={"stripe-signature": _sign(payload)})
    assert r.status_code == 503
    db.payment_transactions.update_one.assert_not_called()


def test_platform_unsigned_and_bad_sig_rejected(platform_client, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", TEST_SECRET)
    db = _invoice_db({"paid": False})
    payload = _checkout_event()
    with patch.object(payments, "get_db", return_value=db):
        r1 = platform_client.post("/api/stripe/webhook", content=payload)
        r2 = platform_client.post("/api/stripe/webhook", content=payload, headers={"stripe-signature": _sign(payload, "whsec_bad")})
    assert r1.status_code == 400
    assert r2.status_code == 400
    db.payment_transactions.update_one.assert_not_called()


def test_platform_valid_signature_processes(platform_client, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", TEST_SECRET)
    db = _invoice_db({"paid": False})
    payload = _checkout_event()
    with patch.object(payments, "get_db", return_value=db):
        r = platform_client.post("/api/stripe/webhook", content=payload, headers={"stripe-signature": _sign(payload)})
    assert r.status_code == 200, r.text
    db.payment_transactions.update_one.assert_awaited()


# ---------- No placeholder key ----------

def test_no_placeholder_key_in_production(monkeypatch, caplog):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    with caplog.at_level(logging.ERROR, logger="stripe_config"):
        ok = stripe_config.configure_stripe_api_key()
    assert ok is False
    assert stripe.api_key == ""
    assert "sk_test_emergent" not in (stripe.api_key or "")
    assert any("production" in r.message for r in caplog.records)


def test_placeholder_value_refused(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_emergent")
    assert stripe_config.configure_stripe_api_key() is False
    assert stripe.api_key == ""


def test_require_key_fails_loudly(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    with pytest.raises(HTTPException) as ei:
        stripe_config.require_stripe_api_key()
    assert ei.value.status_code == 503


def test_no_placeholder_literal_fallback_in_source():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for rel in ("routers/payments.py", "routers/overage.py", "routers/call_to_payment.py"):
        src = (root / rel).read_text()
        assert 'or "sk_test_emergent"' not in src, rel
