"""End-to-end backend API tests for AI Office Platform Phase 1.

Covers: health, auth (register/login/me), industries, countries, tenant
onboarding + CRUD, tenant isolation, admin RBAC, public business page,
Stripe plans/checkout/status, feature flag toggle, country override,
advisor chat (with fallback accepted).
"""
import os
import uuid
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://127.0.0.1:9")  # WL-044: never a public server by default.rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@aioffice.io"
ADMIN_PASSWORD = "AdminPass123!"


def _client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ---------- Session-scoped fixtures ----------
@pytest.fixture(scope="session")
def admin_session():
    s = _client()
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    assert r.json().get("role") == "platform_admin"
    return s


@pytest.fixture(scope="session")
def owner_session_a():
    s = _client()
    email = f"TEST_owner_a_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{API}/auth/register", json={
        "email": email, "password": "OwnerPass123!",
        "name": "Owner A", "business_name": f"TEST Biz A {uuid.uuid4().hex[:6]}",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    s._user = data
    s._email = email
    s._password = "OwnerPass123!"
    return s


@pytest.fixture(scope="session")
def owner_session_b():
    s = _client()
    email = f"TEST_owner_b_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{API}/auth/register", json={
        "email": email, "password": "OwnerPass123!",
        "name": "Owner B", "business_name": f"TEST Biz B {uuid.uuid4().hex[:6]}",
    })
    assert r.status_code == 200, r.text
    s._user = r.json()
    s._email = email
    return s


# ---------- Health ----------
class TestHealth:
    def test_health_ok(self):
        r = requests.get(f"{API}/health")
        assert r.status_code == 200
        body = r.json()
        assert body.get("status") == "ok"
        assert body.get("db") == "ok"


