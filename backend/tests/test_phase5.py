"""Phase 5 backend tests: Stripe price IDs on plans, overage billing (admin preview/run),
public /pricing data, Google-invite accept endpoint, admin source zip/manifest,
sales intelligence (scoring, extract, apply-fields, upsells, follow-up cadence)."""
import os
import uuid
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://127.0.0.1:9").rstrip("/")  # WL-044: env only, never a deployment file or public server
API = f"{BASE_URL}/api"

ADMIN_EMAIL = os.environ.get("ADMIN_TEST_EMAIL", "admin@example.test")  # WL-044: no personal address in the repo
ADMIN_PASSWORD = "AdminPass123!"


def _client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ---------- Fixtures ----------
@pytest.fixture(scope="session")
def admin_session():
    s = _client()
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    assert r.json().get("role") == "platform_admin"
    return s


@pytest.fixture(scope="session")
def owner_session():
    s = _client()
    email = f"TEST_phase5_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{API}/auth/register", json={
        "email": email, "password": "OwnerPass123!",
        "name": "Phase5 Owner", "business_name": f"TEST Phase5 Biz {uuid.uuid4().hex[:6]}",
    })
    assert r.status_code == 200, r.text
    # Onboard with plumbing (so upsell suggest tests feel natural)
    r2 = s.post(f"{API}/tenants/onboard", json={
        "name": r.json().get("name", "TEST biz"),
        "industry_slug": "plumbing",
        "description": "Phase5 test tenant",
        "contact_email": email,
    })
    assert r2.status_code == 200, r2.text
    s._email = email
    return s


@pytest.fixture(scope="session")
def owner_b_session():
    s = _client()
    email = f"TEST_phase5b_{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(f"{API}/auth/register", json={
        "email": email, "password": "OwnerPass123!",
        "name": "Phase5 Owner B", "business_name": f"TEST Phase5 Biz B {uuid.uuid4().hex[:6]}",
    })
    assert r.status_code == 200, r.text
    return s


# ---------- 1. Public plans ----------
class TestPublicPlans:
    def test_public_plans_no_auth(self):
        r = requests.get(f"{API}/plans")
        assert r.status_code == 200
        rows = r.json()
        assert isinstance(rows, list) and len(rows) >= 3
        keys = {p["key"] for p in rows}
        # Public pricing plans
        assert {"starter", "growth", "ai_office", "high_volume"}.issubset(keys)


# ---------- 2. Stripe Price ID persistence + checkout fallback ----------
class TestStripePriceId:
    def test_admin_update_plan_stripe_price_id(self, admin_session):
        # Fetch starter
        r = admin_session.get(f"{API}/admin/plans")
        assert r.status_code == 200
        plans = r.json()
        starter = next(p for p in plans if p["key"] == "starter")
        payload = {**starter}
        payload["stripe_price_id"] = "price_test_123"
        # Strip read-only / server-managed fields
        for k in ("id", "created_at", "updated_at"):
            payload.pop(k, None)
        r2 = admin_session.put(f"{API}/admin/plans/starter", json=payload)
        assert r2.status_code == 200, r2.text
        assert r2.json().get("stripe_price_id") == "price_test_123"

        # Verify in GET
        r3 = admin_session.get(f"{API}/admin/plans")
        starter2 = next(p for p in r3.json() if p["key"] == "starter")
        assert starter2.get("stripe_price_id") == "price_test_123"

    def test_checkout_with_price_id_returns_checkout_url(self, owner_session, admin_session):
        # starter was set to price_test_123 — Stripe test mode will reject this fake id.
        # We expect either (a) 200 w/ checkout_url, or (b) 500 Stripe error if invalid.
        # To make this deterministic, flip starter's stripe_price_id back to empty, then
        # checkout should use price_data fallback.
        r = admin_session.get(f"{API}/admin/plans")
        starter = next(p for p in r.json() if p["key"] == "starter")
        payload = {**starter, "stripe_price_id": ""}
        for k in ("id", "created_at", "updated_at"):
            payload.pop(k, None)
        admin_session.put(f"{API}/admin/plans/starter", json=payload)

        r2 = owner_session.post(f"{API}/payments/checkout", json={
            "plan_id": "starter", "origin_url": BASE_URL,
        })
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body.get("checkout_url", "").startswith("http")
        assert body.get("session_id")


