"""Tests for the zero-touch job lifecycle router (routers/zero_touch.py).

Fake in-memory collections stand in for MongoDB: equality, $gte, $in and
sort are supported, which is all the router needs.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "zero-touch-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "zero_touch_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.zero_touch as zt  # noqa: E402
from security import require_tenant_user  # noqa: E402


def _match(doc, query):
    for k, v in query.items():
        if isinstance(v, dict):
            if "$gte" in v and str(doc.get(k, "")) < str(v["$gte"]):
                return False
            if "$in" in v and doc.get(k) not in v["$in"]:
                return False
        elif doc.get(k) != v:
            return False
    return True


class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        self.docs = sorted(self.docs, key=lambda d: str(d.get(key, "")),
                           reverse=(direction < 0))
        return self

    async def to_list(self, n):
        return [dict(d) for d in self.docs[:n]]


class FakeCollection:
    def __init__(self):
        self.docs = []

    def find(self, query, proj=None):
        return FakeCursor([d for d in self.docs if _match(d, query)])

    async def find_one(self, query, proj=None, **kwargs):
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
                d.update(update.get("$set", {}))
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            doc = dict(query)
            doc.update(update.get("$set", {}))
            self.docs.append(doc)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=True)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def update_many(self, query, update):
        n = 0
        for d in self.docs:
            if _match(d, query):
                d.update(update.get("$set", {}))
                n += 1
        return SimpleNamespace(matched_count=n, modified_count=n)

    async def count_documents(self, query):
        return sum(1 for d in self.docs if _match(d, query))


class FakeDB:
    def __init__(self):
        self.appointments = FakeCollection()
        self.lifecycle_pipelines = FakeCollection()
        self.job_lifecycle_events = FakeCollection()
        self.invoices = FakeCollection()
        self.review_requests = FakeCollection()


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(zt, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(zt.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


@pytest.fixture
def job(fake_db):
    fake_db.appointments.docs.append({
        "id": "job-1", "tenant_id": "t1", "customer_name": "Jane Home",
        "customer_phone": "+15551234567", "title": "AC repair",
        "quoted_total": 450.0, "status": "scheduled",
    })
    return "job-1"


# ---------- pipeline endpoints ----------

def test_list_pipelines_seeds_default(client):
    as_user("t1")
    r = client.get("/api/zero-touch/tenants/pipelines")
    assert r.status_code == 200
    pipes = r.json()["pipelines"]
    assert len(pipes) == 1
    stages = pipes[0]["stages"]
    assert [s["stage"] for s in stages] == [
        "lead", "booked", "dispatched", "en_route", "on_site",
        "complete", "invoiced", "paid", "canceled"]
    booked = next(s for s in stages if s["stage"] == "booked")
    assert booked["actions"] == [{"name": "send_sms", "template": "booking_confirmed"}]
    invoiced = next(s for s in stages if s["stage"] == "invoiced")
    assert invoiced["actions"] == [{"name": "create_invoice_draft"}]
    # second read does not re-seed
    r2 = client.get("/api/zero-touch/tenants/pipelines")
    assert len(r2.json()["pipelines"]) == 1


def test_save_pipeline_owner_only(client):
    as_user("t1", role="tech")
    payload = {"name": "Custom", "stages": [
        {"stage": "lead"}, {"stage": "booked"}, {"stage": "canceled"}]}
    r = client.post("/api/zero-touch/tenants/pipelines", json=payload)
    assert r.status_code == 403
    as_user("t1", role="owner")
    r = client.post("/api/zero-touch/tenants/pipelines", json=payload)
    assert r.status_code == 200
    assert r.json()["pipeline"]["name"] == "Custom"


def test_save_pipeline_rejects_unknown_stage_and_action(client):
    as_user("t1")
    r = client.post("/api/zero-touch/tenants/pipelines", json={
        "name": "Bad", "stages": [{"stage": "warp_zone"}]})
    assert r.status_code == 400
    r = client.post("/api/zero-touch/tenants/pipelines", json={
        "name": "Bad", "stages": [
            {"stage": "lead", "actions": [{"name": "launch_rockets"}]}]})
    assert r.status_code == 400


def test_save_pipeline_replaces_old_active(client):
    as_user("t1")
    client.get("/api/zero-touch/tenants/pipelines")  # seeds default
    client.post("/api/zero-touch/tenants/pipelines", json={
        "name": "Second", "stages": [{"stage": "lead"}, {"stage": "paid"}]})
    r = client.get("/api/zero-touch/tenants/pipelines")
    pipes = r.json()["pipelines"]
    assert len(pipes) == 1
    assert pipes[0]["name"] == "Second"


# ---------- advance ----------

def test_advance_happy_path_runs_actions_and_audits(client, fake_db, job):
    as_user("t1")
    r = client.post(f"/api/zero-touch/tenants/jobs/{job}/advance", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["advanced"] is True
    assert body["from_stage"] == "lead"
    assert body["to_stage"] == "booked"
    assert body["automatic"] is True
    # booked stage fires send_sms; phone present -> fired
    assert body["actions_fired"][0]["name"] == "send_sms"
    assert body["actions_fired"][0]["status"] == "fired"
    # appointment kept in sync
    appt = fake_db.appointments.docs[0]
    assert appt["lifecycle_stage"] == "booked"
    assert appt["status"] == "scheduled"
    # audit event recorded
    evs = fake_db.job_lifecycle_events.docs
    transitions = [e for e in evs if e["kind"] == "transition"]
    assert len(transitions) == 1
    assert transitions[0]["actor"] == {"type": "system"}


def test_advance_rejects_unknown_job(client):
    as_user("t1")
    r = client.post("/api/zero-touch/tenants/jobs/nope/advance", json={})
    assert r.status_code == 404


def test_advance_terminal_stage_is_noop(client, fake_db, job):
    as_user("t1")
    fake_db.appointments.docs[0]["lifecycle_stage"] = "paid"
    r = client.post(f"/api/zero-touch/tenants/jobs/{job}/advance", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["advanced"] is False
    assert body["stage"] == "paid"
    assert len(fake_db.job_lifecycle_events.docs) == 0


def test_advance_idempotent_with_key(client, fake_db, job):
    as_user("t1")
    key = {"idempotency_key": "k-1"}
    r1 = client.post(f"/api/zero-touch/tenants/jobs/{job}/advance", json=key)
    assert r1.status_code == 200
    events_before = len(fake_db.job_lifecycle_events.docs)
    r2 = client.post(f"/api/zero-touch/tenants/jobs/{job}/advance", json=key)
    assert r2.status_code == 200
    assert r2.json()["idempotent_replay"] is True
    assert r2.json()["to_stage"] == "booked"
    # no new events, job not advanced further
    assert len(fake_db.job_lifecycle_events.docs) == events_before
    assert fake_db.appointments.docs[0]["lifecycle_stage"] == "booked"


def test_advance_runs_invoice_draft_and_skips_dupes(client, fake_db, job):
    as_user("t1")
    fake_db.appointments.docs[0]["lifecycle_stage"] = "complete"
    r = client.post(f"/api/zero-touch/tenants/jobs/{job}/advance", json={})
    assert r.json()["to_stage"] == "invoiced"
    assert r.json()["actions_fired"][0]["name"] == "create_invoice_draft"
    invs = fake_db.invoices.docs
    assert len(invs) == 1
    assert invs[0]["status"] == "draft"
    assert invs[0]["total"] == 450.0
    assert invs[0]["tenant_id"] == "t1"


def test_advance_request_review_on_paid(client, fake_db, job):
    as_user("t1")
    fake_db.appointments.docs[0]["lifecycle_stage"] = "invoiced"
    r = client.post(f"/api/zero-touch/tenants/jobs/{job}/advance", json={})
    assert r.json()["to_stage"] == "paid"
    assert r.json()["actions_fired"][0]["name"] == "request_review"
    assert r.json()["actions_fired"][0]["status"] == "fired"
    assert len(fake_db.review_requests.docs) == 1


def test_sms_action_skips_without_phone(client, fake_db, job):
    as_user("t1")
    fake_db.appointments.docs[0]["customer_phone"] = ""
    r = client.post(f"/api/zero-touch/tenants/jobs/{job}/advance", json={})
    assert r.json()["actions_fired"][0]["status"] == "skipped"


# ---------- override ----------

def test_override_sets_any_stage_with_reason_and_audits(client, fake_db, job):
    as_user("t1", role="admin")
    r = client.post(f"/api/zero-touch/tenants/jobs/{job}/stage/on_site",
                    json={"reason": "tech arrived early"})
    assert r.status_code == 200
    body = r.json()
    assert body["override"] is True
    assert body["to_stage"] == "on_site"
    evs = fake_db.job_lifecycle_events.docs
    assert len(evs) == 1
    assert evs[0]["kind"] == "override"
    assert evs[0]["reason"] == "tech arrived early"
    assert evs[0]["actor"]["role"] == "admin"


def test_override_unknown_stage_rejected(client, job):
    as_user("t1")
    r = client.post(f"/api/zero-touch/tenants/jobs/{job}/stage/warp_zone",
                    json={"reason": "x"})
    assert r.status_code == 400


# ---------- timeline & autonomy ----------

def test_timeline_complete_and_ordered(client, fake_db, job):
    as_user("t1")
    client.post(f"/api/zero-touch/tenants/jobs/{job}/advance", json={})
    client.post(f"/api/zero-touch/tenants/jobs/{job}/stage/canceled",
                json={"reason": "customer asked"})
    r = client.get(f"/api/zero-touch/tenants/jobs/{job}/timeline")
    assert r.status_code == 200
    body = r.json()
    # transition + the sms action's audit event + the override
    assert body["count"] == 3
    kinds = [e["kind"] for e in body["events"]]
    assert kinds == ["transition", "action", "override"]
    # full stages recorded
    assert body["events"][0]["to_stage"] == "booked"
    assert body["events"][1]["action"] == "send_sms"
    assert body["events"][2]["to_stage"] == "canceled"


def test_timeline_404_unknown_job(client):
    as_user("t1")
    r = client.get("/api/zero-touch/tenants/jobs/nope/timeline")
    assert r.status_code == 404


def test_recent_events_feed(client, fake_db, job):
    as_user("t1")
    client.post(f"/api/zero-touch/tenants/jobs/{job}/advance", json={})
    r = client.get("/api/zero-touch/tenants/events/recent")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 2  # transition + action event
    assert body["events"][0]["created_at"] >= body["events"][1]["created_at"]
    # tenant isolation: other tenant's events not visible
    as_user("t2")
    r = client.get("/api/zero-touch/tenants/events/recent")
    assert r.json()["count"] == 0


def test_autonomy_dashboard_counts_overrides_and_autonomy(client, fake_db, job):
    as_user("t1")
    # fully autonomous: advance to paid (each step is one advance)
    for _ in range(7):
        client.post(f"/api/zero-touch/tenants/jobs/{job}/advance", json={})
    # second job with a human override
    fake_db.appointments.docs.append({
        "id": "job-2", "tenant_id": "t1", "customer_name": "Bob",
        "customer_phone": "+15559876543", "status": "scheduled"})
    client.post("/api/zero-touch/tenants/jobs/job-2/stage/on_site",
                json={"reason": "skip ahead"})
    r = client.get("/api/zero-touch/tenants/autonomy")
    assert r.status_code == 200
    body = r.json()
    assert body["jobs_reached_paid"] == 1
    assert body["fully_autonomous_pct"] == 100.0
    assert body["human_overrides"] == 1
    assert body["automatic_transitions"] == 7
    assert body["actions_fired"] > 0
    assert body["avg_seconds_per_stage"]
