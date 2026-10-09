"""Phase 9 backend tests: callbacks, confirmations, review-request autopilot, heatmap, ICS."""
import os
import uuid
import time
from datetime import datetime, timezone, timedelta

import pytest
import requests


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
CRON_SECRET = os.environ.get("WEBHOOK_CRON_SECRET") or _read_env("/app/backend/.env", "WEBHOOK_CRON_SECRET", "")


@pytest.fixture(scope="session")
def owner():
    s = requests.Session()
    email = f"test_p9_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{API}/auth/register", json={
        "email": email, "password": "testpass123",
        "name": "P9 Tester", "business_name": f"P9Biz {uuid.uuid4().hex[:6]}",
    })
    assert r.status_code == 200, r.text
    # Save review_url
    r2 = s.put(f"{API}/tenants/me", json={"review_url": "https://g.page/r/testbiz/review"})
    assert r2.status_code == 200, r2.text
    me = s.get(f"{API}/auth/me").json()
    return {"session": s, "email": email, "user": me}


# ---------- Smoke ----------
def test_health():
    r = requests.get(f"{API}/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_review_url_persisted(owner):
    r = owner["session"].get(f"{API}/tenants/me")
    assert r.status_code == 200
    assert r.json().get("review_url") == "https://g.page/r/testbiz/review"


# ---------- Callback scheduler ----------
def test_callback_schedule_list_cancel(owner):
    s = owner["session"]
    call_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = s.post(f"{API}/growth/callbacks", json={
        "phone": "+15551234567", "name": "Alice", "call_at": call_at, "note": "followup",
    })
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["kind"] == "callback"
    assert doc["status"] == "scheduled"
    assert doc.get("id")
    cid = doc["id"]

    # List
    rl = s.get(f"{API}/growth/callbacks")
    assert rl.status_code == 200
    ids = [j["id"] for j in rl.json()]
    assert cid in ids

    # Cancel
    rc = s.post(f"{API}/growth/callbacks/{cid}/cancel")
    assert rc.status_code == 200

    rl2 = s.get(f"{API}/growth/callbacks", params={"status": "cancelled"})
    assert rl2.status_code == 200
    ids2 = [j["id"] for j in rl2.json()]
    assert cid in ids2


def test_cron_callbacks_due_auth():
    r = requests.post(f"{API}/cron/callbacks-due")
    assert r.status_code == 401

    r2 = requests.post(f"{API}/cron/callbacks-due",
                       headers={"Authorization": f"Bearer {CRON_SECRET}"})
    assert r2.status_code == 200
    assert r2.json().get("accepted") is True


# ---------- 24h appointment confirmation ----------
def _create_appt(s, phone, hours_ahead=24, name="Confirmy"):
    start = (datetime.now(timezone.utc) + timedelta(hours=hours_ahead)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(hours=hours_ahead + 1)).isoformat()
    r = s.post(f"{API}/tenants/appointments", json={
        "customer_name": name, "customer_phone": phone,
        "service_name": "Tune-up", "start_at": start, "end_at": end,
        "status": "scheduled",
    })
    assert r.status_code == 200, r.text
    return r.json()


def test_appt_confirmation_cron_and_sms_yes(owner):
    s = owner["session"]
    phone = f"+1555000{uuid.uuid4().int % 10000:04d}"
    appt = _create_appt(s, phone, hours_ahead=24, name="YesCust")

    r = requests.post(f"{API}/cron/appt-confirmations",
                      headers={"Authorization": f"Bearer {CRON_SECRET}"})
    assert r.status_code == 200
    time.sleep(2)  # let background task run

    appts = s.get(f"{API}/tenants/appointments").json()
    row = next((a for a in appts if a["id"] == appt["id"]), None)
    assert row is not None
    assert row.get("confirm_sent_at"), f"confirm_sent_at missing on appt: {row}"

    # Simulate inbound YES
    r2 = s.post(f"{API}/conversations/sms/inbound-sim", json={
        "from": phone, "body": "YES", "name": "YesCust",
    })
    assert r2.status_code == 200, r2.text
    assert "confirmed" in r2.json().get("reply", "").lower()

    appts2 = s.get(f"{API}/tenants/appointments").json()
    row2 = next((a for a in appts2 if a["id"] == appt["id"]), None)
    assert row2["status"] == "confirmed"


def test_appt_sms_reschedule(owner):
    s = owner["session"]
    phone = f"+1555001{uuid.uuid4().int % 10000:04d}"
    appt = _create_appt(s, phone, hours_ahead=24, name="RescheduleCust")

    r2 = s.post(f"{API}/conversations/sms/inbound-sim", json={
        "from": phone, "body": "RESCHEDULE", "name": "R",
    })
    assert r2.status_code == 200
    reply = r2.json().get("reply", "").lower()
    assert "new time" in reply or "pick" in reply or "day" in reply

    appts = s.get(f"{API}/tenants/appointments").json()
    row = next((a for a in appts if a["id"] == appt["id"]), None)
    assert row["status"] == "canceled"
    assert row.get("reschedule_requested") is True


def test_appt_sms_cancel(owner):
    s = owner["session"]
    phone = f"+1555002{uuid.uuid4().int % 10000:04d}"
    appt = _create_appt(s, phone, hours_ahead=24, name="CancelCust")

    r2 = s.post(f"{API}/conversations/sms/inbound-sim", json={
        "from": phone, "body": "CANCEL", "name": "C",
    })
    assert r2.status_code == 200
    appts = s.get(f"{API}/tenants/appointments").json()
    row = next((a for a in appts if a["id"] == appt["id"]), None)
    assert row["status"] == "canceled"


# ---------- Review-request autopilot (2h) ----------
def test_review_request_scheduled_on_completion(owner):
    s = owner["session"]
    phone = f"+1555003{uuid.uuid4().int % 10000:04d}"
    appt = _create_appt(s, phone, hours_ahead=1, name="ReviewCust")
    # Mark completed
    r = s.put(f"{API}/tenants/appointments/{appt['id']}", json={
        "customer_name": "ReviewCust", "customer_phone": phone,
        "service_name": "Tune-up",
        "start_at": appt["start_at"], "end_at": appt["end_at"],
        "status": "completed",
    })
    assert r.status_code == 200, r.text
    time.sleep(1)

    jobs = s.get(f"{API}/sales/followups").json()
    rr = [j for j in jobs if j.get("kind") == "review-request-2h" and j.get("appointment_id") == appt["id"]]
    assert len(rr) >= 1, f"review-request-2h missing. jobs={jobs}"
    job = rr[0]
    send_at = datetime.fromisoformat(job["send_at"].replace("Z", "+00:00"))
    delta_h = (send_at - datetime.now(timezone.utc)).total_seconds() / 3600
    assert 1.5 <= delta_h <= 2.5, f"send_at not ~2h: {delta_h}"


# ---------- Heatmap ----------
def test_heatmap_aggregates_by_zip(owner):
    s = owner["session"]
    leads = [
        {"name": "L1", "phone": "+15550000001", "notes": "123 Main St, Austin, TX 78701"},
        {"name": "L2", "phone": "+15550000002", "notes": "456 Oak, Austin, TX 78702"},
        {"name": "L3", "phone": "+15550000003", "notes": "999 Lake, Dallas, TX 75201"},
    ]
    for ld in leads:
        r = s.post(f"{API}/tenants/leads", json=ld)
        assert r.status_code == 200, r.text

    r = s.get(f"{API}/growth/heatmap", params={"days": 90}, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "totals" in data and "rows" in data
    assert data["totals"]["regions"] >= 3
    assert data["totals"]["leads_total"] >= 3
    zips = {r.get("zip") for r in data["rows"]}
    assert {"78701", "78702", "75201"}.issubset(zips)


# ---------- ICS export ----------
def test_standup_ics(owner):
    s = owner["session"]
    r = s.get(f"{API}/growth/standup/today.ics")
    assert r.status_code == 200
    assert "text/calendar" in r.headers.get("content-type", "")
    body = r.text
    assert body.startswith("BEGIN:VCALENDAR")
    assert "DTSTART:" in body
    assert "SUMMARY:" in body
    assert "END:VCALENDAR" in body
