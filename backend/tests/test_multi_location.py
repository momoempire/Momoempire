"""Unit tests for multi-location management (routers/locations.py).

Zero network, zero database: a small FastAPI app with only the locations
router, a fake in-memory Mongo, and overridden auth dependencies.

Coverage:
- Default "Main" location is lazily auto-provisioned and idempotent.
- Assignment mapping (job/tech/equipment), re-assignment moves, validation.
- Per-location job filtering and dashboard rollups (revenue from invoices,
  excluding void/draft; jobs count; active techs).
- Consolidated totals always equal the sum of the per-location rows.
- Tenant isolation: tenant B cannot see or touch tenant A's locations.
- /consolidated is owner/admin only (tech role -> 403).
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "multiloc-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "multiloc_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.locations as loc_mod  # noqa: E402
from security import require_tenant_user, require_tenant_owner_or_admin  # noqa: E402


# ---------- fake mongo ----------

class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

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
                elif op == "$nin" and val in opval:
                    return False
                elif op not in ("$in", "$ne", "$nin"):
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
        matched = 0
        for d in self.docs:
            if _match(d, query):
                matched += 1
                for k, v in update.get("$set", {}).items():
                    d[k] = v
                break
        if matched == 0 and upsert:
            new_doc = {k: v for k, v in query.items() if not isinstance(v, dict)}
            for k, v in update.get("$set", {}).items():
                new_doc[k] = v
            self.docs.append(new_doc)
            matched = 1
        return SimpleNamespace(matched_count=matched, modified_count=matched,
                               upserted_id=None)

    async def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if _match(d, query):
                del self.docs[i]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


class FakeDB:
    def __init__(self):
        self.locations = FakeCollection()
        self.location_assignments = FakeCollection()
        self.appointments = FakeCollection()
        self.schedule_staff = FakeCollection()
        self.equipment = FakeCollection()
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
    monkeypatch.setattr(loc_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(loc_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def make_job(db, tid, jid, status="scheduled", start_at="2026-10-10T09:00:00"):
    db.appointments.docs.append({
        "id": jid, "tenant_id": tid, "customer_name": "Cust",
        "service_name": "Tune-up", "status": status, "start_at": start_at,
    })


def make_invoice(db, tid, iid, job_id, total, status="paid"):
    db.invoices.docs.append({
        "id": iid, "tenant_id": tid, "appointment_id": job_id,
        "total": total, "status": status,
    })


def make_tech(db, tid, tech_id, name="Tech"):
    db.schedule_staff.docs.append({"id": tech_id, "tenant_id": tid, "name": name})


def make_equipment(db, tid, eq_id):
    db.equipment.docs.append({"id": eq_id, "tenant_id": tid, "equipment_type": "hvac"})


def loc_id_for(db, tid, name):
    for d in db.locations.docs:
        if d["tenant_id"] == tid and d["name"] == name:
            return d["id"]
    raise AssertionError(f"no location {name} for {tid}")


# ---------- default location auto-provision ----------

def test_default_location_auto_provisioned_on_first_get(client, fake_db):
    as_user("t1")
    r = client.get("/locations/")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    loc = body["locations"][0]
    assert loc["name"] == "Main"
    assert loc["is_default"] is True
    assert loc["active"] is True
    assert loc["tenant_id"] == "t1"


def test_default_provision_idempotent(client, fake_db):
    as_user("t1")
    client.get("/locations/")
    body = client.get("/locations/").json()
    assert body["count"] == 1, "second GET must not create another Main"

    # Creating a real location then listing again: still exactly one Main.
    r = client.post("/locations/", json={"name": "Downtown", "city": "X"})
    assert r.status_code == 200
    mains = [l for l in client.get("/locations/").json()["locations"]
             if l["is_default"]]
    assert len(mains) == 1


def test_create_update_deactivate_location(client, fake_db):
    as_user("t1")
    r = client.post("/locations/", json={
        "name": "North Branch",
        "address": {"street": "1 Main St", "city": "Dallas", "state": "TX", "zip": "75201"},
        "timezone": "America/Chicago", "phone": "555-0100", "manager": "Sam",
    })
    assert r.status_code == 200
    created = r.json()
    assert created["address"]["city"] == "Dallas"
    assert created["is_default"] is False
    lid = created["id"]

    r = client.put(f"/locations/{lid}", json={"manager": "Alex", "phone": "555-0200"})
    assert r.status_code == 200
    assert r.json()["manager"] == "Alex"

    r = client.delete(f"/locations/{lid}")
    assert r.status_code == 200
    assert r.json()["active"] is False
    # Still listed (soft delete), marked inactive.
    locs = {l["id"]: l for l in client.get("/locations/").json()["locations"]}
    assert locs[lid]["active"] is False


def test_create_requires_name(client, fake_db):
    as_user("t1")
    r = client.post("/locations/", json={"name": ""})
    assert r.status_code == 422


def test_update_unknown_location_404(client, fake_db):
    as_user("t1")
    r = client.put("/locations/nope", json={"manager": "x"})
    assert r.status_code == 404


# ---------- assignment mapping ----------

def test_assign_job_and_filter_jobs(client, fake_db):
    as_user("t1")
    client.get("/locations/")  # provision Main
    main = loc_id_for(fake_db, "t1", "Main")
    north = client.post("/locations/", json={"name": "North"}).json()["id"]

    make_job(fake_db, "t1", "j1")
    make_job(fake_db, "t1", "j2")

    r = client.post("/locations/assign", json={"entity": "job", "entity_id": "j1", "location_id": north})
    assert r.status_code == 200
    assert r.json()["location_id"] == north

    north_jobs = client.get(f"/locations/{north}/jobs").json()
    assert north_jobs["count"] == 1
    assert north_jobs["jobs"][0]["id"] == "j1"

    main_jobs = client.get(f"/locations/{main}/jobs").json()
    assert main_jobs["count"] == 0


def test_reassign_moves_entity(client, fake_db):
    as_user("t1")
    north = client.post("/locations/", json={"name": "North"}).json()["id"]
    south = client.post("/locations/", json={"name": "South"}).json()["id"]
    make_job(fake_db, "t1", "j1")

    client.post("/locations/assign", json={"entity": "job", "entity_id": "j1", "location_id": north})
    client.post("/locations/assign", json={"entity": "job", "entity_id": "j1", "location_id": south})

    assert client.get(f"/locations/{north}/jobs").json()["count"] == 0
    assert client.get(f"/locations/{south}/jobs").json()["count"] == 1
    # One mapping doc per entity: no duplicates.
    assert len(fake_db.location_assignments.docs) == 1


def test_assign_tech_and_equipment(client, fake_db):
    as_user("t1")
    north = client.post("/locations/", json={"name": "North"}).json()["id"]
    make_tech(fake_db, "t1", "tech1")
    make_equipment(fake_db, "t1", "eq1")

    r = client.post("/locations/assign", json={"entity": "tech", "entity_id": "tech1", "location_id": north})
    assert r.status_code == 200
    r = client.post("/locations/assign", json={"entity": "equipment", "entity_id": "eq1", "location_id": north})
    assert r.status_code == 200

    dash = client.get(f"/locations/{north}/dashboard").json()
    assert dash["active_techs"] == 1


def test_unassign(client, fake_db):
    as_user("t1")
    north = client.post("/locations/", json={"name": "North"}).json()["id"]
    make_job(fake_db, "t1", "j1")
    client.post("/locations/assign", json={"entity": "job", "entity_id": "j1", "location_id": north})
    assert client.get(f"/locations/{north}/jobs").json()["count"] == 1

    r = client.delete("/locations/assign", params={"entity": "job", "entity_id": "j1"})
    assert r.json() == {"ok": True, "removed": True}
    assert client.get(f"/locations/{north}/jobs").json()["count"] == 0


def test_assign_validation(client, fake_db):
    as_user("t1")
    north = client.post("/locations/", json={"name": "North"}).json()["id"]
    make_job(fake_db, "t1", "j1")

    # Unknown entity type.
    r = client.post("/locations/assign", json={"entity": "spaceship", "entity_id": "j1", "location_id": north})
    assert r.status_code == 400

    # Unknown entity id.
    r = client.post("/locations/assign", json={"entity": "job", "entity_id": "ghost", "location_id": north})
    assert r.status_code == 404

    # Unknown location id.
    r = client.post("/locations/assign", json={"entity": "job", "entity_id": "j1", "location_id": "ghost"})
    assert r.status_code == 404


# ---------- per-location dashboard ----------

def test_per_location_dashboard_rollup(client, fake_db):
    as_user("t1")
    north = client.post("/locations/", json={"name": "North"}).json()["id"]
    south = client.post("/locations/", json={"name": "South"}).json()["id"]

    make_job(fake_db, "t1", "j1", status="completed")
    make_job(fake_db, "t1", "j2", status="scheduled")
    make_job(fake_db, "t1", "j3", status="completed")  # unassigned
    make_invoice(fake_db, "t1", "i1", "j1", 500.0, status="paid")
    make_invoice(fake_db, "t1", "i2", "j2", 200.0, status="sent")
    make_invoice(fake_db, "t1", "i3", "j1", 50.0, status="void")     # excluded
    make_invoice(fake_db, "t1", "i4", "j2", 75.0, status="draft")    # excluded
    make_invoice(fake_db, "t1", "i5", "j3", 999.0, status="paid")    # unassigned job
    make_tech(fake_db, "t1", "tech1")
    make_tech(fake_db, "t1", "tech2")

    client.post("/locations/assign", json={"entity": "job", "entity_id": "j1", "location_id": north})
    client.post("/locations/assign", json={"entity": "job", "entity_id": "j2", "location_id": north})
    client.post("/locations/assign", json={"entity": "tech", "entity_id": "tech1", "location_id": north})
    client.post("/locations/assign", json={"entity": "tech", "entity_id": "tech2", "location_id": south})

    dash = client.get(f"/locations/{north}/dashboard").json()
    assert dash["jobs_count"] == 2
    assert dash["revenue"] == 700.0          # 500 + 200; void and draft excluded
    assert dash["active_techs"] == 1
    assert dash["jobs_by_status"] == {"completed": 1, "scheduled": 1}

    dash_s = client.get(f"/locations/{south}/dashboard").json()
    assert dash_s["jobs_count"] == 0
    assert dash_s["revenue"] == 0.0          # j3's invoice is unassigned, not South's
    assert dash_s["active_techs"] == 1


# ---------- consolidated ----------

def test_consolidated_totals_equal_sum_of_parts(client, fake_db):
    as_user("t1")
    north = client.post("/locations/", json={"name": "North"}).json()["id"]
    south = client.post("/locations/", json={"name": "South"}).json()["id"]
    main = loc_id_for(fake_db, "t1", "Main")

    make_job(fake_db, "t1", "j1")
    make_job(fake_db, "t1", "j2")
    make_job(fake_db, "t1", "j3")
    make_invoice(fake_db, "t1", "i1", "j1", 100.0)
    make_invoice(fake_db, "t1", "i2", "j2", 250.0)
    make_invoice(fake_db, "t1", "i3", "j3", 400.0)   # Main's job
    make_tech(fake_db, "t1", "tech1")
    make_tech(fake_db, "t1", "tech2")

    client.post("/locations/assign", json={"entity": "job", "entity_id": "j1", "location_id": north})
    client.post("/locations/assign", json={"entity": "job", "entity_id": "j2", "location_id": south})
    client.post("/locations/assign", json={"entity": "job", "entity_id": "j3", "location_id": main})
    client.post("/locations/assign", json={"entity": "tech", "entity_id": "tech1", "location_id": north})
    client.post("/locations/assign", json={"entity": "tech", "entity_id": "tech2", "location_id": north})

    body = client.get("/locations/consolidated").json()
    assert body["location_count"] == 3
    rows = {r["name"]: r for r in body["locations"]}

    assert rows["North"]["jobs_count"] == 1
    assert rows["North"]["revenue"] == 100.0
    assert rows["North"]["active_techs"] == 2
    assert rows["South"]["revenue"] == 250.0
    assert rows["Main"]["revenue"] == 400.0

    totals = body["totals"]
    assert totals["jobs_count"] == sum(r["jobs_count"] for r in body["locations"])
    assert totals["revenue"] == sum(r["revenue"] for r in body["locations"])
    assert totals["revenue"] == 750.0
    assert totals["active_techs"] == sum(r["active_techs"] for r in body["locations"])


def test_consolidated_reports_unassigned_separately(client, fake_db):
    as_user("t1")
    north = client.post("/locations/", json={"name": "North"}).json()["id"]
    make_job(fake_db, "t1", "j1")   # assigned
    make_job(fake_db, "t1", "j2")   # unassigned
    make_invoice(fake_db, "t1", "i1", "j1", 100.0)
    make_invoice(fake_db, "t1", "i2", "j2", 300.0)
    client.post("/locations/assign", json={"entity": "job", "entity_id": "j1", "location_id": north})

    body = client.get("/locations/consolidated").json()
    assert body["unassigned"] == {"jobs_count": 1, "revenue": 300.0}
    # Totals still equal the sum of per-location parts, NOT including unassigned.
    assert body["totals"]["revenue"] == 100.0
    assert body["totals"]["jobs_count"] == 1


def test_consolidated_requires_owner_or_admin(client, fake_db):
    as_user("t1", role="owner")
    assert client.get("/locations/consolidated").status_code == 200

    as_user("t1", role="admin")
    assert client.get("/locations/consolidated").status_code == 200

    as_user("t1", role="tech")
    r = client.get("/locations/consolidated")
    assert r.status_code == 403

    as_user("t1", role="dispatcher")
    assert client.get("/locations/consolidated").status_code == 403


# ---------- tenant isolation ----------

def test_tenant_isolation(client, fake_db):
    as_user("tA")
    north_a = client.post("/locations/", json={"name": "North"}).json()["id"]
    make_job(fake_db, "tA", "ja1")
    client.post("/locations/assign", json={"entity": "job", "entity_id": "ja1", "location_id": north_a})

    as_user("tB")
    # B's first use provisions B's own Main — A's locations are invisible.
    body = client.get("/locations/").json()
    assert body["count"] == 1
    assert body["locations"][0]["name"] == "Main"
    assert all(l["tenant_id"] == "tB" for l in body["locations"])

    # A's location id is invisible to B: 404 like a missing one.
    assert client.get(f"/locations/{north_a}/jobs").status_code == 404
    assert client.get(f"/locations/{north_a}/dashboard").status_code == 404
    assert client.put(f"/locations/{north_a}", json={"name": "X"}).status_code == 404

    # B cannot steal A's job into B's location: entity lookup is tenant-scoped.
    main_b = body["locations"][0]["id"]
    r = client.post("/locations/assign", json={"entity": "job", "entity_id": "ja1", "location_id": main_b})
    assert r.status_code == 404

    # B's consolidated sees only B's (empty) numbers.
    cons = client.get("/locations/consolidated").json()
    assert cons["totals"] == {"jobs_count": 0, "revenue": 0.0, "active_techs": 0}
    assert cons["location_count"] == 1
    assert cons["locations"][0]["name"] == "Main"
    assert "North" not in {l["name"] for l in cons["locations"]}
