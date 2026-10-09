"""Phase 2 backend API tests — conversations, CRM, reviews, invitations,
domains, integrations, automations, portal, usage, tenant isolation.
"""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://127.0.0.1:9")  # WL-044: never a public server by default.rstrip("/")
API = f"{BASE_URL}/api"


def _client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


def _register_and_onboard(industry="hvac", prefix="owner"):
    s = _client()
    email = f"TEST_{prefix}_{uuid.uuid4().hex[:8]}@example.com"
    pwd = "OwnerPass123!"
    r = s.post(f"{API}/auth/register", json={
        "email": email, "password": pwd, "name": f"TEST {prefix}",
        "business_name": f"TEST Biz {uuid.uuid4().hex[:6]}",
    })
    assert r.status_code == 200, r.text
    user = r.json()
    r2 = s.post(f"{API}/tenants/onboard", json={
        "name": user.get("name", "TEST Biz"),
        "industry_slug": industry,
        "description": "Phase 2 test tenant",
        "contact_email": email,
    })
    assert r2.status_code == 200, r2.text
    s._user = user
    s._email = email
    s._password = pwd
    return s


# ---- Session fixtures ----
@pytest.fixture(scope="module")
def owner():
    return _register_and_onboard(prefix="p2own")


@pytest.fixture(scope="module")
def owner_b():
    return _register_and_onboard(prefix="p2own_b")


