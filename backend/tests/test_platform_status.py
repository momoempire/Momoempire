"""Tests for routers/platform_status.py (public status page + proactive
issue monitoring).

Uses a fake in-memory mongo (same pattern as test_reputation.py): no live
database. Covers: public routes need no auth, component CRUD, the incident
lifecycle with public updates, the monitor failure counter (auto-incident
after 3 consecutive failures, counter reset on success, dedupe while an
auto-incident is open), and tenant isolation.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "platform-status-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "platform_status_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.platform_status as ps_mod  # noqa: E402
from security import require_tenant_user, require_tenant_owner_or_admin  # noqa: E402


# ---------- fake mongo (copied from test_reputation.py) ----------

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
        self.status_components = FakeCollection()
        self.status_incidents = FakeCollection()
        self.status_updates = FakeCollection()
        self.status_monitors = FakeCollection()
        self.monitor_results = FakeCollection()


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(ps_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(ps_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: _authed(current_user)
    app.dependency_overrides[require_tenant_owner_or_admin] = lambda: _guard_owner(_authed(current_user))
    return TestClient(app)


def _authed(user):
    if not user.get("tenant_id"):
        from fastapi import HTTPException
        raise HTTPException(401, "Not authenticated")
    return user


def _guard_owner(user):
    if user.get("role") not in {"owner", "admin"}:
        from fastapi import HTTPException
        raise HTTPException(403, "Owner/admin role required")
    return user


def as_user(tenant_id="t1", role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def first_component_id(client):
    """Seed via the authed endpoint and return the first component's id."""
    b = client.get("/api/platform-status/components").json()
    return b["components"][0]["id"], b["components"][0]["key"]


# ---------- public routes: no auth required ----------

def test_public_status_no_auth(client):
    # No as_user() call — proves the route is public.
    r = client.get("/api/platform-status/public/status?tenant=pub1")
    assert r.status_code == 200
    b = r.json()
    assert b["tenant_id"] == "pub1"
    assert b["status"] == "operational"
    keys = [c["key"] for c in b["components"]]
    assert keys == ["api", "ai_receptionist", "voice_pipeline", "sms_delivery", "web_app"]


def test_public_status_missing_tenant_400(client):
    r = client.get("/api/platform-status/public/status?tenant=")
    assert r.status_code == 400


def test_public_status_reflects_degraded_component(client, fake_db):
    r = client.get("/api/platform-status/public/status?tenant=pub2")
    assert r.status_code == 200
    # Simulate a degraded component without any auth.
    fake_db.status_components.docs[1]["status"] = "major_outage"
    b = client.get("/api/platform-status/public/status?tenant=pub2").json()
    assert b["status"] == "major_outage"


def test_public_incidents_no_auth(client, fake_db):
    as_user(tenant_id="t1")
    r = client.post("/api/platform-status/incidents",
                    json={"title": "Voice pipeline latency", "severity": "minor"})
    incident_id = r.json()["id"]
    client.post(f"/api/platform-status/incidents/{incident_id}/updates",
                json={"status": "identified", "message": "Found a stuck queue."})
    current_user.clear()  # drop auth entirely
    r = client.get("/api/platform-status/public/incidents?tenant=t1")
    assert r.status_code == 200
    incidents = r.json()["incidents"]
    assert len(incidents) == 1
    assert incidents[0]["title"] == "Voice pipeline latency"
    assert [u["message"] for u in incidents[0]["updates"]] == [
        "Incident opened: Voice pipeline latency",
        "Found a stuck queue.",
    ]


def test_authed_routes_reject_no_auth(client):
    r = client.get("/api/platform-status/components")
    assert r.status_code in {401, 403}


# ---------- components ----------

def test_components_seed_and_list(client):
    as_user()
    b = client.get("/api/platform-status/components").json()
    assert b["count"] == 5
    # Second read must not reseed.
    b2 = client.get("/api/platform-status/components").json()
    assert b2["count"] == 5


