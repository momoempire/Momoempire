"""Unit tests for the price intelligence network (routers/price_intelligence.py,
services/price_intelligence.py).

Zero network, zero database: a small FastAPI app with only the price-intel
router, a fake in-memory Mongo, and an overridden auth dependency.

Privacy coverage (the non-negotiables):
- < 5 contributors -> {"insufficient_data": True} and NO pricing numbers.
- Opted-out tenants (the default) are excluded from every aggregation.
- Tenant ids, names, and individual prices never appear in responses.
- Region always comes from the tenant's own profile (no client-supplied region).
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "priceintel-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "priceintel_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.price_intelligence as pi_mod  # noqa: E402
from security import require_tenant_user  # noqa: E402
from services.price_intelligence import (  # noqa: E402
    MIN_CONTRIBUTORS,
    aggregate_prices,
    is_opportunity,
    percentile,
    position_for,
)


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
        return SimpleNamespace(inserted_id=doc.get("id"))

    async def update_one(self, query, update):
        matched = 0
        for d in self.docs:
            if _match(d, query):
                matched += 1
                for k, v in update.get("$set", {}).items():
                    d[k] = v
                break
        return SimpleNamespace(matched_count=matched, modified_count=matched)


class FakeDB:
    def __init__(self):
        self.tenants = FakeCollection()
        self.pricebook_items = FakeCollection()


# ---------- fixtures ----------

current_user = {}
_db = None


@pytest.fixture
def fake_db():
    global _db
    _db = FakeDB()
    return _db


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(pi_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(pi_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def make_tenant(db, tid, industry="hvac", region="Dallas, TX", opt_in=None):
    doc = {"id": tid, "name": f"Shop {tid}", "industry_slug": industry,
           "service_areas": [region]}
    if opt_in is not None:
        doc["price_intel_opt_in"] = opt_in
    db.tenants.docs.append(doc)
    return doc


def make_item(db, tid, name="Capacitor replacement", price=100.0, active=True):
    db.pricebook_items.docs.append({
        "id": f"item-{tid}-{len(db.pricebook_items.docs)}",
        "tenant_id": tid, "name": name, "category": "service",
        "price": price, "cost": 0.0, "active": active, "version": 1,
    })


# ---------- service-level math ----------

def test_percentile_linear_interpolation():
    assert percentile([10.0, 20.0, 30.0, 40.0, 50.0], 0.5) == 30.0
    assert percentile([10.0, 20.0, 30.0, 40.0, 50.0], 0.25) == 20.0
    assert percentile([10.0, 20.0, 30.0, 40.0, 50.0], 0.75) == 40.0
    assert percentile([100.0, 200.0], 0.5) == 150.0  # even count interpolates
    assert percentile([99.0], 0.5) == 99.0


def test_aggregate_prices_threshold():
    assert aggregate_prices([100.0, 120.0, 140.0, 160.0]) is None  # only 4
    agg = aggregate_prices([100.0, 120.0, 140.0, 160.0, 180.0])
    assert agg == {"median": 140.0, "p25": 120.0, "p75": 160.0, "contributor_count": 5}


def test_position_for():
    assert position_for(80.0, 100.0) == "below"
    assert position_for(120.0, 100.0) == "above"
    assert position_for(103.0, 100.0) == "at"   # within 5% tolerance
    assert position_for(100.0, 100.0) == "at"


def test_is_opportunity_threshold():
    assert is_opportunity(80.0, 100.0) is True    # 20% below
    assert is_opportunity(85.0, 100.0) is True    # exactly 15% below
    assert is_opportunity(86.0, 100.0) is False   # 14% below
    assert is_opportunity(110.0, 100.0) is False


# ---------- privacy threshold ----------

def test_below_threshold_returns_insufficient_data_with_no_numbers(client, fake_db):
    """4 contributors -> insufficient_data, and NO pricing numbers leak."""
    make_tenant(fake_db, "me", opt_in=True)
    for i, p in enumerate([100.0, 120.0, 140.0, 160.0]):
        make_tenant(fake_db, f"t{i}", opt_in=True)
        make_item(fake_db, f"t{i}", price=p)
    as_user("me")

    r = client.get("/tenants/price-intel/benchmarks", params={"item_name": "Capacitor replacement"})
    assert r.status_code == 200
    body = r.json()
    assert body["insufficient_data"] is True
    # No numbers whatsoever: not even contributor_count.
    # (booleans excluded from the check — isinstance(True, int) is True in Python)
    for key in ("my_price", "median", "p25", "p75", "contributor_count"):
        assert key not in body, f"leaked key {key} below threshold"
    assert not any(isinstance(v, (int, float)) and not isinstance(v, bool)
                   for v in body.values())


def test_five_contributors_returns_aggregates(client, fake_db):
    prices = [100.0, 120.0, 140.0, 160.0, 180.0]
    make_tenant(fake_db, "me")  # caller NOT opted in: excluded from cohort
    for i, p in enumerate(prices):
        make_tenant(fake_db, f"t{i}", opt_in=True)
        make_item(fake_db, f"t{i}", price=p)
    make_item(fake_db, "me", price=90.0)  # my own pricebook item
    as_user("me")

    r = client.get("/tenants/price-intel/benchmarks", params={"item_name": "capacitor REPLACEMENT"})
    assert r.status_code == 200
    body = r.json()
    assert body["insufficient_data"] is False
    assert body["median"] == 140.0
    assert body["p25"] == 120.0
    assert body["p75"] == 160.0
    assert body["contributor_count"] == 5
    assert body["my_price"] == 90.0
    assert body["position"] == "below"
    assert body["region"] == "Dallas, TX"


def test_opt_out_tenant_excluded_from_aggregation(client, fake_db):
    """An opted-out tenant's price must not move the aggregates."""
    make_tenant(fake_db, "me")
    for i in range(5):
        make_tenant(fake_db, f"t{i}", opt_in=True)
        make_item(fake_db, f"t{i}", price=100.0)
    make_tenant(fake_db, "quitter", opt_in=False)
    make_item(fake_db, "quitter", price=9999.0)  # would wreck the median if included
    as_user("me")

    body = client.get("/tenants/price-intel/benchmarks",
                      params={"item_name": "Capacitor replacement"}).json()
    assert body["contributor_count"] == 5
    assert body["median"] == 100.0


