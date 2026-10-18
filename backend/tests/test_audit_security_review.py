"""Track D security-audit regression tests (2026-10-17).

Adversarial coverage for the audit fixes, in the same headless style as
test_security_hardening.py (fake in-memory Mongo + TestClient):

- Tenant isolation: cross-tenant CRM estimate/invoice mutations -> 404.
- RBAC: staff cannot reach owner/admin surfaces; tenant owner cannot reach
  platform-admin surfaces.
- Password reset: strength policy for ALL users, forgot-password rate limit,
  reset burns sibling tokens + Google sessions, no token in logs.
- Disabled accounts: login / refresh / live session all cut off.
- Staff offboarding endpoint: guards (self, owner-by-admin, cross-tenant).
- Integration secrets: encrypted at rest, masked in responses, plaintext
  only inside the live verification call; fail closed without a key.
- Log hygiene: invite / review tokens never printed.
- Portal magic link: never printed in strict (production/staging) envs.
- Marketing unsubscribe signing: fails closed without JWT_SECRET.
- Connect refunds/disputes: resolved via the recorded payment (any payment,
  not just the latest), tenant-account validated, cumulative refunds never
  double-subtracted.
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "audit-test-secret")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "audit_test")
os.environ.pop("REDIS_URL", None)
os.environ.pop("BOOKKEEPING_LINK_ENCRYPTION_KEY", None)

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("TWO_FACTOR_ENCRYPTION_KEY", Fernet.generate_key().decode())

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import rate_limit  # noqa: E402
import security as security_mod  # noqa: E402
import routers.auth as auth_mod  # noqa: E402
import routers.call_to_payment as c2p_mod  # noqa: E402
import routers.crm as crm_mod  # noqa: E402
import routers.integrations as integ_mod  # noqa: E402
import routers.invitations as inv_mod  # noqa: E402
import routers.plans as plans_mod  # noqa: E402
import routers.portal as portal_mod  # noqa: E402
import routers.reviews as reviews_mod  # noqa: E402
import services.marketing_automation as mkt  # noqa: E402
from security import create_refresh_token, get_current_user, hash_password  # noqa: E402


# ---------- fake mongo (extended: sort / count / update_many / delete_many) ----------

class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        self.docs.sort(key=lambda d: str(d.get(key) or ""), reverse=(direction < 0))
        return self

    async def to_list(self, n):
        return [dict(d) for d in self.docs[:n]]


def _match(doc, query):
    for k, cond in (query or {}).items():
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

    def find(self, query=None, proj=None):
        return FakeCursor([d for d in self.docs if _match(d, query or {})])

    async def find_one(self, query, proj=None):
        for d in self.docs:
            if _match(d, query):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("id") or doc.get("_id"))

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

    async def update_many(self, query, update):
        matched = 0
        for d in self.docs:
            if _match(d, query):
                matched += 1
                for k, v in update.get("$set", {}).items():
                    d[k] = v
        return SimpleNamespace(matched_count=matched, modified_count=matched)

    async def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if _match(d, query):
                del self.docs[i]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def delete_many(self, query):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _match(d, query)]
        return SimpleNamespace(deleted_count=before - len(self.docs))

    async def count_documents(self, query):
        return sum(1 for d in self.docs if _match(d, query))


class FakeDB:
    def __init__(self):
        self.users = FakeCollection()
        self.tenants = FakeCollection()
        self.invitations = FakeCollection()
        self.estimates = FakeCollection()
        self.invoices = FakeCollection()
        self.invoice_payments = FakeCollection()
        self.webhook_events = FakeCollection()
        self.password_reset_tokens = FakeCollection()
        self.user_sessions = FakeCollection()
        self.portal_tokens = FakeCollection()
        self.customers = FakeCollection()
        self.review_requests = FakeCollection()
        self.tenant_integrations = FakeCollection()
        self.plans = FakeCollection()
        self.cost_config = FakeCollection()


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------- fixtures ----------

current_user = {}


def as_user(uid="u-owner", tenant_id="t1", role="owner", email="owner@example.com"):
    current_user.clear()
    current_user.update({"id": uid, "tenant_id": tenant_id, "role": role, "email": email})


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def api(monkeypatch, fake_db):
    """App with auth dependency overridden (RBAC / tenant-isolation tests)."""
    for mod in (auth_mod, crm_mod, inv_mod, integ_mod, plans_mod, portal_mod, reviews_mod):
        monkeypatch.setattr(mod, "get_db", lambda: fake_db)
    monkeypatch.setattr(security_mod, "get_db", lambda: fake_db)
    rate_limit.reset_rate_limiter_singleton()
    app = FastAPI()
    for mod in (auth_mod, crm_mod, inv_mod, integ_mod, plans_mod, portal_mod, reviews_mod):
        app.include_router(mod.router)
    app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app)


@pytest.fixture
def auth_client(monkeypatch, fake_db):
    """App with the REAL auth chain (login / refresh / disabled tests)."""
    monkeypatch.setattr(auth_mod, "get_db", lambda: fake_db)
    monkeypatch.setattr(security_mod, "get_db", lambda: fake_db)
    rate_limit.reset_rate_limiter_singleton()
    app = FastAPI()
    app.include_router(auth_mod.router)
    # https base: auth cookies are Secure, so httpx would not resend them
    # over the default http://testserver base.
    return TestClient(app, base_url="https://testserver")


async def _seed_user(db, uid="u1", email="user@example.com", password="Correct1!x",
                     role="owner", tenant_id="t1", **extra):
    doc = {"id": uid, "email": email, "password_hash": hash_password(password),
           "name": "Test User", "role": role, "tenant_id": tenant_id}
    doc.update(extra)
    await db.users.insert_one(doc)
    return doc


# ================= TENANT ISOLATION (CRM) =================

EST_BODY = {"customer_name": "Victim", "title": "Job", "lines": []}


def _seed_crm(db):
    run(db.estimates.insert_one({"id": "est-a", "tenant_id": "t1", "customer_name": "A",
                                 "title": "A job", "lines": [], "total": 0, "status": "draft"}))
    run(db.invoices.insert_one({"id": "inv-a", "tenant_id": "t1", "customer_name": "A",
                                "title": "A inv", "lines": [], "total": 100, "status": "sent"}))


def test_cross_tenant_estimate_mutations_404(api, fake_db):
    _seed_crm(fake_db)
    as_user(uid="u-b", tenant_id="t2", role="owner")
    assert api.put("/tenants/estimates/est-a", json=EST_BODY).status_code == 404
    assert api.post("/tenants/estimates/est-a/send").status_code == 404
    assert api.delete("/tenants/estimates/est-a").status_code == 404
    doc = run(fake_db.estimates.find_one({"id": "est-a"}))
    assert doc["status"] == "draft" and doc["tenant_id"] == "t1"


def test_cross_tenant_invoice_mutations_404(api, fake_db):
    _seed_crm(fake_db)
    as_user(uid="u-b", tenant_id="t2", role="owner")
    assert api.post("/tenants/invoices/inv-a/mark-paid").status_code == 404
    assert api.delete("/tenants/invoices/inv-a").status_code == 404
    doc = run(fake_db.invoices.find_one({"id": "inv-a"}))
    assert doc is not None and doc["status"] == "sent"


def test_cross_tenant_lists_do_not_leak(api, fake_db):
    _seed_crm(fake_db)
    as_user(uid="u-b", tenant_id="t2", role="owner")
    assert api.get("/tenants/estimates").json() == []
    assert api.get("/tenants/invoices").json() == []


def test_same_tenant_estimate_update_still_works(api, fake_db):
    _seed_crm(fake_db)
    as_user(uid="u-a", tenant_id="t1", role="owner")
    r = api.put("/tenants/estimates/est-a", json=EST_BODY)
    assert r.status_code == 200


# ================= RBAC =================

def test_staff_blocked_from_owner_admin_surfaces(api, fake_db):
    as_user(uid="u-staff", tenant_id="t1", role="staff")
    assert api.post("/tenants/invitations", json={"email": "x@example.com", "role": "staff"}).status_code == 403
    assert api.get("/tenants/invitations/staff").status_code == 403
    assert api.put("/tenants/invitations/staff/u-staff/role?role=admin").status_code == 403
    assert api.post("/tenants/integrations", json={"key": "twilio", "enabled": True, "config": {}}).status_code == 403


def test_tenant_owner_blocked_from_platform_admin(api, fake_db):
    as_user(uid="u-a", tenant_id="t1", role="owner")
    assert api.get("/admin/plans").status_code == 403


def test_platform_admin_can_list_plans(api, fake_db):
    as_user(uid="pa", tenant_id=None, role="platform_admin")
    assert api.get("/admin/plans").status_code == 200


def test_invite_role_cannot_be_platform_admin(api, fake_db):
    as_user()
    r = api.post("/tenants/invitations", json={"email": "x@example.com", "role": "platform_admin"})
    assert r.status_code == 422  # Literal["owner","admin","staff"] rejects it


# ================= PASSWORD RESET =================

STRONG = "Str0ng!Passw0rd"


def _seed_reset(db, token="tok-1", user_id="u1"):
    from datetime import datetime, timedelta, timezone
    run(db.password_reset_tokens.insert_one({
        "token": token, "user_id": user_id, "used": False,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
    }))


def test_reset_weak_password_rejected_for_regular_user(auth_client, fake_db):
    run(_seed_user(fake_db))
    _seed_reset(fake_db)
    # 8 chars passes the model's length floor but not the strength policy
    # (no upper/digit/symbol, all-same-char): pre-fix this returned 200 for
    # any non-admin, non-must-change account.
    r = auth_client.post("/auth/reset-password", json={"token": "tok-1", "new_password": "aaaaaaaa"})
    assert r.status_code == 400
    user = run(fake_db.users.find_one({"id": "u1"}))
    from security import verify_password
    assert verify_password("Correct1!x", user["password_hash"])  # unchanged


def test_reset_known_default_password_rejected(auth_client, fake_db):
    from password_policy import KNOWN_DEFAULT_PASSWORDS
    run(_seed_user(fake_db))
    _seed_reset(fake_db)
    r = auth_client.post("/auth/reset-password",
                         json={"token": "tok-1", "new_password": KNOWN_DEFAULT_PASSWORDS[0]})
    assert r.status_code == 400


def test_reset_success_burns_sibling_tokens_and_google_sessions(auth_client, fake_db):
    run(_seed_user(fake_db))
    _seed_reset(fake_db, token="tok-1")
    _seed_reset(fake_db, token="tok-2")
    run(fake_db.user_sessions.insert_one({"id": "s1", "user_id": "u1", "session_token": "gsess"}))
    r = auth_client.post("/auth/reset-password", json={"token": "tok-1", "new_password": STRONG})
    assert r.status_code == 200, r.text
    sibling = run(fake_db.password_reset_tokens.find_one({"token": "tok-2"}))
    assert sibling is None  # burned by the successful reset
    assert run(fake_db.user_sessions.count_documents({"user_id": "u1"})) == 0
    # The burned sibling can no longer be used.
    r2 = auth_client.post("/auth/reset-password", json={"token": "tok-2", "new_password": STRONG})
    assert r2.status_code == 400


def test_forgot_password_rate_limited(auth_client, fake_db):
    run(_seed_user(fake_db))
    codes = [auth_client.post("/auth/forgot-password", json={"email": "user@example.com"}).status_code
             for _ in range(6)]
    assert codes[:5] == [200] * 5
    assert codes[5] == 429


def test_forgot_password_never_logs_token(auth_client, fake_db, capsys):
    run(_seed_user(fake_db))
    r = auth_client.post("/auth/forgot-password", json={"email": "user@example.com"})
    assert r.status_code == 200
    rec = run(fake_db.password_reset_tokens.find_one({"user_id": "u1"}))
    assert rec and rec["token"]
    out = capsys.readouterr().out
    assert "[EMAIL skipped]" in out  # capture is live (the send path printed)
    assert rec["token"] not in out


# ================= DISABLED ACCOUNTS =================

def test_disabled_user_locked_out_everywhere(auth_client, fake_db):
    run(_seed_user(fake_db))
    r = auth_client.post("/auth/login", json={"email": "user@example.com", "password": "Correct1!x"})
    assert r.status_code == 200, r.text
    assert auth_client.get("/auth/me").status_code == 200
    # Owner disables the account -> live session, refresh and login all die.
    run(fake_db.users.update_one({"id": "u1"}, {"$set": {"disabled": True}}))
    assert auth_client.get("/auth/me").status_code == 403
    assert auth_client.post("/auth/refresh").status_code == 403
    r = auth_client.post("/auth/login", json={"email": "user@example.com", "password": "Correct1!x"})
    assert r.status_code == 403


def test_refresh_token_for_disabled_user_rejected(auth_client, fake_db):
    run(_seed_user(fake_db, disabled=True))
    auth_client.cookies.set("refresh_token", create_refresh_token("u1"))
    assert auth_client.post("/auth/refresh").status_code == 403


# ================= STAFF OFFBOARDING ENDPOINT =================

def _seed_staff(db):
    run(_seed_user(db, uid="u-owner", email="o@example.com", role="owner"))
    run(_seed_user(db, uid="u-admin", email="a@example.com", role="admin"))
    run(_seed_user(db, uid="u-staff", email="s@example.com", role="staff"))
    run(_seed_user(db, uid="u-other", email="x@example.com", role="staff", tenant_id="t2"))


def test_owner_can_disable_and_reenable_staff(api, fake_db):
    _seed_staff(fake_db)
    as_user(uid="u-owner", role="owner")
    r = api.put("/tenants/invitations/staff/u-staff/status", json={"disabled": True})
    assert r.status_code == 200, r.text
    assert run(fake_db.users.find_one({"id": "u-staff"}))["disabled"] is True
    r = api.put("/tenants/invitations/staff/u-staff/status", json={"disabled": False})
    assert r.status_code == 200
    assert run(fake_db.users.find_one({"id": "u-staff"}))["disabled"] is False


def test_cannot_disable_self(api, fake_db):
    _seed_staff(fake_db)
    as_user(uid="u-owner", role="owner")
    r = api.put("/tenants/invitations/staff/u-owner/status", json={"disabled": True})
    assert r.status_code == 400


def test_admin_cannot_disable_owner(api, fake_db):
    _seed_staff(fake_db)
    as_user(uid="u-admin", role="admin")
    r = api.put("/tenants/invitations/staff/u-owner/status", json={"disabled": True})
    assert r.status_code == 403


def test_disable_cross_tenant_target_404(api, fake_db):
    _seed_staff(fake_db)
    as_user(uid="u-owner", role="owner")
    r = api.put("/tenants/invitations/staff/u-other/status", json={"disabled": True})
    assert r.status_code == 404
    assert not run(fake_db.users.find_one({"id": "u-other"})).get("disabled")


# ================= LOG HYGIENE =================

def test_invite_token_never_logged(api, fake_db, capsys):
    run(fake_db.tenants.insert_one({"id": "t1", "name": "Shop"}))
    as_user()
    r = api.post("/tenants/invitations", json={"email": "new@example.com", "role": "staff"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    out = capsys.readouterr().out
    assert "[INVITE]" in out  # capture is live
    assert token and token not in out


def test_review_token_never_logged(api, fake_db, monkeypatch, capsys):
    run(fake_db.tenants.insert_one({"id": "t1", "name": "Shop"}))

    async def fake_sms(**kw):
        return {"status": "queued"}

    async def fake_usage(*a, **k):
        return None

    monkeypatch.setattr(reviews_mod, "send_sms", fake_sms)
    monkeypatch.setattr(reviews_mod, "record_usage", fake_usage)
    as_user()
    r = api.post("/tenants/reviews", json={
        "customer_name": "Cust", "customer_phone": "+15555550100", "channel": "sms"})
    assert r.status_code == 200, r.text
    token = r.json()["public_token"]
    out = capsys.readouterr().out
    assert "[REVIEW-REQ" in out  # capture is live
    assert token and token not in out


def test_portal_magic_link_not_logged_in_strict_env(api, fake_db, monkeypatch, capsys):
    run(fake_db.tenants.insert_one({"id": "t1", "slug": "shop", "status": "active", "name": "Shop"}))
    run(fake_db.customers.insert_one({"id": "c1", "tenant_id": "t1", "phone": "+15555550100", "name": "Cust"}))
    monkeypatch.setenv("APP_ENV", "production")
    r = api.post("/portal/shop/magic-link", json={"phone": "+15555550100"})
    assert r.status_code == 200, r.text
    rec = run(fake_db.portal_tokens.find_one({"tenant_id": "t1"}))
    assert rec and rec["token"]
    out = capsys.readouterr().out
    assert "[PORTAL MAGIC LINK] issued" in out  # capture is live
    assert rec["token"] not in out


# ================= INTEGRATION SECRETS =================

TWILIO = {"account_sid": "AC_test", "auth_token": "tok-secret-123", "phone_number": "+15555550000"}


def _stub_verify(monkeypatch, seen):
    async def fake_verify(key, config):
        seen.update(config)
        return True, "verified"
    monkeypatch.setattr(integ_mod, "verify_integration", fake_verify)


def test_integration_secret_encrypted_at_rest_and_masked(api, fake_db, monkeypatch):
    seen = {}
    _stub_verify(monkeypatch, seen)
    as_user()
    r = api.post("/tenants/integrations", json={"key": "twilio", "enabled": True, "config": TWILIO})
    assert r.status_code == 200, r.text
    stored = run(fake_db.tenant_integrations.find_one({"key": "twilio"}))
    assert stored["config"]["auth_token"] != "tok-secret-123"
    assert stored["config"]["auth_token"].startswith("enc:v1:")
    assert "tok-secret-123" not in str(stored["config"])
    # Verification saw the decrypted plaintext; the API never returns it.
    assert seen["auth_token"] == "tok-secret-123"
    assert r.json()["integration"]["config"]["auth_token"] == integ_mod.MASK
    rows = api.get("/tenants/integrations").json()
    assert rows[0]["config"]["auth_token"] == integ_mod.MASK


def test_integration_mask_roundtrip_keeps_secret(api, fake_db, monkeypatch):
    seen = {}
    _stub_verify(monkeypatch, seen)
    as_user()
    api.post("/tenants/integrations", json={"key": "twilio", "enabled": True, "config": TWILIO})
    # UI re-saves with the masked value it was shown -> secret preserved.
    r = api.post("/tenants/integrations", json={
        "key": "twilio", "enabled": True,
        "config": {**TWILIO, "auth_token": integ_mod.MASK}})
    assert r.status_code == 200, r.text
    assert r.json()["missing"] == []
    assert seen["auth_token"] == "tok-secret-123"
    stored = run(fake_db.tenant_integrations.find_one({"key": "twilio"}))
    assert stored["config"]["auth_token"].startswith("enc:v1:")


def test_integration_verify_uses_decrypted_secret(api, fake_db, monkeypatch):
    seen = {}
    _stub_verify(monkeypatch, seen)
    as_user()
    api.post("/tenants/integrations", json={"key": "twilio", "enabled": True, "config": TWILIO})
    seen.clear()
    r = api.post("/tenants/integrations/twilio/verify")
    assert r.status_code == 200, r.text
    assert seen["auth_token"] == "tok-secret-123"
    assert r.json()["integration"]["config"]["auth_token"] == integ_mod.MASK


def test_integration_save_fails_closed_without_encryption_key(api, fake_db, monkeypatch):
    _stub_verify(monkeypatch, {})
    monkeypatch.delenv("TWO_FACTOR_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("BOOKKEEPING_LINK_ENCRYPTION_KEY", raising=False)
    as_user()
    r = api.post("/tenants/integrations", json={"key": "twilio", "enabled": True, "config": TWILIO})
    assert r.status_code == 503
    assert run(fake_db.tenant_integrations.find_one({"key": "twilio"})) is None


# ================= MARKETING SIGNING KEY =================

def test_marketing_signing_fails_closed_without_jwt_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        mkt.make_unsubscribe_token(campaign_id="c1", contact="a@example.com")


def test_marketing_unsubscribe_token_roundtrip():
    tok = mkt.make_unsubscribe_token(campaign_id="c1", contact="a@example.com")
    payload = mkt.verify_unsubscribe_token(tok)
    assert payload and payload["campaign_id"] == "c1"


# ================= CONNECT REFUNDS / DISPUTES =================

def _seed_connect(db):
    run(db.tenants.insert_one({"id": "t1", "stripe_connect_id": "acct_A"}))
    run(db.invoices.insert_one({
        "id": "inv1", "tenant_id": "t1", "total_cents": 10000,
        "amount_paid_cents": 10000, "status": "paid",
        "last_payment_intent_id": "pi_2",
    }))
    run(db.invoice_payments.insert_one({
        "id": "ip1", "tenant_id": "t1", "invoice_id": "inv1",
        "stripe_payment_intent_id": "pi_1", "connected_account": "acct_A",
        "amount_cents": 6000, "status": "succeeded",
    }))
    run(db.invoice_payments.insert_one({
        "id": "ip2", "tenant_id": "t1", "invoice_id": "inv1",
        "stripe_payment_intent_id": "pi_2", "connected_account": "acct_A",
        "amount_cents": 4000, "status": "succeeded",
    }))


def test_refund_on_non_latest_payment_applies(fake_db):
    _seed_connect(fake_db)
    # pi_1 is NOT the invoice's last_payment_intent_id (pi_2 is): the old
    # matcher silently dropped this refund.
    run(c2p_mod._apply_refund(fake_db, {"payment_intent": "pi_1", "amount_refunded": 6000}, "acct_A"))
    inv = run(fake_db.invoices.find_one({"id": "inv1"}))
    assert inv["amount_paid_cents"] == 4000


def test_refund_replay_never_double_subtracts(fake_db):
    _seed_connect(fake_db)
    charge = {"payment_intent": "pi_1", "amount_refunded": 6000}
    run(c2p_mod._apply_refund(fake_db, charge, "acct_A"))
    run(c2p_mod._apply_refund(fake_db, charge, "acct_A"))  # duplicate delivery, new event id
    inv = run(fake_db.invoices.find_one({"id": "inv1"}))
    assert inv["amount_paid_cents"] == 4000


def test_refund_partial_then_more_uses_cumulative_delta(fake_db):
    _seed_connect(fake_db)
    run(c2p_mod._apply_refund(fake_db, {"payment_intent": "pi_2", "amount_refunded": 1500}, "acct_A"))
    run(c2p_mod._apply_refund(fake_db, {"payment_intent": "pi_2", "amount_refunded": 4000}, "acct_A"))
    inv = run(fake_db.invoices.find_one({"id": "inv1"}))
    assert inv["amount_paid_cents"] == 6000  # only 4000 total refunded on pi_2


def test_refund_from_wrong_connected_account_rejected(fake_db):
    _seed_connect(fake_db)
    with pytest.raises(c2p_mod.ConnectEventMismatch):
        run(c2p_mod._apply_refund(fake_db, {"payment_intent": "pi_1", "amount_refunded": 6000}, "acct_B"))
    inv = run(fake_db.invoices.find_one({"id": "inv1"}))
    assert inv["amount_paid_cents"] == 10000


def test_dispute_resolved_via_payment_record_and_account_checked(fake_db):
    _seed_connect(fake_db)
    run(c2p_mod._apply_dispute(fake_db, {"id": "dp_1", "payment_intent": "pi_1"}, "acct_A"))
    inv = run(fake_db.invoices.find_one({"id": "inv1"}))
    assert inv["dispute_id"] == "dp_1"
    with pytest.raises(c2p_mod.ConnectEventMismatch):
        run(c2p_mod._apply_dispute(fake_db, {"id": "dp_2", "payment_intent": "pi_1"}, "acct_B"))
