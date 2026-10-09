"""EMP-FIX-008 — public waitlist hardening (EMP-W-CF-024, EMP-WL-007, EMP-W-CF-025).

No network: fake DB, email sender and Turnstile verification mocked.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "fix008-test")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "fix008")

from routers import marketing as mk  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


# ---------------- fake db ----------------
def _match(doc, q):
    for k, v in (q or {}).items():
        if isinstance(v, dict) and "$exists" in v:
            if (k in doc) != bool(v["$exists"]):
                return False
        elif doc.get(k) != v:
            return False
    return True


class _Res:
    def __init__(self, modified=0, upserted_id=None):
        self.modified_count, self.upserted_id = modified, upserted_id


class FakeWaitlist:
    def __init__(self):
        self.docs = []
        self.indexes = []

    async def create_index(self, *a, **k):
        self.indexes.append((a, k))

    async def update_one(self, q, upd, upsert=False):
        for d in self.docs:
            if _match(d, q):
                if "$set" in upd:
                    d.update(upd["$set"])
                    return _Res(modified=1)
                return _Res()
        if upsert:
            nd = {k: v for k, v in q.items() if not isinstance(v, dict)}
            nd.update(copy.deepcopy(upd.get("$setOnInsert") or {}))
            nd.update(upd.get("$set") or {})
            self.docs.append(nd)
            return _Res(upserted_id=nd.get("id", "x"))
        return _Res()


class FakeDB:
    def __init__(self):
        self.waitlist = FakeWaitlist()


@pytest.fixture
def env(monkeypatch):
    db = FakeDB()
    sent = []

    async def fake_send(email):
        sent.append(email)

    monkeypatch.setattr(mk, "get_db", lambda: db)
    monkeypatch.setattr(mk, "_send_waitlist_confirmation", fake_send)
    monkeypatch.setattr(mk, "_WAITLIST_INDEX_READY", False)
    monkeypatch.delenv("TURNSTILE_ENABLED", raising=False)
    for lim in (mk._DEMO_LIMIT, mk._WAITLIST_IP_MINUTE, mk._WAITLIST_IP_HOUR):
        lim.reset()
    app = FastAPI()
    app.include_router(mk.router, prefix="/api")
    return TestClient(app), db, sent, app


WL = "/api/public/waitlist"


def _body(**kw):
    b = {"email": "owner@example.com", "name": "Pat", "business_name": "Pat's HVAC", "industry": "HVAC", "note": "hi"}
    b.update(kw)
    return b


# ---------------- no enumeration + email once ----------------
def test_new_and_duplicate_identical_and_email_once(env):
    c, db, sent, _ = env
    r1 = c.post(WL, json=_body())
    r2 = c.post(WL, json=_body(email="OWNER@example.com", note="again"))
    assert r1.status_code == r2.status_code == 200
    assert r1.content == r2.content
    assert r1.json() == {"ok": True, "status": "received"}
    assert len(db.waitlist.docs) == 1 and db.waitlist.docs[0]["note"] == "hi"  # duplicate didn't overwrite
    assert sent == ["owner@example.com"]
    assert "confirmation_sent_at" in db.waitlist.docs[0]


def test_legacy_row_never_gets_email(env):
    c, db, sent, _ = env
    db.waitlist.docs.append({"id": "old", "email": "legacy@example.com"})  # pre-existing, no confirmation_sent_at
    r = c.post(WL, json=_body(email="legacy@example.com"))
    assert r.status_code == 200 and r.json()["status"] == "received"
    assert sent == []


def test_unique_index_requested(env):
    c, db, _, _ = env
    c.post(WL, json=_body())
    assert any(a == ("email",) and k.get("unique") for a, k in db.waitlist.indexes)


# ---------------- validation / limits ----------------
@pytest.mark.parametrize("field,size", [("note", 2001), ("name", 121), ("business_name", 121), ("industry", 61)])
def test_field_length_limits_422(env, field, size):
    c, db, sent, _ = env
    r = c.post(WL, json=_body(**{field: "x" * size}))
    assert r.status_code == 422
    assert db.waitlist.docs == [] and sent == []


def test_field_length_at_limit_ok(env):
    c, _, _, _ = env
    r = c.post(WL, json=_body(note="x" * 2000, name="n" * 120, business_name="b" * 120, industry="i" * 60))
    assert r.status_code == 200


@pytest.mark.parametrize("email", ["not-an-email", "a@", "@b.com", "a b@c.com", ("x" * 250) + "@example.com"])
def test_invalid_or_too_long_email_422(env, email):
    c, db, _, _ = env
    assert c.post(WL, json=_body(email=email)).status_code == 422
    assert db.waitlist.docs == []


def test_body_cap_413(env):
    c, db, _, _ = env
    big = json.dumps(_body(note="x" * 20_000))
    r = c.post(WL, content=big, headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    assert db.waitlist.docs == []


def test_body_cap_413_without_content_length(env):
    c, db, _, _ = env

    def gen():
        for _ in range(20):
            yield b"x" * 1024

    r = c.post(WL, content=gen(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_bad_json_422(env):
    c, _, _, _ = env
    assert c.post(WL, content=b"{nope", headers={"Content-Type": "application/json"}).status_code == 422
    assert c.post(WL, json=["a"]).status_code == 422


# ---------------- honeypot ----------------
def test_honeypot_same_response_stores_nothing(env):
    c, db, sent, _ = env
    real = c.post(WL, json=_body(email="human@example.com"))
    bot = c.post(WL, json=_body(email="bot@example.com", website="http://spam.example"))
    assert bot.status_code == real.status_code == 200 and bot.content == real.content
    assert [d["email"] for d in db.waitlist.docs] == ["human@example.com"]
    assert sent == ["human@example.com"]


# ---------------- Turnstile hook ----------------
def test_turnstile_off_by_default_is_noop(env, monkeypatch):
    c, _, _, _ = env
    called = []
    monkeypatch.setattr(mk, "verify_turnstile", lambda *a: called.append(a))
    assert c.post(WL, json=_body()).status_code == 200
    assert called == []


def test_turnstile_on_rejects_bad_token_and_accepts_good(env, monkeypatch):
    c, db, _, _ = env
    monkeypatch.setenv("TURNSTILE_ENABLED", "true")

    async def fake_verify(token, ip):
        return token == "good-token"

    monkeypatch.setattr(mk, "verify_turnstile", fake_verify)
    assert c.post(WL, json=_body(turnstile_token="bad")).status_code == 400
    assert c.post(WL, json=_body()).status_code == 400
    assert db.waitlist.docs == []
    assert c.post(WL, json=_body(turnstile_token="good-token")).status_code == 200
    assert len(db.waitlist.docs) == 1


def test_turnstile_enabled_without_secret_fails_closed(monkeypatch):
    import asyncio
    monkeypatch.setenv("TURNSTILE_ENABLED", "true")
    monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
    assert asyncio.run(mk.verify_turnstile("tok", "1.2.3.4")) is False


# ---------------- separate rate limiters ----------------
def test_waitlist_rate_limited_429(env):
    c, _, _, _ = env
    codes = [c.post(WL, json=_body(email=f"u{i}@example.com")).status_code for i in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]


def test_demo_exhaustion_does_not_block_waitlist(env):
    c, _, _, _ = env
    codes = [c.post("/api/public/demo/start", json={"industry": "hvac"}).status_code for _ in range(21)]
    assert codes[-1] == 429
    assert c.post(WL, json=_body()).status_code == 200


def test_waitlist_exhaustion_does_not_block_demo(env):
    c, _, _, _ = env
    for i in range(6):
        c.post(WL, json=_body(email=f"w{i}@example.com"))
    assert c.post(WL, json=_body(email="z@example.com")).status_code == 429
    assert c.post("/api/public/demo/start", json={"industry": "hvac"}).status_code == 200


def test_limiters_are_distinct_objects():
    assert mk._DEMO_LIMIT is not mk._WAITLIST_IP_MINUTE
    assert mk._DEMO_LIMIT.hits is not mk._WAITLIST_IP_MINUTE.hits


# ---------------- real client IP / spoofed X-Forwarded-For ----------------
def _proxied(app, trusted):
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    return TestClient(ProxyHeadersMiddleware(app, trusted_hosts=trusted))


def test_spoofed_xff_from_untrusted_peer_ignored(env):
    _, db, _, app = env
    c = _proxied(app, "127.0.0.1")  # TestClient peer is "testclient": untrusted
    codes = []
    for i in range(7):
        r = c.post(WL, json=_body(email=f"s{i}@example.com"), headers={"X-Forwarded-For": f"203.0.113.{i}"})
        codes.append(r.status_code)
    assert codes[5:] == [429, 429]  # rotating XFF did not create new buckets
    assert {d["ip"] for d in db.waitlist.docs} == {"testclient"}


def test_xff_honored_from_trusted_proxy(env):
    _, db, _, app = env
    c = _proxied(app, "testclient")
    codes = [c.post(WL, json=_body(email=f"t{i}@example.com"), headers={"X-Forwarded-For": f"198.51.100.{i}"}).status_code
             for i in range(7)]
    assert codes == [200] * 7  # distinct real clients, distinct buckets
    assert db.waitlist.docs[0]["ip"] == "198.51.100.0"


# ---------------- deployment config ----------------
def test_dockerfile_no_wildcard_forwarded_ips():
    df = (REPO / "backend" / "Dockerfile").read_text()
    assert "--forwarded-allow-ips='*'" not in df and "forwarded-allow-ips=*" not in df
    assert "FORWARDED_ALLOW_IPS" in df and "127.0.0.1" in df


def test_compose_binds_loopback_only():
    dc = (REPO / "docker-compose.yml").read_text()
    assert '"127.0.0.1:8001:8001"' in dc
    assert '- "8001:8001"' not in dc


def test_env_example_names_only():
    ex = (REPO / "backend" / ".env.example").read_text()
    for name in ("CORS_ORIGINS", "FORWARDED_ALLOW_IPS", "TURNSTILE_ENABLED", "TURNSTILE_SECRET_KEY"):
        assert f"\n{name}=" in ex
    for name in ("FORWARDED_ALLOW_IPS", "TURNSTILE_ENABLED", "TURNSTILE_SECRET_KEY"):
        line = [l for l in ex.splitlines() if l.startswith(f"{name}=")][0]
        assert line == f"{name}=", line
