"""EMP-FIX-037 / EMP-W-CF-019 (PR #7 follow-ups).

Connect: a payment only counts when the event's account is the invoice tenant's stripe_connect_id
and currency/amount match the PaymentIntent we created. Platform: replayed events are ignored and a
Checkout session with payment_status "unpaid" never activates a plan.

Real signatures (stripe.Webhook.construct_event with a local secret, no network) against a THROWAWAY
MongoDB named by STRIPE_TEST_MONGO_URL (each test makes and drops its own database); skipped when unset.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from types import SimpleNamespace

import pytest
import stripe

os.environ.setdefault("JWT_SECRET", "stripe-fu-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "stripe_fu")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import db as dbmod  # noqa: E402
from routers import call_to_payment as c2p, payments  # noqa: E402

MONGO_URL = os.environ.get("STRIPE_TEST_MONGO_URL", "")
pytestmark = pytest.mark.skipif(not MONGO_URL, reason="set STRIPE_TEST_MONGO_URL to a throwaway MongoDB")
SECRET = "whsec_fix037_local_only"


def _signed(event: dict):
    payload = json.dumps(event).encode()
    ts = int(time.time())
    sig = stripe.WebhookSignature._compute_signature(f"{ts}.{payload.decode()}", SECRET)
    return payload, {"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"}


@pytest.fixture
def env(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    from pymongo import MongoClient

    name = f"stripefu_{uuid.uuid4().hex[:10]}"
    sync = MongoClient(MONGO_URL)[name]
    sync.tenants.insert_many([
        {"id": "victim", "name": "Victim Co", "stripe_connect_id": "acct_victim"},
        {"id": "attacker", "name": "Attacker Co", "stripe_connect_id": "acct_attacker"},
    ])
    sync.invoices.insert_one({"id": "inv_v", "tenant_id": "victim", "status": "sent", "total_cents": 5000,
                              "amount_paid_cents": 0, "title": "Repair", "customer_name": "Ana",
                              "customer_email": None, "public_token": "tok"})
    # what create_invoice_payment records for a PaymentIntent it created
    sync.invoice_payments.insert_one({"id": "p1", "tenant_id": "victim", "invoice_id": "inv_v",
                                      "stripe_payment_intent_id": "pi_v", "connected_account": "acct_victim",
                                      "amount_cents": 5000, "status": "requires_payment_method"})
    sync.payment_transactions.insert_one({"session_id": "cs_1", "tenant_id": "victim", "plan_id": "pro",
                                          "status": "initiated", "payment_status": "unpaid"})
    motor = AsyncIOMotorClient(MONGO_URL)
    monkeypatch.setattr(dbmod, "_client", motor)
    monkeypatch.setattr(dbmod, "_db", motor[name])
    monkeypatch.setattr(c2p, "get_db", lambda: motor[name])
    monkeypatch.setattr(payments, "get_db", lambda: motor[name])
    monkeypatch.setenv("STRIPE_CONNECT_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    app = FastAPI()
    app.add_api_route("/api/stripe/connect-webhook", c2p.connect_webhook, methods=["POST"])
    app.add_api_route("/api/stripe/webhook", payments.stripe_webhook, methods=["POST"])
    with TestClient(app) as client:
        yield client, sync
    motor.close()
    MongoClient(MONGO_URL).drop_database(name)


def _pi_event(ev_id="evt_1", account="acct_victim", pi_id="pi_v", amount=5000, currency="usd",
              meta=None):
    return {"id": ev_id, "object": "event", "type": "payment_intent.succeeded", "account": account,
            "data": {"object": {"id": pi_id, "object": "payment_intent", "amount": amount,
                                "amount_received": amount, "currency": currency,
                                "metadata": meta or {"invoice_id": "inv_v", "tenant_id": "victim"}}}}


def _post(client, path, event):
    payload, headers = _signed(event)
    return client.post(path, content=payload, headers=headers)


def _inv(sync):
    return sync.invoices.find_one({"id": "inv_v"})


# ---------- Connect (EMP-W-CF-019) ----------

def test_genuine_payment_from_tenants_own_account_is_applied(env):
    client, sync = env
    r = _post(client, "/api/stripe/connect-webhook", _pi_event())
    assert r.status_code == 200 and r.json() == {"ok": True}
    inv = _inv(sync)
    assert inv["status"] == "paid" and inv["amount_paid_cents"] == 5000


@pytest.mark.parametrize("label,event", [
    ("another tenant's account pays the victim's invoice",
     _pi_event(account="acct_attacker", pi_id="pi_attacker")),
    ("event without an account (platform event replayed to the Connect endpoint)",
     _pi_event(account=None)),
    ("right account, PaymentIntent we never created", _pi_event(pi_id="pi_unknown")),
    ("currency mismatch (5000 JPY is about $33, not $50)", _pi_event(currency="jpy")),
    ("amount mismatch", _pi_event(amount=4999)),
    ("metadata names another tenant", _pi_event(meta={"invoice_id": "inv_v", "tenant_id": "attacker"})),
])
def test_mismatched_payments_never_mark_invoice_paid(env, label, event):
    client, sync = env
    r = _post(client, "/api/stripe/connect-webhook", event)
    assert r.status_code == 200 and r.json().get("rejected") is True, label
    inv = _inv(sync)
    assert inv["status"] == "sent" and inv["amount_paid_cents"] == 0, label
    row = sync.webhook_events.find_one({"_id": event["id"]})
    assert row["rejected"], label


def test_recorded_intent_on_other_account_is_rejected(env):
    client, sync = env
    sync.invoice_payments.update_one({"stripe_payment_intent_id": "pi_v"}, {"$set": {"connected_account": "acct_old"}})
    assert _post(client, "/api/stripe/connect-webhook", _pi_event()).json().get("rejected") is True
    assert _inv(sync)["status"] == "sent"


def test_tenant_without_connect_account_is_rejected(env):
    client, sync = env
    sync.tenants.update_one({"id": "victim"}, {"$set": {"stripe_connect_id": None}})
    assert _post(client, "/api/stripe/connect-webhook", _pi_event()).json().get("rejected") is True
    assert _inv(sync)["status"] == "sent"


def test_payment_failure_from_other_account_is_not_recorded(env):
    client, sync = env
    ev = _pi_event(ev_id="evt_f", account="acct_attacker", pi_id="pi_x")
    ev["type"] = "payment_intent.payment_failed"
    assert _post(client, "/api/stripe/connect-webhook", ev).json().get("rejected") is True
    assert sync.invoice_payments.count_documents({"stripe_payment_intent_id": "pi_x"}) == 0
    ev = _pi_event(ev_id="evt_f2")
    ev["type"] = "payment_intent.payment_failed"
    assert _post(client, "/api/stripe/connect-webhook", ev).json() == {"ok": True}
    assert sync.invoice_payments.find_one({"stripe_payment_intent_id": "pi_v"})["status"] == "failed"


def test_account_updated_only_from_the_tenants_own_account(env):
    client, sync = env
    forged = {"id": "evt_acct1", "object": "event", "type": "account.updated", "account": "acct_attacker",
              "data": {"object": {"id": "acct_attacker", "object": "account", "charges_enabled": False,
                                  "payouts_enabled": False, "metadata": {"tenant_id": "victim"}}}}
    assert _post(client, "/api/stripe/connect-webhook", forged).json().get("rejected") is True
    assert "stripe_connect_status" not in sync.tenants.find_one({"id": "victim"})
    real = {"id": "evt_acct2", "object": "event", "type": "account.updated", "account": "acct_victim",
            "data": {"object": {"id": "acct_victim", "object": "account", "charges_enabled": True,
                                "payouts_enabled": True, "metadata": {"tenant_id": "victim"}}}}
    assert _post(client, "/api/stripe/connect-webhook", real).json() == {"ok": True}
    assert sync.tenants.find_one({"id": "victim"})["stripe_connect_status"] == "active"


# ---------- Platform webhook ----------

def _cs_event(ev_id, payment_status, type_="checkout.session.completed"):
    return {"id": ev_id, "object": "event", "type": type_,
            "data": {"object": {"id": "cs_1", "object": "checkout.session", "status": "complete",
                                "payment_status": payment_status, "subscription": "sub_1", "customer": "cus_1",
                                "metadata": {"tenant_id": "victim", "plan_id": "pro"}}}}


def test_unpaid_checkout_does_not_activate_plan_until_async_payment_succeeds(env):
    client, sync = env
    assert _post(client, "/api/stripe/webhook", _cs_event("evt_u", "unpaid")).status_code == 200
    t = sync.tenants.find_one({"id": "victim"})
    assert t.get("subscription_status") != "active" and t.get("plan_id") is None
    assert sync.payment_transactions.find_one({"session_id": "cs_1"})["payment_status"] == "unpaid"
    r = _post(client, "/api/stripe/webhook",
              _cs_event("evt_a", "paid", "checkout.session.async_payment_succeeded"))
    assert r.status_code == 200
    t = sync.tenants.find_one({"id": "victim"})
    assert t["subscription_status"] == "active" and t["plan_id"] == "pro"


def test_paid_checkout_activates_plan(env):
    client, sync = env
    assert _post(client, "/api/stripe/webhook", _cs_event("evt_p", "paid")).status_code == 200
    assert sync.tenants.find_one({"id": "victim"})["subscription_status"] == "active"


def test_replayed_platform_event_is_ignored(env):
    client, sync = env
    ev = _cs_event("evt_once", "paid")
    assert _post(client, "/api/stripe/webhook", ev).json() == {"status": "ok"}
    # the business cancels; an attacker / misfire re-delivers the same signed event later
    sync.tenants.update_one({"id": "victim"}, {"$set": {"subscription_status": "canceled", "plan_id": None}})
    r = _post(client, "/api/stripe/webhook", ev)
    assert r.json() == {"status": "ok", "duplicate": True}
    assert sync.tenants.find_one({"id": "victim"})["subscription_status"] == "canceled"


def test_failed_platform_processing_lets_stripe_retry(env, monkeypatch):
    client, sync = env
    real = sync.client  # noqa: F841  (keeps the sync client alive)
    calls = {"n": 0}
    orig = payments.get_db

    class Boom:
        def __getattr__(self, name):
            coll = getattr(orig(), name)
            if name == "payment_transactions" and calls["n"] == 0:
                calls["n"] += 1

                async def fail(*a, **k):
                    raise RuntimeError("db hiccup")
                return SimpleNamespace(update_one=fail)
            return coll

    monkeypatch.setattr(payments, "get_db", lambda: Boom())
    ev = _cs_event("evt_retry", "paid")
    with pytest.raises(RuntimeError):
        _post(client, "/api/stripe/webhook", ev)
    assert sync.platform_webhook_events.count_documents({"_id": "evt_retry"}) == 0
    assert _post(client, "/api/stripe/webhook", ev).json() == {"status": "ok"}
    assert sync.tenants.find_one({"id": "victim"})["subscription_status"] == "active"


def test_status_poll_does_not_activate_complete_but_unpaid_session(env, monkeypatch):
    client, sync = env
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fix037_unit_only")
    from services import stripe_config
    stripe_config.configure_stripe_api_key()
    sess = SimpleNamespace(payment_status="unpaid", status="complete", subscription="sub_1",
                           customer="cus_1", payment_intent=None)
    monkeypatch.setattr(stripe.checkout.Session, "retrieve", lambda sid: sess)
    app = FastAPI()
    app.include_router(payments.router, prefix="/api")
    with TestClient(app) as c:
        r = c.get("/api/payments/status/cs_1")
        assert r.status_code == 200 and r.json()["payment_status"] == "unpaid"
        assert sync.tenants.find_one({"id": "victim"}).get("subscription_status") != "active"
        sess.payment_status = "paid"
        assert c.get("/api/payments/status/cs_1").json()["payment_status"] == "paid"
    assert sync.tenants.find_one({"id": "victim"})["subscription_status"] == "active"
