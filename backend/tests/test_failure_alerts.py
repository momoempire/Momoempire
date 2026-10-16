"""Tests for routers/failure_alerts.py (predictive equipment-failure alerts).

Uses a fake in-memory mongo (same pattern as test_reputation.py): no live
database. Covers scan alert generation for at-risk equipment, healthy units
producing nothing, acknowledge/dismiss flows with the weight-feedback loop,
tenant isolation, and risk-breakdown math.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "failure-alerts-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "failure_alerts_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.failure_alerts as fa_mod  # noqa: E402
from security import require_tenant_user, require_tenant_owner_or_admin  # noqa: E402


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
        self.failure_alerts = FakeCollection()
        self.failure_alert_weights = FakeCollection()
        self.failure_alert_scans = FakeCollection()
        self.equipment = FakeCollection()
        self.equipment_service_events = FakeCollection()
        self.appointments = FakeCollection()
        self.tenants = FakeCollection()


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(fa_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(fa_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    app.dependency_overrides[require_tenant_owner_or_admin] = lambda: _guard_owner(current_user)
    return TestClient(app)


def _guard_owner(user):
    if user.get("role") not in {"owner", "admin"}:
        from fastapi import HTTPException
        raise HTTPException(403, "Owner/admin role required")
    return user


def as_user(tenant_id="t1", role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def seed_equipment(fake_db, eid, tenant_id="t1", eq_type="ac_condenser",
                   install_days_ago=30, last_service_days_ago=10,
                   customer_id="c1", customer_name="Kim", active=True,
                   make="Trane", model="XR14"):
    fake_db.equipment.docs.append({
        "id": eid, "tenant_id": tenant_id, "customer_id": customer_id,
        "customer_name": customer_name, "location_label": "Attic",
        "equipment_type": eq_type, "make": make, "model": model,
        "serial_number": "SN-1",
        "install_date": _iso_days_ago(install_days_ago),
        "active": active, "service_event_count": 0,
        "created_at": _iso_days_ago(install_days_ago + 5),
        "updated_at": _iso_days_ago(last_service_days_ago),
    })
    # A baseline service event = "last service" (unless the caller adds more).
    if last_service_days_ago is not None:
        seed_service_event(fake_db, eid, tenant_id,
                           days_ago=last_service_days_ago,
                           description="Annual tune-up, all good")


def seed_service_event(fake_db, eid, tenant_id="t1", days_ago=10,
                       description="Tune-up", job_id=None):
    fake_db.equipment_service_events.docs.append({
        "id": f"ev-{eid}-{days_ago}-{len(fake_db.equipment_service_events.docs)}",
        "tenant_id": tenant_id, "equipment_id": eid,
        "service_date": _iso_days_ago(days_ago),
        "job_id": job_id, "description": description, "tech": "Sam",
        "created_at": _iso_days_ago(days_ago),
    })


def seed_job(fake_db, jid, tenant_id="t1", customer_id="c1",
             days_ago=5, status="completed", notes=""):
    fake_db.appointments.docs.append({
        "id": jid, "tenant_id": tenant_id, "customer_id": customer_id,
        "customer_name": "Kim", "service_name": "Diagnostic visit",
        "status": status, "notes": notes,
        "start_at": _iso_days_ago(days_ago),
        "created_at": _iso_days_ago(days_ago),
    })


def scan(client):
    as_user()
    r = client.post("/api/failure-alerts/scan")
    assert r.status_code == 200
    return r.json()


# ---------- scan ----------

def test_scan_creates_alert_for_ancient_unit(client, fake_db):
    # 18-year-old AC condenser (lifespan 15y) with overdue service -> at risk.
    seed_equipment(fake_db, "eq-old", install_days_ago=18 * 365,
                   last_service_days_ago=400)
    b = scan(client)
    assert b["equipment_scanned"] == 1
    assert b["alerts_created"] == 1
    alert = fake_db.failure_alerts.docs[0]
    assert alert["equipment_id"] == "eq-old"
    assert alert["severity"] in {"warning", "critical"}
    assert alert["status"] == "new"
    assert alert["predicted_window"]
    assert alert["suggested_action"]
    assert alert["breakdown"]
    assert alert["reasons"]


def test_scan_no_alert_for_healthy_unit(client, fake_db):
    # 2-year-old furnace serviced last month -> healthy.
    seed_equipment(fake_db, "eq-new", eq_type="furnace",
                   install_days_ago=2 * 365, last_service_days_ago=30)
    b = scan(client)
    assert b["alerts_created"] == 0
    assert b["skipped_healthy"] == 1
    assert len(fake_db.failure_alerts.docs) == 0


def test_scan_repeat_repairs_flag_risk(client, fake_db):
    # Young unit but 3 repair visits in the last year -> failure_freq kicks in.
    seed_equipment(fake_db, "eq-lemon", install_days_ago=3 * 365,
                   last_service_days_ago=10)
    seed_service_event(fake_db, "eq-lemon", days_ago=90, description="Capacitor replaced")
    seed_service_event(fake_db, "eq-lemon", days_ago=60, description="Contactor replaced")
    seed_service_event(fake_db, "eq-lemon", days_ago=20, description="Refrigerant topped up")
    b = scan(client)
    assert b["alerts_created"] == 1
    factors = {c["factor"]: c for c in fake_db.failure_alerts.docs[0]["breakdown"]}
    assert factors["failure_freq"]["score"] >= 0.75  # 4 visits incl. baseline


def test_scan_anomaly_keywords_in_job_notes(client, fake_db):
    # Mid-life unit; recent job notes mention an emergency breakdown -> anomaly.
    seed_equipment(fake_db, "eq-noisy", install_days_ago=8 * 365,
                   last_service_days_ago=100)
    seed_job(fake_db, "job-1", notes="Customer reports emergency breakdown, no cool at night")
    seed_service_event(fake_db, "eq-noisy", days_ago=5,
                       description="Checked on customer complaint", job_id="job-1")
    b = scan(client)
    assert b["alerts_created"] == 1
    alert = fake_db.failure_alerts.docs[0]
    factors = {c["factor"]: c for c in alert["breakdown"]}
    assert factors["anomaly"]["score"] > 0
    assert any("breakdown" in r or "emergency" in r for r in alert["reasons"])


def test_scan_dedupes_refreshes_open_alert(client, fake_db):
    seed_equipment(fake_db, "eq-old", install_days_ago=18 * 365,
                   last_service_days_ago=400)
    b1 = scan(client)
    b2 = scan(client)
    assert b1["alerts_created"] == 1
    assert b2["alerts_created"] == 0
    assert b2["alerts_updated"] == 1
    assert len(fake_db.failure_alerts.docs) == 1


def test_scan_suppressed_after_dismiss(client, fake_db):
    seed_equipment(fake_db, "eq-old", install_days_ago=18 * 365,
                   last_service_days_ago=400)
    scan(client)
    alert_id = fake_db.failure_alerts.docs[0]["id"]
    as_user()
    r = client.post(f"/api/failure-alerts/alerts/{alert_id}/dismiss")
    assert r.status_code == 200
    b = scan(client)
    assert b["alerts_created"] == 0 and b["suppressed"] == 1


def test_scan_suppressed_after_acknowledge(client, fake_db):
    seed_equipment(fake_db, "eq-old", install_days_ago=18 * 365,
                   last_service_days_ago=400)
    scan(client)
    alert_id = fake_db.failure_alerts.docs[0]["id"]
    as_user()
    client.post(f"/api/failure-alerts/alerts/{alert_id}/acknowledge")
    b = scan(client)
    assert b["alerts_created"] == 0 and b["suppressed"] == 1


def test_scan_records_scan_doc(client, fake_db):
    seed_equipment(fake_db, "eq-old", install_days_ago=18 * 365,
                   last_service_days_ago=400)
    scan(client)
    assert len(fake_db.failure_alert_scans.docs) == 1
    s = fake_db.failure_alert_scans.docs[0]
    assert s["equipment_scanned"] == 1 and s["alerts_created"] == 1


def test_scan_non_owner_forbidden(client):
    as_user(role="tech")
    r = client.post("/api/failure-alerts/scan")
    assert r.status_code == 403


def test_scan_skips_inactive_equipment(client, fake_db):
    seed_equipment(fake_db, "eq-dead", install_days_ago=25 * 365,
                   last_service_days_ago=900, active=False)
    b = scan(client)
    assert b["equipment_scanned"] == 0 and b["alerts_created"] == 0


# ---------- overview ----------

def test_overview_counts(client, fake_db):
    seed_equipment(fake_db, "eq-old", install_days_ago=18 * 365,
                   last_service_days_ago=400)
    seed_equipment(fake_db, "eq-new", eq_type="furnace",
                   install_days_ago=2 * 365, last_service_days_ago=30)
    scan(client)
    as_user()
    b = client.get("/api/failure-alerts/overview").json()
    assert b["total_equipment"] == 2
    assert b["equipment_at_risk"] == 1
    assert b["open"] == 1
    assert b["critical"] + b["warning"] + b["info"] == 1
    assert b["last_scan_at"] is not None
    assert abs(sum(b["weights"].values()) - 1.0) < 1e-6


def test_overview_scoped_to_tenant(client, fake_db):
    seed_equipment(fake_db, "eq-old", tenant_id="t1",
                   install_days_ago=18 * 365, last_service_days_ago=400)
    as_user(tenant_id="t1")
    client.post("/api/failure-alerts/scan")
    as_user(tenant_id="t2")
    b = client.get("/api/failure-alerts/overview").json()
    assert b["open"] == 0 and b["total_equipment"] == 0


# ---------- alert list filters ----------

def _alerts(client, **qs):
    as_user()
    r = client.get("/api/failure-alerts/alerts", params=qs)
    assert r.status_code == 200
    return r.json()


def test_alerts_list_and_filters(client, fake_db):
    seed_equipment(fake_db, "eq-old", install_days_ago=18 * 365,
                   last_service_days_ago=400)
    scan(client)
    b = _alerts(client)
    assert b["count"] == 1
    sev = b["alerts"][0]["severity"]
    assert _alerts(client, severity=sev)["count"] == 1
    other = "info" if sev != "info" else "critical"
    assert _alerts(client, severity=other)["count"] == 0
    assert _alerts(client, status="new")["count"] == 1
    assert _alerts(client, status="acknowledged")["count"] == 0


def test_alerts_bad_severity_rejected(client):
    as_user()
    assert client.get("/api/failure-alerts/alerts?severity=bogus").status_code == 400


# ---------- acknowledge / dismiss ----------

def _first_alert_id(fake_db):
    return fake_db.failure_alerts.docs[0]["id"]


def test_acknowledge_flow_and_weight_feedback(client, fake_db):
    seed_equipment(fake_db, "eq-old", install_days_ago=18 * 365,
                   last_service_days_ago=400)
    scan(client)
    as_user()
    before = client.get("/api/failure-alerts/overview").json()["weights"]

    r = client.post(f"/api/failure-alerts/alerts/{_first_alert_id(fake_db)}/acknowledge")
    assert r.status_code == 200
    b = r.json()
    assert b["ok"] and b["alert"]["status"] == "acknowledged"
    assert b["alert"]["acknowledged_by"] == "u-t1"
    after = b["weights"]
    top = max(fake_db.failure_alerts.docs[0]["breakdown"],
              key=lambda c: c["points"])["factor"]
    assert after[top] > before[top]  # reinforced
    assert abs(sum(after.values()) - 1.0) < 1e-6  # still normalized


def test_acknowledge_twice_conflicts(client, fake_db):
    seed_equipment(fake_db, "eq-old", install_days_ago=18 * 365,
                   last_service_days_ago=400)
    scan(client)
    as_user()
    aid = _first_alert_id(fake_db)
    assert client.post(f"/api/failure-alerts/alerts/{aid}/acknowledge").status_code == 200
    assert client.post(f"/api/failure-alerts/alerts/{aid}/acknowledge").status_code == 409


def test_dismiss_flow_and_weight_feedback(client, fake_db):
    seed_equipment(fake_db, "eq-old", install_days_ago=18 * 365,
                   last_service_days_ago=400)
    scan(client)
    as_user()
    before = client.get("/api/failure-alerts/overview").json()["weights"]

    r = client.post(f"/api/failure-alerts/alerts/{_first_alert_id(fake_db)}/dismiss")
    assert r.status_code == 200
    b = r.json()
    assert b["ok"] and b["alert"]["status"] == "dismissed"
    assert b["alert"]["dismissed_at"] is not None
    after = b["weights"]
    top = max(fake_db.failure_alerts.docs[0]["breakdown"],
              key=lambda c: c["points"])["factor"]
    assert after[top] < before[top]  # dampened


def test_dismiss_twice_conflicts(client, fake_db):
    seed_equipment(fake_db, "eq-old", install_days_ago=18 * 365,
                   last_service_days_ago=400)
    scan(client)
    as_user()
    aid = _first_alert_id(fake_db)
    assert client.post(f"/api/failure-alerts/alerts/{aid}/dismiss").status_code == 200
    assert client.post(f"/api/failure-alerts/alerts/{aid}/dismiss").status_code == 409


def test_acknowledge_missing_alert_404(client):
    as_user()
    assert client.post("/api/failure-alerts/alerts/nope/acknowledge").status_code == 404


def test_acknowledge_other_tenant_404(client, fake_db):
    seed_equipment(fake_db, "eq-old", install_days_ago=18 * 365,
                   last_service_days_ago=400)
    scan(client)
    as_user(tenant_id="t2")
    r = client.post(f"/api/failure-alerts/alerts/{_first_alert_id(fake_db)}/acknowledge")
    assert r.status_code == 404


def test_tech_can_acknowledge(client, fake_db):
    seed_equipment(fake_db, "eq-old", install_days_ago=18 * 365,
                   last_service_days_ago=400)
    scan(client)
    as_user(role="tech")
    r = client.post(f"/api/failure-alerts/alerts/{_first_alert_id(fake_db)}/acknowledge")
    assert r.status_code == 200


# ---------- risk endpoint ----------

def test_risk_breakdown_math(client, fake_db):
    # 15y-old AC condenser (lifespan 15y) -> age score ~1.0, weighted 30 pts.
    # (Leap years make 15*365 days marginally under 15.0y, hence >= 0.99.)
    seed_equipment(fake_db, "eq-old", install_days_ago=15 * 365,
                   last_service_days_ago=10)
    as_user()
    b = client.get("/api/failure-alerts/equipment/eq-old/risk").json()
    factors = {c["factor"]: c for c in b["components"]}
    assert factors["age"]["score"] >= 0.99
    assert factors["age"]["points"] == pytest.approx(30.0, abs=0.4)  # ~1.0 * 0.30 * 100
    assert b["score"] == int(round(sum(c["points"] for c in b["components"])))
    assert b["severity"] in {"info", "warning", "critical"}
    assert not b["healthy"]
    assert b["predicted_window"] in {"0-30 days", "1-3 months", "3-6 months"}
    assert b["suggested_action"]
    # points must add up to the score
    assert abs(sum(c["points"] for c in b["components"]) - b["score"]) < 0.6


def test_risk_healthy_unit(client, fake_db):
    seed_equipment(fake_db, "eq-new", eq_type="furnace",
                   install_days_ago=2 * 365, last_service_days_ago=30)
    as_user()
    b = client.get("/api/failure-alerts/equipment/eq-new/risk").json()
    assert b["healthy"] is True and b["severity"] is None
    assert b["score"] < 30


def test_risk_missing_install_date_is_known_unknown(client, fake_db):
    fake_db.equipment.docs.append({
        "id": "eq-nodate", "tenant_id": "t1", "customer_id": "c1",
        "customer_name": "Kim", "location_label": "", "equipment_type": "furnace",
        "make": "", "model": "", "serial_number": "", "install_date": None,
        "active": True, "service_event_count": 0,
        "created_at": _iso_days_ago(30), "updated_at": _iso_days_ago(10),
    })
    seed_service_event(fake_db, "eq-nodate", days_ago=10, description="Tune-up")
    as_user()
    b = client.get("/api/failure-alerts/equipment/eq-nodate/risk").json()
    factors = {c["factor"]: c for c in b["components"]}
    assert factors["age"]["score"] == 0.0
    assert "unknown" in factors["age"]["detail"].lower()


def test_risk_unknown_equipment_404(client):
    as_user()
    assert client.get("/api/failure-alerts/equipment/nope/risk").status_code == 404


def test_risk_scoped_to_tenant(client, fake_db):
    seed_equipment(fake_db, "eq-old", tenant_id="t1",
                   install_days_ago=18 * 365, last_service_days_ago=400)
    as_user(tenant_id="t2")
    assert client.get("/api/failure-alerts/equipment/eq-old/risk").status_code == 404


# ---------- weights feedback persistence ----------

def test_weights_persisted_per_tenant(client, fake_db):
    seed_equipment(fake_db, "eq-old", install_days_ago=18 * 365,
                   last_service_days_ago=400)
    scan(client)
    as_user()
    w1 = client.get("/api/failure-alerts/overview").json()["weights"]
    client.post(f"/api/failure-alerts/alerts/{_first_alert_id(fake_db)}/dismiss")
    w2 = client.get("/api/failure-alerts/overview").json()["weights"]
    assert w1 != w2
    assert len(fake_db.failure_alert_weights.docs) == 1
    assert fake_db.failure_alert_weights.docs[0]["tenant_id"] == "t1"
