"""Phase 7 backend tests: Objections, Persona, Quote Estimator, Coach, Cron appt reminders, Widget, Winback fix."""
import os
import time
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import requests
from pymongo import MongoClient

def _read_env(path, key, default=None):
    try:
        with open(path) as fh:
            for ln in fh:
                if ln.strip().startswith(key + "="):
                    return ln.split("=", 1)[1].strip().strip('"')
    except Exception:
        return default
    return default

BASE = os.environ.get("REACT_APP_BACKEND_URL", "http://127.0.0.1:9").rstrip("/")  # WL-044: env only, never a deployment file or public server
API = f"{BASE}/api"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "ai_office_platform")
# Fallback: read from backend/.env directly for pytest runs outside supervisor env
if MONGO_URL == "mongodb://localhost:27017" and not os.environ.get("MONGO_URL"):
    try:
        with open("/app/backend/.env") as fh:
            for ln in fh:
                if ln.startswith("MONGO_URL"):
                    MONGO_URL = ln.split("=", 1)[1].strip().strip('"')
                if ln.startswith("DB_NAME"):
                    DB_NAME = ln.split("=", 1)[1].strip().strip('"')
                if ln.startswith("WEBHOOK_CRON_SECRET"):
                    os.environ["WEBHOOK_CRON_SECRET"] = ln.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass

CRON_SECRET = os.environ.get("WEBHOOK_CRON_SECRET", "")


def _mongo():
    return MongoClient(MONGO_URL)[DB_NAME]


@pytest.fixture(scope="session")
def tenant_owner():
    s = requests.Session()
    email = f"test_p7_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{API}/auth/register", json={
        "email": email,
        "password": "testpass123",
        "name": "P7 Tester",
        "business_name": f"P7Biz {uuid.uuid4().hex[:6]}",
    })
    assert r.status_code == 200, r.text
    me = s.get(f"{API}/auth/me").json()
    assert me.get("tenant_id")
    # fetch slug
    tm = s.get(f"{API}/tenants/me").json()
    return {"session": s, "email": email, "user": me, "tenant": tm}


# ---------- Smoke ----------
def test_health():
    r = requests.get(f"{API}/health")
    assert r.status_code == 200


def test_public_pricing_renders():
    r = requests.get(f"{BASE}/pricing")
    assert r.status_code == 200


# ---------- Objections CRUD ----------
def test_objection_crud(tenant_owner):
    s = tenant_owner["session"]
    r = s.post(f"{API}/sales/objections", json={"pattern": "too expensive", "rebuttal": "We offer a payment plan."})
    assert r.status_code == 200, r.text
    obj = r.json()
    assert obj["pattern"] == "too expensive"
    assert obj["rebuttal"] == "We offer a payment plan."
    oid = obj["id"]

    lst = s.get(f"{API}/sales/objections").json()
    assert any(o["id"] == oid for o in lst)

    d = s.delete(f"{API}/sales/objections/{oid}")
    assert d.status_code == 200
    lst2 = s.get(f"{API}/sales/objections").json()
    assert not any(o["id"] == oid for o in lst2)


# ---------- Persona ----------
def test_persona_get_default_and_update(tenant_owner):
    s = tenant_owner["session"]
    r = s.get(f"{API}/sales/persona")
    assert r.status_code == 200
    data = r.json()
    assert data.get("key") == "receptionist"
    assert "presets" in data and "sales_pro" in data["presets"]

    p = s.put(f"{API}/sales/persona", json={"key": "sales_pro"})
    assert p.status_code == 200
    assert p.json()["key"] == "sales_pro"
    assert s.get(f"{API}/sales/persona").json()["key"] == "sales_pro"


# ---------- Quote estimator ----------
def test_quote_estimate(tenant_owner):
    s = tenant_owner["session"]
    r = s.post(f"{API}/sales/quote-estimate", json={"service_description": "replace central AC unit"})
    assert r.status_code == 200, r.text
    data = r.json()
    for key in ["low", "high", "confidence", "rationale", "disclaimer", "method"]:
        assert key in data, f"missing {key}: {data}"
    assert isinstance(data["low"], (int, float))
    assert isinstance(data["high"], (int, float))
    assert data["confidence"] in {"low", "medium", "high"}


