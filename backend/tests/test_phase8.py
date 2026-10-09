"""Phase 8 backend tests: post-job automation, review-response drafter, standup, social, cron, settings."""
import os
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import requests



BASE = os.environ.get("REACT_APP_BACKEND_URL", "http://127.0.0.1:9").rstrip("/")  # WL-044: env only, never a deployment file or public server
API = f"{BASE}/api"
CRON_SECRET = os.environ.get("WEBHOOK_CRON_SECRET", "")  # WL-044: env only, never the deployment's backend/.env


@pytest.fixture(scope="session")
def owner():
    s = requests.Session()
    email = f"test_p8_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{API}/auth/register", json={
        "email": email, "password": "testpass123",
        "name": "P8 Tester", "business_name": f"P8Biz {uuid.uuid4().hex[:6]}",
    })
    assert r.status_code == 200, r.text
    me = s.get(f"{API}/auth/me").json()
    return {"session": s, "email": email, "user": me}


# ---------- Smoke ----------
def test_health():
    r = requests.get(f"{API}/health")
    assert r.status_code == 200


def test_pricing_renders():
    r = requests.get(f"{BASE}/pricing")
    assert r.status_code == 200


# ---------- Post-job trigger via appointment completion ----------
def test_appointment_completion_triggers_post_job(owner):
    s = owner["session"]
    start = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    r = s.post(f"{API}/tenants/appointments", json={
        "customer_name": "Mia Test",
        "customer_phone": "+15555550101",
        "customer_email": "mia@example.com",
        "service_name": "AC Tune-up",
        "start_at": start,
        "end_at": end,
        "status": "scheduled",
    })
    assert r.status_code == 200, r.text
    appt = r.json()
    appt_id = appt["id"]

    # Update to completed
    r2 = s.put(f"{API}/tenants/appointments/{appt_id}", json={
        "customer_name": "Mia Test",
        "customer_phone": "+15555550101",
        "customer_email": "mia@example.com",
        "service_name": "AC Tune-up",
        "start_at": start,
        "end_at": end,
        "status": "completed",
    })
    assert r2.status_code == 200, r2.text
    assert r2.json().get("status") == "completed"

    # Verify post-job run
    runs = s.get(f"{API}/growth/post-job/runs").json()
    assert isinstance(runs, list) and len(runs) >= 1
    match = [x for x in runs if x.get("appointment_id") == appt_id]
    assert match, f"no run for appt_id {appt_id}"
    run = match[0]
    assert run.get("customer_name") == "Mia Test"
    assert run.get("service") == "AC Tune-up"
    assert "thanks_sent" in run
    assert run.get("winback_scheduled_for")
    # ~60 days
    scheduled = datetime.fromisoformat(run["winback_scheduled_for"].replace("Z", "+00:00"))
    delta_days = (scheduled - datetime.now(timezone.utc)).days
    assert 58 <= delta_days <= 61

    # Idempotency: PUT again (same status completed) should not create duplicate followup_job
    # Direct re-trigger via manual run to also test the upsert key
    r3 = s.post(f"{API}/growth/post-job/{appt_id}/run")
    assert r3.status_code == 200, r3.text

    # followup_jobs count via mongo
    from pymongo import MongoClient
    MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")  # WL-044: env only, never the deployment's backend/.env
    DB_NAME = os.environ.get("DB_NAME", "ai_office_platform")
    db = MongoClient(MONGO_URL)[DB_NAME]
    n = db.followup_jobs.count_documents({"appointment_id": appt_id, "kind": "winback-60d"})
    assert n == 1, f"expected 1 winback job, got {n}"


def test_manual_post_job(owner):
    s = owner["session"]
    start = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    r = s.post(f"{API}/tenants/appointments", json={
        "customer_name": "Manual Test", "customer_phone": "+15555550202",
        "service_name": "Drain Clean", "start_at": start, "end_at": end, "status": "scheduled",
    })
    appt_id = r.json()["id"]
    r2 = s.post(f"{API}/growth/post-job/{appt_id}/run")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert "thanks_sent" in body
    assert "sms" in body["thanks_sent"] and "email" in body["thanks_sent"]
    assert body.get("winback_scheduled_for")


# ---------- Review response drafter ----------
def test_review_response_positive(owner):
    s = owner["session"]
    r = s.post(f"{API}/growth/review-response", json={
        "review_text": "Amazing service", "rating": 5, "customer_name": "Mia",
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("reply")
    assert d.get("tone") == "grateful"
    assert d.get("method")


def test_review_response_negative(owner):
    s = owner["session"]
    r = s.post(f"{API}/growth/review-response", json={
        "review_text": "Terrible, rude technician", "rating": 1,
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("tone") == "apologetic"
    txt = (d.get("reply") or "").lower()
    assert "make it right" in txt or "contact" in txt or "reach" in txt or "fix" in txt


# ---------- Standup ----------
def test_standup_preview(owner):
    s = owner["session"]
    r = s.get(f"{API}/growth/standup/preview")
    assert r.status_code == 200, r.text
    d = r.json()
    for k in ("appointments", "hot_leads", "pending_followups", "missed_yesterday", "generated_at"):
        assert k in d


def test_standup_send(owner):
    s = owner["session"]
    r = s.post(f"{API}/growth/standup/send")
    assert r.status_code == 200, r.text
    d = r.json()
    assert "sent" in d and "sms" in d["sent"] and "email" in d["sent"]
    assert d.get("preview", "").startswith("Good morning")


# ---------- Social draft ----------
def test_social_draft(owner):
    s = owner["session"]
    r = s.post(f"{API}/growth/social/draft")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("caption") and len(d["caption"]) >= 30
    assert isinstance(d.get("hashtags"), list)
    assert d.get("method")


# ---------- Cron auth ----------
def test_daily_standup_cron_unauth():
    r = requests.post(f"{API}/cron/daily-standup")
    assert r.status_code == 401


def test_daily_standup_cron_authed():
    assert CRON_SECRET, "WEBHOOK_CRON_SECRET missing"
    r = requests.post(f"{API}/cron/daily-standup", headers={"Authorization": f"Bearer {CRON_SECRET}"})
    assert r.status_code == 200
    assert r.json().get("accepted") is True


# ---------- Tenant review_url persistence ----------
def test_review_url_persistence(owner):
    s = owner["session"]
    url = "https://g.page/r/testbiz/review"
    r = s.put(f"{API}/tenants/me", json={"review_url": url})
    assert r.status_code == 200, r.text
    g = s.get(f"{API}/tenants/me").json()
    assert g.get("review_url") == url
