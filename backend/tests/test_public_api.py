"""Unit tests for the public API + webhooks router (routers/developer.py).

Zero network, zero database: a small FastAPI app with only the developer +
/api/v1 routers, a fake in-memory Mongo, overridden JWT auth, and a mocked
HTTP transport for webhook delivery.

Coverage:
- Key issuance: raw key returned once; only SHA-256 hash stored server-side.
- Scope enforcement: read key can't write, write key can't read, 403s.
- Revoked key -> 401; bad/missing key -> 401; constant-time compare present.
- Tenant isolation across API keys.
- Webhook HMAC: known-vector unit test + test-mode signature verifies with
  the raw secret, with NO network performed.
- Delivery fan-out: captured via mocked transport; success + failure both
  recorded in the deliveries log.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "publicapi-test-secret")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "publicapi_test")

from fastapi import FastAPI, APIRouter  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.developer as dev_mod  # noqa: E402
from security import get_current_user, require_tenant_owner_or_admin  # noqa: E402


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
        return SimpleNamespace(inserted_id=doc.get("id"))

    async def update_one(self, query, update):
        matched = 0
        for d in self.docs:
            if _match(d, query):
                matched += 1
                for k, v in update.get("$set", {}).items():
                    d[k] = v
                break
        return SimpleNamespace(matched_count=matched, modified_count=matched)

    async def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if _match(d, query):
                del self.docs[i]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


class FakeDB:
    def __init__(self):
        self.api_keys = FakeCollection()
        self.webhook_subscriptions = FakeCollection()
        self.webhook_deliveries = FakeCollection()
        self.customers = FakeCollection()
        self.appointments = FakeCollection()
        self.invoices = FakeCollection()


# ---------- fixtures ----------

current_user = {}
_db = None


@pytest.fixture
def fake_db():
    global _db
    _db = FakeDB()
    return _db


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(dev_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    api = APIRouter(prefix="/api")
    api.include_router(dev_mod.router)
    app.include_router(api)
    # Mirrors the required integration: public API mounts at APP level,
    # not under the /api APIRouter (prefix is /api/v1 already).
    app.include_router(dev_mod.public_api_router)
    app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}-{role}", "tenant_id": tenant_id,
                         "role": role})


def make_key(client, tenant="a", scopes=None, name="test-key"):
    as_user(tenant, "owner")
    r = client.post("/api/developer/keys",
                    json={"name": name, "scopes": scopes or ["customers:read"]})
    assert r.status_code == 200, r.text
    return r.json()


def make_webhook(client, tenant="a", url="https://example.com/hook",
                 events=None):
    as_user(tenant, "owner")
    r = client.post("/api/developer/webhooks",
                    json={"url": url, "events": events or ["customer.created"]})
    assert r.status_code == 200, r.text
    return r.json()


def key_headers(key, bearer=True):
    if bearer:
        return {"Authorization": f"Bearer {key}"}
    return {"X-API-Key": key}


# ---------- API key issuance ----------

def test_key_issuance_returns_raw_once_and_stores_hash(client, fake_db):
    body = make_key(client, tenant="a", scopes=["customers:read", "jobs:read"])
    raw = body["key"]
    assert raw.startswith("mk_live_")

    # Only the hash is stored — the raw key is nowhere in the DB.
    docs = fake_db.api_keys.docs
    assert len(docs) == 1
    stored = docs[0]
    assert "key" not in stored
    assert "raw" not in " ".join(str(v) for v in stored.values()).lower()
    assert stored["key_hash"] == hashlib.sha256(raw.encode()).hexdigest()
    assert stored["key_hash"] != raw
    assert stored["revoked"] is False
    assert stored["scopes"] == ["customers:read", "jobs:read"]
    assert stored["tenant_id"] == "a"


def test_key_list_never_shows_raw(client, fake_db):
    body = make_key(client, tenant="a")
    raw = body["key"]
    r = client.get("/api/developer/keys")
    assert r.status_code == 200
    keys = r.json()
    assert len(keys) == 1
    assert "key" not in keys[0]
    assert raw not in str(keys)
    assert keys[0]["key_hint"].endswith(raw[-4:])
    assert keys[0]["name"] == "test-key"


def test_key_create_rejects_unknown_scope(client):
    as_user("a", "owner")
    r = client.post("/api/developer/keys",
                    json={"name": "bad", "scopes": ["admin:everything"]})
    assert r.status_code == 400
    assert "admin:everything" in r.text


def test_key_management_requires_owner_role(client):
    as_user("a", "member")  # real require_tenant_owner_or_admin runs
    r = client.post("/api/developer/keys",
                    json={"name": "x", "scopes": ["customers:read"]})
    assert r.status_code == 403
    r = client.get("/api/developer/keys")
    assert r.status_code == 403


# ---------- key auth: 401s and scopes ----------

def test_missing_key_401(client):
    r = client.get("/api/v1/customers")
    assert r.status_code == 401


def test_garbage_key_401(client):
    r = client.get("/api/v1/customers", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    r = client.get("/api/v1/customers", headers={"X-API-Key": "mk_live_wrongkey"})
    assert r.status_code == 401


def test_read_key_cannot_write_403(client):
    body = make_key(client, tenant="a", scopes=["customers:read"])
    key = body["key"]
    r = client.get("/api/v1/customers", headers=key_headers(key))
    assert r.status_code == 200
    r = client.post("/api/v1/customers", json={"name": "Nope"},
                    headers=key_headers(key))
    assert r.status_code == 403
    assert "customers:write" in r.text


def test_write_key_cannot_read_denied_scope_403(client):
    body = make_key(client, tenant="a", scopes=["customers:write"])
    key = body["key"]
    r = client.get("/api/v1/customers", headers=key_headers(key))
    assert r.status_code == 403
    assert "customers:read" in r.text


def test_invoice_key_cannot_touch_customers(client):
    body = make_key(client, tenant="a", scopes=["invoices:read"])
    key = body["key"]
    assert client.get("/api/v1/invoices", headers=key_headers(key)).status_code == 200
    assert client.get("/api/v1/customers", headers=key_headers(key)).status_code == 403
    assert client.get("/api/v1/jobs", headers=key_headers(key)).status_code == 403


def test_revoked_key_401(client, fake_db):
    body = make_key(client, tenant="a", scopes=["customers:read", "customers:write"])
    key = body["key"]
    kid = body["id"]
    assert client.get("/api/v1/customers", headers=key_headers(key)).status_code == 200
    as_user("a", "owner")
    r = client.post(f"/api/developer/keys/{kid}/revoke")
    assert r.status_code == 200
    assert r.json()["revoked"] is True
    # Revoked key is rejected everywhere, for reads AND writes.
    assert client.get("/api/v1/customers", headers=key_headers(key)).status_code == 401
    r = client.post("/api/v1/customers", json={"name": "Nope"},
                    headers=key_headers(key))
    assert r.status_code == 401


def test_revoke_unknown_key_404(client):
    as_user("a", "owner")
    r = client.post("/api/developer/keys/does-not-exist/revoke")
    assert r.status_code == 404


# ---------- tenant isolation ----------

def test_tenant_isolation_via_keys(client, fake_db):
    fake_db.customers.docs.append(
        {"id": "cus-b-1", "tenant_id": "b", "name": "Other Tenant's Customer"})
    body_a = make_key(client, tenant="a", scopes=["customers:read"])
    r = client.get("/api/v1/customers", headers=key_headers(body_a["key"]))
    assert r.status_code == 200
    assert r.json() == []  # tenant B's customer is invisible to tenant A
    r = client.get("/api/v1/customers/cus-b-1", headers=key_headers(body_a["key"]))
    assert r.status_code == 404

    body_b = make_key(client, tenant="b", scopes=["customers:read"])
    r = client.get("/api/v1/customers", headers=key_headers(body_b["key"]))
    assert [c["id"] for c in r.json()] == ["cus-b-1"]


def test_post_customer_and_job_roundtrip(client):
    body = make_key(client, tenant="a", scopes=["customers:read", "customers:write",
                                                "jobs:read", "jobs:write"])
    key = body["key"]
    r = client.post("/api/v1/customers", json={"name": "Acme Home",
                                               "phone": "555-0100"},
                    headers=key_headers(key, bearer=False))  # X-API-Key works too
    assert r.status_code == 200
    cus = r.json()
    assert cus["tenant_id"] == "a"
    assert cus["name"] == "Acme Home"

    r = client.post("/api/v1/jobs",
                    json={"customer_name": "Acme Home", "service_name": "AC tune-up",
                          "start_at": "2026-10-20T09:00:00Z",
                          "end_at": "2026-10-20T10:00:00Z"},
                    headers=key_headers(key))
    assert r.status_code == 200
    job = r.json()
    assert job["tenant_id"] == "a"
    r = client.get(f"/api/v1/jobs/{job['id']}", headers=key_headers(key))
    assert r.status_code == 200
    assert r.json()["service_name"] == "AC tune-up"


# ---------- HMAC signing ----------

def test_build_signature_known_vector():
    """Fixed secret/timestamp/body -> the exact expected digest."""
    secret = "whsec_unit_test_vector_123"
    ts = "2026-10-15T18:00:00+00:00"
    body = b'{"data":{"id":"job-1"},"event":"job.created"}'
    expected = "sha256=" + hmac.new(
        secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    assert dev_mod.build_signature(secret, ts, body) == expected
    assert dev_mod.verify_signature(secret, ts, body, expected) is True
    assert dev_mod.verify_signature(secret, ts, body, expected + "x") is False
    assert dev_mod.verify_signature(secret, "other-ts", body, expected) is False


def test_webhook_create_validation(client):
    as_user("a", "owner")
    r = client.post("/api/developer/webhooks",
                    json={"url": "https://example.com/h", "events": ["nope.created"]})
    assert r.status_code == 400
    r = client.post("/api/developer/webhooks",
                    json={"url": "ftp://example.com/h", "events": ["job.created"]})
    assert r.status_code == 400
    r = client.post("/api/developer/webhooks",
                    json={"url": "https://example.com/h", "events": []})
    assert r.status_code == 422  # pydantic min_length


def test_webhook_secret_stored_encrypted_and_shown_once(client, fake_db):
    wh = make_webhook(client, tenant="a", events=["customer.created"])
    raw_secret = wh["secret"]
    assert raw_secret.startswith("whsec_")
    stored = fake_db.webhook_subscriptions.docs[0]
    assert "secret" not in stored
    assert stored["secret_enc"] != raw_secret  # encrypted at rest

    as_user("a", "owner")
    r = client.get("/api/developer/webhooks")
    assert r.status_code == 200
    listed = r.json()[0]
    assert "secret" not in listed
    assert listed["secret_hint"].endswith(raw_secret[-4:])


def test_test_mode_signs_without_sending(client, fake_db, monkeypatch):
    """Test-mode returns the would-be payload + a verifiable signature,
    and performs zero network calls."""
    def _boom(url, body, headers):
        raise AssertionError("test mode must not touch the network")
    monkeypatch.setattr(dev_mod, "_post_webhook", _boom)

    wh = make_webhook(client, tenant="a", events=["job.created"])
    raw_secret = wh["secret"]
    as_user("a", "owner")
    r = client.post(f"/api/developer/webhooks/{wh['id']}/test")
    assert r.status_code == 200
    data = r.json()
    assert data["delivered"] is False
    assert data["event"] == "job.created"
    assert data["payload"]["event"] == "job.created"
    sig = data["headers"][dev_mod.SIGNATURE_HEADER]
    ts = data["headers"][dev_mod.TIMESTAMP_HEADER]
    assert sig.startswith("sha256=")
    # The signature verifies with the one-time raw secret and the canonical body.
    body = dev_mod._canonical_json(data["payload"])
    assert dev_mod.verify_signature(raw_secret, ts, body, sig) is True
    # Nothing was recorded as a real delivery.
    assert fake_db.webhook_deliveries.docs == []


def test_delivery_success_logged_and_signed(client, fake_db, monkeypatch):
    captured = []

    async def fake_post(url, body, headers):
        captured.append({"url": url, "body": body, "headers": headers})
        return 200, ""
    monkeypatch.setattr(dev_mod, "_post_webhook", fake_post)

    wh = make_webhook(client, tenant="a", events=["customer.created"],
                      url="https://example.com/hook")
    raw_secret = wh["secret"]
    body = make_key(client, tenant="a",
                    scopes=["customers:read", "customers:write"])
    key = body["key"]
    r = client.post("/api/v1/customers", json={"name": "Emit Me"},
                    headers=key_headers(key))
    assert r.status_code == 200

    assert len(captured) == 1
    call = captured[0]
    assert call["url"] == "https://example.com/hook"
    sig = call["headers"][dev_mod.SIGNATURE_HEADER]
    ts = call["headers"][dev_mod.TIMESTAMP_HEADER]
    assert dev_mod.verify_signature(raw_secret, ts, call["body"], sig) is True

    as_user("a", "owner")
    r = client.get(f"/api/developer/webhooks/{wh['id']}/deliveries")
    assert r.status_code == 200
    deliveries = r.json()
    assert len(deliveries) == 1
    d = deliveries[0]
    assert d["event"] == "customer.created"
    assert d["status"] == "delivered"
    assert d["attempts"] == 1
    assert d["status_code"] == 200


def test_delivery_failure_retries_and_logs(client, fake_db, monkeypatch):
    attempts = []

    async def fake_post(url, body, headers):
        attempts.append(url)
        raise ConnectionError("boom")
    monkeypatch.setattr(dev_mod, "_post_webhook", fake_post)

    wh = make_webhook(client, tenant="a", events=["job.created"],
                      url="https://example.com/hook")
    body = make_key(client, tenant="a", scopes=["jobs:write"])
    r = client.post("/api/v1/jobs",
                    json={"customer_name": "X", "service_name": "Y",
                          "start_at": "2026-10-20T09:00:00Z",
                          "end_at": "2026-10-20T10:00:00Z"},
                    headers=key_headers(body["key"]))
    assert r.status_code == 200

    assert len(attempts) == dev_mod.DELIVERY_MAX_ATTEMPTS  # retried
    as_user("a", "owner")
    deliveries = client.get(
        f"/api/developer/webhooks/{wh['id']}/deliveries").json()
    assert deliveries[0]["status"] == "failed"
    assert deliveries[0]["attempts"] == dev_mod.DELIVERY_MAX_ATTEMPTS
    assert "boom" in deliveries[0]["error"]


def test_non_matching_events_do_not_emit(client, fake_db, monkeypatch):
    async def fake_post(url, body, headers):
        raise AssertionError("no subscription matches this event")
    monkeypatch.setattr(dev_mod, "_post_webhook", fake_post)

    make_webhook(client, tenant="a", events=["invoice.paid"])
    body = make_key(client, tenant="a", scopes=["customers:write"])
    r = client.post("/api/v1/customers", json={"name": "Quiet"},
                    headers=key_headers(body["key"]))
    assert r.status_code == 200
    assert fake_db.webhook_deliveries.docs == []


def test_emit_helper_is_callable_for_other_routers(fake_db, monkeypatch):
    """The documented cross-router helper signs + delivers for job.completed
    and invoice.paid (other routers' TODO integration points)."""
    import asyncio
    monkeypatch.setattr(dev_mod, "get_db", lambda: fake_db)
    captured = []

    async def fake_post(url, body, headers):
        captured.append({"url": url, "body": body, "headers": headers})
        return 200, ""
    monkeypatch.setattr(dev_mod, "_post_webhook", fake_post)

    async def _run():
        as_user("a", "owner")
        secret_raw = dev_mod._new_signing_secret()
        fake_db.webhook_subscriptions.docs.append({
            "id": "sub-1", "tenant_id": "a",
            "url": "https://example.com/hook",
            "events": ["job.completed", "invoice.paid"],
            "secret_enc": dev_mod._encrypt_secret(secret_raw),
            "active": True,
        })
        d1 = await dev_mod.emit_webhook("a", "job.completed", {"id": "job-9"})
        d2 = await dev_mod.emit_webhook("a", "invoice.paid", {"id": "inv-9"})
        return secret_raw, d1, d2
    secret_raw, d1, d2 = asyncio.run(_run())

    assert len(captured) == 2
    assert [d["status"] for d in d1 + d2] == ["delivered", "delivered"]
    assert [d["event"] for d in d1 + d2] == ["job.completed", "invoice.paid"]
    for call in captured:
        sig = call["headers"][dev_mod.SIGNATURE_HEADER]
        ts = call["headers"][dev_mod.TIMESTAMP_HEADER]
        assert dev_mod.verify_signature(secret_raw, ts, call["body"], sig) is True


def test_emit_unknown_event_ignored(fake_db, monkeypatch):
    import asyncio
    monkeypatch.setattr(dev_mod, "get_db", lambda: fake_db)
    out = asyncio.run(dev_mod.emit_webhook("a", "spaceship.landed", {}))
    assert out == []


def test_deliveries_require_owner_and_own_webhook(client):
    wh = make_webhook(client, tenant="a")
    as_user("a", "member")
    r = client.get(f"/api/developer/webhooks/{wh['id']}/deliveries")
    assert r.status_code == 403
    as_user("b", "owner")
    r = client.get(f"/api/developer/webhooks/{wh['id']}/deliveries")
    assert r.status_code == 404  # tenant B can't see tenant A's webhook


def test_delete_webhook(client, fake_db):
    wh = make_webhook(client, tenant="a")
    as_user("a", "owner")
    r = client.delete(f"/api/developer/webhooks/{wh['id']}")
    assert r.status_code == 200
    assert fake_db.webhook_subscriptions.docs == []
    r = client.post(f"/api/developer/webhooks/{wh['id']}/test")
    assert r.status_code == 404


def test_public_api_rate_limit_headers_present_on_429(monkeypatch, client):
    """Force the shared limiter to deny once and confirm the 429 contract."""
    async def deny(key, limit, window_s):
        return False, 12.7
    monkeypatch.setattr(dev_mod, "check_rate_limit", deny)
    body = make_key(client, tenant="a", scopes=["customers:read"])
    r = client.get("/api/v1/customers", headers=key_headers(body["key"]))
    assert r.status_code == 429
    assert r.headers.get("Retry-After")
