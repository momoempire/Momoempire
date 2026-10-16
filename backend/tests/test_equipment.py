"""Unit tests for equipment history (routers/equipment.py).

No database, network, or browser needed: a small FastAPI app with only the
equipment router, a fake in-memory Mongo, and an overridden auth dependency.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "equipment-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "equipment_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.equipment as eq_mod  # noqa: E402
from security import require_tenant_user  # noqa: E402


# ---------- fake mongo ----------

class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        self.docs = sorted(self.docs, key=lambda d: str(d.get(key, "")), reverse=(direction < 0))
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
                elif op == "$gte" and not (val >= opval):
                    return False
                elif op == "$lte" and not (val <= opval):
                    return False
                elif op not in ("$in", "$ne", "$gte", "$lte"):
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


class FakeDB:
    def __init__(self):
        self.equipment = FakeCollection()
        self.equipment_service_events = FakeCollection()


# ---------- fixtures ----------

current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(eq_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(eq_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def equipment_payload(**over):
    payload = {
        "customer_id": "cust-1",
        "customer_name": "Jane Doe",
        "location_label": "Attic unit",
        "service_address": {"street": "123 Main St", "city": "Dallas", "state": "TX", "zip": "75201"},
        "equipment_type": "ac_condenser",
        "make": "Trane",
        "model": "XR16",
        "serial_number": "SN-001",
        "install_date": "2021-05-01",
        "notes": "Attic access via garage",
    }
    payload.update(over)
    return payload


# ---------- create / read / update ----------

def test_create_persists_address_fields(client, fake_db):
    as_user("t1")
    r = client.post("/tenants/equipment/", json=equipment_payload())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["service_address"] == {"street": "123 Main St", "city": "Dallas", "state": "TX", "zip": "75201"}
    assert body["address_summary"] == "123 Main St, Dallas, TX, 75201"
    assert body["location_label"] == "Attic unit"
    assert body["make"] == "Trane" and body["model"] == "XR16"
    assert body["serial_number"] == "SN-001"
    assert body["install_date"] == "2021-05-01"
    assert body["active"] is True


def test_create_requires_customer_id(client, fake_db):
    as_user("t1")
    payload = equipment_payload()
    del payload["customer_id"]
    r = client.post("/tenants/equipment/", json=payload)
    assert r.status_code == 422


def test_list_filters_by_customer_and_search(client, fake_db):
    as_user("t1")
    client.post("/tenants/equipment/", json=equipment_payload(customer_id="cust-1", make="Trane"))
    client.post("/tenants/equipment/", json=equipment_payload(customer_id="cust-1", make="Carrier",
                                                              serial_number="SN-002",
                                                              service_address={"street": "9 Elm St", "city": "Austin",
                                                                               "state": "TX", "zip": "78701"}))
    client.post("/tenants/equipment/", json=equipment_payload(customer_id="cust-9", make="Lennox",
                                                              serial_number="SN-003"))

    r = client.get("/tenants/equipment/", params={"customer_id": "cust-1"})
    assert [e["serial_number"] for e in r.json()["equipment"]] == ["SN-002", "SN-001"]  # newest first

    r = client.get("/tenants/equipment/", params={"q": "trane"})
    assert r.json()["count"] == 1

    r = client.get("/tenants/equipment/", params={"q": "austin"})
    assert r.json()["count"] == 1  # matches service-address city


def test_get_and_update(client, fake_db):
    as_user("t1")
    eid = client.post("/tenants/equipment/", json=equipment_payload()).json()["id"]

    r = client.get(f"/tenants/equipment/{eid}")
    assert r.status_code == 200
    assert r.json()["model"] == "XR16"

    r = client.put(f"/tenants/equipment/{eid}", json={
        "model": "XR17",
        "service_address": {"street": "456 Oak Ave", "city": "Dallas", "state": "TX", "zip": "75202"},
    })
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "XR17"
    assert r.json()["service_address"]["street"] == "456 Oak Ave"


def test_tenant_isolation_crud(client, fake_db):
    as_user("t1")
    eid = client.post("/tenants/equipment/", json=equipment_payload()).json()["id"]

    as_user("t2")
    assert client.get(f"/tenants/equipment/{eid}").status_code == 404
    assert client.put(f"/tenants/equipment/{eid}", json={"model": "X"}).status_code == 404
    assert client.delete(f"/tenants/equipment/{eid}").status_code == 404
    assert client.get("/tenants/equipment/").json()["count"] == 0
    assert client.get(f"/tenants/equipment/{eid}/history").status_code == 404

    as_user("t1")
    assert client.get(f"/tenants/equipment/{eid}").status_code == 200


# ---------- soft delete ----------

def test_soft_delete_hides_from_list_but_keeps_history(client, fake_db):
    as_user("t1")
    eid = client.post("/tenants/equipment/", json=equipment_payload()).json()["id"]
    client.post(f"/tenants/equipment/{eid}/service-events",
                json={"description": "Capacitor replaced", "tech": "Amy"})

    r = client.delete(f"/tenants/equipment/{eid}")
    assert r.status_code == 200
    assert r.json()["active"] is False

    assert client.get("/tenants/equipment/").json()["count"] == 0
    assert client.get("/tenants/equipment/", params={"include_inactive": True}).json()["count"] == 1

    # History survives soft delete.
    r = client.get(f"/tenants/equipment/{eid}/history")
    assert len(r.json()["events"]) == 1


def test_invalid_transitions_rejected(client, fake_db):
    as_user("t1")
    eid = client.post("/tenants/equipment/", json=equipment_payload()).json()["id"]
    client.delete(f"/tenants/equipment/{eid}")

    # Deactivated records reject service events and edits (reactivation only).
    r = client.post(f"/tenants/equipment/{eid}/service-events",
                    json={"description": "Tune-up", "tech": "Bo"})
    assert r.status_code == 400

    r = client.put(f"/tenants/equipment/{eid}", json={"notes": "changed"})
    assert r.status_code == 400

    # Reactivation is the valid transition back.
    r = client.put(f"/tenants/equipment/{eid}", json={"active": True})
    assert r.status_code == 200
    assert r.json()["active"] is True

    r = client.post(f"/tenants/equipment/{eid}/service-events",
                    json={"description": "Tune-up", "tech": "Bo"})
    assert r.status_code == 201

    # Event with no description is rejected.
    r = client.post(f"/tenants/equipment/{eid}/service-events",
                    json={"description": "  ", "tech": "Bo"})
    assert r.status_code == 400


# ---------- service timeline ----------

def test_service_events_timeline_newest_first(client, fake_db):
    as_user("t1")
    eid = client.post("/tenants/equipment/", json=equipment_payload()).json()["id"]

    client.post(f"/tenants/equipment/{eid}/service-events",
                json={"service_date": "2025-06-01", "description": "Capacitor replaced", "tech": "Amy"})
    client.post(f"/tenants/equipment/{eid}/service-events",
                json={"service_date": "2026-01-15", "description": "Annual tune-up", "tech": "Bo"})

    r = client.get(f"/tenants/equipment/{eid}/history")
    events = r.json()["events"]
    assert [e["description"] for e in events] == ["Annual tune-up", "Capacitor replaced"]

    # Record carries the event count.
    r = client.get(f"/tenants/equipment/{eid}")
    assert r.json()["service_event_count"] == 2


def test_service_event_linked_to_job(client, fake_db):
    as_user("t1")
    e1 = client.post("/tenants/equipment/", json=equipment_payload(serial_number="SN-001")).json()["id"]
    e2 = client.post("/tenants/equipment/", json=equipment_payload(serial_number="SN-002")).json()["id"]

    client.post(f"/tenants/equipment/{e1}/service-events",
                json={"description": "Replaced contactor", "job_id": "job-42", "tech": "Amy"})
    client.post(f"/tenants/equipment/{e2}/service-events",
                json={"description": "Cleaned coils", "job_id": "job-42", "tech": "Amy"})
    client.post(f"/tenants/equipment/{e2}/service-events",
                json={"description": "Unrelated visit", "job_id": "job-99", "tech": "Bo"})

    r = client.get("/tenants/equipment/by-job/job-42")
    assert r.status_code == 200, r.text
    assert r.json()["job_id"] == "job-42"
    assert sorted(e["serial_number"] for e in r.json()["equipment"]) == ["SN-001", "SN-002"]

    r = client.get("/tenants/equipment/by-job/job-999")
    assert r.json()["equipment"] == []


def test_service_events_tenant_isolated(client, fake_db):
    as_user("t1")
    eid = client.post("/tenants/equipment/", json=equipment_payload()).json()["id"]
    client.post(f"/tenants/equipment/{eid}/service-events",
                json={"description": "Tune-up", "job_id": "job-7", "tech": "Amy"})

    as_user("t2")
    # Other tenant cannot see this tenant's events or job linkage.
    assert client.get(f"/tenants/equipment/{eid}/history").status_code == 404
    assert client.get("/tenants/equipment/by-job/job-7").json()["equipment"] == []

    r = client.post(f"/tenants/equipment/{eid}/service-events",
                    json={"description": "Intrusion", "tech": "X"})
    assert r.status_code == 404


# ---------- misc ----------

def test_suggested_types(client, fake_db):
    as_user("t1")
    r = client.get("/tenants/equipment/types/suggested")
    assert r.status_code == 200
    assert "furnace" in r.json()["suggested"]
    assert "ac_condenser" in r.json()["suggested"]


def test_equipment_type_free_form(client, fake_db):
    as_user("t1")
    r = client.post("/tenants/equipment/", json=equipment_payload(equipment_type="custom_fryer"))
    assert r.status_code == 201
    assert r.json()["equipment_type"] == "custom_fryer"


def test_customer_service_address_model():
    """CustomerIn now accepts an optional service address without breaking the old shape."""
    from models import CustomerIn
    c = CustomerIn(name="Acme")
    assert c.service_address is None
    c2 = CustomerIn(name="Acme", service_address={"street": "1 Way", "city": "Tulsa", "state": "OK", "postal_code": "74101"})
    assert c2.service_address.city == "Tulsa"
    assert c.model_dump()["name"] == "Acme"
