"""Unit tests for the marketing automation engine (routers/marketing_automation.py).

Dual-purpose engine (#31): shop context (/tenants/marketing, tenant-scoped)
and platform context (/admin/marketing, platform-admin only).

No database, network, or browser needed: a small FastAPI app mounts the
shop router, the public unsubscribe router and the admin router; a fake
in-memory Mongo stands in for Motor; auth dependencies are overridden;
email/SMS providers are monkeypatched with recording fakes.
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("JWT_SECRET", "marketing-test-secret")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "marketing_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.marketing_automation as mkt_mod  # noqa: E402
import services.marketing_automation as svc_mod  # noqa: E402
from security import require_tenant_user, require_tenant_owner_or_admin, require_platform_admin  # noqa: E402


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
        for i, d in enumerate(self.docs):
            if _match(d, query):
                del self.docs[i]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


class FakeDB:
    def __init__(self):
        self.integrations = FakeCollection()  # for services.twilio demo fallback
        self.customers = FakeCollection()
        self.platform_prospects = FakeCollection()
        self.marketing_segments = FakeCollection()
        self.marketing_campaigns = FakeCollection()
        self.marketing_sequences = FakeCollection()
        self.marketing_enrollments = FakeCollection()
        self.marketing_sends = FakeCollection()
        self.marketing_suppressions = FakeCollection()


# ---------- recording providers (zero network) ----------

class FakeEmailProvider:
    def __init__(self):
        self.sent = []

    async def send(self, *, to, subject, body, reply_to=None):
        self.sent.append({"to": to, "subject": subject, "body": body})
        return {"provider": "fake-email", "message_id": f"fe-{len(self.sent)}",
                "status": "sent", "demo": True}


class FakeSmsProvider:
    def __init__(self):
        self.sent = []

    async def send(self, *, to, body):
        self.sent.append({"to": to, "body": body})
        return {"provider": "fake-sms", "message_id": f"fs-{len(self.sent)}",
                "status": "sent", "demo": True}


# ---------- fixtures ----------

current_user = {}


def as_user(tenant_id, role="owner", user_id=None):
    current_user.clear()
    current_user.update({"id": user_id or f"u-{tenant_id}-{role}",
                         "tenant_id": tenant_id, "role": role})


def as_platform_admin():
    current_user.clear()
    current_user.update({"id": "u-platform-admin", "tenant_id": None,
                         "role": "platform_admin"})


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def providers(monkeypatch):
    email = FakeEmailProvider()
    sms = FakeSmsProvider()
    monkeypatch.setattr(mkt_mod, "get_email_provider", lambda tenant_id=None: email)
    monkeypatch.setattr(mkt_mod, "get_sms_provider", lambda tenant_id=None: sms)
    return email, sms


@pytest.fixture
def client(monkeypatch, fake_db, providers):
    monkeypatch.setattr(mkt_mod, "get_db", lambda: fake_db)

    def fake_tenant_user():
        if not current_user.get("tenant_id"):
            raise HTTPException(403, "No tenant assigned")
        return current_user

    def fake_owner_or_admin():
        u = fake_tenant_user()
        if u.get("role") not in ("owner", "admin"):
            raise HTTPException(403, "Owner/admin role required")
        return u

    def fake_platform_admin():
        if current_user.get("role") != "platform_admin":
            raise HTTPException(403, "Platform admin only")
        return current_user

    app = FastAPI()
    app.include_router(mkt_mod.router)
    app.include_router(mkt_mod.public_router)
    app.include_router(mkt_mod.admin_router)
    app.dependency_overrides[require_tenant_user] = fake_tenant_user
    app.dependency_overrides[require_tenant_owner_or_admin] = fake_owner_or_admin
    app.dependency_overrides[require_platform_admin] = fake_platform_admin
    return TestClient(app)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def seed_customers(db, tenant_id, customers):
    for i, c in enumerate(customers):
        await db.customers.insert_one({"id": f"c-{tenant_id}-{i}", "tenant_id": tenant_id, **c})


# ---------- segment rules ----------

def test_segment_rule_matching_and_preview(client, fake_db):
    as_user("t1")
    run(seed_customers(fake_db, "t1", [
        {"name": "Amy", "email": "amy@x.com", "phone": "555-0001",
         "equipment_age_years": 12, "maintenance_member": False},
        {"name": "Bo", "email": "bo@x.com", "phone": "555-0002",
         "equipment_age_years": 15, "maintenance_member": True},
        {"name": "Cy", "email": "cy@x.com", "phone": "555-0003",
         "equipment_age_years": 3, "maintenance_member": False},
    ]))
    # "customers with AC units 10+ years old"
    r = client.post("/tenants/marketing/segments", json={
        "name": "Old AC units",
        "rules": [{"field": "equipment_age_years", "operator": "gte", "value": 10}],
    })
    assert r.status_code == 200, r.text
    seg_id = r.json()["id"]

    r = client.post(f"/tenants/marketing/segments/{seg_id}/preview")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    assert {s["name"] for s in body["sample"]} == {"Amy", "Bo"}

    # match=any widens the net
    r = client.post("/tenants/marketing/segments", json={
        "name": "Old or member", "match": "any",
        "rules": [
            {"field": "equipment_age_years", "operator": "gte", "value": 10},
            {"field": "maintenance_member", "operator": "equals", "value": True},
        ],
    })
    seg2 = r.json()["id"]
    r = client.post(f"/tenants/marketing/segments/{seg2}/preview")
    assert r.json()["count"] == 2  # Amy (old), Bo (old+member)


def test_segment_invalid_operator_rejected(client):
    as_user("t1")
    r = client.post("/tenants/marketing/segments", json={
        "name": "Bad", "rules": [{"field": "name", "operator": "fuzzy_match", "value": "x"}]})
    assert r.status_code == 422


def test_segment_no_visit_rule(client, fake_db):
    as_user("t1")
    run(seed_customers(fake_db, "t1", [
        {"name": "Old Visit", "email": "old@x.com", "last_visit_at": "2023-01-01T00:00:00+00:00"},
        {"name": "Recent", "email": "new@x.com", "last_visit_at": "2026-10-01T00:00:00+00:00"},
    ]))
    # "no visit in 18 months" -> last_visit_at older than 2025-04-15
    r = client.post("/tenants/marketing/segments", json={
        "name": "Win-back",
        "rules": [{"field": "last_visit_at", "operator": "lt", "value": "2025-04-15T00:00:00+00:00"}],
    })
    seg_id = r.json()["id"]
    r = client.post(f"/tenants/marketing/segments/{seg_id}/preview")
    assert r.json()["count"] == 1
    assert r.json()["sample"][0]["name"] == "Old Visit"


# ---------- tenant isolation ----------

def test_tenant_isolation_campaigns(client, fake_db):
    as_user("tA")
    r = client.post("/tenants/marketing/campaigns", json={
        "name": "A blast", "channel": "email", "subject": "Hi", "body": "Hello {{name}}"})
    assert r.status_code == 200, r.text
    camp_id = r.json()["id"]

    as_user("tB")
    r = client.get("/tenants/marketing/campaigns")
    assert r.status_code == 200
    assert r.json() == []  # B cannot see A's campaigns

    r = client.get(f"/tenants/marketing/campaigns/{camp_id}")
    assert r.status_code == 404

    r = client.post(f"/tenants/marketing/campaigns/{camp_id}/send")
    assert r.status_code == 404  # B cannot send A's campaign

    r = client.get("/tenants/marketing/segments")
    assert r.json() == []


def test_non_owner_cannot_write(client):
    as_user("t1", role="technician")
    r = client.post("/tenants/marketing/campaigns", json={
        "name": "x", "channel": "email", "subject": "s", "body": "b"})
    assert r.status_code == 403
    # reads still fine
    r = client.get("/tenants/marketing/campaigns")
    assert r.status_code == 200


def test_user_without_tenant_rejected(client):
    current_user.clear()
    current_user.update({"id": "u-orphan", "tenant_id": None, "role": "owner"})
    r = client.get("/tenants/marketing/campaigns")
    assert r.status_code == 403


# ---------- campaigns: send / test-send / stats ----------

def _make_blast(client, fake_db, tenant="t1", channel="email", segment_id=None,
                seed=True):
    """Returns (campaign_id, segment_id)."""
    if seed:
        run(seed_customers(fake_db, tenant, [
            {"name": "Amy", "first_name": "Amy", "email": "amy@x.com", "phone": "555-0001"},
            {"name": "Bo", "first_name": "Bo", "email": "bo@x.com", "phone": "555-0002"},
        ]))
    if segment_id is None:
        r = client.post("/tenants/marketing/segments", json={"name": "All", "rules": []})
        segment_id = r.json()["id"]
    r = client.post("/tenants/marketing/campaigns", json={
        "name": "Tune-up blast", "channel": channel, "segment_id": segment_id,
        "subject": "Fall tune-up", "body": "Hi {{first_name}}, time for a tune-up! {{unsubscribe_url}}"})
    assert r.status_code == 200, r.text
    return r.json()["id"], segment_id


def test_campaign_send_email_and_stats(client, fake_db, providers):
    as_user("t1")
    camp_id, _seg = _make_blast(client, fake_db)
    email, sms = providers

    r = client.post(f"/tenants/marketing/campaigns/{camp_id}/send")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "sent"
    assert body["sent"] == 2
    assert len(email.sent) == 2
    assert len(sms.sent) == 0
    # template tokens rendered, unsubscribe link stamped
    assert email.sent[0]["body"].startswith("Hi Amy,")
    assert "/unsubscribe/" in email.sent[0]["body"]

    r = client.get(f"/tenants/marketing/campaigns/{camp_id}/stats")
    stats = r.json()
    assert stats["total_sends"] == 2
    assert stats["by_status"] == {"sent": 2}
    assert stats["by_channel"] == {"email": 2}

    # duplicate send blocked
    r = client.post(f"/tenants/marketing/campaigns/{camp_id}/send")
    assert r.status_code == 400


def test_campaign_send_sms(client, fake_db, providers):
    as_user("t1")
    camp_id, _seg = _make_blast(client, fake_db, channel="sms")
    email, sms = providers
    r = client.post(f"/tenants/marketing/campaigns/{camp_id}/send")
    assert r.status_code == 200, r.text
    assert r.json()["sent"] == 2
    assert len(sms.sent) == 2 and len(email.sent) == 0


def test_campaign_test_send_does_not_flip_status(client, fake_db, providers):
    as_user("t1")
    camp_id, _seg = _make_blast(client, fake_db)
    email, _ = providers
    r = client.post(f"/tenants/marketing/campaigns/{camp_id}/test-send",
                    json={"to": "boss@shop.com"})
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "sent"
    assert email.sent[0]["to"] == "boss@shop.com"
    # still a draft — real blast untouched
    r = client.get(f"/tenants/marketing/campaigns/{camp_id}")
    assert r.json()["status"] == "draft"
    r = client.get(f"/tenants/marketing/campaigns/{camp_id}/stats")
    assert r.json()["total_sends"] == 0  # test sends excluded


def test_campaign_contact_without_address_skipped(client, fake_db, providers):
    as_user("t1")
    run(seed_customers(fake_db, "t1", [
        {"name": "NoEmail", "phone": ""},  # email campaign, no email
    ]))
    r = client.post("/tenants/marketing/segments", json={"name": "All", "rules": []})
    seg_id = r.json()["id"]
    r = client.post("/tenants/marketing/campaigns", json={
        "name": "x", "channel": "email", "segment_id": seg_id,
        "subject": "s", "body": "b"})
    camp_id = r.json()["id"]
    r = client.post(f"/tenants/marketing/campaigns/{camp_id}/send")
    assert r.json()["skipped_no_address"] == 1
    assert r.json()["sent"] == 0


# ---------- sequences ----------

def _make_sequence(client, steps):
    r = client.post("/tenants/marketing/sequences", json={
        "name": "Nurture", "description": "day 0/3/7 drip", "steps": steps})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_sequence_schedule_preview_ordering(client):
    as_user("t1")
    seq_id = _make_sequence(client, [
        {"channel": "email", "delay_days": 7, "subject": "s3", "body": "b3"},
        {"channel": "email", "delay_days": 0, "subject": "s1", "body": "b1"},
        {"channel": "sms", "delay_days": 3, "body": "b2"},
    ])
    r = client.get(f"/tenants/marketing/sequences/{seq_id}/schedule-preview")
    assert r.status_code == 200, r.text
    steps = r.json()["steps"]
    # steps keep creation order (day 7, day 0, day 3 as submitted)
    assert [s["order"] for s in steps] == [0, 1, 2]
    assert [s["delay_days"] for s in steps] == [7, 0, 3]
    # absolute send dates respect the delays
    assert steps[0]["send_at"] > steps[1]["send_at"]
    assert steps[2]["send_at"] > steps[1]["send_at"]


def test_sequence_enroll_and_drip_process_due(client, fake_db, providers):
    as_user("t1")
    email, _ = providers
    run(seed_customers(fake_db, "t1", [
        {"name": "Amy", "first_name": "Amy", "email": "amy@x.com", "phone": "555-0001"},
    ]))
    seq_id = _make_sequence(client, [
        {"channel": "email", "delay_days": 0, "subject": "Welcome", "body": "Hi {{first_name}}"},
        {"channel": "email", "delay_days": 0, "subject": "Follow-up", "body": "Still there?"},
    ])
    r = client.post(f"/tenants/marketing/sequences/{seq_id}/enroll",
                    json={"contact_ids": ["c-t1-0"]})
    assert r.json()["enrolled"] == 1

    # first process-due: step 0 goes out (delay 0 => due immediately)
    r = client.post("/tenants/marketing/sequences/process-due")
    assert r.status_code == 200, r.text
    assert r.json()["steps_sent"] == 1
    assert email.sent[0]["subject"] == "Welcome"

    # second process-due: step 1 (delay 0 => due now) then enrollment completes
    r = client.post("/tenants/marketing/sequences/process-due")
    assert r.json()["steps_sent"] == 1
    assert email.sent[1]["subject"] == "Follow-up"

    r = client.post("/tenants/marketing/sequences/process-due")
    assert r.json()["steps_sent"] == 0  # completed, nothing due


def test_sequence_enroll_dedupe(client, fake_db):
    as_user("t1")
    run(seed_customers(fake_db, "t1", [{"name": "Amy", "email": "amy@x.com"}]))
    seq_id = _make_sequence(client, [
        {"channel": "email", "delay_days": 30, "subject": "s", "body": "b"}])
    r = client.post(f"/tenants/marketing/sequences/{seq_id}/enroll",
                    json={"contact_ids": ["c-t1-0"]})
    assert r.json()["enrolled"] == 1
    r = client.post(f"/tenants/marketing/sequences/{seq_id}/enroll",
                    json={"contact_ids": ["c-t1-0"]})
    assert r.json()["enrolled"] == 0  # already active — no duplicate


# ---------- unsubscribe / suppression ----------

def test_unsubscribe_suppresses_future_sends(client, fake_db, providers):
    as_user("t1")
    camp_id, seg_id = _make_blast(client, fake_db)
    email, _ = providers
    r = client.post(f"/tenants/marketing/campaigns/{camp_id}/send")
    assert r.json()["sent"] == 2

    # Amy unsubscribes via the public link (no auth needed)
    token = svc_mod.make_unsubscribe_token(campaign_id=camp_id, contact="amy@x.com")
    current_user.clear()  # anonymous click from an inbox
    r = client.get(f"/tenants/marketing/unsubscribe/{token}")
    assert r.status_code == 200
    assert "unsubscribed" in r.text.lower()

    # next blast to the same segment skips Amy (no re-seeding: same 2 contacts)
    as_user("t1")
    camp2, _ = _make_blast(client, fake_db, segment_id=seg_id, seed=False)
    r = client.post(f"/tenants/marketing/campaigns/{camp2}/send")
    body = r.json()
    assert body["sent"] == 1
    assert body["skipped_suppressed"] == 1
    assert email.sent[-1]["to"] == "bo@x.com"


def test_unsubscribe_invalid_token_rejected(client):
    current_user.clear()
    r = client.get("/tenants/marketing/unsubscribe/not-a-real-token")
    assert r.status_code == 400
    r = client.get("/tenants/marketing/unsubscribe/abc.def")  # bad signature
    assert r.status_code == 400


def test_unsubscribe_token_tamper_rejected():
    token = svc_mod.make_unsubscribe_token(campaign_id="c1", contact="a@x.com")
    body, sig = token.rsplit(".", 1)
    assert svc_mod.verify_unsubscribe_token(f"{body}.{'0' * len(sig)}") is None
    assert svc_mod.verify_unsubscribe_token(token)["contact"] == "a@x.com"


# ---------- platform context ----------

def test_platform_endpoints_reject_non_admins(client):
    as_user("t1", role="owner")  # shop owner, NOT platform admin
    r = client.post("/admin/marketing/campaigns", json={
        "name": "x", "channel": "email", "subject": "s", "body": "b"})
    assert r.status_code == 403
    r = client.get("/admin/marketing/campaigns")
    assert r.status_code == 403


def test_platform_campaign_flow_isolated_from_shops(client, fake_db, providers):
    email, _ = providers
    # platform admin builds a trial-drip blast to prospective shops
    as_platform_admin()
    run(fake_db.platform_prospects.insert_one(
        {"id": "p-1", "business_name": "Acme HVAC", "email": "owner@acme.com",
         "phone": "555-9000", "trial_signup": True}))
    r = client.post("/admin/marketing/segments", json={
        "name": "Trial signups",
        "rules": [{"field": "trial_signup", "operator": "equals", "value": True}]})
    assert r.status_code == 200, r.text
    seg_id = r.json()["id"]
    r = client.post("/admin/marketing/campaigns", json={
        "name": "Trial nurture", "channel": "email", "segment_id": seg_id,
        "subject": "Welcome to the trial", "body": "Hi!"})
    camp_id = r.json()["id"]
    assert r.json()["context"] == "platform"
    r = client.post(f"/admin/marketing/campaigns/{camp_id}/send")
    assert r.json()["sent"] == 1
    assert email.sent[0]["to"] == "owner@acme.com"

    # shop tenants cannot see platform campaigns at all
    as_user("t1")
    r = client.get("/tenants/marketing/campaigns")
    assert r.json() == []
    r = client.get(f"/tenants/marketing/campaigns/{camp_id}")
    assert r.status_code == 404


def test_platform_suppression_list_visible_to_admin(client, fake_db):
    as_platform_admin()
    run(fake_db.platform_prospects.insert_one(
        {"id": "p-9", "business_name": "Gone Shop", "email": "gone@shop.com"}))
    r = client.post("/admin/marketing/segments", json={"name": "All", "rules": []})
    seg_id = r.json()["id"]
    r = client.post("/admin/marketing/campaigns", json={
        "name": "Blast", "channel": "email", "segment_id": seg_id,
        "subject": "s", "body": "b"})
    camp_id = r.json()["id"]
    client.post(f"/admin/marketing/campaigns/{camp_id}/send")

    token = svc_mod.make_unsubscribe_token(campaign_id=camp_id, contact="gone@shop.com")
    current_user.clear()
    assert client.get(f"/tenants/marketing/unsubscribe/{token}").status_code == 200

    as_platform_admin()
    r = client.get("/admin/marketing/suppressions")
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert r.json()["suppressions"][0]["contact"] == "gone@shop.com"


# ---------- provider abstraction: zero live network ----------

def test_default_providers_make_zero_network_calls(monkeypatch):
    """The out-of-the-box providers must never touch the network."""
    import socket
    monkeypatch.delenv("MARKETING_EMAIL_PROVIDER", raising=False)
    monkeypatch.delenv("MARKETING_SMS_PROVIDER", raising=False)

    def _boom(*a, **k):
        raise AssertionError("network call attempted by demo provider")

    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)

    email = svc_mod.get_email_provider()
    sms = svc_mod.get_sms_provider()
    assert isinstance(email, svc_mod.LogEmailProvider)
    assert isinstance(sms, svc_mod.LogSmsProvider)
    out = run(email.send(to="a@x.com", subject="s", body="b"))
    assert out["demo"] is True
    out = run(sms.send(to="555-1", body="b"))
    assert out["demo"] is True


def test_twilio_sms_provider_degrades_without_creds(monkeypatch, fake_db):
    """MARKETING_SMS_PROVIDER=twilio with no creds -> demo stub, no network."""
    import socket
    import services.twilio as twilio_mod
    monkeypatch.setattr(twilio_mod, "get_db", lambda: fake_db)  # never touch real Mongo
    monkeypatch.setenv("MARKETING_SMS_PROVIDER", "twilio")
    for var in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER",
                "TWILIO_MESSAGING_SID"):
        monkeypatch.delenv(var, raising=False)

    def _boom(*a, **k):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    provider = svc_mod.get_sms_provider(tenant_id="t1")
    assert isinstance(provider, svc_mod.TwilioSmsProvider)
    out = run(provider.send(to="555-1", body="hello"))
    assert out["demo"] is True
    assert out["provider"] == "twilio"
