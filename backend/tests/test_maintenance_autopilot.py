"""Tests for the Maintenance Agreement Autopilot router (Wave 5 / Phase D).

Covers plan templates, enrollment, status computation, renewal math,
idempotent visit scheduling, and the MRR-style revenue summary.
Uses the fake-mongo pattern from test_trade_packs.py — no live database.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET", "maintenance-autopilot-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "maintenance_autopilot_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import pytest  # noqa: E402

import routers.maintenance_autopilot as ma  # noqa: E402
from security import require_tenant_user  # noqa: E402


# ---------- fake mongo ----------

class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        self.docs = sorted(self.docs, key=lambda d: str(d.get(key, "")), reverse=(direction < 0))
        return self

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
        self.maintenance_plans = FakeCollection()
        self.maintenance_agreements = FakeCollection()
        self.maintenance_visits = FakeCollection()
        self.maintenance_renewals = FakeCollection()


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(ma, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(ma.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def now():
    return datetime.now(timezone.utc)


PLAN = {
    "name": "HVAC Comfort Plan",
    "visits_per_year": 2,
    "price": 240.0,
    "billing_interval": "annual",
    "trade": "hvac",
}


def make_plan(client, payload=None):
    r = client.post("/tenants/maintenance/plans", json=payload or PLAN)
    assert r.status_code == 200, r.text
    return r.json()


def enroll(client, plan_id, start_days_ago=60, customer_name="Jane Homeowner"):
    start = (now() - timedelta(days=start_days_ago)).date().isoformat()
    r = client.post("/tenants/maintenance/agreements", json={
        "plan_id": plan_id,
        "customer": {"name": customer_name, "phone": "555-0100"},
        "start_date": start,
        "equipment": [{"equipment_type": "ac_condenser", "make": "Trane", "model": "XR14"}],
    })
    assert r.status_code == 200, r.text
    return r.json()


# ---------- plan templates ----------

def test_create_plan_happy_path(client):
    as_user("t1")
    plan = make_plan(client)
    assert plan["id"]
    assert plan["tenant_id"] == "t1"
    assert plan["visits_per_year"] == 2
    assert plan["price"] == 240.0
    assert plan["billing_interval"] == "annual"
    assert plan["created_at"]


def test_create_plan_rejects_bad_billing_interval(client):
    as_user("t1")
    bad = dict(PLAN, billing_interval="weekly")
    r = client.post("/tenants/maintenance/plans", json=bad)
    assert r.status_code == 400


def test_create_plan_rejects_too_many_visits(client):
    as_user("t1")
    bad = dict(PLAN, visits_per_year=13)
    r = client.post("/tenants/maintenance/plans", json=bad)
    assert r.status_code == 422


def test_create_plan_requires_owner_or_admin(client):
    as_user("t1", role="tech")
    r = client.post("/tenants/maintenance/plans", json=PLAN)
    assert r.status_code == 403
    as_user("t1", role="admin")
    r = client.post("/tenants/maintenance/plans", json=PLAN)
    assert r.status_code == 200


def test_list_plans_is_tenant_scoped(client):
    as_user("t1")
    make_plan(client)
    make_plan(client, dict(PLAN, name="Plumbing Guard"))
    as_user("t2")
    r = client.get("/tenants/maintenance/plans")
    assert r.json() == {"plans": [], "count": 0}
    as_user("t1")
    r = client.get("/tenants/maintenance/plans")
    assert r.json()["count"] == 2
    assert {p["name"] for p in r.json()["plans"]} == {"HVAC Comfort Plan", "Plumbing Guard"}


# ---------- agreements ----------

def test_enroll_agreement_happy_path(client):
    as_user("t1")
    plan = make_plan(client)
    agr = enroll(client, plan["id"])
    assert agr["plan_id"] == plan["id"]
    assert agr["plan_name"] == "HVAC Comfort Plan"
    assert agr["customer"]["name"] == "Jane Homeowner"
    assert agr["equipment"][0]["make"] == "Trane"
    assert agr["renewal_count"] == 0
    # term math: start 60 days ago -> end is 1 year - 1 day later, still active
    start = ma._parse_date(agr["start_date"])
    end = ma._parse_date(agr["end_date"])
    assert (end - start).days == 364
    assert agr["status"] == "active"
    # $240 annual -> $20 monthly
    assert agr["monthly_value"] == 20.0


def test_enroll_requires_owner_or_admin(client):
    as_user("t1")
    plan = make_plan(client)
    as_user("t1", role="tech")
    r = client.post("/tenants/maintenance/agreements", json={
        "plan_id": plan["id"],
        "customer": {"name": "Bob"},
        "start_date": "2026-01-01",
    })
    assert r.status_code == 403


def test_enroll_unknown_plan_404(client):
    as_user("t1")
    r = client.post("/tenants/maintenance/agreements", json={
        "plan_id": "nope", "customer": {"name": "Bob"}, "start_date": "2026-01-01",
    })
    assert r.status_code == 404


def test_list_agreements_status_and_expiring_soon(client):
    as_user("t1")
    plan = make_plan(client)
    active = enroll(client, plan["id"], start_days_ago=60, customer_name="Active Ann")
    # started 340 days ago -> ends in ~25 days -> expiring
    expiring = enroll(client, plan["id"], start_days_ago=340, customer_name="Expiring Ed")
    # started 400 days ago -> expired
    old = enroll(client, plan["id"], start_days_ago=400, customer_name="Expired Eve")

    r = client.get("/tenants/maintenance/agreements")
    by_name = {a["customer"]["name"]: a["status"] for a in r.json()["agreements"]}
    assert by_name == {
        "Active Ann": "active",
        "Expiring Ed": "expiring",
        "Expired Eve": "expired",
    }

    r = client.get("/tenants/maintenance/agreements", params={"expiring_soon": "true"})
    names = [a["customer"]["name"] for a in r.json()["agreements"]]
    assert names == ["Expiring Ed"]

    r = client.get("/tenants/maintenance/agreements", params={"status": "expired"})
    assert [a["customer"]["name"] for a in r.json()["agreements"]] == ["Expired Eve"]
    assert active["id"] and expiring["id"] and old["id"]


# ---------- renewals ----------

def test_renew_extends_term_and_records(client, fake_db):
    as_user("t1")
    plan = make_plan(client)
    agr = enroll(client, plan["id"], start_days_ago=60)
    old_end = ma._parse_date(agr["end_date"])

    r = client.post(f"/tenants/maintenance/agreements/{agr['id']}/renew")
    assert r.status_code == 200
    body = r.json()
    renewed = body["agreement"]
    assert renewed["renewal_count"] == 1
    assert renewed["status"] == "active"
    # continuous term: new start = old end + 1 day; new end = +1 year - 1 day
    new_start = ma._parse_date(renewed["start_date"])
    new_end = ma._parse_date(renewed["end_date"])
    assert (new_start - (old_end + timedelta(days=1))).days == 0
    # one full year, minus the inclusive-end day (365 in leap years, 364 otherwise)
    expected_end = ma._add_years(new_start, 1) - timedelta(days=1)
    assert new_end.date() == expected_end.date()

    renewal = body["renewal"]
    assert renewal["agreement_id"] == agr["id"]
    assert renewal["renewal_number"] == 1
    assert len(fake_db.maintenance_renewals.docs) == 1


def test_renew_lapsed_agreement_restarts_from_today(client):
    as_user("t1")
    plan = make_plan(client)
    agr = enroll(client, plan["id"], start_days_ago=400)
    assert agr["status"] == "expired"

    r = client.post(f"/tenants/maintenance/agreements/{agr['id']}/renew")
    renewed = r.json()["agreement"]
    assert renewed["renewal_count"] == 1
    new_start = ma._parse_date(renewed["start_date"])
    assert new_start.date() == now().date()
    assert renewed["status"] == "active"


def test_renew_unknown_404(client):
    as_user("t1")
    r = client.post("/tenants/maintenance/agreements/nope/renew")
    assert r.status_code == 404


# ---------- visit scheduling ----------

def test_schedule_visit_due_dates_from_visits_per_year(client):
    as_user("t1")
    plan = make_plan(client)
    agr = enroll(client, plan["id"], start_days_ago=10)
    start = ma._parse_date(agr["start_date"]).date()

    r1 = client.post(f"/tenants/maintenance/agreements/{agr['id']}/schedule-visit")
    assert r1.status_code == 200
    v1 = r1.json()["visit"]
    assert r1.json()["created"] is True
    assert v1["visit_number"] == 1
    assert v1["due_date"] == start.isoformat()
    assert v1["status"] == "scheduled"
    assert v1["source"] == "maintenance-autopilot"

    r2 = client.post(f"/tenants/maintenance/agreements/{agr['id']}/schedule-visit")
    v2 = r2.json()["visit"]
    assert v2["visit_number"] == 2
    # 365 / 2 = 182.5 days later for a 2-visits-per-year plan
    expected = (start + timedelta(days=182.5)).isoformat()
    assert v2["due_date"] == expected[:10] or v2["due_date"] == expected
    assert v2["id"] != v1["id"]


def test_schedule_visit_is_idempotent(client, fake_db):
    """Scheduling past the last period never double-books the same visit."""
    as_user("t1")
    plan = make_plan(client, dict(PLAN, visits_per_year=1))
    agr = enroll(client, plan["id"])

    first = client.post(f"/tenants/maintenance/agreements/{agr['id']}/schedule-visit")
    assert first.json()["created"] is True
    assert len(fake_db.maintenance_visits.docs) == 1

    second = client.post(f"/tenants/maintenance/agreements/{agr['id']}/schedule-visit")
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["visit"]["id"] == first.json()["visit"]["id"]
    assert len(fake_db.maintenance_visits.docs) == 1


def test_schedule_visit_rejects_expired(client):
    as_user("t1")
    plan = make_plan(client)
    agr = enroll(client, plan["id"], start_days_ago=400)
    assert agr["status"] == "expired"
    r = client.post(f"/tenants/maintenance/agreements/{agr['id']}/schedule-visit")
    assert r.status_code == 400


def test_visits_list_for_agreement(client):
    as_user("t1")
    plan = make_plan(client)
    agr = enroll(client, plan["id"])
    client.post(f"/tenants/maintenance/agreements/{agr['id']}/schedule-visit")
    r = client.get(f"/tenants/maintenance/agreements/{agr['id']}/visits")
    assert r.status_code == 200
    assert r.json()["count"] == 1


# ---------- revenue ----------

def test_revenue_summary_mrr_math(client):
    as_user("t1")
    # $10/mo monthly plan + $120/yr annual plan + $90/qtr quarterly plan
    make_plan(client, dict(PLAN, name="Monthly", price=10.0, billing_interval="monthly", visits_per_year=1))
    make_plan(client, dict(PLAN, name="Annual", price=120.0, billing_interval="annual", visits_per_year=1))
    make_plan(client, dict(PLAN, name="Quarterly", price=90.0, billing_interval="quarterly", visits_per_year=1))
    plans = {p["name"]: p for p in client.get("/tenants/maintenance/plans").json()["plans"]}
    enroll(client, plans["Monthly"]["id"], customer_name="M1")
    enroll(client, plans["Annual"]["id"], customer_name="M2")
    enroll(client, plans["Quarterly"]["id"], customer_name="M3")
    # one expiring: started 340 days ago
    enroll(client, plans["Monthly"]["id"], start_days_ago=340, customer_name="Expiring")

    r = client.get("/tenants/maintenance/revenue")
    assert r.status_code == 200
    body = r.json()
    # 10 + 10 + 30 active monthly value, + 10 expiring = 60 MRR
    assert body["active_agreements"] == 3
    assert body["expiring_agreements"] == 1
    assert body["monthly_recurring_value"] == 60.0
    assert body["annual_recurring_value"] == 720.0
    assert body["total_agreements"] == 4
    # renewal pipeline holds the expiring agreement ranked by value
    assert len(body["renewal_pipeline"]) == 1
    assert body["renewal_pipeline"][0]["customer_name"] == "Expiring"
    assert body["renewal_pipeline_monthly_value"] == 10.0
    assert body["by_trade"]["hvac"]["agreements"] == 4
