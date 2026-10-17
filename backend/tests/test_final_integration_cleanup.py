"""Final-integration cleanup tests (2026-10-17):

1. Plan truth-in-advertising: Pro copy must match its finite 25-user limit,
   and DEFAULT_PLANS prices must equal the locked prices
   (Lite 999 / Starter 3999 / Pro 15999 / Empire 36999 cents).
2. Integration status honesty: saving a complete config must NEVER yield
   "connected" by itself — only a successful live verification may.

Headless: fake in-memory Mongo + TestClient, verification layer stubbed.
"""
from __future__ import annotations

import os
import types

import pytest

os.environ.setdefault("JWT_SECRET", "final-integration-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "final_integration_test")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.integrations as integ_mod  # noqa: E402
from routers.plans import DEFAULT_PLANS  # noqa: E402
from security import require_tenant_owner_or_admin  # noqa: E402


# ================= 1. Plans =================

LOCKED_PRICES = {"lite": 999, "starter": 3999, "pro": 15999, "empire": 36999}


class TestPlanTruthInAdvertising:
    def test_default_plan_prices_are_the_locked_prices(self):
        by_key = {p["key"]: p for p in DEFAULT_PLANS}
        for key, cents in LOCKED_PRICES.items():
            assert by_key[key]["price_cents"] == cents, (
                f"{key} price drifted: {by_key[key]['price_cents']} != locked {cents}")

    def test_no_plan_with_finite_user_limit_claims_unlimited_users(self):
        for plan in DEFAULT_PLANS:
            users = (plan.get("limits") or {}).get("users")
            if users is None:
                continue  # no stated cap (e.g. custom enterprise) — nothing to contradict
            copy = " ".join([plan.get("description") or ""] + list(plan.get("features") or []))
            assert "unlimited users" not in copy.lower(), (
                f"plan {plan['key']} advertises unlimited users but limits.users={users}")

    def test_pro_copy_states_the_real_25_user_cap(self):
        pro = next(p for p in DEFAULT_PLANS if p["key"] == "pro")
        assert pro["limits"]["users"] == 25
        assert "Up to 25 users" in pro["description"]
        assert "Up to 25 users" in pro["features"]


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
        return types.SimpleNamespace(matched_count=matched, modified_count=matched)

    async def delete_one(self, query):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _match(d, query)]
        return types.SimpleNamespace(deleted_count=before - len(self.docs))


class FakeDB:
    def __init__(self):
        self.tenant_integrations = FakeCollection()


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(integ_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(integ_mod.router)

    def fake_owner():
        if current_user.get("role") not in {"owner", "admin"}:
            raise HTTPException(403, "Owner/admin role required")
        return current_user

    app.dependency_overrides[require_tenant_owner_or_admin] = fake_owner
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


TWILIO_FULL = {"account_sid": "AC_test", "auth_token": "tok", "phone_number": "+15555550000"}
GMAIL_FULL = {"email": "shop@example.com", "client_id": "cid", "client_secret": "sec"}


def _stub_verifier(monkeypatch, result, calls=None):
    async def fake_verify(key, config):
        if calls is not None:
            calls.append(key)
        return result
    monkeypatch.setattr(integ_mod, "verify_integration", fake_verify)


# ================= 2. Integration status honesty =================

class TestIntegrationStatusHonesty:
    def test_complete_config_without_any_live_check_is_configured_not_connected(
            self, client, monkeypatch):
        # Gmail has no live check for its saved fields: even a perfect config
        # must land on "configured", never "connected".
        as_user("t1")
        r = client.post("/tenants/integrations", json={
            "key": "gmail", "enabled": True, "config": GMAIL_FULL})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["integration"]["status"] == "configured"
        assert body["integration"]["status"] != "connected"
        assert body["verification"]["attempted"] is False

    def test_successful_live_verification_earns_connected(self, client, monkeypatch):
        as_user("t1")
        _stub_verifier(monkeypatch, (True, "Twilio account verified (AC_test)."))
        r = client.post("/tenants/integrations", json={
            "key": "twilio", "enabled": True, "config": TWILIO_FULL})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["integration"]["status"] == "connected"
        assert body["integration"]["last_checked_at"]
        assert body["verification"] == {
            "attempted": True, "ok": True, "detail": "Twilio account verified (AC_test)."}

    def test_failed_live_verification_is_error_with_useful_message(self, client, monkeypatch):
        as_user("t1")
        _stub_verifier(monkeypatch, (False, "Twilio rejected these credentials (HTTP 401) — "
                                            "check the account SID and auth token, then reconnect."))
        r = client.post("/tenants/integrations", json={
            "key": "twilio", "enabled": True, "config": TWILIO_FULL})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["integration"]["status"] == "error"
        assert "401" in body["integration"]["status_detail"]
        assert body["integration"]["last_checked_at"]
        assert body["verification"]["ok"] is False

    def test_missing_fields_stay_disconnected_and_skip_verification(self, client, monkeypatch):
        as_user("t1")
        calls = []
        _stub_verifier(monkeypatch, (True, "should never run"), calls)
        r = client.post("/tenants/integrations", json={
            "key": "twilio", "enabled": True, "config": {"account_sid": "AC_test"}})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["integration"]["status"] == "disconnected"
        assert "auth_token" in body["missing"] and "phone_number" in body["missing"]
        assert calls == [], "verification must not run on an incomplete config"

    def test_disabled_config_is_disconnected_even_when_complete(self, client, monkeypatch):
        as_user("t1")
        _stub_verifier(monkeypatch, (True, "ok"))
        r = client.post("/tenants/integrations", json={
            "key": "twilio", "enabled": False, "config": TWILIO_FULL})
        assert r.json()["integration"]["status"] == "disconnected"

    def test_verify_endpoint_is_the_reconnect_path(self, client, monkeypatch):
        as_user("t1")
        _stub_verifier(monkeypatch, (False, "Twilio rejected these credentials (HTTP 401)."))
        r = client.post("/tenants/integrations", json={
            "key": "twilio", "enabled": True, "config": TWILIO_FULL})
        assert r.json()["integration"]["status"] == "error"
        # Owner fixes the token in Twilio, then re-checks WITHOUT resaving:
        _stub_verifier(monkeypatch, (True, "Twilio account verified (AC_test)."))
        r2 = client.post("/tenants/integrations/twilio/verify")
        assert r2.status_code == 200, r2.text
        assert r2.json()["integration"]["status"] == "connected"

    def test_verify_endpoint_404_when_nothing_saved(self, client):
        as_user("t1")
        r = client.post("/tenants/integrations/twilio/verify")
        assert r.status_code == 404

    def test_integrations_are_tenant_isolated(self, client, monkeypatch):
        _stub_verifier(monkeypatch, (True, "ok"))
        as_user("tenant-a")
        client.post("/tenants/integrations", json={
            "key": "twilio", "enabled": True, "config": TWILIO_FULL})
        as_user("tenant-b")
        r = client.get("/tenants/integrations")
        assert r.status_code == 200
        assert r.json() == [], "tenant B must not see tenant A's integrations"
        r2 = client.post("/tenants/integrations/twilio/verify")
        assert r2.status_code == 404, "tenant B must not verify tenant A's config"
