"""Unit tests for warranty & callback tracking (routers/warranty.py).

Zero network, zero database: a small FastAPI app with only the warranty
router, a fake in-memory Mongo, and an overridden auth dependency.

Coverage:
- Coverage math: in-warranty / expired / upcoming, and labor-by-job lookups.
- Repeat-visit detection: 2 visits in 60 days -> open callback, 3rd in 60 days
  -> escalated; exactly 60 days counts as in-window; 61 days does not.
- Manager resolution flips callbacks to resolved (idempotent).
- Callback stats: overall rate, by tech, by equipment type, monthly trend.
- Tenant isolation: no cross-tenant reads on warranties, coverage, callbacks.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "warranty-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "warranty_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.warranty as w_mod  # noqa: E402
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
            new = {k: v for k, v in query.items() if not isinstance(v, dict)}
            for k, v in update.get("$set", {}).items():
                new[k] = v
            self.docs.append(new)
            matched = 1
        return SimpleNamespace(matched_count=matched, modified_count=matched)


class FakeDB:
    def __init__(self):
        self.warranties = FakeCollection()
        self.equipment = FakeCollection()
        self.equipment_service_events = FakeCollection()
        self.warranty_callback_resolutions = FakeCollection()


# ---------- fixtures ----------

current_user = {}
_db = None
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def fake_db():
    global _db
    _db = FakeDB()
    return _db


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(w_mod, "get_db", lambda: fake_db)
    monkeypatch.setattr(w_mod, "_utcnow", lambda: NOW)
    app = FastAPI()
    app.include_router(w_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def iso(dt):
    return dt.isoformat()


def make_equipment(db, tid, eq_id="eq-1", eq_type="ac_condenser",
                   customer_id="c-1", customer_name="Jane Doe"):
    db.equipment.docs.append({
        "id": eq_id, "tenant_id": tid, "customer_id": customer_id,
        "customer_name": customer_name, "location_label": "Attic unit",
        "equipment_type": eq_type, "make": "Trane", "model": "XR16",
        "active": True, "service_event_count": 0,
    })
    return eq_id


def make_event(db, tid, eq_id, dt, event_id, tech="Tech A", desc="repair", job_id=None):
    db.equipment_service_events.docs.append({
        "id": event_id, "tenant_id": tid, "equipment_id": eq_id,
        "service_date": iso(dt), "job_id": job_id,
        "description": desc, "tech": tech, "created_at": iso(dt),
    })
    return event_id


def make_warranty(client, tid, payload):
    as_user(tid)
    r = client.post("/warranty/warranties", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# ---------- warranty CRUD + coverage math ----------

def test_create_warranty_returns_201_with_computed_status(client, fake_db):
    w = make_warranty(client, "t1", {
        "customer_id": "c-1", "customer_name": "Jane Doe",
        "equipment_id": "eq-1", "coverage_type": "equipment",
        "start_date": "2026-01-01", "end_date": "2026-12-31",
        "terms": "Parts + labor, 1 year",
    })
    assert w["status"] == "active"          # NOW is 2026-10-15, inside the range
    assert w["days_remaining"] == 77        # 2026-12-31 - 2026-10-15
    assert w["coverage_type"] == "equipment"
    assert w["id"]


def test_coverage_math_active_expired_upcoming(client, fake_db):
    as_user("t1")
    active = make_warranty(client, "t1", {"equipment_id": "eq-a", "coverage_type": "equipment",
                                         "start_date": "2026-01-01", "end_date": "2026-12-31"})
    expired = make_warranty(client, "t1", {"equipment_id": "eq-b", "coverage_type": "equipment",
                                           "start_date": "2025-01-01", "end_date": "2025-12-31"})
    upcoming = make_warranty(client, "t1", {"equipment_id": "eq-c", "coverage_type": "equipment",
                                            "start_date": "2026-12-01", "end_date": "2027-12-01"})
    assert active["status"] == "active"
    assert expired["status"] == "expired"
    assert expired["days_remaining"] is None
    assert upcoming["status"] == "upcoming"


def test_coverage_endpoint_covered_and_expires(client, fake_db):
    make_warranty(client, "t1", {"equipment_id": "eq-1", "coverage_type": "equipment",
                                 "start_date": "2026-01-01", "end_date": "2026-12-31"})
    as_user("t1")
    body = client.get("/warranty/coverage", params={"equipment_id": "eq-1"}).json()
    assert body["covered"] is True
    assert body["expires"] == "2026-12-31"
    assert body["days_remaining"] == 77
    assert len(body["warranties"]) == 1


def test_coverage_endpoint_expired_not_covered(client, fake_db):
    make_warranty(client, "t1", {"equipment_id": "eq-1", "coverage_type": "equipment",
                                 "start_date": "2025-01-01", "end_date": "2025-12-31"})
    as_user("t1")
    body = client.get("/warranty/coverage", params={"equipment_id": "eq-1"}).json()
    assert body["covered"] is False
    assert body["expires"] is None
    # the expired warranty is still listed for context
    assert body["warranties"][0]["status"] == "expired"


def test_coverage_no_warranty_means_not_covered(client, fake_db):
    as_user("t1")
    body = client.get("/warranty/coverage", params={"equipment_id": "eq-ghost"}).json()
    assert body["covered"] is False
    assert body["warranties"] == []


def test_labor_warranty_scoped_to_job(client, fake_db):
    make_warranty(client, "t1", {"coverage_type": "labor", "job_id": "job-9",
                                 "customer_id": "c-1",
                                 "start_date": "2026-10-01", "end_date": "2027-10-01",
                                 "terms": "90-day labor warranty on install"})
    as_user("t1")
    body = client.get("/warranty/coverage", params={"job_id": "job-9"}).json()
    assert body["covered"] is True
    assert body["warranties"][0]["coverage_type"] == "labor"
    # a different job is not covered
    body = client.get("/warranty/coverage", params={"job_id": "job-other"}).json()
    assert body["covered"] is False


def test_labor_warranty_requires_job_id(client, fake_db):
    as_user("t1")
    r = client.post("/warranty/warranties", json={
        "coverage_type": "labor", "start_date": "2026-01-01", "end_date": "2026-12-31"})
    assert r.status_code == 400


def test_warranty_requires_equipment_or_job(client, fake_db):
    as_user("t1")
    r = client.post("/warranty/warranties", json={
        "coverage_type": "equipment", "start_date": "2026-01-01", "end_date": "2026-12-31"})
    assert r.status_code == 400


def test_warranty_rejects_end_before_start(client, fake_db):
    as_user("t1")
    r = client.post("/warranty/warranties", json={
        "equipment_id": "eq-1", "coverage_type": "equipment",
        "start_date": "2026-12-31", "end_date": "2026-01-01"})
    assert r.status_code == 400


def test_warranty_rejects_bad_coverage_type(client, fake_db):
    as_user("t1")
    r = client.post("/warranty/warranties", json={
        "equipment_id": "eq-1", "coverage_type": "lifetime",
        "start_date": "2026-01-01", "end_date": "2026-12-31"})
    assert r.status_code == 400


def test_coverage_requires_a_target(client, fake_db):
    as_user("t1")
    r = client.get("/warranty/coverage")
    assert r.status_code == 400


def test_warranty_list_filters(client, fake_db):
    make_warranty(client, "t1", {"equipment_id": "eq-1", "customer_id": "c-1",
                                 "customer_name": "Jane Doe", "coverage_type": "equipment",
                                 "start_date": "2026-01-01", "end_date": "2026-12-31"})
    make_warranty(client, "t1", {"equipment_id": "eq-2", "customer_id": "c-2",
                                 "customer_name": "John Smith", "coverage_type": "parts",
                                 "start_date": "2025-01-01", "end_date": "2025-06-30"})
    as_user("t1")

    assert client.get("/warranty/warranties").json()["count"] == 2
    body = client.get("/warranty/warranties", params={"customer": "jane"}).json()
    assert body["count"] == 1 and body["warranties"][0]["customer_name"] == "Jane Doe"
    body = client.get("/warranty/warranties", params={"equipment": "eq-2"}).json()
    assert body["count"] == 1 and body["warranties"][0]["equipment_id"] == "eq-2"
    body = client.get("/warranty/warranties", params={"status": "expired"}).json()
    assert body["count"] == 1 and body["warranties"][0]["status"] == "expired"
    body = client.get("/warranty/warranties", params={"status": "active"}).json()
    assert body["count"] == 1 and body["warranties"][0]["status"] == "active"
    r = client.get("/warranty/warranties", params={"status": "bogus"})
    assert r.status_code == 400


def test_warranty_get_and_update(client, fake_db):
    w = make_warranty(client, "t1", {"equipment_id": "eq-1", "coverage_type": "equipment",
                                     "start_date": "2026-01-01", "end_date": "2026-12-31"})
    as_user("t1")
    fetched = client.get(f"/warranty/warranties/{w['id']}").json()
    assert fetched["id"] == w["id"]

    r = client.put(f"/warranty/warranties/{w['id']}", json={"terms": "updated terms"})
    assert r.status_code == 200
    assert r.json()["terms"] == "updated terms"

    r = client.put(f"/warranty/warranties/{w['id']}", json={"end_date": "2025-01-01"})
    assert r.status_code == 400  # would put end before start

    r = client.get("/warranty/warranties/nope")
    assert r.status_code == 404


# ---------- callback detection ----------

def _seed_unit(client, fake_db, tid, eq_id="eq-1", dates=None, tech="Tech A"):
    """Create equipment + one service event per datetime in `dates`."""
    make_equipment(fake_db, tid, eq_id=eq_id)
    dates = dates or []
    for i, dt in enumerate(dates):
        make_event(fake_db, tid, eq_id, dt, f"{eq_id}-ev-{i}", tech=tech)
    as_user(tid)
    return client.get("/warranty/callbacks").json()


def test_single_visit_is_not_a_callback(client, fake_db):
    body = _seed_unit(client, fake_db, "t1", dates=[NOW - timedelta(days=10)])
    assert body["callbacks"] == [] and body["count"] == 0


def test_second_visit_in_60d_is_open_callback_not_escalated(client, fake_db):
    body = _seed_unit(client, fake_db, "t1",
                      dates=[NOW - timedelta(days=30), NOW - timedelta(days=5)])
    assert body["count"] == 1
    cb = body["callbacks"][0]
    assert cb["status"] == "open"
    assert cb["visits_in_window"] == 2
    assert cb["equipment_id"] == "eq-1"
    assert cb["tech"] == "Tech A"
    assert body["open"] == 1 and body["escalated"] == 0


def test_third_visit_in_60d_escalates(client, fake_db):
    body = _seed_unit(client, fake_db, "t1",
                      dates=[NOW - timedelta(days=50), NOW - timedelta(days=25), NOW])
    assert body["count"] == 2
    statuses = [c["status"] for c in body["callbacks"]]
    assert statuses == ["escalated", "open"]  # newest first
    newest = body["callbacks"][0]
    assert newest["visits_in_window"] == 3
    assert body["escalated"] == 1 and body["open"] == 1


def test_repeat_visit_outside_window_is_not_a_callback(client, fake_db):
    body = _seed_unit(client, fake_db, "t1",
                      dates=[NOW - timedelta(days=100), NOW - timedelta(days=5)])
    assert body["count"] == 0  # 95 days apart -> fresh visit, no flag


def test_boundary_exactly_60_days_counts(client, fake_db):
    """Exactly 60 days apart is still within the window (inclusive)."""
    body = _seed_unit(client, fake_db, "t1",
                      dates=[NOW - timedelta(days=60), NOW])
    assert body["count"] == 1
    assert body["callbacks"][0]["status"] == "open"


def test_boundary_61_days_does_not_count(client, fake_db):
    body = _seed_unit(client, fake_db, "t1",
                      dates=[NOW - timedelta(days=61), NOW])
    assert body["count"] == 0


def test_escalation_uses_rolling_window_not_chain(client, fake_db):
    """Visits day-100, day-50, today: today's prior window holds day-50 only
    (day-100 is 100 days out), so it is open, not escalated."""
    body = _seed_unit(client, fake_db, "t1",
                      dates=[NOW - timedelta(days=100), NOW - timedelta(days=50), NOW])
    assert body["count"] == 2
    assert [c["status"] for c in body["callbacks"]] == ["open", "open"]


def test_callbacks_scoped_to_equipment_unit(client, fake_db):
    """Visits to different units are independent — no cross-unit flagging."""
    make_equipment(fake_db, "t1", eq_id="eq-1")
    make_equipment(fake_db, "t1", eq_id="eq-2")
    make_event(fake_db, "t1", "eq-1", NOW - timedelta(days=10), "eq-1-ev-0")
    make_event(fake_db, "t1", "eq-2", NOW - timedelta(days=5), "eq-2-ev-0")
    as_user("t1")
    body = client.get("/warranty/callbacks").json()
    assert body["count"] == 0


def test_resolve_callback_requires_manager_review_and_is_idempotent(client, fake_db):
    body = _seed_unit(client, fake_db, "t1",
                      dates=[NOW - timedelta(days=50), NOW - timedelta(days=25), NOW])
    cb_id = body["callbacks"][0]["id"]
    assert body["callbacks"][0]["status"] == "escalated"

    r = client.post(f"/warranty/callbacks/{cb_id}/resolve",
                    json={"note": "Replaced capacitor under warranty", "resolved_by": "u-t1"})
    assert r.status_code == 200
    assert r.json()["status"] == "resolved"
    assert r.json()["resolution_note"] == "Replaced capacitor under warranty"

    # visible as resolved afterwards
    body = client.get("/warranty/callbacks").json()
    assert body["callbacks"][0]["status"] == "resolved"
    assert body["escalated"] == 0

    # idempotent: resolving again returns the same resolved record
    r = client.post(f"/warranty/callbacks/{cb_id}/resolve", json={"note": "again"})
    assert r.status_code == 200
    assert r.json()["status"] == "resolved"

    # unknown callback -> 404
    r = client.post("/warranty/callbacks/eq-1:ghost-event/resolve", json={})
    assert r.status_code == 404


def test_callbacks_filter_by_status_and_tech(client, fake_db):
    _seed_unit(client, fake_db, "t1",
               dates=[NOW - timedelta(days=50), NOW - timedelta(days=25), NOW],
               tech="Tech A")
    # second unit, one open callback by another tech
    make_equipment(fake_db, "t1", eq_id="eq-2")
    make_event(fake_db, "t1", "eq-2", NOW - timedelta(days=20), "eq-2-ev-0", tech="Tech B")
    make_event(fake_db, "t1", "eq-2", NOW - timedelta(days=2), "eq-2-ev-1", tech="Tech B")
    as_user("t1")

    assert client.get("/warranty/callbacks", params={"status": "escalated"}).json()["count"] == 1
    assert client.get("/warranty/callbacks", params={"status": "open"}).json()["count"] == 2
    body = client.get("/warranty/callbacks", params={"tech": "tech b"}).json()
    assert body["count"] == 1 and body["callbacks"][0]["tech"] == "Tech B"


# ---------- stats ----------

def test_stats_callback_rates_and_trend(client, fake_db):
    # unit 1: 3 visits (2 callbacks, 1 escalated) by Tech A, type ac_condenser
    make_equipment(fake_db, "t1", eq_id="eq-1", eq_type="ac_condenser")
    make_event(fake_db, "t1", "eq-1", NOW - timedelta(days=50), "e1-0", tech="Tech A")
    make_event(fake_db, "t1", "eq-1", NOW - timedelta(days=25), "e1-1", tech="Tech A")
    make_event(fake_db, "t1", "eq-1", NOW, "e1-2", tech="Tech A")
    # unit 2: 2 visits (1 callback) by Tech B, type furnace, 3 months ago
    three_mo = NOW - timedelta(days=95)
    make_equipment(fake_db, "t1", eq_id="eq-2", eq_type="furnace")
    make_event(fake_db, "t1", "eq-2", three_mo - timedelta(days=10), "e2-0", tech="Tech B")
    make_event(fake_db, "t1", "eq-2", three_mo, "e2-1", tech="Tech B")
    as_user("t1")

    body = client.get("/warranty/stats").json()
    assert body["total_visits"] == 5
    assert body["total_callbacks"] == 3
    assert body["callback_rate_pct"] == 60.0
    assert body["escalated_open"] == 1

    by_tech = {r["tech"]: r for r in body["by_tech"]}
    assert by_tech["Tech A"]["visits"] == 3 and by_tech["Tech A"]["callbacks"] == 2
    assert by_tech["Tech A"]["rate_pct"] == 66.7
    assert by_tech["Tech B"]["visits"] == 2 and by_tech["Tech B"]["callbacks"] == 1

    by_type = {r["equipment_type"]: r for r in body["by_equipment_type"]}
    assert by_type["ac_condenser"]["rate_pct"] == 66.7
    assert by_type["furnace"]["rate_pct"] == 50.0

    trend = {m["month"]: m for m in body["trend"]}
    assert len(body["trend"]) == 6
    assert trend["2026-10"]["visits"] == 1 and trend["2026-10"]["callbacks"] == 1
    assert trend["2026-09"]["visits"] == 1 and trend["2026-09"]["callbacks"] == 1
    assert trend["2026-08"]["visits"] == 1 and trend["2026-08"]["callbacks"] == 0
    assert trend["2026-07"]["visits"] == 2 and trend["2026-07"]["callbacks"] == 1


def test_stats_empty_tenant(client, fake_db):
    as_user("t1")
    body = client.get("/warranty/stats").json()
    assert body["total_visits"] == 0
    assert body["callback_rate_pct"] == 0.0
    assert body["by_tech"] == [] and body["trend"] and len(body["trend"]) == 6
    assert body["warranties"] == {"active": 0, "upcoming": 0, "expired": 0, "expiring_30d": 0}


def test_stats_warranty_inventory_counts(client, fake_db):
    make_warranty(client, "t1", {"equipment_id": "eq-1", "coverage_type": "equipment",
                                 "start_date": "2026-01-01", "end_date": "2026-10-20"})  # expiring soon
    make_warranty(client, "t1", {"equipment_id": "eq-2", "coverage_type": "equipment",
                                 "start_date": "2026-01-01", "end_date": "2027-01-01"})  # active
    make_warranty(client, "t1", {"equipment_id": "eq-3", "coverage_type": "equipment",
                                 "start_date": "2025-01-01", "end_date": "2025-12-31"})  # expired
    as_user("t1")
    inv = client.get("/warranty/stats").json()["warranties"]
    assert inv == {"active": 2, "upcoming": 0, "expired": 1, "expiring_30d": 1}


# ---------- tenant isolation ----------

def test_tenant_isolation(client, fake_db):
    make_warranty(client, "tenant-a", {"equipment_id": "eq-1", "coverage_type": "equipment",
                                       "start_date": "2026-01-01", "end_date": "2026-12-31",
                                       "customer_id": "c-a"})
    make_equipment(fake_db, "tenant-a", eq_id="eq-1")
    make_event(fake_db, "tenant-a", "eq-1", NOW - timedelta(days=30), "a-0")
    make_event(fake_db, "tenant-a", "eq-1", NOW - timedelta(days=5), "a-1")

    as_user("tenant-b")
    assert client.get("/warranty/warranties").json()["count"] == 0
    assert client.get("/warranty/coverage", params={"equipment_id": "eq-1"}).json()["covered"] is False
    assert client.get("/warranty/callbacks").json()["count"] == 0
    body = client.get("/warranty/stats").json()
    assert body["total_visits"] == 0 and body["total_callbacks"] == 0
