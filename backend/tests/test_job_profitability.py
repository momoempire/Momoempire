"""Tests for real-time per-job profitability (#11).

Covers services/job_profitability.py (pure Decimal math) and
routers/job_profitability.py (breakdown, dashboard, settings RBAC).

Zero live network: a fake in-memory Mongo stands in for Motor and auth
dependencies are overridden, following tests/test_marketing_automation.py.
"""
from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("JWT_SECRET", "profitability-test-secret")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "profitability_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.job_profitability as prof_mod  # noqa: E402
import services.job_profitability as prof_svc  # noqa: E402
from security import require_tenant_user, require_tenant_owner_or_admin  # noqa: E402


# ---------- fake mongo ----------

class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

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

    async def update_one(self, query, update, upsert=False):
        matched = 0
        for d in self.docs:
            if _match(d, query):
                matched += 1
                for k, v in update.get("$set", {}).items():
                    d[k] = v
                break
        if matched == 0 and upsert:
            doc = dict(query)
            doc.update(update.get("$set", {}))
            self.docs.append(doc)
            matched = 1
        return SimpleNamespace(matched_count=matched, modified_count=matched)


class FakeDB:
    def __init__(self):
        self.appointments = FakeCollection()
        self.invoices = FakeCollection()
        self.estimates = FakeCollection()
        self.pricebook_items = FakeCollection()
        self.profitability_settings = FakeCollection()


# ---------- fixtures ----------

current_user = {}


def as_user(tenant_id, role="owner", user_id=None):
    current_user.clear()
    current_user.update({"id": user_id or f"u-{tenant_id}-{role}",
                         "tenant_id": tenant_id, "role": role})


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(prof_mod, "get_db", lambda: fake_db)

    def fake_tenant_user():
        if not current_user.get("tenant_id"):
            raise HTTPException(403, "No tenant assigned")
        return current_user

    def fake_owner_or_admin():
        u = fake_tenant_user()
        if u.get("role") not in ("owner", "admin"):
            raise HTTPException(403, "Owner/admin role required")
        return u

    app = FastAPI()
    app.include_router(prof_mod.router)
    app.dependency_overrides[require_tenant_user] = fake_tenant_user
    app.dependency_overrides[require_tenant_owner_or_admin] = fake_owner_or_admin
    return TestClient(app)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _seed(db):
    await db.appointments.insert_one({
        "id": "j1", "tenant_id": "t1", "customer_name": "Rosa Diaz",
        "service_name": "AC Repair", "staff_id": "tech1", "staff": "Ava",
        "logged_hours": 2.5, "quote_id": "q1", "status": "completed",
        "start_at": "2026-10-10T09:00:00+00:00",
        "end_at": "2026-10-10T11:30:00+00:00",
        "completed_at": "2026-10-10T12:00:00+00:00",
    })
    await db.invoices.insert_one({
        "id": "i1", "tenant_id": "t1", "appointment_id": "j1", "quote_id": "q1",
        "total_cents": 50000,
        "lines": [{"description": "Capacitor", "quantity": 2, "unit_price": 75.0,
                   "service_id": None, "pricebook_item_id": "pb-cap"}],
        "status": "sent",
    })
    await db.pricebook_items.insert_one(
        {"id": "pb-cap", "tenant_id": "t1", "name": "Capacitor", "price": 75.0, "cost": 18.0})
    # other tenant's job: must never leak
    await db.appointments.insert_one({
        "id": "jX", "tenant_id": "t2", "customer_name": "Intruder",
        "service_name": "AC Repair", "staff_id": "tech9", "staff": "Zed",
        "logged_hours": 1.0, "status": "completed",
        "start_at": "2026-10-10T09:00:00+00:00",
        "end_at": "2026-10-10T10:00:00+00:00",
        "completed_at": "2026-10-10T10:00:00+00:00",
    })


# ---------- service math (hand-computed Decimal values) ----------

