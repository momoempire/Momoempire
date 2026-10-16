"""Unit tests for tech time tracking / timesheets (routers/time_tracking.py).

Zero network, zero database: a small FastAPI app with only the time-tracking
router, a fake in-memory Mongo, and an overridden auth dependency.

Coverage (the non-negotiables):
- Double clock-in is blocked (409); one open entry per tech.
- Hours math, including overnight shifts.
- OT split: exactly 40h -> no OT; 41h -> 1h OT.
- Approved entries are immutable (PUT -> 409).
- Tenant isolation: entries never cross tenants.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "timetracking-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "timetracking_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.time_tracking as tt_mod  # noqa: E402
from security import require_tenant_owner_or_admin, require_tenant_user  # noqa: E402


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


def _apply_update(doc, update):
    for k, v in update.get("$set", {}).items():
        doc[k] = v
    for k, v in update.get("$push", {}).items():
        doc.setdefault(k, []).append(v)


class FakeCollection:
    def __init__(self):
        self.docs = []

    def __getitem__(self, name):
        raise KeyError(name)

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
                _apply_update(d, update)
                break
        return SimpleNamespace(matched_count=matched, modified_count=matched)


class FakeDB:
    def __init__(self):
        self.tenants = FakeCollection()
        self.schedule_staff = FakeCollection()
        self.time_entries = FakeCollection()

    def __getitem__(self, name):
        return getattr(self, name)


# ---------- fixtures ----------

current_user = {}


@pytest.fixture
def fake_db():
    db = FakeDB()
    db.tenants.docs.append({"id": "t1", "name": "Shop One"})
    db.tenants.docs.append({"id": "t2", "name": "Shop Two"})
    db.schedule_staff.docs.append({"id": "tech1", "tenant_id": "t1", "name": "Al Tech"})
    db.schedule_staff.docs.append({"id": "tech2", "tenant_id": "t1", "name": "Bo Fix"})
    db.schedule_staff.docs.append({"id": "tech9", "tenant_id": "t2", "name": "Cy Other"})
    return db


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(tt_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(tt_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    app.dependency_overrides[require_tenant_owner_or_admin] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


# ---------- clock in / out ----------

def test_clock_in_creates_open_entry(client, fake_db):
    as_user("t1")
    r = client.post("/time-tracking/clock-in", json={"tech_id": "tech1"})
    assert r.status_code == 200
    e = r.json()["entry"]
    assert e["tech_id"] == "tech1"
    assert e["tech_name"] == "Al Tech"
    assert e["clock_out"] is None
    assert e["hours"] is None


def test_double_clock_in_blocked(client, fake_db):
    """One open entry per tech: the second clock-in is rejected with 409."""
    as_user("t1")
    r = client.post("/time-tracking/clock-in", json={"tech_id": "tech1"})
    assert r.status_code == 200
    r = client.post("/time-tracking/clock-in", json={"tech_id": "tech1"})
    assert r.status_code == 409
    # Other techs are unaffected.
    r = client.post("/time-tracking/clock-in", json={"tech_id": "tech2"})
    assert r.status_code == 200


def test_clock_out_computes_hours(client, fake_db):
    as_user("t1")
    client.post("/time-tracking/clock-in", json={
        "tech_id": "tech1", "at": "2026-10-12T09:00:00+00:00"})
    r = client.post("/time-tracking/clock-out", json={
        "tech_id": "tech1", "at": "2026-10-12T13:30:00+00:00"})
    assert r.status_code == 200
    assert r.json()["entry"]["hours"] == 4.5


def test_overnight_shift_hours(client, fake_db):
    """A shift crossing midnight (22:00 -> 02:00) yields 4 hours."""
    as_user("t1")
    client.post("/time-tracking/clock-in", json={
        "tech_id": "tech1", "at": "2026-10-12T22:00:00+00:00"})
    r = client.post("/time-tracking/clock-out", json={
        "tech_id": "tech1", "at": "2026-10-13T02:00:00+00:00"})
    assert r.status_code == 200
    assert r.json()["entry"]["hours"] == 4.0


def test_clock_out_without_open_entry_409(client, fake_db):
    as_user("t1")
    r = client.post("/time-tracking/clock-out", json={"tech_id": "tech1"})
    assert r.status_code == 409


def test_clock_in_unknown_tech_404(client, fake_db):
    as_user("t1")
    r = client.post("/time-tracking/clock-in", json={"tech_id": "ghost"})
    assert r.status_code == 404


def _shift(client, tech_id, day, start, end):
    """One clocked-in/out shift, all UTC ISO. ``day`` is a full 'YYYY-MM-DD'."""
    r = client.post("/time-tracking/clock-in", json={
        "tech_id": tech_id, "at": f"{day}T{start}:00+00:00"})
    assert r.status_code == 200, r.text
    r = client.post("/time-tracking/clock-out", json={
        "tech_id": tech_id, "at": f"{day}T{end}:00+00:00"})
    assert r.status_code == 200, r.text


# ---------- overtime ----------

def test_ot_threshold_exactly_40h(client, fake_db):
    """40.0h total at the 40h threshold -> zero overtime."""
    as_user("t1")
    for day in ("2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"):  # Mon-Fri 8h each
        _shift(client, "tech1", day, "09:00", "17:00")
    body = client.get("/time-tracking/timesheet",
                      params={"tech_id": "tech1", "week": "2026-W41"}).json()
    assert body["total_hours"] == 40.0
    assert body["overtime_hours"] == 0.0
    assert body["regular_hours"] == 40.0


def test_ot_split_over_threshold(client, fake_db):
    """41h at the 40h threshold -> 1h overtime."""
    as_user("t1")
    for day in ("2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"):
        _shift(client, "tech1", day, "09:00", "17:00")
    _shift(client, "tech1", "2026-10-10", "09:00", "10:00")  # Saturday 1h
    body = client.get("/time-tracking/timesheet",
                      params={"tech_id": "tech1", "week": "2026-W41"}).json()
    assert body["total_hours"] == 41.0
    assert body["overtime_hours"] == 1.0
    assert body["regular_hours"] == 40.0


def test_ot_threshold_configurable_per_tenant(client, fake_db):
    """A tenant can lower the OT threshold to 35h."""
    as_user("t1")
    r = client.put("/time-tracking/settings", json={"ot_weekly_hours": 35.0})
    assert r.status_code == 200
    assert r.json()["ot_weekly_hours"] == 35.0
    for day in ("2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"):
        _shift(client, "tech1", day, "09:00", "17:00")
    body = client.get("/time-tracking/timesheet",
                      params={"tech_id": "tech1", "week": "2026-W41"}).json()
    assert body["total_hours"] == 40.0
    assert body["overtime_hours"] == 5.0
    assert body["regular_hours"] == 35.0


def test_timesheet_daily_totals(client, fake_db):
    as_user("t1")
    _shift(client, "tech1", "2026-10-05", "09:00", "13:00")  # Monday 4h
    _shift(client, "tech1", "2026-10-05", "14:00", "18:00")  # Monday 4h
    body = client.get("/time-tracking/timesheet",
                      params={"tech_id": "tech1", "week": "2026-10-07"}).json()
    monday = [d for d in body["days"] if d["date"] == "2026-10-05"][0]
    assert monday["hours"] == 8.0
    assert monday["entry_count"] == 2
    assert body["week_start"] == "2026-10-05"
    assert body["week_end"] == "2026-10-11"


# ---------- approval + immutability ----------

def _approve_all(client, entry_ids):
    r = client.post("/time-tracking/timesheet/approve", json={"entry_ids": entry_ids})
    assert r.status_code == 200, r.text
    return r.json()


def test_approved_entries_immutable(client, fake_db):
    """PUT on an approved entry -> 409; unapproved entries stay editable."""
    as_user("t1")
    _shift(client, "tech1", "2026-10-05", "09:00", "13:00")
    entries = client.get("/time-tracking/entries", params={"tech_id": "tech1"}).json()["entries"]
    eid = entries[0]["id"]

    # Manager correction works before approval (with audit note).
    r = client.put(f"/time-tracking/entries/{eid}", json={
        "clock_out": "2026-10-05T14:00:00+00:00", "correction_note": "tech forgot to clock out"})
    assert r.status_code == 200, r.text
    assert r.json()["entry"]["hours"] == 5.0
    assert r.json()["entry"]["correction_history"][0]["note"] == "tech forgot to clock out"

    _approve_all(client, [eid])
    assert client.get("/time-tracking/entries",
                      params={"tech_id": "tech1"}).json()["entries"][0]["approved"] is True

    # After approval: locked.
    r = client.put(f"/time-tracking/entries/{eid}", json={
        "note": "post-approval edit", "correction_note": "too late"})
    assert r.status_code == 409


def test_open_entry_cannot_be_approved(client, fake_db):
    as_user("t1")
    client.post("/time-tracking/clock-in", json={"tech_id": "tech1"})
    entries = client.get("/time-tracking/entries",
                         params={"tech_id": "tech1"}).json()["entries"]
    r = client.post("/time-tracking/timesheet/approve",
                    json={"entry_ids": [entries[0]["id"]]})
    assert r.status_code == 409


def test_payroll_export_per_tech_totals(client, fake_db):
    """Payroll-ready shape: per-tech regular/OT hours for the month."""
    as_user("t1")
    # 40h in week 42 -> all regular; 2h extra in week 43 is fine, no OT here.
    for day in ("2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"):
        _shift(client, "tech1", day, "09:00", "17:00")
    _shift(client, "tech2", "2026-10-05", "09:00", "13:00")
    for eid in [e["id"] for e in
                client.get("/time-tracking/entries").json()["entries"]]:
        _approve_all(client, [eid])

    body = client.get("/time-tracking/payroll-export",
                      params={"period": "2026-10"}).json()
    by_tech = {r["tech_id"]: r for r in body["techs"]}
    assert by_tech["tech1"]["regular_hours"] == 40.0
    assert by_tech["tech1"]["overtime_hours"] == 0.0
    assert by_tech["tech1"]["approved_count"] == 5
    assert by_tech["tech2"]["total_hours"] == 4.0
    assert body["totals"]["total_hours"] == 44.0
    assert body["period_start"] == "2026-10-01"
    assert body["ot_weekly_hours"] == 40.0


# ---------- tenant isolation ----------

def test_tenant_isolation(client, fake_db):
    """Tenant B's entries are invisible to tenant A, and vice versa."""
    as_user("t1")
    _shift(client, "tech1", "2026-10-05", "09:00", "13:00")
    as_user("t2")
    _shift(client, "tech9", "2026-10-05", "09:00", "18:00")

    as_user("t1")
    body = client.get("/time-tracking/entries").json()
    assert body["count"] == 1
    assert all(e["tech_id"] == "tech1" for e in body["entries"])
    assert "tech9" not in str(body)

    # Cross-tenant approval of t2's entry id must not touch t2's data.
    t2_eid = [e["id"] for e in fake_db.time_entries.docs
              if e["tenant_id"] == "t2"][0]
    r = client.post("/time-tracking/timesheet/approve", json={"entry_ids": [t2_eid]})
    assert r.json()["count"] == 0
    as_user("t2")
    t2_entries = client.get("/time-tracking/entries").json()["entries"]
    assert t2_entries[0]["approved"] is False

    # Cross-tenant correction -> 404.
    as_user("t1")
    r = client.put(f"/time-tracking/entries/{t2_eid}",
                   json={"correction_note": "sneaky"})
    assert r.status_code == 404

    # Tenant A's staff is not valid for tenant B's clock-in.
    as_user("t2")
    r = client.post("/time-tracking/clock-in", json={"tech_id": "tech1"})
    assert r.status_code == 404


def test_entries_filter_by_date_range(client, fake_db):
    as_user("t1")
    _shift(client, "tech1", "2026-10-05", "09:00", "13:00")
    _shift(client, "tech1", "2026-10-08", "09:00", "13:00")
    body = client.get("/time-tracking/entries",
                      params={"from": "2026-10-08", "to": "2026-10-08"}).json()
    assert body["count"] == 1
    assert body["entries"][0]["clock_in"].startswith("2026-10-08")