# ---------- Coach ----------
def test_coach_returns_suggestions_and_rule_fallback_picks_rebuttal(tenant_owner):
    s = tenant_owner["session"]
    # Baseline — no objection yet
    r = s.post(f"{API}/sales/coach", json={"transcript": "Customer: it is too expensive", "scenario": "objection"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert "suggestions" in out and len(out["suggestions"]) >= 3

    # Add objection
    obj = s.post(f"{API}/sales/objections", json={"pattern": "too expensive", "rebuttal": "PAYMENT_PLAN_MARKER"}).json()
    # Query coach again — in the rule-fallback branch the rebuttal must appear;
    # in the LLM branch content is unpredictable but should still return 200.
    r2 = s.post(f"{API}/sales/coach", json={"transcript": "Customer: it is too expensive", "scenario": "objection"})
    assert r2.status_code == 200
    out2 = r2.json()
    assert "suggestions" in out2 and len(out2["suggestions"]) >= 1
    if out2.get("method") == "rules":
        assert any("PAYMENT_PLAN_MARKER" in x for x in out2["suggestions"])
    # cleanup
    s.delete(f"{API}/sales/objections/{obj['id']}")


# ---------- Winback cutoff fix ----------
def test_winback_run_only_targets_inactive(tenant_owner):
    s = tenant_owner["session"]
    tid = tenant_owner["user"]["tenant_id"]
    db = _mongo()
    # Create via API then update last_contacted_at directly in DB
    c_recent = s.post(f"{API}/tenants/customers", json={"name": "RecentCust", "phone": "+15550000001"}).json()
    c_old = s.post(f"{API}/tenants/customers", json={"name": "OldCust", "phone": "+15550000002"}).json()
    now = datetime.now(timezone.utc)
    db.customers.update_one({"id": c_recent["id"]}, {"$set": {"last_contacted_at": (now - timedelta(days=10)).isoformat()}})
    db.customers.update_one({"id": c_old["id"]}, {"$set": {"last_contacted_at": (now - timedelta(days=100)).isoformat()}})

    r = s.post(f"{API}/growth/winback/run", json={
        "days_inactive": 30, "channel": "sms", "message": "Hi {name}", "dry_run": False
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["attempted"] == 1, f"Expected 1 attempt, got {out}"


# ---------- Cron appointment reminders ----------
def test_cron_appt_reminders_auth_and_run(tenant_owner):
    s = tenant_owner["session"]
    tid = tenant_owner["user"]["tenant_id"]
    db = _mongo()

    # Unauthorized
    r = requests.post(f"{API}/cron/appointment-reminders")
    assert r.status_code == 401

    # Create appointment 24h away with phone
    start = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(hours=25)).isoformat()
    appt = s.post(f"{API}/tenants/appointments", json={
        "customer_name": "ReminderMe",
        "customer_phone": "+15559990000",
        "service_name": "AC tune-up",
        "start_at": start,
        "end_at": end,
        "status": "scheduled",
    })
    assert appt.status_code == 200, appt.text
    appt_id = appt.json()["id"]

    # Authorized trigger
    r2 = requests.post(f"{API}/cron/appointment-reminders",
                       headers={"Authorization": f"Bearer {CRON_SECRET}"})
    assert r2.status_code == 200
    assert r2.json().get("accepted") is True

    # Background task — poll for reminder_sent_at
    found = False
    for _ in range(15):
        time.sleep(0.5)
        doc = db.appointments.find_one({"id": appt_id})
        if doc and doc.get("reminder_sent_at"):
            found = True
            break
    assert found, "reminder_sent_at was not set within 7.5s"


# ---------- Widget JS + lead ----------
def test_widget_js_unknown_slug():
    r = requests.get(f"{API}/public/widget/not-a-real-slug-xyz.js")
    assert r.status_code == 200
    assert "javascript" in r.headers.get("content-type", "")
    assert "unknown workspace" in r.text


def test_widget_js_known_slug_and_public_lead(tenant_owner):
    s = tenant_owner["session"]
    tenant = tenant_owner["tenant"]
    slug = tenant.get("slug")
    assert slug
    r = requests.get(f"{API}/public/widget/{slug}.js")
    assert r.status_code == 200
    assert "javascript" in r.headers.get("content-type", "")
    assert "window.__aioWidgetLoaded" in r.text
    assert tenant["name"] in r.text

    # Public widget lead POST (no auth)
    lead_r = requests.post(f"{API}/public/widget/{slug}/lead",
                           json={"name": "WidgetGuy", "phone": "+15551234567", "message": "need help"})
    assert lead_r.status_code == 200, lead_r.text
    assert lead_r.json().get("ok") is True

    # Verify lead visible to tenant with source=widget
    leads = s.get(f"{API}/tenants/leads").json()
    assert any(l.get("source") == "widget" and l.get("name") == "WidgetGuy" for l in leads), leads


# ---------- AI prompt integration with persona + objections ----------
def test_conversation_with_persona_and_objection_does_not_crash(tenant_owner):
    s = tenant_owner["session"]
    # Set persona industry_expert
    s.put(f"{API}/sales/persona", json={"key": "industry_expert"})
    # Add objection
    s.post(f"{API}/sales/objections", json={"pattern": "too expensive", "rebuttal": "We offer a payment plan."})
    # Start conversation
    start = s.post(f"{API}/conversations/start", json={
        "channel": "call",
        "caller_name": "Walt",
        "caller_phone": "+15558887777",
        "caller_email": "",
        "is_simulation": True,
    })
    assert start.status_code == 200, start.text
    conv_id = start.json()["conversation"]["id"]
    turn = s.post(f"{API}/conversations/{conv_id}/caller-turn", json={"text": "that sounds too expensive"})
    assert turn.status_code == 200, turn.text
    body = turn.json()
    assert "reply" in body
    assert "action" in body
    assert "ended" in body or "end" in body
