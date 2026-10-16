"""Tests for routers/reputation.py (autonomous reputation engine).

Uses a fake in-memory mongo (same pattern as test_trade_packs.py): no live
database. Covers overview KPIs, perfect-moment review requests, dedupe
(one request per job), opt-out enforcement, AI-draft tone selection,
policy-gated auto-posting, approve flow, and tenant scoping.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "reputation-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "reputation_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.reputation as rep_mod  # noqa: E402
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
        self.reviews = FakeCollection()
        self.review_requests = FakeCollection()
        self.review_responses = FakeCollection()
        self.reputation_policy = FakeCollection()
        self.review_opt_outs = FakeCollection()
        self.tenants = FakeCollection()


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(rep_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(rep_mod.router)
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


def seed_review(fake_db, tenant_id="t1", rating=5, status="needs_response", author="Kim"):
    fake_db.reviews.docs.append({
        "id": f"r-{tenant_id}-{rating}-{author}",
        "tenant_id": tenant_id, "rating": rating, "author_name": author,
        "text": "Great job", "source": "google",
        "response_status": status,
        "created_at": "2026-10-14T12:00:00+00:00",
    })


# ---------- overview ----------

def test_overview_kpis(client, fake_db):
    as_user()
    seed_review(fake_db, rating=5, author="Kim")
    seed_review(fake_db, rating=5, status="posted", author="Lee")
    seed_review(fake_db, rating=2, author="Ann")
    r = client.get("/api/reputation/tenants/overview")
    assert r.status_code == 200
    b = r.json()
    assert b["review_count"] == 3
    assert b["avg_rating"] == 4.0
    assert b["response_rate"] == pytest.approx(0.333)
    assert b["sentiment"] == {"positive": 2, "neutral": 0, "negative": 1}
    assert len(b["sentiment_trend"]) == 4
    assert b["monitoring"] and b["monitoring"][0]["site"] == "google"
    assert "never offer incentives" in b["policy_note"].lower()


def test_overview_empty_tenant(client, fake_db):
    as_user(tenant_id="empty")
    b = client.get("/api/reputation/tenants/overview").json()
    assert b["review_count"] == 0 and b["avg_rating"] == 0.0 and b["response_rate"] == 0.0
    assert b["sentiment"] == {"positive": 0, "neutral": 0, "negative": 0}


def test_overview_scoped_to_tenant(client, fake_db):
    as_user(tenant_id="t1")
    seed_review(fake_db, tenant_id="t1", rating=5, author="A")
    seed_review(fake_db, tenant_id="t2", rating=1, author="B")
    b = client.get("/api/reputation/tenants/overview").json()
    assert b["review_count"] == 1


# ---------- review requests ----------

def _request_job(client, job_id="job-1", **kw):
    payload = {"job_id": job_id, "customer_name": "Kim",
               "customer_phone": "555-0100", "channel": "sms"}
    payload.update(kw)
    return client.post("/api/reputation/tenants/requests", json=payload)


def test_create_request_perfect_moment_timing(client, fake_db):
    as_user()
    r = _request_job(client, job_id="job-1", signal_score=5)
    assert r.status_code == 200
    b = r.json()
    assert b["ok"] and b["duplicate"] is False
    assert b["timing"] == "perfect-moment"
    assert len(fake_db.review_requests.docs) == 1


def test_create_request_weak_signal_staggers(client, fake_db):
    as_user()
    r = _request_job(client, job_id="job-9")  # no signal_score
    assert r.json()["timing"] == "staggered"


def test_dedupe_one_request_per_job(client, fake_db):
    as_user()
    r1 = _request_job(client, job_id="job-7")
    r2 = _request_job(client, job_id="job-7")
    assert r1.json()["duplicate"] is False
    assert r2.json()["duplicate"] is True
    assert r2.json()["id"] == r1.json()["id"]
    assert len(fake_db.review_requests.docs) == 1


def test_request_missing_contact_rejected(client):
    as_user()
    r = client.post("/api/reputation/tenants/requests",
                    json={"job_id": "job-x"})
    assert r.status_code == 400


def test_request_non_owner_forbidden(client):
    as_user(role="tech")
    r = _request_job(client, job_id="job-z")
    assert r.status_code == 403


# ---------- opt-outs ----------

def test_opt_out_enforced_on_request(client, fake_db):
    as_user()
    fake_db.review_opt_outs.docs.append({
        "id": "oo-1", "tenant_id": "t1", "customer_name": "Kim",
        "customer_phone": "555-0100", "customer_email": "", "created_at": "2026-10-01T00:00:00+00:00",
    })
    r = _request_job(client, job_id="job-oo")
    assert r.status_code == 409
    assert len(fake_db.review_requests.docs) == 0


def test_add_opt_out_then_request_blocked(client, fake_db):
    as_user()
    r = client.post("/api/reputation/tenants/opt-outs",
                    json={"customer_name": "Lee", "customer_phone": "555-0200"})
    assert r.status_code == 200
    r2 = _request_job(client, job_id="job-oo2", customer_phone="555-0200")
    assert r2.status_code == 409


def test_opt_out_by_email(client, fake_db):
    as_user()
    client.post("/api/reputation/tenants/opt-outs",
                json={"customer_email": "lee@example.com"})
    r = _request_job(client, job_id="job-ooe", customer_phone="",
                     customer_email="lee@example.com", channel="email")
    assert r.status_code == 409


def test_opt_out_list_endpoint(client, fake_db):
    as_user()
    client.post("/api/reputation/tenants/opt-outs", json={"customer_phone": "555-0300"})
    b = client.get("/api/reputation/tenants/opt-outs").json()
    assert b["count"] == 1


# ---------- drafts & tone matching ----------

def _draft(client, review_id):
    return client.post(f"/api/reputation/tenants/reviews/{review_id}/draft")


def test_draft_5star_grateful_tone(client, fake_db):
    as_user()
    seed_review(fake_db, rating=5, author="Kim")
    b = _draft(client, "r-t1-5-Kim").json()
    assert b["ok"] and b["tone"] == "grateful"
    assert b["draft_labeled"] is True
    assert b["generated_by"] == "template-draft-v1"
    # Default policy auto-posts 5-star drafts
    assert b["status"] == "posted" and b["auto_posted"] is True


def test_draft_4star_appreciative_pending(client, fake_db):
    as_user()
    seed_review(fake_db, rating=4, author="Lee")
    b = _draft(client, "r-t1-4-Lee").json()
    assert b["tone"] == "appreciative"
    assert b["status"] == "draft" and b["auto_posted"] is False


def test_draft_low_rating_empathetic_action(client, fake_db):
    as_user()
    seed_review(fake_db, rating=2, author="Ann")
    b = _draft(client, "r-t1-2-Ann").json()
    assert b["tone"] == "empathetic_action"
    assert "sorry" in b["draft"].lower()
    assert b["status"] == "draft"


def test_draft_nonexistent_review_404(client):
    as_user()
    r = _draft(client, "nope")
    assert r.status_code == 404


def test_draft_stores_response_and_marks_review(client, fake_db):
    as_user()
    seed_review(fake_db, rating=3, author="Pat")
    b = _draft(client, "r-t1-3-Pat").json()
    assert len(fake_db.review_responses.docs) == 1
    assert fake_db.reviews.docs[0]["response_status"] == "drafted"
    assert b["review_id"] == "r-t1-3-Pat"


def test_inbox_attaches_latest_draft(client, fake_db):
    as_user()
    seed_review(fake_db, rating=4, author="Lee")
    _draft(client, "r-t1-4-Lee")
    b = client.get("/api/reputation/tenants/reviews").json()
    assert b["reviews"][0]["draft_text"]
    assert b["reviews"][0]["draft_status"] == "draft"
    assert b["reviews"][0]["draft_tone"] == "appreciative"


# ---------- policy gating ----------

def test_policy_disables_auto_post(client, fake_db):
    as_user()
    r = client.post("/api/reputation/tenants/policy",
                    json={"auto_post_5star": False, "require_approval_low": True})
    assert r.status_code == 200 and r.json()["auto_post_5star"] is False
    seed_review(fake_db, rating=5, author="Kim")
    b = _draft(client, "r-t1-5-Kim").json()
    assert b["status"] == "draft" and b["auto_posted"] is False


def test_policy_gate_other_tenant_unaffected(client, fake_db):
    as_user(tenant_id="t1")
    client.post("/api/reputation/tenants/policy",
                json={"auto_post_5star": False, "require_approval_low": True})
    seed_review(fake_db, tenant_id="t2", rating=5, author="Zed")
    as_user(tenant_id="t2")
    b = _draft(client, "r-t2-5-Zed").json()
    assert b["auto_posted"] is True  # t2 still has the default policy


# ---------- approve flow ----------

def test_approve_marks_posted(client, fake_db):
    as_user()
    seed_review(fake_db, rating=3, author="Pat")
    _draft(client, "r-t1-3-Pat")
    r = client.post("/api/reputation/tenants/reviews/r-t1-3-Pat/approve")
    assert r.status_code == 200
    b = r.json()
    assert b["posted"] is True
    assert fake_db.reviews.docs[0]["response_status"] == "posted"
    assert fake_db.review_responses.docs[0]["status"] == "posted"


def test_approve_without_draft_conflicts(client, fake_db):
    as_user()
    seed_review(fake_db, rating=4, author="Lee")
    r = client.post("/api/reputation/tenants/reviews/r-t1-4-Lee/approve")
    assert r.status_code == 409


def test_approve_non_owner_forbidden(client, fake_db):
    as_user()
    seed_review(fake_db, rating=3, author="Pat")
    _draft(client, "r-t1-3-Pat")
    as_user(role="tech")
    r = client.post("/api/reputation/tenants/reviews/r-t1-3-Pat/approve")
    assert r.status_code == 403


def test_approve_nonexistent_review_404(client):
    as_user()
    r = client.post("/api/reputation/tenants/reviews/nope/approve")
    assert r.status_code == 404


# ---------- inbox filters ----------

def test_inbox_filters(client, fake_db):
    as_user()
    seed_review(fake_db, rating=5, author="A")
    seed_review(fake_db, rating=5, status="posted", author="B")
    seed_review(fake_db, rating=1, author="C")
    b = client.get("/api/reputation/tenants/reviews").json()
    assert b["count"] == 3
    b = client.get("/api/reputation/tenants/reviews?needs_response=true").json()
    assert b["count"] == 2
    b = client.get("/api/reputation/tenants/reviews?rating=1").json()
    assert b["count"] == 1 and b["reviews"][0]["author_name"] == "C"
