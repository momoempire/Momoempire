"""Unit tests for checkout financing (services/financing.py + routers/financing.py).

No database, network, or browser needed: a small FastAPI app with only the
financing routers, a fake in-memory Mongo, and overridden auth dependencies.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("JWT_SECRET", "financing-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "financing_test")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.financing as fin_mod  # noqa: E402
from security import require_tenant_user, require_tenant_owner_or_admin  # noqa: E402
from services.financing import (  # noqa: E402
    DEMO_TERMS,
    DemoFinancingProvider,
    FinancingError,
    FinancingNotConfigured,
    FinancingOffer,
    FinancingProvider,
    WisetackProvider,
    get_provider,
    monthly_payment_cents,
)


# ---------- fake mongo (same shape as test_equipment.py) ----------

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

    async def update_one(self, query, update):
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
        self.estimates = FakeCollection()
        self.invoices = FakeCollection()
        self.financing_applications = FakeCollection()


# ---------- fixtures ----------

current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(fin_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(fin_mod.router)
    app.include_router(fin_mod.public_router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user

    def fake_owner():
        if current_user.get("role") not in {"owner", "admin"}:
            raise HTTPException(403, "Owner/admin role required")
        return current_user

    app.dependency_overrides[require_tenant_owner_or_admin] = fake_owner
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def seed_tenant(fake_db, tenant_id="t1", financing_settings=None):
    doc = {"id": tenant_id, "name": f"Shop {tenant_id}"}
    if financing_settings is not None:
        doc["financing_settings"] = financing_settings
    fake_db.tenants.docs.append(doc)


def seed_quote(fake_db, tenant_id="t1", total=12000.0, token="tok-quote-1"):
    fake_db.estimates.docs.append({
        "id": "q-1", "tenant_id": tenant_id, "public_token": token,
        "total": total, "status": "sent",
    })


# ---------- amortization math ----------

def test_amortization_standard():
    # $1,000.00 at 12% APR for 12 months → $88.85/mo (verified against a
    # high-precision Decimal computation, not this function's own formula).
    assert monthly_payment_cents(100_000, 12.0, 12) == 8885
    assert monthly_payment_cents(1_200_000, 12.99, 60) == 27298
    assert monthly_payment_cents(1_200_000, 9.99, 36) == 38715


def test_amortization_zero_apr_no_division_by_zero():
    # 0% APR (promotional): principal split evenly, no interest term.
    assert monthly_payment_cents(100_000, 0.0, 12) == 8333
    assert monthly_payment_cents(120_000, 0, 10) == 12_000


def test_amortization_rejects_bad_input():
    with pytest.raises(ValueError):
        monthly_payment_cents(100_000, 9.99, 0)
    with pytest.raises(ValueError):
        monthly_payment_cents(-1, 9.99, 12)
    with pytest.raises(ValueError):
        monthly_payment_cents(100_000, -0.5, 12)


# ---------- provider interface ----------

def test_demo_provider_satisfies_abc():
    assert issubclass(DemoFinancingProvider, FinancingProvider)
    p = DemoFinancingProvider()
    assert isinstance(p.get_offers(120_000), list)
    assert all(isinstance(o, FinancingOffer) for o in p.get_offers(120_000))


def test_wisetack_skeleton_satisfies_abc_but_raises_until_live():
    assert issubclass(WisetackProvider, FinancingProvider)
    p = WisetackProvider()
    with pytest.raises(FinancingNotConfigured):
        p.get_offers(120_000)


def test_get_provider_registry():
    assert isinstance(get_provider("demo"), DemoFinancingProvider)
    assert isinstance(get_provider("wisetack"), WisetackProvider)
    with pytest.raises(FinancingError):
        get_provider("nope")


def test_demo_offers_span_terms_with_cheapest_longest():
    offers = DemoFinancingProvider().get_offers(1_200_000)  # $12,000
    by_term = {o.term_months: o for o in offers}
    assert {t for t, _ in DEMO_TERMS} == set(by_term)
    # Promotional 12-mo line is 0% APR and exactly principal/12.
    assert by_term[12].apr_percent == 0.0
    assert by_term[12].monthly_payment_cents == 100_000
    # Longer terms always mean a lower monthly payment.
    monthly = [by_term[t].monthly_payment_cents for t, _ in DEMO_TERMS]
    assert monthly == sorted(monthly, reverse=True)
    # Every offer is clearly labeled demo.
    assert all(o.demo and o.provider_name == "demo" for o in offers)
    assert all(o.total_payable_cents == o.monthly_payment_cents * o.term_months for o in offers)


def test_demo_offer_bounds():
    p = DemoFinancingProvider()
    with pytest.raises(FinancingError):
        p.get_offers(500)  # below $10 floor
    with pytest.raises(FinancingError):
        p.get_offers(2_500_001)  # above the $25k Wisetack cap
    with pytest.raises(FinancingError):
        p.get_offers(120_000, term_options=[13])  # unsupported term


# ---------- settings ----------

def test_settings_default_to_demo_enabled(client, fake_db):
    seed_tenant(fake_db)
    as_user("t1")
    r = client.get("/tenants/financing/settings")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["provider"] == "demo"
    assert body["live_ready"] is False


def test_settings_write_requires_owner_or_admin(client, fake_db):
    seed_tenant(fake_db)
    as_user("t1", role="staff")
    r = client.put("/tenants/financing/settings", json={"enabled": False, "provider": "demo"})
    assert r.status_code == 403, r.text

    as_user("t1", role="admin")
    r = client.put("/tenants/financing/settings", json={"enabled": False, "provider": "demo"})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False

    as_user("t1", role="owner")
    r = client.put("/tenants/financing/settings", json={"enabled": True, "provider": "wisetack", "merchant_id": "wsk_123"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "wisetack"
    assert body["merchant_id"] == "wsk_123"
    # v1 has no encrypted key storage, so Wisetack is never live-ready yet.
    assert body["live_ready"] is False


def test_settings_reject_unknown_provider(client, fake_db):
    seed_tenant(fake_db)
    as_user("t1")
    r = client.put("/tenants/financing/settings", json={"provider": "affirm"})
    assert r.status_code == 422


def test_settings_tenant_isolated(client, fake_db):
    seed_tenant(fake_db, "t1")
    seed_tenant(fake_db, "t2")
    as_user("t1")
    client.put("/tenants/financing/settings", json={"enabled": False, "provider": "demo"})

    as_user("t2")
    assert client.get("/tenants/financing/settings").json()["enabled"] is True


# ---------- offers (tenant) ----------

def test_tenant_offers_return_all_terms(client, fake_db):
    seed_tenant(fake_db)
    as_user("t1")
    r = client.get("/tenants/financing/offers", params={"amount_cents": 1_200_000})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["demo"] is True and body["provider"] == "demo"
    assert len(body["offers"]) == len(DEMO_TERMS)
    assert body["as_low_as_cents"] == min(o["monthly_payment_cents"] for o in body["offers"])
    sixty = next(o for o in body["offers"] if o["term_months"] == 60)
    assert sixty["monthly_payment_cents"] == 27298  # matches the math test


def test_offers_reject_non_positive_amount(client, fake_db):
    seed_tenant(fake_db)
    as_user("t1")
    assert client.get("/tenants/financing/offers", params={"amount_cents": 0}).status_code == 422
    assert client.get("/tenants/financing/offers", params={"amount_cents": -5}).status_code == 422


def test_offers_refuse_when_disabled(client, fake_db):
    seed_tenant(fake_db, financing_settings={"enabled": False, "provider": "demo", "merchant_id": None})
    as_user("t1")
    r = client.get("/tenants/financing/offers", params={"amount_cents": 120_000})
    assert r.status_code == 400


# ---------- applications ----------

def test_tenant_application_is_mock_demo_and_stored(client, fake_db):
    seed_tenant(fake_db)
    as_user("t1")
    r = client.post("/tenants/financing/applications", json={
        "amount_cents": 1_200_000, "term_months": 60,
        "customer_name": "Jane Doe", "customer_email": "jane@example.com",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "demo_pending"
    assert body["demo"] is True
    assert body["provider_application_id"].startswith("demo-app-")

    # Listing is tenant-scoped.
    r = client.get("/tenants/financing/applications")
    assert r.json()["count"] == 1
    assert r.json()["applications"][0]["customer_name"] == "Jane Doe"

    as_user("t2")
    assert client.get("/tenants/financing/applications").json()["count"] == 0


# ---------- public offers via quote token ----------

def test_public_offers_for_quote_token(client, fake_db):
    seed_tenant(fake_db)
    seed_quote(fake_db, total=12000.0)
    r = client.get("/public/financing/offers", params={"token": "tok-quote-1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["demo"] is True
    assert len(body["offers"]) == len(DEMO_TERMS)
    assert body["as_low_as_cents"] == 27298  # 60-mo line for $12,000


def test_public_offers_unknown_token_404(client, fake_db):
    seed_tenant(fake_db)
    r = client.get("/public/financing/offers", params={"token": "nope"})
    assert r.status_code == 404


def test_public_offers_hidden_when_disabled(client, fake_db):
    seed_tenant(fake_db, financing_settings={"enabled": False, "provider": "demo", "merchant_id": None})
    seed_quote(fake_db)
    r = client.get("/public/financing/offers", params={"token": "tok-quote-1"})
    assert r.status_code == 404


def test_public_application_demo_only(client, fake_db):
    seed_tenant(fake_db)
    seed_quote(fake_db)
    r = client.post("/public/financing/applications", json={
        "token": "tok-quote-1", "term_months": 36, "customer_name": "Jane Doe",
    })
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "demo_pending"
    assert r.json()["demo"] is True


def test_public_application_unknown_token_404(client, fake_db):
    seed_tenant(fake_db)
    r = client.post("/public/financing/applications", json={
        "token": "nope", "term_months": 36, "customer_name": "Jane Doe",
    })
    assert r.status_code == 404
