"""Tests for routers/outreach.py (proactive outreach + missed-call rescue +
multilingual support).

Uses a fake in-memory mongo (same pattern as test_reputation.py): no live
database. Covers campaign CRUD + toggle, trigger evaluation (matching vs
non-matching), run dedupe, missed-call rescue + dedupe, template language
fallback, keyword language detection, and tenant isolation.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "outreach-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "outreach_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.outreach as out_mod  # noqa: E402
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
        self.outreach_campaigns = FakeCollection()
        self.outreach_queue = FakeCollection()
        self.missed_call_rescues = FakeCollection()
        self.message_templates = FakeCollection()
        self.customers = FakeCollection()
        self.appointments = FakeCollection()
        self.conversations = FakeCollection()
        self.maintenance_agreements = FakeCollection()
        self.tenants = FakeCollection()


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(out_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(out_mod.router)
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


def _iso_days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


def seed_customer(fake_db, tenant_id="t1", cid="c-1", name="Kim", phone="555-0100",
                 created_days_ago=120, language="en"):
    fake_db.customers.docs.append({
        "id": cid, "tenant_id": tenant_id, "name": name, "phone": phone,
        "language": language, "created_at": _iso_days_ago(created_days_ago),
    })


def seed_appointment(fake_db, tenant_id="t1", aid="a-1", customer_id="c-1",
                     phone="555-0100", name="Kim", status="completed",
                     completed_days_ago=None, start_days_ago=None):
    fake_db.appointments.docs.append({
        "id": aid, "tenant_id": tenant_id, "customer_id": customer_id,
        "customer_phone": phone, "customer_name": name, "status": status,
        "completed_at": _iso_days_ago(completed_days_ago) if completed_days_ago is not None else None,
        "start_at": _iso_days_ago(start_days_ago) if start_days_ago is not None else None,
        "created_at": _iso_days_ago(start_days_ago or completed_days_ago or 0),
    })


def seed_missed_call(fake_db, tenant_id="t1", cid="conv-1", phone="555-0100",
                     status="missed", created_days_ago=0):
    fake_db.conversations.docs.append({
        "id": cid, "tenant_id": tenant_id, "channel": "call", "status": status,
        "caller_phone": phone, "caller_name": "Kim",
        "created_at": _iso_days_ago(created_days_ago),
    })


def _create_campaign(client, **kw):
    payload = {"name": "Win-back 90", "trigger_type": "no_booking_days",
               "days": 90, "template_key": "win_back"}
    payload.update(kw)
    return client.post("/api/outreach/campaigns", json=payload)


# ---------- campaign CRUD ----------

def test_create_and_list_campaign(client, fake_db):
    as_user()
    r = _create_campaign(client)
    assert r.status_code == 200
    b = r.json()
    assert b["ok"] and b["enabled"] is True
    assert b["trigger_type"] == "no_booking_days"
    blist = client.get("/api/outreach/campaigns").json()
    assert blist["count"] == 1
    assert blist["campaigns"][0]["name"] == "Win-back 90"
    assert "win-back" in blist["campaigns"][0]["trigger_label"].lower()


def test_create_campaign_invalid_trigger_rejected(client):
    as_user()
    r = _create_campaign(client, trigger_type="bogus")
    assert r.status_code == 422


def test_create_campaign_non_owner_forbidden(client):
    as_user(role="tech")
    r = _create_campaign(client)
    assert r.status_code == 403


def test_patch_campaign(client):
    as_user()
    cid = _create_campaign(client).json()["id"]
    r = client.patch(f"/api/outreach/campaigns/{cid}", json={"days": 60, "name": "Win-back 60"})
    assert r.status_code == 200
    assert r.json()["days"] == 60 and r.json()["name"] == "Win-back 60"


def test_patch_missing_campaign_404(client):
    as_user()
    r = client.patch("/api/outreach/campaigns/nope", json={"days": 60})
    assert r.status_code == 404


def test_toggle_campaign(client):
    as_user()
    cid = _create_campaign(client).json()["id"]
    r = client.post(f"/api/outreach/campaigns/{cid}/toggle")
    assert r.json()["enabled"] is False
    r2 = client.post(f"/api/outreach/campaigns/{cid}/toggle")
    assert r2.json()["enabled"] is True


def test_toggle_missing_campaign_404(client):
    as_user()
    r = client.post("/api/outreach/campaigns/nope/toggle")
    assert r.status_code == 404


# ---------- campaign run: triggers ----------

def test_run_winback_queues_stale_customer_skips_recent(client, fake_db):
    as_user()
    seed_customer(fake_db, cid="c-old", name="Old", phone="555-0100", created_days_ago=200)
    seed_customer(fake_db, cid="c-new", name="New", phone="555-0200", created_days_ago=10)
    seed_appointment(fake_db, aid="a-recent", customer_id="c-new", phone="555-0200",
                     name="New", start_days_ago=5)  # recent activity for c-new
    cid = _create_campaign(client).json()["id"]  # no_booking_days, 90
    b = client.post(f"/api/outreach/campaigns/{cid}/run").json()
    assert b["ok"] and b["evaluated"] == 1 and b["queued"] == 1
    q = client.get("/api/outreach/queue").json()
    assert q["count"] == 1
    msg = q["messages"][0]
    assert msg["to_phone"] == "555-0100" and msg["kind"] == "campaign"
    assert msg["status"] == "pending" and msg["lang"] == "en"


def test_run_review_nudge_completed_job_window(client, fake_db):
    as_user()
    seed_customer(fake_db, cid="c-1", name="Kim", phone="555-0100", created_days_ago=30)
    seed_appointment(fake_db, aid="a-7", customer_id="c-1", phone="555-0100",
                     name="Kim", completed_days_ago=7)   # inside the 7-day window
    seed_appointment(fake_db, aid="a-30", customer_id="c-1", phone="555-0100",
                     name="Kim", completed_days_ago=30)  # outside the window
    cid = _create_campaign(client, name="Review nudge", trigger_type="job_completed_days",
                           days=7, template_key="review_nudge").json()["id"]
    b = client.post(f"/api/outreach/campaigns/{cid}/run").json()
    assert b["queued"] == 1
    msg = client.get("/api/outreach/queue").json()["messages"][0]
    assert "review" in msg["body"].lower()


def test_run_maintenance_due(client, fake_db):
    as_user()
    fake_db.maintenance_agreements.docs.append({
        "id": "m-1", "tenant_id": "t1", "customer_id": "c-1",
        "customer_name": "Kim", "customer_phone": "555-0100",
        "end_date": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(),
    })
    fake_db.maintenance_agreements.docs.append({
        "id": "m-2", "tenant_id": "t1", "customer_id": "c-2",
        "customer_name": "Lee", "customer_phone": "555-0200",
        "end_date": (datetime.now(timezone.utc) + timedelta(days=400)).isoformat(),
    })
    cid = _create_campaign(client, name="Maint due", trigger_type="maintenance_due",
                           days=30, template_key="maintenance_reminder").json()["id"]
    b = client.post(f"/api/outreach/campaigns/{cid}/run").json()
    assert b["queued"] == 1
    assert client.get("/api/outreach/queue").json()["messages"][0]["to_phone"] == "555-0100"


def test_run_disabled_campaign_conflicts(client):
    as_user()
    cid = _create_campaign(client).json()["id"]
    client.post(f"/api/outreach/campaigns/{cid}/toggle")  # disable
    r = client.post(f"/api/outreach/campaigns/{cid}/run")
    assert r.status_code == 409


def test_run_dedupes_repeated_runs(client, fake_db):
    as_user()
    seed_customer(fake_db, cid="c-old", name="Old", phone="555-0100", created_days_ago=200)
    cid = _create_campaign(client).json()["id"]
    b1 = client.post(f"/api/outreach/campaigns/{cid}/run").json()
    b2 = client.post(f"/api/outreach/campaigns/{cid}/run").json()
    assert b1["queued"] == 1
    assert b2["queued"] == 0 and b2["skipped"] == 1
    assert len(fake_db.outreach_queue.docs) == 1


def test_run_skips_customer_without_phone(client, fake_db):
    as_user()
    fake_db.customers.docs.append({
        "id": "c-nophone", "tenant_id": "t1", "name": "Ghost", "phone": "",
        "created_at": _iso_days_ago(300),
    })
    cid = _create_campaign(client).json()["id"]
    b = client.post(f"/api/outreach/campaigns/{cid}/run").json()
    assert b["queued"] == 0


def test_run_non_owner_forbidden(client):
    as_user()
    cid = _create_campaign(client).json()["id"]
    as_user(role="tech")
    r = client.post(f"/api/outreach/campaigns/{cid}/run")
    assert r.status_code == 403


def test_run_missing_campaign_404(client):
    as_user()
    r = client.post("/api/outreach/campaigns/nope/run")
    assert r.status_code == 404


# ---------- queue ----------

def test_cancel_queued_message(client, fake_db):
    as_user()
    seed_customer(fake_db, cid="c-old", name="Old", phone="555-0100", created_days_ago=200)
    cid = _create_campaign(client).json()["id"]
    client.post(f"/api/outreach/campaigns/{cid}/run")
    mid = client.get("/api/outreach/queue").json()["messages"][0]["id"]
    r = client.post(f"/api/outreach/queue/{mid}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "canceled"
    assert client.get("/api/outreach/queue").json()["count"] == 0
    assert client.get("/api/outreach/queue?status=canceled").json()["count"] == 1


def test_cancel_non_pending_conflicts(client, fake_db):
    as_user()
    seed_customer(fake_db, cid="c-old", name="Old", phone="555-0100", created_days_ago=200)
    cid = _create_campaign(client).json()["id"]
    client.post(f"/api/outreach/campaigns/{cid}/run")
    mid = client.get("/api/outreach/queue").json()["messages"][0]["id"]
    client.post(f"/api/outreach/queue/{mid}/cancel")
    r = client.post(f"/api/outreach/queue/{mid}/cancel")
    assert r.status_code == 409


def test_cancel_missing_message_404(client):
    as_user()
    r = client.post("/api/outreach/queue/nope/cancel")
    assert r.status_code == 404


# ---------- missed-call rescue ----------

def test_rescue_queues_sms_and_records(client, fake_db):
    as_user()
    seed_customer(fake_db, cid="c-1", name="Kim", phone="555-0100", language="es")
    seed_missed_call(fake_db, cid="conv-1", phone="555-0100")
    r = client.post("/api/outreach/missed-calls/conv-1/rescue")
    assert r.status_code == 200
    b = r.json()
    assert b["ok"] and b["duplicate"] is False
    assert b["rescue"]["lang"] == "es"  # caller's language preference honored
    q = client.get("/api/outreach/queue").json()
    assert q["count"] == 1
    assert q["messages"][0]["kind"] == "missed_call_rescue"
    assert q["messages"][0]["lang"] == "es"
    assert q["messages"][0]["body"].startswith("¡Perdón")
    assert len(fake_db.missed_call_rescues.docs) == 1
    assert fake_db.missed_call_rescues.docs[0]["escalated"] is False


def test_rescue_dedupes_second_attempt(client, fake_db):
    as_user()
    seed_missed_call(fake_db, cid="conv-1", phone="555-0100")
    assert client.post("/api/outreach/missed-calls/conv-1/rescue").status_code == 200
    r = client.post("/api/outreach/missed-calls/conv-1/rescue")
    assert r.status_code == 409
    assert len(fake_db.outreach_queue.docs) == 1


def test_rescue_non_missed_conversation_rejected(client, fake_db):
    as_user()
    seed_missed_call(fake_db, cid="conv-9", phone="555-0100", status="completed")
    r = client.post("/api/outreach/missed-calls/conv-9/rescue")
    assert r.status_code == 400


def test_rescue_missing_conversation_404(client):
    as_user()
    r = client.post("/api/outreach/missed-calls/nope/rescue")
    assert r.status_code == 404


def test_missed_call_feed_attaches_rescue_status(client, fake_db):
    as_user()
    seed_missed_call(fake_db, cid="conv-1", phone="555-0100")
    seed_missed_call(fake_db, cid="conv-2", phone="555-0200")
    client.post("/api/outreach/missed-calls/conv-1/rescue")
    b = client.get("/api/outreach/missed-calls").json()
    assert b["count"] == 2
    by_id = {c["id"]: c for c in b["missed_calls"]}
    assert by_id["conv-1"]["rescued"] is True
    assert by_id["conv-1"]["rescue"]["queue_id"]
    assert by_id["conv-2"]["rescued"] is False and by_id["conv-2"]["rescue"] is None


def test_rescue_non_owner_forbidden(client, fake_db):
    as_user()
    seed_missed_call(fake_db, cid="conv-1", phone="555-0100")
    as_user(role="tech")
    r = client.post("/api/outreach/missed-calls/conv-1/rescue")
    assert r.status_code == 403


# ---------- templates / multilingual ----------

def test_templates_seeded_en_and_es(client):
    as_user()
    b = client.get("/api/outreach/templates").json()
    assert b["supported_langs"] == ["en", "es"]
    assert b["templates"]["win_back"]["en"].startswith("Hi")
    assert b["templates"]["win_back"]["es"].startswith("Hola")
    assert b["templates"]["missed_call_rescue"]["es"]


def test_template_upsert_owner(client):
    as_user()
    r = client.put("/api/outreach/templates",
                   json={"key": "win_back", "lang": "es", "text": "Hola de nuevo {name}!"})
    assert r.status_code == 200
    assert r.json()["template"]["text"] == "Hola de nuevo {name}!"
    b = client.get("/api/outreach/templates").json()
    assert b["templates"]["win_back"]["es"] == "Hola de nuevo {name}!"


def test_template_upsert_new_key(client):
    as_user()
    r = client.put("/api/outreach/templates",
                   json={"key": "holiday", "lang": "en", "text": "Happy holidays {name}!"})
    assert r.status_code == 200
    b = client.get("/api/outreach/templates").json()
    assert b["templates"]["holiday"]["en"] == "Happy holidays {name}!"


def test_template_upsert_non_owner_forbidden(client):
    as_user(role="tech")
    r = client.put("/api/outreach/templates",
                   json={"key": "win_back", "lang": "es", "text": "x"})
    assert r.status_code == 403


def test_template_language_fallback_es_to_en(client, fake_db):
    as_user()
    # Spanish-preferring customer; the campaign uses a custom template key
    # that only exists in EN -> body falls back to EN.
    seed_customer(fake_db, cid="c-es", name="Rosa", phone="555-0300",
                  created_days_ago=200, language="es")
    client.put("/api/outreach/templates",
               json={"key": "holiday", "lang": "en", "text": "Happy holidays {name}!"})
    cid = _create_campaign(client, name="Holiday", template_key="holiday").json()["id"]
    b = client.post(f"/api/outreach/campaigns/{cid}/run").json()
    assert b["queued"] == 1
    msg = client.get("/api/outreach/queue").json()["messages"][0]
    assert msg["lang"] == "es"  # preference recorded
    assert msg["body"] == "Happy holidays Rosa!"  # ...but body fell back to EN


def test_detect_language_stub():
    assert out_mod._detect_language("Hola, gracias por su servicio") == "es"
    assert out_mod._detect_language("¿Pueden llamarme mañana?") == "es"
    assert out_mod._detect_language("Thanks for the great service!") == "en"
    assert out_mod._detect_language("") == "en"


def test_render_template_unknown_placeholders_safe():
    body = out_mod._render_template("Hi {name}, visit {missing}!", {"name": "Kim"})
    assert body == "Hi Kim, visit !"


# ---------- tenant isolation ----------

def test_tenant_isolation(client, fake_db):
    as_user(tenant_id="t1")
    _create_campaign(client)
    seed_missed_call(fake_db, tenant_id="t1", cid="conv-1", phone="555-0100")
    client.post("/api/outreach/missed-calls/conv-1/rescue")
    client.put("/api/outreach/templates",
               json={"key": "custom", "lang": "en", "text": "t1 only"})

    as_user(tenant_id="t2")
    assert client.get("/api/outreach/campaigns").json()["count"] == 0
    assert client.get("/api/outreach/queue").json()["count"] == 0
    assert client.get("/api/outreach/missed-calls").json()["count"] == 0
    b = client.get("/api/outreach/templates").json()
    assert "custom" not in b["templates"]
    # t2 cannot touch t1's resources
    t1_cid = fake_db.outreach_campaigns.docs[0]["id"]
    assert client.post(f"/api/outreach/campaigns/{t1_cid}/run").status_code == 404
    assert client.post("/api/outreach/missed-calls/conv-1/rescue").status_code == 404
