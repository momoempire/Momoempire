"""Unlimited tier x seat enforcement interplay (landing reconciliation).

The Unlimited tier (owner decision 2026-10-17) was built BEFORE the audit's
seat-limit enforcement (Track B, PR #98) existed. This file proves the two
compose: an `unlimited` tenant is UNCAPPED end-to-end under the live
enforcement — seat resolution, invite creation, invite acceptance
(password + Google), and usage rendering — through BOTH resolution paths
Track B defined:

  1. a seeded plan doc (DEFAULT_PLANS' unlimited entry stores users=None;
     seat_limits treats an explicit null as uncapped), and
  2. no plan doc at all (models.PLAN_LIMITS fallback, whose unlimited
     entry likewise carries users=None).

Before the reconciliation, PLAN_LIMITS had no "unlimited" key at all, so
path 2 resolved the metering fallback to trial limits and the tier existed
only in DEFAULT_PLANS. Self-contained: in-memory fake Mongo + dependency
overrides, no network (same pattern as test_seat_limits.py).
"""
from __future__ import annotations

import asyncio
import os

import pytest

os.environ.setdefault("JWT_SECRET", "unlimited-seat-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "unlimited_seat_test")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.invitations as inv_mod  # noqa: E402
import routers.usage as usage_mod  # noqa: E402
from models import PLAN_LIMITS  # noqa: E402
from routers.plans import DEFAULT_PLANS  # noqa: E402
from security import require_tenant_owner_or_admin  # noqa: E402
from services import seat_limits  # noqa: E402


# ---------- fake mongo (same pattern as test_seat_limits.py) ----------

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

    def aggregate(self, pipeline):
        return FakeCursor([])


class FakeDB:
    def __init__(self):
        self.tenants = FakeCollection()
        self.users = FakeCollection()
        self.invitations = FakeCollection()
        self.plans = FakeCollection()
        self.user_sessions = FakeCollection()
        self.usage_events = FakeCollection()


# ---------- helpers ----------

def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def seed_tenant(fake_db, tenant_id="t1"):
    fake_db.tenants.docs.append({
        "id": tenant_id, "name": f"Shop {tenant_id}", "slug": tenant_id,
        "plan_id": "unlimited", "subscription_status": "active",
    })


def seed_users(fake_db, tenant_id, count):
    for i in range(count):
        fake_db.users.docs.append({
            "id": f"u-{tenant_id}-{i}", "email": f"u{i}@{tenant_id}.example",
            "tenant_id": tenant_id, "role": "owner" if i == 0 else "staff",
            "name": f"User {i}",
        })


def seed_invite(fake_db, tenant_id="t1", email="new@example.com", token="tok-u"):
    fake_db.invitations.docs.append({
        "id": f"inv-{token}", "tenant_id": tenant_id, "email": email,
        "role": "staff", "token": token, "invited_by": "u-t1-0",
        "status": "pending", "created_at": "2026-10-17T00:00:00+00:00",
    })


def seed_unlimited_plan_doc(fake_db):
    """Seed the plans doc exactly as seed_plans() would from DEFAULT_PLANS."""
    doc = next(p for p in DEFAULT_PLANS if p["key"] == "unlimited")
    fake_db.plans.docs.append({"key": "unlimited", "limits": dict(doc["limits"])})


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


@pytest.fixture
def google_on(monkeypatch):
    monkeypatch.setattr(inv_mod, "google_configured", lambda: True)

    async def fake_identity(code="", id_token="", redirect_uri=""):
        return {"email": "invited@example.com", "name": "Invited Person",
                "picture": "", "session_token": "sess-1"}

    monkeypatch.setattr(inv_mod, "resolve_google_identity", fake_identity)


# ---------- seat resolution: both paths uncapped ----------

def test_unlimited_plan_limits_entry_is_explicitly_uncapped():
    # The fallback entry itself is the contract: Empire's seat cap must NOT
    # be inherited by the tier above it.
    assert PLAN_LIMITS["unlimited"]["users"] is None
    assert PLAN_LIMITS["empire"]["users"] == 25


def test_unlimited_limit_none_via_fallback_no_plan_doc(fake_db):
    seed_tenant(fake_db)
    assert run(seat_limits.get_user_limit(fake_db, "t1")) is None


def test_unlimited_limit_none_via_seeded_plan_doc(fake_db):
    seed_tenant(fake_db)
    seed_unlimited_plan_doc(fake_db)
    assert fake_db.plans.docs[0]["limits"]["users"] is None  # stored null
    assert run(seat_limits.get_user_limit(fake_db, "t1")) is None


def test_unlimited_assert_seat_available_never_raises(fake_db):
    # 40 seats in use + 5 pending — far beyond Empire's cap of 25.
    seed_tenant(fake_db)
    seed_users(fake_db, "t1", 40)
    for i in range(5):
        seed_invite(fake_db, email=f"p{i}@example.com", token=f"tok-p{i}")
    run(seat_limits.assert_seat_available(fake_db, "t1",
                                          include_pending_invites=True))
    run(seat_limits.assert_seat_available(fake_db, "t1",
                                          include_pending_invites=False))


# ---------- enforcement points over HTTP ----------

def test_unlimited_invite_create_allowed_beyond_any_cap(client, fake_db):
    seed_tenant(fake_db)
    seed_users(fake_db, "t1", 40)
    r = client.post("/tenants/invitations", json={"email": "a@b.co", "role": "staff"})
    assert r.status_code == 200
    assert len(fake_db.invitations.docs) == 1


def test_unlimited_invite_create_allowed_with_seeded_plan_doc(client, fake_db):
    seed_tenant(fake_db)
    seed_unlimited_plan_doc(fake_db)
    seed_users(fake_db, "t1", 40)
    r = client.post("/tenants/invitations", json={"email": "a@b.co", "role": "staff"})
    assert r.status_code == 200
    assert len(fake_db.invitations.docs) == 1


def test_unlimited_invite_accept_allowed_beyond_any_cap(client, fake_db):
    seed_tenant(fake_db)
    seed_users(fake_db, "t1", 40)
    seed_invite(fake_db, token="tok-u1")
    r = client.post("/public/invitations/accept",
                    json={"token": "tok-u1", "password": "long-enough-1",
                          "name": "Forty First"})
    assert r.status_code == 200
    assert len(fake_db.users.docs) == 41
    assert fake_db.invitations.docs[0]["status"] == "accepted"


def test_unlimited_invite_accept_google_allowed(client, fake_db, google_on):
    seed_tenant(fake_db)
    seed_users(fake_db, "t1", 40)
    seed_invite(fake_db, email="invited@example.com", token="tok-ug")
    r = client.post("/public/invitations/accept-google",
                    json={"token": "tok-ug", "id_token": "x"})
    assert r.status_code == 200
    assert len(fake_db.users.docs) == 41


# ---------- usage metering + rendering with a None users limit ----------

def test_unlimited_usage_resolution_fallback(monkeypatch, fake_db):
    # No plan doc: metering must resolve the in-code unlimited limits —
    # ai_minutes 3,000 and users None — not the trial bookmark.
    seed_tenant(fake_db)
    monkeypatch.setattr(usage_mod, "get_db", lambda: fake_db)
    key, limits = run(usage_mod._get_plan_limits("t1"))
    assert key == "unlimited"
    assert limits["ai_minutes"] == 3000
    assert limits["users"] is None
    assert run(usage_mod.usage_capped("t1", "users")) is False


def test_unlimited_my_usage_renders_none_limit(monkeypatch, fake_db):
    # Seeded plan doc path: my_usage iterates every limit including
    # users=None; it must render (limit None, exhausted False) instead of
    # comparing a number against None. Usage.jsx renders the null as ∞.
    seed_tenant(fake_db)
    seed_unlimited_plan_doc(fake_db)
    monkeypatch.setattr(usage_mod, "get_db", lambda: fake_db)
    out = run(usage_mod.my_usage({"tenant_id": "t1"}))
    assert out["plan"] == "unlimited"
    users_metric = next(m for m in out["metrics"] if m["metric"] == "users")
    assert users_metric["limit"] is None
    assert users_metric["exhausted"] is False
    assert users_metric["pct"] == 0
