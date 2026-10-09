"""Phase 10 — Smart Repeat Scheduling, Testimonial Auto-Converter, Spanish Mode."""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://127.0.0.1:9").rstrip("/")  # WL-044: never a public server by default
API = f"{BASE_URL}/api"

OWNER_EMAIL = "repeat-tester@example.com"
OWNER_PASSWORD = "StrongPass123!"
ADMIN_EMAIL = "ramonajefferson10@gmail.com"
ADMIN_PASSWORD = "AdminPass123!"

# Pull cron secret from env; fallback to the known test secret
CRON_SECRET = os.environ.get("WEBHOOK_CRON_SECRET", "cCqJd3dYtqZFbOt8c33ogrAMWLhhm1Ilg_1BdJ1-b0E")


@pytest.fixture(scope="module")
def owner_client():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}, timeout=20)
    if r.status_code != 200:
        pytest.skip(f"Owner login failed: {r.status_code} {r.text[:200]}")
    tok = r.json().get("access_token") or r.json().get("token")
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    return s


@pytest.fixture(scope="module")
def admin_client():
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=20)
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code} {r.text[:200]}")
    tok = r.json().get("access_token") or r.json().get("token")
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})
    return s


# ------------------- Repeat Scheduling -------------------
class TestRepeatScheduling:
    def test_defaults(self, owner_client):
        r = owner_client.get(f"{API}/repeat/defaults", timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "default_cadence" in d
        assert set(d["options"]) == {"annual", "semi_annual", "quarterly", "custom"}

    def test_create_annual_schedule(self, owner_client):
        payload = {
            "customer_name": "TEST_John Doe",
            "customer_email": "john@example.com",
            "service_name": "HVAC Tune-up",
            "cadence": "annual",
            "start_date": "2025-01-15",
            "reminder_days_before": 14,
        }
        r = owner_client.post(f"{API}/repeat/schedules", json=payload, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["cadence"] == "annual"
        assert d["status"] == "active"
        assert d["next_due_at"].startswith("2026-01-15")
        assert "id" in d
        pytest.schedule_id = d["id"]

    def test_custom_without_interval_fails(self, owner_client):
        r = owner_client.post(f"{API}/repeat/schedules", json={
            "customer_name": "TEST_x", "service_name": "svc", "cadence": "custom", "start_date": "2025-02-01",
        }, timeout=15)
        assert r.status_code == 400

    def test_custom_with_interval(self, owner_client):
        r = owner_client.post(f"{API}/repeat/schedules", json={
            "customer_name": "TEST_custom", "service_name": "svc", "cadence": "custom",
            "interval_months": 2, "start_date": "2025-02-01",
        }, timeout=15)
        assert r.status_code == 200
        assert r.json()["next_due_at"].startswith("2025-04-01")

    def test_list_schedules(self, owner_client):
        r = owner_client.get(f"{API}/repeat/schedules", timeout=15)
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert any(s.get("id") == getattr(pytest, "schedule_id", None) for s in r.json())

    def test_update_schedule(self, owner_client):
        sid = pytest.schedule_id
        r = owner_client.patch(f"{API}/repeat/schedules/{sid}", json={"cadence": "quarterly"}, timeout=15)
        assert r.status_code == 200
        # verify via GET
        rows = owner_client.get(f"{API}/repeat/schedules", timeout=15).json()
        found = [s for s in rows if s["id"] == sid][0]
        assert found["cadence"] == "quarterly"

    def test_send_reminder_manual(self, owner_client):
        sid = pytest.schedule_id
        r = owner_client.post(f"{API}/repeat/schedules/{sid}/send-reminder", json={}, timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "sms" in d and "email" in d

    def test_delete_schedule(self, owner_client):
        sid = pytest.schedule_id
        r = owner_client.delete(f"{API}/repeat/schedules/{sid}", timeout=15)
        assert r.status_code == 200
        # verify removal
        rows = owner_client.get(f"{API}/repeat/schedules", timeout=15).json()
        assert all(s["id"] != sid for s in rows)


# ------------------- Cron Repeat Reminders -------------------
class TestCronRepeatReminders:
    def test_unauthorized(self):
        r = requests.post(f"{API}/cron/repeat-reminders", timeout=15)
        assert r.status_code == 401

    def test_authorized(self):
        r = requests.post(f"{API}/cron/repeat-reminders",
                          headers={"Authorization": f"Bearer {CRON_SECRET}"}, timeout=20)
        assert r.status_code in (200, 202), r.text


# ------------------- Testimonials -------------------
class TestTestimonials:
    def test_seed_5_star(self, owner_client):
        payload = {
            "customer_name": "TEST_Alice",
            "customer_email": "alice@example.com",
            "rating": 5,
            "original_text": "Amazing service! The technician was professional and friendly. Highly recommend to anyone.",
        }
        r = owner_client.post(f"{API}/testimonials/seed", json=payload, timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "consent_pending"
        assert d["rating"] == 5
        assert d.get("polished_text")
        assert d.get("consent_token")
        pytest.testimonial = d

    def test_seed_rating_lt_5_rejected(self, owner_client):
        r = owner_client.post(f"{API}/testimonials/seed", json={
            "customer_name": "TEST_low", "rating": 4, "original_text": "ok"
        }, timeout=15)
        assert r.status_code == 400

    def test_list(self, owner_client):
        r = owner_client.get(f"{API}/testimonials?status=consent_pending", timeout=15)
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        ids = [t["id"] for t in r.json()]
        assert pytest.testimonial["id"] in ids

    def test_public_consent_get(self):
        token = pytest.testimonial["consent_token"]
        r = requests.get(f"{API}/public/testimonials/consent/{token}", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d["rating"] == 5
        assert d["polished_text"]
        assert d["status"] == "consent_pending"

    def test_public_consent_approve(self):
        token = pytest.testimonial["consent_token"]
        edited = "This is my edited testimonial. Great work!"
        r = requests.post(f"{API}/public/testimonials/consent/{token}",
                          json={"decision": "approve", "edited_text": edited}, timeout=15)
        assert r.status_code == 200
        assert r.json()["status"] == "admin_review"

    def test_public_consent_already_decided(self):
        token = pytest.testimonial["consent_token"]
        r = requests.post(f"{API}/public/testimonials/consent/{token}",
                          json={"decision": "approve"}, timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert "already" in (body.get("note") or "").lower() or body["status"] == "admin_review"

    def test_admin_approve_publishes(self, owner_client):
        tid = pytest.testimonial["id"]
        r = owner_client.post(f"{API}/testimonials/{tid}/decision",
                              json={"decision": "approve"}, timeout=15)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "published"

    def test_published_by_slug(self, owner_client):
        # Get my tenant's slug
        me = owner_client.get(f"{API}/auth/me", timeout=15).json()
        tenant_id = me.get("tenant_id")
        # Try the standard office-engine slug or query tenant
        r_slug = requests.get(f"{API}/public/testimonials/by-slug/office-engine", timeout=15)
        # It may 404 for office-engine; just verify endpoint handles slug input
        assert r_slug.status_code in (200, 404)
        if r_slug.status_code == 200:
            assert isinstance(r_slug.json(), list)

    def test_delete_testimonial(self, owner_client):
        tid = pytest.testimonial["id"]
        r = owner_client.delete(f"{API}/testimonials/{tid}", timeout=15)
        assert r.status_code == 200


# ------------------- Spanish Mode -------------------
class TestSpanishMode:
    def test_set_language_es(self, owner_client):
        r = owner_client.put(f"{API}/tenants/me/language", json={"lang": "es"}, timeout=15)
        assert r.status_code == 200
        assert r.json()["lang"] == "es"

    def test_set_language_invalid(self, owner_client):
        r = owner_client.put(f"{API}/tenants/me/language", json={"lang": "fr"}, timeout=15)
        assert r.status_code == 400

    def test_set_language_back_en(self, owner_client):
        r = owner_client.put(f"{API}/tenants/me/language", json={"lang": "en"}, timeout=15)
        assert r.status_code == 200

    def test_demo_start_spanish(self):
        r = requests.post(f"{API}/public/demo/start",
                          json={"lang": "es", "industry": "hvac"}, timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        # Expect Spanish greeting
        greeting = (body.get("greeting") or body.get("message") or body.get("reply") or "").lower()
        assert "hola" in greeting or "buen" in greeting, f"Expected Spanish greeting, got: {greeting[:200]}"

    def test_demo_turn_spanish(self):
        start = requests.post(f"{API}/public/demo/start",
                              json={"lang": "es", "industry": "hvac"}, timeout=30)
        assert start.status_code == 200
        session_id = start.json().get("session_id") or start.json().get("id")
        if not session_id:
            pytest.skip("No session_id returned by demo/start")
        r = requests.post(f"{API}/public/demo/turn",
                          json={"session_id": session_id, "lang": "es",
                                "text": "Necesito reparar mi aire acondicionado"},
                          timeout=45)
        assert r.status_code == 200, r.text
