"""Security RE-PROBE tests (2026-10-17) — pristine main bee3e86e.

Resolves three probe names observed mid-run during the Track D audit that
exist in no delivered artifact:

1. test_reviews_list_and_stats_are_tenant_scoped
2. test_billing_overage_endpoints_are_admin_gated
3. test_twilio_webhook_rejects_unsigned_requests_in_production

Same headless style as test_audit_security_review.py (PR #101): fake
in-memory Mongo + TestClient, auth dependency overridden per-test.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "reprobe-test-secret")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "reprobe_test")
os.environ.pop("REDIS_URL", None)
# Probe-3/2 env starts from a controlled slate; tests set what they need.
for _v in ("TWILIO_AUTH_TOKEN", "PUBLIC_BACKEND_URL", "APP_ENV", "WEBHOOK_CRON_SECRET"):
    os.environ.pop(_v, None)

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("TWO_FACTOR_ENCRYPTION_KEY", Fernet.generate_key().decode())

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import rate_limit  # noqa: E402
import security as security_mod  # noqa: E402
import routers.advisor as advisor_mod  # noqa: E402
import routers.cron as cron_mod  # noqa: E402
import routers.overage as overage_mod  # noqa: E402
import routers.reputation as reputation_mod  # noqa: E402
import routers.reviews as reviews_mod  # noqa: E402
import routers.testimonials as testimonials_mod  # noqa: E402
import routers.twilio_webhook as twilio_mod  # noqa: E402
import routers.usage as usage_mod  # noqa: E402
from security import get_current_user  # noqa: E402


# ---------- fake mongo (match ops + group/sum/first aggregate + async cursor) ----------

def _match(doc, query):
    for k, cond in (query or {}).items():
        val = doc.get(k)
        if isinstance(cond, dict):
            for op, opval in cond.items():
                if op == "$in":
                    if val not in opval:
                        return False
                elif op == "$ne":
                    if val == opval:
                        return False
                elif op == "$gte":
                    if val is None or val < opval:
                        return False
                elif op == "$gt":
                    if val is None or val <= opval:
                        return False
                elif op == "$lte":
                    if val is None or val > opval:
                        return False
                elif op == "$lt":
                    if val is None or val >= opval:
                        return False
                elif op == "$exists":
                    if (k in doc) != bool(opval):
                        return False
                else:
                    return False
        elif val != cond:
            return False
    return True


class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        self.docs.sort(key=lambda d: str(d.get(key) or ""), reverse=(direction < 0))
        return self

    async def to_list(self, n):
        return [dict(d) for d in self.docs[:n]]

    def __aiter__(self):
        async def gen():
            for d in self.docs:
                yield dict(d)
        return gen()


class FakeAggregateCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    async def to_list(self, n):
        return [dict(d) for d in self.docs[:n]]


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

    async def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if _match(d, query):
                del self.docs[i]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def count_documents(self, query):
        return sum(1 for d in self.docs if _match(d, query))

    def aggregate(self, pipeline):
        docs = [dict(d) for d in self.docs]
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if _match(d, stage["$match"])]
            elif "$group" in stage:
                spec = stage["$group"]
                key_expr = spec.get("_id")
                key_field = key_expr[1:] if isinstance(key_expr, str) and key_expr.startswith("$") else None
                groups = {}
                order = []
                for d in docs:
                    k = d.get(key_field) if key_field else None
                    if k not in groups:
                        groups[k] = {"_id": k}
                        order.append(k)
                    g = groups[k]
                    for out_field, acc in spec.items():
                        if out_field == "_id":
                            continue
                        for op, expr in acc.items():
                            if op == "$sum":
                                add = d.get(expr[1:], 0) if isinstance(expr, str) and expr.startswith("$") else expr
                                g[out_field] = g.get(out_field, 0) + (add or 0)
                            elif op == "$first":
                                if out_field not in g:
                                    g[out_field] = d.get(expr[1:]) if isinstance(expr, str) and expr.startswith("$") else expr
                docs = [groups[k] for k in order]
            elif "$sort" in stage:
                for field, direction in reversed(list(stage["$sort"].items())):
                    docs.sort(key=lambda d: (d.get(field) is None, d.get(field)), reverse=(direction < 0))
            elif "$limit" in stage:
                docs = docs[: stage["$limit"]]
        return FakeAggregateCursor(docs)


class FakeDB:
    def __init__(self):
        self._colls = {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._colls.setdefault(name, FakeCollection())

    def __getitem__(self, name):  # advisor.py uses db[coll] access
        return self._colls.setdefault(name, FakeCollection())


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------- fixtures ----------

current_user = {}


def as_user(uid="u-owner", tenant_id="t1", role="owner", email="owner@example.com"):
    current_user.clear()
    current_user.update({"id": uid, "tenant_id": tenant_id, "role": role, "email": email})


def as_nobody():
    current_user.clear()


def _current_user_dep():
    if not current_user:
        raise HTTPException(401, "unauthenticated")
    return current_user


@pytest.fixture
def fake_db():
    return FakeDB()


def _patch_db(monkeypatch, fake_db, *mods):
    for mod in mods:
        monkeypatch.setattr(mod, "get_db", lambda: fake_db)


@pytest.fixture
def reviews_api(monkeypatch, fake_db):
    _patch_db(monkeypatch, fake_db, reviews_mod, reputation_mod, advisor_mod,
              testimonials_mod, usage_mod, security_mod)
    rate_limit.reset_rate_limiter_singleton()
    app = FastAPI()
    app.include_router(reviews_mod.router)
    app.include_router(reviews_mod.public_router)
    app.include_router(reputation_mod.router)
    app.include_router(advisor_mod.router)
    app.include_router(testimonials_mod.router)
    app.dependency_overrides[get_current_user] = _current_user_dep
    return TestClient(app)


@pytest.fixture
def billing_api(monkeypatch, fake_db):
    _patch_db(monkeypatch, fake_db, overage_mod, cron_mod, usage_mod, security_mod)
    rate_limit.reset_rate_limiter_singleton()
    app = FastAPI()
    app.include_router(overage_mod.tenant_router)
    app.include_router(overage_mod.admin_router)
    app.include_router(cron_mod.router)
    app.dependency_overrides[get_current_user] = _current_user_dep
    return TestClient(app)


@pytest.fixture
def twilio_api(monkeypatch, fake_db):
    _patch_db(monkeypatch, fake_db, twilio_mod)
    rate_limit.reset_rate_limiter_singleton()
    app = FastAPI()
    app.include_router(twilio_mod.router)
    return TestClient(app)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ================= PROBE 1 — reviews list + stats tenant scoping =================

def _seed_reviews(db):
    recent = _now_iso()
    run(db.tenants.insert_one({"id": "t1", "name": "A Shop", "slug": "a-shop", "branding": {}}))
    run(db.tenants.insert_one({"id": "t2", "name": "B Shop", "slug": "b-shop", "branding": {}}))
    # reviews.py surface: review_requests
    run(db.review_requests.insert_one({
        "id": "rr-a1", "tenant_id": "t1", "customer_name": "Alice A", "status": "responded",
        "rating": 5, "public_token": "tok-a1", "created_at": recent}))
    run(db.review_requests.insert_one({
        "id": "rr-a2", "tenant_id": "t1", "customer_name": "Aaron A", "status": "responded",
        "rating": 4, "public_token": "tok-a2", "created_at": recent}))
    run(db.review_requests.insert_one({
        "id": "rr-b1", "tenant_id": "t2", "customer_name": "Betty B", "status": "responded",
        "rating": 5, "public_token": "tok-b1", "created_at": recent}))
    # reputation surface: ingested reviews (A averages 1.0, B averages 5.0)
    run(db.reviews.insert_one({
        "id": "rev-a1", "tenant_id": "t1", "rating": 1, "author_name": "Alice A",
        "text": "A-secret-review-text", "source": "google", "response_status": "needs_response",
        "created_at": recent}))
    run(db.reviews.insert_one({
        "id": "rev-a2", "tenant_id": "t1", "rating": 1, "author_name": "Aaron A",
        "text": "A-second-review", "source": "yelp", "response_status": "needs_response",
        "created_at": recent}))
    run(db.reviews.insert_one({
        "id": "rev-b1", "tenant_id": "t2", "rating": 5, "author_name": "Betty B",
        "text": "B-review", "source": "google", "response_status": "needs_response",
        "created_at": recent}))
    # testimonials surface
    run(db.testimonials.insert_one({
        "id": "testi-a1", "tenant_id": "t1", "customer_name": "Alice A", "status": "published",
        "original_text": "A testimonial", "created_at": recent}))
    run(db.testimonials.insert_one({
        "id": "testi-b1", "tenant_id": "t2", "customer_name": "Betty B", "status": "published",
        "original_text": "B testimonial", "created_at": recent}))


def test_reviews_list_and_stats_are_tenant_scoped(reviews_api, fake_db):
    """Probe 1 (the observed name): EVERY reviews read surface — the
    review-request list, the reputation review inbox, the reputation
    overview stats, the advisor snapshot stat, and the testimonials list —
    must show tenant B only tenant B's data."""
    _seed_reviews(fake_db)
    as_user(uid="u-b", tenant_id="t2", role="owner")

    # (a) reviews.py list — review_requests for own tenant only
    r = reviews_api.get("/tenants/reviews")
    assert r.status_code == 200, r.text
    ids = [d["id"] for d in r.json()]
    assert ids == ["rr-b1"], ids

    # (b) reputation review inbox — ingested reviews for own tenant only
    r = reviews_api.get("/api/reputation/tenants/reviews")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert [d["id"] for d in body["reviews"]] == ["rev-b1"]
    assert "A-secret-review-text" not in r.text

    # (c) reputation overview — stats computed from own tenant only
    r = reviews_api.get("/api/reputation/tenants/overview")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["review_count"] == 1, body
    assert body["avg_rating"] == 5.0, body  # A's two 1-star reviews must not drag this down
    assert body["review_requests_sent"] == 1, body  # B sent 1 request; A sent 2

    # (d) advisor snapshot — review_responses_30d counts own tenant only
    r = reviews_api.get("/advisor/snapshot")
    assert r.status_code == 200, r.text
    assert r.json()["review_responses_30d"] == 1, r.json()

    # (e) testimonials list — own tenant only
    r = reviews_api.get("/testimonials")
    assert r.status_code == 200, r.text
    assert [d["id"] for d in r.json()] == ["testi-b1"]


