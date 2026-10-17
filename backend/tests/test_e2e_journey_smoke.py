"""E2E customer-journey SMOKE test (headless, sandbox only).

Walks the real routers through the whole journey on one fake in-memory
Mongo, with auth dependencies overridden and ONLY the true external edges
stubbed (the LLM's structured output, the bookkeeping app's HTTP API):

    inquiry → AI receptionist → lead capture → appointment →
    technician assignment → estimate → invoice → payment webhook →
    bookkeeping sync

Legs that genuinely cannot run headless are NOT faked here; they are
listed in the module footer and in the completion report:

    * Live LLM receptionist turn — needs AI_API_KEY + provider network.
      The deterministic no-key fallback IS exercised (leg 2), and the
      router's structured-action execution is exercised with the LLM's
      output stubbed at the module seam (leg 3).
    * Live Stripe PaymentIntent creation (POST /public/c2p/invoices/{token}/pay)
      — needs a real Stripe account + network. The invoice_payments record
      it would have written is seeded directly; the webhook that completes
      the payment runs the REAL stripe.Webhook.construct_event signature
      verification with a local test secret.
    * Live Twilio voice/SMS — needs carrier credentials + network.

No real charges, no production credentials, no database server.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import types

import pytest

os.environ.setdefault("JWT_SECRET", "e2e-journey-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "e2e_journey_test")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("BOOKKEEPING_LINK_ENCRYPTION_KEY", Fernet.generate_key().decode())

import stripe  # noqa: E402  (real package: real signature verification, local secret)
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.bookkeeping_link as bk_mod  # noqa: E402
import routers.call_to_payment as c2p_mod  # noqa: E402
import routers.conversations as conv_mod  # noqa: E402
import routers.dispatch as dispatch_mod  # noqa: E402
import routers.usage as usage_mod  # noqa: E402
from security import require_tenant_user, require_tenant_owner_or_admin  # noqa: E402
from services import token_vault  # noqa: E402
from services.accounting import MindAtEaseProvider  # noqa: E402

WEBHOOK_SECRET = "whsec_e2e_journey_test_secret"
TENANT = "t-journey"
ACCT = "acct_journey"


# ================= fake mongo =================

class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        self.docs = sorted(self.docs, key=lambda d: str(d.get(key, "")),
                           reverse=(direction < 0))
        return self

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
                elif op == "$gte" and not (val is not None and val >= opval):
                    return False
                elif op == "$lte" and not (val is not None and val <= opval):
                    return False
                elif op not in ("$in", "$ne", "$gte", "$lte"):
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
        # Mongo enforces _id uniqueness (the app also creates a unique index
        # on webhook_events._id at startup) — emulate that contract.
        if "_id" in doc and any(d.get("_id") == doc["_id"] for d in self.docs):
            raise Exception(f"duplicate key error: _id={doc['_id']!r}")
        self.docs.append(dict(doc))
        return types.SimpleNamespace(inserted_id=doc.get("id") or doc.get("_id"))

    async def update_one(self, query, update, upsert=False):
        matched = 0
        for d in self.docs:
            if _match(d, query):
                matched += 1
                for k, v in update.get("$set", {}).items():
                    d[k] = v
                break
        if matched == 0 and upsert:
            doc = {k: v for k, v in query.items() if not isinstance(v, dict)}
            doc.update(update.get("$setOnInsert", {}))
            doc.update(update.get("$set", {}))
            self.docs.append(doc)
            matched = 1
        return types.SimpleNamespace(matched_count=matched, modified_count=matched)

    async def delete_one(self, query):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _match(d, query)]
        return types.SimpleNamespace(deleted_count=before - len(self.docs))

    def aggregate(self, pipeline):
        docs = [dict(d) for d in self.docs]
        group_spec = None
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _match(d, stage["$match"])]
            elif "$group" in stage:
                group_spec = stage["$group"]
        rows = []
        if group_spec:
            id_field = group_spec["_id"].lstrip("$")
            buckets = {}
            for d in docs:
                key = d.get(id_field)
                total = buckets.setdefault(key, 0)
                for out_key, agg in group_spec.items():
                    if out_key == "_id":
                        continue
                    sum_field = agg["$sum"].lstrip("$")
                    total += float(d.get(sum_field) or 0)
                buckets[key] = total
            rows = [{"_id": k, "total": v} for k, v in buckets.items()]
        return FakeCursor(rows)


class FakeDB:
    COLLECTIONS = [
        "tenants", "industries", "services", "knowledge", "upsells",
        "discount_policies", "ai_personas", "objections", "customers",
        "leads", "conversations", "conv_messages", "appointments",
        "schedule_staff", "estimates", "invoices", "invoice_payments",
        "webhook_events", "usage_events", "plans",
    ]

    def __init__(self):
        for name in self.COLLECTIONS:
            setattr(self, name, FakeCollection())


# ================= fake bookkeeping-app HTTP =================

class FakeBookkeepingHTTP:
    """Stands in for the tenant's bookkeeping app API (records every call)."""

    def __init__(self):
        self.calls = []

    async def __call__(self, method, path, json=None, idem=None):
        self.calls.append({"method": method, "path": path, "json": json, "idem": idem})
        if path == "/auth/me":
            return {"id": "bk-user-1", "name": "Shop Owner",
                    "email": "owner@example.com", "tenant": {"name": "Journey HVAC"}}
        if path == "/customers":
            return {"id": "bk-cust-1"}
        if path == "/invoices":
            return {"id": "bk-inv-1"}
        if path.endswith("/pay"):
            return {"entry_number": 42}
        raise AssertionError(f"unexpected bookkeeping path {path}")


