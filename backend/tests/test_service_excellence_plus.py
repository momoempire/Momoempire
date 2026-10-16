"""Tests for the wave/service-excellence-plus hardening of the Wave 8
customer-service layer. Covers what the gap audit found:

- Spanish (ES) sentiment scoring + escalation (lexicons were EN-only)
- churn rule matching the FULL message text (was the 160-char excerpt)
- escalations carrying a full transcript snapshot
- auto-tickets written in the canonical helpdesk shape
- instant escalation: inbox SMS ingest auto-analyzes (was a manual button)
- missed-call rescue flipping to escalated when the caller replies
- satisfaction pulse dispatched on thread resolve
- SLA breach recorded once (stamp + audit event), not just displayed
- resolution notes stored; KB draft (flywheel) created on resolve
- service benchmarks with the >=5-tenant privacy floor
- monitor recovery posted to the auto-incident timeline
- the satisfaction pulse lifecycle incl. the leaderboard event

Same fake in-memory mongo pattern as the Wave 8 suites.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "se-plus-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "se_plus_test")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.conversation_intel as ci_mod  # noqa: E402
import routers.inbox as inbox_mod  # noqa: E402
import routers.platform_status as ps_mod  # noqa: E402
import routers.satisfaction as sat_mod  # noqa: E402
import routers.support_ai as sai_mod  # noqa: E402
import routers.support_tickets as st_mod  # noqa: E402
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
        return SimpleNamespace(matched_count=0, modified_count=0)


class FakeDB:
    def __init__(self):
        for name in (
            "inbox_threads", "inbox_messages", "conversations", "conv_messages",
            "sentiment_scores", "escalations", "escalation_rules", "support_tickets",
            "ticket_events", "ticket_replies", "sla_policies", "kb_article_drafts",
            "satisfaction_pulses", "gamification_events", "gamification_rules",
            "missed_call_rescues", "outreach_queue", "message_templates",
            "status_components", "status_incidents", "status_updates",
            "status_monitors", "monitor_results", "knowledge_chunks",
            "support_answers", "support_feedback", "shop_health_scores",
            "churn_flags", "usage_events", "users", "tenants", "customers",
            "appointments", "maintenance_agreements",
        ):
            setattr(self, name, FakeCollection())


current_user = {}


def _guard_owner(user):
    if user.get("role") not in {"owner", "admin"}:
        raise HTTPException(403, "Owner/admin role required")
    return user


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    for mod in (ci_mod, inbox_mod, ps_mod, sat_mod, sai_mod, st_mod):
        monkeypatch.setattr(mod, "get_db", lambda: fake_db)
    app = FastAPI()
    for mod in (ci_mod, inbox_mod, ps_mod, sat_mod, sai_mod, st_mod):
        app.include_router(mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    app.dependency_overrides[require_tenant_owner_or_admin] = lambda: _guard_owner(current_user)
    return TestClient(app)


def as_user(tenant_id="t1", role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def seed_thread(fake_db, thread_id="th-1", tenant_id="t1", messages=(),
                customer_name="Kim", channel="sms"):
    fake_db.inbox_threads.docs.append({
        "id": thread_id, "tenant_id": tenant_id, "customer_name": customer_name,
        "customer_contact": "+15550100", "customer_phone": "+15550100",
        "channel": channel, "status": "open", "urgent": False,
        "created_at": "2026-10-15T10:00:00+00:00",
        "updated_at": "2026-10-15T10:00:00+00:00",
        "last_message_at": "2026-10-15T10:00:00+00:00", "last_message_preview": "",
    })
    for i, (direction, text) in enumerate(messages):
        fake_db.inbox_messages.docs.append({
            "id": f"{thread_id}-m{i}", "tenant_id": tenant_id, "thread_id": thread_id,
            "direction": direction, "sender": "customer" if direction == "in" else "shop",
            "body": text, "created_at": f"2026-10-15T10:{i:02d}:00+00:00",
        })


# ---------- ES sentiment ----------

def test_spanish_negative_message_scores_and_escalates(client, fake_db):
    as_user()
    seed_thread(fake_db, messages=[
        ("in", "El servicio fue terrible, llegaron tarde y el técnico fue grosero. Estoy muy enojado."),
        ("in", "Nadie me ayuda, es un desastre total, pésimo trabajo."),
    ])
    b = client.post("/api/conversation-intel/analyze/thread/th-1").json()
    assert b["overall_label"] == "negative"
    assert b["counts"]["negative"] == 2
    assert b["thread_flagged_urgent"] is True
    assert len(fake_db.escalations.docs) >= 1


def test_spanish_positive_message_scores_positive(client, fake_db):
    as_user()
    seed_thread(fake_db, messages=[
        ("in", "¡Muchas gracias! El trabajo fue excelente y el técnico muy amable y puntual."),
    ])
    b = client.post("/api/conversation-intel/analyze/thread/th-1").json()
    assert b["overall_label"] == "positive"
    assert b["escalations"] == []


def test_spanish_churn_language_escalates(client, fake_db):
    as_user()
    seed_thread(fake_db, messages=[
        ("in", "Quiero cancelar el servicio y exijo un reembolso completo."),
    ])
    b = client.post("/api/conversation-intel/analyze/thread/th-1").json()
    rules = {e["rule_key"] for e in b["escalations"]}
    assert "churn_keyword" in rules


# ---------- churn rule: full text, not the excerpt ----------

def test_churn_word_late_in_long_message_still_escalates(client, fake_db):
    as_user()
    filler = " ".join(["the technician came by and looked at the unit and "
                       "we talked about the weather and the schedule"] * 6)
    assert len(filler) > 200  # churn word lands well past the 160-char excerpt
    seed_thread(fake_db, messages=[("in", filler + " — anyway, I want a refund.")])
    b = client.post("/api/conversation-intel/analyze/thread/th-1").json()
    rules = {e["rule_key"] for e in b["escalations"]}
    assert "churn_keyword" in rules


# ---------- transcript + canonical auto-ticket ----------

def test_escalation_carries_full_transcript(client, fake_db):
    as_user()
    seed_thread(fake_db, messages=[
        ("in", "This is terrible, the worst service ever."),
        ("out", "We're so sorry — a manager is looking into this now."),
        ("in", "Still nothing? I want a refund right now."),
    ])
    client.post("/api/conversation-intel/analyze/thread/th-1")
    esc = fake_db.escalations.docs[0]
    transcript = esc["transcript"]
    assert len(transcript) == 3
    assert transcript[0]["role"] == "customer"
    assert transcript[1]["role"] == "business"
    assert "refund" in transcript[2]["text"]


def test_auto_ticket_uses_canonical_helpdesk_shape(client, fake_db):
    as_user()
    seed_thread(fake_db, messages=[("in", "I want to cancel and get a refund, calling my lawyer.")])
    client.post("/api/conversation-intel/analyze/thread/th-1")
    # Several rules fire on this message; every auto-ticket must be canonical.
    assert len(fake_db.support_tickets.docs) >= 1
    for t in fake_db.support_tickets.docs:
        assert t["subject"].startswith("[")  # "[CRITICAL] Churn / cancellation language — Kim"
        assert t["description"]  # the rule reason, not a missing "body"
        assert "title" not in t and "body" not in t  # the old broken shape
        assert t["priority"] in ("low", "medium", "high", "urgent")
        assert t["status"] == "open"
        assert t["waiting_seconds"] == 0.0
        assert t["status_changed_at"]
        assert t["source"] == "sentiment-escalation"
    # The critical churn escalation maps to an urgent ticket.
    churn_esc = next(e for e in fake_db.escalations.docs if e["rule_key"] == "churn_keyword")
    churn_ticket = next(t for t in fake_db.support_tickets.docs
                        if t["escalation_id"] == churn_esc["id"])
    assert churn_ticket["priority"] == "urgent"


# ---------- instant escalation on ingest ----------

def test_sms_ingest_auto_escalates_without_manual_analyze(client, fake_db):
    as_user()
    r1 = client.post("/api/inbox/ingest/sms",
                     json={"From": "+15550100", "Body": "This is terrible, awful service."})
    assert r1.json()["analysis"] is not None
    r2 = client.post("/api/inbox/ingest/sms",
                     json={"From": "+15550100", "Body": "Still broken and I am furious, worst ever."})
    b2 = r2.json()
    assert b2["analysis"]["escalations_created"] >= 1
    assert b2["analysis"]["thread_flagged_urgent"] is True
    assert len(fake_db.escalations.docs) >= 1


def test_sms_ingest_survives_a_happy_path_without_escalation(client, fake_db):
    as_user()
    r = client.post("/api/inbox/ingest/sms",
                    json={"From": "+15550100", "Body": "Thanks, all good here!"})
    b = r.json()
    assert b["ok"] is True
    assert b["analysis"]["escalations_created"] == 0
    assert fake_db.escalations.docs == []


# ---------- missed-call rescue reply flip ----------

def test_inbound_sms_flips_queued_missed_call_rescue(client, fake_db):
    as_user()
    fake_db.missed_call_rescues.docs.append({
        "id": "resc-1", "tenant_id": "t1", "conversation_id": "conv-9",
        "caller_phone": "+15550100", "status": "queued", "escalated": False,
        "created_at": "2026-10-15T09:00:00+00:00",
    })
    r = client.post("/api/inbox/ingest/sms",
                    json={"From": "+15550100", "Body": "Hi, returning your text."})
    assert r.json()["rescue_escalated"] is True
    doc = fake_db.missed_call_rescues.docs[0]
    assert doc["status"] == "replied"
    assert doc["escalated"] is True
    assert doc["escalated_via"] == "sms_reply"


def test_inbound_sms_without_rescue_is_unaffected(client, fake_db):
    as_user()
    r = client.post("/api/inbox/ingest/sms",
                    json={"From": "+15559999", "Body": "Hello?"})
    assert r.json()["rescue_escalated"] is False


# ---------- pulse on thread resolve ----------

def test_thread_resolve_dispatches_satisfaction_pulse(client, fake_db):
    as_user()
    seed_thread(fake_db)
    b = client.post("/api/inbox/threads/th-1/resolve").json()
    assert b["status"] == "resolved"
    assert b["satisfaction_pulse_id"]
    assert len(fake_db.satisfaction_pulses.docs) == 1
    pulse = fake_db.satisfaction_pulses.docs[0]
    assert pulse["source_type"] == "thread" and pulse["source_id"] == "th-1"
    # Re-resolving must not double-send.
    client.post("/api/inbox/threads/th-1/resolve")
    assert len(fake_db.satisfaction_pulses.docs) == 1


# ---------- SLA breach handling ----------

def _seed_breached_ticket(fake_db):
    from datetime import datetime, timedelta, timezone
    created = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    fake_db.support_tickets.docs.append({
        "id": "tk-breach", "tenant_id": "t1", "subject": "Help",
        "description": "broken", "priority": "high", "category": "bug",
        "status": "in_progress", "assignee": None, "contact_name": "",
        "contact_email": "", "contact_phone": "", "created_by": "u-t1",
        "created_at": created, "updated_at": created, "status_changed_at": created,
        "waiting_seconds": 0.0, "resolved_at": None,
    })


def test_sla_breach_recorded_once_on_detail_read(client, fake_db):
    as_user()
    _seed_breached_ticket(fake_db)  # high: 1h first response; 2h old, no reply
    b = client.get("/api/support-tickets/tk-breach").json()
    assert b["sla"]["overall"] == "breached"
    doc = fake_db.support_tickets.docs[0]
    assert doc.get("sla_breach_recorded_at")
    kinds = [e["kind"] for e in fake_db.ticket_events.docs]
    assert kinds.count("sla_breached") == 1
    # Second read: still exactly one breach event.
    client.get("/api/support-tickets/tk-breach")
    kinds = [e["kind"] for e in fake_db.ticket_events.docs]
    assert kinds.count("sla_breached") == 1


def test_on_track_ticket_gets_no_breach_event(client, fake_db):
    as_user()
    t = client.post("/api/support-tickets/",
                    json={"subject": "Quick question", "priority": "low"}).json()
    client.get(f"/api/support-tickets/{t['id']}")
    kinds = [e["kind"] for e in fake_db.ticket_events.docs]
    assert "sla_breached" not in kinds


# ---------- resolve: resolution stored + KB flywheel + pulse ----------

def test_resolve_stores_note_drafts_kb_and_pulses(client, fake_db):
    as_user()
    t = client.post("/api/support-tickets/",
                    json={"subject": "Billing question", "description": "Charged twice?",
                          "priority": "medium", "contact_email": "shop@example.com"}).json()
    b = client.post(f"/api/support-tickets/{t['id']}/resolve",
                    json={"resolution_note": "Refunded the duplicate charge; see invoice 42."}).json()
    assert b["status"] == "resolved"
    doc = fake_db.support_tickets.docs[0]
    assert doc["resolution_note"].startswith("Refunded")
    assert doc["resolved_by"] == "u-t1"
    # Flywheel draft
    assert len(fake_db.kb_article_drafts.docs) == 1
    draft = fake_db.kb_article_drafts.docs[0]
    assert draft["status"] == "draft"
    assert draft["question"] == "Charged twice?"
    assert "duplicate charge" in draft["answer"]
    # Listed for review
    lst = client.get("/api/support-tickets/kb-drafts").json()
    assert lst["count"] == 1
    # Pulse dispatched to the shop contact
    assert len(fake_db.satisfaction_pulses.docs) == 1
    assert fake_db.satisfaction_pulses.docs[0]["source_type"] == "ticket"


def test_resolve_without_note_creates_no_kb_draft(client, fake_db):
    as_user()
    t = client.post("/api/support-tickets/", json={"subject": "Hi"}).json()
    client.post(f"/api/support-tickets/{t['id']}/resolve", json={})
    assert fake_db.kb_article_drafts.docs == []


# ---------- support-ai canonical escalation ticket ----------

def test_support_ai_escalation_ticket_shape_is_canonical(client, fake_db):
    as_user()
    fake_db.tenants.docs.append({"id": "t1", "name": "Kim's HVAC"})
    b = client.post("/api/support-ai/ask",
                    json={"question": "How do I frobnicate the widget?"}).json()
    assert b["escalated"] is True
    t = fake_db.support_tickets.docs[0]
    assert t["priority"] == "medium"  # was "normal" — not a helpdesk priority
    assert t["status"] == "open"
    assert t["status_changed_at"]
    assert t["waiting_seconds"] == 0.0
    assert t["category"] == "ai-escalation"


# ---------- service benchmarks ----------

def _seed_score(fake_db, tenant_id, label="positive", avg=1.0):
    fake_db.sentiment_scores.docs.append({
        "id": f"ss-{tenant_id}-{len(fake_db.sentiment_scores.docs)}",
        "tenant_id": tenant_id, "thread_id": f"th-{tenant_id}",
        "overall_label": label, "avg_score": avg,
        "analyzed_at": "2026-10-15T10:00:00+00:00",
    })


def test_benchmarks_privacy_floor_hides_network(client, fake_db):
    as_user()
    _seed_score(fake_db, "t1", label="negative", avg=-2.0)
    _seed_score(fake_db, "t2")
    _seed_score(fake_db, "t3")
    b = client.get("/api/conversation-intel/benchmarks").json()
    assert b["insufficient_data"] is True
    assert "network" not in b
    assert b["mine"]["threads_analyzed"] == 1
    assert b["mine"]["negative_thread_ratio"] == 1.0


def test_benchmarks_network_appears_at_five_tenants(client, fake_db):
    as_user()
    _seed_score(fake_db, "t1", label="negative", avg=-2.0)
    for tid in ("t2", "t3", "t4", "t5"):
        _seed_score(fake_db, tid, label="positive", avg=2.0)
    b = client.get("/api/conversation-intel/benchmarks").json()
    assert b["insufficient_data"] is False
    assert b["contributor_count"] == 5
    assert b["network"]["threads_analyzed"] == 5
    assert b["network"]["negative_thread_ratio"] == 0.2


# ---------- monitor recovery ----------

def test_monitor_recovery_posts_to_auto_incident(client, fake_db):
    as_user()
    comps = client.get("/api/platform-status/components").json()["components"]
    mon = client.post("/api/platform-status/monitors",
                      json={"name": "API ping", "component_id": comps[0]["id"]}).json()
    incident_id = None
    for _ in range(3):
        r = client.post(f"/api/platform-status/monitors/{mon['id']}/check",
                        json={"success": False}).json()
        incident_id = r["incident"] or incident_id
    assert incident_id
    r = client.post(f"/api/platform-status/monitors/{mon['id']}/check",
                    json={"success": True}).json()
    assert r["recovered_incident"] == incident_id
    inc = next(d for d in fake_db.status_incidents.docs if d["id"] == incident_id)
    assert inc["status"] == "monitoring"
    msgs = [u["message"] for u in fake_db.status_updates.docs
            if u["incident_id"] == incident_id]
    assert any("passing again" in m for m in msgs)


# ---------- satisfaction pulse lifecycle ----------

def test_pulse_lifecycle_and_leaderboard_event(client, fake_db):
    as_user()
    b = client.post("/api/satisfaction/pulses/dispatch", json={
        "source_type": "job", "source_id": "job-1",
        "customer_name": "Kim", "customer_contact": "+15550100",
        "tech_id": "tech-7", "tech_name": "Sam",
    }).json()
    pulse = b["pulse"]
    assert pulse["status"] == "sent"
    # Duplicate dispatch returns the same pulse.
    b2 = client.post("/api/satisfaction/pulses/dispatch", json={
        "source_type": "job", "source_id": "job-1"}).json()
    assert b2["pulse"]["id"] == pulse["id"]
    assert len(fake_db.satisfaction_pulses.docs) == 1

    r = client.post(f"/api/satisfaction/pulses/{pulse['id']}/respond",
                    json={"rating": 5, "comment": "Great work!"}).json()
    assert r["pulse"]["status"] == "responded"
    assert r["leaderboard_event"]["points"] == 25
    events = fake_db.gamification_events.docs
    assert len(events) == 1 and events[0]["event_type"] == "5_star_review"
    assert events[0]["tech_id"] == "tech-7"

    # Second response rejected.
    r2 = client.post(f"/api/satisfaction/pulses/{pulse['id']}/respond",
                     json={"rating": 1})
    assert r2.status_code == 409

    s = client.get("/api/satisfaction/summary").json()
    assert s["sent"] == 1 and s["responded"] == 1
    assert s["avg_rating"] == 5.0
    assert s["distribution"]["5"] == 1
    assert s["by_tech"][0]["tech_id"] == "tech-7"
    assert s["promoters"] == 1


def test_low_rating_pulse_awards_no_points(client, fake_db):
    as_user()
    b = client.post("/api/satisfaction/pulses/dispatch", json={
        "source_type": "ticket", "source_id": "tk-1", "tech_id": "tech-7"}).json()
    client.post(f"/api/satisfaction/pulses/{b['pulse']['id']}/respond",
                json={"rating": 2, "comment": "meh"})
    assert fake_db.gamification_events.docs == []
    s = client.get("/api/satisfaction/summary").json()
    assert s["detractors"] == 1