def test_reviews_cross_tenant_mutation_404(reviews_api, fake_db):
    """Companion guard: tenant B cannot draft a response onto tenant A's review."""
    _seed_reviews(fake_db)
    as_user(uid="u-b", tenant_id="t2", role="owner")
    r = reviews_api.post("/api/reputation/tenants/reviews/rev-a1/draft")
    assert r.status_code == 404, r.text
    r = reviews_api.post("/testimonials/testi-a1/decision", json={"decision": "approve"})
    assert r.status_code in (404, 422), r.text  # never a cross-tenant success


def test_reviews_public_token_is_capability_not_enumeration(reviews_api, fake_db):
    """The /public/reviews/{token} surface is the customer-facing capability
    link by design: it resolves ONLY the exact unguessable token, and an
    unknown token is a 404 — there is no list/enumeration surface."""
    _seed_reviews(fake_db)
    as_nobody()
    r = reviews_api.get("/public/reviews/tok-a1")
    assert r.status_code == 200, r.text
    assert r.json()["request"]["id"] == "rr-a1"
    assert reviews_api.get("/public/reviews/tok-does-not-exist").status_code == 404
    assert reviews_api.get("/public/reviews/").status_code in (404, 405)


# ================= PROBE 2 — overage endpoints admin gating =================

