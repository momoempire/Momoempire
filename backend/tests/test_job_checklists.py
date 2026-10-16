"""Unit tests for custom job forms / checklists (#36,
routers/checklists.py).

Zero network, zero database: a small FastAPI app with only the checklists
router, a fake in-memory Mongo, and an overridden auth dependency.

Coverage:
- Template CRUD (create / list / update / delete), item type validation.
- Default seeding: 3 trade defaults appear on first use and are idempotent.
- Instance creation against a real (fake) job; 404 for unknown jobs.
- Item completion with values/notes/photo urls; required-item evidence rules.
- Gate: blocks while required items are open, passes when all are done.
- Tenant isolation: templates and instances never leak across tenants.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "checklists-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "checklists_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.checklists as cl_mod  # noqa: E402
from security import require_tenant_user  # noqa: E402


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

    async def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if _match(d, query):
                del self.docs[i]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


class FakeDB:
    def __init__(self):
        self.checklist_templates = FakeCollection()
        self.checklist_instances = FakeCollection()
        self.appointments = FakeCollection()


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
    monkeypatch.setattr(cl_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(cl_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, uid=None):
    current_user.clear()
    current_user.update({"id": uid or f"u-{tenant_id}", "tenant_id": tenant_id,
                         "role": "owner"})


def make_job(db, job_id, tid="t1"):
    db.appointments.docs.append({"id": job_id, "tenant_id": tid,
                                 "customer_name": "Cust"})


# ---------- default seeding ----------

def test_defaults_seed_on_first_list_and_are_idempotent(client, fake_db):
    as_user("t1")
    r = client.get("/checklists/templates")
    assert r.status_code == 200
    body = r.json()
    names = {t["name"] for t in body["templates"]}
    assert names == {"HVAC install QC", "Plumbing safety check", "General job closeout"}
    hvac = next(t for t in body["templates"] if t["trade"] == "hvac")
    assert hvac["builtin"] is True
    assert any(i["required"] and i["type"] == "check" for i in hvac["items"])

    first_ids = [t["id"] for t in body["templates"]]
    # Second call must NOT create duplicates.
    r2 = client.get("/checklists/templates")
    assert [t["id"] for t in r2.json()["templates"]] == first_ids
    assert r2.json()["count"] == 3


def test_seeding_is_per_tenant(client, fake_db):
    as_user("t1")
    client.get("/checklists/templates")
    as_user("t2")
    body = client.get("/checklists/templates").json()
    assert body["count"] == 3  # seeded for t2 independently
    assert all(t["id"] not in {d["id"] for d in fake_db.checklist_templates.docs
                               if d["tenant_id"] == "t1"} for t in body["templates"])


# ---------- template CRUD ----------

def test_template_crud_roundtrip(client, fake_db):
    as_user("t1")
    r = client.post("/checklists/templates", json={
        "name": "My Custom Form", "trade": "roofing",
        "items": [
            {"label": "Shingles match", "type": "check", "required": True},
            {"label": "Nail count", "type": "number"},
        ],
    })
    assert r.status_code == 201
    created = r.json()
    assert created["name"] == "My Custom Form"
    assert created["trade"] == "roofing"
    assert created["builtin"] is False
    assert len(created["items"]) == 2
    assert created["items"][0]["required"] is True
    item_ids = {i["id"] for i in created["items"]}
    assert len(item_ids) == 2  # unique item ids

    tid = created["id"]
    got = client.get("/checklists/templates").json()
    assert any(t["id"] == tid for t in got["templates"])

    upd = client.put(f"/checklists/templates/{tid}", json={
        "name": "Renamed Form",
        "items": [{"label": "Only item", "type": "text", "required": True}],
    }).json()
    assert upd["name"] == "Renamed Form"
    assert len(upd["items"]) == 1
    assert upd["items"][0]["label"] == "Only item"

    assert client.delete(f"/checklists/templates/{tid}").json()["ok"] is True
    assert not any(t["id"] == tid for t in client.get("/checklists/templates").json()["templates"])


def test_builtin_templates_cannot_be_deleted(client, fake_db):
    as_user("t1")
    builtin = client.get("/checklists/templates").json()["templates"][0]["id"]
    r = client.delete(f"/checklists/templates/{builtin}")
    assert r.status_code == 400
    assert client.get("/checklists/templates").json()["count"] == 3


def test_template_validation(client, fake_db):
    as_user("t1")
    assert client.post("/checklists/templates", json={"name": "  "}).status_code == 400
    r = client.post("/checklists/templates", json={
        "name": "Bad", "items": [{"label": "x", "type": "bogus"}]})
    assert r.status_code == 400
    r = client.post("/checklists/templates", json={
        "name": "Bad", "items": [{"label": "   ", "type": "check"}]})
    assert r.status_code == 400
    r = client.put("/checklists/templates/missing", json={"name": "x"})
    assert r.status_code == 404


def test_template_trade_filter(client, fake_db):
    as_user("t1")
    body = client.get("/checklists/templates", params={"trade": "hvac"}).json()
    assert body["count"] == 1
    assert body["templates"][0]["name"] == "HVAC install QC"


# ---------- instances ----------

def test_instance_creation_and_job_verification(client, fake_db):
    as_user("t1")
    make_job(fake_db, "job-1")
    template = client.get("/checklists/templates", params={"trade": "hvac"}).json()["templates"][0]

    r = client.post("/checklists/instances",
                    json={"template_id": template["id"], "job_id": "job-1"})
    assert r.status_code == 201
    inst = r.json()
    assert inst["template_name"] == "HVAC install QC"
    assert inst["job_id"] == "job-1"
    assert len(inst["items"]) == len(template["items"])
    assert all(i["done"] is False for i in inst["items"])
    assert inst["progress"] == {"required_done": 0,
                               "required_total": sum(1 for i in template["items"] if i["required"])}

    # Unknown job -> 404, unknown template -> 404, other tenant's job -> 404.
    assert client.post("/checklists/instances",
                       json={"template_id": template["id"], "job_id": "nope"}).status_code == 404
    assert client.post("/checklists/instances",
                       json={"template_id": "clt_nope", "job_id": "job-1"}).status_code == 404
    make_job(fake_db, "job-x", tid="t2")
    assert client.post("/checklists/instances",
                       json={"template_id": template["id"], "job_id": "job-x"}).status_code == 404


def test_list_instances_by_job(client, fake_db):
    as_user("t1")
    make_job(fake_db, "job-1")
    make_job(fake_db, "job-2")
    tpl = client.get("/checklists/templates").json()["templates"][0]["id"]
    client.post("/checklists/instances", json={"template_id": tpl, "job_id": "job-1"})
    client.post("/checklists/instances", json={"template_id": tpl, "job_id": "job-2"})

    body = client.get("/checklists/instances", params={"job_id": "job-1"}).json()
    assert body["count"] == 1 and body["instances"][0]["job_id"] == "job-1"
    assert client.get("/checklists/instances").json()["count"] == 2

    inst_id = body["instances"][0]["id"]
    single = client.get(f"/checklists/instances/{inst_id}").json()
    assert single["id"] == inst_id
    assert client.get("/checklists/instances/nope").status_code == 404


def test_instance_delete(client, fake_db):
    as_user("t1")
    make_job(fake_db, "job-1")
    tpl = client.get("/checklists/templates").json()["templates"][0]["id"]
    inst = client.post("/checklists/instances",
                       json={"template_id": tpl, "job_id": "job-1"}).json()
    assert client.delete(f"/checklists/instances/{inst['id']}").json()["ok"] is True
    assert client.get("/checklists/instances").json()["count"] == 0


# ---------- item completion ----------

def _start_instance(client, fake_db, trade="hvac", job="job-1"):
    as_user("t1")
    make_job(fake_db, job)
    tpl = client.get("/checklists/templates", params={"trade": trade}).json()["templates"][0]
    return client.post("/checklists/instances",
                       json={"template_id": tpl["id"], "job_id": job}).json()


def test_complete_check_item_with_progress(client, fake_db):
    inst = _start_instance(client, fake_db)
    required_check = next(i for i in inst["items"] if i["type"] == "check" and i["required"])

    r = client.post(f"/checklists/instances/{inst['id']}/items/{required_check['item_id']}/complete",
                    json={"done": True})
    assert r.status_code == 200
    body = r.json()
    done_item = next(i for i in body["items"] if i["item_id"] == required_check["item_id"])
    assert done_item["done"] is True
    assert done_item["completed_at"] is not None
    assert done_item["completed_by"] == "u-t1"
    assert body["progress"]["required_done"] == 1

    # Re-open it.
    r = client.post(f"/checklists/instances/{inst['id']}/items/{required_check['item_id']}/complete",
                    json={"done": False})
    assert r.status_code == 200
    reopened = next(i for i in r.json()["items"] if i["item_id"] == required_check["item_id"])
    assert reopened["done"] is False and reopened["completed_at"] is None


def test_required_item_evidence_rules(client, fake_db):
    inst = _start_instance(client, fake_db)
    required_photo = next(i for i in inst["items"] if i["type"] == "photo" and i["required"])

    # Required photo without a photo_url -> rejected.
    r = client.post(f"/checklists/instances/{inst['id']}/items/{required_photo['item_id']}/complete",
                    json={"done": True})
    assert r.status_code == 400

    # With a photo_url -> accepted, note persisted.
    r = client.post(f"/checklists/instances/{inst['id']}/items/{required_photo['item_id']}/complete",
                    json={"done": True, "photo_url": "https://img.example/p.jpg", "note": "north side"})
    assert r.status_code == 200
    done = next(i for i in r.json()["items"] if i["item_id"] == required_photo["item_id"])
    assert done["photo_url"] == "https://img.example/p.jpg"
    assert done["note"] == "north side"


def test_required_text_number_need_value(client, fake_db):
    as_user("t1")
    make_job(fake_db, "job-1")
    tpl = client.post("/checklists/templates", json={
        "name": "Meter readings",
        "items": [
            {"label": "Meter number", "type": "text", "required": True},
            {"label": "Reading", "type": "number", "required": True},
        ],
    }).json()
    inst = client.post("/checklists/instances",
                       json={"template_id": tpl["id"], "job_id": "job-1"}).json()

    text_item = inst["items"][0]
    r = client.post(f"/checklists/instances/{inst['id']}/items/{text_item['item_id']}/complete",
                    json={"done": True})
    assert r.status_code == 400  # required text without a value
    r = client.post(f"/checklists/instances/{inst['id']}/items/{text_item['item_id']}/complete",
                    json={"done": True, "value": "M-12345"})
    assert r.status_code == 200
    assert next(i for i in r.json()["items"]
                if i["item_id"] == text_item["item_id"])["value"] == "M-12345"

    # Re-opening a required item (done=false) is allowed; the gate re-blocks.
    check_item = inst["items"][1]
    r = client.post(f"/checklists/instances/{inst['id']}/items/{check_item['item_id']}/complete",
                    json={"done": False})
    assert r.status_code == 200
    assert next(i for i in r.json()["items"]
                if i["item_id"] == check_item["item_id"])["done"] is False


def test_optional_items_complete_without_evidence(client, fake_db):
    inst = _start_instance(client, fake_db)
    optional = next(i for i in inst["items"] if not i["required"])
    r = client.post(f"/checklists/instances/{inst['id']}/items/{optional['item_id']}/complete",
                    json={"done": True})
    assert r.status_code == 200
    assert next(i for i in r.json()["items"]
                if i["item_id"] == optional["item_id"])["done"] is True


def test_complete_unknown_item_404(client, fake_db):
    inst = _start_instance(client, fake_db)
    r = client.post(f"/checklists/instances/{inst['id']}/items/it_nope/complete",
                    json={"done": True})
    assert r.status_code == 404


# ---------- gate ----------

def test_gate_blocks_then_passes(client, fake_db):
    inst = _start_instance(client, fake_db)
    gate = client.get(f"/checklists/instances/{inst['id']}/gate").json()
    assert gate["can_complete"] is False
    assert len(gate["blocking"]) == gate["progress"]["required_total"] > 0

    # Complete every required item (supplying evidence where needed).
    for item in gate["blocking"]:
        it = next(i for i in inst["items"] if i["label"] == item)
        payload = {"done": True}
        if it["type"] == "photo":
            payload["photo_url"] = "https://img.example/x.jpg"
        r = client.post(f"/checklists/instances/{inst['id']}/items/{it['item_id']}/complete",
                        json=payload)
        assert r.status_code == 200, f"completing {item} failed: {r.text}"
        inst = r.json()

    gate = client.get(f"/checklists/instances/{inst['id']}/gate").json()
    assert gate["can_complete"] is True
    assert gate["blocking"] == []
    assert gate["progress"]["required_done"] == gate["progress"]["required_total"]


def test_gate_reblocks_when_item_reopened(client, fake_db):
    inst = _start_instance(client, fake_db)
    first = next(i for i in inst["items"] if i["required"])
    r = client.post(f"/checklists/instances/{inst['id']}/items/{first['item_id']}/complete",
                    json={"done": True})
    assert r.status_code == 200
    gate = client.get(f"/checklists/instances/{inst['id']}/gate").json()
    assert first["label"] not in gate["blocking"]  # done, so not blocking

    client.post(f"/checklists/instances/{inst['id']}/items/{first['item_id']}/complete",
                json={"done": False})
    gate = client.get(f"/checklists/instances/{inst['id']}/gate").json()
    assert gate["can_complete"] is False
    assert first["label"] in gate["blocking"]


# ---------- tenant isolation ----------

def test_tenant_isolation(client, fake_db):
    as_user("t1")
    make_job(fake_db, "job-1")
    tpl = client.get("/checklists/templates", params={"trade": "hvac"}).json()["templates"][0]
    inst = client.post("/checklists/instances",
                       json={"template_id": tpl["id"], "job_id": "job-1"}).json()

    as_user("t2")
    # t2 sees only its own seeded templates; t1's instance is invisible.
    assert all(t["id"] != tpl["id"] for t in client.get("/checklists/templates").json()["templates"])
    assert client.get("/checklists/instances").json()["count"] == 0
    assert client.get(f"/checklists/instances/{inst['id']}").status_code == 404
    assert client.get(f"/checklists/instances/{inst['id']}/gate").status_code == 404
    r = client.post(f"/checklists/instances/{inst['id']}/items/x/complete", json={"done": True})
    assert r.status_code == 404
    # t2 cannot create an instance from t1's template.
    make_job(fake_db, "job-9", tid="t2")
    assert client.post("/checklists/instances",
                       json={"template_id": tpl["id"], "job_id": "job-9"}).status_code == 404
