"""Phase 6 backend tests: Twilio webhooks, Growth (digest/winback/referrals),
Discount policy, Realtime token, Weekly digest cron, AI with discount policy, Invitations."""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://office-engine.preview.emergentagent.com").rstrip("/")

# Load WEBHOOK_CRON_SECRET from backend/.env
def _load_cron_secret():
    try:
        with open("/app/backend/.env") as f:
            for line in f:
                if line.startswith("WEBHOOK_CRON_SECRET="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""

CRON_SECRET = _load_cron_secret()


@pytest.fixture(scope="module")
def owner_session():
    """Register fresh tenant owner, onboarded with industry, return authenticated session + info."""
    s = requests.Session()
    uniq = uuid.uuid4().hex[:8]
    email = f"qa+phase6_{uniq}@example.com"
    password = "testpass123"
    r = s.post(f"{BASE_URL}/api/auth/register", json={
        "email": email, "password": password, "name": "Phase6 Owner",
        "business_name": f"TEST_P6_{uniq}",
    })
    assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
    # onboard with plumbing industry
    try:
        s.post(f"{BASE_URL}/api/tenants/me/onboard", json={"industry_slug": "plumbing"})
    except Exception:
        pass
    return {"session": s, "email": email, "password": password}


@pytest.fixture(scope="module")
def owner(owner_session):
    return owner_session


# ---------- Smoke ----------
def test_health():
    r = requests.get(f"{BASE_URL}/api/health")
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


# ---------- Twilio webhooks ----------
# EMP-FIX-034: /api/twilio/* needs a valid X-Twilio-Signature. Set TWILIO_TEST_AUTH_TOKEN to the
# server's TWILIO_AUTH_TOKEN (and its PUBLIC_BACKEND_URL to BASE_URL) to run the signed checks.
TWILIO_TEST_AUTH_TOKEN = os.environ.get("TWILIO_TEST_AUTH_TOKEN", "")


def _twilio_post(path, data):
    headers = {}
    if TWILIO_TEST_AUTH_TOKEN:
        from twilio.request_validator import RequestValidator
        url = f"{BASE_URL}{path}"
        headers["X-Twilio-Signature"] = RequestValidator(TWILIO_TEST_AUTH_TOKEN).compute_signature(url, data)
    return requests.post(f"{BASE_URL}{path}", data=data, headers=headers)


class TestTwilioWebhooks:
    def test_unsigned_webhook_is_rejected(self):
        r = requests.post(f"{BASE_URL}/api/twilio/voice",
                          data={"To": "+15555550199", "From": "+15551234567", "CallSid": "CA_demo"})
        assert r.status_code == 403

    @pytest.fixture(autouse=True)
    def _needs_token(self, request):
        if request.function.__name__ != "test_unsigned_webhook_is_rejected" and not TWILIO_TEST_AUTH_TOKEN:
            pytest.skip("set TWILIO_TEST_AUTH_TOKEN to sign Twilio webhook requests")

    def test_voice_unresolved_returns_twiml(self):
        r = _twilio_post("/api/twilio/voice",
                         {"To": "+15555550199", "From": "+15551234567", "CallSid": "CA_demo"})
        assert r.status_code == 200
        assert "<?xml" in r.text
        assert "<Response" in r.text
        # unresolved number → "not configured"
        assert "not configured" in r.text.lower()

    def test_sms_unregistered_returns_empty_response(self):
        r = _twilio_post("/api/twilio/sms",
                         {"To": "+15555550199", "From": "+15551234567", "Body": "hi"})
        assert r.status_code == 200
        assert "<Response/>" in r.text or "<Response></Response>" in r.text

    def test_missed_call_completed_ignored(self):
        r = _twilio_post("/api/twilio/missed-call",
                         {"CallStatus": "completed", "To": "+15555550199", "From": "+15551234567"})
        assert r.status_code == 200
        assert r.json().get("ignored") is True

    def test_missed_call_no_answer_unresolved_ignored(self):
        r = _twilio_post("/api/twilio/missed-call",
                         {"CallStatus": "no-answer", "To": "+15555550199", "From": "+15551234567"})
        assert r.status_code == 200
        assert r.json().get("ignored") is True


# ---------- Discount policy ----------
class TestDiscountPolicy:
    def test_get_defaults(self, owner):
        r = owner["session"].get(f"{BASE_URL}/api/sales/discount-policy")
        assert r.status_code == 200
        d = r.json()
        assert "enabled" in d and "max_percent_off" in d and "phrase" in d

    def test_put_and_persist(self, owner):
        s = owner["session"]
        payload = {
            "enabled": True, "max_percent_off": 15, "max_absolute_cents": 5000,
            "phrase": "I can match that quote.", "conditions": "Only mentioned competing quote.",
        }
        r = s.put(f"{BASE_URL}/api/sales/discount-policy", json=payload)
        assert r.status_code == 200
        g = s.get(f"{BASE_URL}/api/sales/discount-policy").json()
        assert g["enabled"] is True
        assert g["max_percent_off"] == 15
        assert g["max_absolute_cents"] == 5000
        assert g["phrase"] == "I can match that quote."


# ---------- Realtime token ----------
class TestRealtimeToken:
    def test_realtime_fallback_when_no_key(self, owner):
        # Without OPENAI_API_KEY, should return available:false fallback:'web-speech'
        r = owner["session"].post(f"{BASE_URL}/api/realtime/token")
        assert r.status_code == 200
        d = r.json()
        if not os.environ.get("OPENAI_API_KEY"):
            assert d.get("available") is False
            assert d.get("fallback") == "web-speech"


# ---------- Growth digest ----------
class TestGrowthDigest:
    def test_digest_preview(self, owner):
        r = owner["session"].get(f"{BASE_URL}/api/growth/digest/preview")
        assert r.status_code == 200
        d = r.json()
        assert "stats" in d
        assert "highlights" in d
        assert "tenant_name" in d

    def test_digest_send_falls_back_to_user_email(self, owner):
        # No contact_email set — should fall back to user email → 200 sent/logged
        r = owner["session"].post(f"{BASE_URL}/api/growth/digest/send", json={})
        assert r.status_code == 200
        d = r.json()
        assert d.get("status") in {"sent", "logged"}
        assert d.get("to") == owner["email"]


# ---------- Winback ----------
class TestWinback:
    def test_winback_preview(self, owner):
        r = owner["session"].post(f"{BASE_URL}/api/growth/winback/preview", json={
            "days_inactive": 30, "channel": "sms",
            "message": "Hi {name}, miss you at {business}",
        })
        assert r.status_code == 200
        d = r.json()
        assert "eligible" in d
        assert isinstance(d.get("sample"), list)

    def test_winback_dry_run_default(self, owner):
        r = owner["session"].post(f"{BASE_URL}/api/growth/winback/run", json={
            "days_inactive": 30, "channel": "sms",
            "message": "Hi {name}, miss you at {business}",
        })
        assert r.status_code == 200
        d = r.json()
        assert "campaign_id" in d
        assert d.get("sent") == 0
        assert d.get("dry_run") is True

    def test_winback_real_run_logged(self, owner):
        r = owner["session"].post(f"{BASE_URL}/api/growth/winback/run", json={
            "days_inactive": 30, "channel": "sms", "dry_run": False,
            "message": "Hi {name}, miss you at {business}",
        })
        assert r.status_code == 200
        d = r.json()
        assert "campaign_id" in d
        assert d.get("dry_run") is False
        # Verify campaign is listed
        g = owner["session"].get(f"{BASE_URL}/api/growth/campaigns")
        assert g.status_code == 200
        campaigns = g.json()
        assert any(c.get("id") == d["campaign_id"] for c in campaigns)


# ---------- Referrals ----------
@pytest.fixture(scope="module")
def issued_referral(owner):
    s = owner["session"]
    r = s.post(f"{BASE_URL}/api/growth/referrals/issue", json={"customer_name": "Alice"})
    assert r.status_code == 200
    d = r.json()
    assert "code" in d and len(d["code"]) == 8
    assert "id" in d
    return d


class TestReferrals:
    def test_issue_and_list(self, owner, issued_referral):
        g = owner["session"].get(f"{BASE_URL}/api/growth/referrals")
        assert g.status_code == 200
        rows = g.json()
        assert any(r["code"] == issued_referral["code"] for r in rows)

    def test_public_visit_and_convert(self, owner, issued_referral):
        code = issued_referral["code"]
        v = requests.post(f"{BASE_URL}/api/public/r/{code}/visit")
        assert v.status_code == 200
        d = v.json()
        assert "referrer" in d
        assert d["business"]["name"]
        c = requests.post(f"{BASE_URL}/api/public/r/{code}/convert")
        assert c.status_code == 200
        assert c.json().get("ok") is True
        # Counters incremented
        rows = owner["session"].get(f"{BASE_URL}/api/growth/referrals").json()
        row = next(r for r in rows if r["code"] == code)
        assert row["visits"] >= 1
        assert row["conversions"] >= 1

    def test_unknown_code_404(self):
        r = requests.post(f"{BASE_URL}/api/public/r/NOPE1234/visit")
        assert r.status_code == 404


# ---------- Weekly digest cron ----------
class TestWeeklyDigestCron:
    def test_unauth_401(self):
        r = requests.post(f"{BASE_URL}/api/cron/weekly-digest")
        assert r.status_code == 401

    def test_authed_accepted(self):
        assert CRON_SECRET, "WEBHOOK_CRON_SECRET missing"
        r = requests.post(f"{BASE_URL}/api/cron/weekly-digest",
                          headers={"Authorization": f"Bearer {CRON_SECRET}"})
        assert r.status_code == 200
        assert r.json().get("accepted") is True


# ---------- AI receptionist with discount policy ----------
class TestAIWithDiscountPolicy:
    def test_conversation_with_competing_quote(self, owner):
        s = owner["session"]
        # Ensure policy enabled
        s.put(f"{BASE_URL}/api/sales/discount-policy", json={
            "enabled": True, "max_percent_off": 15, "max_absolute_cents": 5000,
            "phrase": "I can offer ${amount} off today.",
            "conditions": "Only when caller mentions competing quote.",
        })
        start = s.post(f"{BASE_URL}/api/conversations/start",
                      json={"channel": "call", "caller_phone": "+15551112222"})
        assert start.status_code == 200
        conv_id = start.json()["conversation"]["id"]
        turn = s.post(f"{BASE_URL}/api/conversations/{conv_id}/caller-turn",
                     json={"text": "Another plumber quoted me $400 for the same job, can you beat it?"})
        assert turn.status_code == 200
        d = turn.json()
        assert "reply" in d


# ---------- Invitations send ----------
class TestInvitations:
    def test_create_invitation(self, owner):
        s = owner["session"]
        r = s.post(f"{BASE_URL}/api/tenants/invitations", json={
            "email": f"qa+invite_{uuid.uuid4().hex[:6]}@example.com", "role": "staff",
        })
        assert r.status_code == 200, f"invite failed: {r.status_code} {r.text}"