def test_labor_parts_overhead_hand_computed():
    hours, rate = Decimal("2.5"), Decimal("75")
    labor = prof_svc.labor_cost(hours, rate)
    assert labor == Decimal("187.50"), labor

    parts = prof_svc.parts_cost(
        [{"description": "Capacitor", "quantity": 2, "pricebook_item_id": "pb-cap"}],
        {"pb-cap": {"cost": 18.0}},
    )
    assert parts == Decimal("36.00"), parts

    overhead = prof_svc.overhead_allocated(
        hours, Decimal("500.00"),
        {"overhead_rate": "15", "overhead_method": "per-hour"},
    )
    assert overhead == Decimal("37.50"), overhead

    total = labor + parts + overhead
    profit = prof_svc.gross_profit(Decimal("500.00"), total)
    assert profit == Decimal("239.00"), profit

    margin = prof_svc.margin_pct(profit, Decimal("500.00"))
    assert margin == Decimal("47.80"), margin  # 239/500*100 = 47.80


def test_overhead_pct_of_revenue_method():
    overhead = prof_svc.overhead_allocated(
        Decimal("2.5"), Decimal("500.00"),
        {"overhead_rate": "10", "overhead_method": "pct-of-revenue"},
    )
    assert overhead == Decimal("50.00"), overhead


def test_margin_pct_zero_revenue_no_crash():
    assert prof_svc.margin_pct(Decimal("-37.50"), Decimal("0")) == Decimal("0")


def test_job_hours_fallback_chain():
    # logged_hours wins
    assert prof_svc.job_hours({"logged_hours": 3}) == Decimal("3.00")
    # then the scheduled window: 9:00 -> 11:30 = 2.5h
    assert prof_svc.job_hours({
        "start_at": "2026-10-10T09:00:00+00:00",
        "end_at": "2026-10-10T11:30:00+00:00",
    }) == Decimal("2.50")
    # nothing at all degrades to 0
    assert prof_svc.job_hours({}) == Decimal("0")


def test_hourly_rate_precedence():
    settings = {
        "default_hourly_rate": "45.00",
        "hourly_rates": {"tech1": "75.00", "role:senior": "65.00"},
    }
    assert prof_svc.hourly_rate_for("tech1", "junior", settings) == Decimal("75.00")
    assert prof_svc.hourly_rate_for("tech7", "senior", settings) == Decimal("65.00")
    assert prof_svc.hourly_rate_for("tech7", "junior", settings) == Decimal("45.00")


def test_normalize_settings_rejects_bad_input():
    with pytest.raises(ValueError):
        prof_svc.normalize_settings({"overhead_method": "per-day"})
    with pytest.raises(ValueError):
        prof_svc.normalize_settings({"hourly_rates": {"tech1": -5}})


# ---------- router: per-job breakdown ----------

def test_job_breakdown_matches_hand_computation(client, fake_db):
    as_user("t1")
    run(_seed(fake_db))
    r = client.put("/tenants/profitability/settings", json={
        "default_hourly_rate": 60,
        "hourly_rates": {"tech1": 75},
        "overhead_rate": 15,
        "overhead_method": "per-hour",
    })
    assert r.status_code == 200, r.text

    r = client.get("/tenants/profitability/jobs/j1")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["job_id"] == "j1"
    assert b["revenue"] == "500.00" and b["revenue_source"] == "invoice"
    assert b["hours"] == "2.50" and b["hourly_rate"] == "75.00"
    assert b["labor_cost"] == "187.50"
    assert b["parts_cost"] == "36.00"
    assert b["overhead_allocated"] == "37.50"
    assert b["total_cost"] == "261.00"
    assert b["gross_profit"] == "239.00"
    assert b["margin_pct"] == "47.80"
    assert b["tech"] == {"id": "tech1", "name": "Ava"}


def test_job_without_hours_or_invoice_degrades_gracefully(client, fake_db):
    as_user("t1")
    run(fake_db.appointments.insert_one({
        "id": "j-bare", "tenant_id": "t1", "customer_name": "Solo",
        "service_name": "Tune-up", "status": "completed",
        "start_at": "2026-10-11T09:00:00+00:00",
    }))
    r = client.get("/tenants/profitability/jobs/j-bare")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["revenue"] == "0"
    assert b["revenue_source"] == "none"
    assert b["labor_cost"] == "0.00"
    assert b["parts_cost"] == "0.00"
    assert b["margin_pct"] == "0"


def test_job_404_and_tenant_isolation(client, fake_db):
    as_user("t1")
    run(_seed(fake_db))
    # other tenant's job is invisible -> 404, not a data leak
    assert client.get("/tenants/profitability/jobs/jX").status_code == 404
    assert client.get("/tenants/profitability/jobs/nope").status_code == 404

    # dashboard for t1 must not include the t2 job
    r = client.get("/tenants/profitability/dashboard?from=2026-10-01&to=2026-10-31")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["jobs"] == 1
    assert list(d["by_tech"].keys()) == ["Ava"]


