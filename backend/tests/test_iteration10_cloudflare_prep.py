"""Iteration 10 — Cloudflare deployment-prep regression tests.

Scope:
    1. /api/health/deployment probe correctness
    2. LLM shim does NOT regress AI receptionist (EN + ES)
    3. Testimonial auto-polish still works
    4. Repeat scheduling CRUD still works
    5. Workspace isolation (two tenants, no cross-leakage)
    6. CORS preflight
"""
import os
import uuid
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://127.0.0.1:9").rstrip("/")  # WL-044: never a public server by default
API = f"{BASE_URL}/api"

OWNER_A_EMAIL = "repeat-tester@example.com"
OWNER_A_PASSWORD = "StrongPass123!"


def _apply_token(s, data):
    tok = data.get("access_token") or data.get("token") if isinstance(data, dict) else None
    if tok:
        s.headers.update({"Authorization": f"Bearer {tok}"})


def _register_fresh_tenant():
    email = f"iter10-{uuid.uuid4().hex[:8]}@example.com"
    pwd = "StrongPass123!"
    biz = f"Iter10Biz{uuid.uuid4().hex[:6]}"
    s = requests.Session()
    r = s.post(f"{API}/auth/register", json={
        "email": email, "password": pwd, "name": "Iter10 Owner", "business_name": biz
    })
    assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
    _apply_token(s, r.json())
    # Verify authenticated
    me = s.get(f"{API}/auth/me")
    assert me.status_code == 200, f"auth/me after register failed: {me.status_code} {me.text}"
    # Onboard if needed (best-effort)
    try:
        s.post(f"{API}/tenants/me/onboard", json={"industry_slug": "hvac"})
    except Exception:
        pass
    return email, pwd, s


def _login_session(email, pwd):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": pwd})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    _apply_token(s, r.json())
    me = s.get(f"{API}/auth/me")
    assert me.status_code == 200, f"auth/me after login failed: {me.status_code} {me.text}"
    return s


