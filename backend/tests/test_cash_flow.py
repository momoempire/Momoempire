"""Tests for the predictive cash-flow forecaster (routers/cash_flow.py +
services/cash_flow.py). Fake mongo, no live DB. Env defaults mirror
test_trade_packs.py.

Precise forecast math is tested by calling services.cash_flow.build_forecast
directly with a fixed `now` (day-of-week independent). Endpoint tests use
live `now` and assert shapes, flags, round-trips and tenant scoping.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "cash-flow-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "cash_flow_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.cash_flow as cf_router_mod  # noqa: E402
from security import require_tenant_user  # noqa: E402
from services import cash_flow as cf_svc  # noqa: E402

FIXED_NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)  # Thursday


# ---------- fake mongo ----------

class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

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
        self.invoices = FakeCollection()
        self.appointments = FakeCollection()
        self.estimates = FakeCollection()
        self.purchase_orders = FakeCollection()
        self.tenant_settings = FakeCollection()


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(cf_router_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(cf_router_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def _iso(dt):
    return dt.isoformat()


def shop_data(tid: str, now: datetime):
    """Returns (invoices, appointments, estimates_by_id, purchase_orders, settings).

    Paid history -> cust-1 pays in 9 days. Open: $2000 due in 3d (cust-1),
    $5000 past-due (cust-2, no history -> shop default). One confirmed $3000
    job in 6d. One sent $1000 PO.
    """
    invoices = []
    for k in range(3):
        billed = now - timedelta(days=60 + k * 10)
        invoices.append({
            "id": f"paid-{k}", "tenant_id": tid, "customer_id": "cust-1",
            "customer_name": "Acme Homes", "total": 1000.0, "status": "paid",
            "created_at": _iso(billed), "paid_at": _iso(billed + timedelta(days=9)),
        })
    invoices.append({
        "id": "open-1", "tenant_id": tid, "customer_id": "cust-1",
        "customer_name": "Acme Homes", "amount_due": 2000.0, "status": "unpaid",
        "created_at": _iso(now - timedelta(days=10)),
        "due_date": _iso(now + timedelta(days=3)),
    })
    invoices.append({
        "id": "open-2", "tenant_id": tid, "customer_id": "cust-2",
        "customer_name": "Beta LLC", "amount_due": 5000.0, "status": "overdue",
        "created_at": _iso(now - timedelta(days=40)),
        "due_date": _iso(now - timedelta(days=5)),
    })
    estimates_by_id = {"est-1": {"id": "est-1", "total": 3000.0}}
    appointments = [{
        "id": "appt-1", "tenant_id": tid, "status": "confirmed",
        "start_at": _iso(now + timedelta(days=6)), "customer_id": "cust-1",
        "customer_name": "Acme Homes", "service_name": "AC install", "quote_id": "est-1",
    }]
    purchase_orders = [{
        "id": "po-1", "tenant_id": tid, "supplier_name": "ACME Supply",
        "status": "sent", "sent_at": _iso(now - timedelta(days=1)),
        "lines": [{"qty": 5, "unit_cost": 200.0}],
    }]
    settings = {
        "starting_balance": 10000.0, "safety_threshold": 9000.0,
        "monthly_payroll": 0.0, "default_days_to_pay": 7,
        "supplier_payment_days": 14, "invoice_terms_days": 30,
    }
    return invoices, appointments, estimates_by_id, purchase_orders, settings


def seed_shop(db: FakeDB, tid: str, now: datetime = None):
    now = now or datetime.now(timezone.utc)
    invoices, appointments, estimates_by_id, pos, settings = shop_data(tid, now)
    db.invoices.docs.extend(invoices)
    db.appointments.docs.extend(appointments)
    db.estimates.docs.extend([dict(e, tenant_id=tid) for e in estimates_by_id.values()])
    db.purchase_orders.docs.extend(pos)
    db.tenant_settings.docs.append(
        {"id": f"ts-{tid}", "tenant_id": tid, "cash_flow": settings})


def forecast_fixed(settings_override=None):
    """build_forecast on shop_data with FIXED_NOW (2026-10-15 Thu)."""
    invoices, appts, est, pos, settings = shop_data("t1", FIXED_NOW)
    settings.update(settings_override or {})
    return cf_svc.build_forecast(invoices, appts, est, pos, settings, now=FIXED_NOW)


# ---------- service unit tests ----------

def test_days_to_pay_per_customer_from_paid_history():
    now = FIXED_NOW
    paid = [
        {"customer_id": "a", "created_at": _iso(now - timedelta(days=30)),
         "paid_at": _iso(now - timedelta(days=20))},  # 10 days
        {"customer_id": "a", "created_at": _iso(now - timedelta(days=50)),
         "paid_at": _iso(now - timedelta(days=40))},  # 10 days
        {"customer_id": "b", "created_at": _iso(now - timedelta(days=30)),
         "paid_at": _iso(now - timedelta(days=25))},  # 5 days
    ]
    overall, per = cf_svc.compute_days_to_pay(paid)
    assert per["a"]["avg_days_to_pay"] == 10.0
    assert per["b"]["avg_days_to_pay"] == 5.0
    assert overall == 8.3  # rounded to 1 decimal


def test_days_to_pay_ignores_bad_records():
    now = FIXED_NOW
    paid = [
        {"customer_id": "a", "created_at": _iso(now - timedelta(days=5)),
         "paid_at": _iso(now - timedelta(days=10))},  # paid before billed -> skip
        {"customer_id": "a"},  # no dates -> skip
    ]
    overall, per = cf_svc.compute_days_to_pay(paid)
    assert per == {}
    assert overall == cf_svc.DEFAULT_DAYS_TO_PAY


def test_week_index_past_events_land_in_week_zero():
    monday = cf_svc._monday_of(FIXED_NOW)
    assert cf_svc._week_index(FIXED_NOW - timedelta(days=40), monday) == 0
    assert cf_svc._week_index(FIXED_NOW + timedelta(days=90), monday) == 11  # clamped


def test_forecast_invoice_buckets_fixed_now():
    # FIXED_NOW Thu 2026-10-15; monday = Oct 12.
    fc = forecast_fixed()
    # open-1: due Oct 18 + 9d cust-1 lateness = Oct 27 -> (15d)//7 = week idx 2
    # open-2: past-due Oct 10 + 7d default = Oct 17 -> 5d -> week idx 0
    assert fc["weeks"][2]["invoices_in"] == pytest.approx(2000.0, abs=0.01)
    assert fc["weeks"][0]["invoices_in"] == pytest.approx(5000.0, abs=0.01)


def test_forecast_job_value_weighted_by_stage_probability():
    fc = forecast_fixed()
    # confirmed job: 3000 * 0.75 = 2250; pays Oct 21 + 30d terms + 9d = Nov 29
    # -> (48d)//7 = week idx 6
    assert fc["weeks"][6]["jobs_in"] == pytest.approx(2250.0, abs=0.01)


def test_forecast_po_outflow_bucket_and_running_balance():
    fc = forecast_fixed()
    # PO $1000 sent Oct 14 + 14d supplier terms = Oct 28 -> (16d)//7 = idx 2
    assert fc["weeks"][2]["expected_out"] == pytest.approx(1000.0, abs=0.01)
    assert any(d["type"] == "purchase_order" for d in fc["outflow_detail"])
    # running balance math: start 10000; w0: +5000; w1: 0; w2: +2000-1000
    assert fc["weeks"][0]["running_balance"] == pytest.approx(15000.0, abs=0.01)
    assert fc["weeks"][2]["running_balance"] == pytest.approx(16000.0, abs=0.01)


def test_forecast_warnings_with_low_starting_balance():
    fc = forecast_fixed({"starting_balance": 1000.0, "safety_threshold": 9000.0})
    assert fc["warnings"], "expected at least one low-balance week"
    w0 = fc["warnings"][0]
    assert w0["week"] == 1
    assert w0["projected_balance"] == pytest.approx(6000.0, abs=0.01)  # 1000 + 5000
    assert w0["shortfall"] == pytest.approx(3000.0, abs=0.01)


def test_levers_ranked_by_impact():
    invoices, appts, est, pos, settings = shop_data("t1", FIXED_NOW)
    fc = cf_svc.build_forecast(invoices, appts, est, pos, settings, now=FIXED_NOW)
    levers = cf_svc.build_levers(fc, invoices, pos)
    assert len(levers) >= 3
    impacts = [l["projected_impact"] for l in levers]
    assert impacts == sorted(impacts, reverse=True)
    # top-5 open invoices: (5000 + 2000) * 0.8 = 5600
    assert levers[0]["id"] == "accelerate-invoices"
    assert levers[0]["projected_impact"] == pytest.approx(5600.0, abs=0.01)
    po_lever = next(l for l in levers if l["kind"] == "defer_outflow")
    assert po_lever["projected_impact"] == pytest.approx(1000.0, abs=0.01)


def test_assumptions_expose_model_parameters():
    fc = forecast_fixed()
    a = fc["assumptions"]
    assert a["avg_days_to_pay"] == 9.0
    assert a["days_to_pay_samples"] == 3
    assert a["job_completion_probability"]["confirmed"] == 0.75
    assert a["supplier_payment_days"] == 14
    assert a["invoice_terms_days"] == 30
    assert a["data_source"] == "live"


# ---------- endpoint tests ----------

def test_forecast_endpoint_shape(client, fake_db):
    as_user("t1")
    seed_shop(fake_db, "t1")
    r = client.get("/tenants/cash-flow/forecast")
    assert r.status_code == 200
    body = r.json()
    assert body["demo"] is False
    assert len(body["weeks"]) == 12
    w = body["weeks"][0]
    assert {"week", "week_start", "expected_in", "expected_out", "net",
            "running_balance", "invoices_in", "jobs_in"} <= set(w.keys())
    assert w["running_balance"] == pytest.approx(10000.0 + w["net"], abs=0.01)
    assert body["starting_balance"] == 10000.0


def test_starting_balance_round_trip_and_roles(client, fake_db):
    as_user("t1", role="tech")
    assert client.post("/tenants/cash-flow/starting-balance",
                       json={"balance": 5000.0}).status_code == 403
    as_user("t1", role="admin")
    r = client.post("/tenants/cash-flow/starting-balance",
                    json={"balance": 5000.0, "safety_threshold": 2000.0})
    assert r.status_code == 200
    assert r.json()["cash_flow"]["starting_balance"] == 5000.0
    assert r.json()["cash_flow"]["safety_threshold"] == 2000.0
    assert client.get("/tenants/cash-flow/forecast").json()["starting_balance"] == 5000.0
    # validation: negative balance rejected
    assert client.post("/tenants/cash-flow/starting-balance",
                       json={"balance": -1}).status_code == 422


def test_warnings_endpoint_shape(client, fake_db):
    as_user("t1")
    seed_shop(fake_db, "t1")
    body = client.get("/tenants/cash-flow/warnings").json()
    assert "warnings" in body and "safety_threshold" in body
    for w in body["warnings"]:
        assert w["projected_balance"] < 9000.0
        assert w["shortfall"] == pytest.approx(9000.0 - w["projected_balance"], abs=0.01)


def test_levers_endpoint_ranking(client, fake_db):
    as_user("t1")
    seed_shop(fake_db, "t1")
    body = client.get("/tenants/cash-flow/levers").json()
    levers = body["levers"]
    assert len(levers) >= 3
    impacts = [l["projected_impact"] for l in levers]
    assert impacts == sorted(impacts, reverse=True)
    assert levers[0]["id"] == "accelerate-invoices"
    assert levers[0]["projected_impact"] == pytest.approx(5600.0, abs=0.01)


def test_assumptions_endpoint(client, fake_db):
    as_user("t1")
    seed_shop(fake_db, "t1")
    body = client.get("/tenants/cash-flow/assumptions").json()
    a = body["assumptions"]
    assert a["avg_days_to_pay"] == 9.0
    assert a["days_to_pay_samples"] == 3
    assert a["forecast_weeks"] == 12
    assert a["data_source"] == "live"


def test_demo_fallback_when_no_data(client, fake_db):
    as_user("t-empty")
    body = client.get("/tenants/cash-flow/forecast").json()
    assert body["demo"] is True
    assert len(body["weeks"]) == 12
    assert body["starting_balance"] == 12000.0
    assert body["assumptions"]["data_source"] == "demo"
    assert client.get("/tenants/cash-flow/warnings").json()["demo"] is True
    assert client.get("/tenants/cash-flow/levers").json()["demo"] is True
    assert client.get("/tenants/cash-flow/assumptions").json()["demo"] is True


def test_endpoints_are_tenant_scoped(client, fake_db):
    as_user("t1")
    seed_shop(fake_db, "t1")
    as_user("t2")
    body = client.get("/tenants/cash-flow/forecast").json()
    assert body["demo"] is True  # t2 has no data -> demo, not t1's numbers
