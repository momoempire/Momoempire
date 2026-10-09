"""EMP-DEV-002 — checkout endpoint unit tests with Stripe mocked (no live network)."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Env required before importing app modules that touch JWT/DB.
os.environ.setdefault("JWT_SECRET", "emp-dev-002-test-secret")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "emp_dev_002_test")

from routers import payments as payments_mod  # noqa: E402
from security import require_tenant_user  # noqa: E402


class _FakePlans:
    def __init__(self, plan):
        self._plan = plan

    async def find_one(self, query, projection=None):
        key = query.get("key")
        if self._plan and self._plan.get("key") == key:
            return dict(self._plan)
        return None


class _FakeTx:
    def __init__(self):
        self.inserted = []

    async def insert_one(self, doc):
        self.inserted.append(doc)
        return SimpleNamespace(inserted_id="x")


class _FakeDB:
    def __init__(self, plan):
        self.plans = _FakePlans(plan)
        self.payment_transactions = _FakeTx()


def _app_with_user(user: dict | None):
    app = FastAPI()
    app.include_router(payments_mod.router, prefix="/api")
    if user is not None:
        async def _override():
            return user
        app.dependency_overrides[require_tenant_user] = _override
    return app


STARTER_WITH_PRICE = {
    "key": "starter",
    "name": "Starter",
    "price_cents": 1999,
    "stripe_price_id": "price_test_abc",
}
STARTER_NO_PRICE = {
    "key": "starter",
    "name": "Starter",
    "price_cents": 1999,
    "stripe_price_id": "",
}
TRIAL_PLAN = {
    "key": "trial",
    "name": "Trial",
    "price_cents": 0,
    "stripe_price_id": "",
}


@pytest.fixture
def owner():
    return {"id": "user_1", "tenant_id": "tenant_1", "email": "owner@example.com", "role": "owner"}


def test_checkout_requires_auth():
    from fastapi import HTTPException
    app = FastAPI()
    app.include_router(payments_mod.router, prefix="/api")

    async def _deny():
        raise HTTPException(401, "Not authenticated")

    app.dependency_overrides[require_tenant_user] = _deny
    client = TestClient(app)
    r = client.post("/api/payments/checkout", json={"plan_id": "starter", "origin_url": "https://example.com"})
    assert r.status_code == 401


def test_checkout_unknown_plan_400(owner):
    app = _app_with_user(owner)
    client = TestClient(app)
    fake_db = _FakeDB(None)
    with patch.object(payments_mod, "get_db", return_value=fake_db):
        r = client.post(
            "/api/payments/checkout",
            json={"plan_id": "does-not-exist", "origin_url": "https://example.com"},
        )
    assert r.status_code == 400
    assert "Unknown plan" in r.text


def test_checkout_non_purchasable_plan_400(owner):
    app = _app_with_user(owner)
    client = TestClient(app)
    fake_db = _FakeDB(TRIAL_PLAN)
    with patch.object(payments_mod, "get_db", return_value=fake_db):
        r = client.post(
            "/api/payments/checkout",
            json={"plan_id": "trial", "origin_url": "https://example.com"},
        )
    assert r.status_code == 400


def test_checkout_uses_stripe_price_id_when_set(owner):
    app = _app_with_user(owner)
    client = TestClient(app)
    fake_db = _FakeDB(STARTER_WITH_PRICE)
    session = SimpleNamespace(id="cs_test_price", url="https://checkout.stripe.test/c/pay/cs_test_price")

    with patch.object(payments_mod, "get_db", return_value=fake_db), patch.object(
        payments_mod.stripe.checkout.Session, "create", return_value=session
    ) as create_mock:
        r = client.post(
            "/api/payments/checkout",
            json={"plan_id": "starter", "origin_url": "https://app.example.com"},
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["checkout_url"] == session.url
    assert body["session_id"] == session.id
    kwargs = create_mock.call_args.kwargs
    assert kwargs["mode"] == "subscription"
    assert kwargs["line_items"] == [{"price": "price_test_abc", "quantity": 1}]
    assert "price_data" not in kwargs["line_items"][0]
    assert kwargs["success_url"].startswith("https://app.example.com/payment/success")
    assert kwargs["metadata"]["tenant_id"] == "tenant_1"
    assert kwargs["metadata"]["plan_id"] == "starter"
    assert fake_db.payment_transactions.inserted


def test_checkout_falls_back_to_price_data(owner):
    app = _app_with_user(owner)
    client = TestClient(app)
    fake_db = _FakeDB(STARTER_NO_PRICE)
    session = SimpleNamespace(id="cs_test_adhoc", url="https://checkout.stripe.test/c/pay/cs_test_adhoc")

    with patch.object(payments_mod, "get_db", return_value=fake_db), patch.object(
        payments_mod.stripe.checkout.Session, "create", return_value=session
    ) as create_mock:
        r = client.post(
            "/api/payments/checkout",
            json={"plan_id": "starter", "origin_url": "https://app.example.com"},
        )

    assert r.status_code == 200, r.text
    kwargs = create_mock.call_args.kwargs
    item = kwargs["line_items"][0]
    assert "price" not in item
    assert item["price_data"]["unit_amount"] == 1999
    assert item["price_data"]["recurring"]["interval"] == "month"


def test_payments_plans_strips_stripe_price_id(owner):
    """Defense in depth: checkout-ready list must not leak Stripe price IDs."""
    app = _app_with_user(owner)
    client = TestClient(app)

    class _Cursor:
        def __init__(self, rows):
            self._rows = rows

        def sort(self, *a, **k):
            return self

        async def to_list(self, n):
            return list(self._rows)

    class _Plans:
        def find(self, query, projection=None):
            # Mimic Mongo projection stripping
            row = dict(STARTER_WITH_PRICE)
            if projection and projection.get("stripe_price_id") == 0:
                row.pop("stripe_price_id", None)
            return _Cursor([row])

    fake = MagicMock()
    fake.plans = _Plans()

    with patch.object(payments_mod, "get_db", return_value=fake):
        r = client.get("/api/payments/plans")
    assert r.status_code == 200
    rows = r.json()
    assert rows and "stripe_price_id" not in rows[0]
