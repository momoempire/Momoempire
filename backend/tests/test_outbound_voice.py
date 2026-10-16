"""Unit tests for outbound voice AI (routers/outbound_voice.py + services/outbound_voice.py).

Self-contained: a small FastAPI app mounts the voice router; a fake
in-memory Mongo stands in for Motor; auth dependencies are overridden.
The MockVoiceProvider is zero-network (in-memory records, deterministic
outcomes derived from phone hash).

Covers: template listing + rendering with variables, dry-run producing
zero side effects, real launch enqueueing in priority order, the FCC
consent gate blocking non-consented numbers, the missed-call trigger
creating a 60s-due priority job, tenant isolation, zero network.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "voice-test-secret")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "voice_test")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.outbound_voice as voice_mod  # noqa: E402
import services.outbound_voice as svc_mod  # noqa: E402
from security import require_tenant_user, require_tenant_owner_or_admin  # noqa: E402


# ---------- fake mongo ----------

class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    async def to_list(self, n):
        return [dict(d) for d in self.docs[:n]]


def _match(doc, query):
    for k, cond in query.items():
        if k == "_id":
            continue
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

    def clear(self):
        self.docs.clear()


class FakeDB:
    def __init__(self):
        self.customers = FakeCollection()
        self.voice_campaigns = FakeCollection()
        self.voice_calls = FakeCollection()
        self.voice_jobs = FakeCollection()


# ---------- fixtures ----------

current_user = {}


def as_user(tenant_id, role="owner", user_id=None):
    current_user.clear()
    current_user.update({"id": user_id or f"u-{tenant_id}-{role}",
                         "tenant_id": tenant_id, "role": role})


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(voice_mod, "get_db", lambda: fake_db)

    def fake_tenant_user():
        if not current_user.get("tenant_id"):
            raise HTTPException(403, "No tenant assigned")
        return current_user

    def fake_owner_or_admin():
        u = fake_tenant_user()
        if u.get("role") not in ("owner", "admin"):
            raise HTTPException(403, "Owner/admin role required")
        return u

    app = FastAPI()
    app.include_router(voice_mod.router)
    app.dependency_overrides[require_tenant_user] = fake_tenant_user
    app.dependency_overrides[require_tenant_owner_or_admin] = fake_owner_or_admin
    return TestClient(app)


async def seed_customer(db, tenant_id, cid, phone, voice_consent, name="Sam"):
    await db.customers.insert_one({"id": cid, "tenant_id": tenant_id,
                                   "phone": phone, "name": name,
                                   "voice_consent": voice_consent})


def campaign_payload(name="Tuneups", template_id="tuneup_campaign", audience=None):
    return {
        "name": name,
        "template_id": template_id,
        "audience": audience or [
            {"customer_id": "c1", "phone": "+15550001111",
             "variables": {"customer_name": "Sam", "shop_name": "Apex HVAC",
                           "season": "fall", "offer": "20% off",
                           "booking_link": "https://book.example"}},
            {"customer_id": "c2", "phone": "+15550002222",
             "variables": {"customer_name": "Jo", "shop_name": "Apex HVAC",
                           "season": "fall", "offer": "20% off",
                           "booking_link": "https://book.example"}},
        ],
    }


# ---------- template rendering ----------

def test_render_script_fills_variables_and_lists_templates(client):
    as_user("t1")
    r = client.get("/tenants/voice/templates")
    assert r.status_code == 200, r.text
    ids = [t["id"] for t in r.json()]
    assert ids == ["missed_call_callback", "overdue_invoice",
                   "reschedule_nudge", "tuneup_campaign"]
    out = svc_mod.render_script("Hi {{customer_name}}, {{shop_name}}!",
                                {"customer_name": "Sam", "shop_name": "Apex"})
    assert out == "Hi Sam, Apex!"
    with pytest.raises(ValueError, match="missing variable"):
        svc_mod.render_script("Hi {{customer_name}}!", {})
    # template_variables rejects unknown template ids
    with pytest.raises(ValueError):
        svc_mod.template_variables("nope")


def test_mock_provider_is_zero_network_and_deterministic():
    p = svc_mod.MockVoiceProvider()
    import asyncio

    async def two():
        first = await p.place_call(tenant_id="t", to="+15550001111",
                                   script="hi", call_id="call-a")
        second = await p.place_call(tenant_id="t", to="+15550001111",
                                    script="hi", call_id="call-a")
        return first, second

    first, second = asyncio.new_event_loop().run_until_complete(two())
    assert first == second  # deterministic outcomes
    assert len(p.recorded) == 2
    assert first["demo"] is True
    # outcome spread is stable across a handful of numbers
    outcomes = set()
    for n in range(10):
        o, dur = p._outcome_for(f"+1555000{1000 + n}")
        outcomes.add(o)
        assert 45 <= dur <= 240
    assert len(outcomes) > 1


# ---------- campaign lifecycle ----------

def test_create_and_list_campaign(client):
    as_user("t1")
    r = client.post("/tenants/voice/campaigns", json=campaign_payload())
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    assert r.json()["status"] == "draft"
    r = client.get("/tenants/voice/campaigns")
    assert r.status_code == 200
    assert [c["id"] for c in r.json()] == [cid]


def test_unknown_template_rejected(client):
    as_user("t1")
    r = client.post("/tenants/voice/campaigns",
                    json=campaign_payload(template_id="bogus"))
    assert r.status_code == 422, r.text


def test_dry_run_makes_zero_calls(client, fake_db):
    as_user("t1")
    run = __import__("asyncio").new_event_loop().run_until_complete
    run(seed_customer(fake_db, "t1", "c1", "+15550001111", True))
    run(seed_customer(fake_db, "t1", "c2", "+15550002222", True))
    run(seed_customer(fake_db, "t1", "c2", "+15550002222", True))
    r = client.post("/tenants/voice/campaigns", json=campaign_payload())
    cid = r.json()["id"]

    r = client.post(f"/tenants/voice/campaigns/{cid}/launch", json={"dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["order"] == ["+15550001111", "+15550002222"]
    assert "Hi Sam, Apex HVAC" not in body["call_list"][0]["script"]
    assert body["call_list"][0]["script"].startswith("Hi Sam, this is Apex HVAC")
    assert body["estimated_minutes"] == 6.0
    # zero side effects: nothing enqueued, campaign still draft
    assert fake_db.voice_calls.docs == []
    assert fake_db.voice_jobs.docs == []
    assert fake_db.voice_campaigns.docs[0]["status"] == "draft"


def test_launch_enqueues_in_priority_order(client, fake_db):
    as_user("t1")
    run = __import__("asyncio").new_event_loop().run_until_complete
    for cid, phone in (("c1", "+15550001111"), ("c2", "+15550002222"),
                       ("c3", "+15550003333")):
        run(seed_customer(fake_db, "t1", cid, phone, True))
    payload = campaign_payload(audience=[
        {"customer_id": "c1", "phone": "+15550001111", "priority": "normal",
         "variables": {"customer_name": "Sam", "shop_name": "Apex HVAC",
                       "season": "fall", "offer": "20% off",
                       "booking_link": "https://book.example"}},
        {"customer_id": "c2", "phone": "+15550002222", "priority": "high",
         "variables": {"customer_name": "Jo", "shop_name": "Apex HVAC",
                       "season": "fall", "offer": "20% off",
                       "booking_link": "https://book.example"}},
        {"customer_id": "c3", "phone": "+15550003333", "priority": "normal",
         "variables": {"customer_name": "Al", "shop_name": "Apex HVAC",
                       "season": "fall", "offer": "20% off",
                       "booking_link": "https://book.example"}},
    ])
    r = client.post("/tenants/voice/campaigns", json=payload)
    cid = r.json()["id"]

    r = client.post(f"/tenants/voice/campaigns/{cid}/launch", json={"dry_run": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enqueued"] == 3
    # high-priority entry jumps the queue
    assert body["order"] == ["+15550002222", "+15550001111", "+15550003333"]
    assert body["estimated_minutes"] == 9.0

    jobs = fake_db.voice_jobs.docs
    assert len(jobs) == 3
    assert [j["priority"] for j in jobs] == ["high", "normal", "normal"]

    # per-call log: status/duration/outcome/transcript placeholder
    r = client.get(f"/tenants/voice/campaigns/{cid}/calls")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 3
    for row in rows:
        assert row["status"] == "queued"
        assert row["outcome"] == "pending"
        assert "transcript" in row and "after the call completes" in row["transcript"]
    # campaign marked launched, cannot re-launch
    assert fake_db.voice_campaigns.docs[0]["status"] == "launched"
    r = client.post(f"/tenants/voice/campaigns/{cid}/launch", json={"dry_run": False})
    assert r.status_code == 409


def test_missing_script_variable_fails_launch_plan(client, fake_db):
    as_user("t1")
    run = __import__("asyncio").new_event_loop().run_until_complete
    run(seed_customer(fake_db, "t1", "c1", "+15550001111", True))
    payload = campaign_payload(audience=[
        {"customer_id": "c1", "phone": "+15550001111",
         "variables": {"customer_name": "Sam"}}  # offer/season missing
    ])
    r = client.post("/tenants/voice/campaigns", json=payload)
    cid = r.json()["id"]
    r = client.post(f"/tenants/voice/campaigns/{cid}/launch", json={"dry_run": True})
    assert r.status_code == 422


# ---------- FCC consent gate ----------

def test_consent_gate_blocks_non_consented_numbers(client, fake_db):
    as_user("t1")
    run = __import__("asyncio").new_event_loop().run_until_complete
    run(seed_customer(fake_db, "t1", "c1", "+15550001111", True))
    run(seed_customer(fake_db, "t1", "c2", "+15550002222", False))  # opted out
    # c3 has no customer record at all -> treated as no-consent
    payload = campaign_payload(audience=[
        {"customer_id": "c1", "phone": "+15550001111",
         "variables": {"customer_name": "Sam", "shop_name": "Apex HVAC",
                       "season": "fall", "offer": "20% off",
                       "booking_link": "https://book.example"}},
        {"customer_id": "c2", "phone": "+15550002222",
         "variables": {"customer_name": "Jo", "shop_name": "Apex HVAC",
                       "season": "fall", "offer": "20% off",
                       "booking_link": "https://book.example"}},
        {"phone": "+15550003333",
         "variables": {"customer_name": "Al", "shop_name": "Apex HVAC",
                       "season": "fall", "offer": "20% off",
                       "booking_link": "https://book.example"}},
    ])
    r = client.post("/tenants/voice/campaigns", json=payload)
    cid = r.json()["id"]

    # dry-run reports blocked numbers but enqueues nothing
    r = client.post(f"/tenants/voice/campaigns/{cid}/launch", json={"dry_run": True})
    assert r.status_code == 200, r.text
    blocked = r.json()["blocked"]
    assert {b["phone"] for b in blocked} == {"+15550002222", "+15550003333"}
    reasons = {b["phone"]: b["reason"] for b in blocked}
    assert reasons["+15550002222"] == "opted_out"
    assert reasons["+15550003333"] == "no_consent_record"

    # real launch is refused outright — FCC compliance
    r = client.post(f"/tenants/voice/campaigns/{cid}/launch", json={"dry_run": False})
    assert r.status_code == 422, r.text
    assert fake_db.voice_calls.docs == []
    assert fake_db.voice_jobs.docs == []
    assert fake_db.voice_campaigns.docs[0]["status"] == "draft"


# ---------- missed-call trigger ----------

def test_missed_call_trigger_creates_60s_due_job(client, fake_db):
    as_user("t1")
    run = __import__("asyncio").new_event_loop().run_until_complete
    run(seed_customer(fake_db, "t1", "c9", "+15550009999", True, name="Rita"))

    before = datetime.now(timezone.utc)
    r = client.post("/tenants/voice/triggers/missed-call", json={
        "customer_id": "c9", "phone": "+15550009999",
        "shop_name": "Apex HVAC", "callback_number": "+15550000000"})
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["kind"] == "missed_call_callback"
    assert job["priority"] == "high"
    assert job["status"] == "queued"
    assert "Hi Rita, this is Apex HVAC" in job["script"]
    due = datetime.fromisoformat(job["due_at"])
    delta = (due - before).total_seconds()
    assert 55 <= delta <= 65, delta  # due within 60s
    assert len(fake_db.voice_jobs.docs) == 1


def test_missed_call_trigger_refuses_opted_out(client, fake_db):
    as_user("t1")
    run = __import__("asyncio").new_event_loop().run_until_complete
    run(seed_customer(fake_db, "t1", "c9", "+15550009999", False))
    r = client.post("/tenants/voice/triggers/missed-call",
                    json={"phone": "+15550009999"})
    assert r.status_code == 409, r.text
    assert fake_db.voice_jobs.docs == []


def test_missed_call_trigger_allows_unknown_number(client):
    as_user("t1")
    r = client.post("/tenants/voice/triggers/missed-call",
                    json={"phone": "+15550007777", "customer_name": "New Caller"})
    assert r.status_code == 200, r.text
    assert "Hi New Caller" in r.json()["script"]


# ---------- tenant isolation + authz ----------

def test_tenant_isolation(client, fake_db):
    run = __import__("asyncio").new_event_loop().run_until_complete
    run(seed_customer(fake_db, "t1", "c1", "+15550001111", True))
    run(seed_customer(fake_db, "t1", "c2", "+15550002222", True))
    as_user("t1")
    r = client.post("/tenants/voice/campaigns", json=campaign_payload())
    cid = r.json()["id"]
    r = client.post(f"/tenants/voice/campaigns/{cid}/launch", json={"dry_run": False})
    assert r.status_code == 200

    as_user("t2")  # different tenant: nothing leaks
    assert client.get("/tenants/voice/campaigns").json() == []
    assert client.get(f"/tenants/voice/campaigns/{cid}/calls").status_code == 404
    assert client.post(f"/tenants/voice/campaigns/{cid}/launch",
                       json={"dry_run": True}).status_code == 404
    assert client.get("/tenants/voice/jobs").json() == []

    # jobs queue only shows the owning tenant's jobs
    as_user("t1")
    assert len(client.get("/tenants/voice/jobs").json()) == 2


def test_writes_require_owner_or_admin(client):
    as_user("t1", role="tech")
    r = client.post("/tenants/voice/campaigns", json=campaign_payload())
    assert r.status_code == 403
    r = client.post("/tenants/voice/triggers/missed-call",
                    json={"phone": "+15550007777"})
    assert r.status_code == 403
    # reads still work for techs
    assert client.get("/tenants/voice/templates").status_code == 200


def test_no_tenant_rejected(client):
    current_user.clear()
    current_user.update({"id": "u-x", "tenant_id": None, "role": "owner"})
    assert client.get("/tenants/voice/templates").status_code == 403


def test_twilio_skeleton_requires_creds(monkeypatch):
    import asyncio
    import services.twilio as twilio_mod

    async def no_cfg(tenant_id):
        return {"account_sid": "", "auth_token": "",
                "messaging_sid": "", "from_number": ""}

    # patch the real config reader so no network/DB is touched
    monkeypatch.setattr(twilio_mod, "_tenant_twilio_cfg", no_cfg)
    monkeypatch.setenv("VOICE_PROVIDER", "live")
    try:
        prov = svc_mod.get_voice_provider("t1")
        assert isinstance(prov, svc_mod.TwilioVoiceProvider)
        with pytest.raises(RuntimeError, match="not configured"):
            asyncio.new_event_loop().run_until_complete(
                prov.place_call(tenant_id="t1", to="+15550001111",
                                script="hi", call_id="c"))
    finally:
        monkeypatch.delenv("VOICE_PROVIDER", raising=False)