def test_create_component(client):
    as_user()
    r = client.post("/api/platform-status/components",
                    json={"key": "payments", "name": "Payments", "description": "Stripe webhooks"})
    assert r.status_code == 200
    assert r.json()["status"] == "operational"
    assert client.get("/api/platform-status/components").json()["count"] == 6


def test_create_component_duplicate_key_409(client):
    as_user()
    r = client.post("/api/platform-status/components",
                    json={"key": "api", "name": "API dup"})
    assert r.status_code == 409


def test_create_component_non_owner_forbidden(client):
    as_user(role="tech")
    r = client.post("/api/platform-status/components",
                    json={"key": "x", "name": "X"})
    assert r.status_code == 403


def test_patch_component_status(client):
    as_user()
    comp_id, _ = first_component_id(client)
    r = client.patch(f"/api/platform-status/components/{comp_id}",
                     json={"status": "degraded"})
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"
    assert client.get("/api/platform-status/public/status?tenant=t1").json()["status"] == "degraded"


def test_patch_component_invalid_status_422(client):
    as_user()
    comp_id, _ = first_component_id(client)
    r = client.patch(f"/api/platform-status/components/{comp_id}",
                     json={"status": "fine"})
    assert r.status_code == 422


def test_patch_component_unknown_404(client):
    as_user()
    r = client.patch("/api/platform-status/components/nope", json={"status": "degraded"})
    assert r.status_code == 404


def test_component_tenant_isolation(client, fake_db):
    as_user(tenant_id="t1")
    comp_id, _ = first_component_id(client)
    as_user(tenant_id="t2")
    r = client.patch(f"/api/platform-status/components/{comp_id}", json={"status": "degraded"})
    assert r.status_code == 404  # t1's component is invisible to t2


# ---------- incident lifecycle ----------

def test_open_incident_and_list(client):
    as_user()
    r = client.post("/api/platform-status/incidents",
                    json={"title": "SMS delays", "severity": "major"})
    assert r.status_code == 200
    incident_id = r.json()["id"]
    assert r.json()["status"] == "investigating"
    b = client.get("/api/platform-status/incidents").json()
    assert len(b["incidents"]) == 1
    assert b["incidents"][0]["id"] == incident_id
    assert len(b["incidents"][0]["updates"]) == 1  # "Incident opened" update


def test_incident_updates_advance_status(client):
    as_user()
    incident_id = client.post("/api/platform-status/incidents",
                              json={"title": "API errors"}).json()["id"]
    r = client.post(f"/api/platform-status/incidents/{incident_id}/updates",
                    json={"status": "identified", "message": "Bad deploy identified."})
    assert r.status_code == 200
    assert r.json()["status"] == "identified"
    b = client.get("/api/platform-status/incidents").json()
    assert b["incidents"][0]["status"] == "identified"
    assert len(b["incidents"][0]["updates"]) == 2


def test_resolve_restores_component(client):
    as_user()
    comp_id, comp_key = first_component_id(client)
    incident_id = client.post("/api/platform-status/incidents",
                              json={"title": "API down", "component_id": comp_id,
                                    "severity": "critical"}).json()["id"]
    client.patch(f"/api/platform-status/components/{comp_id}", json={"status": "major_outage"})
    r = client.post(f"/api/platform-status/incidents/{incident_id}/resolve")
    assert r.status_code == 200
    assert r.json()["status"] == "resolved"
    assert r.json()["final_update"]["status"] == "resolved"
    comps = client.get("/api/platform-status/components").json()["components"]
    assert next(c for c in comps if c["id"] == comp_id)["status"] == "operational"


def test_update_unknown_incident_404(client):
    as_user()
    r = client.post("/api/platform-status/incidents/nope/updates",
                    json={"message": "x"})
    assert r.status_code == 404


def test_open_incident_non_owner_forbidden(client):
    as_user(role="tech")
    r = client.post("/api/platform-status/incidents", json={"title": "x"})
    assert r.status_code == 403


