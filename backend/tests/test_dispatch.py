"""Unit tests for the dispatch board (routers/dispatch.py).

No database, network, or browser needed: a small FastAPI app with only the
dispatch router, a fake in-memory Mongo, and an overridden auth dependency.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "dispatch-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "dispatch_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.dispatch as dispatch_mod  # noqa: E402
import routers.call_to_payment as c2p_mod  # noqa: E402
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
        self.appointments = FakeCollection()
        self.schedule_staff = FakeCollection()
        self.invoices = FakeCollection()


# ---------- fixtures ----------

current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(dispatch_mod, "get_db", lambda: fake_db)
    monkeypatch.setattr(c2p_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(dispatch_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


async def seed_staff(db, tenant_id, sid, name="Tech One"):
    await db.schedule_staff.insert_one({"id": sid, "tenant_id": tenant_id, "name": name, "role": "technician"})


async def seed_job(db, tenant_id, jid, day="2026-10-20", hour=9, staff_id="", status="scheduled"):
    start = f"{day}T{hour:02d}:00:00+00:00"
    end = f"{day}T{hour + 1:02d}:00:00+00:00"
    await db.appointments.insert_one({
        "id": jid, "tenant_id": tenant_id, "customer_name": f"Cust {jid}",
        "customer_phone": "555-0100", "service_name": "AC Repair",
        "staff_id": staff_id, "staff": "", "start_at": start, "end_at": end,
        "status": status, "notes": "",
    })


import asyncio  # noqa: E402


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------- board ----------

def test_board_groups_unassigned_and_techs(client, fake_db):
    as_user("t1")
    run(seed_staff(fake_db, "t1", "tech1", "Amy"))
    run(seed_staff(fake_db, "t1", "tech2", "Bo"))
    run(seed_job(fake_db, "t1", "j1", hour=9))                      # unassigned
    run(seed_job(fake_db, "t1", "j2", hour=10, staff_id="tech1"))   # amy
    run(seed_job(fake_db, "t1", "j3", hour=8, staff_id="tech1"))    # amy, earlier
    run(seed_job(fake_db, "t1", "j4", day="2026-10-21", hour=9))    # other day

    r = client.get("/tenants/dispatch/board", params={"date": "2026-10-20"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [j["id"] for j in body["unassigned"]] == ["j1"]
    amy = next(t for t in body["techs"] if t["id"] == "tech1")
    assert [j["id"] for j in amy["jobs"]] == ["j3", "j2"]  # time order
    bo = next(t for t in body["techs"] if t["id"] == "tech2")
    assert bo["jobs"] == []


def test_board_tenant_isolation(client, fake_db):
    as_user("t1")
    run(seed_staff(fake_db, "t1", "tech1"))
    run(seed_job(fake_db, "t1", "jA", hour=9))
    run(seed_job(fake_db, "t2", "jB", hour=9))  # other tenant
    run(seed_staff(fake_db, "t2", "techX", "Xavier"))

    r = client.get("/tenants/dispatch/board", params={"date": "2026-10-20"})
    body = r.json()
    assert [j["id"] for j in body["unassigned"]] == ["jA"]
    assert [t["id"] for t in body["techs"]] == ["tech1"]


def test_board_bad_date(client, fake_db):
    as_user("t1")
    r = client.get("/tenants/dispatch/board", params={"date": "not-a-date"})
    assert r.status_code == 400


# ---------- assign / move ----------

def test_assign_sets_staff_and_keeps_duration(client, fake_db):
    as_user("t1")
    run(seed_staff(fake_db, "t1", "tech1", "Amy"))
    run(seed_job(fake_db, "t1", "j1", hour=9))

    r = client.post("/tenants/dispatch/assign", json={
        "job_id": "j1", "tech_id": "tech1", "start_at": "2026-10-20T14:00:00+00:00"})
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["staff_id"] == "tech1"
    assert job["staff"] == "Amy"
    assert job["start_at"] == "2026-10-20T14:00:00+00:00"
    assert job["end_at"] == "2026-10-20T15:00:00+00:00"  # 1h duration preserved


def test_assign_rejects_cross_tenant_tech(client, fake_db):
    as_user("t1")
    run(seed_staff(fake_db, "t2", "techX", "Xavier"))
    run(seed_job(fake_db, "t1", "j1", hour=9))

    r = client.post("/tenants/dispatch/assign", json={
        "job_id": "j1", "tech_id": "techX", "start_at": "2026-10-20T14:00:00+00:00"})
    assert r.status_code == 404


def test_assign_rejects_other_tenant_job(client, fake_db):
    as_user("t1")
    run(seed_staff(fake_db, "t1", "tech1", "Amy"))
    run(seed_job(fake_db, "t2", "jB", hour=9))

    r = client.post("/tenants/dispatch/assign", json={
        "job_id": "jB", "tech_id": "tech1", "start_at": "2026-10-20T14:00:00+00:00"})
    assert r.status_code == 404


def test_move_updates_tech_and_time(client, fake_db):
    as_user("t1")
    run(seed_staff(fake_db, "t1", "tech1", "Amy"))
    run(seed_staff(fake_db, "t1", "tech2", "Bo"))
    run(seed_job(fake_db, "t1", "j1", hour=9, staff_id="tech1"))

    r = client.post("/tenants/dispatch/move", json={
        "job_id": "j1", "tech_id": "tech2", "start_at": "2026-10-20T16:30:00+00:00"})
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["staff_id"] == "tech2"
    assert job["staff"] == "Bo"
    assert job["start_at"].startswith("2026-10-20T16:30")


def test_move_unassign_clears_staff(client, fake_db):
    as_user("t1")
    run(seed_job(fake_db, "t1", "j1", hour=9, staff_id="tech1"))

    r = client.post("/tenants/dispatch/move", json={"job_id": "j1", "tech_id": ""})
    assert r.status_code == 200, r.text
    assert r.json()["staff_id"] == ""

    r = client.get("/tenants/dispatch/board", params={"date": "2026-10-20"})
    assert [j["id"] for j in r.json()["unassigned"]] == ["j1"]


def test_move_completed_job_rejected(client, fake_db):
    as_user("t1")
    run(seed_staff(fake_db, "t1", "tech1", "Amy"))
    run(seed_job(fake_db, "t1", "j1", hour=9, status="completed"))

    r = client.post("/tenants/dispatch/move", json={"job_id": "j1", "tech_id": "tech1"})
    assert r.status_code == 400


# ---------- status ----------

def test_status_full_flow_to_completed(client, fake_db):
    as_user("t1")
    run(seed_job(fake_db, "t1", "j1", hour=9))

    for nxt in ["en_route", "on_site"]:
        r = client.post("/tenants/dispatch/status", json={"job_id": "j1", "status": nxt})
        assert r.status_code == 200, (nxt, r.text)
        assert r.json()["status"] == nxt

    r = client.post("/tenants/dispatch/status", json={"job_id": "j1", "status": "completed"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"  # via shared c2p completion logic


def test_status_invalid_transitions_rejected(client, fake_db):
    as_user("t1")
    run(seed_job(fake_db, "t1", "j1", hour=9))
    run(seed_job(fake_db, "t1", "j2", hour=10, status="completed"))

    # scheduled -> completed skips the field workflow
    r = client.post("/tenants/dispatch/status", json={"job_id": "j1", "status": "completed"})
    assert r.status_code == 400

    # completed is terminal
    r = client.post("/tenants/dispatch/status", json={"job_id": "j2", "status": "en_route"})
    assert r.status_code == 400

    # unknown status rejected by schema
    r = client.post("/tenants/dispatch/status", json={"job_id": "j1", "status": "flying"})
    assert r.status_code == 422


def test_status_other_tenant_job_hidden(client, fake_db):
    as_user("t1")
    run(seed_job(fake_db, "t2", "jB", hour=9))

    r = client.post("/tenants/dispatch/status", json={"job_id": "jB", "status": "en_route"})
    assert r.status_code == 404
