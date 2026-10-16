"""Tests for the service-hub adapter (services/service_hub.py +
routers/service_hub.py + the hooks in conversation_intel/support_tickets).

The adapter syncs escalated conversations + human resolutions to the
central service hub's escalation archive (the learning-loop corpus).
Contract under test:
- OFF/demo by default: unconfigured -> sync is a no-op that stamps
  NOTHING (zero behavior change on live paths).
- Configured: POST {SERVICE_HUB_URL}/api/archive/escalations with a
  Bearer key + deterministic Idempotency-Key; payload follows schema
  "momo.service_escalation.v1"; success stamps hub_sync on the source doc.
- Failures are retried (bounded) and NEVER raise into the live path; the
  final failure is stamped hub_sync.status="error" for later retry via
  POST /api/service-hub/sync-pending.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "hub-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "hub_test")

import httpx  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.conversation_intel as ci_mod  # noqa: E402
import routers.service_hub as hub_router_mod  # noqa: E402
import services.service_hub as hub_mod  # noqa: E402
from security import require_tenant_user, require_tenant_owner_or_admin  # noqa: E402


# ---------- fake mongo ----------

class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        self.docs = sorted(self.docs, key=lambda d: str(d.get(key, "")), reverse=(direction < 0))
        return self

    async def to_list(self, n):
        return [dict(d) for d in self.docs[:n]]


class FakeCollection:
    def __init__(self):
        self.docs = []

    def find(self, query, proj=None):
        return FakeCursor([d for d in self.docs if all(d.get(k) == v for k, v in query.items())])

    async def find_one(self, query, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("id"))

    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                # Support dotted $set keys (none used by the hub paths, but
                # keep parity with the other suites' fakes).
                for k, v in update.get("$set", {}).items():
                    parts = k.split(".")
                    target = d
                    for p in parts[:-1]:
                        target = target.setdefault(p, {})
                    target[parts[-1]] = v
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)


class FakeDB:
    def __init__(self):
        self.escalations = FakeCollection()
        self.support_tickets = FakeCollection()
        self.inbox_threads = FakeCollection()
        self.inbox_messages = FakeCollection()
        self.conversations = FakeCollection()
        self.conv_messages = FakeCollection()
        self.sentiment_scores = FakeCollection()
        self.escalation_rules = FakeCollection()


# ---------- fake httpx transport ----------

class FakeResponse:
    def __init__(self, status_code=201, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"id": "arch-1"}
        self.text = "{}"

    def json(self):
        return self._payload


class FakeAsyncClient:
    calls = []
    responder = staticmethod(lambda attempt: FakeResponse())

    def __init__(self, timeout=None):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        FakeAsyncClient.calls.append({"url": url, "json": json, "headers": headers})
        result = FakeAsyncClient.responder(len(FakeAsyncClient.calls))
        if isinstance(result, Exception):
            raise result
        return result


current_user = {}


def _guard_owner(user):
    if user.get("role") not in {"owner", "admin"}:
        raise HTTPException(403, "Owner/admin role required")
    return user


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture(autouse=True)
def _reset_transport():
    FakeAsyncClient.calls = []
    FakeAsyncClient.responder = staticmethod(lambda attempt: FakeResponse())
    yield
    FakeAsyncClient.calls = []


@pytest.fixture
def client(monkeypatch, fake_db):
    for mod in (ci_mod, hub_router_mod):
        monkeypatch.setattr(mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(ci_mod.router)
    app.include_router(hub_router_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    app.dependency_overrides[require_tenant_owner_or_admin] = lambda: _guard_owner(current_user)
    return TestClient(app)


def as_user(tenant_id="t1", role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def configure_hub(monkeypatch):
    monkeypatch.setenv("SERVICE_HUB_ENABLED", "1")
    monkeypatch.setenv("SERVICE_HUB_URL", "https://hub.example.com/")
    monkeypatch.setenv("SERVICE_HUB_API_KEY", "test-key-123")
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(hub_mod, "BACKOFF_SECONDS", (0, 0))


def unconfigure_hub(monkeypatch):
    for var in ("SERVICE_HUB_ENABLED", "SERVICE_HUB_URL", "SERVICE_HUB_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def seed_escalation(fake_db, **over):
    esc = {
        "id": "esc-1", "tenant_id": "t1", "thread_id": "th-1",
        "rule_key": "churn_keyword", "rule_name": "Churn / cancellation language",
        "reason": "Churn language detected: refund", "severity": "critical",
        "status": "open", "customer_name": "Kim", "channel": "sms",
        "message_excerpt": "I want a refund",
        "transcript": [{"role": "customer", "text": "I want a refund",
                        "created_at": "2026-10-15T10:00:00+00:00"}],
        "resolution_note": "", "created_at": "2026-10-15T10:00:00+00:00",
        "updated_at": "2026-10-15T10:00:00+00:00",
        "resolved_at": None, "resolved_by": None,
    }
    esc.update(over)
    fake_db.escalations.docs.append(esc)
    return esc


# ---------- configuration ----------

def test_unconfigured_by_default(monkeypatch):
    unconfigure_hub(monkeypatch)
    s = hub_mod.hub_settings()
    assert s["configured"] is False
    assert s["enabled"] is False


def test_configured_requires_all_three_env_vars(monkeypatch):
    monkeypatch.setenv("SERVICE_HUB_ENABLED", "1")
    monkeypatch.setenv("SERVICE_HUB_URL", "https://hub.example.com")
    monkeypatch.delenv("SERVICE_HUB_API_KEY", raising=False)
    assert hub_mod.hub_settings()["configured"] is False
    monkeypatch.setenv("SERVICE_HUB_API_KEY", "k")
    assert hub_mod.hub_settings()["configured"] is True
    monkeypatch.setenv("SERVICE_HUB_ENABLED", "0")
    assert hub_mod.hub_settings()["configured"] is False


# ---------- sync engine ----------

@pytest.mark.asyncio
async def test_sync_unconfigured_is_a_silent_noop(monkeypatch, fake_db):
    unconfigure_hub(monkeypatch)
    esc = seed_escalation(fake_db)
    res = await hub_mod.sync_escalation(fake_db, "t1", esc)
    assert res == {"synced": False, "reason": "not_configured"}
    assert "hub_sync" not in fake_db.escalations.docs[0]  # zero behavior change
    assert FakeAsyncClient.calls == []


@pytest.mark.asyncio
async def test_sync_posts_archive_record_and_stamps(monkeypatch, fake_db):
    configure_hub(monkeypatch)
    esc = seed_escalation(fake_db)
    res = await hub_mod.sync_escalation(fake_db, "t1", esc)
    assert res["synced"] is True
    assert res["archive_id"] == "arch-1"
    assert len(FakeAsyncClient.calls) == 1
    call = FakeAsyncClient.calls[0]
    assert call["url"] == "https://hub.example.com/api/archive/escalations"
    assert call["headers"]["Authorization"] == "Bearer test-key-123"
    assert call["headers"]["Idempotency-Key"]
    body = call["json"]
    assert body["schema"] == "momo.service_escalation.v1"
    assert body["source"] == "momo"
    assert body["record_type"] == "escalation"
    assert body["source_id"] == "esc-1"
    assert body["tenant_id"] == "t1"
    assert body["status"] == "open"
    assert body["resolution"] is None
    assert body["transcript"][0]["text"] == "I want a refund"
    doc = fake_db.escalations.docs[0]
    assert doc["hub_sync"]["status"] == "synced"
    assert doc["hub_sync"]["for_status"] == "open"


@pytest.mark.asyncio
async def test_idempotency_key_is_deterministic(monkeypatch, fake_db):
    configure_hub(monkeypatch)
    esc = seed_escalation(fake_db)
    await hub_mod.sync_escalation(fake_db, "t1", esc)
    await hub_mod.sync_escalation(fake_db, "t1", esc)
    keys = [c["headers"]["Idempotency-Key"] for c in FakeAsyncClient.calls]
    assert keys[0] == keys[1]
    # A different lifecycle state is a different archive write.
    esc2 = seed_escalation(fake_db, id="esc-1", status="resolved",
                           resolution_note="Refunded in full.",
                           resolved_by="u-t1", resolved_at="2026-10-15T11:00:00+00:00")
    fake_db.escalations.docs = [esc2]
    await hub_mod.sync_escalation(fake_db, "t1", esc2)
    assert FakeAsyncClient.calls[-1]["headers"]["Idempotency-Key"] != keys[0]
    body = FakeAsyncClient.calls[-1]["json"]
    assert body["status"] == "resolved"
    assert body["resolution"]["note"] == "Refunded in full."


@pytest.mark.asyncio
async def test_sync_retries_then_succeeds(monkeypatch, fake_db):
    configure_hub(monkeypatch)
    attempts = [FakeResponse(500), FakeResponse(502), FakeResponse(201)]
    FakeAsyncClient.responder = staticmethod(lambda n: attempts[min(n - 1, 2)])
    esc = seed_escalation(fake_db)
    res = await hub_mod.sync_escalation(fake_db, "t1", esc)
    assert res["synced"] is True
    assert len(FakeAsyncClient.calls) == 3


@pytest.mark.asyncio
async def test_sync_failure_never_raises_and_stamps_error(monkeypatch, fake_db):
    configure_hub(monkeypatch)
    FakeAsyncClient.responder = staticmethod(lambda n: FakeResponse(500))
    esc = seed_escalation(fake_db)
    res = await hub_mod.sync_escalation(fake_db, "t1", esc)
    assert res["synced"] is False and res["reason"] == "error"
    assert fake_db.escalations.docs[0]["hub_sync"]["status"] == "error"
    # Even a transport-level exception is contained.
    FakeAsyncClient.calls = []
    FakeAsyncClient.responder = staticmethod(
        lambda n: httpx.ConnectError("down", request=None))
    res = await hub_mod.sync_escalation(fake_db, "t1", esc)
    assert res["synced"] is False


# ---------- router: status + sync-pending ----------

def test_status_reports_demo_mode_when_unconfigured(client, fake_db, monkeypatch):
    unconfigure_hub(monkeypatch)
    as_user()
    b = client.get("/api/service-hub/status").json()
    assert b["configured"] is False
    assert b["api_key_set"] is False
    assert "demo" in b["mode"]
    assert b["counts"]["escalations"]["total"] == 0


def test_sync_pending_noop_when_unconfigured(client, fake_db, monkeypatch):
    unconfigure_hub(monkeypatch)
    as_user()
    seed_escalation(fake_db)
    b = client.post("/api/service-hub/sync-pending").json()
    assert b["ok"] is True and b["configured"] is False
    assert FakeAsyncClient.calls == []


def test_sync_pending_pushes_backlog(client, fake_db, monkeypatch):
    configure_hub(monkeypatch)
    as_user()
    seed_escalation(fake_db)  # open, never synced
    seed_escalation(fake_db, id="esc-2", status="resolved",
                    resolution_note="Called and fixed it.", resolved_by="u-t1",
                    resolved_at="2026-10-15T12:00:00+00:00",
                    hub_sync={"status": "synced", "for_status": "open",
                              "archive_id": "arch-0", "at": "x", "error": None})
    fake_db.support_tickets.docs.append({
        "id": "tk-1", "tenant_id": "t1", "subject": "Billing", "description": "?",
        "priority": "medium", "status": "resolved", "contact_name": "Kim",
        "resolution_note": "Refunded.", "resolved_by": "u-t1",
        "resolved_at": "2026-10-15T12:30:00+00:00",
        "created_at": "2026-10-15T09:00:00+00:00", "updated_at": "2026-10-15T12:30:00+00:00",
    })
    b = client.post("/api/service-hub/sync-pending").json()
    assert b["escalations"] == {"attempted": 2, "synced": 2}
    assert b["tickets"] == {"attempted": 1, "synced": 1}
    assert len(FakeAsyncClient.calls) == 3
    assert b["counts"]["escalations"]["synced"] == 2
    # Second run: nothing left to do.
    FakeAsyncClient.calls = []
    b2 = client.post("/api/service-hub/sync-pending").json()
    assert b2["escalations"]["attempted"] == 0
    assert b2["tickets"]["attempted"] == 0


# ---------- live-path hooks ----------

def test_resolve_escalation_syncs_resolution_to_hub(client, fake_db, monkeypatch):
    configure_hub(monkeypatch)
    as_user()
    fake_db.inbox_threads.docs.append({
        "id": "th-1", "tenant_id": "t1", "customer_name": "Kim", "channel": "sms",
        "urgent": True, "created_at": "2026-10-15T10:00:00+00:00"})
    seed_escalation(fake_db)
    b = client.post("/api/conversation-intel/escalations/esc-1/resolve",
                    json={"resolution_note": "Refunded and apologized."}).json()
    assert b["ok"] is True
    assert len(FakeAsyncClient.calls) == 1
    body = FakeAsyncClient.calls[0]["json"]
    assert body["status"] == "resolved"
    assert body["resolution"]["note"] == "Refunded and apologized."
    assert body["resolution"]["resolved_by"] == "u-t1"
    doc = fake_db.escalations.docs[0]
    assert doc["resolution_note"] == "Refunded and apologized."
    assert doc["hub_sync"]["for_status"] == "resolved"


def test_resolve_escalation_unconfigured_stamps_nothing(client, fake_db, monkeypatch):
    unconfigure_hub(monkeypatch)
    as_user()
    fake_db.inbox_threads.docs.append({
        "id": "th-1", "tenant_id": "t1", "customer_name": "Kim", "channel": "sms",
        "urgent": True, "created_at": "2026-10-15T10:00:00+00:00"})
    seed_escalation(fake_db)
    b = client.post("/api/conversation-intel/escalations/esc-1/resolve",
                    json={"resolution_note": "Sorted."}).json()
    assert b["ok"] is True
    assert "hub_sync" not in fake_db.escalations.docs[0]
    assert FakeAsyncClient.calls == []
