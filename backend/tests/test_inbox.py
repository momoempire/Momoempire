"""Tests for routers/inbox.py (omnichannel inbox).

Uses a fake in-memory mongo (same pattern as test_reputation.py): no live
database. Covers thread listing/filters, send/assign/resolve/reopen,
template-based reply suggestions, SMS + call-transcript ingestion
(including reuse of the receptionist's conversations/conv_messages
collections), and tenant isolation.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "inbox-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "inbox_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.inbox as inbox_mod  # noqa: E402
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
                d.update(update.get("$set", {}))
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            doc = dict(query)
            doc.update(update.get("$set", {}))
            self.docs.append(doc)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=True)
        return SimpleNamespace(matched_count=0, modified_count=0)


class FakeDB:
    def __init__(self):
        self.inbox_threads = FakeCollection()
        self.inbox_messages = FakeCollection()
        self.conversations = FakeCollection()
        self.conv_messages = FakeCollection()
        self.tenants = FakeCollection()


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(inbox_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(inbox_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    app.dependency_overrides[require_tenant_owner_or_admin] = lambda: _guard_owner(current_user)
    return TestClient(app)


def _guard_owner(user):
    if user.get("role") not in {"owner", "admin"}:
        from fastapi import HTTPException
        raise HTTPException(403, "Owner/admin role required")
    return user


def as_user(tenant_id="t1", role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def _ingest_sms(client, phone="555-0100", body="Hi, my AC is broken", name="Kim"):
    return client.post("/api/inbox/ingest/sms",
                       json={"From": phone, "To": "555-9999", "Body": body,
                             "customer_name": name})


# ---------- thread listing / filters ----------

def test_ingest_sms_creates_thread(client, fake_db):
    as_user()
    r = _ingest_sms(client)
    assert r.status_code == 200
    b = r.json()
    assert b["ok"] and b["thread"]["channel"] == "sms"
    assert b["thread"]["status"] == "open"
    assert b["thread"]["customer_contact"] == "555-0100"
    assert b["message"]["direction"] == "in"
    assert len(fake_db.inbox_threads.docs) == 1


def test_ingest_sms_threads_same_customer(client, fake_db):
    as_user()
    _ingest_sms(client, body="first")
    _ingest_sms(client, body="second")
    assert len(fake_db.inbox_threads.docs) == 1
    assert len(fake_db.inbox_messages.docs) == 2
    b = client.get("/api/inbox/threads").json()
    assert b["count"] == 1
    assert b["threads"][0]["last_message_preview"] == "second"


def test_thread_listing_filters(client, fake_db):
    as_user()
    _ingest_sms(client, phone="555-0100")
    _ingest_sms(client, phone="555-0200")
    t1 = client.get("/api/inbox/threads").json()["threads"][0]["id"]
    client.post(f"/api/inbox/threads/{t1}/resolve")

    b = client.get("/api/inbox/threads").json()
    assert b["count"] == 2
    b = client.get("/api/inbox/threads?status=open").json()
    assert b["count"] == 1
    b = client.get("/api/inbox/threads?status=resolved").json()
    assert b["count"] == 1
    b = client.get("/api/inbox/threads?channel=sms").json()
    assert b["count"] == 2
    b = client.get("/api/inbox/threads?channel=voice").json()
    assert b["count"] == 0


def test_thread_filters_assignee(client, fake_db):
    as_user()
    _ingest_sms(client, phone="555-0100")
    _ingest_sms(client, phone="555-0200")
    t1 = client.get("/api/inbox/threads").json()["threads"][0]["id"]
    client.post(f"/api/inbox/threads/{t1}/assign", json={"assignee": "agent-7"})

    b = client.get("/api/inbox/threads?assignee=agent-7").json()
    assert b["count"] == 1 and b["threads"][0]["assignee"] == "agent-7"
    b = client.get("/api/inbox/threads?assignee=unassigned").json()
    assert b["count"] == 1 and b["threads"][0]["assignee"] is None


def test_get_thread_detail(client, fake_db):
    as_user()
    tid = _ingest_sms(client, body="hello there").json()["thread"]["id"]
    client.post(f"/api/inbox/threads/{tid}/messages", json={"body": "How can we help?"})
    b = client.get(f"/api/inbox/threads/{tid}").json()
    assert b["thread"]["id"] == tid
    assert b["count"] == 2
    assert b["messages"][0]["direction"] == "in"
    assert b["messages"][1]["direction"] == "out"
    assert b["messages"][1]["sender"] == "shop"


def test_get_thread_404(client):
    as_user()
    r = client.get("/api/inbox/threads/nope")
    assert r.status_code == 404


def test_tenant_isolation(client, fake_db):
    as_user(tenant_id="t1")
    _ingest_sms(client)
    as_user(tenant_id="t2")
    b = client.get("/api/inbox/threads").json()
    assert b["count"] == 0
    tid = fake_db.inbox_threads.docs[0]["id"]
    assert client.get(f"/api/inbox/threads/{tid}").status_code == 404
    assert client.post(f"/api/inbox/threads/{tid}/messages", json={"body": "hi"}).status_code == 404


# ---------- send / assign / lifecycle ----------

def test_send_message_marks_pending(client, fake_db):
    as_user()
    tid = _ingest_sms(client).json()["thread"]["id"]
    r = client.post(f"/api/inbox/threads/{tid}/messages", json={"body": "On it!"})
    assert r.status_code == 200
    assert fake_db.inbox_threads.docs[0]["status"] == "pending"
    # Inbound after pending re-opens
    _ingest_sms(client, body="thanks!")
    assert fake_db.inbox_threads.docs[0]["status"] == "open"


def test_send_empty_body_rejected(client, fake_db):
    as_user()
    tid = _ingest_sms(client).json()["thread"]["id"]
    r = client.post(f"/api/inbox/threads/{tid}/messages", json={"body": ""})
    assert r.status_code == 422


def test_assign_and_unassign(client, fake_db):
    as_user()
    tid = _ingest_sms(client).json()["thread"]["id"]
    r = client.post(f"/api/inbox/threads/{tid}/assign", json={"assignee": "queue:sales"})
    assert r.status_code == 200 and r.json()["assignee"] == "queue:sales"
    assert fake_db.inbox_threads.docs[0]["assignee"] == "queue:sales"
    r = client.post(f"/api/inbox/threads/{tid}/assign", json={"assignee": None})
    assert r.json()["assignee"] is None


def test_assign_non_owner_forbidden(client, fake_db):
    as_user()
    tid = _ingest_sms(client).json()["thread"]["id"]
    as_user(role="tech")
    r = client.post(f"/api/inbox/threads/{tid}/assign", json={"assignee": "agent-1"})
    assert r.status_code == 403


def test_resolve_reopen_cycle(client, fake_db):
    as_user()
    tid = _ingest_sms(client).json()["thread"]["id"]
    r = client.post(f"/api/inbox/threads/{tid}/resolve")
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    assert fake_db.inbox_threads.docs[0]["status"] == "resolved"
    r = client.post(f"/api/inbox/threads/{tid}/reopen")
    assert r.json()["status"] == "open"
    assert fake_db.inbox_threads.docs[0]["status"] == "open"


def test_resolve_404(client):
    as_user()
    assert client.post("/api/inbox/threads/nope/resolve").status_code == 404


# ---------- suggestions ----------

def test_suggestions_intent_chips(client, fake_db):
    as_user()
    tid = _ingest_sms(client, body="How much would it cost to fix the leak?").json()["thread"]["id"]
    b = client.get(f"/api/inbox/threads/{tid}/suggestions").json()
    assert b["draft_labeled"] is True
    kinds = {s["kind"] for s in b["suggestions"]}
    assert "acknowledge" in kinds and "pricing" in kinds
    assert all(s["generated_by"] == "template-suggestion-v1" for s in b["suggestions"])
    assert "Kim" in b["suggestions"][0]["draft"]


def test_suggestions_scheduling_and_apology(client, fake_db):
    as_user()
    tid = _ingest_sms(client, body="The tech did a terrible job, I want a refund").json()["thread"]["id"]
    kinds = {s["kind"] for s in client.get(f"/api/inbox/threads/{tid}/suggestions").json()["suggestions"]}
    assert "apology" in kinds
    tid2 = _ingest_sms(client, phone="555-0300", body="Can I book an appointment Tuesday?").json()["thread"]["id"]
    kinds2 = {s["kind"] for s in client.get(f"/api/inbox/threads/{tid2}/suggestions").json()["suggestions"]}
    assert "scheduling" in kinds2


def test_suggestions_uses_tenant_business_name(client, fake_db):
    as_user()
    fake_db.tenants.docs.append({"id": "t1", "name": "Ace Plumbing"})
    tid = _ingest_sms(client).json()["thread"]["id"]
    b = client.get(f"/api/inbox/threads/{tid}/suggestions").json()
    assert "Ace Plumbing" in b["suggestions"][0]["draft"]


def test_suggestions_404(client):
    as_user()
    assert client.get("/api/inbox/threads/nope/suggestions").status_code == 404


# ---------- call-transcript ingestion (receptionist reuse) ----------

def _seed_receptionist_call(fake_db, conv_id="call-1", tenant_id="t1"):
    fake_db.conversations.docs.append({
        "id": conv_id, "tenant_id": tenant_id, "channel": "call",
        "caller_name": "Dana", "caller_phone": "555-0400",
        "summary": "Customer wants a quote for a water heater install.",
        "created_at": "2026-10-15T10:00:00+00:00",
    })
    fake_db.conv_messages.docs.extend([
        {"id": "m1", "conversation_id": conv_id, "role": "assistant",
         "content": "Thanks for calling! How can I help?", "created_at": "2026-10-15T10:00:01+00:00"},
        {"id": "m2", "conversation_id": conv_id, "role": "user",
         "content": "I need a water heater replaced", "created_at": "2026-10-15T10:00:10+00:00"},
    ])


def test_ingest_call_transcript_creates_voice_thread(client, fake_db):
    as_user()
    _seed_receptionist_call(fake_db)
    r = client.post("/api/inbox/ingest/call-transcript", json={"conversation_id": "call-1"})
    assert r.status_code == 200
    b = r.json()
    assert b["ok"] and b["ingested"] is True
    assert b["thread"]["channel"] == "voice"
    assert b["thread"]["customer_name"] == "Dana"
    assert b["thread"]["status"] == "open"
    # Summary + transcript preserved in the inbox message
    assert "water heater install" in b["message"]["body"]
    assert "Caller: I need a water heater replaced" in b["message"]["body"]


def test_ingest_call_transcript_idempotent(client, fake_db):
    as_user()
    _seed_receptionist_call(fake_db)
    client.post("/api/inbox/ingest/call-transcript", json={"conversation_id": "call-1"})
    r = client.post("/api/inbox/ingest/call-transcript", json={"conversation_id": "call-1"})
    assert r.json()["ingested"] is False
    assert len(fake_db.inbox_threads.docs) == 1
    assert len(fake_db.inbox_messages.docs) == 1


def test_ingest_call_transcript_404(client, fake_db):
    as_user()
    r = client.post("/api/inbox/ingest/call-transcript", json={"conversation_id": "missing"})
    assert r.status_code == 404


def test_ingest_call_transcript_tenant_scoped(client, fake_db):
    as_user(tenant_id="t1")
    _seed_receptionist_call(fake_db, conv_id="call-9", tenant_id="t1")
    as_user(tenant_id="t2")
    r = client.post("/api/inbox/ingest/call-transcript", json={"conversation_id": "call-9"})
    assert r.status_code == 404


def test_voice_thread_appears_in_listing(client, fake_db):
    as_user()
    _seed_receptionist_call(fake_db)
    client.post("/api/inbox/ingest/call-transcript", json={"conversation_id": "call-1"})
    b = client.get("/api/inbox/threads?channel=voice").json()
    assert b["count"] == 1 and b["threads"][0]["customer_name"] == "Dana"
