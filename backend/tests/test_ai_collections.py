"""Tests for the AI collections with empathy router (routers/ai_collections.py).

Zero network, zero database: a small FastAPI app with only the ai-collections
router, a fake in-memory Mongo, and an overridden auth dependency.

Coverage:
- pick_tone unit tests: friendly early, firmer later, plan-offer for the
  hardest cases; never abusive; DNC/dispute/promise overrides
- empathy guardrail: no banned phrases in any shipped template
- overdue list: tenant isolation, AI tone badges, consent surfacing
- sequence CRUD, enrollment (incl. DNC hold + ad-hoc invoice refs)
- touch creation with consent gate (voice blocked without voice_consent)
- touch outcome logging: paid closes enrollments, dnc flags customer
- dashboard KPIs math
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "ai-collections-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "ai_collections_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.ai_collections as ac_mod  # noqa: E402
from security import require_tenant_user  # noqa: E402


# ---------- fake mongo ----------

class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        self.docs = sorted(
            self.docs, key=lambda d: str(d.get(key, "")), reverse=(direction < 0)
        )
        return self

    async def to_list(self, n):
        return [dict(d) for d in self.docs[:n]]


def _match(doc, query):
    for k, cond in query.items():
        val = doc.get(k)
        if isinstance(cond, dict):
            for op, opval in cond.items():
                if op == "$in":
                    if val not in opval:
                        return False
                elif op == "$ne":
                    if val == opval:
                        return False
                elif op == "$lt":
                    if not (val is not None and val < opval):
                        return False
                elif op == "$gte":
                    if not (val is not None and val >= opval):
                        return False
                else:
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

    async def count_documents(self, query):
        return sum(1 for d in self.docs if _match(d, query))

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

    async def update_many(self, query, update):
        matched = 0
        for d in self.docs:
            if _match(d, query):
                matched += 1
                for k, v in update.get("$set", {}).items():
                    d[k] = v
        return SimpleNamespace(matched_count=matched, modified_count=matched)


class FakeDB:
    def __init__(self):
        self.invoices = FakeCollection()
        self.customers = FakeCollection()
        self.tenants = FakeCollection()
        self.collection_sequences = FakeCollection()
        self.collection_enrollments = FakeCollection()
        self.collection_touches = FakeCollection()


# ---------- fixtures ----------

current_user = {}
_db = None


@pytest.fixture
def fake_db():
    global _db
    _db = FakeDB()
    _db.tenants.docs.append({"id": "t1", "name": "Acme Plumbing"})
    _db.tenants.docs.append({"id": "t2", "name": "Rival Rooter"})
    return _db


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(ac_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(ac_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def _iso(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def seed_customer(db, cid="c1", tenant="t1", **over):
    doc = {
        "id": cid, "tenant_id": tenant, "name": "Jane Doe",
        "phone": "+15550001111", "email": "jane@example.com",
        "voice_consent": False, "sentiment": "neutral",
    }
    doc.update(over)
    db.customers.docs.append(doc)
    return doc


def seed_invoice(db, iid="inv1", tenant="t1", due_days_ago=10, **over):
    doc = {
        "id": iid, "tenant_id": tenant, "customer_id": "c1",
        "customer_name": "Jane Doe", "invoice_number": "INV-1001",
        "amount_due": 500.0, "total": 500.0,
        "due_date": _iso(due_days_ago), "status": "unpaid",
    }
    doc.update(over)
    db.invoices.docs.append(doc)
    return doc


def make_seq():
    return {
        "name": "Gentle Nudge",
        "description": "3-step empathetic sequence",
        "steps": [
            {"wait_days": 0, "channel": "sms", "tone": "friendly"},
            {"wait_days": 7, "channel": "sms", "tone": "firm"},
            {"wait_days": 14, "channel": "email", "tone": "plan-offer"},
        ],
        "active": True,
    }


# ---------- pick_tone unit tests ----------

def test_pick_tone_friendly_early():
    assert ac_mod.pick_tone(3, prior_touches=0) == "friendly"
    assert ac_mod.pick_tone(10, prior_touches=1) == "friendly"


def test_pick_tone_firmer_later():
    assert ac_mod.pick_tone(30, prior_touches=1) == "firm"


def test_pick_tone_plan_offer_for_hardest():
    assert ac_mod.pick_tone(90, prior_touches=5) == "plan-offer"
    assert ac_mod.pick_tone(10, prior_touches=4) == "plan-offer"


def test_pick_tone_never_abusive():
    tones = {
        ac_mod.pick_tone(d, prior_touches=p)
        for d in (0, 5, 30, 120, 365) for p in (0, 1, 3, 10)
    }
    assert tones <= {"friendly", "firm", "plan-offer", "none", "pause"}


def test_pick_tone_dnc_dispute_promise_overrides():
    assert ac_mod.pick_tone(120, prior_touches=9, dnc=True) == "none"
    assert ac_mod.pick_tone(120, prior_touches=9, dispute=True) == "friendly"
    assert ac_mod.pick_tone(120, prior_touches=9, promised_recently=True) == "pause"


def test_pick_tone_frustrated_customer_stays_gentle_longer():
    plain = ac_mod.pick_tone(30, prior_touches=0)
    soft = ac_mod.pick_tone(30, prior_touches=0, customer_sentiment="frustrated")
    assert plain == "firm"
    assert soft == "friendly"


# ---------- empathy guardrail ----------

def test_templates_contain_no_banned_phrases():
    blob = " ".join(ac_mod.TEMPLATES.values()).lower()
    for phrase in ac_mod.BANNED_PHRASES:
        assert phrase not in blob, f"banned phrase leaked: {phrase!r}"


def test_templates_are_option_giving():
    blob = " ".join(ac_mod.TEMPLATES.values()).lower()
    assert "payment plan" in blob
    assert "or " in blob  # always offers an option


# ---------- overdue endpoint ----------

def test_overdue_lists_with_ai_tone(client, fake_db):
    as_user("t1")
    seed_customer(fake_db)
    seed_invoice(fake_db, due_days_ago=5)
    seed_invoice(fake_db, iid="inv2", invoice_number="INV-1002", due_days_ago=40)
    seed_invoice(fake_db, iid="inv3", invoice_number="INV-1003", due_days_ago=5,
                 tenant="t2", customer_id="c2", customer_name="Bob")
    r = client.get("/api/ai-collections/tenants/overdue")
    assert r.status_code == 200
    items = {i["invoice_id"]: i for i in r.json()["items"]}
    assert set(items) == {"inv1", "inv2"}  # tenant isolation
    assert items["inv1"]["tone"] == "friendly"
    assert items["inv2"]["tone"] in ("firm", "plan-offer")
    assert items["inv1"]["template"]  # rendered copy present
    assert items["inv1"]["next_action"].startswith("send_")


def test_overdue_hides_future_invoices(client, fake_db):
    as_user("t1")
    seed_customer(fake_db)
    seed_invoice(fake_db, due_days_ago=-5)  # not yet due
    r = client.get("/api/ai-collections/tenants/overdue")
    assert r.json()["count"] == 0


# ---------- sequences ----------

def test_sequence_crud(client, fake_db):
    as_user("t1")
    r = client.post("/api/ai-collections/tenants/sequences", json=make_seq())
    assert r.status_code == 200
    seq_id = r.json()["id"]

    r = client.get("/api/ai-collections/tenants/sequences")
    assert len(r.json()["items"]) == 1

    r = client.put(f"/api/ai-collections/tenants/sequences/{seq_id}",
                   json={**make_seq(), "name": "Updated"})
    assert r.json()["ok"] is True
    assert fake_db.collection_sequences.docs[0]["name"] == "Updated"


def test_sequence_rejects_bad_channel(client, fake_db):
    as_user("t1")
    bad = make_seq()
    bad["steps"][0]["channel"] = "pigeon"
    r = client.post("/api/ai-collections/tenants/sequences", json=bad)
    assert r.status_code == 422


def test_enroll_and_dnc_hold(client, fake_db):
    as_user("t1")
    seed_customer(fake_db, cid="c-dnc", name="Quiet Quinn", dnc=True)
    seed_invoice(fake_db, iid="inv-dnc", customer_id="c-dnc")
    seed_customer(fake_db)
    seed_invoice(fake_db)
    seq_id = client.post("/api/ai-collections/tenants/sequences", json=make_seq()).json()["id"]

    # normal enrollment
    r = client.post(f"/api/ai-collections/tenants/sequences/{seq_id}/enroll",
                    json={"sequence_id": seq_id, "invoice": {"invoice_id": "inv1"}})
    assert r.status_code == 200
    assert r.json()["status"] == "active"
    assert r.json()["next_touch_at"]

    # DNC customer -> held, no touches scheduled
    r = client.post(f"/api/ai-collections/tenants/sequences/{seq_id}/enroll",
                    json={"sequence_id": seq_id, "invoice": {"invoice_id": "inv-dnc"}})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "held"
    assert body["held_reason"] == "do_not_contact"
    assert body["next_touch_at"] is None


def test_enroll_accepts_ad_hoc_invoice_ref(client, fake_db):
    as_user("t1")
    seed_customer(fake_db)
    seq_id = client.post("/api/ai-collections/tenants/sequences", json=make_seq()).json()["id"]
    r = client.post(
        f"/api/ai-collections/tenants/sequences/{seq_id}/enroll",
        json={"sequence_id": seq_id, "invoice": {
            "customer_id": "c1", "customer_name": "Jane Doe",
            "invoice_number": "INV-9001", "amount": 250.0, "due_date": _iso(20),
        }},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "active"


# ---------- touches + consent gate ----------

def test_touch_voice_blocked_without_consent(client, fake_db):
    as_user("t1")
    seed_customer(fake_db)  # voice_consent=False
    seed_invoice(fake_db)
    r = client.post("/api/ai-collections/tenants/touches",
                    json={"invoice_id": "inv1", "channel": "voice"})
    assert r.status_code == 409
    assert "consent" in r.json()["detail"].lower()


def test_touch_sms_ok_and_dnc_blocked(client, fake_db):
    as_user("t1")
    seed_customer(fake_db, cid="c-dnc2", name="Quiet Quinn", dnc=True)
    seed_invoice(fake_db, iid="inv-x", customer_id="c-dnc2")
    r = client.post("/api/ai-collections/tenants/touches",
                    json={"invoice_id": "inv-x", "channel": "sms"})
    assert r.status_code == 409
    assert "do-not-contact" in r.json()["detail"]

    seed_customer(fake_db)
    seed_invoice(fake_db)
    r = client.post("/api/ai-collections/tenants/touches",
                    json={"invoice_id": "inv1", "channel": "sms"})
    assert r.status_code == 200
    body = r.json()
    assert body["tone"] == "friendly"
    assert body["status"] == "preview"
    assert "Jane Doe" in body["template"]


def test_log_paid_closes_enrollment(client, fake_db):
    as_user("t1")
    seed_customer(fake_db)
    seed_invoice(fake_db)
    seq_id = client.post("/api/ai-collections/tenants/sequences", json=make_seq()).json()["id"]
    client.post(f"/api/ai-collections/tenants/sequences/{seq_id}/enroll",
                json={"sequence_id": seq_id, "invoice": {"invoice_id": "inv1"}})
    touch = client.post("/api/ai-collections/tenants/touches",
                        json={"invoice_id": "inv1", "channel": "sms"}).json()
    r = client.post(f"/api/ai-collections/tenants/touches/{touch['id']}/log",
                    json={"outcome": "paid", "amount_paid": 500.0})
    assert r.json()["ok"] is True
    enr = fake_db.collection_enrollments.docs[0]
    assert enr["status"] == "complete"
    assert fake_db.invoices.docs[0]["status"] == "paid"


def test_log_dnc_flags_customer_and_holds(client, fake_db):
    as_user("t1")
    seed_customer(fake_db)
    seed_invoice(fake_db)
    seq_id = client.post("/api/ai-collections/tenants/sequences", json=make_seq()).json()["id"]
    client.post(f"/api/ai-collections/tenants/sequences/{seq_id}/enroll",
                json={"sequence_id": seq_id, "invoice": {"invoice_id": "inv1"}})
    touch = client.post("/api/ai-collections/tenants/touches",
                        json={"invoice_id": "inv1", "channel": "sms"}).json()
    r = client.post(f"/api/ai-collections/tenants/touches/{touch['id']}/log",
                    json={"outcome": "dnc", "note": "customer asked to stop"})
    assert r.json()["ok"] is True
    assert fake_db.customers.docs[0]["dnc"] is True
    assert fake_db.collection_enrollments.docs[0]["status"] == "held"


# ---------- dashboard ----------

def test_dashboard_kpis(client, fake_db):
    as_user("t1")
    seed_customer(fake_db)
    seed_invoice(fake_db, iid="inv1", due_days_ago=10, amount_due=500.0)
    seed_invoice(fake_db, iid="inv2", due_days_ago=75, amount_due=200.0,
                 invoice_number="INV-1002")
    touch = client.post("/api/ai-collections/tenants/touches",
                        json={"invoice_id": "inv1", "channel": "sms"}).json()
    client.post(f"/api/ai-collections/tenants/touches/{touch['id']}/log",
                json={"outcome": "paid", "amount_paid": 500.0})
    r = client.get("/api/ai-collections/tenants/dashboard")
    body = r.json()
    assert body["overdue_invoices"] == 1  # inv1 was marked paid by the touch log
    assert body["outstanding"] == 200.0
    assert body["collected"] == 500.0
    assert body["at_risk"] == 200.0
    assert body["touches_total"] == 1
