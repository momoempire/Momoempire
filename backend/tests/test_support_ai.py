"""Tests for routers/support_ai.py (AI support agent + health/churn).

Uses a fake in-memory mongo (same pattern as test_reputation.py): no live
database. Covers KB-grounded answers with cited sources, low-confidence
escalation (human ticket written), QA feedback, health-score math, churn tier
triggers (score drop + ticket spike, plan downgrade), and tenant scoping.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "support-ai-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "support_ai_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.support_ai as sai_mod  # noqa: E402
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
        self.knowledge_chunks = FakeCollection()
        self.support_answers = FakeCollection()
        self.support_feedback = FakeCollection()
        self.support_tickets = FakeCollection()
        self.shop_health_scores = FakeCollection()
        self.churn_flags = FakeCollection()
        self.usage_events = FakeCollection()
        self.users = FakeCollection()
        self.tenants = FakeCollection()


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(sai_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(sai_mod.router)
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


def seed_tenant(fake_db, tenant_id="t1", status="active", plan_id="pro", **kw):
    doc = {"id": tenant_id, "name": "Test Shop", "subscription_status": status,
           "plan_id": plan_id}
    doc.update(kw)
    fake_db.tenants.docs.append(doc)


def seed_kb(fake_db, tenant_id="t1", chunks=1):
    base = "Dispatch boards show each technician's jobs for the day. " \
           "Drag a job card to a technician column to assign it. " \
           "Unassigned jobs stay in the backlog lane until claimed."
    for i in range(chunks):
        fake_db.knowledge_chunks.docs.append({
            "id": f"kc-{tenant_id}-{i}", "tenant_id": tenant_id,
            "doc_id": f"doc-{tenant_id}", "doc_title": "Dispatch Guide",
            "chunk_idx": i, "content": base,
        })


def seed_usage(fake_db, tenant_id="t1", metrics=("ai_minutes", "calls", "sms", "ai_interactions"),
               created_at="2026-10-15T12:00:00+00:00"):
    for m in metrics:
        fake_db.usage_events.docs.append({
            "id": f"ue-{tenant_id}-{m}", "tenant_id": tenant_id, "metric": m,
            "value": 10.0, "period_key": "2026-10", "created_at": created_at,
        })


def seed_user(fake_db, tenant_id="t1", last_login_at="2026-10-15T12:00:00+00:00"):
    fake_db.users.docs.append({
        "id": f"u-{tenant_id}", "tenant_id": tenant_id,
        "last_login_at": last_login_at,
    })


def seed_health(fake_db, tenant_id="t1", score=92, plan_id="pro"):
    fake_db.shop_health_scores.docs.append({
        "id": f"hs-{tenant_id}", "tenant_id": tenant_id, "score": score,
        "factors": {}, "plan_id": plan_id,
        "computed_at": "2026-10-14T12:00:00+00:00",
    })


def seed_ticket(fake_db, tenant_id="t1", status="open",
                created_at="2026-10-15T12:00:00+00:00", n=1, tag=""):
    for i in range(n):
        fake_db.support_tickets.docs.append({
            "id": f"tk-{tenant_id}-{tag}-{i}", "tenant_id": tenant_id,
            "subject": "help", "description": "help", "status": status,
            "priority": "normal", "source": "ai-support-escalation",
            "created_at": created_at, "updated_at": created_at,
        })


# ---------- /ask ----------

def test_ask_answers_with_kb_sources(client, fake_db):
    as_user()
    seed_tenant(fake_db)
    seed_kb(fake_db)
    r = client.post("/api/support-ai/ask",
                    json={"question": "How do I assign jobs on the dispatch board?"})
    assert r.status_code == 200
    b = r.json()
    assert b["ok"] and b["escalated"] is False
    assert b["ticket_id"] is None
    assert b["confidence"] >= 0.45
    assert b["sources"], "answer must cite KB sources"
    assert b["sources"][0]["doc_title"] == "Dispatch Guide"
    assert "dispatch" in b["sources"][0]["excerpt"].lower()
    assert len(fake_db.support_answers.docs) == 1  # logged for QA
    assert len(fake_db.support_tickets.docs) == 0


def test_ask_low_confidence_escalates_to_ticket(client, fake_db):
    as_user()
    seed_tenant(fake_db)
    # No KB seeded -> cannot answer -> escalation.
    r = client.post("/api/support-ai/ask",
                    json={"question": "How do I assign jobs on the dispatch board?"})
    b = r.json()
    assert b["escalated"] is True
    assert b["confidence"] < 0.45
    assert b["ticket_id"], "escalation must reference the ticket"
    assert len(fake_db.support_tickets.docs) == 1
    t = fake_db.support_tickets.docs[0]
    assert t["tenant_id"] == "t1"
    assert t["status"] == "open"
    assert t["source"] == "ai-support-escalation"
    assert t["question"] in t["description"]
    # The escalation answer is still logged for QA.
    assert len(fake_db.support_answers.docs) == 1


def test_ask_weak_match_escalates(client, fake_db):
    as_user()
    seed_tenant(fake_db)
    seed_kb(fake_db)  # KB is about dispatch boards
    r = client.post("/api/support-ai/ask",
                    json={"question": "What is the capital of France?"})
    b = r.json()
    assert b["escalated"] is True
    assert len(fake_db.support_tickets.docs) == 1


def test_ask_empty_question_rejected(client, fake_db):
    as_user()
    r = client.post("/api/support-ai/ask", json={"question": ""})
    assert r.status_code == 422


def test_ask_tenant_isolation(client, fake_db):
    as_user(tenant_id="t1")
    seed_tenant(fake_db, tenant_id="t1")
    seed_tenant(fake_db, tenant_id="t2")
    seed_kb(fake_db, tenant_id="t2")  # KB only exists for t2
    b = client.post("/api/support-ai/ask",
                    json={"question": "How do I assign jobs on the dispatch board?"}).json()
    assert b["escalated"] is True  # t1 sees no KB articles
    assert fake_db.support_answers.docs[0]["tenant_id"] == "t1"


# ---------- QA log + feedback ----------

def test_answers_qa_log_with_feedback(client, fake_db):
    as_user()
    seed_tenant(fake_db)
    seed_kb(fake_db)
    b = client.post("/api/support-ai/ask",
                    json={"question": "How do I assign jobs on the dispatch board?"}).json()
    aid = b["id"]
    r = client.post(f"/api/support-ai/answers/{aid}/feedback", json={"helpful": True})
    assert r.status_code == 200 and r.json()["helpful"] is True
    log = client.get("/api/support-ai/answers").json()
    assert log["count"] == 1
    assert log["answers"][0]["feedback"]["helpful"] is True


def test_feedback_upsert_updates_vote(client, fake_db):
    as_user()
    seed_tenant(fake_db)
    seed_kb(fake_db)
    aid = client.post("/api/support-ai/ask",
                      json={"question": "How do I assign jobs on the dispatch board?"}).json()["id"]
    client.post(f"/api/support-ai/answers/{aid}/feedback", json={"helpful": False})
    r = client.post(f"/api/support-ai/answers/{aid}/feedback", json={"helpful": True})
    assert r.json()["updated"] is True
    assert len(fake_db.support_feedback.docs) == 1  # one vote per user
    assert fake_db.support_feedback.docs[0]["helpful"] is True


def test_feedback_unknown_answer_404(client):
    as_user()
    r = client.post("/api/support-ai/answers/nope/feedback", json={"helpful": True})
    assert r.status_code == 404


def test_answers_log_owner_only(client):
    as_user(role="tech")
    r = client.get("/api/support-ai/answers")
    assert r.status_code == 403


# ---------- health scoring ----------

def test_health_score_full_marks(client, fake_db):
    as_user()
    seed_tenant(fake_db, status="active", plan_id="pro")
    seed_usage(fake_db)  # 4 metered metrics recent
    seed_user(fake_db)    # logged in today
    b = client.get("/api/support-ai/health").json()
    assert b["score"] == 93  # 28 usage + 20 login + 20 tickets + 25 payment
    assert b["factors"]["usage_breadth"]["score"] == 28
    assert b["factors"]["login_recency"]["score"] == 20
    assert b["factors"]["ticket_sentiment"]["score"] == 20
    assert b["factors"]["payment_health"]["score"] == 25
    assert len(fake_db.shop_health_scores.docs) == 1  # snapshot persisted


def test_health_stale_login_drags_score(client, fake_db):
    as_user()
    seed_tenant(fake_db)
    seed_usage(fake_db, created_at="2026-08-01T00:00:00+00:00")  # stale usage too
    seed_user(fake_db, last_login_at="2026-08-01T00:00:00+00:00")
    b = client.get("/api/support-ai/health").json()
    assert b["factors"]["login_recency"]["score"] == 0
    assert b["factors"]["usage_breadth"]["score"] == 0


def test_health_payment_past_due(client, fake_db):
    as_user()
    seed_tenant(fake_db, status="past_due")
    b = client.get("/api/support-ai/health").json()
    assert b["factors"]["payment_health"]["score"] == 8


def test_health_open_tickets_penalize_sentiment(client, fake_db):
    as_user()
    seed_tenant(fake_db)
    seed_ticket(fake_db, n=2)
    b = client.get("/api/support-ai/health").json()
    assert b["factors"]["ticket_sentiment"]["score"] == 14  # 20 - 3*2


def test_health_history_ordering(client, fake_db):
    as_user()
    seed_tenant(fake_db)
    client.get("/api/support-ai/health")
    client.get("/api/support-ai/health")
    b = client.get("/api/support-ai/health/history").json()
    assert b["count"] == 2
    assert b["history"][0]["computed_at"] >= b["history"][1]["computed_at"]


def test_health_tenant_isolation(client, fake_db):
    as_user(tenant_id="t1")
    seed_tenant(fake_db, tenant_id="t1")
    seed_tenant(fake_db, tenant_id="t2")
    client.get("/api/support-ai/health")
    as_user(tenant_id="t2")
    b = client.get("/api/support-ai/health/history").json()
    assert b["count"] == 0


# ---------- churn prediction ----------

def test_churn_low_tier_when_healthy(client, fake_db):
    as_user()
    seed_tenant(fake_db)
    seed_usage(fake_db)
    seed_user(fake_db)
    b = client.get("/api/support-ai/churn").json()
    assert b["tier"] == "low"
    assert b["flag_id"] is None
    assert len(fake_db.churn_flags.docs) == 0


def test_churn_high_on_score_drop_plus_ticket_spike(client, fake_db):
    as_user()
    seed_tenant(fake_db, status="active", plan_id="pro")
    seed_health(fake_db, score=92, plan_id="pro")  # previous snapshot: great
    # Current state is grim: no usage/login -> score will drop ~60 pts.
    seed_ticket(fake_db, n=3)  # spike: 3 tickets in the last 7 days
    b = client.get("/api/support-ai/churn").json()
    assert b["tier"] == "high"
    assert any("score_drop" in r for r in b["reasons"])
    assert any("ticket_spike" in r for r in b["reasons"])
    assert len(b["playbook"]) >= 4, "high risk gets a save playbook"
    assert b["flag_id"]
    assert len(fake_db.churn_flags.docs) == 1
    flag = fake_db.churn_flags.docs[0]
    assert flag["tier"] == "high" and flag["acknowledged"] is False


def test_churn_flag_dedupes_per_tier(client, fake_db):
    as_user()
    seed_tenant(fake_db)
    seed_health(fake_db, score=92)
    seed_ticket(fake_db, n=3)
    client.get("/api/support-ai/churn")
    client.get("/api/support-ai/churn")
    assert len(fake_db.churn_flags.docs) == 1  # no duplicate unacked flag


def test_churn_watch_on_plan_downgrade(client, fake_db):
    as_user()
    seed_tenant(fake_db, status="active", plan_id="lite")  # now lite...
    seed_health(fake_db, score=88, plan_id="pro")           # ...was pro
    seed_usage(fake_db)  # otherwise healthy
    seed_user(fake_db)
    b = client.get("/api/support-ai/churn").json()
    assert b["tier"] == "watch"
    assert any("plan_downgrade" in r for r in b["reasons"])
    assert b["flag_id"]


def test_plan_rank_unlimited_above_empire():
    # Unlimited (2026-10-17) sits strictly above Empire in the ladder; if it
    # were missing from PLAN_RANK it would rank 0 and every Empire->Unlimited
    # upgrade would be misread as a downgrade by the churn signal below.
    assert sai_mod.PLAN_RANK["unlimited"] > sai_mod.PLAN_RANK["empire"]


def test_churn_no_downgrade_flag_on_empire_to_unlimited(client, fake_db):
    as_user()
    seed_tenant(fake_db, status="active", plan_id="unlimited")  # now unlimited...
    seed_health(fake_db, score=88, plan_id="empire")            # ...was empire
    seed_usage(fake_db)  # otherwise healthy
    seed_user(fake_db)
    b = client.get("/api/support-ai/churn").json()
    assert not any("plan_downgrade" in r for r in b["reasons"])


def test_churn_high_on_past_due(client, fake_db):
    as_user()
    seed_tenant(fake_db, status="past_due")
    b = client.get("/api/support-ai/churn").json()
    assert b["tier"] == "high"


def test_churn_watch_on_ticket_spike_alone(client, fake_db):
    as_user()
    seed_tenant(fake_db)
    seed_usage(fake_db)
    seed_user(fake_db)
    seed_ticket(fake_db, n=3)
    b = client.get("/api/support-ai/churn").json()
    assert b["tier"] == "watch"
    assert any("ticket_spike" in r for r in b["reasons"])


def test_churn_requires_owner_or_admin(client):
    as_user(role="tech")
    r = client.get("/api/support-ai/churn")
    assert r.status_code == 403
