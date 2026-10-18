"""Audit (2026-10-17) Track B — plan seat-limit enforcement.

Owner-approved caps (PR #97): Lite 1 / Starter 2 / Pro 12 / Empire 25.
Before this audit the caps were enforced NOWHERE: invites never checked
them and models.PLAN_LIMITS had no "users" key. These tests pin the caps
by plan key (mirroring the collision-guard style of
test_final_integration_cleanup.py), pin the DB-first resolution, and
exercise the enforcement points: invite creation, invite acceptance
(password + Google), with tenant isolation.

Self-contained: in-memory fake Mongo + dependency overrides, no network.
"""
from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("JWT_SECRET", "seat-limits-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "seat_limits_test")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.invitations as inv_mod  # noqa: E402
from models import PLAN_LIMITS  # noqa: E402
from routers.plans import DEFAULT_PLANS  # noqa: E402
from security import require_tenant_owner_or_admin  # noqa: E402
from services import seat_limits  # noqa: E402


# ---------- fake mongo (same pattern as test_bookkeeping_link.py) ----------

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

    async def update_one(self, query, update, upsert=False):
        matched = 0
        for d in self.docs:
            if _match(d, query):
                matched += 1
                for k, v in update.get("$set", {}).items():
                    d[k] = v
                break
        return type("R", (), {"matched_count": matched, "modified_count": matched})()


class FakeDB:
    def __init__(self):
        self.tenants = FakeCollection()
        self.users = FakeCollection()
        self.invitations = FakeCollection()
        self.plans = FakeCollection()
        self.user_sessions = FakeCollection()


# ---------- helpers ----------

def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def seed_plan(fake_db, key, users):
    fake_db.plans.docs.append({"key": key, "limits": {"users": users}})


def seed_tenant(fake_db, tenant_id="t1", plan="lite"):
    fake_db.tenants.docs.append({
        "id": tenant_id, "name": f"Shop {tenant_id}", "slug": tenant_id,
        "plan_id": plan, "subscription_status": "active",
    })


def seed_user(fake_db, tenant_id="t1", n=0, role="staff"):
    fake_db.users.docs.append({
        "id": f"u-{tenant_id}-{n}", "email": f"u{n}@{tenant_id}.example",
        "tenant_id": tenant_id, "role": role, "name": f"User {n}",
    })


def seed_users(fake_db, tenant_id, count):
    for i in range(count):
        seed_user(fake_db, tenant_id, i, role="owner" if i == 0 else "staff")


def seed_invite(fake_db, tenant_id="t1", email="new@example.com", token="tok-1",
                status="pending"):
    fake_db.invitations.docs.append({
        "id": f"inv-{token}", "tenant_id": tenant_id, "email": email,
        "role": "staff", "token": token, "invited_by": "u-t1-0",
        "status": status, "created_at": "2026-10-17T00:00:00+00:00",
    })


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(inv_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(inv_mod.router)
    app.include_router(inv_mod.public_router)

    def fake_owner():
        if current_user.get("role") not in {"owner", "admin"}:
            raise HTTPException(403, "Owner/admin role required")
        return current_user

    app.dependency_overrides[require_tenant_owner_or_admin] = fake_owner
    current_user.clear()
    current_user.update({"id": "u-t1-0", "tenant_id": "t1", "role": "owner"})
    return TestClient(app, raise_server_exceptions=False)


# ---------- caps pinned by plan key ----------

def test_plan_limits_fallback_pins_owner_approved_caps():
    users = {k: v.get("users") for k, v in PLAN_LIMITS.items()}
    # "unlimited" (owner decision 2026-10-17) is pinned as None on purpose:
    # seats are uncapped on that tier; every capped tier keeps its PR #97 cap.
    assert users == {"trial": 1, "lite": 1, "starter": 2, "pro": 12,
                     "empire": 25, "unlimited": None}


def test_default_plans_and_fallback_never_drift():
    for plan in DEFAULT_PLANS:
        key = plan["key"]
        if key in PLAN_LIMITS:
            assert (plan.get("limits") or {}).get("users") == PLAN_LIMITS[key]["users"], key


# ---------- limit resolution ----------

def test_db_plan_doc_wins_over_fallback(fake_db):
    seed_tenant(fake_db, plan="pro")
    seed_plan(fake_db, "pro", 12)
    assert run(seat_limits.get_user_limit(fake_db, "t1")) == 12


def test_db_plan_doc_custom_value_is_honored(fake_db):
    # Admins manage plan docs; enforcement must honor the stored value
    # rather than silently substituting the code default.
    seed_tenant(fake_db, plan="pro")
    seed_plan(fake_db, "pro", 40)
    assert run(seat_limits.get_user_limit(fake_db, "t1")) == 40


def test_fallback_when_no_plan_doc(fake_db):
    seed_tenant(fake_db, plan="empire")
    assert run(seat_limits.get_user_limit(fake_db, "t1")) == 25


def test_explicit_null_users_in_plan_doc_means_uncapped(fake_db):
    seed_tenant(fake_db, plan="pro")
    seed_plan(fake_db, "pro", None)
    assert run(seat_limits.get_user_limit(fake_db, "t1")) is None


def test_enterprise_has_no_cap(fake_db):
    seed_tenant(fake_db, plan="enterprise")
    assert run(seat_limits.get_user_limit(fake_db, "t1")) is None


def test_trial_tenant_defaults_to_one_seat(fake_db):
    fake_db.tenants.docs.append({"id": "t9", "subscription_status": "trialing"})
    assert run(seat_limits.get_user_limit(fake_db, "t9")) == 1


# ---------- invite creation ----------

def test_invite_blocked_when_lite_seat_full(client, fake_db):
    seed_tenant(fake_db, plan="lite")
    seed_plan(fake_db, "lite", 1)
    seed_users(fake_db, "t1", 1)  # the owner occupies Lite's only seat
    r = client.post("/tenants/invitations", json={"email": "a@b.co", "role": "staff"})
    assert r.status_code == 403
    assert "seat limit" in r.json()["detail"]
    assert "'lite'" in r.json()["detail"] and "1 user" in r.json()["detail"]
    assert fake_db.invitations.docs == []


def test_invite_counts_pending_invitations(client, fake_db):
    seed_tenant(fake_db, plan="starter")
    seed_plan(fake_db, "starter", 2)
    seed_users(fake_db, "t1", 1)
    seed_invite(fake_db, email="pending@example.com", token="tok-p")
    r = client.post("/tenants/invitations", json={"email": "a@b.co", "role": "staff"})
    assert r.status_code == 403


def test_invite_allowed_below_cap(client, fake_db):
    seed_tenant(fake_db, plan="pro")
    seed_plan(fake_db, "pro", 12)
    seed_users(fake_db, "t1", 3)
    r = client.post("/tenants/invitations", json={"email": "a@b.co", "role": "staff"})
    assert r.status_code == 200
    assert len(fake_db.invitations.docs) == 1


def test_invite_cap_is_per_tenant(client, fake_db):
    seed_tenant(fake_db, tenant_id="t1", plan="lite")
    seed_plan(fake_db, "lite", 1)
    seed_users(fake_db, "t1", 1)
    # Another tenant being full must not matter — and t1's own fullness
    # is what blocks. Give t2 room and invite there via its owner.
    seed_tenant(fake_db, tenant_id="t2", plan="pro")
    seed_plan(fake_db, "pro", 12)
    seed_users(fake_db, "t2", 2)
    current_user.update({"id": "u-t2-0", "tenant_id": "t2", "role": "owner"})
    r = client.post("/tenants/invitations", json={"email": "a@b.co", "role": "staff"})
    assert r.status_code == 200


# ---------- invite acceptance ----------

def test_accept_blocked_at_cap_and_invite_stays_pending(client, fake_db):
    seed_tenant(fake_db, plan="lite")
    seed_plan(fake_db, "lite", 1)
    seed_users(fake_db, "t1", 1)
    seed_invite(fake_db, token="tok-full")
    r = client.post("/public/invitations/accept",
                    json={"token": "tok-full", "password": "long-enough-1", "name": "New"})
    assert r.status_code == 403
    assert "seat limit" in r.json()["detail"]
    inv = fake_db.invitations.docs[0]
    assert inv["status"] == "pending"  # usable after a seat frees up
    assert len(fake_db.users.docs) == 1  # no user was created


def test_accept_allowed_below_cap(client, fake_db):
    seed_tenant(fake_db, plan="starter")
    seed_plan(fake_db, "starter", 2)
    seed_users(fake_db, "t1", 1)
    seed_invite(fake_db, token="tok-ok")
    r = client.post("/public/invitations/accept",
                    json={"token": "tok-ok", "password": "long-enough-1", "name": "New"})
    assert r.status_code == 200
    assert len(fake_db.users.docs) == 2
    assert fake_db.invitations.docs[0]["status"] == "accepted"


def test_pro_cap_boundary_twelve(client, fake_db):
    seed_tenant(fake_db, plan="pro")
    seed_plan(fake_db, "pro", 12)
    seed_users(fake_db, "t1", 12)
    seed_invite(fake_db, token="tok-13th")
    r = client.post("/public/invitations/accept",
                    json={"token": "tok-13th", "password": "long-enough-1", "name": "13th"})
    assert r.status_code == 403


# ---------- Google acceptance ----------

@pytest.fixture
def google_on(monkeypatch):
    monkeypatch.setattr(inv_mod, "google_configured", lambda: True)

    async def fake_identity(code="", id_token="", redirect_uri=""):
        return {"email": "invited@example.com", "name": "Invited Person",
                "picture": "", "session_token": "sess-1"}

    monkeypatch.setattr(inv_mod, "resolve_google_identity", fake_identity)


def test_accept_google_new_user_blocked_at_cap(client, fake_db, google_on):
    seed_tenant(fake_db, plan="lite")
    seed_plan(fake_db, "lite", 1)
    seed_users(fake_db, "t1", 1)
    seed_invite(fake_db, email="invited@example.com", token="tok-g")
    r = client.post("/public/invitations/accept-google",
                    json={"token": "tok-g", "id_token": "x"})
    assert r.status_code == 403
    assert len(fake_db.users.docs) == 1


def test_accept_google_existing_user_from_other_tenant_blocked(client, fake_db, google_on):
    seed_tenant(fake_db, plan="lite")
    seed_plan(fake_db, "lite", 1)
    seed_users(fake_db, "t1", 1)
    # The invitee already has an account — under a DIFFERENT tenant, so
    # attaching them here would consume t1's only seat.
    fake_db.users.docs.append({
        "id": "u-elsewhere", "email": "invited@example.com",
        "tenant_id": "t-other", "role": "owner", "name": "Elsewhere",
    })
    seed_invite(fake_db, email="invited@example.com", token="tok-g2")
    r = client.post("/public/invitations/accept-google",
                    json={"token": "tok-g2", "id_token": "x"})
    assert r.status_code == 403
    assert fake_db.users.docs[-1]["tenant_id"] == "t-other"  # not re-attached


def test_accept_google_below_cap_creates_user(client, fake_db, google_on):
    seed_tenant(fake_db, plan="starter")
    seed_plan(fake_db, "starter", 2)
    seed_users(fake_db, "t1", 1)
    seed_invite(fake_db, email="invited@example.com", token="tok-g3")
    r = client.post("/public/invitations/accept-google",
                    json={"token": "tok-g3", "id_token": "x"})
    assert r.status_code == 200
    assert len(fake_db.users.docs) == 2
