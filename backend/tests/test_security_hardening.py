"""Security hardening tests: 2FA (TOTP), login/challenge rate limiting, Sentry noop.

No database, network, or browser needed: small FastAPI apps with only the
auth + 2FA routers, a fake in-memory Mongo, and overridden auth dependencies.
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "hardening-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "hardening_test")
os.environ.pop("REDIS_URL", None)
os.environ.pop("SENTRY_DSN", None)

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("TWO_FACTOR_ENCRYPTION_KEY", Fernet.generate_key().decode())

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.auth as auth_mod  # noqa: E402
import routers.two_factor as t2f_mod  # noqa: E402
from security import get_current_user, hash_password  # noqa: E402
import rate_limit  # noqa: E402
import two_factor as t2f  # noqa: E402
import sentry_init  # noqa: E402


# ---------- fake mongo ----------

class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    async def to_list(self, n):
        return [dict(d) for d in self.docs[:n]]


def _match(doc, query):
    for k, cond in query.items():
        val = doc.get(k)
        if isinstance(cond, dict):
            for op, opval in cond.items():
                if op == "$in" and val not in opval:
                    return False
                elif op == "$ne" and val == opval:
                    return False
                elif op not in ("$in", "$ne"):
                    return False
        elif val != cond:
            return False
    return True


class FakeCollection:
    def __init__(self):
        self.docs = []

    def find(self, query, proj=None):
        return FakeCursor([d for d in self.docs if _match(d, query)])

    async def find_one(self, query, proj=None):
        for d in self.docs:
            if _match(d, query):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("id"))

    async def update_one(self, query, update):
        matched = 0
        for d in self.docs:
            if _match(d, query):
                matched += 1
                for k, v in update.get("$set", {}).items():
                    d[k] = v
                for k in update.get("$unset", {}):
                    d.pop(k, None)
                pull = update.get("$pull", {})
                for k, v in pull.items():
                    if isinstance(d.get(k), list) and v in d[k]:
                        d[k].remove(v)
                break
        return SimpleNamespace(matched_count=matched, modified_count=matched)

    async def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if _match(d, query):
                del self.docs[i]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


class FakeDB:
    def __init__(self):
        self.users = FakeCollection()
        self.login_attempts = FakeCollection()


# ---------- fixtures ----------

current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(auth_mod, "get_db", lambda: fake_db)
    monkeypatch.setattr(t2f_mod, "get_db", lambda: fake_db)
    rate_limit.reset_rate_limiter_singleton()
    app = FastAPI()
    app.include_router(auth_mod.router)
    app.include_router(t2f_mod.router)
    app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app)


def as_user(uid="u1", tenant_id="t1", role="owner"):
    current_user.clear()
    current_user.update({"id": uid, "tenant_id": tenant_id, "role": role, "email": "owner@example.com"})


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def seed_user(db, email="owner@example.com", password="Correct1!x", mfa=False):
    await db.users.insert_one({
        "id": "u1", "email": email, "password_hash": hash_password(password),
        "name": "Owner", "role": "owner", "tenant_id": "t1",
        "mfa_enabled": mfa, "created_at": "2026-01-01T00:00:00+00:00",
    })


def enroll_2fa(client, db):
    """Full enrollment via the API; returns (secret, backup_codes)."""
    as_user()
    r = client.post("/auth/2fa/setup")
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    assert r.json()["otpauth_uri"].startswith("otpauth://totp/")
    import pyotp

    code = pyotp.TOTP(secret).now()
    r = client.post("/auth/2fa/verify", json={"code": code})
    assert r.status_code == 200, r.text
    codes = r.json()["backup_codes"]
    assert len(codes) == 10
    return secret, codes


# ---------- 2FA ----------

def test_2fa_setup_requires_pending_before_verify(client, fake_db):
    as_user()
    run(seed_user(fake_db))
    r = client.post("/auth/2fa/verify", json={"code": "123456"})
    assert r.status_code == 400


def test_2fa_setup_verify_wrong_code_rejected(client, fake_db):
    as_user()
    run(seed_user(fake_db))
    r = client.post("/auth/2fa/setup")
    assert r.status_code == 200
    r = client.post("/auth/2fa/verify", json={"code": "000000"})
    assert r.status_code == 401


def test_2fa_full_enroll_then_login_challenge(client, fake_db):
    as_user()
    run(seed_user(fake_db))
    secret, codes = enroll_2fa(client, fake_db)

    # Pending secret is encrypted at rest, never plaintext.
    user = run(fake_db.users.find_one({"id": "u1"}))
    assert user["mfa_enabled"] is True
    assert "mfa_pending_secret_enc" not in user
    assert user["mfa_secret_enc"] != secret

    # Login with 2FA enabled -> 2fa token, NO auth cookies.
    r = client.post("/auth/login", json={"email": "owner@example.com", "password": "Correct1!x"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("requires_2fa") is True
    assert body.get("two_fa_token")
    assert "access_token" not in (r.cookies or {})

    # Wrong TOTP -> 401.
    r = client.post("/auth/2fa/challenge",
                    json={"two_fa_token": body["two_fa_token"], "code": "000000"})
    assert r.status_code == 401

    # Right TOTP -> auth cookies.
    import pyotp

    r = client.post("/auth/2fa/challenge",
                    json={"two_fa_token": body["two_fa_token"], "code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text
    assert r.cookies.get("access_token")


def test_2fa_backup_code_single_use(client, fake_db):
    as_user()
    run(seed_user(fake_db))
    _, codes = enroll_2fa(client, fake_db)
    r = client.post("/auth/login", json={"email": "owner@example.com", "password": "Correct1!x"})
    token = r.json()["two_fa_token"]

    r = client.post("/auth/2fa/challenge",
                    json={"two_fa_token": token, "backup_code": codes[0]})
    assert r.status_code == 200, r.text

    # Same backup code again -> burned -> 401.
    r = client.post("/auth/login", json={"email": "owner@example.com", "password": "Correct1!x"})
    token2 = r.json()["two_fa_token"]
    r = client.post("/auth/2fa/challenge",
                    json={"two_fa_token": token2, "backup_code": codes[0]})
    assert r.status_code == 401


def test_2fa_disable_requires_password(client, fake_db):
    as_user()
    run(seed_user(fake_db))
    enroll_2fa(client, fake_db)

    r = client.post("/auth/2fa/disable", json={"password": "Wrong1!x"})
    assert r.status_code == 401

    r = client.post("/auth/2fa/disable", json={"password": "Correct1!x"})
    assert r.status_code == 200
    user = run(fake_db.users.find_one({"id": "u1"}))
    assert user["mfa_enabled"] is False
    assert "mfa_secret_enc" not in user

    # Login works normally again (no 2FA step).
    r = client.post("/auth/login", json={"email": "owner@example.com", "password": "Correct1!x"})
    assert r.status_code == 200
    assert r.json().get("requires_2fa") is not True
    assert r.cookies.get("access_token")


def test_2fa_setup_fails_closed_without_key(client, fake_db, monkeypatch):
    as_user()
    run(seed_user(fake_db))
    monkeypatch.delenv("TWO_FACTOR_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("BOOKKEEPING_LINK_ENCRYPTION_KEY", raising=False)
    r = client.post("/auth/2fa/setup")
    assert r.status_code == 503


# ---------- rate limiting ----------

def test_login_rate_limited_with_retry_after(client, fake_db):
    run(seed_user(fake_db))
    for _ in range(5):
        r = client.post("/auth/login", json={"email": "owner@example.com", "password": "Wrong1!x"})
        assert r.status_code == 401
    r = client.post("/auth/login", json={"email": "owner@example.com", "password": "Wrong1!x"})
    assert r.status_code == 429
    assert "retry-after" in {k.lower() for k in r.headers.keys()}
    assert int(r.headers["Retry-After"]) >= 1


def test_login_rate_limit_resets_on_success(client, fake_db):
    run(seed_user(fake_db))
    for _ in range(4):
        client.post("/auth/login", json={"email": "owner@example.com", "password": "Wrong1!x"})
    r = client.post("/auth/login", json={"email": "owner@example.com", "password": "Correct1!x"})
    assert r.status_code == 200
    # Bucket cleared: 5 more failures allowed before the next 429.
    for _ in range(5):
        r = client.post("/auth/login", json={"email": "owner@example.com", "password": "Wrong1!x"})
        assert r.status_code == 401
    r = client.post("/auth/login", json={"email": "owner@example.com", "password": "Wrong1!x"})
    assert r.status_code == 429


def test_challenge_rate_limited(client, fake_db):
    as_user()
    run(seed_user(fake_db))
    enroll_2fa(client, fake_db)
    r = client.post("/auth/login", json={"email": "owner@example.com", "password": "Correct1!x"})
    token = r.json()["two_fa_token"]
    for _ in range(5):
        r = client.post("/auth/2fa/challenge", json={"two_fa_token": token, "code": "000000"})
        assert r.status_code == 401
    r = client.post("/auth/2fa/challenge", json={"two_fa_token": token, "code": "000000"})
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_inmemory_limiter_unit():
    lim = rate_limit.InMemoryRateLimiter()
    for _ in range(3):
        allowed, ra = lim.check("k", 3, 60.0)
        assert allowed and ra == 0.0
    allowed, ra = lim.check("k", 3, 60.0)
    assert not allowed and ra > 0
    lim.reset("k")
    allowed, _ = lim.check("k", 3, 60.0)
    assert allowed


def test_rate_limiter_factory_falls_back_without_redis():
    os.environ.pop("REDIS_URL", None)
    rate_limit.reset_rate_limiter_singleton()
    assert isinstance(rate_limit.get_rate_limiter(), rate_limit.InMemoryRateLimiter)


# ---------- sentry ----------

def test_sentry_noop_without_dsn():
    os.environ.pop("SENTRY_DSN", None)
    assert sentry_init.is_configured() is False
    assert sentry_init.init_sentry() is False


def test_sentry_scrubs_pii():
    event = {
        "request": {
            "data": {"password": "hunter2", "email": "a@b.c"},
            "headers": {"Authorization": "Bearer abc", "X-Ok": "yes"},
            "cookies": {"session_token": "xyz"},
        }
    }
    out = sentry_init._before_send(event, {})
    assert out["request"]["data"]["password"] == "[redacted]"
    assert out["request"]["data"]["email"] == "a@b.c"
    assert out["request"]["headers"]["Authorization"] == "[redacted]"
    assert out["request"]["cookies"]["session_token"] == "[redacted]"
