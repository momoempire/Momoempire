"""Tests for routers/home_reports.py (AI home report after every visit).

Uses a fake in-memory mongo (same pattern as test_reputation.py): no live
database. Covers: generate from a completed job, rejected generate for a
non-completed job, dedupe (one report per job), before/after photo refs (no
image bytes leak), equipment snapshot, template recommendations with
priorities, public magic-link view without auth, invalid token 404, view
tracking on public access, send + send-event tracking, events endpoint, and
tenant isolation.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "home-reports-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "home_reports_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.home_reports as hr_mod  # noqa: E402
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
        self.home_reports = FakeCollection()
        self.home_report_events = FakeCollection()
        self.appointments = FakeCollection()
        self.photo_estimates = FakeCollection()
        self.equipment = FakeCollection()
        self.tenants = FakeCollection()


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(hr_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(hr_mod.router)
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


def seed_job(fake_db, tenant_id="t1", job_id="job-1", status="completed",
             customer_id="c-1", customer_phone="555-0100", service="HVAC furnace tune-up"):
    fake_db.appointments.docs.append({
        "id": job_id, "tenant_id": tenant_id, "status": status,
        "customer_id": customer_id, "customer_phone": customer_phone,
        "customer_name": "Kim Lee", "service_name": service,
        "staff": "Tech Sam", "notes": "Replaced filter, checked burner.",
        "completed_at": "2026-10-15T18:00:00+00:00",
    })


def seed_photo(fake_db, tenant_id="t1", pid="photo-1", filename="before.jpg"):
    fake_db.photo_estimates.docs.append({
        "id": pid, "tenant_id": tenant_id, "filename": filename,
        "content_type": "image/jpeg", "size_bytes": 12345,
        "image_bytes": b"\x89PNG-fake-bytes",  # must NEVER leak into reports
        "trade": "hvac", "notes": "", "status": "uploaded",
        "created_at": "2026-10-15T17:00:00+00:00",
    })


def seed_equipment(fake_db, tenant_id="t1", customer_id="c-1", install="2008-01-01T00:00:00+00:00"):
    fake_db.equipment.docs.append({
        "id": "eq-1", "tenant_id": tenant_id, "customer_id": customer_id,
        "equipment_type": "furnace", "make": "Carrier", "model": "58MC",
        "serial_number": "SN123", "install_date": install, "active": True,
    })


def _gen(client, job_id="job-1", **kw):
    payload = {"job_id": job_id, "technician_notes": "All good."}
    payload.update(kw)
    return client.post("/api/home-reports/generate", json=payload)


# ---------- generate ----------

def test_generate_from_completed_job(client, fake_db):
    as_user()
    seed_job(fake_db)
    r = _gen(client)
    assert r.status_code == 200
    b = r.json()
    assert b["ok"] and b["duplicate"] is False
    assert b["share_token"].startswith("hr_")
    rep = b["report"]
    assert rep["service_summary"] == "HVAC furnace tune-up"
    assert rep["customer_name"] == "Kim Lee"
    assert rep["technician"] == "Tech Sam"
    assert rep["technician_notes"] == "All good."
    assert rep["status"] == "draft"
    assert len(fake_db.home_reports.docs) == 1
    # a "created" event is recorded
    assert fake_db.home_report_events.docs[0]["type"] == "created"


def test_generate_rejects_non_completed_job(client, fake_db):
    as_user()
    seed_job(fake_db, job_id="job-2", status="scheduled")
    r = _gen(client, job_id="job-2")
    assert r.status_code == 409


def test_generate_missing_job_404(client):
    as_user()
    r = _gen(client, job_id="nope")
    assert r.status_code == 404


def test_generate_dedupe_one_report_per_job(client, fake_db):
    as_user()
    seed_job(fake_db)
    r1 = _gen(client)
    r2 = _gen(client)
    assert r1.json()["duplicate"] is False
    assert r2.json()["duplicate"] is True
    assert r2.json()["id"] == r1.json()["id"]
    assert r2.json()["share_token"] == r1.json()["share_token"]
    assert len(fake_db.home_reports.docs) == 1


def test_generate_non_owner_forbidden(client, fake_db):
    as_user(role="tech")
    seed_job(fake_db)
    r = _gen(client)
    assert r.status_code == 403


# ---------- photos & equipment ----------

def test_photo_refs_validated_and_bytes_never_leak(client, fake_db):
    as_user()
    seed_job(fake_db)
    seed_photo(fake_db, pid="before-1", filename="before.jpg")
    seed_photo(fake_db, pid="after-1", filename="after.jpg")
    r = _gen(client, before_photo_ids=["before-1"], after_photo_ids=["after-1"])
    assert r.status_code == 200
    photos = r.json()["report"]["photos"]
    assert photos["before"][0]["filename"] == "before.jpg"
    assert photos["after"][0]["filename"] == "after.jpg"
    for shot in photos["before"] + photos["after"]:
        assert "image_bytes" not in shot


def test_photo_ref_unknown_id_404(client, fake_db):
    as_user()
    seed_job(fake_db)
    r = _gen(client, before_photo_ids=["ghost"])
    assert r.status_code == 404
    assert len(fake_db.home_reports.docs) == 0


def test_equipment_snapshot_included(client, fake_db):
    as_user()
    seed_job(fake_db)
    seed_equipment(fake_db)
    r = _gen(client)
    snap = r.json()["report"]["equipment_snapshot"]
    assert len(snap) == 1
    assert snap[0]["equipment_type"] == "furnace"
    assert snap[0]["make"] == "Carrier"
    assert snap[0]["health"] == "aging — consider replacement planning"


def test_equipment_snapshot_matches_phone_when_no_customer_id(client, fake_db):
    as_user()
    seed_job(fake_db, customer_id="", customer_phone="555-0100")
    fake_db.equipment.docs.append({
        "id": "eq-9", "tenant_id": "t1", "customer_id": "",
        "customer_phone": "555-0100", "equipment_type": "water heater",
        "make": "Rheem", "model": "", "serial_number": "",
        "install_date": "2024-05-01T00:00:00+00:00", "active": True,
    })
    r = _gen(client)
    snap = r.json()["report"]["equipment_snapshot"]
    assert len(snap) == 1
    assert snap[0]["health"] == "healthy"


def test_equipment_snapshot_scoped_to_tenant(client, fake_db):
    as_user(tenant_id="t1")
    seed_job(fake_db, tenant_id="t1", customer_id="c-shared")
    seed_equipment(fake_db, tenant_id="t2", customer_id="c-shared")
    r = _gen(client)
    assert r.json()["report"]["equipment_snapshot"] == []


# ---------- recommendations ----------

def test_recommendations_have_priorities_and_labels(client, fake_db):
    as_user()
    seed_job(fake_db, service="HVAC furnace tune-up")
    r = _gen(client)
    recs = r.json()["report"]["recommendations"]
    assert recs, "expected at least the generic recommendations"
    prios = {x["priority"] for x in recs}
    assert prios <= {"high", "medium", "low"}
    assert all(x.get("generated_by") == "template-recs-v1" for x in recs)
    # furnace keywords -> a high-priority safety rec
    highs = [x for x in recs if x["priority"] == "high"]
    assert any("safety" in x["title"].lower() or "detector" in x["title"].lower() for x in highs)


def test_recommendations_generic_for_unknown_trade(client, fake_db):
    as_user()
    seed_job(fake_db, job_id="job-gen", service="Miscellaneous handyman visit")
    r = _gen(client, job_id="job-gen")
    recs = r.json()["report"]["recommendations"]
    titles = [x["title"] for x in recs]
    assert any("service records" in t.lower() for t in titles)


# ---------- list & shop view ----------

def test_list_reports_with_tracking(client, fake_db):
    as_user()
    seed_job(fake_db, job_id="job-a", service="Plumbing drain clear")
    seed_job(fake_db, job_id="job-b", service="Pool filter clean")
    _gen(client, job_id="job-a")
    _gen(client, job_id="job-b")
    b = client.get("/api/home-reports/").json()
    assert b["count"] == 2
    assert b["reports"][0]["tracking"]["views"] == 0
    assert b["reports"][0]["tracking"]["sends"] == 0


def test_shop_view_shows_tracking(client, fake_db):
    as_user()
    seed_job(fake_db)
    rid = _gen(client).json()["id"]
    b = client.get(f"/api/home-reports/{rid}").json()
    assert b["id"] == rid
    assert b["tracking"] == {"views": 0, "first_viewed_at": None,
                             "last_viewed_at": None, "sends": 0}


def test_shop_view_unknown_id_404(client):
    as_user()
    r = client.get("/api/home-reports/nope")
    assert r.status_code == 404


# ---------- public view (no auth) ----------

def test_public_view_works_without_auth(client, fake_db):
    as_user()
    seed_job(fake_db)
    token = _gen(client).json()["share_token"]
    as_user(tenant_id="stranger", role="owner")  # auth overridden next line
    r = client.get(f"/api/home-reports/public/{token}")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["service_summary"] == "HVAC furnace tune-up"
    assert b["technician"] == "Tech Sam"
    assert b["recommendations"], "homeowner should see recommendations"
    # no internal tracking fields leak
    assert "tracking" not in b and "tenant_id" not in b


def test_public_view_invalid_token_404(client, fake_db):
    r = client.get("/api/home-reports/public/hr_bogus-token")
    assert r.status_code == 404


def test_public_view_records_open_tracking(client, fake_db):
    as_user()
    seed_job(fake_db)
    token = _gen(client).json()["share_token"]
    client.get(f"/api/home-reports/public/{token}")
    client.get(f"/api/home-reports/public/{token}")  # second hit same day: deduped
    views = [e for e in fake_db.home_report_events.docs if e["type"] == "viewed"]
    assert len(views) == 1


def test_public_view_no_bytes_in_photos(client, fake_db):
    as_user()
    seed_job(fake_db)
    seed_photo(fake_db, pid="after-1")
    token = _gen(client, after_photo_ids=["after-1"]).json()["share_token"]
    b = client.get(f"/api/home-reports/public/{token}").json()
    assert "image_bytes" not in b["photos"]["after"][0]


# ---------- send ----------

def test_send_marks_sent_and_records_event(client, fake_db):
    as_user()
    seed_job(fake_db)
    rid = _gen(client).json()["id"]
    r = client.post(f"/api/home-reports/{rid}/send", json={"channel": "sms"})
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "sent" and b["sent_channel"] == "sms"
    assert b["share_token"].startswith("hr_")
    assert fake_db.home_reports.docs[0]["status"] == "sent"
    sent = [e for e in fake_db.home_report_events.docs if e["type"] == "sent"]
    assert len(sent) == 1 and sent[0]["channel"] == "sms"


def test_send_tracking_visible_in_shop_view(client, fake_db):
    as_user()
    seed_job(fake_db)
    token = _gen(client).json()["share_token"]
    rid = fake_db.home_reports.docs[0]["id"]
    client.post(f"/api/home-reports/{rid}/send", json={"channel": "email"})
    client.get(f"/api/home-reports/public/{token}")
    as_user()
    b = client.get(f"/api/home-reports/{rid}").json()
    assert b["tracking"]["sends"] == 1
    assert b["tracking"]["views"] == 1
    assert b["tracking"]["first_viewed_at"] is not None


def test_events_endpoint_lists_created_sent_viewed(client, fake_db):
    as_user()
    seed_job(fake_db)
    token = _gen(client).json()["share_token"]
    rid = fake_db.home_reports.docs[0]["id"]
    client.post(f"/api/home-reports/{rid}/send", json={"channel": "link"})
    client.get(f"/api/home-reports/public/{token}")
    as_user()
    b = client.get(f"/api/home-reports/{rid}/events").json()
    types = {e["type"] for e in b["events"]}
    assert {"created", "sent", "viewed"} <= types
    assert b["count"] == 3


def test_send_non_owner_forbidden(client, fake_db):
    as_user()
    seed_job(fake_db)
    rid = _gen(client).json()["id"]
    as_user(role="tech")
    r = client.post(f"/api/home-reports/{rid}/send", json={"channel": "sms"})
    assert r.status_code == 403


def test_send_unknown_report_404(client):
    as_user()
    r = client.post("/api/home-reports/nope/send", json={"channel": "sms"})
    assert r.status_code == 404


# ---------- tenant isolation ----------

def test_tenant_isolation(client, fake_db):
    as_user(tenant_id="t1")
    seed_job(fake_db, tenant_id="t1", job_id="job-t1")
    rid = _gen(client, job_id="job-t1").json()["id"]
    token = fake_db.home_reports.docs[0]["share_token"]

    as_user(tenant_id="t2")
    assert client.get("/api/home-reports/").json()["count"] == 0
    assert client.get(f"/api/home-reports/{rid}").status_code == 404
    assert client.post(f"/api/home-reports/{rid}/send", json={"channel": "sms"}).status_code == 404

    # cross-tenant photo ref rejected at generate time
    seed_job(fake_db, tenant_id="t2", job_id="job-t2")
    seed_photo(fake_db, tenant_id="t1", pid="t1-photo")
    r = client.post("/api/home-reports/generate",
                    json={"job_id": "job-t2", "before_photo_ids": ["t1-photo"]})
    assert r.status_code == 404

    # but the public magic link still works (token is the auth)
    assert client.get(f"/api/home-reports/public/{token}").status_code == 200
