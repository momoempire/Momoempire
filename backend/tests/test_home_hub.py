"""Unit tests for the Homeowner Home Hub (routers/home_hub.py).

No database, network, or browser needed: a small FastAPI app with only the
home-hub router, a fake in-memory Mongo, and an overridden auth dependency.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "home-hub-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "home_hub_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.home_hub as hh_mod  # noqa: E402
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


class FakeDB:
    def __init__(self):
        self.home_profiles = FakeCollection()
        self.home_hub_tokens = FakeCollection()
        self.appointments = FakeCollection()
        self.invoices = FakeCollection()
        self.equipment = FakeCollection()
        self.equipment_service_events = FakeCollection()
        self.leads = FakeCollection()
        self.tenants = FakeCollection()


# ---------- fixtures ----------

current_user = {}


@pytest.fixture
def fake_db():
    db = FakeDB()
    db.tenants.docs.append({"id": "t1", "name": "Test Shop", "contact_phone": "555-0000",
                            "contact_email": "shop@example.com"})
    return db


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(hh_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(hh_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def home_payload(**over):
    payload = {
        "homeowner_name": "Jane Doe",
        "email": "jane@example.com",
        "phone": "555-0100",
        "address": {"street": "123 Main St", "city": "Dallas", "state": "TX", "zip": "75201"},
        "property_type": "single_family",
        "year_built": 1998,
        "sqft": 2100,
        "equipment": [{"equipment_type": "furnace", "make": "Trane", "model": "XR80",
                       "install_date": "2019-03-01"}],
        "reminders": [{"title": "Change filter", "due_date": "2026-11-01", "recurring": "monthly"}],
        "notes": "Dog in backyard",
    }
    payload.update(over)
    return payload


def create_home(client, **over):
    as_user("t1")
    r = client.post("/api/home-hub/tenants/homes", json=home_payload(**over))
    assert r.status_code == 201, r.text
    return r.json()


# ---------- create / read / update ----------

def test_create_home_returns_profile_and_share_token(client, fake_db):
    body = create_home(client)
    home = body["home"]
    assert home["homeowner_name"] == "Jane Doe"
    assert home["address_summary"] == "123 Main St, Dallas, TX, 75201"
    assert home["equipment"][0]["make"] == "Trane"
    assert home["reminders"][0]["title"] == "Change filter"
    assert body["share_token"].startswith("hh_")


def test_create_home_requires_name(client):
    as_user("t1")
    r = client.post("/api/home-hub/tenants/homes", json=home_payload(homeowner_name=""))
    assert r.status_code == 422


def test_list_homes_tenant_scoped_and_searchable(client, fake_db):
    create_home(client)
    create_home(client, homeowner_name="Bob Smith", phone="555-0200")
    as_user("t2")
    r = client.get("/api/home-hub/tenants/homes")
    assert r.status_code == 200
    assert r.json()["homes"] == []
    as_user("t1")
    r = client.get("/api/home-hub/tenants/homes")
    assert len(r.json()["homes"]) == 2
    r = client.get("/api/home-hub/tenants/homes", params={"search": "bob"})
    assert len(r.json()["homes"]) == 1
    assert r.json()["homes"][0]["homeowner_name"] == "Bob Smith"


def test_update_home_via_home_id(client, fake_db):
    body = create_home(client)
    home_id = body["home"]["id"]
    as_user("t1")
    r = client.post(f"/api/home-hub/tenants/homes?home_id={home_id}",
                    json=home_payload(notes="Updated notes", sqft=2200))
    assert r.status_code == 200, r.text
    assert r.json()["updated"] is True
    assert r.json()["home"]["notes"] == "Updated notes"
    assert r.json()["home"]["sqft"] == 2200


def test_update_home_missing_returns_404(client):
    as_user("t1")
    r = client.post("/api/home-hub/tenants/homes?home_id=missing", json=home_payload())
    assert r.status_code == 404


# ---------- public homeowner profile ----------

def test_public_profile_with_valid_token(client, fake_db):
    body = create_home(client)
    token = body["share_token"]
    r = client.get(f"/api/home-hub/profile/{token}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["profile"]["homeowner_name"] == "Jane Doe"
    assert data["equipment"][0]["model"] == "XR80"
    assert data["reminders"][0]["recurring"] == "monthly"
    assert data["shop"]["name"] == "Test Shop"


def test_public_profile_invalid_token_401(client, fake_db):
    r = client.get("/api/home-hub/profile/hh_nope")
    assert r.status_code == 401


def test_public_profile_revoked_token_401(client, fake_db):
    body = create_home(client)
    old_token = body["share_token"]
    home_id = body["home"]["id"]
    as_user("t1")
    r = client.post(f"/api/home-hub/tenants/homes/{home_id}/token")
    assert r.status_code == 200
    new_token = r.json()["share_token"]
    assert new_token != old_token
    assert client.get(f"/api/home-hub/profile/{old_token}").status_code == 401
    assert client.get(f"/api/home-hub/profile/{new_token}").status_code == 200


# ---------- history timeline ----------

def test_home_history_merges_sources_newest_first(client, fake_db):
    body = create_home(client)
    home_id = body["home"]["id"]
    fake_db.appointments.docs.append({
        "id": "a-past", "tenant_id": "t1", "customer_phone": "555-0100",
        "service_name": "AC tune-up", "status": "completed", "staff": "Ray",
        "start_at": "2024-01-01T09:00:00+00:00",
    })
    fake_db.appointments.docs.append({
        "id": "a-future", "tenant_id": "t1", "customer_phone": "555-0100",
        "service_name": "Filter change", "status": "scheduled", "staff": "Ray",
        "start_at": "2099-01-01T09:00:00+00:00",
    })
    fake_db.invoices.docs.append({
        "id": "i-1", "tenant_id": "t1", "customer_phone": "555-0100",
        "title": "Repair invoice", "status": "paid", "total": 249.0,
        "created_at": "2024-06-01T12:00:00+00:00", "paid_at": "2024-06-02T12:00:00+00:00",
    })
    # other tenant's records must not leak in
    fake_db.appointments.docs.append({
        "id": "a-other", "tenant_id": "t2", "customer_phone": "555-0100",
        "service_name": "Sneaky", "status": "completed", "start_at": "2024-02-01T09:00:00+00:00",
    })

    as_user("t1")
    r = client.get(f"/api/home-hub/tenants/homes/{home_id}/history")
    assert r.status_code == 200, r.text
    items = r.json()["history"]
    kinds = {i["type"] for i in items}
    assert {"appointment", "invoice"} <= kinds
    assert all(i["ref_id"] != "a-other" for i in items)
    dates = [str(i["date"]) for i in items]
    assert dates == sorted(dates, reverse=True)
    # public profile splits upcoming vs history
    token = body["share_token"]
    r = client.get(f"/api/home-hub/profile/{token}")
    data = r.json()
    assert [u["ref_id"] for u in data["upcoming"]] == ["a-future"]
    assert "a-future" not in [h["ref_id"] for h in data["history"]]


# ---------- request service ----------

def test_public_request_service_creates_lead(client, fake_db):
    body = create_home(client)
    token = body["share_token"]
    home_id = body["home"]["id"]
    r = client.post(f"/api/home-hub/request/{token}", json={
        "description": "AC blowing warm air",
        "service_type": "AC repair",
        "preferred_date": "2026-10-20",
        "contact_name": "Jane Doe",
        "contact_phone": "555-0100",
    })
    assert r.status_code == 201, r.text
    lead_id = r.json()["lead_id"]
    lead = next(d for d in fake_db.leads.docs if d["id"] == lead_id)
    assert lead["source"] == "home-hub"
    assert lead["home_id"] == home_id
    assert lead["status"] == "new"
    assert "AC blowing warm air" in lead["notes"]


def test_public_request_service_bad_token_401(client, fake_db):
    r = client.post("/api/home-hub/request/hh_bad", json={"description": "x"})
    assert r.status_code == 401


def test_shop_request_service_requires_auth_and_home(client, fake_db):
    body = create_home(client)
    home_id = body["home"]["id"]
    as_user("t1")
    r = client.post(f"/api/home-hub/tenants/homes/{home_id}/request",
                    json={"description": "Annual checkup", "service_type": "maintenance"})
    assert r.status_code == 201, r.text
    assert len(fake_db.leads.docs) == 1
    as_user("t2")
    r = client.post(f"/api/home-hub/tenants/homes/{home_id}/request",
                    json={"description": "sneaky"})
    assert r.status_code == 404  # other tenant cannot see this home
