"""Unit tests for inventory / truck stock (routers/inventory.py).

Zero network, zero database: a small FastAPI app with only the inventory
router, a fake in-memory Mongo, and an overridden auth dependency.

Coverage:
- Location CRUD (warehouse + truck, tech assignment, delete blocked w/ stock).
- Part CRUD, per-tenant SKU uniqueness, pricebook cost link.
- Movement math: receive adds, transfer moves, use_on_job decrements and
  requires job_id (and the job must exist).
- Negative stock is blocked (400).
- Ledger is append-only: no update/delete routes exist for movements.
- Low-stock alerts fire when qty <= reorder_point.
- Per-job usage totals with cost snapshots.
- Tenant isolation on every collection.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "inventory-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "inventory_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.inventory as inv_mod  # noqa: E402
from security import require_tenant_user  # noqa: E402


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
        if k == "$or":
            if not any(_match(doc, sub) for sub in cond):
                return False
            continue
        val = doc.get(k)
        if isinstance(cond, dict):
            for op, opval in cond.items():
                if op == "$in" and val not in opval:
                    return False
                elif op == "$ne" and val == opval:
                    return False
                elif op == "$gt" and not (val is not None and val > opval):
                    return False
                elif op == "$gte" and not (val is not None and val >= opval):
                    return False
                elif op not in ("$in", "$ne", "$gt", "$gte"):
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

    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if _match(d, query):
                for k, v in update.get("$set", {}).items():
                    d[k] = v
                for k, v in update.get("$inc", {}).items():
                    d[k] = (d.get(k) or 0) + v
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            doc = {k: v for k, v in query.items()
                   if not isinstance(v, dict)}
            for k, v in update.get("$set", {}).items():
                doc[k] = v
            for k, v in update.get("$inc", {}).items():
                doc[k] = (doc.get(k) or 0) + v
            self.docs.append(doc)
            return SimpleNamespace(matched_count=0, modified_count=0,
                                   upserted_id=doc.get("id"))
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if _match(d, query):
                self.docs.pop(i)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def delete_many(self, query):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _match(d, query)]
        return SimpleNamespace(deleted_count=before - len(self.docs))


class FakeDB:
    def __init__(self):
        self.inventory_locations = FakeCollection()
        self.inventory_parts = FakeCollection()
        self.inventory_movements = FakeCollection()
        self.inventory_stock = FakeCollection()
        self.appointments = FakeCollection()
        self.pricebook_items = FakeCollection()
        self.tenants = FakeCollection()

    def __getitem__(self, name):
        return getattr(self, name)


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
    monkeypatch.setattr(inv_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(inv_mod.router)

    def _user():
        return dict(current_user)

    app.dependency_overrides[require_tenant_user] = _user
    return TestClient(app, raise_server_exceptions=False)


def login(tenant="t1", uid="u1"):
    current_user.clear()
    current_user.update({"tenant_id": tenant, "id": uid, "role": "owner"})


def seed_basic(fake_db, tenant="t1"):
    """One warehouse, one truck, one job; returns (warehouse, truck, part)."""
    login(tenant)
    fake_db.inventory_locations.docs.append({
        "id": "loc-wh", "tenant_id": tenant, "type": "warehouse",
        "name": "Main Warehouse", "active": True})
    fake_db.inventory_locations.docs.append({
        "id": "loc-truck", "tenant_id": tenant, "type": "truck",
        "name": "Truck 7", "tech_id": "tech-1", "tech_name": "Sam",
        "active": True})
    fake_db.appointments.docs.append(
        {"id": "job-1", "tenant_id": tenant, "status": "scheduled"})
    fake_db.inventory_parts.docs.append({
        "id": "part-1", "tenant_id": tenant, "sku": "CAP-35",
        "name": "Capacitor 35uF", "reorder_point": 5, "unit": "ea",
        "unit_cost": 12.5, "active": True})
    return "loc-wh", "loc-truck", "part-1"


# ---------- locations ----------

def test_location_crud(client, fake_db):
    login("t1")
    r = client.post("/inventory/locations",
                    json={"type": "warehouse", "name": "Main Warehouse"})
    assert r.status_code == 201
    loc = r.json()["location"]
    assert loc["type"] == "warehouse"
    assert loc["name"] == "Main Warehouse"

    r = client.post("/inventory/locations",
                    json={"type": "truck", "name": "Truck 7",
                          "tech_id": "tech-1", "tech_name": "Sam"})
    assert r.status_code == 201
    truck_id = r.json()["location"]["id"]
    assert r.json()["location"]["tech_name"] == "Sam"

    r = client.get("/inventory/locations")
    assert r.status_code == 200
    assert len(r.json()["locations"]) == 2

    r = client.put(f"/inventory/locations/{truck_id}",
                   json={"tech_name": "Alex"})
    assert r.status_code == 200
    assert r.json()["location"]["tech_name"] == "Alex"

    # delete an empty location works
    r = client.delete(f"/inventory/locations/{truck_id}")
    assert r.status_code == 200
    assert r.json()["deleted"] == truck_id


def test_location_delete_blocked_with_stock(client, fake_db):
    loc_wh, _, part = seed_basic(fake_db)
    r = client.post("/inventory/movements",
                    json={"part_id": part, "to_location_id": loc_wh,
                          "qty": 10, "type": "receive"})
    assert r.status_code == 201
    r = client.delete(f"/inventory/locations/{loc_wh}")
    assert r.status_code == 409
    assert "stock" in r.json()["detail"].lower()


def test_invalid_location_type_rejected(client, fake_db):
    login("t1")
    r = client.post("/inventory/locations",
                    json={"type": "shelf", "name": "X"})
    assert r.status_code == 422


# ---------- parts ----------

def test_part_create_and_sku_unique(client, fake_db):
    login("t1")
    r = client.post("/inventory/parts",
                    json={"sku": "CAP-35", "name": "Capacitor 35uF",
                          "reorder_point": 5, "unit_cost": 12.5})
    assert r.status_code == 201
    assert r.json()["part"]["sku"] == "CAP-35"

    # same SKU for same tenant -> 409
    r = client.post("/inventory/parts",
                    json={"sku": "CAP-35", "name": "Duplicate"})
    assert r.status_code == 409

    r = client.get("/inventory/parts")
    assert len(r.json()["parts"]) == 1

    pid = r.json()["parts"][0]["id"]
    r = client.put(f"/inventory/parts/{pid}", json={"reorder_point": 10})
    assert r.status_code == 200
    assert r.json()["part"]["reorder_point"] == 10


def test_part_pricebook_link_pulls_cost(client, fake_db):
    login("t1")
    fake_db.pricebook_items.docs.append({
        "id": "pb-1", "tenant_id": "t1", "name": "Contactor 30A",
        "sku": "CON-30", "price": 45.0, "cost": 18.75, "active": True})
    r = client.post("/inventory/parts",
                    json={"sku": "CON-30", "name": "Contactor 30A",
                          "pricebook_item_id": "pb-1"})
    assert r.status_code == 201

    # linking a pricebook item from another tenant -> 404
    r = client.post("/inventory/parts",
                    json={"sku": "X-1", "name": "X",
                          "pricebook_item_id": "pb-nope"})
    assert r.status_code == 404


# ---------- movements ----------

def test_receive_adds_stock(client, fake_db):
    loc_wh, _, part = seed_basic(fake_db)
    r = client.post("/inventory/movements",
                    json={"part_id": part, "to_location_id": loc_wh,
                          "qty": 20, "type": "receive", "note": "PO-1"})
    assert r.status_code == 201
    mv = r.json()["movement"]
    assert mv["type"] == "receive"
    assert mv["qty"] == 20
    assert mv["unit_cost"] == 12.5  # cost snapshot from the part

    r = client.get("/inventory/stock", params={"location_id": loc_wh})
    assert r.json()["stock"][0]["qty"] == 20


def test_receive_requires_to_location_only(client, fake_db):
    loc_wh, _, part = seed_basic(fake_db)
    r = client.post("/inventory/movements",
                    json={"part_id": part, "from_location_id": loc_wh,
                          "to_location_id": loc_wh, "qty": 5, "type": "receive"})
    assert r.status_code == 400


def test_transfer_moves_stock(client, fake_db):
    loc_wh, loc_truck, part = seed_basic(fake_db)
    client.post("/inventory/movements",
                json={"part_id": part, "to_location_id": loc_wh,
                      "qty": 20, "type": "receive"})
    r = client.post("/inventory/movements",
                    json={"part_id": part, "from_location_id": loc_wh,
                          "to_location_id": loc_truck, "qty": 8,
                          "type": "transfer"})
    assert r.status_code == 201
    r = client.get("/inventory/stock", params={"location_id": loc_wh})
    assert r.json()["stock"][0]["qty"] == 12
    r = client.get("/inventory/stock", params={"location_id": loc_truck})
    assert r.json()["stock"][0]["qty"] == 8


def test_transfer_needs_two_different_locations(client, fake_db):
    loc_wh, _, part = seed_basic(fake_db)
    r = client.post("/inventory/movements",
                    json={"part_id": part, "from_location_id": loc_wh,
                          "to_location_id": loc_wh, "qty": 1, "type": "transfer"})
    assert r.status_code == 400


def test_use_on_job_decrements_and_requires_job(client, fake_db):
    loc_wh, loc_truck, part = seed_basic(fake_db)
    client.post("/inventory/movements",
                json={"part_id": part, "to_location_id": loc_truck,
                      "qty": 10, "type": "receive"})

    # no job_id -> 400
    r = client.post("/inventory/movements",
                    json={"part_id": part, "from_location_id": loc_truck,
                          "qty": 2, "type": "use_on_job"})
    assert r.status_code == 400

    # unknown job -> 404
    r = client.post("/inventory/movements",
                    json={"part_id": part, "from_location_id": loc_truck,
                          "qty": 2, "type": "use_on_job", "job_id": "job-nope"})
    assert r.status_code == 404

    # valid job -> truck stock drops by 2
    r = client.post("/inventory/movements",
                    json={"part_id": part, "from_location_id": loc_truck,
                          "qty": 2, "type": "use_on_job", "job_id": "job-1",
                          "note": "installed"})
    assert r.status_code == 201
    mv = r.json()["movement"]
    assert mv["job_id"] == "job-1"
    r = client.get("/inventory/stock", params={"location_id": loc_truck})
    assert r.json()["stock"][0]["qty"] == 8


def test_negative_stock_blocked(client, fake_db):
    loc_wh, loc_truck, part = seed_basic(fake_db)
    client.post("/inventory/movements",
                json={"part_id": part, "to_location_id": loc_truck,
                      "qty": 3, "type": "receive"})
    r = client.post("/inventory/movements",
                    json={"part_id": part, "from_location_id": loc_truck,
                          "qty": 5, "type": "use_on_job", "job_id": "job-1"})
    assert r.status_code == 400
    assert "stock" in r.json()["detail"].lower()
    # balance unchanged
    r = client.get("/inventory/stock", params={"location_id": loc_truck})
    assert r.json()["stock"][0]["qty"] == 3


def test_adjust_positive_and_negative(client, fake_db):
    loc_wh, _, part = seed_basic(fake_db)
    r = client.post("/inventory/movements/adjust",
                    json={"part_id": part, "location_id": loc_wh,
                          "qty": 7, "note": "cycle count"})
    assert r.status_code == 201
    r = client.post("/inventory/movements/adjust",
                    json={"part_id": part, "location_id": loc_wh,
                          "qty": -2, "note": "damaged"})
    assert r.status_code == 201
    r = client.get("/inventory/stock", params={"location_id": loc_wh})
    assert r.json()["stock"][0]["qty"] == 5
    # negative adjust can't drive below zero either
    r = client.post("/inventory/movements/adjust",
                    json={"part_id": part, "location_id": loc_wh, "qty": -6})
    assert r.status_code == 400


def test_ledger_is_append_only(client, fake_db):
    loc_wh, _, part = seed_basic(fake_db)
    r = client.post("/inventory/movements",
                    json={"part_id": part, "to_location_id": loc_wh,
                          "qty": 5, "type": "receive"})
    mv_id = r.json()["movement"]["id"]
    # no update/delete routes exist on the ledger: 404 (no route) or 405
    r = client.put(f"/inventory/movements/{mv_id}", json={"qty": 99})
    assert r.status_code in (404, 405), r.status_code
    r = client.delete(f"/inventory/movements/{mv_id}")
    assert r.status_code in (404, 405), r.status_code
    # the original movement is untouched
    r = client.get("/inventory/movements")
    assert r.json()["count"] == 1
    assert r.json()["movements"][0]["qty"] == 5


def test_movement_history_filters(client, fake_db):
    loc_wh, loc_truck, part = seed_basic(fake_db)
    client.post("/inventory/movements",
                json={"part_id": part, "to_location_id": loc_wh,
                      "qty": 20, "type": "receive"})
    client.post("/inventory/movements",
                json={"part_id": part, "from_location_id": loc_wh,
                      "to_location_id": loc_truck, "qty": 8, "type": "transfer"})
    r = client.get("/inventory/movements")
    assert r.json()["count"] == 2
    r = client.get("/inventory/movements", params={"type": "transfer"})
    assert r.json()["count"] == 1
    r = client.get("/inventory/movements", params={"location_id": loc_truck})
    assert r.json()["count"] == 1


# ---------- alerts & job usage ----------

def test_low_stock_alerts(client, fake_db):
    loc_wh, loc_truck, part = seed_basic(fake_db)  # reorder_point=5
    client.post("/inventory/movements",
                json={"part_id": part, "to_location_id": loc_wh,
                      "qty": 5, "type": "receive"})   # at reorder point -> alert
    client.post("/inventory/movements",
                json={"part_id": part, "to_location_id": loc_truck,
                      "qty": 20, "type": "receive"})  # healthy -> no alert
    r = client.get("/inventory/alerts")
    alerts = r.json()["alerts"]
    assert r.json()["count"] == 1
    assert alerts[0]["location_id"] == loc_wh
    assert alerts[0]["qty"] == 5
    assert alerts[0]["reorder_point"] == 5
    assert alerts[0]["out_of_stock"] is False


def test_out_of_stock_alert(client, fake_db):
    loc_wh, _, part = seed_basic(fake_db)
    client.post("/inventory/movements",
                json={"part_id": part, "to_location_id": loc_wh,
                      "qty": 1, "type": "receive"})
    r = client.get("/inventory/stock", params={"location_id": loc_wh})
    assert r.json()["stock"][0]["low_stock"] is True  # 1 <= reorder 5
    r = client.post("/inventory/movements/adjust",
                    json={"part_id": part, "location_id": loc_wh, "qty": -1})
    assert r.status_code == 201
    r = client.get("/inventory/alerts")
    assert r.json()["count"] == 1
    assert r.json()["alerts"][0]["out_of_stock"] is True


def test_job_usage_totals(client, fake_db):
    _, loc_truck, part = seed_basic(fake_db)
    # a second part
    login("t1")
    r = client.post("/inventory/parts",
                    json={"sku": "CON-30", "name": "Contactor 30A",
                          "unit_cost": 18.75})
    part2 = r.json()["part"]["id"]
    client.post("/inventory/movements",
                json={"part_id": part, "to_location_id": loc_truck,
                      "qty": 10, "type": "receive"})
    client.post("/inventory/movements",
                json={"part_id": part2, "to_location_id": loc_truck,
                      "qty": 10, "type": "receive"})
    client.post("/inventory/movements",
                json={"part_id": part, "from_location_id": loc_truck,
                      "qty": 2, "type": "use_on_job", "job_id": "job-1"})
    client.post("/inventory/movements",
                json={"part_id": part, "from_location_id": loc_truck,
                      "qty": 1, "type": "use_on_job", "job_id": "job-1"})
    client.post("/inventory/movements",
                json={"part_id": part2, "from_location_id": loc_truck,
                      "qty": 1, "type": "use_on_job", "job_id": "job-1"})

    r = client.get("/inventory/job-usage/job-1")
    assert r.status_code == 200
    data = r.json()
    assert data["job_id"] == "job-1"
    assert data["line_count"] == 2
    assert data["total_qty"] == 4
    by_sku = {l["sku"]: l for l in data["lines"]}
    assert by_sku["CAP-35"]["qty_used"] == 3
    assert by_sku["CAP-35"]["total_cost"] == round(3 * 12.5, 2)
    assert by_sku["CON-30"]["qty_used"] == 1
    assert by_sku["CON-30"]["total_cost"] == 18.75
    assert data["total_cost"] == round(3 * 12.5 + 18.75, 2)


def test_job_usage_empty_job(client, fake_db):
    seed_basic(fake_db)
    r = client.get("/inventory/job-usage/job-1")
    assert r.status_code == 200
    assert r.json()["line_count"] == 0
    assert r.json()["total_cost"] == 0


# ---------- tenant isolation ----------

def test_tenant_isolation(client, fake_db):
    loc_wh, _, part = seed_basic(fake_db, tenant="t1")
    client.post("/inventory/movements",
                json={"part_id": part, "to_location_id": loc_wh,
                      "qty": 10, "type": "receive"})

    login("t2")
    r = client.get("/inventory/locations")
    assert r.json()["locations"] == []
    r = client.get("/inventory/parts")
    assert r.json()["parts"] == []
    r = client.get("/inventory/stock")
    assert r.json()["stock"] == []
    r = client.get("/inventory/movements")
    assert r.json()["count"] == 0
    r = client.get("/inventory/alerts")
    assert r.json()["count"] == 0
    r = client.get("/inventory/job-usage/job-1")
    assert r.json()["line_count"] == 0

    # tenant 2 cannot reference tenant 1's part or location
    r = client.post("/inventory/movements",
                    json={"part_id": part, "to_location_id": loc_wh,
                          "qty": 1, "type": "receive"})
    assert r.status_code == 404

    # tenant 2 can use its own SKU namespace freely
    r = client.post("/inventory/parts",
                    json={"sku": "CAP-35", "name": "Own capacitor"})
    assert r.status_code == 201

    # tenant 1's data is intact
    login("t1")
    r = client.get("/inventory/stock", params={"location_id": loc_wh})
    assert r.json()["stock"][0]["qty"] == 10
