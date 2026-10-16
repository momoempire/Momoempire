"""Tests for routers/conversation_intel.py (conversation intelligence +
sentiment escalation).

Uses a fake in-memory mongo (same pattern as test_reputation.py): no live
database. Covers keyword sentiment scoring (positive/negative/neutral),
consecutive-negative escalation, churn-keyword escalation, rule updates
taking effect, resolution clearing the queue, and tenant isolation.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "convo-intel-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "convo_intel_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.conversation_intel as ci_mod  # noqa: E402
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
        self.inbox_threads = FakeCollection()
        self.inbox_messages = FakeCollection()
        self.conversations = FakeCollection()
        self.conv_messages = FakeCollection()
        self.sentiment_scores = FakeCollection()
        self.escalations = FakeCollection()
        self.escalation_rules = FakeCollection()
        self.support_tickets = FakeCollection()


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(ci_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(ci_mod.router)
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


def seed_inbox_thread(fake_db, thread_id="th-1", tenant_id="t1", messages=(),
                      customer_name="Kim", channel="sms"):
    fake_db.inbox_threads.docs.append({
        "id": thread_id, "tenant_id": tenant_id, "customer_name": customer_name,
        "channel": channel, "urgent": False,
        "created_at": "2026-10-15T10:00:00+00:00",
    })
    for i, (direction, text) in enumerate(messages):
        fake_db.inbox_messages.docs.append({
            "id": f"{thread_id}-m{i}", "tenant_id": tenant_id, "thread_id": thread_id,
            "direction": direction, "body": text,
            "created_at": f"2026-10-15T10:{i:02d}:00+00:00",
        })


def analyze(client, thread_id):
    return client.post(f"/api/conversation-intel/analyze/thread/{thread_id}")


# ---------- sentiment scoring ----------

def test_scoring_positive_thread(client, fake_db):
    as_user()
    seed_inbox_thread(fake_db, messages=[
        ("inbound", "Thank you so much! Great job today, I'm very happy with the work."),
        ("outbound", "Glad to hear it!"),
        ("inbound", "Your tech was so friendly and professional. Highly recommend!"),
    ])
    b = analyze(client, "th-1").json()
    assert b["overall_label"] == "positive"
    assert b["counts"]["positive"] == 2
    assert b["escalations"] == []
    assert b["thread_flagged_urgent"] is False


def test_scoring_negative_thread(client, fake_db):
    as_user()
    seed_inbox_thread(fake_db, messages=[
        ("inbound", "This is terrible. Worst service ever, I'm furious."),
    ])
    b = analyze(client, "th-1").json()
    assert b["overall_label"] == "negative"
    assert b["counts"]["negative"] == 1


def test_scoring_neutral_thread(client, fake_db):
    as_user()
    seed_inbox_thread(fake_db, messages=[
        ("inbound", "What time is the appointment tomorrow?"),
        ("outbound", "Between 9 and 11."),
        ("inbound", "Ok, see you then."),
    ])
    b = analyze(client, "th-1").json()
    assert b["overall_label"] == "neutral"
    assert b["avg_score"] == 0.0
    assert b["escalations"] == []


def test_only_customer_messages_scored(client, fake_db):
    as_user()
    # Business apologizes with negative words — must not count against sentiment.
    seed_inbox_thread(fake_db, messages=[
        ("outbound", "We're so sorry for the terrible delay, this is unacceptable."),
        ("inbound", "Thanks for fixing it so quickly, great service!"),
    ])
    b = analyze(client, "th-1").json()
    assert b["customer_message_count"] == 1
    assert b["counts"] == {"positive": 1, "neutral": 0, "negative": 0}
    assert b["overall_label"] == "positive"


def test_analyze_unknown_thread_404(client):
    as_user()
    r = analyze(client, "nope")
    assert r.status_code == 404


def test_score_text_unit():
    assert ci_mod.score_text("Thanks, amazing work!")["label"] == "positive"
    assert ci_mod.score_text("Terrible, awful, horrible experience")["label"] == "negative"
    assert ci_mod.score_text("See you at noon")["label"] == "neutral"
    # Churn language forces negative
    s = ci_mod.score_text("I want a full refund right now")
    assert s["label"] == "negative" and "refund" in s["churn"]


# ---------- escalations ----------

def test_two_negatives_in_a_row_escalates(client, fake_db):
    as_user()
    seed_inbox_thread(fake_db, thread_id="th-neg2", messages=[
        ("inbound", "This is terrible. Worst service ever."),
        ("inbound", "I'm so angry and frustrated with this mess."),
    ])
    b = analyze(client, "th-neg2").json()
    assert b["max_consecutive_negatives"] == 2
    keys = [e["rule_key"] for e in b["escalations"]]
    assert "consecutive_negatives" in keys
    assert b["thread_flagged_urgent"] is True
    esc = next(e for e in b["escalations"] if e["rule_key"] == "consecutive_negatives")
    assert esc["status"] == "open" and esc["created"] is True
    # Thread flagged urgent + support ticket opened by the rule
    assert fake_db.inbox_threads.docs[0]["urgent"] is True
    assert len(fake_db.support_tickets.docs) >= 1


def test_single_negative_does_not_escalate_by_default(client, fake_db):
    as_user()
    seed_inbox_thread(fake_db, thread_id="th-neg1", messages=[
        ("inbound", "Thanks, great work!"),
        ("inbound", "Actually this one thing was a bit disappointing."),
        ("inbound", "But overall I'm happy with it."),
    ])
    b = analyze(client, "th-neg1").json()
    assert b["max_consecutive_negatives"] == 1
    assert b["escalations"] == []


def test_churn_keyword_escalates(client, fake_db):
    as_user()
    seed_inbox_thread(fake_db, thread_id="th-churn", messages=[
        ("inbound", "The tech never showed up and I'm done waiting."),
        ("inbound", "I want a full refund right now or I'm calling my lawyer."),
    ])
    b = analyze(client, "th-churn").json()
    keys = [e["rule_key"] for e in b["escalations"]]
    assert "churn_keyword" in keys
    esc = next(e for e in b["escalations"] if e["rule_key"] == "churn_keyword")
    assert esc["severity"] == "critical"
    assert "refund" in esc["reason"] and "lawyer" in esc["reason"]
    assert b["churn_hits"] >= 1


def test_escalation_not_duplicated_on_reanalyze(client, fake_db):
    as_user()
    seed_inbox_thread(fake_db, thread_id="th-dup", messages=[
        ("inbound", "This is terrible."),
        ("inbound", "Truly awful service."),
    ])
    b1 = analyze(client, "th-dup").json()
    created = sum(1 for e in b1["escalations"] if e["created"])
    b2 = analyze(client, "th-dup").json()
    created2 = sum(1 for e in b2["escalations"] if e["created"])
    assert created >= 1 and created2 == 0
    open_esc = [d for d in fake_db.escalations.docs
                if d["thread_id"] == "th-dup" and d["status"] == "open"]
    assert len(open_esc) == len(b1["escalations"])


def test_negative_ratio_rule(client, fake_db):
    as_user()
    seed_inbox_thread(fake_db, thread_id="th-ratio", messages=[
        ("inbound", "Thanks!"),
        ("inbound", "This part was awful."),
        ("inbound", "And this was terrible too."),
        ("inbound", "Honestly the whole visit was disappointing."),
    ])
    b = analyze(client, "th-ratio").json()
    keys = [e["rule_key"] for e in b["escalations"]]
    assert "negative_ratio" in keys  # 3/4 negative >= 60%


def test_severe_single_message_rule(client, fake_db):
    as_user()
    seed_inbox_thread(fake_db, thread_id="th-severe", messages=[
        ("inbound", "Terrible, awful, horrible, disgusting disaster of a visit."),
    ])
    b = analyze(client, "th-severe").json()
    keys = [e["rule_key"] for e in b["escalations"]]
    assert "severe_message" in keys


def test_escalations_queue(client, fake_db):
    as_user()
    seed_inbox_thread(fake_db, thread_id="th-q1", messages=[
        ("inbound", "Terrible."), ("inbound", "Awful."),
    ])
    seed_inbox_thread(fake_db, thread_id="th-q2", customer_name="Lee", messages=[
        ("inbound", "I want a refund."),
    ])
    analyze(client, "th-q1")
    analyze(client, "th-q2")
    b = client.get("/api/conversation-intel/escalations").json()
    assert b["count"] >= 2
    assert b["escalations"][0]["severity"] == "critical"  # critical floats up
    names = {e["customer_name"] for e in b["escalations"]}
    assert {"Kim", "Lee"} <= names


# ---------- resolve ----------

def test_resolve_clears_escalation_and_urgent(client, fake_db):
    as_user()
    seed_inbox_thread(fake_db, thread_id="th-res", messages=[
        ("inbound", "Terrible."), ("inbound", "Awful."),
    ])
    b = analyze(client, "th-res").json()
    for esc in b["escalations"]:
        r = client.post(f"/api/conversation-intel/escalations/{esc['id']}/resolve")
        assert r.status_code == 200
    q = client.get("/api/conversation-intel/escalations").json()
    assert q["count"] == 0
    resolved = client.get("/api/conversation-intel/escalations?status=resolved").json()
    assert resolved["count"] == len(b["escalations"])
    assert fake_db.inbox_threads.docs[0]["urgent"] is False


def test_resolve_keeps_urgent_while_others_open(client, fake_db):
    as_user()
    seed_inbox_thread(fake_db, thread_id="th-part", messages=[
        ("inbound", "Terrible."),
        ("inbound", "Awful, horrible disaster of a visit."),
    ])
    b = analyze(client, "th-part").json()
    assert len(b["escalations"]) >= 2
    first = b["escalations"][0]
    client.post(f"/api/conversation-intel/escalations/{first['id']}/resolve")
    # One still open -> thread stays urgent
    assert fake_db.inbox_threads.docs[0]["urgent"] is True
    q = client.get("/api/conversation-intel/escalations").json()
    assert q["count"] == len(b["escalations"]) - 1


def test_resolve_nonexistent_404(client):
    as_user()
    r = client.post("/api/conversation-intel/escalations/nope/resolve")
    assert r.status_code == 404


def test_resolve_non_owner_forbidden(client, fake_db):
    as_user()
    seed_inbox_thread(fake_db, thread_id="th-forb", messages=[
        ("inbound", "Terrible."), ("inbound", "Awful."),
    ])
    esc_id = analyze(client, "th-forb").json()["escalations"][0]["id"]
    as_user(role="tech")
    r = client.post(f"/api/conversation-intel/escalations/{esc_id}/resolve")
    assert r.status_code == 403


# ---------- rules ----------

def test_rules_seeded_with_defaults(client):
    as_user()
    b = client.get("/api/conversation-intel/rules").json()
    keys = {r["key"] for r in b["rules"]}
    assert {"consecutive_negatives", "churn_keyword", "negative_ratio", "severe_message"} <= keys
    cn = next(r for r in b["rules"] if r["key"] == "consecutive_negatives")
    assert cn["enabled"] is True and cn["params"]["count"] == 2


def test_rule_update_takes_effect(client, fake_db):
    as_user()
    # Raise the consecutive-negatives threshold to 3: 2 negatives no longer fire.
    r = client.put("/api/conversation-intel/rules", json={"rules": [{
        "key": "consecutive_negatives", "name": "N in a row",
        "enabled": True, "severity": "high", "auto_ticket": True,
        "params": {"count": 3},
    }]})
    assert r.status_code == 200
    seed_inbox_thread(fake_db, thread_id="th-ru1", messages=[
        ("inbound", "Terrible."), ("inbound", "Awful."),
    ])
    b = analyze(client, "th-ru1").json()
    keys = [e["rule_key"] for e in b["escalations"]]
    assert "consecutive_negatives" not in keys
    # 3 negatives DO fire.
    seed_inbox_thread(fake_db, thread_id="th-ru2", messages=[
        ("inbound", "Terrible."), ("inbound", "Awful."), ("inbound", "Horrible."),
    ])
    b2 = analyze(client, "th-ru2").json()
    assert "consecutive_negatives" in [e["rule_key"] for e in b2["escalations"]]


def test_rule_disable_stops_escalation(client, fake_db):
    as_user()
    client.put("/api/conversation-intel/rules", json={"rules": [{
        "key": "consecutive_negatives", "enabled": False,
        "severity": "high", "auto_ticket": False, "params": {"count": 2},
    }]})
    seed_inbox_thread(fake_db, thread_id="th-dis", messages=[
        ("inbound", "Thanks!"),
        ("inbound", "This part was awful."),
        ("inbound", "And this bit was bad too."),
    ])
    b = analyze(client, "th-dis").json()
    keys = [e["rule_key"] for e in b["escalations"]]
    assert "consecutive_negatives" not in keys


def test_update_rules_non_owner_forbidden(client):
    as_user(role="tech")
    r = client.put("/api/conversation-intel/rules", json={"rules": [
        {"key": "churn_keyword", "enabled": False}]})
    assert r.status_code == 403


# ---------- overview & timeline ----------

def test_overview_distribution_and_trend(client, fake_db):
    as_user()
    seed_inbox_thread(fake_db, thread_id="th-ov1", messages=[
        ("inbound", "Thanks, great work!"),
    ])
    seed_inbox_thread(fake_db, thread_id="th-ov2", messages=[
        ("inbound", "Terrible service."),
        ("inbound", "Worst experience ever."),
    ])
    seed_inbox_thread(fake_db, thread_id="th-ov3", messages=[
        ("inbound", "What time tomorrow?"),
    ])
    analyze(client, "th-ov1")
    analyze(client, "th-ov2")
    analyze(client, "th-ov3")
    b = client.get("/api/conversation-intel/sentiment/overview").json()
    assert b["threads_analyzed"] == 3
    assert b["distribution"] == {"positive": 1, "neutral": 1, "negative": 1}
    assert b["open_escalations"] >= 1
    assert len(b["sentiment_trend"]) == 4
    # All three analyses are in the current week bucket
    assert b["sentiment_trend"][-1]["threads"] == 3


def test_overview_empty_tenant(client):
    as_user(tenant_id="empty")
    b = client.get("/api/conversation-intel/sentiment/overview").json()
    assert b["threads_analyzed"] == 0
    assert b["distribution"] == {"positive": 0, "neutral": 0, "negative": 0}
    assert b["open_escalations"] == 0


def test_timeline_endpoint(client, fake_db):
    as_user()
    seed_inbox_thread(fake_db, thread_id="th-tl", messages=[
        ("inbound", "Thanks!"),
        ("inbound", "This was awful."),
    ])
    analyze(client, "th-tl")
    b = client.get("/api/conversation-intel/threads/th-tl/timeline").json()
    assert b["thread_id"] == "th-tl"
    assert len(b["analysis"]["message_scores"]) == 2
    assert b["analysis"]["message_scores"][0]["label"] == "positive"
    assert b["analysis"]["message_scores"][1]["label"] == "negative"


def test_timeline_missing_404(client):
    as_user()
    r = client.get("/api/conversation-intel/threads/nope/timeline")
    assert r.status_code == 404


# ---------- tenant isolation ----------

def test_tenant_isolation(client, fake_db):
    as_user(tenant_id="t1")
    seed_inbox_thread(fake_db, tenant_id="t1", thread_id="th-iso", messages=[
        ("inbound", "Thanks, great!"),
    ])
    seed_inbox_thread(fake_db, tenant_id="t2", thread_id="th-iso", messages=[
        ("inbound", "Terrible."),
    ])
    analyze(client, "th-iso")
    b = client.get("/api/conversation-intel/sentiment/overview").json()
    assert b["threads_analyzed"] == 1
    assert b["distribution"]["positive"] == 1
    q = client.get("/api/conversation-intel/escalations").json()
    assert q["count"] == 0  # t1's thread didn't escalate
    as_user(tenant_id="t2")
    analyze(client, "th-iso")
    b2 = client.get("/api/conversation-intel/sentiment/overview").json()
    assert b2["distribution"]["negative"] == 1
    # t2's rule update doesn't leak into t1
    client.put("/api/conversation-intel/rules", json={"rules": [
        {"key": "churn_keyword", "enabled": False}]})
    as_user(tenant_id="t1")
    rules = client.get("/api/conversation-intel/rules").json()
    churn = next(r for r in rules["rules"] if r["key"] == "churn_keyword")
    assert churn["enabled"] is True


# ---------- conversations fallback read ----------

def test_conversations_shape_fallback(client, fake_db):
    as_user()
    fake_db.conversations.docs.append({
        "id": "conv-1", "tenant_id": "t1", "customer_name": "Zed",
        "channel": "call", "urgent": False,
        "created_at": "2026-10-15T10:00:00+00:00",
    })
    fake_db.conv_messages.docs.extend([
        {"id": "cm-1", "tenant_id": "t1", "conversation_id": "conv-1",
         "role": "caller", "content": "Terrible service, I'm furious.",
         "created_at": "2026-10-15T10:01:00+00:00"},
        {"id": "cm-2", "tenant_id": "t1", "conversation_id": "conv-1",
         "role": "caller", "content": "I want to cancel everything.",
         "created_at": "2026-10-15T10:02:00+00:00"},
    ])
    b = analyze(client, "conv-1").json()
    assert b["source"] == "conversations"
    assert b["overall_label"] == "negative"
    assert "churn_keyword" in [e["rule_key"] for e in b["escalations"]]
    assert fake_db.conversations.docs[0]["urgent"] is True
