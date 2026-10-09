"""Phase 4 backend tests — plans, platform analytics, domain providers,
workspaces/sessions, usage reading plan limits from DB, admin RBAC, and
non-regression on core endpoints.
"""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://127.0.0.1:9").rstrip("/")  # WL-044: never a public server by default
API = f"{BASE_URL}/api"

ADMIN_EMAIL = os.environ.get("ADMIN_TEST_EMAIL", "admin@example.test")  # WL-044: no personal address in the repo
ADMIN_PASSWORD = "AdminPass123!"


def _client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def _register_and_onboard(industry="hvac", prefix="p4own"):
    s = _client()
    email = f"TEST_{prefix}_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "OwnerPass123!"
    r = s.post(f"{API}/auth/register", json={
        "email": email, "password": pwd, "name": f"TEST {prefix}",
        "business_name": f"TEST Biz {uuid.uuid4().hex[:6]}",
    })
    assert r.status_code == 200, r.text
    r2 = s.post(f"{API}/tenants/onboard", json={
        "name": f"TEST Biz {uuid.uuid4().hex[:6]}",
        "industry_slug": industry,
        "description": "Phase 4 test tenant",
        "contact_email": email,
    })
    assert r2.status_code == 200, r2.text
    s._email = email
    return s


@pytest.fixture(scope="module")
def admin():
    s = _client()
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code} {r.text}")
    body = r.json()
    role = body.get("role") or body.get("user", {}).get("role")
    assert role == "platform_admin", body
    return s


@pytest.fixture(scope="module")
def owner():
    return _register_and_onboard()


# ============== Public plans ==============
class TestPublicPlans:
    def test_public_plans_no_auth(self):
        s = _client()
        r = s.get(f"{API}/plans")
        assert r.status_code == 200, r.text
        plans = r.json()
        assert isinstance(plans, list)
        assert len(plans) >= 5, f"expected >=5 plans, got {len(plans)}"
        by_key = {p["key"]: p for p in plans}
        assert by_key["starter"]["price_cents"] == 1999
        assert by_key["growth"]["price_cents"] == 4999
        assert by_key["ai_office"]["price_cents"] == 9999
        assert by_key["high_volume"]["price_cents"] == 19999


# ============== Admin plans CRUD ==============
class TestAdminPlans:
    def test_admin_list_includes_enterprise(self, admin):
        r = admin.get(f"{API}/admin/plans")
        assert r.status_code == 200, r.text
        plans = r.json()
        keys = {p["key"] for p in plans}
        assert "enterprise" in keys
        assert {"trial", "starter", "growth", "ai_office", "high_volume"}.issubset(keys)

    def test_admin_update_plan_limits(self, admin):
        # Fetch current starter plan
        r = admin.get(f"{API}/admin/plans")
        starter = next(p for p in r.json() if p["key"] == "starter")
        new_limits = dict(starter.get("limits", {}))
        new_limits["sms"] = 999  # tweak
        payload = {
            "key": "starter",
            "name": starter["name"],
            "price_cents": starter["price_cents"],
            "interval": starter.get("interval", "month"),
            "limits": new_limits,
            "overage": starter.get("overage", {}),
            "features": starter.get("features", []),
            "trial_days": starter.get("trial_days", 0),
            "is_public": starter.get("is_public", True),
            "sort_order": starter.get("sort_order", 10),
            "description": starter.get("description", ""),
        }
        u = admin.put(f"{API}/admin/plans/starter", json=payload)
        assert u.status_code == 200, u.text
        # Re-fetch and verify
        r2 = admin.get(f"{API}/admin/plans")
        starter2 = next(p for p in r2.json() if p["key"] == "starter")
        assert starter2["limits"]["sms"] == 999


# ============== Admin cost config ==============
class TestCostConfig:
    def test_get_and_update(self, admin):
        r = admin.get(f"{API}/admin/plans/costs/current")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "costs" in body
        new_costs = {"ai_minutes": 7, "calls": 2, "sms": 0.4, "storage_gb": 1, "payment_processing_bps": 290}
        u = admin.put(f"{API}/admin/plans/costs/current", json={"costs": new_costs})
        assert u.status_code == 200, u.text
        assert u.json()["costs"]["ai_minutes"] == 7


# ============== Admin analytics ==============
class TestAnalytics:
    def test_overview_shape(self, admin):
        r = admin.get(f"{API}/admin/analytics/overview")
        assert r.status_code == 200, r.text
        b = r.json()
        assert "mrr" in b and "cents" in b["mrr"]
        assert "arpu_cents" in b
        assert "churn_30d_pct" in b
        assert "conversion_pct" in b
        assert "gross_margin_pct" in b
        assert "ai_attributed_revenue_cents" in b
        assert "usage_totals" in b
        c = b["customers"]
        for k in ("total", "active", "trial", "canceled_30d"):
            assert k in c, f"missing customers.{k}"

    def test_margin_by_plan(self, admin):
        r = admin.get(f"{API}/admin/analytics/margin-by-plan")
        assert r.status_code == 200, r.text
        rows = r.json()
        assert isinstance(rows, list) and len(rows) > 0
        row = rows[0]
        for k in ("plan_key", "tenants", "revenue_cents", "cost_cents", "margin_pct"):
            assert k in row, f"missing {k} in margin-by-plan row"


