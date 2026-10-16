"""Tests for self-healing operations (routers/self_healing.py).

All tests run against a fake in-memory Mongo: no live DB required.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "self-healing-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "self_healing_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.self_healing as sh  # noqa: E402
from security import require_tenant_user, require_tenant_owner_or_admin  # noqa: E402


# ---------- fake mongo ----------

def _match(doc, query):
    for key, expected in query.items():
        value = doc.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if value not in expected["$in"]:
                return False
        elif value != expected:
            return False
    return True


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
        return FakeCursor([d for d in self.docs if _match(d, query)])

    async def find_one(self, query, proj=None):
        for d in self.docs:
            if _match(d, query):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("id"))

    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if _match(d, query):
                d.update(update.get("$set", {}))
                for k in (update.get("$unset") or {}):
                    d.pop(k, None)
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            doc = {k: v for k, v in query.items() if not isinstance(v, dict)}
            doc.update(update.get("$set", {}))
            self.docs.append(doc)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=True)
        return SimpleNamespace(matched_count=0, modified_count=0)


class FakeDB:
    def __init__(self):
        self.appointments = FakeCollection()
        self.leads = FakeCollection()
        self.invoices = FakeCollection()
        self.ops_issues = FakeCollection()
        self.healer_runs = FakeCollection()
        self.healer_settings = FakeCollection()
        self.collection_enrollments = FakeCollection()


current_user = {}


def as_user(tenant_id="t1", role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(sh, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(sh.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    app.dependency_overrides[require_tenant_owner_or_admin] = lambda: _owner_only()
    return TestClient(app)


def _owner_only():
    if current_user.get("role") not in {"owner", "admin"}:
        from fastapi import HTTPException

        raise HTTPException(403, "Owner/admin role required")
    return current_user


def _tomorrow_iso(hour=9):
    return (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    ).isoformat()


def _seed_double_booking(db):
    day = _tomorrow_iso(9)
    day2 = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=10, minute=0, second=0, microsecond=0
    ).isoformat()
    db.appointments.docs.extend([
        {"id": "a1", "tenant_id": "t1", "tech_id": "tech-1", "status": "scheduled",
         "date": day[:10], "start_at": day, "end_at": day2, "customer_name": "Acme"},
        {"id": "a2", "tenant_id": "t1", "tech_id": "tech-1", "status": "confirmed",
         "date": day[:10], "start_at": day, "end_at": day2, "customer_name": "Beta"},
        {"id": "a3", "tenant_id": "t1", "tech_id": "tech-2", "status": "scheduled",
         "date": day[:10],
         "start_at": (datetime.now(timezone.utc) + timedelta(days=1)).replace(
             hour=14, minute=0, second=0, microsecond=0).isoformat(),
         "end_at": (datetime.now(timezone.utc) + timedelta(days=1)).replace(
             hour=15, minute=0, second=0, microsecond=0).isoformat(),
         "customer_name": "Gamma"},
    ])


# ---------- healer listing / toggling ----------

def test_healers_list_defaults_enabled(client):
    as_user("t1")
    r = client.get("/tenants/self-healing/healers")
    assert r.status_code == 200
    healers = r.json()["healers"]
    assert {h["id"] for h in healers} == set(sh.HEALER_DEFS)
    assert all(h["enabled"] for h in healers)
    assert all("name" in h and "description" in h for h in healers)


def test_toggle_disable_and_scan_skips(client, fake_db):
    as_user("t1")
    r = client.post("/tenants/self-healing/healers/stale_lead/toggle", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    r = client.get("/tenants/self-healing/healers")
    stale = next(h for h in r.json()["healers"] if h["id"] == "stale_lead")
    assert stale["enabled"] is False


def test_toggle_unknown_healer_404(client):
    as_user("t1")
    r = client.post("/tenants/self-healing/healers/nope/toggle", json={"enabled": False})
    assert r.status_code == 404


def test_toggle_forbidden_for_dispatcher(client):
    as_user("t1", role="dispatcher")
    r = client.post("/tenants/self-healing/healers/stale_lead/toggle", json={"enabled": False})
    assert r.status_code == 403


# ---------- double booking ----------

def test_double_booking_detected_and_flagged(client, fake_db):
    as_user("t1")
    _seed_double_booking(fake_db)
    r = client.post("/tenants/self-healing/scan")
    assert r.status_code == 200
    body = r.json()
    assert body["detected"] >= 1
    dbl = [i for i in body["issues"] if i["healer_id"] == "double_booking"]
    assert len(dbl) == 1
    assert dbl[0]["status"] == "auto_fixed"
    assert "suggested" in dbl[0]["fix_detail"].lower()
    # auto-fix applied: later appointment flagged for reassignment
    a2 = fake_db.appointments.docs[1]
    assert a2["needs_reassign"] is True
    assert a2["reassign_suggested_to"] == "tech-2"


def test_double_booking_no_overlap_no_issue(client, fake_db):
    as_user("t1")
    day = _tomorrow_iso(9)
    fake_db.appointments.docs.append(
        {"id": "a1", "tenant_id": "t1", "tech_id": "tech-1", "status": "scheduled",
         "date": day[:10], "start_at": day})
    r = client.post("/tenants/self-healing/scan")
    assert r.status_code == 200
    assert [i for i in r.json()["issues"] if i["healer_id"] == "double_booking"] == []


# ---------- unconfirmed appointments ----------

def test_unconfirmed_tomorrow_nudged(client, fake_db):
    as_user("t1")
    day = _tomorrow_iso(14)
    fake_db.appointments.docs.append(
        {"id": "u1", "tenant_id": "t1", "tech_id": "tech-9", "status": "scheduled",
         "date": day[:10], "start_at": day, "customer_name": "Nudge Me"})
    r = client.post("/tenants/self-healing/scan")
    assert r.status_code == 200
    nudge = [i for i in r.json()["issues"] if i["healer_id"] == "unconfirmed_appointment"]
    assert len(nudge) == 1
    assert nudge[0]["entity_id"] == "u1"
    assert fake_db.appointments.docs[0]["confirmation_nudge_sent_at"]


def test_confirmed_appointment_not_nudged(client, fake_db):
    as_user("t1")
    day = _tomorrow_iso(14)
    fake_db.appointments.docs.append(
        {"id": "u1", "tenant_id": "t1", "tech_id": "tech-9", "status": "confirmed",
         "date": day[:10], "start_at": day})
    r = client.post("/tenants/self-healing/scan")
    assert r.status_code == 200
    assert [i for i in r.json()["issues"] if i["healer_id"] == "unconfirmed_appointment"] == []


# ---------- stale leads ----------

def test_stale_lead_revived(client, fake_db):
    as_user("t1")
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    fake_db.leads.docs.append(
        {"id": "l1", "tenant_id": "t1", "status": "new", "name": "Old Lead",
         "created_at": old, "updated_at": old})
    r = client.post("/tenants/self-healing/scan")
    assert r.status_code == 200
    stale = [i for i in r.json()["issues"] if i["healer_id"] == "stale_lead"]
    assert len(stale) == 1
    assert stale[0]["fix_type"] == "revival_task"
    assert fake_db.leads.docs[0]["revive_task_created"] is True


def test_recent_lead_not_stale(client, fake_db):
    as_user("t1")
    recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    fake_db.leads.docs.append(
        {"id": "l1", "tenant_id": "t1", "status": "new", "name": "Fresh",
         "created_at": recent, "updated_at": recent})
    r = client.post("/tenants/self-healing/scan")
    assert r.status_code == 200
    assert [i for i in r.json()["issues"] if i["healer_id"] == "stale_lead"] == []


# ---------- overdue invoices ----------

def test_overdue_invoice_nudged(client, fake_db):
    as_user("t1")
    past = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
    fake_db.invoices.docs.append(
        {"id": "inv1", "tenant_id": "t1", "status": "unpaid",
         "due_date": past, "invoice_number": "INV-1001", "total": 450.0})
    r = client.post("/tenants/self-healing/scan")
    assert r.status_code == 200
    ov = [i for i in r.json()["issues"] if i["healer_id"] == "overdue_invoice_nudge"]
    assert len(ov) == 1
    assert "5 days past due" in ov[0]["detail"]
    assert fake_db.invoices.docs[0]["healer_nudge_sent_at"]


def test_invoice_in_collections_noted(client, fake_db):
    as_user("t1")
    past = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
    fake_db.invoices.docs.append(
        {"id": "inv1", "tenant_id": "t1", "status": "overdue", "due_date": past})
    fake_db.collection_enrollments.docs.append(
        {"id": "e1", "tenant_id": "t1", "invoice_id": "inv1", "status": "active"})
    r = client.post("/tenants/self-healing/scan")
    assert r.status_code == 200
    ov = [i for i in r.json()["issues"] if i["healer_id"] == "overdue_invoice_nudge"]
    assert len(ov) == 1
    assert "already enrolled" in ov[0]["fix_detail"]


# ---------- no-show risk ----------

def test_no_show_risk_flags_tech(client, fake_db):
    as_user("t1")
    recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    for i in range(2):
        fake_db.appointments.docs.append(
            {"id": f"n{i}", "tenant_id": "t1", "tech_id": "tech-slow",
             "status": "completed", "start_at": recent, "arrived_late": True})
    r = client.post("/tenants/self-healing/scan")
    assert r.status_code == 200
    risk = [i for i in r.json()["issues"] if i["healer_id"] == "no_show_risk"]
    assert len(risk) == 1
    assert risk[0]["entity_id"] == "tech-slow"
    assert risk[0]["fix_type"] == "review_flag"


# ---------- scan bookkeeping / dedupe ----------

def test_scan_dedupes_second_run(client, fake_db):
    as_user("t1")
    _seed_double_booking(fake_db)
    first = client.post("/tenants/self-healing/scan").json()
    second = client.post("/tenants/self-healing/scan").json()
    assert first["detected"] >= 1
    assert second["detected"] == 0
    r = client.get("/tenants/self-healing/healers")
    dbl = next(h for h in r.json()["healers"] if h["id"] == "double_booking")
    assert dbl["last_run"]
    assert dbl["fixes_applied"] >= 1


def test_scan_respects_disabled_healer(client, fake_db):
    as_user("t1")
    client.post("/tenants/self-healing/healers/double_booking/toggle", json={"enabled": False})
    _seed_double_booking(fake_db)
    r = client.post("/tenants/self-healing/scan")
    assert r.status_code == 200
    assert [i for i in r.json()["issues"] if i["healer_id"] == "double_booking"] == []


# ---------- override / resolve ----------

def test_override_reverts_autofix(client, fake_db):
    as_user("t1")
    _seed_double_booking(fake_db)
    client.post("/tenants/self-healing/scan")
    issue = next(i for i in fake_db.ops_issues.docs if i["healer_id"] == "double_booking")
    r = client.post(f"/tenants/self-healing/issues/{issue['id']}/override",
                    json={"reason": "Tech covered it after all"})
    assert r.status_code == 200
    assert r.json()["status"] == "overridden"
    stored = fake_db.ops_issues.docs[0]
    assert stored["status"] == "overridden"
    assert stored["override_reason"] == "Tech covered it after all"
    # auto-fix reverted: needs_reassign unset again
    a2 = next(d for d in fake_db.appointments.docs if d["id"] == "a2")
    assert "needs_reassign" not in a2 or a2.get("needs_reassign") is None


def test_override_requires_reason(client, fake_db):
    as_user("t1")
    _seed_double_booking(fake_db)
    client.post("/tenants/self-healing/scan")
    issue = fake_db.ops_issues.docs[0]
    r = client.post(f"/tenants/self-healing/issues/{issue['id']}/override", json={"reason": "  "})
    assert r.status_code == 400


def test_resolve_marks_resolved(client, fake_db):
    as_user("t1")
    _seed_double_booking(fake_db)
    client.post("/tenants/self-healing/scan")
    issue = fake_db.ops_issues.docs[0]
    r = client.post(f"/tenants/self-healing/issues/{issue['id']}/resolve")
    assert r.status_code == 200
    assert r.json()["status"] == "resolved"
    assert fake_db.ops_issues.docs[0]["status"] == "resolved"


def test_issues_inbox_filters(client, fake_db):
    as_user("t1")
    _seed_double_booking(fake_db)
    client.post("/tenants/self-healing/scan")
    r = client.get("/tenants/self-healing/issues")
    assert r.status_code == 200
    assert r.json()["counts"]["auto_fixed"] >= 1
    r = client.get("/tenants/self-healing/issues", params={"status": "auto_fixed"})
    assert all(i["status"] == "auto_fixed" for i in r.json()["issues"])
    r = client.get("/tenants/self-healing/issues", params={"healer": "no_show_risk"})
    assert r.json()["issues"] == []


def test_tenant_isolation(client, fake_db):
    as_user("t1")
    _seed_double_booking(fake_db)
    client.post("/tenants/self-healing/scan")
    as_user("t2")
    r = client.get("/tenants/self-healing/issues")
    assert r.json()["issues"] == []