# ---------- monitors & auto-incidents ----------

def _make_monitor(client, component_id):
    r = client.post("/api/platform-status/monitors",
                    json={"name": "API health", "component_id": component_id,
                          "check_type": "http", "target": "https://example.com/health"})
    assert r.status_code == 200
    return r.json()["id"]


def test_monitor_crud(client):
    as_user()
    comp_id, _ = first_component_id(client)
    monitor_id = _make_monitor(client, comp_id)
    b = client.get("/api/platform-status/monitors").json()
    assert b["count"] == 1
    assert b["monitors"][0]["id"] == monitor_id
    assert b["monitors"][0]["consecutive_failures"] == 0


def test_monitor_needs_component_404(client):
    as_user()
    r = client.post("/api/platform-status/monitors",
                    json={"name": "orphan", "component_id": "nope"})
    assert r.status_code == 404


def test_three_consecutive_failures_auto_open_incident(client, fake_db):
    as_user()
    comp_id, _ = first_component_id(client)
    monitor_id = _make_monitor(client, comp_id)
    # Two failures: no incident yet.
    for i in range(2):
        b = client.post(f"/api/platform-status/monitors/{monitor_id}/check",
                        json={"success": False, "latency_ms": 5000.0,
                              "detail": f"timeout {i}"}).json()
        assert b["consecutive_failures"] == i + 1
        assert b["incident"] is None
    assert len(fake_db.status_incidents.docs) == 0
    # Third failure: incident auto-opens.
    b = client.post(f"/api/platform-status/monitors/{monitor_id}/check",
                    json={"success": False, "detail": "timeout 2"}).json()
    assert b["consecutive_failures"] == 3
    assert b["incident"]
    assert len(fake_db.status_incidents.docs) == 1
    inc = fake_db.status_incidents.docs[0]
    assert inc["auto_monitor_id"] == monitor_id
    assert inc["status"] == "investigating"
    # Linked component degraded, public page reflects it.
    assert client.get("/api/platform-status/public/status?tenant=t1").json()["status"] == "degraded"
    # Fourth failure reuses the same open incident (dedupe).
    b2 = client.post(f"/api/platform-status/monitors/{monitor_id}/check",
                     json={"success": False}).json()
    assert b2["incident"] == b["incident"]
    assert len(fake_db.status_incidents.docs) == 1
    # Recent results attached on the monitor list.
    monitors = client.get("/api/platform-status/monitors").json()["monitors"]
    assert len(monitors[0]["recent_results"]) == 4


def test_success_resets_failure_counter(client):
    as_user()
    comp_id, _ = first_component_id(client)
    monitor_id = _make_monitor(client, comp_id)
    client.post(f"/api/platform-status/monitors/{monitor_id}/check", json={"success": False})
    client.post(f"/api/platform-status/monitors/{monitor_id}/check", json={"success": False})
    b = client.post(f"/api/platform-status/monitors/{monitor_id}/check",
                    json={"success": True, "latency_ms": 120.0}).json()
    assert b["consecutive_failures"] == 0
    # Three MORE failures are needed after the reset to open an incident.
    for _ in range(3):
        b = client.post(f"/api/platform-status/monitors/{monitor_id}/check",
                        json={"success": False}).json()
    assert b["incident"]


def test_check_unknown_monitor_404(client):
    as_user()
    r = client.post("/api/platform-status/monitors/nope/check", json={"success": True})
    assert r.status_code == 404


def test_monitor_tenant_isolation(client):
    as_user(tenant_id="t1")
    comp_id, _ = first_component_id(client)
    monitor_id = _make_monitor(client, comp_id)
    as_user(tenant_id="t2")
    r = client.post(f"/api/platform-status/monitors/{monitor_id}/check",
                    json={"success": True})
    assert r.status_code == 404
    assert client.get("/api/platform-status/monitors").json()["count"] == 0
    assert client.get("/api/platform-status/incidents").json()["incidents"] == []
