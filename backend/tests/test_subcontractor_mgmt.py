"""Unit tests for subcontractor management (routers/subcontractors.py).

Zero network, zero database: a small FastAPI app with only the
subcontractors router, a fake in-memory Mongo, and an overridden auth
dependency.

Coverage: CRUD, insurance alert windows (29/30/31 days, expired), dispatch
linking a job, pay summary totals per year, tenant isolation, and
non-admins blocked from pay writes.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "submgmt-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "submgmt_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.subcontractors as sc_mod  # noqa: E402
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
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _match(d, query)]
        return SimpleNamespace(deleted_count=before - len(self.docs))


class FakeDB:
    def __init__(self):
        self.subcontractors = FakeCollection()
        self.appointments = FakeCollection()
        self.sub_payments = FakeCollection()


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
    monkeypatch.setattr(sc_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(sc_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    # NOTE: require_tenant_owner_or_admin is NOT overridden — it wraps the
    # overridden require_tenant_user, so the real role check still runs.
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def expiry_in(days):
    return (date.today() + timedelta(days=days)).isoformat()


def make_sub_payload(**over):
    payload = {
        "name": "Mike Torres",
        "trade": "plumbing",
        "phone": "555-0100",
        "rate_type": "hourly",
        "rate": 65.0,
        "insurance_expiry": expiry_in(60),
        "license": "PLB-123",
        "w9_on_file": True,
        "active": True,
    }
    payload.update(over)
    return payload


def make_job(fake_db, tenant_id, job_id="job-1", status="scheduled"):
    fake_db.appointments.docs.append({
        "id": job_id, "tenant_id": tenant_id, "status": status,
        "start_at": "2026-10-16T09:00:00", "service": "Water heater install",
        "customer": {"name": "Jane Doe"},
    })
    return job_id


# ---------- CRUD ----------

def test_crud_lifecycle(client, fake_db):
    as_user("t1")
    r = client.post("/subcontractors/subs", json=make_sub_payload())
    assert r.status_code == 200
    sub = r.json()
    sub_id = sub["id"]
    assert sub["name"] == "Mike Torres"
    assert sub["insurance"]["status"] == "valid"

    r = client.get("/subcontractors/subs")
    assert r.json()["count"] == 1

    r = client.get(f"/subcontractors/subs/{sub_id}")
    assert r.status_code == 200
    assert r.json()["trade"] == "plumbing"

    r = client.patch(f"/subcontractors/subs/{sub_id}",
                     json={"rate": 70.0, "w9_on_file": False})
    assert r.json()["rate"] == 70.0
    assert r.json()["w9_on_file"] is False

    r = client.delete(f"/subcontractors/subs/{sub_id}")
    assert r.json()["ok"] is True
    assert client.get(f"/subcontractors/subs/{sub_id}").status_code == 404
    assert client.get("/subcontractors/subs").json()["count"] == 0


def test_create_rejects_bad_rate_type(client, fake_db):
    as_user("t1")
    r = client.post("/subcontractors/subs",
                    json=make_sub_payload(rate_type="daily"))
    assert r.status_code == 422


def test_create_rejects_bad_insurance_date(client, fake_db):
    as_user("t1")
    r = client.post("/subcontractors/subs",
                    json=make_sub_payload(insurance_expiry="not-a-date"))
    assert r.status_code == 400


def test_unknown_sub_404(client, fake_db):
    as_user("t1")
    assert client.get("/subcontractors/subs/nope").status_code == 404


def test_active_only_filter(client, fake_db):
    as_user("t1")
    client.post("/subcontractors/subs", json=make_sub_payload(name="Active One"))
    client.post("/subcontractors/subs",
                json=make_sub_payload(name="Inactive One", active=False))
    body = client.get("/subcontractors/subs", params={"active_only": True}).json()
    assert body["count"] == 1
    assert body["subcontractors"][0]["name"] == "Active One"


# ---------- insurance alerts ----------

def test_insurance_alert_windows(client, fake_db):
    """29d and 30d -> expiring_soon; 31d -> quiet; past -> expired."""
    as_user("t1")
    client.post("/subcontractors/subs",
                json=make_sub_payload(name="TwentyNine", insurance_expiry=expiry_in(29)))
    client.post("/subcontractors/subs",
                json=make_sub_payload(name="Thirty", insurance_expiry=expiry_in(30)))
    client.post("/subcontractors/subs",
                json=make_sub_payload(name="ThirtyOne", insurance_expiry=expiry_in(31)))
    client.post("/subcontractors/subs",
                json=make_sub_payload(name="Expired", insurance_expiry=expiry_in(-5)))
    client.post("/subcontractors/subs",
                json=make_sub_payload(name="NoDate", insurance_expiry=None))

    body = client.get("/subcontractors/alerts").json()
    soon_names = [r["name"] for r in body["expiring_soon"]]
    expired_names = [r["name"] for r in body["expired"]]
    assert soon_names == ["TwentyNine", "Thirty"]  # sorted nearest first
    assert body["expiring_soon"][0]["days_until_expiry"] == 29
    assert body["expiring_soon"][1]["days_until_expiry"] == 30
    assert "ThirtyOne" not in soon_names + expired_names
    assert "NoDate" not in soon_names + expired_names
    assert expired_names == ["Expired"]
    assert body["expired"][0]["days_until_expiry"] == -5
    assert body["count"] == 3


def test_insurance_badge_on_detail(client, fake_db):
    as_user("t1")
    sub_id = client.post(
        "/subcontractors/subs",
        json=make_sub_payload(insurance_expiry=expiry_in(3))).json()["id"]
    detail = client.get(f"/subcontractors/subs/{sub_id}").json()
    assert detail["insurance"]["status"] == "expiring_soon"
    assert detail["insurance"]["days_until_expiry"] == 3


# ---------- dispatch ----------

def test_dispatch_links_job(client, fake_db):
    as_user("t1")
    sub_id = client.post("/subcontractors/subs",
                         json=make_sub_payload()).json()["id"]
    make_job(fake_db, "t1", "job-1")

    r = client.post(f"/subcontractors/subs/{sub_id}/dispatch",
                    json={"job_id": "job-1"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["sub_name"] == "Mike Torres"

    # The assignment is recorded ON the job.
    job = fake_db.appointments.docs[0]
    assert job["sub_id"] == sub_id
    assert job["sub_name"] == "Mike Torres"

    # And shows up in the sub's job list.
    jobs = client.get(f"/subcontractors/subs/{sub_id}/jobs").json()
    assert jobs["count"] == 1
    assert jobs["jobs"][0]["id"] == "job-1"


def test_dispatch_validates(client, fake_db):
    as_user("t1")
    sub_id = client.post("/subcontractors/subs",
                         json=make_sub_payload()).json()["id"]
    inactive_id = client.post(
        "/subcontractors/subs",
        json=make_sub_payload(name="Retired", active=False)).json()["id"]
    make_job(fake_db, "t1", "job-done", status="completed")

    # Unknown job.
    r = client.post(f"/subcontractors/subs/{sub_id}/dispatch",
                    json={"job_id": "nope"})
    assert r.status_code == 404
    # Completed job.
    r = client.post(f"/subcontractors/subs/{sub_id}/dispatch",
                    json={"job_id": "job-done"})
    assert r.status_code == 400
    # Inactive sub.
    make_job(fake_db, "t1", "job-2")
    r = client.post(f"/subcontractors/subs/{inactive_id}/dispatch",
                    json={"job_id": "job-2"})
    assert r.status_code == 400


# ---------- payments & 1099 summary ----------

def record(client, sub_id, job_id, amount, pay_date, method="check"):
    return client.post(
        f"/subcontractors/subs/{sub_id}/payments",
        json={"job_id": job_id, "amount": amount, "date": pay_date,
              "method": method, "note": ""})


def test_payments_and_year_summary(client, fake_db):
    as_user("t1")
    sub_id = client.post("/subcontractors/subs",
                         json=make_sub_payload(w9_on_file=True)).json()["id"]
    make_job(fake_db, "t1", "job-a")
    make_job(fake_db, "t1", "job-b")

    assert record(client, sub_id, "job-a", 500.0, "2026-03-01").status_code == 200
    assert record(client, sub_id, "job-b", 700.0, "2026-06-15").status_code == 200
    assert record(client, sub_id, "job-a", 100.0, "2025-12-20").status_code == 200

    summary = client.get(f"/subcontractors/subs/{sub_id}/pay-summary",
                         params={"year": 2026}).json()
    assert summary["total_paid"] == 1200.0
    assert summary["payment_count"] == 2
    assert summary["w9_on_file"] is True
    assert summary["ready_to_file"] is True  # w9 + >= $600
    assert summary["filing_threshold"] == 600.0
    assert len(summary["per_job"]) == 2
    assert summary["per_job"][0] == {
        "job_id": "job-b", "job_label": "Water heater install — Jane Doe",
        "total": 700.0, "payments": 1}

    summary_2025 = client.get(f"/subcontractors/subs/{sub_id}/pay-summary",
                              params={"year": 2025}).json()
    assert summary_2025["total_paid"] == 100.0
    assert summary_2025["ready_to_file"] is False  # below threshold


def test_summary_not_ready_without_w9(client, fake_db):
    as_user("t1")
    sub_id = client.post("/subcontractors/subs",
                         json=make_sub_payload(w9_on_file=False)).json()["id"]
    make_job(fake_db, "t1", "job-a")
    record(client, sub_id, "job-a", 5000.0, "2026-01-10")

    summary = client.get(f"/subcontractors/subs/{sub_id}/pay-summary",
                         params={"year": 2026}).json()
    assert summary["total_paid"] == 5000.0
    assert summary["w9_on_file"] is False
    assert summary["ready_to_file"] is False  # money is there, W-9 is not


def test_payment_requires_positive_amount(client, fake_db):
    as_user("t1")
    sub_id = client.post("/subcontractors/subs",
                         json=make_sub_payload()).json()["id"]
    make_job(fake_db, "t1", "job-a")
    r = record(client, sub_id, "job-a", 0.0, "2026-01-10")
    assert r.status_code == 422


def test_payment_unknown_job_404(client, fake_db):
    as_user("t1")
    sub_id = client.post("/subcontractors/subs",
                         json=make_sub_payload()).json()["id"]
    r = record(client, sub_id, "ghost-job", 50.0, "2026-01-10")
    assert r.status_code == 404


# ---------- tenant isolation ----------

def test_tenant_isolation(client, fake_db):
    as_user("t1")
    sub_id = client.post("/subcontractors/subs",
                         json=make_sub_payload(name="T1 Sub")).json()["id"]
    make_job(fake_db, "t1", "job-t1")
    client.post(f"/subcontractors/subs/{sub_id}/dispatch",
                json={"job_id": "job-t1"})
    record(client, sub_id, "job-t1", 200.0, "2026-02-02")

    as_user("t2")
    # Tenant B sees none of tenant A's data.
    assert client.get("/subcontractors/subs").json()["count"] == 0
    assert client.get(f"/subcontractors/subs/{sub_id}").status_code == 404
    assert client.get("/subcontractors/alerts").json()["count"] == 0
    assert client.get(f"/subcontractors/subs/{sub_id}/jobs").status_code == 404
    assert client.get(f"/subcontractors/subs/{sub_id}/payments").status_code == 404
    assert client.get(f"/subcontractors/subs/{sub_id}/pay-summary",
                      params={"year": 2026}).status_code == 404
    # ...and cannot touch it either.
    r = client.post(f"/subcontractors/subs/{sub_id}/dispatch",
                    json={"job_id": "job-t1"})
    assert r.status_code == 404


# ---------- role gating ----------

def test_non_admin_blocked_from_pay_writes(client, fake_db):
    as_user("t1", role="owner")
    sub_id = client.post("/subcontractors/subs",
                         json=make_sub_payload()).json()["id"]
    make_job(fake_db, "t1", "job-a")

    as_user("t1", role="tech")
    # A tech can still read subs and dispatch, but not record payments.
    assert client.get("/subcontractors/subs").status_code == 200
    assert client.post(f"/subcontractors/subs/{sub_id}/dispatch",
                       json={"job_id": "job-a"}).status_code == 200
    r = record(client, sub_id, "job-a", 100.0, "2026-01-10")
    assert r.status_code == 403

    # Dispatchers are gated too.
    as_user("t1", role="dispatcher")
    r = record(client, sub_id, "job-a", 100.0, "2026-01-10")
    assert r.status_code == 403


def test_admin_can_record_payment(client, fake_db):
    as_user("t1", role="admin")
    sub_id = client.post("/subcontractors/subs",
                         json=make_sub_payload()).json()["id"]
    make_job(fake_db, "t1", "job-a")
    assert record(client, sub_id, "job-a", 250.0, "2026-04-04",
                  method="ach").status_code == 200
    payments = client.get(f"/subcontractors/subs/{sub_id}/payments").json()
    assert payments["count"] == 1
    assert payments["payments"][0]["method"] == "ach"
