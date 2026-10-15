"""Pricing v2: the price shown on the site and the amount checkout charges come from the same
plan.price_cents. Fails if the seeded prices drift or checkout charges anything else."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "price-parity-test")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "price_parity_test")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_unit")  # fake test value, never sent anywhere

from routers import payments as payments_mod  # noqa: E402
from routers.plans import DEFAULT_PLANS  # noqa: E402
from security import require_tenant_user  # noqa: E402

# The checkout amounts Brann confirmed (Oct 13): $19.99 / $49.99 / $99.99 / $199.99.
EXPECTED_CENTS = {"trial": 0, "starter": 1999, "growth": 4999, "ai_office": 9999, "high_volume": 19999, "enterprise": 0}


def test_default_plan_prices_match_checkout_amounts():
    assert {p["key"]: p["price_cents"] for p in DEFAULT_PLANS} == EXPECTED_CENTS


class _DB:
    def __init__(self, plan):
        self._plan = plan
        self.inserted = []
        outer = self

        class Plans:
            async def find_one(self, q, projection=None):
                return dict(outer._plan) if q.get("key") == outer._plan["key"] else None

        class Tx:
            async def insert_one(self, doc):
                outer.inserted.append(doc)

        self.plans, self.payment_transactions = Plans(), Tx()


@pytest.mark.parametrize("plan", [p for p in DEFAULT_PLANS if p["price_cents"] > 0], ids=lambda p: p["key"])
def test_checkout_charges_the_displayed_price(plan):
    app = FastAPI()
    app.include_router(payments_mod.router, prefix="/api")
    app.dependency_overrides[require_tenant_user] = lambda: {"id": "u", "tenant_id": "t", "role": "owner"}
    db = _DB({**plan, "stripe_price_id": ""})
    session = SimpleNamespace(id="cs_test_1", url="https://checkout.stripe.test/cs_test_1")
    with patch.object(payments_mod, "get_db", return_value=db), \
         patch.object(payments_mod.stripe.checkout.Session, "create", return_value=session) as create:
        r = TestClient(app).post("/api/payments/checkout", json={"plan_id": plan["key"], "origin_url": "https://example.com"})
    assert r.status_code == 200, r.text
    item = create.call_args.kwargs["line_items"][0]
    assert item["price_data"]["unit_amount"] == plan["price_cents"]
    assert db.inserted[0]["amount"] == plan["price_cents"]
