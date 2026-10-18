"""WS-3 — Stripe MX (Mexico entry playbook §3.3/§3.4).

Covers: the MXN anchor table vs the playbook, the FX assumption, MXN
checkout construction (OXXO + SPEI rails, MSI), IVA-inclusive display,
the MX plans endpoint data, USD-path non-regression, and the OXXO
pending-state contract end-to-end through the platform webhook:
completed(unpaid) does not activate → async_payment_succeeded activates;
async_payment_failed closes the transaction and never activates
(playbook §4.5 gate). Fake in-memory mongo + monkeypatched Stripe —
no live keys, no network.
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("JWT_SECRET", "stripe-mx-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "stripe_mx_test")

import pytest  # noqa: E402
from pymongo.errors import DuplicateKeyError  # noqa: E402

import stripe  # noqa: E402
from routers import payments  # noqa: E402
import services.stripe_mx as mx  # noqa: E402


# ---------- fake mongo ----------

def _match(doc, query):
    for key, want in query.items():
        if isinstance(want, dict) and "$ne" in want:
            if doc.get(key) == want["$ne"]:
                return False
        elif doc.get(key) != want:
            return False
    return True


class FakeCollection:
    def __init__(self):
        self.docs = []

    async def find_one(self, query, proj=None):
        for d in self.docs:
            if _match(d, query):
                return dict(d)
        return None

    async def insert_one(self, doc):
        if "_id" in doc and any(d.get("_id") == doc["_id"] for d in self.docs):
            raise DuplicateKeyError("duplicate event id")
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("_id"))

    async def update_one(self, query, update, upsert=False):
        n = 0
        for d in self.docs:
            if _match(d, query):
                d.update(update.get("$set", {}))
                n += 1
        return SimpleNamespace(matched_count=n, modified_count=n)

    async def delete_one(self, query):
        self.docs = [d for d in self.docs if not _match(d, query)]
        return SimpleNamespace(deleted_count=1)


class FakeDB:
    def __init__(self):
        self._cols = {}

    def __getattr__(self, name):
        return self._cols.setdefault(name, FakeCollection())


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def fake_db(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(payments, "get_db", lambda: db)
    return db


# ---------- anchors & builders (pure) ----------

def test_anchors_match_playbook_table():
    # Playbook §3.3 at MX$18.4/US$1: Lite 184, Starter 736, Pro 2,944, Empire 6,808.
    assert mx.MXN_FX_RATE == 18.4
    assert mx.MXN_PRICE_ANCHORS["lite"]["mxn_pesos"] == 184
    assert mx.MXN_PRICE_ANCHORS["starter"]["mxn_pesos"] == 736
    assert mx.MXN_PRICE_ANCHORS["pro"]["mxn_pesos"] == 2944
    assert mx.MXN_PRICE_ANCHORS["empire"]["mxn_pesos"] == 6808
    assert mx.MXN_PRICE_ANCHORS["pro"]["mxn_cents"] == 294400
    # Unlimited $549.99 (2026-10-17), derived by the same FX pattern:
    # round(549.99 x 18.4) = 10,120.
    assert mx.MXN_PRICE_ANCHORS["unlimited"]["mxn_pesos"] == 10120
    assert mx.MXN_PRICE_ANCHORS["unlimited"]["mxn_cents"] == 1012000


def test_mx_line_item_and_kwargs():
    plan = {"key": "pro", "name": "Pro"}
    item = mx.mx_checkout_line_item(plan, quantity=2)
    pd = item["price_data"]
    assert pd["currency"] == "mxn" and pd["unit_amount"] == 294400
    assert pd["recurring"] == {"interval": "month"} and item["quantity"] == 2
    kw = mx.mx_checkout_kwargs(plan)
    assert kw["payment_method_types"] == ["card", "oxxo", "customer_balance"]
    bt = kw["payment_method_options"]["customer_balance"]["bank_transfer"]
    assert bt["type"] == "mx_bank_transfer"  # SPEI rail
    assert kw["payment_method_options"]["card"]["installments"]["enabled"] is True  # MSI
    with pytest.raises(ValueError):
        mx.mx_checkout_line_item({"key": "enterprise", "name": "Enterprise"})


def test_iva_inclusive_display_and_copy():
    d = mx.format_price_display_mx("pro")
    assert d["iva_included"] is True and d["currency"] == "MXN"
    assert d["amount"] == "$2,944.00 MXN"
    assert "IVA incluido" in d["label_es"]
    assert "ahorra dos meses" in mx.MX_BILLING_COPY["annual_via_transfer"]["es"]
    disp = mx.mx_plans_display()
    assert [p["plan"] for p in disp] == ["lite", "starter", "pro", "empire", "unlimited"]


def test_plans_mx_endpoint_data(fake_db):
    out = run(payments.list_plans_mx())
    assert out["market"] == "mx" and out["iva_included"] is True
    assert out["payment_method_types"] == ["card", "oxxo", "customer_balance"]
    assert len(out["plans"]) == 5 and out["plans"][2]["mxn_cents"] == 294400
    assert out["plans"][4]["plan"] == "unlimited" and out["plans"][4]["mxn_cents"] == 1012000
    assert "annual_via_transfer" in out["billing_copy"]


# ---------- checkout paths ----------

@pytest.fixture()
def stripe_capture(monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="cs_test_1", url="https://checkout.stripe.test/x")

    monkeypatch.setattr(stripe.checkout.Session, "create", fake_create)
    monkeypatch.setattr(payments, "require_stripe_api_key", lambda: None)
    return captured


async def _seed_plan(db):
    await db.plans.insert_one({"key": "pro", "name": "Pro", "price_cents": 15900})


def test_mx_checkout_uses_mxn_book_and_local_rails(fake_db, stripe_capture):
    async def go():
        await _seed_plan(fake_db)
        req = payments.CheckoutRequest(plan_id="pro", origin_url="https://app.example", market="mx")
        out = await payments.create_checkout(req, user={"tenant_id": "t1", "id": "u1"})
        assert out["session_id"] == "cs_test_1"
        item = stripe_capture["line_items"][0]
        assert item["price_data"]["currency"] == "mxn"
        assert item["price_data"]["unit_amount"] == 294400
        assert stripe_capture["payment_method_types"] == ["card", "oxxo", "customer_balance"]
        assert stripe_capture["metadata"]["market"] == "mx"
        txn = fake_db.payment_transactions.docs[0]
        assert txn["currency"] == "mxn" and txn["amount"] == 294400 and txn["market"] == "mx"
    run(go())


def test_usd_checkout_unchanged(fake_db, stripe_capture):
    async def go():
        await _seed_plan(fake_db)
        req = payments.CheckoutRequest(plan_id="pro", origin_url="https://app.example")
        await payments.create_checkout(req, user={"tenant_id": "t1", "id": "u1"})
        item = stripe_capture["line_items"][0]
        assert item["price_data"]["currency"] == "usd"
        assert item["price_data"]["unit_amount"] == 15900
        assert "payment_method_types" not in stripe_capture
        assert "market" not in stripe_capture["metadata"]
        txn = fake_db.payment_transactions.docs[0]
        assert txn["currency"] == "usd" and "market" not in txn
    run(go())


# ---------- webhook pending-state contract ----------

class FakeRequest:
    def __init__(self):
        self.headers = {"stripe-signature": "sig_test"}

    async def body(self):
        return b"{}"


def _event(ev_id, type_, payment_status):
    return {"id": ev_id, "type": type_, "data": {"object": {
        "id": "cs_mx_1", "payment_status": payment_status, "subscription": "sub_1",
        "customer": "cus_1", "payment_intent": "pi_1",
        "metadata": {"tenant_id": "t1", "plan_id": "pro", "market": "mx"},
    }}}


@pytest.fixture()
def webhook_env(monkeypatch, fake_db):
    async def seed():
        await fake_db.payment_transactions.insert_one({
            "session_id": "cs_mx_1", "tenant_id": "t1", "plan_id": "pro",
            "amount": 294400, "currency": "mxn", "market": "mx",
            "status": "initiated", "payment_status": "pending",
        })
        await fake_db.tenants.insert_one({"id": "t1", "subscription_status": "trial"})
    run(seed())
    return fake_db


def _post_event(monkeypatch, event):
    monkeypatch.setattr(payments, "construct_stripe_event", lambda payload, sig, secret: event)
    return run(payments.stripe_webhook(FakeRequest()))


def test_oxxo_pending_then_succeeded_activates(webhook_env, monkeypatch):
    db = webhook_env
    # Voucher generated: session completes but payment_status stays unpaid.
    _post_event(monkeypatch, _event("evt_1", "checkout.session.completed", "unpaid"))
    txn = db.payment_transactions.docs[0]
    assert txn["payment_status"] == "unpaid"
    assert db.tenants.docs[0]["subscription_status"] == "trial"  # NOT activated
    # Customer pays at OXXO: async success activates.
    _post_event(monkeypatch, _event("evt_2", "checkout.session.async_payment_succeeded", "paid"))
    txn = db.payment_transactions.docs[0]
    assert txn["payment_status"] == "paid" and txn["status"] == "completed"
    assert db.tenants.docs[0]["subscription_status"] == "active"
    assert db.tenants.docs[0]["plan_id"] == "pro"


def test_oxxo_voucher_expires_failed_never_activates(webhook_env, monkeypatch):
    db = webhook_env
    _post_event(monkeypatch, _event("evt_3", "checkout.session.completed", "unpaid"))
    _post_event(monkeypatch, _event("evt_4", "checkout.session.async_payment_failed", "unpaid"))
    txn = db.payment_transactions.docs[0]
    assert txn["status"] == "failed" and txn["payment_status"] == "failed"
    assert db.tenants.docs[0]["subscription_status"] == "trial"  # never activated
    assert db.tenants.docs[0].get("plan_id") is None
