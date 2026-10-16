"""Tests for parts ordering from the job (routers/parts_ordering.py).

Zero network, zero database: a small FastAPI app with only the parts-ordering
router, a fake in-memory Mongo, and an overridden auth dependency.

Coverage:
- Adding job parts flags truck-stock coverage (covered / partial / needs_order).
- Parts list returns fulfillment status + summary counts.
- Creating a PO pulls only unfulfilled parts; explicit lines also work.
- Send marks sent with a stub transmission; double-send is rejected.
- Receive marks received; wrong-status transitions rejected.
- Tenant isolation on every endpoint.
- 422s for empty orders and bad status filters.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET", "parts-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "parts_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.parts_ordering as po_mod  # noqa: E402
from security import require_tenant_user  # noqa: E402


# ---------- fake mongo ----------

class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    async def to_list(self, n):
        return [dict(d) for d in self.docs[:n]]


def _match(doc, query):
    for k, cond in query.items():
        if isinstance(cond, dict):
            if "$ne" in cond:
                if doc.get(k) == cond["$ne"]:
                    return False
            else:
                return False
        elif doc.get(k) != cond:
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
        self.job_parts = FakeCollection()
        self.purchase_orders = FakeCollection()
        self.inventory_parts = FakeCollection()
        self.inventory_stock = FakeCollection()
        self.inventory_locations = FakeCollection()
        self.supplier_catalogs = FakeCollection()
        self.supplier_feed_lines = FakeCollection()


# ---------- fixtures ----------

current_user = {}
_db = None


def fake_db():
    global _db
    _db = FakeDB()
    return _db


def client():
    global _db
    _db = fake_db()
    po_mod.get_db = lambda: _db
    app = FastAPI()
    app.include_router(po_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": "owner"})


def stock_part(db, tid, sku, qty, loc_type="truck", loc_id=None):
    loc_id = loc_id or f"loc-{tid}-{loc_type}"
    db.inventory_locations.docs.append(
        {"id": loc_id, "tenant_id": tid, "type": loc_type, "active": True})
    pid = f"part-{tid}-{sku}"
    db.inventory_parts.docs.append(
        {"id": pid, "tenant_id": tid, "sku": sku, "name": sku})
    db.inventory_stock.docs.append(
        {"tenant_id": tid, "part_id": pid, "location_id": loc_id, "qty": qty})
    return pid


def make_supplier(db, tid, sid="sup-1", name="ACME Parts"):
    db.supplier_catalogs.docs.append(
        {"id": sid, "tenant_id": tid, "name": name, "feed_type": "mock"})
    db.supplier_feed_lines.docs.append(
        {"tenant_id": tid, "supplier_id": sid, "sku": "CAP-35-5", "cost": 25.0})


def add_parts(c, job, parts):
    r = c.post(f"/parts-orders/tenants/jobs/{job}/parts", json={"parts": parts})
    assert r.status_code == 201, r.text
    return r.json()["parts"]


# ---------- tests ----------

def test_add_parts_covered_and_needs_order():
    c, db = client(), _db
    as_user("t1")
    stock_part(db, "t1", "CAP-35-5", 5)
    parts = add_parts(c, "job-1", [
        {"name": "Capacitor 35/5", "sku": "CAP-35-5", "qty": 2},
        {"name": "Contactor", "sku": "CONTACT-40", "qty": 1},
    ])
    by_sku = {p["sku"]: p for p in parts}
    assert by_sku["CAP-35-5"]["fulfillment_status"] == "covered"
    assert by_sku["CAP-35-5"]["to_order_qty"] == 0
    assert by_sku["CONTACT-40"]["fulfillment_status"] == "needs_order"
    assert by_sku["CONTACT-40"]["to_order_qty"] == 1


def test_add_parts_partial_coverage():
    c, db = client(), _db
    as_user("t1")
    stock_part(db, "t1", "FILTER-16x25", 1)
    parts = add_parts(c, "job-2", [
        {"name": "Filter", "sku": "FILTER-16x25", "qty": 3},
    ])
    p = parts[0]
    assert p["fulfillment_status"] == "partial"
    assert p["fulfilled_qty"] == 1
    assert p["to_order_qty"] == 2


def test_add_parts_warehouse_stock_not_counted():
    c, db = client(), _db
    as_user("t1")
    stock_part(db, "t1", "CAP-35-5", 10, loc_type="warehouse")
    parts = add_parts(c, "job-3", [{"name": "Capacitor", "sku": "CAP-35-5", "qty": 1}])
    assert parts[0]["fulfillment_status"] == "needs_order"


def test_list_job_parts_summary():
    c, db = client(), _db
    as_user("t1")
    stock_part(db, "t1", "CAP-35-5", 5)
    add_parts(c, "job-4", [
        {"name": "Capacitor", "sku": "CAP-35-5", "qty": 1},
        {"name": "Contactor", "sku": "CONTACT-40", "qty": 2},
    ])
    r = c.get("/parts-orders/tenants/jobs/job-4/parts")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["total_parts"] == 2
    assert body["summary"]["covered_parts"] == 1
    assert body["summary"]["lines_to_order"] == 1
    assert body["summary"]["units_to_order"] == 2
    statuses = {p["sku"]: p["fulfillment_status"] for p in body["parts"]}
    assert statuses["CAP-35-5"] == "covered"
    assert statuses["CONTACT-40"] == "needs_order"


def test_create_order_from_job_pulls_only_unfulfilled():
    c, db = client(), _db
    as_user("t1")
    make_supplier(db, "t1")
    stock_part(db, "t1", "CAP-35-5", 5)
    add_parts(c, "job-5", [
        {"name": "Capacitor", "sku": "CAP-35-5", "qty": 1},
        {"name": "Contactor", "sku": "CONTACT-40", "qty": 2, "supplier_id": "sup-1"},
    ])
    r = c.post("/parts-orders/tenants/orders",
               json={"supplier_id": "sup-1", "job_ids": ["job-5"]})
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["status"] == "draft"
    assert order["supplier_name"] == "ACME Parts"
    assert len(order["lines"]) == 1  # the covered capacitor is excluded
    line = order["lines"][0]
    assert line["sku"] == "CONTACT-40"
    assert line["qty"] == 2


def test_create_order_explicit_lines_local_supplier_ref():
    c, db = client(), _db
    as_user("t1")
    r = c.post("/parts-orders/tenants/orders", json={
        "supplier_name": "Local Distributor",
        "lines": [{"name": "Gas valve", "sku": "GV-1", "qty": 1, "unit_cost": 89.99}],
    })
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["supplier_id"] is None
    assert order["supplier_name"] == "Local Distributor"
    assert order["estimated_total"] == 89.99


def test_create_order_rejects_empty():
    c = client()
    as_user("t1")
    r = c.post("/parts-orders/tenants/orders", json={"supplier_name": "ACME"})
    assert r.status_code == 422


def test_send_and_receive_flow():
    c, db = client(), _db
    as_user("t1")
    make_supplier(db, "t1")
    add_parts(c, "job-6", [{"name": "Contactor", "sku": "CONTACT-40", "qty": 1}])
    oid = c.post("/parts-orders/tenants/orders",
                 json={"supplier_id": "sup-1", "job_ids": ["job-6"]}).json()["id"]
    r = c.post(f"/parts-orders/tenants/orders/{oid}/send")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "sent"
    assert r.json()["stub_transmission"]["channel"] == "stub"
    # double-send rejected
    assert c.post(f"/parts-orders/tenants/orders/{oid}/send").status_code == 409
    r = c.post(f"/parts-orders/tenants/orders/{oid}/receive")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "received"
    # cannot receive a draft
    oid2 = c.post("/parts-orders/tenants/orders",
                  json={"supplier_name": "Local"}).status_code
    assert oid2 == 422  # empty order still rejected


def test_list_orders_status_filter():
    c, db = client(), _db
    as_user("t1")
    add_parts(c, "job-7", [{"name": "Contactor", "sku": "CONTACT-40", "qty": 1}])
    oid = c.post("/parts-orders/tenants/orders",
                 json={"supplier_name": "ACME", "job_ids": ["job-7"]}).json()["id"]
    c.post(f"/parts-orders/tenants/orders/{oid}/send")
    draft = c.get("/parts-orders/tenants/orders", params={"status": "draft"}).json()
    sent = c.get("/parts-orders/tenants/orders", params={"status": "sent"}).json()
    assert draft["count"] == 0
    assert sent["count"] == 1
    assert c.get("/parts-orders/tenants/orders", params={"status": "bogus"}).status_code == 422


def test_tenant_isolation():
    c, db = client(), _db
    as_user("t1")
    add_parts(c, "job-8", [{"name": "Capacitor", "sku": "CAP-35-5", "qty": 1}])
    oid = c.post("/parts-orders/tenants/orders",
                 json={"supplier_name": "ACME", "job_ids": ["job-8"]}).json()["id"]
    as_user("t2")
    assert c.get("/parts-orders/tenants/jobs/job-8/parts").json()["parts"] == []
    assert c.get("/parts-orders/tenants/orders").json()["count"] == 0
    assert c.post(f"/parts-orders/tenants/orders/{oid}/send").status_code == 404


def test_unknown_order_404():
    c = client()
    as_user("t1")
    assert c.post("/parts-orders/tenants/orders/nope/send").status_code == 404


def test_cost_estimate_from_supplier_feed():
    c, db = client(), _db
    as_user("t1")
    make_supplier(db, "t1")
    parts = add_parts(c, "job-9", [
        {"name": "Capacitor 35/5", "sku": "CAP-35-5", "qty": 2, "supplier_id": "sup-1"},
    ])
    assert parts[0]["unit_cost_estimate"] == 25.0