# ================= fixtures =================

current_user = {}


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


@pytest.fixture
def journey(monkeypatch):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("EMERGENT_LLM_KEY", raising=False)
    monkeypatch.setenv("STRIPE_CONNECT_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)

    db = FakeDB()
    for mod in (conv_mod, usage_mod, dispatch_mod, c2p_mod, bk_mod):
        monkeypatch.setattr(mod, "get_db", lambda: db)

    bk_http = FakeBookkeepingHTTP()
    monkeypatch.setattr(MindAtEaseProvider, "_request", bk_http)

    # Seed tenant (with bookkeeping link enabled + encrypted token), industry,
    # service catalog entry, and a technician.
    settings = dict(bk_mod.DEFAULT_SETTINGS)
    settings.update({"enabled": True, "auto_sync_on_paid": True,
                     "api_token_enc": token_vault.encrypt_secret("bk-test-token"),
                     "api_token_set": True})
    db.tenants.docs.append({
        "id": TENANT, "name": "Journey HVAC", "slug": "journey-hvac",
        "industry_slug": "hvac", "stripe_connect_id": ACCT,
        "bookkeeping_link_settings": settings,
    })
    db.industries.docs.append({"slug": "hvac", "name": "HVAC"})
    db.services.docs.append({
        "id": "svc-1", "tenant_id": TENANT, "name": "AC Tune-Up",
        "price": 120.0, "duration_minutes": 60})
    db.schedule_staff.docs.append({
        "id": "tech-1", "tenant_id": TENANT, "name": "Tech One", "role": "technician"})

    app = FastAPI()
    app.include_router(conv_mod.router)
    app.include_router(c2p_mod.router)
    app.include_router(c2p_mod.public_router)
    app.include_router(dispatch_mod.router)
    app.include_router(bk_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user

    def fake_owner():
        if current_user.get("role") not in {"owner", "admin"}:
            raise HTTPException(403, "Owner/admin role required")
        return current_user

    app.dependency_overrides[require_tenant_owner_or_admin] = fake_owner
    return TestClient(app), db, bk_http


def _sign(payload: bytes, secret: str = WEBHOOK_SECRET) -> str:
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.{payload.decode()}".encode(),
                   hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


# ================= the journey =================

class TestCustomerJourneySmoke:
    def test_full_journey(self, journey, monkeypatch):
        client, db, bk_http = journey
        as_user(TENANT)

        # -- Leg 1: inquiry — caller starts a conversation ----------------
        r = client.post("/conversations/start", json={
            "channel": "call", "caller_name": "Jane Doe",
            "caller_phone": "+15550000199", "is_simulation": True})
        assert r.status_code == 200, r.text
        conv = r.json()["conversation"]
        conv_id = conv["id"]
        assert "Journey HVAC" in r.json()["greeting"]

        # -- Leg 2: lead capture — unknown caller became a lead -----------
        assert conv.get("lead_id"), "unknown caller must be captured as a lead"
        assert len(db.leads.docs) == 1
        lead_id = db.leads.docs[0]["id"]
        assert db.leads.docs[0]["phone"] == "+15550000199"

        # -- Leg 3a: AI receptionist — deterministic fallback (no LLM key) -
        r = client.post(f"/conversations/{conv_id}/caller-turn",
                        json={"text": "Hi, I need to book an appointment for a tune-up"})
        assert r.status_code == 200, r.text
        assert r.json()["reply"], "receptionist must answer, never dead-end"

        # -- Leg 3b: receptionist structured action books the appointment -
        # (LLM output stubbed at the module seam; the router's real
        # action-execution path runs.)
        async def fake_llm_reply(*args, **kwargs):
            return {"reply": "You're booked for Tuesday at 9am.",
                    "action": {"type": "book_appointment", "payload": {
                        "customer_name": "Jane Doe",
                        "customer_phone": "+15550000199",
                        "service_name": "AC Tune-Up",
                        "start_at": "2026-10-20T09:00:00+00:00",
                        "end_at": "2026-10-20T10:00:00+00:00"}},
                    "end": False}
        monkeypatch.setattr(conv_mod, "receptionist_reply", fake_llm_reply)
        r = client.post(f"/conversations/{conv_id}/caller-turn",
                        json={"text": "Tuesday 9am works"})
        assert r.status_code == 200, r.text
        appt_id = r.json()["action_result"]["appointment_id"]
        assert any(a["id"] == appt_id for a in db.appointments.docs)
        assert any(c["phone"] == "+15550000199" for c in db.customers.docs), \
            "booking must ensure a customer record"

        # -- Leg 4: estimate from the service catalog ----------------------
        r = client.post("/c2p/quotes/draft", json={
            "lead_id": lead_id, "customer_name": "Jane Doe",
            "customer_email": "jane@example.com",
            "customer_phone": "+15550000199",
            "title": "AC Tune-Up", "service_ids": ["svc-1"]})
        assert r.status_code == 200, r.text
        quote = r.json()
        assert quote["total"] == 120.0, "price must come from the catalog, not the caller"

        r = client.post(f"/c2p/quotes/{quote['id']}/decision",
                        json={"decision": "approve_and_send"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "sent"

        # -- Leg 5: customer accepts → appointment + invoice (idempotent) --
        r = client.post(f"/public/c2p/quotes/{quote['public_token']}/accept",
                        json={"deposit_cents": 0,
                              "scheduled_at": "2026-10-21T09:00:00+00:00"})
        assert r.status_code == 200, r.text
        invoice = r.json()["invoice"]
        job_id = r.json()["appointment_id"]
        assert invoice["total_cents"] == 12000
        assert invoice["status"] == "sent"

        r = client.post(f"/public/c2p/quotes/{quote['public_token']}/accept",
                        json={"deposit_cents": 0})
        assert r.json().get("idempotent") is True
        assert r.json()["invoice"]["id"] == invoice["id"]
        assert len(db.invoices.docs) == 1, "re-accept must not duplicate the invoice"

        # -- Leg 6: technician assignment ----------------------------------
        r = client.post("/tenants/dispatch/assign", json={
            "job_id": job_id, "tech_id": "tech-1",
            "start_at": "2026-10-21T09:00:00+00:00"})
        assert r.status_code == 200, r.text
        job = next(a for a in db.appointments.docs if a["id"] == job_id)
        assert job["staff_id"] == "tech-1" and job["staff"] == "Tech One"

        # -- Leg 7: job completion ------------------------------------------
        r = client.post("/c2p/jobs/complete", json={"appointment_id": job_id})
        assert r.status_code == 200, r.text
        assert r.json()["invoice_id"] == invoice["id"]
        job = next(a for a in db.appointments.docs if a["id"] == job_id)
        assert job["status"] == "completed"

        # -- Leg 8: payment — Stripe webhook (real signature verification) --
        # The invoice_payments row is what the live pay endpoint writes when
        # it creates the PaymentIntent (that Stripe API call is not headless).
        db.invoice_payments.docs.append({
            "id": "pay-1", "tenant_id": TENANT, "invoice_id": invoice["id"],
            "stripe_payment_intent_id": "pi_journey", "amount_cents": 12000,
            "connected_account": ACCT, "status": "requires_payment"})
        event = {"id": "evt_journey_1", "type": "payment_intent.succeeded",
                 "account": ACCT,
                 "data": {"object": {"id": "pi_journey", "amount": 12000,
                                     "amount_received": 12000, "currency": "usd",
                                     "metadata": {"invoice_id": invoice["id"],
                                                  "tenant_id": TENANT}}}}
        payload = json.dumps(event).encode()
        r = client.post("/public/c2p/stripe/connect-webhook", content=payload,
                        headers={"Stripe-Signature": _sign(payload),
                                 "Content-Type": "application/json"})
        assert r.status_code == 200, r.text
        assert r.json().get("ok") is True
        inv = next(i for i in db.invoices.docs if i["id"] == invoice["id"])
        assert inv["status"] == "paid" and inv["amount_paid_cents"] == 12000

        # Duplicate delivery must be a no-op (webhook idempotency).
        r = client.post("/public/c2p/stripe/connect-webhook", content=payload,
                        headers={"Stripe-Signature": _sign(payload),
                                 "Content-Type": "application/json"})
        assert r.json().get("duplicate") is True
        inv = next(i for i in db.invoices.docs if i["id"] == invoice["id"])
        assert inv["amount_paid_cents"] == 12000, "duplicate webhook must not double-apply"

        # -- Leg 9: bookkeeping sync fired by the payment -------------------
        sync = inv.get("bookkeeping_sync") or {}
        assert sync.get("customer_provider_id") == "bk-cust-1"
        assert sync.get("invoice_provider_id") == "bk-inv-1"
        assert sync.get("payment_provider_id") == "42"
        paths = [c["path"] for c in bk_http.calls]
        assert paths == ["/customers", "/invoices", "/invoices/bk-inv-1/pay"], paths
        assert all(c["idem"] for c in bk_http.calls), "every push must carry an idempotency key"
        tenant = next(t for t in db.tenants.docs if t["id"] == TENANT)
        counts = tenant["bookkeeping_link_settings"]["counts"]
        assert counts == {"customers": 1, "invoices": 1, "payments": 1}

        # Re-sync is idempotent: no further provider calls.
        r = client.post(f"/tenants/bookkeeping-link/invoices/{invoice['id']}/sync")
        assert r.status_code == 200, r.text
        assert len(bk_http.calls) == 3, "already-pushed steps must be skipped"

        # -- Leg 10: tenant isolation ---------------------------------------
        as_user("t-other")
        r = client.get("/conversations")
        assert r.json() == []
        r = client.get("/c2p/invoices")
        assert r.json() == []