def test_dashboard_bad_dates_rejected(client, fake_db):
    as_user("t1")
    assert client.get("/tenants/profitability/dashboard?from=soon&to=2026-10-31").status_code == 400
    assert client.get("/tenants/profitability/dashboard?from=2026-10-31&to=2026-10-01").status_code == 400


# ---------- router: dashboard aggregation ----------

def test_dashboard_aggregates_by_tech_and_job_type(client, fake_db):
    as_user("t1")
    run(_seed(fake_db))
    run(fake_db.appointments.insert_one({
        "id": "j2", "tenant_id": "t1", "customer_name": "Bo Lane",
        "service_name": "Furnace Install", "staff_id": "tech2", "staff": "Moe",
        "logged_hours": 8.0, "status": "completed",
        "start_at": "2026-10-12T08:00:00+00:00",
        "end_at": "2026-10-12T16:00:00+00:00",
        "completed_at": "2026-10-12T16:00:00+00:00",
    }))
    run(fake_db.invoices.insert_one({
        "id": "i2", "tenant_id": "t1", "appointment_id": "j2",
        "total_cents": 300000,
        "lines": [{"description": "Furnace", "quantity": 1, "unit_price": 3000.0,
                   "pricebook_item_id": "pb-furnace"}],
        "status": "paid",
    }))
    run(fake_db.pricebook_items.insert_one(
        {"id": "pb-furnace", "tenant_id": "t1", "name": "Furnace", "price": 3000.0, "cost": 2000.0}))
    r = client.put("/tenants/profitability/settings", json={
        "default_hourly_rate": 60, "overhead_rate": 15, "overhead_method": "per-hour"})
    assert r.status_code == 200, r.text

    r = client.get("/tenants/profitability/dashboard?from=2026-10-01&to=2026-10-31")
    assert r.status_code == 200, r.text
    d = r.json()

    # j1: rev 500, labor 2.5*60=150, parts 36, overhead 2.5*15=37.50, cost 223.50, profit 276.50
    # j2: rev 3000, labor 8*60=480, parts 2000, overhead 8*15=120, cost 2600, profit 400
    # totals: rev 3500, cost 2823.50, profit 676.50, margin 676.50/3500*100 = 19.33
    assert d["jobs"] == 2
    assert d["revenue"] == "3500.00"
    assert d["cost"] == "2823.50"
    assert d["profit"] == "676.50"
    assert d["margin_pct"] == "19.33"

    ava, moe = d["by_tech"]["Ava"], d["by_tech"]["Moe"]
    assert ava["jobs"] == 1 and ava["revenue"] == "500.00" and ava["profit"] == "276.50"
    assert moe["jobs"] == 1 and moe["revenue"] == "3000.00" and moe["profit"] == "400.00"
    assert d["by_job_type"]["AC Repair"]["revenue"] == "500.00"
    assert d["by_job_type"]["Furnace Install"]["revenue"] == "3000.00"


# ---------- router: settings RBAC ----------

def test_settings_restricted_to_owner_admin(client, fake_db):
    as_user("t1", role="tech")
    r = client.put("/tenants/profitability/settings", json={"overhead_rate": 20})
    assert r.status_code == 403, r.text

    as_user("t1", role="admin")
    r = client.put("/tenants/profitability/settings", json={"overhead_rate": 20})
    assert r.status_code == 200, r.text
    assert r.json()["overhead_rate"] == "20.00"

    # owner reads merged settings back; untouched defaults survive
    as_user("t1", role="owner")
    r = client.get("/tenants/profitability/settings")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["overhead_rate"] == "20.00"
    assert s["overhead_method"] == "per-hour"  # default preserved through merge
    assert s["default_hourly_rate"] == "45.00"

    # tech can read settings (needed for the dashboard card) but not write
    as_user("t1", role="tech")
    assert client.get("/tenants/profitability/settings").status_code == 200


def test_settings_rejects_invalid_method(client, fake_db):
    as_user("t1", role="owner")
    r = client.put("/tenants/profitability/settings", json={"overhead_method": "per-day"})
    assert r.status_code == 400, r.text
