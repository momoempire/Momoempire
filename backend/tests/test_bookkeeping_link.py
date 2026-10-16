"""Unit tests for the bookkeeping link (services/accounting.py,
services/token_vault.py, routers/bookkeeping_link.py, and the
invoice-paid auto-sync hook in routers/call_to_payment.py).

No database, network, or browser: a small FastAPI app with only the
bookkeeping-link router, a fake in-memory Mongo, overridden auth
dependencies, and a stubbed HTTP layer on MindAtEaseProvider (NO live
calls to the Render bookkeeping URL).
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest

os.environ.setdefault("JWT_SECRET", "bookkeeping-link-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "bookkeeping_link_test")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("BOOKKEEPING_LINK_ENCRYPTION_KEY", Fernet.generate_key().decode())

# Stub `stripe` before importing call_to_payment (unit env has no stripe pkg).
_stripe_stub = types.ModuleType("stripe")
_stripe_stub.api_key = None


class _StripeError(Exception):
    pass


class _InvalidRequestError(_StripeError):
    pass


_stripe_stub.error = types.SimpleNamespace(
    StripeError=_StripeError, InvalidRequestError=_InvalidRequestError)
_stripe_stub.Account = types.SimpleNamespace(create=None, retrieve=None)
_stripe_stub.PaymentIntent = types.SimpleNamespace(create=None)
_stripe_stub.AccountLink = types.SimpleNamespace(create=None)
sys.modules.setdefault("stripe", _stripe_stub)

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.bookkeeping_link as bk_mod  # noqa: E402
import routers.call_to_payment as c2p_mod  # noqa: E402
from security import require_tenant_user, require_tenant_owner_or_admin  # noqa: E402
from services.accounting import (  # noqa: E402
    AccountingError,
    AccountingProvider,
    MindAtEaseProvider,
    SyncCustomer,
    SyncInvoice,
    SyncInvoiceLine,
    SyncPayment,
    get_provider,
    idempotency_key,
)
from services import token_vault  # noqa: E402


# ---------- fake mongo ----------

class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        self.docs = sorted(self.docs, key=lambda d: str(d.get(key, "")),
                           reverse=(direction < 0))
        return self

    async def to_list(self, n):
        return [dict(d) for d in self.docs[:n]]


def _match(doc, query):
    for k, cond in query.items():
        val = doc.get(k)
        if isinstance(cond, dict):
            for op, opval in cond.items():
                if op == "$in" and val not in opval:
                    return False
                elif op == "$ne" and val == opval:
                    return False
                elif op not in ("$in", "$ne"):
                    return False
        elif val != cond:
            return False
    return True


class FakeCollection:
    def __init__(self):
        self.docs = []

    def find(self, query, proj=None):
        return FakeCursor([d for d in self.docs if _match(d, query)])

    async def find_one(self, query, proj=None):
        for d in self.docs:
            if _match(d, query):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_one(self, query, update, upsert=False):
        matched = 0
        for d in self.docs:
            if _match(d, query):
                matched += 1
                for k, v in update.get("$set", {}).items():
                    d[k] = v
                break
        if matched == 0 and upsert:
            doc = dict(query)
            doc.update(update.get("$set", {}))
            doc.update(update.get("$setOnInsert", {}))
            self.docs.append(doc)
        return type("R", (), {"matched_count": matched, "modified_count": matched})()


class FakeDB:
    def __init__(self):
        self.tenants = FakeCollection()
        self.invoices = FakeCollection()
        self.invoice_payments = FakeCollection()


# ---------- fixtures ----------

current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(bk_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(bk_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user

    def fake_owner():
        if current_user.get("role") not in {"owner", "admin"}:
            raise HTTPException(403, "Owner/admin role required")
        return current_user

    app.dependency_overrides[require_tenant_owner_or_admin] = fake_owner
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def seed_tenant(fake_db, tenant_id="t1", settings=None):
    doc = {"id": tenant_id, "name": f"Shop {tenant_id}"}
    if settings is not None:
        doc["bookkeeping_link_settings"] = settings
    fake_db.tenants.docs.append(doc)


def seed_paid_invoice(fake_db, tenant_id="t1", inv_id="inv-1"):
    fake_db.invoices.docs.append({
        "id": inv_id, "tenant_id": tenant_id,
        "customer_name": "Jane Doe", "customer_email": "jane@example.com",
        "customer_phone": "555-0100", "title": "AC repair",
        "lines": [{"description": "AC repair labor", "quantity": 1, "unit_price": 120.0}],
        "total_cents": 12000, "amount_paid_cents": 12000,
        "status": "paid", "due_at": "2026-10-29T00:00:00+00:00",
    })


def encrypted_settings(**over):
    s = dict(bk_mod.DEFAULT_SETTINGS)
    s.update({"enabled": True, "auto_sync_on_paid": True,
              "api_token_enc": token_vault.encrypt_secret("bk-test-token"),
              "api_token_set": True})
    s.update(over)
    return s


# Fake HTTP layer for the provider: records calls, returns canned responses.
class FakeHTTP:
    def __init__(self):
        self.calls = []

    async def __call__(self, method, path, json=None, idem=None):
        self.calls.append((method, path, json, idem))
        if method == "GET" and path == "/auth/me":
            return {"id": "u-1", "name": "Owner", "email": "o@example.com",
                    "tenant": {"name": "Test Shop"}}
        if method == "POST" and path == "/customers":
            return {"id": "bk-cust-1", "name": (json or {}).get("name")}
        if method == "POST" and path == "/invoices":
            return {"id": "bk-inv-1"}
        if method == "POST" and path.endswith("/pay"):
            return {"status": "paid", "entry_number": "JE-42"}
        raise AssertionError(f"unexpected call {method} {path}")


@pytest.fixture
def fake_http(monkeypatch):
    fh = FakeHTTP()
    monkeypatch.setattr(MindAtEaseProvider, "_request", fh)
    return fh


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------- provider ABC ----------

def test_abc_cannot_be_instantiated():
    with pytest.raises(TypeError):
        AccountingProvider()  # abstract


def test_mindatease_implements_abc():
    assert issubclass(MindAtEaseProvider, AccountingProvider)
    assert set(AccountingProvider.__abstractmethods__) <= {
        "test_connection", "push_customer", "push_invoice", "push_payment"}
    p = MindAtEaseProvider(api_token="x")
    for m in ("test_connection", "push_customer", "push_invoice", "push_payment"):
        assert callable(getattr(p, m))


def test_get_provider_unknown_raises():
    with pytest.raises(AccountingError):
        get_provider("quickbooks")  # deliberately unsupported


def test_idempotency_key_deterministic_and_scoped():
    k1 = idempotency_key("t1", "inv-1", "payment")
    assert k1 == idempotency_key("t1", "inv-1", "payment")
    assert k1 != idempotency_key("t1", "inv-1", "invoice")
    assert k1 != idempotency_key("t2", "inv-1", "payment")


# ---------- token vault ----------

def test_token_encryption_round_trip():
    enc = token_vault.encrypt_secret("super-secret-token")
    assert enc != "super-secret-token"
    assert token_vault.decrypt_secret(enc) == "super-secret-token"


def test_token_vault_fails_closed_without_key(monkeypatch):
    monkeypatch.delenv("BOOKKEEPING_LINK_ENCRYPTION_KEY", raising=False)
    with pytest.raises(token_vault.EncryptionNotConfigured):
        token_vault.encrypt_secret("x")
    with pytest.raises(token_vault.EncryptionNotConfigured):
        token_vault.decrypt_secret("x")


# ---------- settings: tenant isolation + roles ----------

def test_settings_tenant_isolation(client, fake_db):
    seed_tenant(fake_db, "t1", {"enabled": True, "api_base_url": "https://a.example"})
    seed_tenant(fake_db, "t2", {"enabled": False})
    as_user("t1")
    assert client.get("/tenants/bookkeeping-link/settings").json()["enabled"] is True
    as_user("t2")
    body = client.get("/tenants/bookkeeping-link/settings").json()
    assert body["enabled"] is False
    assert body["api_base_url"] == bk_mod.DEFAULT_API_BASE_URL


def test_settings_write_staff_forbidden(client, fake_db):
    seed_tenant(fake_db, "t1")
    as_user("t1", role="staff")
    r = client.put("/tenants/bookkeeping-link/settings", json={"enabled": True})
    assert r.status_code == 403


def test_test_connection_staff_forbidden(client, fake_db):
    seed_tenant(fake_db, "t1")
    as_user("t1", role="staff")
    assert client.post("/tenants/bookkeeping-link/test-connection").status_code == 403


def test_settings_round_trip_never_returns_raw_token(client, fake_db):
    seed_tenant(fake_db, "t1")
    as_user("t1")
    r = client.put("/tenants/bookkeeping-link/settings", json={
        "enabled": True, "api_base_url": "https://bookkeeping-api-ymh6.onrender.com",
        "api_token": "owner-jwt-token-123", "auto_sync_on_paid": True})
    assert r.status_code == 200
    body = r.json()
    assert body["api_token_set"] is True
    assert "owner-jwt-token-123" not in str(body)
    assert "api_token" not in body and "api_token_enc" not in body
    again = client.get("/tenants/bookkeeping-link/settings").json()
    assert again["api_token_set"] is True
    assert "owner-jwt-token-123" not in str(again)
    # ...but the stored ciphertext decrypts back to the token
    stored = fake_db.tenants.docs[0]["bookkeeping_link_settings"]
    assert token_vault.decrypt_secret(stored["api_token_enc"]) == "owner-jwt-token-123"


def test_settings_rejects_non_https_base_url(client, fake_db):
    seed_tenant(fake_db, "t1")
    as_user("t1")
    r = client.put("/tenants/bookkeeping-link/settings",
                   json={"api_base_url": "http://evil.example.com"})
    assert r.status_code == 400


# ---------- test-connection ----------

def test_test_connection_success(client, fake_db, fake_http):
    seed_tenant(fake_db, "t1", encrypted_settings())
    as_user("t1")
    r = client.post("/tenants/bookkeeping-link/test-connection")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "Owner" in body["detail"] and "Test Shop" in body["detail"]
    assert fake_http.calls[0][:2] == ("GET", "/auth/me")
    stored = fake_db.tenants.docs[0]["bookkeeping_link_settings"]
    assert stored["last_test_ok"] is True and stored["last_test_at"]


def test_test_connection_failure_records_and_returns_detail(client, fake_db, monkeypatch):
    seed_tenant(fake_db, "t1", encrypted_settings())
    as_user("t1")

    async def boom(self, method, path, json=None, idem=None):
        raise AccountingError("Bookkeeping API rejected the token (401)")

    monkeypatch.setattr(MindAtEaseProvider, "_request", boom)
    r = client.post("/tenants/bookkeeping-link/test-connection")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and "401" in body["detail"]
    stored = fake_db.tenants.docs[0]["bookkeeping_link_settings"]
    assert stored["last_test_ok"] is False


# ---------- sync engine: idempotent push, no live calls ----------

def test_sync_pushes_customer_invoice_payment(fake_db, fake_http):
    seed_tenant(fake_db, "t1", encrypted_settings())
    seed_paid_invoice(fake_db, "t1")
    inv = run(fake_db.invoices.find_one({"id": "inv-1"}))
    result = run(bk_mod.sync_invoice_paid(fake_db, "t1", inv))
    assert result["synced"] is True
    assert result["steps"] == ["customer", "invoice", "payment"]
    assert result["customer_provider_id"] == "bk-cust-1"
    assert result["invoice_provider_id"] == "bk-inv-1"
    assert result["payment_provider_id"] == "JE-42"
    # Idempotency keys are stable per step.
    keys = [c[3] for c in fake_http.calls]
    assert keys[0] == idempotency_key("t1", "inv-1", "customer")
    assert keys[1] == idempotency_key("t1", "inv-1", "invoice")
    assert keys[2] == idempotency_key("t1", "inv-1", "payment")
    # Provider ids recorded on the local invoice doc.
    inv2 = run(fake_db.invoices.find_one({"id": "inv-1"}))
    assert inv2["bookkeeping_sync"]["invoice_provider_id"] == "bk-inv-1"
    # Tenant counts + last sync recorded.
    s = run(bk_mod._get_settings(fake_db, "t1"))
    assert s["counts"] == {"customers": 1, "invoices": 1, "payments": 1}
    assert s["last_sync_at"] and s["last_error"] is None


def test_sync_is_idempotent_push_twice_one_provider_record(fake_db, fake_http):
    seed_tenant(fake_db, "t1", encrypted_settings())
    seed_paid_invoice(fake_db, "t1")
    inv = run(fake_db.invoices.find_one({"id": "inv-1"}))
    first = run(bk_mod.sync_invoice_paid(fake_db, "t1", inv))
    assert first["synced"] is True
    calls_after_first = len(fake_http.calls)
    assert calls_after_first == 3
    inv = run(fake_db.invoices.find_one({"id": "inv-1"}))
    second = run(bk_mod.sync_invoice_paid(fake_db, "t1", inv))
    assert second["synced"] is True and second["steps"] == []
    assert len(fake_http.calls) == calls_after_first  # no new HTTP calls


def test_sync_noop_when_disabled(fake_db, fake_http):
    seed_tenant(fake_db, "t1", encrypted_settings(enabled=False))
    seed_paid_invoice(fake_db, "t1")
    inv = run(fake_db.invoices.find_one({"id": "inv-1"}))
    result = run(bk_mod.sync_invoice_paid(fake_db, "t1", inv))
    assert result == {"synced": False, "reason": "disabled"}
    assert fake_http.calls == []


def test_sync_noop_when_auto_sync_off(fake_db, fake_http):
    seed_tenant(fake_db, "t1", encrypted_settings(auto_sync_on_paid=False))
    seed_paid_invoice(fake_db, "t1")
    inv = run(fake_db.invoices.find_one({"id": "inv-1"}))
    result = run(bk_mod.sync_invoice_paid(fake_db, "t1", inv))
    assert result == {"synced": False, "reason": "auto_sync_off"}
    assert fake_http.calls == []


def test_sync_failure_never_raises_and_records_error(fake_db, monkeypatch):
    seed_tenant(fake_db, "t1", encrypted_settings())
    seed_paid_invoice(fake_db, "t1")

    async def boom(self, method, path, json=None, idem=None):
        raise AccountingError("Bookkeeping API unreachable")

    monkeypatch.setattr(MindAtEaseProvider, "_request", boom)
    inv = run(fake_db.invoices.find_one({"id": "inv-1"}))
    result = run(bk_mod.sync_invoice_paid(fake_db, "t1", inv))  # must not raise
    assert result["synced"] is False and result["reason"] == "error"
    s = run(bk_mod._get_settings(fake_db, "t1"))
    assert "unreachable" in (s["last_error"] or "")


# ---------- manual sync endpoint ----------

def test_manual_sync_endpoint(client, fake_db, fake_http):
    seed_tenant(fake_db, "t1", encrypted_settings())
    seed_paid_invoice(fake_db, "t1")
    as_user("t1", role="staff")  # any tenant user may trigger a manual sync
    r = client.post("/tenants/bookkeeping-link/invoices/inv-1/sync")
    assert r.status_code == 200
    assert r.json()["synced"] is True
    # Second manual sync is a no-op at the provider level (idempotent).
    r2 = client.post("/tenants/bookkeeping-link/invoices/inv-1/sync")
    assert r2.json()["steps"] == []
    assert len(fake_http.calls) == 3


def test_manual_sync_unpaid_rejected(client, fake_db):
    seed_tenant(fake_db, "t1", encrypted_settings())
    fake_db.invoices.docs.append({"id": "inv-9", "tenant_id": "t1", "status": "sent"})
    as_user("t1")
    assert client.post("/tenants/bookkeeping-link/invoices/inv-9/sync").status_code == 400


def test_sync_status_endpoint(client, fake_db):
    seed_tenant(fake_db, "t1", encrypted_settings())
    as_user("t1", role="staff")
    body = client.get("/tenants/bookkeeping-link/sync-status").json()
    assert body["enabled"] is True and body["api_token_set"] is True
    assert body["counts"] == {"customers": 0, "invoices": 0, "payments": 0}
    assert body["last_sync_at"] is None and body["last_error"] is None


# ---------- auto-sync hook wiring in call_to_payment ----------

def test_hook_fires_on_invoice_paid_when_enabled(fake_db, fake_http, monkeypatch):
    """Drive _apply_payment_success (the Stripe webhook path) end to end with
    a fake DB and stubbed HTTP: flipping an invoice to paid must trigger the
    bookkeeping sync exactly once, with no live network calls."""
    monkeypatch.setattr(bk_mod, "get_db", lambda: fake_db)
    fake_db.tenants.docs.append({"id": "t1", "name": "Shop t1",
                                 "stripe_connect_id": "acct_1",
                                 "bookkeeping_link_settings": encrypted_settings()})
    fake_db.invoices.docs.append({
        "id": "inv-1", "tenant_id": "t1", "customer_name": "Jane Doe",
        "title": "AC repair",
        "lines": [{"description": "AC repair labor", "quantity": 1, "unit_price": 120.0}],
        "total_cents": 12000, "amount_paid_cents": 0, "status": "sent",
    })
    fake_db.invoice_payments.docs.append({
        "stripe_payment_intent_id": "pi_123", "invoice_id": "inv-1",
        "tenant_id": "t1", "connected_account": "acct_1", "amount_cents": 12000,
    })
    pi = {"id": "pi_123", "amount_received": 12000, "currency": "usd",
          "metadata": {"invoice_id": "inv-1", "tenant_id": "t1"}}
    run(c2p_mod._apply_payment_success(fake_db, pi, "acct_1"))
    inv = run(fake_db.invoices.find_one({"id": "inv-1"}))
    assert inv["status"] == "paid"
    assert inv["bookkeeping_sync"]["payment_provider_id"] == "JE-42"
    assert len(fake_http.calls) == 3  # customer, invoice, payment — nothing live


def test_hook_noop_when_link_disabled(fake_db, fake_http, monkeypatch):
    monkeypatch.setattr(bk_mod, "get_db", lambda: fake_db)
    fake_db.tenants.docs.append({"id": "t1", "name": "Shop t1",
                                 "stripe_connect_id": "acct_1",
                                 "bookkeeping_link_settings":
                                     encrypted_settings(enabled=False)})
    fake_db.invoices.docs.append({
        "id": "inv-1", "tenant_id": "t1", "customer_name": "Jane Doe",
        "title": "AC repair", "lines": [],
        "total_cents": 12000, "amount_paid_cents": 0, "status": "sent",
    })
    fake_db.invoice_payments.docs.append({
        "stripe_payment_intent_id": "pi_123", "invoice_id": "inv-1",
        "tenant_id": "t1", "connected_account": "acct_1", "amount_cents": 12000,
    })
    pi = {"id": "pi_123", "amount_received": 12000, "currency": "usd",
          "metadata": {"invoice_id": "inv-1", "tenant_id": "t1"}}
    run(c2p_mod._apply_payment_success(fake_db, pi, "acct_1"))
    inv = run(fake_db.invoices.find_one({"id": "inv-1"}))
    assert inv["status"] == "paid"  # the paid flow itself is unaffected
    assert "bookkeeping_sync" not in inv
    assert fake_http.calls == []  # sync correctly skipped
