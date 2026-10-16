"""Tests for routers/compliance.py (Compliance Autopilot, Wave 6 / Phase E #5).

Fake in-memory mongo — no live MongoDB. Date math is anchored to
datetime.now(timezone.utc) so the suite is deterministic.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "compliance-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "compliance_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.compliance as comp_mod  # noqa: E402
from security import require_tenant_user  # noqa: E402


# ---------- fake mongo ----------

class FakeCollection:
    def __init__(self):
        self.docs = []

    def find(self, query, proj=None):
        return [d for d in self.docs if all(d.get(k) == v for k, v in query.items())]

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
        return SimpleNamespace(matched_count=0, modified_count=0)


class FakeDB:
    def __init__(self):
        self.compliance_items = FakeCollection()
        self.compliance_docs = FakeCollection()


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(comp_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(comp_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id="t1", role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def _iso(days_from_now: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days_from_now)).date().isoformat()


# ---------- seeding ----------

def test_checklist_seeds_hvac_extras(client):
    as_user("t1")
    r = client.get("/api/compliance/tenants/checklist?trade=hvac&state=TX")
    assert r.status_code == 200
    body = r.json()
    names = [i["name"] for i in body["items"]]
    assert "EPA Section 608 certification" in names
    assert "Business license renewal" in names
    assert body["trade"] == "hvac"
    assert "not legal advice" in body["disclaimer"]


def test_trade_specific_seeding_differs(client):
    as_user("t-plumb")
    hvac = client.get("/api/compliance/tenants/checklist?trade=hvac").json()
    as_user("t-plumb2")
    plumbing = client.get("/api/compliance/tenants/checklist?trade=plumbing").json()
    hvac_names = {i["name"] for i in hvac["items"]}
    plumbing_names = {i["name"] for i in plumbing["items"]}
    assert "EPA Section 608 certification" in hvac_names
    assert "EPA Section 608 certification" not in plumbing_names
    assert "Backflow prevention assembly tester cert" in plumbing_names


def test_unknown_trade_falls_back_to_base(client):
    as_user("t3")
    r = client.get("/api/compliance/tenants/checklist?trade=quantum_welding")
    assert r.status_code == 200
    names = {i["name"] for i in r.json()["items"]}
    assert "Business license renewal" in names
    assert len(r.json()["items"]) == len(comp_mod.BASE_ITEMS)


def test_seed_is_idempotent(client):
    as_user("t4")
    first = client.get("/api/compliance/tenants/checklist?trade=hvac").json()
    second = client.get("/api/compliance/tenants/checklist").json()
    assert first["count"] == second["count"]
    assert len({i["id"] for i in second["items"]}) == second["count"]


# ---------- custom items ----------

def test_add_custom_item_owner(client):
    as_user("t5")
    r = client.post("/api/compliance/tenants/checklist/items",
                    json={"name": "City fire inspection", "category": "safety", "required": True})
    assert r.status_code == 201
    item = r.json()["item"]
    assert item["source"] == "custom"
    assert item["status"] == "pending"
    got = client.get("/api/compliance/tenants/checklist").json()
    assert any(i["id"] == item["id"] for i in got["items"])


def test_add_custom_item_forbidden_for_tech(client):
    as_user("t6", role="tech")
    r = client.post("/api/compliance/tenants/checklist/items", json={"name": "X"})
    assert r.status_code == 403


def test_update_item_status(client):
    as_user("t7")
    seeded = client.get("/api/compliance/tenants/checklist?trade=hvac").json()
    item_id = seeded["items"][0]["id"]
    r = client.put(f"/api/compliance/tenants/checklist/items/{item_id}",
                   json={"status": "done", "notes": "renewed online"})
    assert r.status_code == 200
    assert r.json()["item"]["status"] == "done"
    assert r.json()["item"]["notes"] == "renewed online"


def test_update_unknown_item_404(client):
    as_user("t8")
    client.get("/api/compliance/tenants/checklist")
    r = client.put("/api/compliance/tenants/checklist/items/nope", json={"status": "done"})
    assert r.status_code == 404


# ---------- document vault ----------

def test_register_document(client):
    as_user("t9")
    r = client.post("/api/compliance/tenants/documents", json={
        "name": "GL policy 2026", "type": "insurance",
        "expiry_date": _iso(45), "file_ref": "vault/gl-2026.pdf",
    })
    assert r.status_code == 201
    doc = r.json()["document"]
    assert doc["days_to_expiry"] == 45
    assert doc["urgency"] == "amber"
    assert "not legal advice" in r.json()["disclaimer"]


def test_expiring_sorted_by_urgency(client):
    as_user("t10")
    docs = [
        ("Far future cert", "cert", 400),
        ("Expiring soon", "license", 10),
        ("Already expired", "insurance", -5),
        ("Mid-range", "license", 59),
    ]
    for name, dtype, days in docs:
        r = client.post("/api/compliance/tenants/documents",
                        json={"name": name, "type": dtype, "expiry_date": _iso(days)})
        assert r.status_code == 201
    r = client.get("/api/compliance/tenants/documents/expiring")
    body = r.json()
    assert body["window_days"] == 60
    names = [d["name"] for d in body["documents"]]
    # expired first, then ascending; the 400-day doc is excluded
    assert names == ["Already expired", "Expiring soon", "Mid-range"]
    urgencies = [d["urgency"] for d in body["documents"]]
    assert urgencies == ["expired", "red", "amber"]


def test_tenant_isolation(client):
    as_user("t11")
    client.post("/api/compliance/tenants/documents",
                json={"name": "Secret doc", "type": "license", "expiry_date": _iso(5)})
    as_user("t12")
    body = client.get("/api/compliance/tenants/documents/expiring").json()
    assert body["count"] == 0
    status = client.get("/api/compliance/tenants/status").json()
    assert status["total"] > 0  # seeded checklist exists for t12, independent of t11


# ---------- status / report ----------

def test_status_score_math_and_colors(client):
    as_user("t13")
    seeded = client.get("/api/compliance/tenants/checklist?trade=hvac").json()
    items = seeded["items"]
    total = len(items)
    half = total // 2
    for item in items[:half]:
        client.put(f"/api/compliance/tenants/checklist/items/{item['id']}",
                   json={"status": "done"})
    body = client.get("/api/compliance/tenants/status").json()
    expected = round(100 * half / total)
    assert body["score"] == expected
    assert body["done"] == half and body["total"] == total
    assert body["color"] == ("amber" if expected >= 50 else "red")
    # all-done category must be green
    for cat in body["categories"].values():
        if cat["score"] == 100:
            assert cat["color"] == "green"
    assert body["upcoming_deadlines"] == []
    assert "not legal advice" in body["disclaimer"]


def test_status_includes_upcoming_deadlines(client):
    as_user("t14")
    client.get("/api/compliance/tenants/checklist")
    client.post("/api/compliance/tenants/documents",
                json={"name": "Barber license", "type": "license", "expiry_date": _iso(20)})
    body = client.get("/api/compliance/tenants/status").json()
    assert len(body["upcoming_deadlines"]) == 1
    assert body["upcoming_deadlines"][0]["name"] == "Barber license"
    assert body["open_required_items"] >= 1  # seeded required items start pending


def test_report_is_audit_ready(client):
    as_user("t15")
    client.get("/api/compliance/tenants/checklist?trade=electrical&state=CA")
    client.post("/api/compliance/tenants/documents",
                json={"name": "E&O policy", "type": "insurance",
                      "expiry_date": _iso(90), "file_ref": "vault/eo.pdf"})
    body = client.get("/api/compliance/tenants/report").json()
    assert body["generated_at"]
    assert "not legal advice" in body["disclaimer"]
    assert "license" in body["items_by_category"]
    names = [i["name"] for cat in body["items_by_category"].values() for i in cat]
    assert "State electrician license renewal" in names
    assert any(d["name"] == "E&O policy" for d in body["documents"])
    # printable-view shape: every item carries status/required
    for cat_items in body["items_by_category"].values():
        for i in cat_items:
            assert {"status", "required", "recurring"} <= set(i.keys())