# ---------- 1. Deployment probe ----------
class TestDeploymentHealth:
    def test_deployment_probe_ready(self):
        r = requests.get(f"{API}/health/deployment", timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        print(f"PROBE: {d}")
        assert d.get("ready") is True, f"not ready: {d}"
        assert d.get("db_ok") is True
        assert d.get("tenant_isolation_ok") is True
        assert d.get("missing_required") == []
        assert d.get("llm_backend") == "emergent", f"expected emergent, got {d.get('llm_backend')}"
        counts = d.get("counts", {})
        for k in ("tenants", "users", "plans"):
            assert isinstance(counts.get(k), int) and counts[k] >= 0, f"bad count {k}: {counts}"


# ---------- 2. CORS preflight ----------
class TestCORS:
    def test_cors_preflight_health(self):
        r = requests.options(
            f"{API}/health/deployment",
            headers={
                "Origin": "https://example-origin.pages.dev",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "content-type",
            },
            timeout=10,
        )
        # Must be 200 or 204, and must echo an allow-origin header
        assert r.status_code in (200, 204), f"preflight failed: {r.status_code} {r.text}"
        allow = r.headers.get("access-control-allow-origin") or r.headers.get("Access-Control-Allow-Origin")
        assert allow, f"no CORS allow-origin header: {dict(r.headers)}"


# ---------- 3. AI demo (EN + ES) via llm_portable shim ----------
class TestAIDemoShim:
    def test_demo_english_reply_nonempty(self):
        r = requests.post(f"{API}/public/demo/start", json={"industry": "hvac", "lang": "en"})
        assert r.status_code == 200, r.text
        sid = r.json()["session_id"]
        time.sleep(1)
        r2 = requests.post(f"{API}/public/demo/turn",
                           json={"session_id": sid, "text": "My AC is making a loud grinding noise, can someone come today?"},
                           timeout=60)
        assert r2.status_code == 200, r2.text
        reply = r2.json().get("reply", "")
        print(f"EN reply: {reply!r}")
        assert reply and len(reply) > 5, f"empty EN reply: {reply!r}"
        assert "glitch" not in reply.lower(), f"LLM errored out: {reply!r}"

    def test_demo_spanish_reply_in_spanish(self):
        r = requests.post(f"{API}/public/demo/start", json={"industry": "hvac", "lang": "es"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["lang"] == "es"
        sid = d["session_id"]
        time.sleep(1)
        r2 = requests.post(f"{API}/public/demo/turn",
                           json={"session_id": sid, "text": "Hola, mi aire acondicionado no enfría, ¿pueden venir hoy?"},
                           timeout=60)
        assert r2.status_code == 200, r2.text
        reply = r2.json().get("reply", "")
        print(f"ES reply: {reply!r}")
        assert reply and len(reply) > 5
        assert "glitch" not in reply.lower()
        # Heuristic: Spanish reply should include a Spanish-language signal
        lowered = reply.lower()
        es_signals = ["hola", "gracias", "sí", "si,", "puedo", "podemos", "cita", "aire", "acondicionado",
                      "lo siento", "claro", "por supuesto", "cuando", "cuándo", "qué", "está", "estoy",
                      "le", "para usted", "nombre"]
        assert any(s in lowered for s in es_signals), f"reply doesn't look Spanish: {reply!r}"


# ---------- 4. Testimonial polish ----------
class TestTestimonialPolish:
    def test_seed_5star_sets_polished_text(self):
        s = _login_session(OWNER_A_EMAIL, OWNER_A_PASSWORD)
        payload = {
            "customer_name": "TEST_Iter10_Polish",
            "customer_email": f"iter10polish-{uuid.uuid4().hex[:6]}@example.com",
            "rating": 5,
            "original_text": "They were on time, fixed my furnace fast, super polite. Highly recommend!",
            "service_name": "Furnace Repair",
        }
        r = s.post(f"{API}/testimonials/seed", json=payload, timeout=60)
        assert r.status_code in (200, 201), r.text
        doc = r.json()
        if "testimonial" in doc and isinstance(doc["testimonial"], dict):
            doc = doc["testimonial"]
        polished = doc.get("polished_text") or doc.get("polished") or ""
        print(f"polished_text: {polished!r}")
        assert polished and len(polished) > 10, f"no polished_text in: {doc}"


# ---------- 5. Repeat scheduling CRUD ----------
class TestRepeatCRUD:
    def test_defaults_list_create_remind_delete(self):
        s = _login_session(OWNER_A_EMAIL, OWNER_A_PASSWORD)

        r = s.get(f"{API}/repeat/defaults")
        assert r.status_code == 200, r.text
        defaults = r.json()
        assert "default_cadence" in defaults
        assert "options" in defaults

        payload = {
            "customer_name": "TEST_Iter10_Repeat",
            "customer_email": f"iter10rep-{uuid.uuid4().hex[:6]}@example.com",
            "service_name": "HVAC Tune-up",
            "cadence": defaults["default_cadence"],
            "start_date": "2026-03-01",
        }
        rc = s.post(f"{API}/repeat/schedules", json=payload)
        assert rc.status_code in (200, 201), rc.text
        sched = rc.json()
        sid = sched.get("id") or sched.get("_id")
        assert sid, sched

        rl = s.get(f"{API}/repeat/schedules")
        assert rl.status_code == 200
        items = rl.json()
        if isinstance(items, dict):
            items = items.get("schedules") or items.get("items") or []
        assert any((it.get("id") or it.get("_id")) == sid for it in items), "created schedule not in list"

        rr = s.post(f"{API}/repeat/schedules/{sid}/send-reminder")
        assert rr.status_code in (200, 202), rr.text

        rd = s.delete(f"{API}/repeat/schedules/{sid}")
        assert rd.status_code in (200, 204), rd.text


# ---------- 6. Workspace isolation ----------
class TestWorkspaceIsolation:
    def test_two_tenants_no_cross_leakage(self):
        emailA, pwdA, sA = _register_fresh_tenant()
        emailB, pwdB, sB = _register_fresh_tenant()

        # A creates a repeat schedule
        defaults = sA.get(f"{API}/repeat/defaults").json()
        payload = {
            "customer_name": f"TEST_IsoA_{uuid.uuid4().hex[:5]}",
            "customer_email": "isoa@example.com",
            "service_name": "Isolation Service A",
            "cadence": defaults["default_cadence"],
            "start_date": "2026-04-01",
        }
        rA = sA.post(f"{API}/repeat/schedules", json=payload)
        assert rA.status_code in (200, 201), rA.text
        sidA = (rA.json().get("id") or rA.json().get("_id"))

        # A seeds a testimonial
        tp = {
            "customer_name": f"TEST_IsoA_Testi_{uuid.uuid4().hex[:5]}",
            "customer_email": "isoa-t@example.com",
            "rating": 5,
            "original_text": "Great service — isolation A.",
            "service_name": "Isolation A",
        }
        rtA = sA.post(f"{API}/testimonials/seed", json=tp, timeout=60)
        assert rtA.status_code in (200, 201), rtA.text

        # B lists repeat schedules → must NOT see A's
        rLB = sB.get(f"{API}/repeat/schedules")
        assert rLB.status_code == 200
        itemsB = rLB.json()
        if isinstance(itemsB, dict):
            itemsB = itemsB.get("schedules") or itemsB.get("items") or []
        b_ids = {(it.get("id") or it.get("_id")) for it in itemsB}
        assert sidA not in b_ids, f"LEAK: tenant B sees tenant A schedule {sidA}; b_ids={b_ids}"
        for it in itemsB:
            cn = (it.get("customer_name") or "")
            assert "TEST_IsoA" not in cn, f"LEAK by name: {it}"

        # B lists testimonials → must NOT see A's
        rtLB = sB.get(f"{API}/testimonials")
        assert rtLB.status_code == 200
        tlist = rtLB.json()
        if isinstance(tlist, dict):
            tlist = tlist.get("testimonials") or tlist.get("items") or []
        for t in tlist:
            cn = (t.get("customer_name") or "")
            assert "TEST_IsoA" not in cn, f"LEAK: tenant B sees tenant A testimonial: {t}"

        # Cleanup A
        if sidA:
            sA.delete(f"{API}/repeat/schedules/{sidA}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