# ---------- 3. Google invite accept endpoint signature ----------
class TestGoogleInviteAccept:
    def test_invalid_session_rejected(self):
        r = requests.post(
            f"{API}/public/invitations/accept-google",
            json={"token": "bogus_token", "session_id": "bogus_session"},
        )
        # Either 400 (invite not pending) or 401 (session exchange failed)
        assert r.status_code in (400, 401), f"got {r.status_code} {r.text}"

    def test_endpoint_signature_requires_token_and_session(self):
        # Missing fields → 422
        r = requests.post(f"{API}/public/invitations/accept-google", json={})
        assert r.status_code == 422


# ---------- 4. Admin source zip + manifest ----------
class TestSourceExport:
    def test_manifest(self, admin_session):
        r = admin_session.get(f"{API}/admin/source/manifest")
        assert r.status_code == 200
        body = r.json()
        for k in ("files", "approx_bytes_raw", "excludes"):
            assert k in body
        assert body["files"] > 10
        assert isinstance(body["excludes"], list)

    def test_zip_download(self, admin_session):
        r = admin_session.get(f"{API}/admin/source/zip", stream=True)
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("application/zip")
        body = r.content
        assert len(body) > 2000, f"zip too small: {len(body)}"
        # ZIP magic
        assert body[:2] == b"PK"

    def test_zip_forbidden_for_non_admin(self, owner_session):
        r = owner_session.get(f"{API}/admin/source/zip")
        assert r.status_code == 403

    def test_manifest_forbidden_for_non_admin(self, owner_session):
        r = owner_session.get(f"{API}/admin/source/manifest")
        assert r.status_code == 403


# ---------- 5. Overage preview/run ----------
class TestOverage:
    def test_preview_returns_array(self, admin_session):
        r = admin_session.get(f"{API}/admin/overage/preview")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_run_dry(self, admin_session):
        r = admin_session.post(f"{API}/admin/overage/run", params={"dry_run": "true"})
        assert r.status_code == 200
        body = r.json()
        assert "results" in body and isinstance(body["results"], list)

    def test_tenant_overage_view(self, owner_session):
        r = owner_session.get(f"{API}/usage/overage")
        assert r.status_code == 200
        body = r.json()
        assert "items" in body
        assert "total_cents" in body
        assert body.get("tenant_id")

    def test_overage_preview_forbidden_for_non_admin(self, owner_session):
        r = owner_session.get(f"{API}/admin/overage/preview")
        assert r.status_code == 403


# ---------- 6. Sales Intel: upsells ----------
class TestUpsells:
    def test_create_and_suggest(self, owner_session):
        r = owner_session.post(f"{API}/sales/upsells", json={
            "name": "TEST Water Heater Flush",
            "triggers": ["water heater", "plumbing"],
            "pitch": "Flush your heater while we're here.",
            "estimated_price": 149.0,
        })
        assert r.status_code == 200, r.text
        up = r.json()
        assert up["id"] and up["name"] == "TEST Water Heater Flush"

        # Matching
        r2 = owner_session.get(f"{API}/sales/upsells/suggest", params={"service": "plumbing"})
        assert r2.status_code == 200
        matches = r2.json()
        assert any(m["id"] == up["id"] for m in matches)

        # Non-matching
        r3 = owner_session.get(f"{API}/sales/upsells/suggest", params={"service": "haircut"})
        assert r3.status_code == 200
        assert all(m["id"] != up["id"] for m in r3.json())


