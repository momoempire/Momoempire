"""Audit (2026-10-17) Track B — bookkeeping integration credentials,
connection health, rotation/revocation, and legacy-JWT migration.

Before this audit the link stored only a pasted owner JWT (~7-day
expiry) with a manual test button: no credential model, no health state,
no rotation flow, and failures surfaced only as a single last_error
string. These tests pin the new behavior:

  * credential kind classification (integration vs legacy_jwt) +
    fingerprint exposure, raw secret NEVER in any response;
  * migration surfacing for legacy tenants (sync keeps working);
  * health checks: healthy / degraded / revoked, consecutive failures;
  * revocation pauses sync without hammering the dead credential;
  * rotation validates the new credential BEFORE swapping;
  * disconnect wipes the secret and stops sync;
  * every sync failure is recorded (bounded log + per-invoice attempts);
  * the cron sweep health-checks all connected tenants.

Self-contained: in-memory fake Mongo (with dotted-key matching, like
real Mongo), dependency overrides, stubbed provider HTTP. No network.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest

os.environ.setdefault("JWT_SECRET", "bookkeeping-credentials-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "bookkeeping_credentials_test")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("BOOKKEEPING_LINK_ENCRYPTION_KEY", Fernet.generate_key().decode())

# Stub `stripe` before importing routers.cron (its import chain reaches
# payments code in some environments) — same approach as
# test_bookkeeping_link.py.
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

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.bookkeeping_link as bk_mod  # noqa: E402
import routers.cron as cron_mod  # noqa: E402
from security import require_tenant_user, require_tenant_owner_or_admin  # noqa: E402
from services.accounting import AccountingError, MindAtEaseProvider  # noqa: E402
from services import token_vault  # noqa: E402


# ---------- fake mongo with dotted-key matching ----------

def _resolve(doc, key):
    cur = doc
    for part in key.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _match(doc, query):
    for k, cond in query.items():
        val = _resolve(doc, k)
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


class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        self.docs = sorted(self.docs, key=lambda d: str(_resolve(d, key) or ""),
                           reverse=(direction < 0))
        return self

    async def to_list(self, n):
        return [dict(d) for d in self.docs[:n]]


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
        return type("R", (), {"matched_count": matched, "modified_count": matched})()


class FakeDB:
    def __init__(self):
        self.tenants = FakeCollection()
        self.invoices = FakeCollection()


# ---------- provider stub ----------

def _success_payload(method, path, json):
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


class ProviderStub:
    """Callable class replacing MindAtEaseProvider._request per-test.

    behavior: "ok" | AccountingError instance | fn(token) -> payload/exception
    """

    def __init__(self, behavior="ok"):
        self.behavior = behavior
        self.calls = []

    def install(self, monkeypatch):
        stub = self

        async def _request(provider, method, path, json=None, idem=None):
            stub.calls.append((provider.api_token, method, path))
            b = stub.behavior
            if isinstance(b, Exception):
                raise b
            if callable(b):
                return b(provider.api_token, method, path, json)
            return _success_payload(method, path, json)

        monkeypatch.setattr(MindAtEaseProvider, "_request", _request)
        return self


# ---------- fixtures / helpers ----------

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
    current_user.clear()
    current_user.update({"id": "u-t1", "tenant_id": "t1", "role": "owner"})
    fake_db.tenants.docs.append({"id": "t1", "name": "Shop t1"})
    return TestClient(app, raise_server_exceptions=False)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def seed_settings(fake_db, tenant_id="t1", token="bk-live-token", kind="integration",
                  **over):
    s = dict(bk_mod.DEFAULT_SETTINGS)
    s["health"] = bk_mod._default_health()
    s["sync_failures"] = []
    s.update({"enabled": True, "auto_sync_on_paid": True,
              "api_token_enc": token_vault.encrypt_secret(token),
              "api_token_set": True,
              "credential_kind": kind,
              "credential_fingerprint": bk_mod.credential_fingerprint(token)})
    s.update(over)
    for d in fake_db.tenants.docs:
        if d["id"] == tenant_id:
            d["bookkeeping_link_settings"] = s
            return s
    fake_db.tenants.docs.append({"id": tenant_id, "name": "Shop",
                                 "bookkeeping_link_settings": s})
    return s


def stored_settings(fake_db, tenant_id="t1"):
    for d in fake_db.tenants.docs:
        if d["id"] == tenant_id:
            return d.get("bookkeeping_link_settings") or {}
    return {}


def seed_paid_invoice(fake_db, tenant_id="t1", inv_id="inv-1"):
    fake_db.invoices.docs.append({
        "id": inv_id, "tenant_id": tenant_id,
        "customer_name": "Jane Doe", "customer_email": "jane@example.com",
        "customer_phone": "555-0100", "title": "AC repair",
        "lines": [{"description": "AC repair labor", "quantity": 1, "unit_price": 120.0}],
        "total_cents": 12000, "amount_paid_cents": 12000,
        "status": "paid", "due_at": "2026-10-29T00:00:00+00:00",
    })


def get_invoice(fake_db, inv_id="inv-1"):
    return next(d for d in fake_db.invoices.docs if d["id"] == inv_id)


# ---------- credential model ----------

def test_opaque_token_is_an_integration_credential(client, fake_db):
    r = client.put("/tenants/bookkeeping-link/settings",
                   json={"api_token": "bk_live_opaque_123"})
    assert r.status_code == 200
    body = r.json()
    assert body["credential_kind"] == "integration"
    assert body["credential_fingerprint"] == bk_mod.credential_fingerprint("bk_live_opaque_123")
    assert body["migration"]["required"] is False
    assert "bk_live_opaque_123" not in r.text  # raw secret never returned
    assert "api_token_enc" not in body


def test_jwt_shaped_token_is_legacy_and_flagged_for_migration(client, fake_db):
    jwt_like = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature"
    r = client.put("/tenants/bookkeeping-link/settings", json={"api_token": jwt_like})
    assert r.status_code == 200
    body = r.json()
    assert body["credential_kind"] == "legacy_jwt"
    assert body["migration"]["required"] is True
    assert body["migration"]["reason"] == "legacy_jwt"
    # …and the migration state is visible on sync-status too.
    s = client.get("/tenants/bookkeeping-link/sync-status")
    assert s.json()["migration"]["required"] is True
    assert s.json()["credential_kind"] == "legacy_jwt"


def test_explicit_kind_overrides_shape_and_bad_kind_rejected(client, fake_db):
    jwt_like = "aaa.bbb.ccc"
    r = client.put("/tenants/bookkeeping-link/settings",
                   json={"api_token": jwt_like, "credential_kind": "integration"})
    assert r.status_code == 200
    assert r.json()["credential_kind"] == "integration"
    r = client.put("/tenants/bookkeeping-link/settings",
                   json={"api_token": "x", "credential_kind": "bogus"})
    assert r.status_code == 400


def test_replacing_credential_records_rotation(client, fake_db):
    client.put("/tenants/bookkeeping-link/settings", json={"api_token": "first-token"})
    r = client.put("/tenants/bookkeeping-link/settings", json={"api_token": "second-token"})
    body = r.json()
    assert body["credential_fingerprint"] == bk_mod.credential_fingerprint("second-token")
    assert body["credential_saved_at"] is not None
    assert body["credential_rotated_at"] is not None


# ---------- health checks ----------

def test_health_check_success_marks_healthy(client, fake_db, monkeypatch):
    seed_settings(fake_db)
    ProviderStub("ok").install(monkeypatch)
    r = client.post("/tenants/bookkeeping-link/health-check")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["health"]["status"] == "healthy"
    assert stored_settings(fake_db)["health"]["status"] == "healthy"


def test_health_check_failures_mark_degraded_and_count(client, fake_db, monkeypatch):
    seed_settings(fake_db)
    ProviderStub(AccountingError("Bookkeeping API rejected the token (401)", kind="auth")).install(monkeypatch)
    for expected in (1, 2):
        r = client.post("/tenants/bookkeeping-link/health-check")
        assert r.json()["ok"] is False
        health = r.json()["health"]
        assert health["status"] == "degraded"
        assert health["consecutive_failures"] == expected
    ProviderStub("ok").install(monkeypatch)
    r = client.post("/tenants/bookkeeping-link/health-check")
    assert r.json()["health"]["status"] == "healthy"
    assert r.json()["health"]["consecutive_failures"] == 0


def test_health_check_network_failure_is_degraded_not_revoked(client, fake_db, monkeypatch):
    seed_settings(fake_db)
    ProviderStub(AccountingError("Bookkeeping API unreachable", kind="network")).install(monkeypatch)
    r = client.post("/tenants/bookkeeping-link/health-check")
    assert r.json()["health"]["status"] == "degraded"
    assert stored_settings(fake_db).get("credential_revoked_at") is None


def test_health_check_without_credential_is_400(client, fake_db):
    r = client.post("/tenants/bookkeeping-link/health-check")
    assert r.status_code == 400


def test_revoked_credential_marks_revoked_and_pauses_sync(client, fake_db, monkeypatch):
    seed_settings(fake_db)
    stub = ProviderStub(AccountingError("credential revoked", kind="revoked")).install(monkeypatch)
    r = client.post("/tenants/bookkeeping-link/health-check")
    assert r.json()["health"]["status"] == "revoked"
    s = stored_settings(fake_db)
    assert s["credential_revoked_at"] is not None
    status = client.get("/tenants/bookkeeping-link/sync-status").json()
    assert status["migration"]["reason"] == "credential_revoked"
    # Sync is paused BEFORE the provider is touched.
    seed_paid_invoice(fake_db)
    calls_before = len(stub.calls)
    res = run(bk_mod.sync_invoice_paid(fake_db, "t1", get_invoice(fake_db)))
    assert res == {"synced": False, "reason": "credential_revoked"}
    assert len(stub.calls) == calls_before


def test_health_check_is_owner_only(client, fake_db, monkeypatch):
    seed_settings(fake_db)
    ProviderStub("ok").install(monkeypatch)
    current_user.update({"role": "staff"})
    r = client.post("/tenants/bookkeeping-link/health-check")
    assert r.status_code == 403


# ---------- rotation ----------

def test_rotation_validates_then_swaps(client, fake_db, monkeypatch):
    seed_settings(fake_db, token="old-token")
    ProviderStub("ok").install(monkeypatch)
    r = client.post("/tenants/bookkeeping-link/rotate-credential",
                    json={"api_token": "new-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["credential_fingerprint"] == bk_mod.credential_fingerprint("new-token")
    assert body["credential_rotated_at"] is not None
    assert body["health"]["status"] == "healthy"
    assert "new-token" not in r.text
    enc = stored_settings(fake_db)["api_token_enc"]
    assert token_vault.decrypt_secret(enc) == "new-token"


def test_rotation_rejection_keeps_old_credential(client, fake_db, monkeypatch):
    seed_settings(fake_db, token="old-token")

    def picky(token, method, path, json):
        if token == "bad-new":
            raise AccountingError("Bookkeeping API rejected the token (401)", kind="auth")
        return _success_payload(method, path, json)

    ProviderStub(picky).install(monkeypatch)
    r = client.post("/tenants/bookkeeping-link/rotate-credential",
                    json={"api_token": "bad-new"})
    assert r.status_code == 400
    enc = stored_settings(fake_db)["api_token_enc"]
    assert token_vault.decrypt_secret(enc) == "old-token"  # untouched


def test_rotation_clears_revocation_pause(client, fake_db, monkeypatch):
    seed_settings(fake_db, token="dead-token",
                  credential_revoked_at="2026-10-17T00:00:00+00:00")
    ProviderStub("ok").install(monkeypatch)
    r = client.post("/tenants/bookkeeping-link/rotate-credential",
                    json={"api_token": "fresh-token"})
    assert r.status_code == 200
    assert stored_settings(fake_db)["credential_revoked_at"] is None


# ---------- disconnect ----------

def test_disconnect_wipes_secret_and_stops_sync(client, fake_db, monkeypatch):
    seed_settings(fake_db)
    ProviderStub("ok").install(monkeypatch)
    r = client.post("/tenants/bookkeeping-link/disconnect")
    assert r.status_code == 200
    body = r.json()
    assert body["api_token_set"] is False
    assert body["enabled"] is False
    s = stored_settings(fake_db)
    assert "api_token_enc" not in s
    assert s["disconnected_at"] is not None
    seed_paid_invoice(fake_db)
    res = run(bk_mod.sync_invoice_paid(fake_db, "t1", get_invoice(fake_db)))
    assert res["synced"] is False and res["reason"] == "disabled"


# ---------- sync failure recording / recovery ----------

def test_sync_failures_are_recorded_bounded_and_on_the_invoice(client, fake_db, monkeypatch):
    seed_settings(fake_db)
    ProviderStub(AccountingError("Bookkeeping API unreachable", kind="network")).install(monkeypatch)
    seed_paid_invoice(fake_db)
    for _ in range(12):
        res = run(bk_mod.sync_invoice_paid(fake_db, "t1", get_invoice(fake_db)))
        assert res["synced"] is False
    s = stored_settings(fake_db)
    assert len(s["sync_failures"]) == bk_mod.MAX_RECORDED_FAILURES  # bounded
    last = s["sync_failures"][-1]
    assert last["kind"] == "network" and last["invoice_id"] == "inv-1"
    assert s["health"]["status"] == "degraded"
    assert s["health"]["consecutive_failures"] == 12
    bk = get_invoice(fake_db)["bookkeeping_sync"]
    assert bk["attempts"] == 12 and bk["last_error"]


def test_sync_success_resets_health_and_failures(client, fake_db, monkeypatch):
    seed_settings(fake_db)
    seed_paid_invoice(fake_db)
    ProviderStub(AccountingError("Bookkeeping API unreachable", kind="network")).install(monkeypatch)
    run(bk_mod.sync_invoice_paid(fake_db, "t1", get_invoice(fake_db)))
    assert stored_settings(fake_db)["health"]["consecutive_failures"] == 1
    ProviderStub("ok").install(monkeypatch)
    res = run(bk_mod.sync_invoice_paid(fake_db, "t1", get_invoice(fake_db)))
    assert res["synced"] is True
    health = stored_settings(fake_db)["health"]
    assert health["status"] == "healthy" and health["consecutive_failures"] == 0


def test_legacy_jwt_tenant_sync_keeps_working(client, fake_db, monkeypatch):
    # Migration must never break a connected legacy tenant mid-flight.
    seed_settings(fake_db, token="aaa.bbb.ccc", kind="legacy_jwt")
    seed_paid_invoice(fake_db)
    ProviderStub("ok").install(monkeypatch)
    res = run(bk_mod.sync_invoice_paid(fake_db, "t1", get_invoice(fake_db)))
    assert res["synced"] is True
    assert res["steps"] == ["customer", "invoice", "payment"]


def test_sync_revocation_during_sync_pauses_future_syncs(client, fake_db, monkeypatch):
    seed_settings(fake_db)
    seed_paid_invoice(fake_db)
    stub = ProviderStub(AccountingError("credential revoked", kind="revoked")).install(monkeypatch)
    res = run(bk_mod.sync_invoice_paid(fake_db, "t1", get_invoice(fake_db)))
    assert res["synced"] is False
    s = stored_settings(fake_db)
    assert s["health"]["status"] == "revoked"
    assert s["credential_revoked_at"] is not None
    calls = len(stub.calls)
    res2 = run(bk_mod.sync_invoice_paid(fake_db, "t1", get_invoice(fake_db)))
    assert res2["reason"] == "credential_revoked"
    assert len(stub.calls) == calls  # dead credential is not retried


# ---------- cron sweep ----------

def test_sweep_health_checks_all_connected_tenants(fake_db, monkeypatch):
    monkeypatch.setattr(bk_mod, "get_db", lambda: fake_db)
    seed_settings(fake_db, tenant_id="t-good", token="good-token")
    seed_settings(fake_db, tenant_id="t-bad", token="bad-token")
    seed_settings(fake_db, tenant_id="t-off", token="off-token", enabled=False)

    def by_token(token, method, path, json):
        if token == "bad-token":
            raise AccountingError("Bookkeeping API unreachable", kind="network")
        return _success_payload(method, path, json)

    ProviderStub(by_token).install(monkeypatch)
    summary = run(bk_mod.sweep_bookkeeping_health(fake_db))
    assert summary == {"checked": 2, "healthy": 1}
    assert stored_settings(fake_db, "t-good")["health"]["status"] == "healthy"
    assert stored_settings(fake_db, "t-bad")["health"]["status"] == "degraded"
    assert stored_settings(fake_db, "t-off")["health"]["status"] == "unknown"


def test_cron_endpoint_requires_secret_and_acks(monkeypatch, fake_db):
    monkeypatch.setattr(cron_mod, "get_db", lambda: fake_db)
    monkeypatch.setenv("WEBHOOK_CRON_SECRET", "s3cr3t")
    app = FastAPI()
    app.include_router(cron_mod.router)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/cron/bookkeeping-health")
    assert r.status_code == 401
    r = c.post("/cron/bookkeeping-health",
               headers={"Authorization": "Bearer s3cr3t"})
    assert r.status_code == 200
    assert r.json() == {"accepted": True}