# ============== Domain providers (tenant) ==============
class TestDomainProviders:
    def test_catalog(self, owner):
        r = owner.get(f"{API}/tenants/domain-providers/catalog")
        assert r.status_code == 200, r.text
        b = r.json()
        assert "active" in b and "default" in b and "providers" in b
        assert len(b["providers"]) >= 1

    def test_search(self, owner):
        r = owner.post(f"{API}/tenants/domain-providers/search", json={"query": "hearthhvac"})
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["provider"] == "demo_registrar"
        assert len(b["results"]) > 0

    def test_purchase_creates_domain_record(self, owner):
        domain = f"hearthhvac{uuid.uuid4().hex[:6]}.com"
        r = owner.post(f"{API}/tenants/domain-providers/purchase",
                       json={"domain": domain, "years": 1})
        assert r.status_code == 200, r.text
        b = r.json()
        assert "domain_record" in b
        assert b["domain_record"]["domain"] == domain.lower()

        # Verify via GET /tenants/domains
        g = owner.get(f"{API}/tenants/domains")
        assert g.status_code == 200
        doms = [d["domain"] for d in g.json()]
        assert domain.lower() in doms


# ============== Admin domain-providers config ==============
class TestAdminDomainProviders:
    def test_get_and_update(self, admin):
        r = admin.get(f"{API}/admin/domain-providers")
        assert r.status_code == 200, r.text
        b = r.json()
        assert "active" in b and "default" in b and "available" in b
        payload = {"active": ["manual", "demo_registrar"], "default": "manual"}
        u = admin.put(f"{API}/admin/domain-providers", json=payload)
        assert u.status_code == 200, u.text
        r2 = admin.get(f"{API}/admin/domain-providers")
        assert r2.json()["default"] == "manual"
        # Restore to demo_registrar default
        admin.put(f"{API}/admin/domain-providers", json={"active": ["manual", "demo_registrar"], "default": "demo_registrar"})


# ============== Workspaces + Sessions ==============
class TestWorkspacesSessions:
    def test_list_workspaces(self, owner):
        r = owner.get(f"{API}/auth/workspaces")
        assert r.status_code == 200, r.text
        ws = r.json()
        assert isinstance(ws, list)
        assert len(ws) >= 1
        current = [w for w in ws if w.get("is_current")]
        assert len(current) == 1

    def test_sessions_no_token_leak(self, owner):
        r = owner.get(f"{API}/auth/sessions")
        assert r.status_code == 200, r.text
        rows = r.json()
        assert isinstance(rows, list)
        for row in rows:
            assert "session_token" not in row, "session_token must not be exposed"
            assert "session_token_tail" in row


# ============== Usage reads from DB plans ==============
class TestUsage:
    def test_me_shape(self, owner):
        r = owner.get(f"{API}/usage/me")
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["plan"] == "trial"  # newly-registered owner defaults to trial
        metrics_by_name = {m["metric"]: m for m in b["metrics"]}
        for key in ("ai_minutes", "calls", "sms", "ai_interactions"):
            assert key in metrics_by_name, f"missing metric {key}"


# ============== RBAC: admin-only endpoints forbidden to tenant owner ==============
class TestRBAC:
    def test_owner_forbidden_from_admin_plans(self, owner):
        r = owner.get(f"{API}/admin/plans")
        assert r.status_code == 403, r.status_code

    def test_owner_forbidden_from_admin_analytics(self, owner):
        r = owner.get(f"{API}/admin/analytics/overview")
        assert r.status_code == 403, r.status_code

    def test_owner_forbidden_from_admin_domain_providers(self, owner):
        r = owner.get(f"{API}/admin/domain-providers")
        assert r.status_code == 403, r.status_code


# ============== Non-regression ==============
class TestNonRegression:
    def test_health(self):
        r = _client().get(f"{API}/health")
        assert r.status_code == 200

    def test_industries(self):
        r = _client().get(f"{API}/industries")
        assert r.status_code == 200
        assert len(r.json()) == 10

    def test_countries_summary(self):
        r = _client().get(f"{API}/countries/summary")
        assert r.status_code == 200

    def test_tenants_me(self, owner):
        r = owner.get(f"{API}/tenants/me")
        assert r.status_code == 200

    def test_conversations_start(self, owner):
        r = owner.post(f"{API}/conversations/start", json={
            "channel": "call",
            "caller_name": "TEST Caller P4",
            "caller_phone": "+15550000911",
            "is_simulation": True,
        })
        assert r.status_code == 200, r.text
        assert r.json()["conversation"]["id"]