# ============== Conversations: Receptionist (call) ==============
class TestReceptionistCall:
    def test_start_call_creates_conv_with_greeting(self, owner):
        r = owner.post(f"{API}/conversations/start", json={
            "channel": "call",
            "caller_name": "TEST Caller",
            "caller_phone": "+15550000111",
            "is_simulation": True,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["conversation"]["id"]
        assert body["greeting"] and isinstance(body["greeting"], str)
        TestReceptionistCall.conv_id = body["conversation"]["id"]

        # GET returns conversation + initial AI greeting as first message
        g = owner.get(f"{API}/conversations/{TestReceptionistCall.conv_id}")
        assert g.status_code == 200
        gb = g.json()
        assert gb["conversation"]["id"] == TestReceptionistCall.conv_id
        assert len(gb["messages"]) >= 1
        assert gb["messages"][0]["role"] == "ai"
        assert gb["messages"][0]["content"]

    def test_caller_turn_booking_intent(self, owner):
        conv_id = TestReceptionistCall.conv_id
        r = owner.post(f"{API}/conversations/{conv_id}/caller-turn", json={
            "text": "Hi, I'd like to book an AC tune-up for next Tuesday at 10am please."
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body.get("reply"), str) and len(body["reply"]) > 0

        # Verify messages were logged
        g = owner.get(f"{API}/conversations/{conv_id}")
        msgs = g.json()["messages"]
        roles = [m["role"] for m in msgs]
        assert "caller" in roles and "ai" in roles

        # Verify usage event for ai_interactions
        ev = owner.get(f"{API}/usage/events").json()
        assert any(e["metric"] == "ai_interactions" for e in ev), f"No ai_interactions usage event; events={ev[:3]}"

    def test_escalation_on_emergency(self, owner):
        # New conversation for the emergency scenario
        r = owner.post(f"{API}/conversations/start", json={
            "channel": "call", "caller_name": "TEST Emergency",
            "caller_phone": "+15550000112", "is_simulation": True,
        })
        assert r.status_code == 200
        cid = r.json()["conversation"]["id"]
        r2 = owner.post(f"{API}/conversations/{cid}/caller-turn", json={
            "text": "I smell gas in my house, this is an urgent emergency, please help right now!"
        })
        assert r2.status_code == 200, r2.text
        body = r2.json()
        action = body.get("action") or {}
        reply = (body.get("reply") or "").lower()
        conv_status = (body.get("conversation") or {}).get("status", "")
        escalated = (
            (isinstance(action, dict) and action.get("type") == "escalate_to_human")
            or any(k in reply for k in ["escalat", "dispatch", "911", "emergency line", "right away"])
            or conv_status == "escalated"
        )
        assert escalated, f"Expected escalation signal. action={action} reply={reply[:200]} status={conv_status}"

    def test_fallback_never_500(self, owner):
        # Even obscure inputs should produce a reply (fallback branch), not 500
        r = owner.post(f"{API}/conversations/start", json={
            "channel": "call", "caller_name": "TEST FB", "caller_phone": "+15550000113",
        })
        assert r.status_code == 200
        cid = r.json()["conversation"]["id"]
        r2 = owner.post(f"{API}/conversations/{cid}/caller-turn", json={
            "text": "?" * 3
        })
        assert r2.status_code == 200, f"Expected graceful reply, got {r2.status_code}: {r2.text}"
        assert len(r2.json().get("reply", "")) > 0


# ============== SMS ==============
class TestSMS:
    def test_sms_inbound_sim_creates_thread_and_replies(self, owner):
        r = owner.post(f"{API}/conversations/sms/inbound-sim", json={
            "from": "+15559990001", "body": "Hi, is anyone available to help?", "name": "TEST SMS"
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("thread_id")
        assert isinstance(body.get("reply"), str) and len(body["reply"]) > 0
        TestSMS.thread_id = body["thread_id"]

    def test_sms_send_creates_or_augments_thread_and_records_usage(self, owner):
        r = owner.post(f"{API}/conversations/sms/send", json={
            "to": "+15559990002", "body": "Hello from the business.", "name": "TEST SMS Out"
        })
        assert r.status_code == 200, r.text
        tid = r.json()["thread_id"]
        assert tid
        ev = owner.get(f"{API}/usage/events").json()
        assert any(e["metric"] == "sms" for e in ev), "No sms usage event recorded"

        # Threads listing
        t = owner.get(f"{API}/conversations/sms/threads")
        assert t.status_code == 200
        assert any(th["id"] == tid for th in t.json())


# ============== Estimates / Invoices ==============
class TestCRM:
    def test_estimate_create_computes_total_send_delete(self, owner):
        payload = {
            "customer_name": "TEST Cust Est", "customer_phone": "+15550000200",
            "title": "AC Tune-up", "notes": "",
            "lines": [
                {"description": "Inspection", "quantity": 1, "unit_price": 100.0},
                {"description": "Filter", "quantity": 2, "unit_price": 25.5},
            ],
        }
        r = owner.post(f"{API}/tenants/estimates", json=payload)
        assert r.status_code == 200, r.text
        est = r.json()
        assert abs(est["total"] - (100 + 2 * 25.5)) < 1e-6
        eid = est["id"]

        # mark sent
        rs = owner.post(f"{API}/tenants/estimates/{eid}/send")
        assert rs.status_code == 200
        assert rs.json()["status"] == "sent"

        # GET verify persisted
        lst = owner.get(f"{API}/tenants/estimates").json()
        assert any(e["id"] == eid and e["status"] == "sent" for e in lst)

        # delete
        rd = owner.delete(f"{API}/tenants/estimates/{eid}")
        assert rd.status_code == 200

    def test_invoice_create_mark_paid_delete(self, owner):
        payload = {
            "customer_name": "TEST Cust Inv", "customer_phone": "+15550000201",
            "title": "Service", "notes": "",
            "lines": [{"description": "Labor", "quantity": 3, "unit_price": 50.0}],
        }
        r = owner.post(f"{API}/tenants/invoices", json=payload)
        assert r.status_code == 200, r.text
        inv = r.json()
        assert abs(inv["total"] - 150.0) < 1e-6
        iid = inv["id"]

        rp = owner.post(f"{API}/tenants/invoices/{iid}/mark-paid")
        assert rp.status_code == 200
        body = rp.json()
        assert body["status"] == "paid"
        assert body.get("paid_at")

        rd = owner.delete(f"{API}/tenants/invoices/{iid}")
        assert rd.status_code == 200


# ============== Reviews ==============
class TestReviews:
    def test_create_review_request_and_submit_public(self, owner):
        r = owner.post(f"{API}/tenants/reviews", json={
            "customer_name": "TEST Rev", "customer_phone": "+15550000300",
            "customer_email": "testrev@example.com", "channel": "sms",
        })
        assert r.status_code == 200, r.text
        rr = r.json()
        token = rr["public_token"]
        TestReviews.token = token

        # Public peek
        rg = requests.get(f"{API}/public/reviews/{token}")
        assert rg.status_code == 200
        assert rg.json()["request"]["public_token"] == token

        # Public submit
        rp = requests.post(f"{API}/public/reviews/{token}", json={"rating": 5, "comment": "Great!"})
        assert rp.status_code == 200

        # Verify persisted status
        lst = owner.get(f"{API}/tenants/reviews").json()
        found = [x for x in lst if x["public_token"] == token]
        assert found and found[0]["status"] == "responded" and found[0]["rating"] == 5


# ============== Invitations ==============
class TestInvitations:
    def test_create_invite_peek_accept_and_staff(self, owner):
        invite_email = f"TEST_staff_{uuid.uuid4().hex[:8]}@example.com"
        r = owner.post(f"{API}/tenants/invitations", json={"email": invite_email, "role": "staff"})
        assert r.status_code == 200, r.text
        inv = r.json()
        token = inv["token"]
        assert inv["status"] == "pending"

        # peek
        rp = requests.get(f"{API}/public/invitations/{token}")
        assert rp.status_code == 200
        assert rp.json()["invitation"]["email"] == invite_email.lower()

        # accept — bootstraps session cookies
        s2 = _client()
        ra = s2.post(f"{API}/public/invitations/accept", json={
            "token": token, "password": "StaffPass123!", "name": "TEST Staff",
        })
        assert ra.status_code == 200, ra.text
        body = ra.json()
        assert body["role"] == "staff"
        assert "access_token" in s2.cookies.get_dict()

        # me via new session
        me = s2.get(f"{API}/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == invite_email.lower()

        # staff listing by owner contains new user
        staff = owner.get(f"{API}/tenants/invitations/staff").json()
        new_user_id = body["id"]
        assert any(u["id"] == new_user_id for u in staff)

        # role flip to admin
        rf = owner.put(f"{API}/tenants/invitations/staff/{new_user_id}/role", params={"role": "admin"})
        assert rf.status_code == 200, rf.text
        staff2 = owner.get(f"{API}/tenants/invitations/staff").json()
        assert any(u["id"] == new_user_id and u["role"] == "admin" for u in staff2)


# ============== Domains ==============
class TestDomains:
    def test_add_and_verify_no_500(self, owner):
        dom = f"test-{uuid.uuid4().hex[:8]}.example.com"
        r = owner.post(f"{API}/tenants/domains", json={"domain": dom})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["domain"] == dom
        assert d["cname_target"]
        did = d["id"]

        v = owner.post(f"{API}/tenants/domains/{did}/verify")
        assert v.status_code == 200, f"Verify must not 500: {v.status_code} {v.text}"
        assert v.json()["status"] in ("pending", "failed", "verified")


# ============== Integrations ==============
class TestIntegrations:
    def test_catalog_has_known_keys(self, owner):
        r = owner.get(f"{API}/tenants/integrations/catalog")
        assert r.status_code == 200
        keys = {x["key"] for x in r.json()}
        assert {"twilio", "gmail", "stripe", "google_my_business"}.issubset(keys)

    def test_upsert_twilio_missing_fields_is_disconnected(self, owner):
        r = owner.post(f"{API}/tenants/integrations", json={
            "key": "twilio", "enabled": True, "config": {"account_sid": "AC_test"}
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["integration"]["status"] == "disconnected"
        assert "auth_token" in body["missing"] and "phone_number" in body["missing"]

    def test_upsert_twilio_full_fields_connected(self, owner):
        r = owner.post(f"{API}/tenants/integrations", json={
            "key": "twilio", "enabled": True,
            "config": {"account_sid": "AC_x", "auth_token": "tkn", "phone_number": "+15555550000"}
        })
        assert r.status_code == 200
        assert r.json()["integration"]["status"] == "connected"


# ============== Automations ==============
class TestAutomations:
    def test_defaults_then_put(self, owner):
        r = owner.get(f"{API}/tenants/automations")
        assert r.status_code == 200
        d = r.json()
        assert d.get("appointment_reminders") is True

        patch = dict(d)
        patch.pop("tenant_id", None)
        patch.pop("updated_at", None)
        patch["appointment_reminders"] = False
        patch["reminder_hours_before"] = 12
        p = owner.put(f"{API}/tenants/automations", json=patch)
        assert p.status_code == 200, p.text
        g = owner.get(f"{API}/tenants/automations").json()
        assert g["appointment_reminders"] is False
        assert g["reminder_hours_before"] == 12


# ============== Usage ==============
class TestUsage:
    def test_usage_me_shape_and_ai_interactions_counted(self, owner):
        # Fire one more caller-turn to be sure we have ai_interactions this run
        r = owner.post(f"{API}/conversations/start", json={
            "channel": "call", "caller_name": "TEST Usage", "caller_phone": "+15550000400"
        })
        cid = r.json()["conversation"]["id"]
        owner.post(f"{API}/conversations/{cid}/caller-turn", json={"text": "Book me."})

        u = owner.get(f"{API}/usage/me")
        assert u.status_code == 200
        body = u.json()
        assert "metrics" in body and isinstance(body["metrics"], list)
        for m in body["metrics"]:
            for k in ("used", "limit", "pct", "warning_tier"):
                assert k in m
        ai = next((m for m in body["metrics"] if m["metric"] == "ai_interactions"), None)
        assert ai and ai["used"] > 0, f"ai_interactions.used must be > 0, got {ai}"


# ============== Portal ==============
@pytest.fixture(scope="module")
def owner_slug(owner):
    me = owner.get(f"{API}/auth/me").json()
    t = owner.get(f"{API}/tenants/mine")
    if t.status_code != 200:
        # fallback — look up by admin
        pytest.skip("No tenant slug accessor")
    return t.json().get("slug")


class TestPortal:
    def _slug(self, owner):
        r = owner.get(f"{API}/tenants/me")
        if r.status_code == 200:
            return r.json().get("slug")
        return None

    def test_portal_home(self, owner):
        slug = self._slug(owner)
        if not slug:
            pytest.skip("no slug accessor")
        r = requests.get(f"{API}/portal/{slug}")
        assert r.status_code == 200, r.text
        body = r.json()
        for k in ("name", "branding", "services", "hours"):
            assert k in body

    def test_portal_service_request_creates_lead(self, owner):
        slug = self._slug(owner)
        if not slug:
            pytest.skip("no slug accessor")
        rsq = requests.post(f"{API}/portal/{slug}/service-request", json={
            "name": "TEST Portal Lead", "phone": "+15550000500",
            "service": "AC Tune-up", "preferred_time": "soon", "notes": "test",
        })
        assert rsq.status_code == 200, rsq.text
        assert rsq.json().get("lead_id")
        # Verify lead persisted with source=portal
        leads = owner.get(f"{API}/tenants/leads").json()
        assert any(l.get("source") == "portal" for l in leads)

    def test_portal_book_creates_appointment(self, owner):
        slug = self._slug(owner)
        if not slug:
            pytest.skip("no slug accessor")
        r = requests.get(f"{API}/portal/{slug}")
        services = r.json().get("services") or []
        svc_id = services[0]["id"] if services else None
        rb = requests.post(f"{API}/portal/{slug}/book", json={
            "customer_name": "TEST Portal Book",
            "customer_phone": "+15550000501",
            "service_id": svc_id,
            "start_at": "2026-03-01T10:00:00+00:00",
            "notes": "test book",
        })
        assert rb.status_code == 200, rb.text
        assert rb.json().get("appointment_id")
        # Verify customer created
        custs = owner.get(f"{API}/tenants/customers").json()
        assert any(c.get("phone") == "+15550000501" for c in custs)

    def test_portal_magic_link_always_200(self, owner):
        slug = self._slug(owner)
        if not slug:
            pytest.skip("no slug accessor")
        r1 = requests.post(f"{API}/portal/{slug}/magic-link", json={"phone": "+19999999999"})
        assert r1.status_code == 200
        r2 = requests.post(f"{API}/portal/{slug}/magic-link", json={"phone": "+15550000501"})
        assert r2.status_code == 200


# ============== Tenant Isolation ==============
class TestTenantIsolationPhase2:
    def test_cross_tenant_access_blocked(self, owner, owner_b):
        # A creates a conversation
        ra = owner.post(f"{API}/conversations/start", json={
            "channel": "call", "caller_name": "TEST ISO", "caller_phone": "+15550000600",
        })
        assert ra.status_code == 200
        a_conv = ra.json()["conversation"]["id"]

        # A creates estimate, invoice, review request, invite, domain
        est = owner.post(f"{API}/tenants/estimates", json={
            "customer_name": "ISO Est", "customer_phone": "+1", "title": "x",
            "lines": [{"description": "l", "quantity": 1, "unit_price": 1}],
        }).json()
        inv = owner.post(f"{API}/tenants/invoices", json={
            "customer_name": "ISO Inv", "customer_phone": "+1", "title": "x",
            "lines": [{"description": "l", "quantity": 1, "unit_price": 1}],
        }).json()
        rev = owner.post(f"{API}/tenants/reviews", json={
            "customer_name": "ISO Rev", "customer_phone": "+1", "channel": "sms",
        }).json()
        inv_token_resp = owner.post(f"{API}/tenants/invitations", json={
            "email": f"TEST_iso_{uuid.uuid4().hex[:6]}@example.com", "role": "staff"
        }).json()
        dom_resp = owner.post(f"{API}/tenants/domains", json={
            "domain": f"iso-{uuid.uuid4().hex[:6]}.example.com"
        }).json()

        # B cannot read / modify A's resources
        assert owner_b.get(f"{API}/conversations/{a_conv}").status_code in (403, 404)
        assert owner_b.delete(f"{API}/tenants/estimates/{est['id']}").status_code in (403, 404)
        assert owner_b.delete(f"{API}/tenants/invoices/{inv['id']}").status_code in (403, 404)

        # B listing must not include A's resources
        for path, _id in [
            ("/tenants/estimates", est["id"]),
            ("/tenants/invoices", inv["id"]),
            ("/tenants/reviews", rev["id"]),
            ("/tenants/invitations", inv_token_resp["id"]),
            ("/tenants/domains", dom_resp["id"]),
            ("/conversations", a_conv),
        ]:
            lst = owner_b.get(f"{API}{path}")
            assert lst.status_code == 200
            assert not any(x.get("id") == _id for x in lst.json()), f"B saw A's {path}"
