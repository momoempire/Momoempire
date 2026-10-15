"""Unit tests for GPS tech tracking (routers/tracking.py + the en_route SMS hook).

No database, network, or browser needed: a small FastAPI app with the tracking
and dispatch routers, a fake in-memory Mongo, and an overridden auth dependency.
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "tracking-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "tracking_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.tracking as tracking_mod  # noqa: E402
import routers.dispatch as dispatch_mod  # noqa: E402
import routers.call_to_payment as c2p_mod  # noqa: E402
from security import require_tenant_user  # noqa: E402


# ---------- fake mongo ----------

class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        self.docs = sorted(self.docs, key=lambda d: str(d.get(key, "")), reverse=(direction < 0))
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
                elif op == "$gte" and not (val >= opval):
                    return False
                elif op == "$lte" and not (val <= opval):
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
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("id"))

    async def update_one(self, query, update):
        matched = 0
        for d in self.docs:
            if _match(d, query):
                matched += 1
                for k, v in update.get("$set", {}).items():
                    d[k] = v
                for k in update.get("$unset", {}):
                    d.pop(k, None)
                break
        return SimpleNamespace(matched_count=matched, modified_count=matched)


class FakeDB:
    def __init__(self):
        self.appointments = FakeCollection()
        self.schedule_staff = FakeCollection()
        self.tech_locations = FakeCollection()
        self.integrations = FakeCollection()
        self.tenants = FakeCollection()


# ---------- fixtures ----------

current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    for mod in (dispatch_mod, c2p_mod, tracking_mod):
        monkeypatch.setattr(mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(dispatch_mod.router)
    app.include_router(tracking_mod.router)
    app.include_router(tracking_mod.public_router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, role="owner", staff_id=None):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}-{role}", "tenant_id": tenant_id, "role": role})
    if staff_id:
        current_user["staff_id"] = staff_id


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def seed_staff(db, tenant_id, sid, name="Tech One"):
    await db.schedule_staff.insert_one(
        {"id": sid, "tenant_id": tenant_id, "name": name, "role": "technician"})


async def seed_job(db, tenant_id, jid, staff_id="", status="scheduled", **extra):
    doc = {
        "id": jid, "tenant_id": tenant_id, "customer_name": f"Cust {jid}",
        "customer_phone": "+15550100", "service_name": "AC Repair",
        "staff_id": staff_id, "staff": "", "start_at": "2026-10-20T09:00:00+00:00",
        "end_at": "2026-10-20T10:00:00+00:00", "status": status, "notes": "",
        "service_address": {"street": "123 Main St", "city": "Dallas", "state": "TX"},
    }
    doc.update(extra)
    await db.appointments.insert_one(doc)


async def seed_sms_config(db, tenant_id):
    await db.integrations.insert_one({
        "tenant_id": tenant_id, "key": "twilio",
        "config": {"account_sid": "AC_test", "auth_token": "tok", "from_number": "+15550000"},
    })
    await db.tenants.insert_one({"id": tenant_id, "name": "Acme HVAC"})


# ---------- POST /location auth ----------

def test_owner_can_post_for_any_tech(client, fake_db):
    as_user("t1", "owner")
    run(seed_staff(fake_db, "t1", "tech1", "Amy"))

    r = client.post("/tenants/tracking/location",
                    json={"tech_id": "tech1", "lat": 32.7, "lng": -97.3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lat"] == 32.7 and body["lng"] == -97.3
    assert body["updated_at"]


def test_tech_can_post_own_location(client, fake_db):
    as_user("t1", "technician", staff_id="tech1")
    run(seed_staff(fake_db, "t1", "tech1", "Amy"))

    r = client.post("/tenants/tracking/location",
                    json={"tech_id": "tech1", "lat": 32.7, "lng": -97.3})
    assert r.status_code == 200, r.text


def test_tech_cannot_post_as_another_tech(client, fake_db):
    as_user("t1", "technician", staff_id="tech1")
    run(seed_staff(fake_db, "t1", "tech1", "Amy"))
    run(seed_staff(fake_db, "t1", "tech2", "Bo"))

    r = client.post("/tenants/tracking/location",
                    json={"tech_id": "tech2", "lat": 32.7, "lng": -97.3})
    assert r.status_code == 403


def test_non_owner_without_staff_link_rejected(client, fake_db):
    as_user("t1", "technician")  # no staff_id link -> cannot post at all
    run(seed_staff(fake_db, "t1", "tech1", "Amy"))

    r = client.post("/tenants/tracking/location",
                    json={"tech_id": "tech1", "lat": 32.7, "lng": -97.3})
    assert r.status_code == 403


def test_post_location_cross_tenant_tech_rejected(client, fake_db):
    as_user("t1", "owner")
    run(seed_staff(fake_db, "t2", "techX", "Xavier"))

    r = client.post("/tenants/tracking/location",
                    json={"tech_id": "techX", "lat": 32.7, "lng": -97.3})
    assert r.status_code == 404


def test_post_location_lat_validation(client, fake_db):
    as_user("t1", "owner")
    run(seed_staff(fake_db, "t1", "tech1", "Amy"))

    r = client.post("/tenants/tracking/location",
                    json={"tech_id": "tech1", "lat": 91.0, "lng": -97.3})
    assert r.status_code == 422


def test_post_location_job_scoped_to_tenant(client, fake_db):
    as_user("t1", "owner")
    run(seed_staff(fake_db, "t1", "tech1", "Amy"))
    run(seed_job(fake_db, "t2", "jOther"))  # other tenant's job

    r = client.post("/tenants/tracking/location",
                    json={"tech_id": "tech1", "lat": 32.7, "lng": -97.3, "job_id": "jOther"})
    assert r.status_code == 404


def test_location_history_capped_at_50(client, fake_db):
    as_user("t1", "owner")
    run(seed_staff(fake_db, "t1", "tech1", "Amy"))

    for i in range(55):
        r = client.post("/tenants/tracking/location",
                        json={"tech_id": "tech1", "lat": 32.7 + i * 0.001, "lng": -97.3})
        assert r.status_code == 200, r.text

    doc = run(fake_db.tech_locations.find_one({"tech_id": "tech1", "tenant_id": "t1"}))
    assert len(doc["history"]) == 50
    assert doc["lat"] == 32.7 + 54 * 0.001  # latest point is current


# ---------- GET /live tenant scoping ----------

def test_live_tenant_scoped(client, fake_db):
    as_user("t1", "owner")
    run(seed_staff(fake_db, "t1", "tech1", "Amy"))
    run(seed_staff(fake_db, "t2", "techX", "Xavier"))

    client.post("/tenants/tracking/location", json={"tech_id": "tech1", "lat": 32.7, "lng": -97.3})
    as_user("t2", "owner")
    client.post("/tenants/tracking/location", json={"tech_id": "techX", "lat": 40.0, "lng": -75.0})

    as_user("t1", "owner")
    r = client.get("/tenants/tracking/live")
    assert r.status_code == 200, r.text
    techs = r.json()["techs"]
    assert [t["tech_id"] for t in techs] == ["tech1"]
    assert techs[0]["lat"] == 32.7 and techs[0]["lng"] == -97.3
    assert techs[0]["current_job_id"] is None


def test_live_includes_techs_without_signal(client, fake_db):
    as_user("t1", "owner")
    run(seed_staff(fake_db, "t1", "tech1", "Amy"))

    r = client.get("/tenants/tracking/live")
    techs = r.json()["techs"]
    assert techs[0]["lat"] is None and techs[0]["updated_at"] is None


# ---------- share / public ETA ----------

def test_share_creates_token_and_public_eta(client, fake_db):
    as_user("t1", "owner")
    run(seed_staff(fake_db, "t1", "tech1", "Amy Adams"))
    run(seed_job(fake_db, "t1", "j1", staff_id="tech1", status="en_route", staff="Amy Adams"))
    run(fake_db.appointments.update_one(
        {"id": "j1", "tenant_id": "t1"}, {"$set": {"service_lat": 32.8, "service_lng": -97.3}}))

    r = client.post("/tenants/tracking/jobs/j1/share", json={})
    assert r.status_code == 200, r.text
    token = r.json()["eta_token"]
    assert len(token) >= 40  # unguessable (secrets.token_urlsafe)

    # second share rotates the token
    r2 = client.post("/tenants/tracking/jobs/j1/share", json={})
    assert r2.json()["eta_token"] != token

    # post a live position for the tech
    client.post("/tenants/tracking/location",
                json={"tech_id": "tech1", "lat": 32.7, "lng": -97.3, "job_id": "j1"})

    r = client.get(f"/public/eta/{r2.json()['eta_token']}")
    assert r.status_code == 200, r.text
    body = r.json()
    # ONLY the allowed fields — assert the exact key set
    assert set(body.keys()) == {"tech_first_name", "lat", "lng",
                                "location_updated_at", "eta_text", "status"}
    assert body["tech_first_name"] == "Amy"  # first name only
    assert body["lat"] == 32.7 and body["lng"] == -97.3
    assert body["status"] == "en_route"


def test_public_eta_exposes_no_pii(client, fake_db):
    as_user("t1", "owner")
    run(seed_staff(fake_db, "t1", "tech1", "Amy Adams"))
    run(seed_job(fake_db, "t1", "j1", staff_id="tech1", status="en_route",
                 staff="Amy Adams", customer_name="Secret Customer",
                 customer_phone="+15559998888"))

    token = client.post("/tenants/tracking/jobs/j1/share", json={}).json()["eta_token"]
    r = client.get(f"/public/eta/{token}")
    blob = r.text.lower()
    assert "secret customer" not in blob
    assert "+15559998888" not in blob and "5559998888" not in blob
    assert "123 main st" not in blob and "dallas" not in blob
    assert "ac repair" not in blob  # service name is not part of the public payload


def test_public_eta_unknown_token_404(client, fake_db):
    r = client.get("/public/eta/this-token-does-not-exist")
    assert r.status_code == 404


def test_revoked_token_404(client, fake_db):
    as_user("t1", "owner")
    run(seed_staff(fake_db, "t1", "tech1", "Amy"))
    run(seed_job(fake_db, "t1", "j1", staff_id="tech1", status="en_route"))

    token = client.post("/tenants/tracking/jobs/j1/share", json={}).json()["eta_token"]
    assert client.get(f"/public/eta/{token}").status_code == 200

    r = client.delete("/tenants/tracking/jobs/j1/share")
    assert r.status_code == 200 and r.json()["revoked"] is True

    assert client.get(f"/public/eta/{token}").status_code == 404
    # revoking twice reports missing share
    assert client.delete("/tenants/tracking/jobs/j1/share").status_code == 404


def test_share_other_tenant_job_hidden(client, fake_db):
    as_user("t1", "owner")
    run(seed_job(fake_db, "t2", "jB", status="en_route"))
    assert client.post("/tenants/tracking/jobs/jB/share", json={}).status_code == 404
    assert client.delete("/tenants/tracking/jobs/jB/share").status_code == 404


# ---------- ETA math ----------

def test_eta_math_sanity():
    # 30 miles straight-line at 30mph -> "About 60 min away"
    text = tracking_mod.eta_text_for(40.0, -74.0, 40.43448, -74.0, "en_route")
    assert text == "About 60 min away"


def test_eta_very_close_arriving_now():
    text = tracking_mod.eta_text_for(40.0, -74.0, 40.001, -74.0, "en_route")
    assert text == "Arriving now"


def test_eta_no_coords_status_only():
    assert tracking_mod.eta_text_for(40.0, -74.0, None, None, "en_route") == "On the way"
    assert tracking_mod.eta_text_for(None, None, 40.0, -74.0, "en_route") == "On the way"
    assert tracking_mod.eta_text_for(None, None, None, None, "on_site") == "Tech has arrived"


def test_public_eta_without_coords_returns_status_only(client, fake_db):
    as_user("t1", "owner")
    run(seed_staff(fake_db, "t1", "tech1", "Amy Adams"))
    run(seed_job(fake_db, "t1", "j1", staff_id="tech1", status="en_route", staff="Amy Adams"))

    token = client.post("/tenants/tracking/jobs/j1/share", json={}).json()["eta_token"]
    body = client.get(f"/public/eta/{token}").json()
    assert body["eta_text"] == "On the way"  # no coords anywhere -> status-only, not a failure
    assert body["lat"] is None


# ---------- en_route SMS hook ----------

def _recorder():
    calls = []

    async def send_sms(*, tenant_id, to, body):
        calls.append({"tenant_id": tenant_id, "to": to, "body": body})
        return {"sid": "SM_test", "status": "queued", "demo": False}

    return calls, send_sms


def test_sms_sent_on_en_route_when_configured(client, fake_db, monkeypatch):
    as_user("t1", "owner")
    run(seed_staff(fake_db, "t1", "tech1", "Amy Adams"))
    run(seed_job(fake_db, "t1", "j1", staff_id="tech1", staff="Amy Adams"))
    run(seed_sms_config(fake_db, "t1"))
    calls, fake_send = _recorder()
    monkeypatch.setattr(tracking_mod, "send_sms", fake_send)
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.com")

    r = client.post("/tenants/dispatch/status", json={"job_id": "j1", "status": "en_route"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "en_route"
    assert len(calls) == 1
    assert calls[0]["to"] == "+15550100"
    assert "Amy" in calls[0]["body"]
    assert "/track/" in calls[0]["body"]
    # the hook minted the eta token on the job
    job = run(fake_db.appointments.find_one({"id": "j1", "tenant_id": "t1"}))
    assert job["eta_token"] and job["eta_token"] in calls[0]["body"]


def test_sms_skipped_when_twilio_not_configured(client, fake_db, monkeypatch):
    as_user("t1", "owner")
    run(seed_staff(fake_db, "t1", "tech1", "Amy Adams"))
    run(seed_job(fake_db, "t1", "j1", staff_id="tech1", staff="Amy Adams"))
    # no integrations row -> SMS not configured
    calls, fake_send = _recorder()
    monkeypatch.setattr(tracking_mod, "send_sms", fake_send)

    r = client.post("/tenants/dispatch/status", json={"job_id": "j1", "status": "en_route"})
    assert r.status_code == 200, r.text
    assert calls == []


def test_sms_failure_does_not_break_status_update(client, fake_db, monkeypatch):
    as_user("t1", "owner")
    run(seed_staff(fake_db, "t1", "tech1", "Amy Adams"))
    run(seed_job(fake_db, "t1", "j1", staff_id="tech1", staff="Amy Adams"))
    run(seed_sms_config(fake_db, "t1"))

    async def boom(*, tenant_id, to, body):
        raise RuntimeError("twilio misconfigured")

    monkeypatch.setattr(tracking_mod, "send_sms", boom)

    r = client.post("/tenants/dispatch/status", json={"job_id": "j1", "status": "en_route"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "en_route"
