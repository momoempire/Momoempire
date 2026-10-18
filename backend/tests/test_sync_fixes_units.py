"""Financial-sync fix track (audit 2026-10-17) — unit regressions.

Covers, without any live server:
  * F4 — the default books base URL includes the /api segment (Track E
    D-4: books serves only under /api; the old default 404'd every
    call for tenants who accepted it).
  * F1 — MindAtEaseProvider.push_customer adopts the existing_id from
    books' natural-dedup 409 (Track E Confirmed Broken #1), while the
    idempotency payload-mismatch 409 stays a hard error.
  * F2 — push_refund posts to the books sync-surface refund endpoint;
    providers without a refund surface refuse cleanly.

Pattern follows tests/test_bookkeeping_link.py (stubbed HTTP layer,
no network).
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest

os.environ.setdefault("JWT_SECRET", "sync-fixes-unit-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "sync_fixes_unit_test")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("BOOKKEEPING_LINK_ENCRYPTION_KEY",
                      Fernet.generate_key().decode())

_stripe_stub = types.ModuleType("stripe")
_stripe_stub.api_key = None


class _StripeError(Exception):
    pass


_stripe_stub.error = types.SimpleNamespace(
    StripeError=_StripeError, InvalidRequestError=_StripeError)
_stripe_stub.Account = types.SimpleNamespace(create=None, retrieve=None)
_stripe_stub.PaymentIntent = types.SimpleNamespace(create=None)
_stripe_stub.AccountLink = types.SimpleNamespace(create=None)
sys.modules.setdefault("stripe", _stripe_stub)

import routers.bookkeeping_link as bk_mod  # noqa: E402
from services.accounting import (  # noqa: E402
    AccountingError, AccountingProvider, DEFAULT_API_BASE_URL,
    MindAtEaseProvider, SyncCustomer)

try:  # SyncRefund is added by the fix track; pre-fix it does not
    # exist, and the F2 unit tests below must FAIL (not error out at
    # collection) on the pre-fix branch to serve as regression proof.
    from services.accounting import SyncRefund  # noqa: E402
except ImportError:  # pragma: no cover - pre-fix branch only
    SyncRefund = None


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class FakeRequest:
    """Replacement for MindAtEaseProvider._request (patched onto the
    class, so __call__ receives no self — same trick as FakeHTTP in
    test_bookkeeping_link.py)."""

    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result
        self.error = error

    async def __call__(self, method, path, json=None, idem=None):
        self.calls.append((method, path, json, idem))
        if self.error is not None:
            raise self.error
        return self.result


def http_error(status, body, text="err"):
    err = AccountingError(f"Bookkeeping API error {status}: {text}",
                          kind="http")
    err.status_code = status
    err.response_body = body
    return err


# ---------------- F4: default base URL ----------------

def test_default_base_url_includes_api_segment():
    assert DEFAULT_API_BASE_URL.endswith("/api"), DEFAULT_API_BASE_URL
    p = MindAtEaseProvider(api_token="tok")
    assert p.base_url == DEFAULT_API_BASE_URL
    assert bk_mod.DEFAULT_SETTINGS["api_base_url"] == DEFAULT_API_BASE_URL


def test_provider_still_allows_explicit_base_url():
    p = MindAtEaseProvider(api_base_url="http://127.0.0.1:8791/api/",
                           api_token="tok")
    assert p.base_url == "http://127.0.0.1:8791/api"  # trailing / trimmed


# ---------------- F1: customer dedup adoption ----------------

def test_push_customer_adopts_existing_id_on_dedup_409(monkeypatch):
    err = http_error(409, {"detail": {
        "message": "Customer already exists",
        "existing_id": "cust-existing-1",
        "existing_name": "Repeat Customer"}})
    monkeypatch.setattr(MindAtEaseProvider, "_request", FakeRequest(error=err))
    p = MindAtEaseProvider(api_base_url="http://x/api", api_token="tok")
    cid = run(p.push_customer(
        SyncCustomer(name="Repeat Customer", email="r@example.com"),
        idempotency_key="k1"))
    assert cid == "cust-existing-1"


def test_push_customer_payload_mismatch_409_still_raises(monkeypatch):
    # The idempotency payload-mismatch 409 carries a plain-string
    # detail and NO existing_id: per contract §4.3 it is a hard error.
    err = http_error(409, {
        "detail": "Idempotency-Key was already used with a different "
                  "request payload"})
    monkeypatch.setattr(MindAtEaseProvider, "_request", FakeRequest(error=err))
    p = MindAtEaseProvider(api_base_url="http://x/api", api_token="tok")
    with pytest.raises(AccountingError):
        run(p.push_customer(SyncCustomer(name="X"), idempotency_key="k2"))


def test_push_customer_other_http_errors_still_raise(monkeypatch):
    monkeypatch.setattr(MindAtEaseProvider, "_request",
                        FakeRequest(error=http_error(500, None)))
    p = MindAtEaseProvider(api_base_url="http://x/api", api_token="tok")
    with pytest.raises(AccountingError):
        run(p.push_customer(SyncCustomer(name="X"), idempotency_key="k3"))


def test_push_customer_success_path_unchanged(monkeypatch):
    fake = FakeRequest(result={"id": "cust-new-1"})
    monkeypatch.setattr(MindAtEaseProvider, "_request", fake)
    p = MindAtEaseProvider(api_base_url="http://x/api", api_token="tok")
    cid = run(p.push_customer(
        SyncCustomer(name="New", email="n@example.com", phone="555"),
        idempotency_key="k4"))
    assert cid == "cust-new-1"
    method, path, payload, idem = fake.calls[0]
    assert (method, path) == ("POST", "/customers")
    assert payload == {"name": "New", "email": "n@example.com",
                       "phone": "555"}
    assert idem == "k4"


# ---------------- F2: provider refund push ----------------

def test_push_refund_posts_to_refund_endpoint(monkeypatch):
    fake = FakeRequest(result={"entry_number": "JE-99", "status": "issued"})
    monkeypatch.setattr(MindAtEaseProvider, "_request", fake)
    p = MindAtEaseProvider(api_base_url="http://x/api", api_token="tok")
    entry = run(p.push_refund(
        SyncRefund(invoice_provider_id="bk-inv-1", amount_cents=1250),
        idempotency_key="rk1"))
    assert entry == "JE-99"
    method, path, payload, idem = fake.calls[0]
    assert (method, path) == ("POST", "/invoices/bk-inv-1/refund")
    assert payload == {"payment_method": "card", "amount": 12.5}
    assert idem == "rk1"


def test_push_refund_missing_entry_number_raises(monkeypatch):
    monkeypatch.setattr(MindAtEaseProvider, "_request",
                        FakeRequest(result={"status": "issued"}))
    p = MindAtEaseProvider(api_base_url="http://x/api", api_token="tok")
    with pytest.raises(AccountingError):
        run(p.push_refund(
            SyncRefund(invoice_provider_id="bk-inv-1", amount_cents=100),
            idempotency_key="rk2"))


def test_abc_push_refund_default_refuses():
    class Minimal(AccountingProvider):
        async def test_connection(self):
            return {}

        async def push_customer(self, customer, *, idempotency_key):
            return "c"

        async def push_invoice(self, invoice, *, idempotency_key):
            return "i"

        async def push_payment(self, payment, *, idempotency_key):
            return "p"

    with pytest.raises(AccountingError) as ei:
        run(Minimal().push_refund(
            SyncRefund(invoice_provider_id="x", amount_cents=1),
            idempotency_key="rk3"))
    assert ei.value.kind == "config"