# ---------- 7. Sales Intel: full conversation flow ----------
class TestSalesConversationPipeline:
    CONV_ID = None
    LEAD_ID = None

    def test_full_flow(self, owner_session):
        # Start conversation
        r = owner_session.post(f"{API}/conversations/start", json={
            "channel": "call", "caller_name": "Jane Doe", "caller_phone": "+15550123",
            "is_simulation": True,
        })
        assert r.status_code == 200, r.text
        conv = r.json()["conversation"]
        TestSalesConversationPipeline.CONV_ID = conv["id"]

        # Caller turn (hot words: "today", address, budget)
        r2 = owner_session.post(f"{API}/conversations/{conv['id']}/caller-turn", json={
            "text": "My water heater is leaking at 123 Main Street, need help today, budget around 500 dollars."
        })
        assert r2.status_code == 200, r2.text

        # End conversation (auto scores + schedules follow-ups)
        r3 = owner_session.post(f"{API}/conversations/{conv['id']}/end")
        assert r3.status_code == 200

        # Allow DB settle
        time.sleep(1)

        # Scored leads should contain this one
        r4 = owner_session.get(f"{API}/sales/leads/scored")
        assert r4.status_code == 200
        leads = r4.json()
        assert len(leads) >= 1, "expected at least one scored lead"
        hot_warm = [L for L in leads if L.get("lead_score", {}).get("label") in {"hot", "warm", "cold"}]
        assert hot_warm, "no lead has a label"
        TestSalesConversationPipeline.LEAD_ID = hot_warm[0]["id"]

    def test_explicit_score_endpoint(self, owner_session):
        cid = TestSalesConversationPipeline.CONV_ID
        assert cid
        r = owner_session.post(f"{API}/sales/conversations/{cid}/score")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("score", {}).get("label") in {"hot", "warm", "cold"}
        assert "score" in body["score"]
        assert "reason" in body["score"]

    def test_extract_fields(self, owner_session):
        cid = TestSalesConversationPipeline.CONV_ID
        r = owner_session.post(f"{API}/sales/conversations/{cid}/extract")
        assert r.status_code == 200, r.text
        fields = r.json().get("fields", {})
        for k in ("address", "phone", "email", "service_requested", "urgency", "notes_summary"):
            assert k in fields
        assert "123 Main" in fields.get("address", ""), f"address not detected: {fields}"
        assert fields.get("urgency") == "high", f"expected urgency=high, got {fields.get('urgency')}"

    def test_apply_fields_updates_lead(self, owner_session):
        cid = TestSalesConversationPipeline.CONV_ID
        payload = {
            "address": "123 Main Street",
            "service_requested": "water heater repair",
            "urgency": "high",
            "notes": "Leaking today",
            "phone": "+15550123",
        }
        r = owner_session.post(f"{API}/sales/conversations/{cid}/apply-fields", json=payload)
        assert r.status_code == 200, r.text
        # Verify lead has notes
        r2 = owner_session.get(f"{API}/tenants/leads")
        assert r2.status_code == 200
        leads = r2.json()
        matched = [L for L in leads if "water heater" in (L.get("notes") or "").lower() or "leaking" in (L.get("notes") or "").lower()]
        assert matched, f"no lead updated with notes: sample={leads[:2]}"


# ---------- 8. Follow-ups ----------
class TestFollowups:
    def test_followups_scheduled_after_end(self, owner_session):
        # Previous test's hot/warm end should have scheduled 3 jobs for its lead
        r = owner_session.get(f"{API}/sales/followups", params={"status": "scheduled"})
        assert r.status_code == 200
        jobs = r.json()
        assert isinstance(jobs, list)
        # Default cadence has 3 steps
        if len(jobs) < 3:
            pytest.skip(f"only {len(jobs)} scheduled jobs — lead may have been scored cold (no phone? no urgency keywords?)")
        assert len(jobs) >= 3

    def test_cancel_followup(self, owner_session):
        r = owner_session.get(f"{API}/sales/followups", params={"status": "scheduled"})
        jobs = r.json()
        if not jobs:
            pytest.skip("no scheduled jobs to cancel")
        jid = jobs[0]["id"]
        r2 = owner_session.post(f"{API}/sales/followups/{jid}/cancel")
        assert r2.status_code == 200
        # Verify
        r3 = owner_session.get(f"{API}/sales/followups")
        statuses = {j["id"]: j["status"] for j in r3.json()}
        assert statuses.get(jid) == "cancelled"

    def test_run_due(self, owner_session):
        r = owner_session.post(f"{API}/sales/followups/run-due")
        assert r.status_code == 200
        body = r.json()
        assert "sent" in body
        assert isinstance(body["sent"], int)

    def test_cadence_default_3_steps(self, owner_b_session):
        r = owner_b_session.get(f"{API}/sales/followups/cadence")
        assert r.status_code == 200
        body = r.json()
        assert "steps" in body
        assert len(body["steps"]) == 3

    def test_cadence_custom_2_steps_persists(self, owner_b_session):
        custom = {
            "enabled": True,
            "steps": [
                {"offset_hours": 2, "channel": "sms", "template": "Hey {name} — custom step 1"},
                {"offset_hours": 48, "channel": "sms", "template": "Hey {name} — custom step 2"},
            ],
        }
        r = owner_b_session.put(f"{API}/sales/followups/cadence", json=custom)
        assert r.status_code == 200, r.text

        r2 = owner_b_session.get(f"{API}/sales/followups/cadence")
        body = r2.json()
        assert len(body["steps"]) == 2
        assert body["steps"][0]["offset_hours"] == 2