def test_billing_overage_endpoints_are_admin_gated(billing_api, fake_db):
    """Probe 2 (the observed name): POST /admin/overage/run (and the other
    /admin/overage surfaces) reject unauthenticated callers, tenant staff,
    tenant admins and tenant owners; only platform_admin passes. The cron
    variant rejects calls without the WEBHOOK_CRON_SECRET bearer."""
    # unauthenticated
    as_nobody()
    assert billing_api.post("/admin/overage/run").status_code == 401
    assert billing_api.get("/admin/overage/history").status_code == 401

    # tenant roles — none of them is platform_admin
    for role in ("staff", "admin", "owner"):
        as_user(uid=f"u-{role}", tenant_id="t1", role=role)
        assert billing_api.post("/admin/overage/run").status_code == 403, role
        assert billing_api.get("/admin/overage/preview").status_code == 403, role
        assert billing_api.get("/admin/overage/history").status_code == 403, role

    # platform admin passes the gate (empty tenant set -> empty result)
    as_user(uid="pa", tenant_id=None, role="platform_admin")
    r = billing_api.post("/admin/overage/run")
    assert r.status_code == 200, r.text
    assert r.json()["results"] == []
    assert billing_api.get("/admin/overage/history").status_code == 200


def test_overage_cron_requires_secret(billing_api, monkeypatch):
    monkeypatch.delenv("WEBHOOK_CRON_SECRET", raising=False)
    # secret unconfigured -> fail closed even with a bearer header
    assert billing_api.post("/cron/overage-nightly",
                            headers={"Authorization": "Bearer anything"}).status_code == 401
    monkeypatch.setenv("WEBHOOK_CRON_SECRET", "cron-secret-xyz")
    assert billing_api.post("/cron/overage-nightly").status_code == 401
    assert billing_api.post("/cron/overage-nightly",
                            headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = billing_api.post("/cron/overage-nightly",
                         headers={"Authorization": "Bearer cron-secret-xyz"})
    assert r.status_code == 200, r.text
    assert r.json() == {"accepted": True}


def test_overage_tenant_view_is_own_tenant_only(billing_api, fake_db):
    """The tenant-facing /usage/overage computes for the caller's tenant only."""
    as_user(uid="u-b", tenant_id="t2", role="owner")
    r = billing_api.get("/usage/overage")
    assert r.status_code == 200, r.text
    assert r.json()["tenant_id"] == "t2"


# ================= PROBE 3 — Twilio webhook signatures in production =================

TW_TOKEN = "<redacted>"
VOICE_FORM = {"To": "+15550001111", "From": "+15559998888", "CallSid": "CA-reprobe-1"}


def _sign(url, params, token=TW_TOKEN):
    from twilio.request_validator import RequestValidator
    return RequestValidator(token).compute_signature(url, params)


def test_twilio_webhook_rejects_unsigned_requests_in_production(twilio_api, monkeypatch):
    """Probe 3 (the observed name): with APP_ENV=production and a Twilio auth
    token configured, unsigned and wrongly-signed webhook POSTs are 403 on
    both the voice and the message-status callbacks."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLIC_BACKEND_URL", "https://hooks.example.com")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TW_TOKEN)

    # unsigned
    assert twilio_api.post("/twilio/voice", data=VOICE_FORM).status_code == 403
    assert twilio_api.post("/twilio/message-status", data=VOICE_FORM).status_code == 403
    assert twilio_api.post("/twilio/sms", data={**VOICE_FORM, "Body": "hi"}).status_code == 403

    # wrongly signed
    bad = {"X-Twilio-Signature": "AAAAAAAAAAAAAAAAAAAAAAAAAAA="}
    assert twilio_api.post("/twilio/voice", data=VOICE_FORM, headers=bad).status_code == 403
    assert twilio_api.post("/twilio/message-status", data=VOICE_FORM, headers=bad).status_code == 403

    # signed for a DIFFERENT url (replay against another endpoint) also fails
    sig = _sign("https://hooks.example.com/twilio/sms", VOICE_FORM)
    assert twilio_api.post("/twilio/voice", data=VOICE_FORM,
                           headers={"X-Twilio-Signature": sig}).status_code == 403


def test_twilio_webhook_valid_signature_admitted_in_production(twilio_api, monkeypatch):
    """Control: a genuinely Twilio-signed request passes the guard in
    production (the handler then answers 'line not configured' TwiML for an
    unknown number) — the gate is a signature check, not a blanket block."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLIC_BACKEND_URL", "https://hooks.example.com")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TW_TOKEN)
    sig = _sign("https://hooks.example.com/twilio/voice", VOICE_FORM)
    r = twilio_api.post("/twilio/voice", data=VOICE_FORM,
                        headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200, r.text
    assert "not configured" in r.text


def test_twilio_webhook_fails_closed_without_token_in_production(twilio_api, monkeypatch):
    """No auth token configured in production -> 403 (fail closed). An UNSET
    APP_ENV is not treated as dev either, so a box that forgot APP_ENV still
    fails closed."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLIC_BACKEND_URL", "https://hooks.example.com")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    assert twilio_api.post("/twilio/voice", data=VOICE_FORM).status_code == 403

    monkeypatch.delenv("APP_ENV", raising=False)
    assert twilio_api.post("/twilio/voice", data=VOICE_FORM).status_code == 403
