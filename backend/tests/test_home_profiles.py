"""Tests for routers/home_profiles.py (transferable home service profile).

Uses a fake in-memory mongo (same pattern as test_reputation.py): no live
database. Covers profile CRUD, the full consent-gated transfer flow
(initiate -> approve -> claim), PII redaction on transfer (old-owner contact
and payment info never leak to the new owner), claim-before-approval
rejection, auto-approval policy, token handling, audit trail, and tenant
isolation.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "home-profiles-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "home_profiles_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.home_profiles as hp_mod  # noqa: E402
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
        self.home_profiles = FakeCollection()
        self.profile_transfers = FakeCollection()


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(hp_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(hp_mod.router)
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


PROFILE_PAYLOAD = {
    "property_address": "123 Maple St, Springfield",
    "owner_name": "Old Owner",
    "owner_contact": {"name": "Old Owner", "email": "old@example.com", "phone": "555-0001"},
    "owner_account_id": "acct-old",
    "payment_info": {"card_last4": "4242", "billing_email": "old@example.com"},
    "equipment": [{
        "type": "HVAC", "brand": "Carrier", "model": "24ABC6",
        "serial": "SN-111", "installed_at": "2022-05-01", "warranty_expires": "2032-05-01",
    }],
    "service_history": [{
        "date": "2026-09-01", "service_type": "Tune-up",
        "notes": "Coils cleaned", "technician": "Sam", "job_id": "job-1",
    }],
    "warranties": [{
        "title": "10-year parts", "provider": "Carrier",
        "expires_at": "2032-05-01", "coverage": "parts",
    }],
    "maintenance_plans": [{
        "name": "Annual tune-up", "frequency": "yearly", "next_due": "2027-05-01",
    }],
    "notes": "Dog on premises — call before arrival.",
}


def create_profile(client, **overrides):
    payload = dict(PROFILE_PAYLOAD)
    payload.update(overrides)
    r = client.post("/api/home-profiles/", json=payload)
    assert r.status_code == 200, r.text
    return r.json()


def initiate_transfer(client, profile_id, auto_approve_days=30):
    r = client.post(f"/api/home-profiles/{profile_id}/transfer/initiate", json={
        "new_owner_name": "New Owner",
        "new_owner_contact": {"name": "New Owner", "email": "new@example.com", "phone": "555-0002"},
        "new_owner_account_id": "acct-new",
        "auto_approve_days": auto_approve_days,
    })
    assert r.status_code == 200, r.text
    return r.json()


# ---------- profile CRUD ----------

def test_create_profile(client, fake_db):
    as_user()
    b = create_profile(client)
    assert b["ok"] and b["id"]
    assert b["tenant_id"] == "t1"
    assert b["property_address"] == "123 Maple St, Springfield"
    assert b["equipment"][0]["serial"] == "SN-111"
    assert b["warranties"][0]["title"] == "10-year parts"
    assert b["payment_info"]["card_last4"] == "4242"
    assert len(fake_db.home_profiles.docs) == 1


def test_create_profile_requires_address(client):
    as_user()
    r = client.post("/api/home-profiles/", json={"property_address": ""})
    assert r.status_code == 422


def test_create_profile_non_owner_forbidden(client):
    as_user(role="tech")
    r = client.post("/api/home-profiles/", json={"property_address": "1 Main St"})
    assert r.status_code == 403


def test_list_profiles_summaries(client):
    as_user()
    create_profile(client)
    b = client.get("/api/home-profiles/").json()
    assert b["count"] == 1
    p = b["profiles"][0]
    assert p["property_address"] == "123 Maple St, Springfield"
    assert p["equipment_count"] == 1 and p["service_count"] == 1 and p["warranty_count"] == 1
    # Summary view leaks no PII/payment details
    assert "owner_contact" not in p and "payment_info" not in p


def test_get_profile_detail(client):
    as_user()
    b = create_profile(client)
    r = client.get(f"/api/home-profiles/{b['id']}")
    assert r.status_code == 200
    d = r.json()
    assert d["equipment"][0]["model"] == "24ABC6"
    assert d["notes"] == "Dog on premises — call before arrival."


def test_get_profile_missing_404(client):
    as_user()
    r = client.get("/api/home-profiles/nope")
    assert r.status_code == 404


def test_update_profile_sections(client):
    as_user()
    b = create_profile(client)
    r = client.put(f"/api/home-profiles/{b['id']}", json={
        "equipment": [{"type": "Water heater", "brand": "Rheem"}],
        "notes": "Updated notes",
    })
    assert r.status_code == 200
    d = r.json()
    assert d["equipment"] == [{"type": "Water heater", "brand": "Rheem",
                               "model": "", "serial": "", "installed_at": "",
                               "warranty_expires": "", "notes": ""}]
    assert d["notes"] == "Updated notes"
    assert d["owner_name"] == "Old Owner"  # owner identity untouched by PUT


def test_tenant_isolation(client):
    as_user(tenant_id="t1")
    b = create_profile(client)
    as_user(tenant_id="t2")
    assert client.get("/api/home-profiles/").json()["count"] == 0
    assert client.get(f"/api/home-profiles/{b['id']}").status_code == 404
    r = client.post(f"/api/home-profiles/{b['id']}/transfer/initiate",
                    json={"new_owner_name": "X"})
    assert r.status_code == 404


# ---------- transfer happy path ----------

def test_full_transfer_happy_path(client, fake_db):
    as_user()
    profile = create_profile(client)
    pid = profile["id"]

    # 1. shop initiates
    t = initiate_transfer(client, pid)
    assert t["status"] == "initiated"
    assert t["token"]
    assert t["events"][0]["event"] == "initiated"
    token = t["token"]

    # 2. old owner approves via token link (public route — no auth needed)
    r = client.post(f"/api/home-profiles/transfer/{token}/approve")
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    # 3. new owner claims with consent
    r = client.post(f"/api/home-profiles/transfer/{token}/claim", json={
        "new_owner_name": "New Owner",
        "new_owner_contact": {"name": "New Owner", "email": "new@example.com", "phone": "555-0002"},
        "new_owner_account_id": "acct-new",
        "consent": True,
    })
    assert r.status_code == 200
    assert r.json()["status"] == "claimed"

    # Profile now belongs to the new owner
    d = client.get(f"/api/home-profiles/{pid}").json()
    assert d["owner_name"] == "New Owner"
    assert d["owner_account_id"] == "acct-new"
    assert d["owner_contact"] == {"name": "New Owner", "email": "new@example.com", "phone": "555-0002"}

    # Service data carried over intact
    assert d["equipment"][0]["serial"] == "SN-111"
    assert d["service_history"][0]["service_type"] == "Tune-up"
    assert d["warranties"][0]["title"] == "10-year parts"
    assert d["maintenance_plans"][0]["name"] == "Annual tune-up"
    assert d["notes"] == "Dog on premises — call before arrival."

    # Audit trail: initiated -> approved -> claimed
    b = client.get(f"/api/home-profiles/{pid}/transfers").json()
    assert b["count"] == 1
    events = b["transfers"][0]["events"]
    assert [e["event"] for e in events] == ["initiated", "approved", "claimed"]


def test_pii_redacted_on_transfer(client):
    """Old-owner contact details and payment info must not survive on the
    profile after the claim."""
    as_user()
    profile = create_profile(client)
    pid = profile["id"]
    t = initiate_transfer(client, pid)
    token = t["token"]
    client.post(f"/api/home-profiles/transfer/{token}/approve")
    client.post(f"/api/home-profiles/transfer/{token}/claim", json={
        "new_owner_name": "New Owner",
        "new_owner_contact": {"name": "New Owner", "email": "new@example.com", "phone": "555-0002"},
        "consent": True,
    })

    d = client.get(f"/api/home-profiles/{pid}").json()
    # payment info wiped
    assert d["payment_info"] == {}
    # old-owner PII gone from the profile document
    body = str(d)
    assert "old@example.com" not in body
    assert "555-0001" not in body
    assert "card_last4" not in body
    assert "acct-old" not in body


def test_claim_without_approval_fails(client):
    as_user()
    profile = create_profile(client)
    t = initiate_transfer(client, profile["id"], auto_approve_days=60)
    r = client.post(f"/api/home-profiles/transfer/{t['token']}/claim", json={
        "new_owner_name": "New Owner", "consent": True,
    })
    assert r.status_code == 409
    assert "approval" in r.json()["detail"].lower()


def test_claim_without_consent_rejected(client):
    as_user()
    profile = create_profile(client)
    t = initiate_transfer(client, profile["id"])
    client.post(f"/api/home-profiles/transfer/{t['token']}/approve")
    r = client.post(f"/api/home-profiles/transfer/{t['token']}/claim", json={
        "new_owner_name": "New Owner", "consent": False,
    })
    assert r.status_code == 400


def test_auto_approve_after_policy_window(client):
    """auto_approve_days=0 means the old owner's silence immediately counts as
    approval — the new owner can claim without an explicit approve."""
    as_user()
    profile = create_profile(client)
    t = initiate_transfer(client, profile["id"], auto_approve_days=0)
    r = client.post(f"/api/home-profiles/transfer/{t['token']}/claim", json={
        "new_owner_name": "New Owner", "consent": True,
    })
    assert r.status_code == 200
    assert r.json()["status"] == "claimed"
    b = client.get(f"/api/home-profiles/{profile['id']}/transfers").json()
    assert "auto_approved" in [e["event"] for e in b["transfers"][0]["events"]]


def test_double_initiate_conflicts(client):
    as_user()
    profile = create_profile(client)
    initiate_transfer(client, profile["id"])
    r = client.post(f"/api/home-profiles/{profile['id']}/transfer/initiate",
                    json={"new_owner_name": "Someone Else"})
    assert r.status_code == 409


def test_approve_twice_conflicts(client):
    as_user()
    profile = create_profile(client)
    t = initiate_transfer(client, profile["id"])
    client.post(f"/api/home-profiles/transfer/{t['token']}/approve")
    r = client.post(f"/api/home-profiles/transfer/{t['token']}/approve")
    assert r.status_code == 409


def test_unknown_token_404(client):
    assert client.post("/api/home-profiles/transfer/bad-token/approve").status_code == 404
    assert client.post("/api/home-profiles/transfer/bad-token/claim",
                       json={"new_owner_name": "X", "consent": True}).status_code == 404


def test_token_single_use_after_claim(client):
    """After a successful claim the token is dead — no replays."""
    as_user()
    profile = create_profile(client)
    t = initiate_transfer(client, profile["id"])
    client.post(f"/api/home-profiles/transfer/{t['token']}/approve")
    client.post(f"/api/home-profiles/transfer/{t['token']}/claim", json={
        "new_owner_name": "New Owner", "consent": True,
    })
    assert client.post(f"/api/home-profiles/transfer/{t['token']}/approve").status_code == 404


def test_transfer_routes_need_no_session(client):
    """approve/claim must work without any auth dependency override."""
    as_user()
    profile = create_profile(client)
    t = initiate_transfer(client, profile["id"])
    current_user.clear()  # no logged-in user at all
    assert client.post(f"/api/home-profiles/transfer/{t['token']}/approve").status_code == 200
    r = client.post(f"/api/home-profiles/transfer/{t['token']}/claim", json={
        "new_owner_name": "New Owner", "consent": True,
    })
    assert r.status_code == 200


def test_transfers_audit_trail_empty(client):
    as_user()
    profile = create_profile(client)
    b = client.get(f"/api/home-profiles/{profile['id']}/transfers").json()
    assert b["count"] == 0
