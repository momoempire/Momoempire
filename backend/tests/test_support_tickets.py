"""Tests for routers/support_tickets.py (helpdesk + ticketing with SLAs).

Uses a fake in-memory mongo (same pattern as test_reputation.py): no live
database. Covers ticket creation/listing/detail, lifecycle transitions
(invalid jumps rejected), public vs internal replies, resolve/reopen,
first-response and resolution SLA breach detection with crafted timestamps,
the SLA pause while waiting_on_customer, SLA policy seeding/tuning, and
tenant isolation.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "support-tickets-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "support_tickets_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.support_tickets as st_mod  # noqa: E402
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
        self.support_tickets = FakeCollection()
        self.ticket_events = FakeCollection()
        self.ticket_replies = FakeCollection()
        self.sla_policies = FakeCollection()


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(st_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(st_mod.router)
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


def _ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


def _make_ticket(client, subject="Login broken", priority="medium", **kw):
    payload = {"subject": subject, "description": "Can't log in", "priority": priority,
               "category": "bug"}
    payload.update(kw)
    r = client.post("/api/support-tickets/", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def _seed_ticket(fake_db, tenant_id="t1", ticket_id="seed-1", priority="high",
                 status="in_progress", created_hours_ago=2, waiting_seconds=0.0,
                 reply_at_hours_ago=None, reply_role="staff"):
    """Insert a ticket (and optional staff public reply) with crafted timestamps."""
    created = _ago(hours=created_hours_ago)
    fake_db.support_tickets.docs.append({
        "id": ticket_id,
        "tenant_id": tenant_id,
        "subject": "Seeded",
        "description": "",
        "priority": priority,
        "category": "",
        "status": status,
        "assignee": None,
        "created_by": "u",
        "created_at": created,
        "updated_at": created,
        "status_changed_at": created,
        "waiting_seconds": waiting_seconds,
        "resolved_at": None,
    })
    if reply_at_hours_ago is not None:
        fake_db.ticket_replies.docs.append({
            "id": f"reply-{ticket_id}",
            "tenant_id": tenant_id,
            "ticket_id": ticket_id,
            "body": "Looking into it",
            "kind": "public",
            "author_role": reply_role,
            "author_id": "staff-1",
            "created_at": _ago(hours=reply_at_hours_ago),
        })


# ---------- create / list / get ----------

def test_create_ticket(client, fake_db):
    as_user()
    b = _make_ticket(client)
    assert b["ok"] is True
    assert b["status"] == "open"
    assert b["priority"] == "medium"
    assert len(fake_db.support_tickets.docs) == 1
    # audit trail gets a "created" event
    kinds = [e["kind"] for e in fake_db.ticket_events.docs]
    assert "created" in kinds


def test_create_ticket_missing_subject_422(client):
    as_user()
    r = client.post("/api/support-tickets/", json={"description": "x"})
    assert r.status_code == 422


def test_create_ticket_defaults_priority(client):
    as_user()
    b = _make_ticket(client, subject="No prio given", priority="medium")
    assert b["priority"] == "medium"


def test_list_tickets_and_filters(client, fake_db):
    as_user()
    t1 = _make_ticket(client, subject="A", priority="urgent")
    _make_ticket(client, subject="B", priority="low")
    client.patch(f"/api/support-tickets/{t1['id']}", json={"status": "in_progress"})

    b = client.get("/api/support-tickets/").json()
    assert b["count"] == 2

    b = client.get("/api/support-tickets/", params={"status": "in_progress"}).json()
    assert b["count"] == 1 and b["tickets"][0]["id"] == t1["id"]

    b = client.get("/api/support-tickets/", params={"priority": "low"}).json()
    assert b["count"] == 1 and b["tickets"][0]["subject"] == "B"

    b = client.get("/api/support-tickets/", params={"priority": "urgent", "status": "open"}).json()
    assert b["count"] == 0


def test_list_includes_sla_snapshot(client):
    as_user()
    t = _make_ticket(client, priority="high")
    b = client.get("/api/support-tickets/").json()
    ticket = [x for x in b["tickets"] if x["id"] == t["id"]][0]
    assert ticket["sla"]["overall"] == "on_track"
    assert ticket["sla"]["first_response"]["target_minutes"] == 60
    assert ticket["sla"]["resolution"]["target_minutes"] == 24 * 60


def test_get_ticket_detail_includes_thread_and_events(client, fake_db):
    as_user()
    t = _make_ticket(client)
    client.post(f"/api/support-tickets/{t['id']}/replies",
                json={"body": "Ack", "kind": "public", "author_role": "staff"})
    b = client.get(f"/api/support-tickets/{t['id']}").json()
    assert b["ticket"]["id"] == t["id"]
    assert b["reply_count"] == 1
    assert b["internal_note_count"] == 0
    kinds = {e["kind"] for e in b["events"]}
    assert {"created", "reply_added"} <= kinds
    assert b["sla"]["first_response"]["responded"] is True
    assert b["sla"]["first_response"]["status"] == "met"


def test_get_ticket_missing_404(client):
    as_user()
    assert client.get("/api/support-tickets/nope").status_code == 404


# ---------- status transitions ----------

def test_patch_valid_transition(client, fake_db):
    as_user()
    t = _make_ticket(client)
    b = client.patch(f"/api/support-tickets/{t['id']}", json={"status": "in_progress"}).json()
    assert b["status"] == "in_progress"
    assert fake_db.support_tickets.docs[0]["status"] == "in_progress"
    kinds = [e["kind"] for e in fake_db.ticket_events.docs]
    assert "status_changed" in kinds


def test_patch_invalid_transition_409(client):
    as_user()
    t = _make_ticket(client)
    client.patch(f"/api/support-tickets/{t['id']}", json={"status": "in_progress"})
    client.post(f"/api/support-tickets/{t['id']}/resolve", json={})
    # resolved -> in_progress via PATCH is forbidden; use /reopen
    r = client.patch(f"/api/support-tickets/{t['id']}", json={"status": "in_progress"})
    assert r.status_code == 409


def test_patch_priority_and_assignee(client, fake_db):
    as_user()
    t = _make_ticket(client, priority="low")
    b = client.patch(f"/api/support-tickets/{t['id']}",
                     json={"priority": "urgent", "assignee": "agent-7"}).json()
    assert b["priority"] == "urgent"
    assert b["assignee"] == "agent-7"
    kinds = [e["kind"] for e in fake_db.ticket_events.docs]
    assert "priority_changed" in kinds and "assignee_changed" in kinds

    # clearing the assignee with an explicit null is allowed
    b = client.patch(f"/api/support-tickets/{t['id']}", json={"assignee": None}).json()
    assert b["assignee"] is None


def test_patch_non_owner_forbidden(client):
    as_user()
    t = _make_ticket(client)
    as_user(role="tech")
    r = client.patch(f"/api/support-tickets/{t['id']}", json={"status": "in_progress"})
    assert r.status_code == 403


# ---------- replies: public vs internal ----------

def test_public_customer_reply(client, fake_db):
    as_user()
    t = _make_ticket(client)
    b = client.post(f"/api/support-tickets/{t['id']}/replies",
                    json={"body": "Still broken", "kind": "public"}).json()
    assert b["ok"] is True
    assert b["kind"] == "public"
    assert b["author_role"] == "customer"


def test_internal_note_by_staff(client, fake_db):
    as_user()
    t = _make_ticket(client)
    b = client.post(f"/api/support-tickets/{t['id']}/replies",
                    json={"body": "Escalate to eng", "kind": "internal",
                          "author_role": "staff"}).json()
    assert b["kind"] == "internal"
    detail = client.get(f"/api/support-tickets/{t['id']}").json()
    assert detail["internal_note_count"] == 1
    # internal notes never satisfy the first-response SLA
    assert detail["sla"]["first_response"]["responded"] is False


def test_customer_internal_note_forbidden(client):
    as_user()
    t = _make_ticket(client)
    r = client.post(f"/api/support-tickets/{t['id']}/replies",
                    json={"body": "sneaky", "kind": "internal", "author_role": "customer"})
    assert r.status_code == 403


def test_public_reply_flips_waiting_to_in_progress(client, fake_db):
    as_user()
    t = _make_ticket(client)
    client.patch(f"/api/support-tickets/{t['id']}", json={"status": "in_progress"})
    client.patch(f"/api/support-tickets/{t['id']}", json={"status": "waiting_on_customer"})
    b = client.post(f"/api/support-tickets/{t['id']}/replies",
                    json={"body": "Here are the logs", "kind": "public"}).json()
    assert b["auto_status"] == "in_progress"
    assert fake_db.support_tickets.docs[0]["status"] == "in_progress"


def test_customer_reply_reopens_resolved_ticket(client, fake_db):
    as_user()
    t = _make_ticket(client)
    client.post(f"/api/support-tickets/{t['id']}/resolve",
                json={"resolution_note": "Fixed"})
    b = client.post(f"/api/support-tickets/{t['id']}/replies",
                    json={"body": "Still broken actually", "kind": "public"}).json()
    assert b["auto_status"] == "in_progress"
    assert fake_db.support_tickets.docs[0]["status"] == "in_progress"
    kinds = [e["kind"] for e in fake_db.ticket_events.docs]
    assert "reopened" in kinds


def test_reply_on_closed_ticket_409(client):
    as_user()
    t = _make_ticket(client)
    client.patch(f"/api/support-tickets/{t['id']}", json={"status": "closed"})
    r = client.post(f"/api/support-tickets/{t['id']}/replies",
                    json={"body": "hello", "kind": "public"})
    assert r.status_code == 409


# ---------- resolve / reopen ----------

def test_resolve_with_note_posts_public_reply(client, fake_db):
    as_user()
    t = _make_ticket(client)
    b = client.post(f"/api/support-tickets/{t['id']}/resolve",
                    json={"resolution_note": "Reset your password"}).json()
    assert b["status"] == "resolved"
    doc = fake_db.support_tickets.docs[0]
    assert doc["resolved_at"] is not None
    replies = fake_db.ticket_replies.docs
    assert len(replies) == 1
    assert replies[0]["kind"] == "public" and replies[0]["author_role"] == "staff"
    assert replies[0]["body"] == "Reset your password"


def test_resolve_already_resolved_409(client):
    as_user()
    t = _make_ticket(client)
    client.post(f"/api/support-tickets/{t['id']}/resolve", json={})
    r = client.post(f"/api/support-tickets/{t['id']}/resolve", json={})
    assert r.status_code == 409


def test_resolve_non_owner_forbidden(client):
    as_user()
    t = _make_ticket(client)
    as_user(role="tech")
    r = client.post(f"/api/support-tickets/{t['id']}/resolve", json={})
    assert r.status_code == 403


def test_reopen_resolved_ticket(client, fake_db):
    as_user()
    t = _make_ticket(client)
    client.post(f"/api/support-tickets/{t['id']}/resolve", json={})
    b = client.post(f"/api/support-tickets/{t['id']}/reopen",
                    json={"reason": "regression"}).json()
    assert b["status"] == "in_progress"
    assert fake_db.support_tickets.docs[0]["resolved_at"] is None
    kinds = [e["kind"] for e in fake_db.ticket_events.docs]
    assert "reopened" in kinds


def test_reopen_closed_ticket(client):
    as_user()
    t = _make_ticket(client)
    client.patch(f"/api/support-tickets/{t['id']}", json={"status": "closed"})
    b = client.post(f"/api/support-tickets/{t['id']}/reopen", json={}).json()
    assert b["status"] == "in_progress"


def test_reopen_open_ticket_409(client):
    as_user()
    t = _make_ticket(client)
    r = client.post(f"/api/support-tickets/{t['id']}/reopen", json={})
    assert r.status_code == 409


# ---------- SLA breach detection (crafted timestamps) ----------

def test_sla_overview_breached_first_response(client, fake_db):
    as_user()
    # high: 1-h first response. 2h old with no staff reply -> breached.
    _seed_ticket(fake_db, ticket_id="br-1", priority="high", created_hours_ago=2)
    b = client.get("/api/support-tickets/sla/overview").json()
    assert b["counts"]["breached"] == 1
    assert b["counts"]["at_risk"] == 0
    assert b["breaches_by_priority"]["high"] == 1
    assert b["breached"][0]["id"] == "br-1"
    assert b["breached"][0]["sla"]["first_response"]["status"] == "breached"


def test_sla_overview_at_risk(client, fake_db):
    as_user()
    # urgent: 15-min first response; 13 min old -> past 80% (12 min), not yet 15 -> at-risk.
    _seed_ticket(fake_db, ticket_id="ar-1", priority="urgent",
                 created_hours_ago=13 / 60)
    b = client.get("/api/support-tickets/sla/overview").json()
    assert b["counts"]["at_risk"] == 1
    assert b["counts"]["breached"] == 0
    assert b["at_risk"][0]["id"] == "ar-1"


def test_sla_first_response_met_within_target(client, fake_db):
    as_user()
    # urgent 15-min target; staff replied 10 min after creation -> met.
    _seed_ticket(fake_db, ticket_id="fr-ok", priority="urgent",
                 created_hours_ago=1, reply_at_hours_ago=50 / 60)
    b = client.get("/api/support-tickets/sla/overview").json()
    assert b["counts"]["on_track"] == 1
    # resolution still on-track (1h < 4h target), nothing breached
    assert b["counts"]["breached"] == 0


def test_sla_resolution_breach_without_pause(client, fake_db):
    as_user()
    # urgent: 4-h resolution. 5h old, staff replied in time, no waiting -> breached.
    _seed_ticket(fake_db, ticket_id="res-br", priority="urgent",
                 created_hours_ago=5, reply_at_hours_ago=4.9)
    b = client.get("/api/support-tickets/sla/overview").json()
    entry = b["breached"][0]
    assert entry["id"] == "res-br"
    assert entry["sla"]["resolution"]["status"] == "breached"
    assert entry["sla"]["first_response"]["status"] == "met"


def test_sla_pause_while_waiting_on_customer(client, fake_db):
    as_user()
    # Two urgent tickets, both 5h old, both answered in time.
    # One accumulated 4.5h of waiting_on_customer time: resolution clock is
    # paused, so 0.5h active < 4h target -> on-track. The other is breached.
    _seed_ticket(fake_db, ticket_id="pause-ok", priority="urgent",
                 created_hours_ago=5, reply_at_hours_ago=4.9,
                 waiting_seconds=4.5 * 3600)
    _seed_ticket(fake_db, ticket_id="pause-no", priority="urgent",
                 created_hours_ago=5, reply_at_hours_ago=4.9,
                 waiting_seconds=0.0)
    b = client.get("/api/support-tickets/sla/overview").json()
    by_id = {e["id"]: e for e in b["breached"]}
    assert "pause-no" in by_id
    assert "pause-ok" not in by_id
    assert b["counts"]["on_track"] == 1
    assert b["counts"]["breached"] == 1
    # the waiting time is surfaced and excluded from the resolution clock
    detail = client.get("/api/support-tickets/pause-ok").json()
    assert detail["sla"]["resolution"]["waiting_seconds"] == pytest.approx(4.5 * 3600)
    assert detail["sla"]["resolution"]["elapsed_minutes"] == pytest.approx(30, abs=2)
    assert detail["sla"]["overall"] == "on_track"


def test_sla_current_waiting_segment_excluded(client, fake_db):
    as_user()
    # Ticket created 5h ago, entered waiting_on_customer 4.5h ago and is STILL
    # waiting: the in-progress waiting segment must also be excluded.
    created = _ago(hours=5)
    fake_db.support_tickets.docs.append({
        "id": "wait-now", "tenant_id": "t1", "subject": "x", "description": "",
        "priority": "urgent", "category": "", "status": "waiting_on_customer",
        "assignee": None, "created_by": "u", "created_at": created,
        "updated_at": created, "status_changed_at": _ago(hours=4.5),
        "waiting_seconds": 0.0, "resolved_at": None,
    })
    fake_db.ticket_replies.docs.append({
        "id": "reply-wait-now", "tenant_id": "t1", "ticket_id": "wait-now",
        "body": "ack", "kind": "public", "author_role": "staff",
        "author_id": "s", "created_at": _ago(hours=4.9),
    })
    detail = client.get("/api/support-tickets/wait-now").json()
    assert detail["sla"]["resolution"]["elapsed_minutes"] == pytest.approx(30, abs=2)
    assert detail["sla"]["overall"] == "on_track"


# ---------- SLA policy seeding / tuning ----------

def test_sla_policies_seeded_on_first_read(client, fake_db):
    as_user()
    b = client.get("/api/support-tickets/sla/policies").json()
    assert b["targets"]["urgent"] == {"first_response": 15, "resolution": 240}
    assert b["targets"]["low"] == {"first_response": 1440, "resolution": 7200}
    assert len(fake_db.sla_policies.docs) == 1
    # second read does not re-seed
    client.get("/api/support-tickets/sla/policies")
    assert len(fake_db.sla_policies.docs) == 1


def test_sla_policies_tuned(client, fake_db):
    as_user()
    client.patch("/api/support-tickets/sla/policies",
                 json={"urgent": {"first_response": 5}})
    b = client.get("/api/support-tickets/sla/policies").json()
    assert b["targets"]["urgent"]["first_response"] == 5
    assert b["targets"]["urgent"]["resolution"] == 240  # untouched
    # custom target is honored in the snapshot
    _seed_ticket(fake_db, ticket_id="tuned", priority="urgent", created_hours_ago=6 / 60)
    detail = client.get("/api/support-tickets/tuned").json()
    assert detail["sla"]["first_response"]["target_minutes"] == 5
    assert detail["sla"]["overall"] == "breached"


def test_sla_policies_reject_nonpositive(client):
    as_user()
    r = client.patch("/api/support-tickets/sla/policies",
                     json={"high": {"resolution": 0}})
    assert r.status_code == 400


# ---------- tenant isolation ----------

def test_tenant_isolation(client, fake_db):
    as_user(tenant_id="t1")
    t = _make_ticket(client, subject="t1 secret")
    as_user(tenant_id="t2")
    b = client.get("/api/support-tickets/").json()
    assert b["count"] == 0
    assert client.get(f"/api/support-tickets/{t['id']}").status_code == 404
    assert client.patch(f"/api/support-tickets/{t['id']}",
                        json={"status": "in_progress"}).status_code == 404
    assert client.post(f"/api/support-tickets/{t['id']}/replies",
                       json={"body": "hi"}).status_code == 404
    assert client.post(f"/api/support-tickets/{t['id']}/resolve", json={}).status_code == 404
    b = client.get("/api/support-tickets/sla/overview").json()
    assert b["open_total"] == 0
    # policies are per-tenant too
    b = client.get("/api/support-tickets/sla/policies").json()
    assert b["targets"]["urgent"]["first_response"] == 15


def test_list_filter_sla_status(client, fake_db):
    as_user()
    _seed_ticket(fake_db, ticket_id="f-br", priority="high", created_hours_ago=2)
    _seed_ticket(fake_db, ticket_id="f-ok", priority="low", created_hours_ago=1)
    b = client.get("/api/support-tickets/", params={"sla_status": "breached"}).json()
    assert b["count"] == 1 and b["tickets"][0]["id"] == "f-br"
    b = client.get("/api/support-tickets/", params={"sla_status": "on_track"}).json()
    assert b["count"] == 1 and b["tickets"][0]["id"] == "f-ok"