def test_default_is_opted_out(client, fake_db):
    """Tenants that never touched consent are excluded (flag defaults OFF)."""
    make_tenant(fake_db, "me")
    for i in range(5):
        make_tenant(fake_db, f"t{i}")  # no opt_in key at all
        make_item(fake_db, f"t{i}", price=100.0)
    as_user("me")

    body = client.get("/tenants/price-intel/benchmarks",
                      params={"item_name": "Capacitor replacement"}).json()
    assert body["insufficient_data"] is True


def test_opt_in_and_opt_out_endpoints(client, fake_db):
    make_tenant(fake_db, "me")
    as_user("me")

    r = client.post("/tenants/price-intel/opt-in")
    assert r.json() == {"ok": True, "opt_in": True}
    assert fake_db.tenants.docs[0]["price_intel_opt_in"] is True

    r = client.post("/tenants/price-intel/opt-out")
    assert r.json() == {"ok": True, "opt_in": False}
    assert fake_db.tenants.docs[0]["price_intel_opt_in"] is False


def test_tenant_isolation_no_ids_or_prices_leak(client, fake_db):
    """Responses must never identify contributors: no tenant ids, names,
    or individual prices in the payload."""
    make_tenant(fake_db, "me")
    secret_ids = []
    for i in range(6):
        tid = f"secret-tenant-{i}-xyz"
        secret_ids.append(tid)
        make_tenant(fake_db, tid, opt_in=True)
        make_item(fake_db, tid, price=100.0 + i * 10)
    as_user("me")

    body = client.get("/tenants/price-intel/benchmarks",
                      params={"item_name": "Capacitor replacement"}).json()
    raw = json.dumps(body)
    for tid in secret_ids:
        assert tid not in raw
    assert "secret" not in raw.lower()
    # Individual contributor prices (110, 120, ...) are not recoverable:
    # only aggregates exist.
    assert "median" in body and body["contributor_count"] == 6