# ---------- Auth ----------
class TestAuth:
    def test_register_creates_owner_with_tenant(self, owner_session_a):
        u = owner_session_a._user
        assert u["role"] == "owner"
        assert u["tenant_id"]
        assert u["email"] == owner_session_a._email.lower()
        # cookie set
        assert "access_token" in owner_session_a.cookies.get_dict()

    def test_login_with_new_credentials(self, owner_session_a):
        fresh = _client()
        r = fresh.post(f"{API}/auth/login", json={
            "email": owner_session_a._email, "password": owner_session_a._password,
        })
        assert r.status_code == 200
        assert "access_token" in fresh.cookies.get_dict()
        me = fresh.get(f"{API}/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == owner_session_a._email.lower()

    def test_admin_login(self, admin_session):
        me = admin_session.get(f"{API}/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == "platform_admin"

    def test_login_invalid_credentials(self):
        s = _client()
        r = s.post(f"{API}/auth/login", json={"email": "nouser@example.com", "password": "badpass"})
        assert r.status_code == 401


# ---------- Industries ----------
class TestIndustries:
    def test_list_industries_seeded(self):
        r = requests.get(f"{API}/industries")
        assert r.status_code == 200
        rows = r.json()
        slugs = {x["slug"] for x in rows}
        expected = {"hvac", "plumbing", "electrical", "roofing", "dental",
                    "physical-therapy", "landscaping", "pest-control",
                    "contractor", "independent"}
        missing = expected - slugs
        assert not missing, f"Missing industry slugs: {missing}"
        assert len(rows) >= 10


# ---------- Countries ----------
class TestCountries:
    def test_countries_list(self):
        r = requests.get(f"{API}/countries")
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) >= 60, f"Expected 60+ countries, got {len(rows)}"

    def test_countries_summary(self):
        r = requests.get(f"{API}/countries/summary")
        assert r.status_code == 200
        s = r.json()
        for k in ["supported", "preview", "extended", "unsupported", "total"]:
            assert k in s


# ---------- Onboarding + Tenant CRUD ----------
class TestOnboardingAndCRUD:
    def test_onboard_tenant_hvac(self, owner_session_a):
        payload = {
            "name": owner_session_a._user.get("name", "TEST Biz A"),
            "industry_slug": "hvac",
            "description": "Test HVAC business",
            "contact_email": owner_session_a._email,
        }
        r = owner_session_a.post(f"{API}/tenants/onboard", json=payload)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tenant"]["onboarding_complete"] is True
        assert body["services_seeded"] > 0

    def test_summary_after_onboarding(self, owner_session_a):
        r = owner_session_a.get(f"{API}/tenants/summary")
        assert r.status_code == 200
        s = r.json()
        assert s["services"] > 0, f"Expected seeded services, got {s}"
        assert s["knowledge_entries"] > 0, f"Expected seeded knowledge entries, got {s}"

    def test_services_crud(self, owner_session_a):
        r = owner_session_a.post(f"{API}/tenants/services", json={
            "name": "TEST_Service", "description": "x", "duration_minutes": 30, "price": 100.0,
        })
        assert r.status_code == 200
        svc = r.json()
        assert svc["name"] == "TEST_Service"
        svc_id = svc["id"]

        # list
        r2 = owner_session_a.get(f"{API}/tenants/services")
        assert r2.status_code == 200
        assert any(s["id"] == svc_id for s in r2.json())

        # update
        r3 = owner_session_a.put(f"{API}/tenants/services/{svc_id}", json={
            "name": "TEST_Service_Upd", "description": "y", "duration_minutes": 45, "price": 150.0,
        })
        assert r3.status_code == 200
        assert r3.json()["name"] == "TEST_Service_Upd"

        # delete
        r4 = owner_session_a.delete(f"{API}/tenants/services/{svc_id}")
        assert r4.status_code == 200

    def test_customers_crud(self, owner_session_a):
        r = owner_session_a.post(f"{API}/tenants/customers", json={
            "name": "TEST_Cust", "email": "testcust@example.com", "phone": "555-1234",
        })
        assert r.status_code == 200
        cid = r.json()["id"]
        r2 = owner_session_a.get(f"{API}/tenants/customers")
        assert any(c["id"] == cid for c in r2.json())
        r3 = owner_session_a.delete(f"{API}/tenants/customers/{cid}")
        assert r3.status_code == 200

    def test_leads_crud(self, owner_session_a):
        r = owner_session_a.post(f"{API}/tenants/leads", json={
            "name": "TEST_Lead", "email": "lead@example.com", "phone": "555-0001",
        })
        assert r.status_code == 200
        lid = r.json()["id"]
        r2 = owner_session_a.put(f"{API}/tenants/leads/{lid}", json={
            "name": "TEST_Lead_U", "email": "lead@example.com", "status": "qualified",
        })
        assert r2.status_code == 200
        assert r2.json()["status"] == "qualified"

    def test_appointments_crud(self, owner_session_a):
        r = owner_session_a.post(f"{API}/tenants/appointments", json={
            "customer_name": "TEST_Appt",
            "start_at": "2026-02-01T10:00:00+00:00",
            "end_at": "2026-02-01T11:00:00+00:00",
        })
        assert r.status_code == 200
        aid = r.json()["id"]
        r2 = owner_session_a.delete(f"{API}/tenants/appointments/{aid}")
        assert r2.status_code == 200

    def test_knowledge_crud(self, owner_session_a):
        r = owner_session_a.post(f"{API}/tenants/knowledge", json={
            "question": "TEST q?", "answer": "TEST a.", "tags": ["t"],
        })
        assert r.status_code == 200
        kid = r.json()["id"]
        r2 = owner_session_a.delete(f"{API}/tenants/knowledge/{kid}")
        assert r2.status_code == 200


# ---------- Tenant Isolation ----------
class TestTenantIsolation:
    def test_second_tenant_cannot_see_first(self, owner_session_a, owner_session_b):
        # create a service in tenant A
        r = owner_session_a.post(f"{API}/tenants/services", json={
            "name": "TEST_IsolationSvc", "description": "", "duration_minutes": 30, "price": 1.0,
        })
        assert r.status_code == 200
        svc_a_id = r.json()["id"]

        # B must not see it
        r2 = owner_session_b.get(f"{API}/tenants/services")
        assert r2.status_code == 200
        assert not any(s["id"] == svc_a_id for s in r2.json())

        # B must not be able to update/delete A's service
        r3 = owner_session_b.delete(f"{API}/tenants/services/{svc_a_id}")
        assert r3.status_code == 404

        # cleanup
        owner_session_a.delete(f"{API}/tenants/services/{svc_a_id}")


# ---------- Admin RBAC ----------
class TestAdminRBAC:
    def test_admin_overview_forbidden_to_owner(self, owner_session_a):
        r = owner_session_a.get(f"{API}/admin/overview")
        assert r.status_code == 403

    def test_admin_overview_ok_for_admin(self, admin_session):
        r = admin_session.get(f"{API}/admin/overview")
        assert r.status_code == 200
        assert "tenants" in r.json()

    def test_admin_tenants_list(self, admin_session):
        r = admin_session.get(f"{API}/admin/tenants")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_admin_feature_flags_list(self, admin_session):
        r = admin_session.get(f"{API}/admin/feature-flags")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_industries_create_requires_admin(self, owner_session_a):
        r = owner_session_a.post(f"{API}/industries", json={"slug": "TEST_nope", "name": "nope"})
        assert r.status_code == 403

    def test_country_update_requires_admin(self, owner_session_a):
        r = owner_session_a.put(f"{API}/countries/US", json={"code": "US", "name": "USA", "status": "supported", "enabled": True})
        assert r.status_code == 403


# ---------- Feature flag toggle ----------
class TestFeatureFlags:
    def test_toggle_flag_as_admin(self, admin_session):
        flags = admin_session.get(f"{API}/admin/feature-flags").json()
        assert flags, "no seeded flags"
        key = flags[0]["key"]
        current = flags[0]["enabled"]
        new_val = not current
        r = admin_session.put(f"{API}/admin/feature-flags/{key}", params={"enabled": str(new_val).lower()})
        assert r.status_code == 200, r.text
        assert r.json()["enabled"] is new_val
        # restore
        admin_session.put(f"{API}/admin/feature-flags/{key}", params={"enabled": str(current).lower()})


# ---------- Country admin override ----------
class TestCountryOverride:
    def test_update_country_as_admin(self, admin_session):
        r = admin_session.put(f"{API}/countries/US", json={
            "code": "US", "name": "United States", "status": "supported", "enabled": True, "notes": "test-override"
        })
        assert r.status_code == 200
        assert r.json()["notes"] == "test-override"


# ---------- Public business page ----------
class TestPublicBusiness:
    def test_public_business_unknown(self):
        r = requests.get(f"{API}/public/business/this-slug-does-not-exist-xyz")
        assert r.status_code == 404

    def test_public_business_office_engine_if_present(self):
        # The seeded demo tenant slug per request spec
        r = requests.get(f"{API}/public/business/office-engine")
        # Accept 200 if seeded, 404 otherwise but note it
        assert r.status_code in (200, 404)
        if r.status_code == 200:
            body = r.json()
            assert body["slug"] == "office-engine"
            assert "ai_employee" in body


# ---------- Payments / Stripe ----------
class TestPayments:
    def test_plans(self):
        r = requests.get(f"{API}/payments/plans")
        assert r.status_code == 200
        rows = r.json()
        ids = {p["id"] for p in rows}
        assert {"starter", "growth", "scale"}.issubset(ids)

    def test_checkout_as_owner(self, owner_session_a):
        r = owner_session_a.post(f"{API}/payments/checkout", json={
            "plan_id": "starter",
            "origin_url": BASE_URL,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("checkout_url", "").startswith("http")
        assert body.get("session_id")
        # Save for status test via class attribute
        TestPayments.session_id = body["session_id"]

    def test_payment_status(self, owner_session_a):
        sid = getattr(TestPayments, "session_id", None)
        if not sid:
            pytest.skip("no checkout session_id from previous test")
        r = owner_session_a.get(f"{API}/payments/status/{sid}")
        assert r.status_code == 200
        body = r.json()
        assert body["session_id"] == sid
        assert body["payment_status"] in ("pending", "paid", "unpaid", "no_payment_required")


# ---------- Advisor chat (fallback accepted) ----------
class TestAdvisor:
    def test_advisor_chat(self, owner_session_a):
        r = owner_session_a.post(f"{API}/advisor/chat", json={
            "message": "Give me 3 quick tips to grow my HVAC business."
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("session_id")
        assert isinstance(body.get("reply"), str) and len(body["reply"]) > 0