def test_region_comes_from_profile_not_client(client, fake_db):
    """Tenants in a different region don't count; there is no way for the
    client to supply a region (no region query parameter is accepted)."""
    make_tenant(fake_db, "me", region="Dallas, TX")
    for i in range(4):  # 4 in Dallas -> below threshold on their own
        make_tenant(fake_db, f"dal{i}", region="Dallas, TX", opt_in=True)
        make_item(fake_db, f"dal{i}", price=100.0)
    for i in range(4):  # 4 in Austin must NOT join the Dallas cohort
        make_tenant(fake_db, f"aus{i}", region="Austin, TX", opt_in=True)
        make_item(fake_db, f"aus{i}", price=500.0)
    as_user("me")

    body = client.get("/tenants/price-intel/benchmarks",
                      params={"item_name": "Capacitor replacement"}).json()
    assert body["insufficient_data"] is True  # 4 Dallas only, Austin excluded
    assert body["region"] == "Dallas, TX"

    # Move my own profile to Austin -> the Austin cohort now applies.
    fake_db.tenants.docs[0]["service_areas"] = ["Austin, TX"]
    body = client.get("/tenants/price-intel/benchmarks",
                      params={"item_name": "Capacitor replacement"}).json()
    assert body["insufficient_data"] is True  # still 4 < 5, but now Austin
    assert body["region"] == "Austin, TX"


def test_trade_filter_defaults_to_my_industry(client, fake_db):
    make_tenant(fake_db, "me", industry="hvac")
    for i in range(5):
        make_tenant(fake_db, f"h{i}", industry="hvac", opt_in=True)
        make_item(fake_db, f"h{i}", price=100.0)
    for i in range(5):
        make_tenant(fake_db, f"p{i}", industry="plumbing", opt_in=True)
        make_item(fake_db, f"p{i}", price=999.0)
    as_user("me")

    body = client.get("/tenants/price-intel/benchmarks",
                      params={"item_name": "Capacitor replacement"}).json()
    assert body["median"] == 100.0  # plumbing shops excluded
    assert body["trade"] == "hvac"

    body = client.get("/tenants/price-intel/benchmarks",
                      params={"item_name": "Capacitor replacement", "trade": "plumbing"}).json()
    assert body["median"] == 999.0


# ---------- opportunities ----------

def test_opportunities_flags_underpriced_items(client, fake_db):
    make_tenant(fake_db, "me")
    for i, p in enumerate([100.0, 110.0, 120.0, 130.0, 140.0]):  # median 120
        make_tenant(fake_db, f"t{i}", opt_in=True)
        make_item(fake_db, f"t{i}", name="Capacitor replacement", price=p)
        make_item(fake_db, f"t{i}", name="Contactor replacement", price=200.0)
    # my catalog: one 20% under median -> opportunity; one above -> not;
    # one with no network data at all -> skipped (insufficient_data)
    make_item(fake_db, "me", name="Capacitor replacement", price=96.0)
    make_item(fake_db, "me", name="Contactor replacement", price=250.0)
    make_item(fake_db, "me", name="Rare widget", price=10.0)
    as_user("me")

    body = client.get("/tenants/price-intel/opportunities").json()
    assert body["count"] == 1
    opp = body["opportunities"][0]
    assert opp["item_name"] == "Capacitor replacement"
    assert opp["my_price"] == 96.0
    assert opp["median"] == 120.0
    assert opp["gap_pct"] == 20.0
    raw = json.dumps(body)
    assert "secret" not in raw


def test_opportunities_empty_when_nothing_below_threshold(client, fake_db):
    make_tenant(fake_db, "me")
    for i in range(5):
        make_tenant(fake_db, f"t{i}", opt_in=True)
        make_item(fake_db, f"t{i}", price=100.0)
    make_item(fake_db, "me", price=100.0)
    as_user("me")

    body = client.get("/tenants/price-intel/opportunities").json()
    assert body == {"opportunities": [], "count": 0, "region": "Dallas, TX",
                    "trade": "hvac", "min_contributors": MIN_CONTRIBUTORS,
                    "opportunity_threshold_pct": 15}


def test_benchmark_requires_item_name(client, fake_db):
    make_tenant(fake_db, "me")
    as_user("me")
    r = client.get("/tenants/price-intel/benchmarks")
    assert r.status_code == 400


def test_benchmark_unknown_tenant_404(client, fake_db):
    as_user("ghost")
    r = client.get("/tenants/price-intel/benchmarks", params={"item_name": "x"})
    assert r.status_code == 404
