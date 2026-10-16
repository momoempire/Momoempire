"""EMP-W-CF-026 / 027 against a REAL MongoDB (pymongo/Motor decode BSON dates as naive UTC).

Needs a throwaway MongoDB at ADMIN_TEST_MONGO_URL (or WAITLIST_TEST_MONGO_URL); each test creates
and drops its own database. Skipped otherwise. No network beyond that local server.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("JWT_SECRET", "cf026-test-secret")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "cf026")

MONGO = os.environ.get("ADMIN_TEST_MONGO_URL") or os.environ.get("WAITLIST_TEST_MONGO_URL")
pytestmark = pytest.mark.skipif(not MONGO, reason="ADMIN_TEST_MONGO_URL / WAITLIST_TEST_MONGO_URL not set")

STRONG = "Tr0ub4dor&Horse!"


def _with_db(monkeypatch, fn):
    """Run `fn(db)` in a fresh loop with a Motor client on a throwaway database."""
    import security
    import seed_data
    from routers import auth as auth_router
    from routers import plans as plans_router
    from routers import portal as portal_router
    from motor.motor_asyncio import AsyncIOMotorClient

    async def main():
        client = AsyncIOMotorClient(MONGO, serverSelectionTimeoutMS=3000)
        name = f"cf026_{uuid.uuid4().hex[:8]}"
        db = client[name]
        for mod in (security, seed_data, auth_router, plans_router, portal_router):
            monkeypatch.setattr(mod, "get_db", lambda db=db: db)
        try:
            return await fn(db)
        finally:
            await client.drop_database(name)
            client.close()

    return asyncio.run(main())


def test_mongo_really_returns_naive_datetimes(monkeypatch):
    async def go(db):
        await db.t.insert_one({"x": datetime.now(timezone.utc)})
        return (await db.t.find_one())["x"]
    assert _with_db(monkeypatch, go).tzinfo is None


def test_forgot_then_reset_password_works_on_real_mongo(monkeypatch):
    """Before the fix: TypeError (offset-naive vs offset-aware) -> HTTP 500."""
    from models import ForgotIn, ResetIn
    from routers import auth as auth_router
    import security

    async def go(db):
        await db.users.insert_one({"id": "adm", "email": "admin@example.com", "role": "platform_admin",
                                   "tenant_id": None, "password_hash": security.hash_password("x" * 20),
                                   "must_change_password": True, "password_unusable": True})
        await auth_router.forgot_password(ForgotIn(email="admin@example.com"))
        tok = await db.password_reset_tokens.find_one({"user_id": "adm"})
        assert isinstance(tok["expires_at"], datetime) and tok["expires_at"].tzinfo is None  # stored as a BSON date
        res = await auth_router.reset_password(ResetIn(token=tok["token"], new_password=STRONG))
        user = await db.users.find_one({"id": "adm"})
        used = (await db.password_reset_tokens.find_one({"token": tok["token"]}))["used"]
        return res, user, used

    res, user, used = _with_db(monkeypatch, go)
    assert res == {"status": "ok"} and used is True
    assert security.verify_password(STRONG, user["password_hash"])
    assert user["password_unusable"] is False and user["must_change_password"] is False


def test_expired_reset_token_is_400_on_real_mongo(monkeypatch):
    from fastapi import HTTPException
    from models import ResetIn
    from routers import auth as auth_router

    async def go(db):
        await db.users.insert_one({"id": "u1", "email": "o@example.com", "role": "owner", "password_hash": "x"})
        await db.password_reset_tokens.insert_one({"token": "old", "user_id": "u1", "used": False,
                                                   "expires_at": datetime.now(timezone.utc) - timedelta(minutes=5)})
        with pytest.raises(HTTPException) as e:
            await auth_router.reset_password(ResetIn(token="old", new_password=STRONG))
        return e.value

    err = _with_db(monkeypatch, go)
    assert err.status_code == 400 and err.detail == "Token expired"


def test_session_cookie_expiry_on_real_mongo(monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request
    import security

    def req(token):
        return Request({"type": "http", "method": "GET", "path": "/", "headers": [(b"cookie", f"session_token={token}".encode())]})

    async def go(db):
        await db.users.insert_one({"id": "u1", "email": "s@example.com", "role": "owner", "tenant_id": "t1"})
        now = datetime.now(timezone.utc)
        await db.user_sessions.insert_one({"user_id": "u1", "session_token": "live", "expires_at": now + timedelta(days=1)})
        await db.user_sessions.insert_one({"user_id": "u1", "session_token": "dead", "expires_at": now - timedelta(days=1)})
        ok = await security._load_current_user(req("live"))
        with pytest.raises(HTTPException) as e:
            await security._load_current_user(req("dead"))
        return ok, e.value

    ok, err = _with_db(monkeypatch, go)
    assert ok["id"] == "u1" and err.status_code == 401


def test_portal_token_expiry_on_real_mongo(monkeypatch):
    """Customer-portal magic links had the same naive-vs-aware crash (EMP-W-CF-026 sweep)."""
    from fastapi import HTTPException
    from starlette.requests import Request
    from routers import portal as portal_router

    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})

    async def go(db):
        await db.tenants.insert_one({"id": "t1", "slug": "acme", "status": "active", "name": "Acme"})
        await db.customers.insert_one({"id": "c1", "tenant_id": "t1", "phone": "+15550001111", "name": "C"})
        now = datetime.now(timezone.utc)
        for tok, exp in (("live", now + timedelta(hours=2)), ("dead", now - timedelta(hours=1))):
            await db.portal_tokens.insert_one({"id": tok, "token": tok, "tenant_id": "t1", "customer_id": "c1",
                                               "expires_at": exp, "used": False})
        ok = await portal_router.portal_me("acme", request, token="live")
        with pytest.raises(HTTPException) as e:
            await portal_router.portal_me("acme", request, token="dead")
        return ok, e.value

    ok, err = _with_db(monkeypatch, go)
    assert ok["customer"]["id"] == "c1" and err.status_code == 401 and err.detail == "Token expired"


def test_admin_email_clash_does_not_abort_seeding_on_real_mongo(monkeypatch, caplog):
    """Before the fix: DuplicateKeyError on the unique users.email index aborted run_all_seeds, so the
    server's startup skipped plans too (Watcher: 0 plans)."""
    import seed_data
    from routers import plans as plans_router

    monkeypatch.setenv("ADMIN_EMAIL", "taken@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", STRONG)

    async def go(db):
        await db.users.insert_one({"id": "u-taken", "email": "taken@example.com", "role": "owner",
                                   "tenant_id": "t1", "password_hash": "their-hash"})
        # Same sequence as server.py's startup hook.
        await seed_data.run_all_seeds()
        await plans_router.seed_plans()
        users = await db.users.find({}, {"_id": 0}).to_list(10)
        counts = {c: await db[c].count_documents({}) for c in ("industries", "countries", "feature_flags", "plans")}
        return users, counts

    with caplog.at_level("WARNING", logger="seed"):
        users, counts = _with_db(monkeypatch, go)
    assert users == [{"id": "u-taken", "email": "taken@example.com", "role": "owner", "tenant_id": "t1",
                      "password_hash": "their-hash"}]  # untouched, not promoted, no admin created
    assert all(n > 0 for n in counts.values()), counts
    assert any("ADMIN_EMAIL matches an existing" in r.getMessage() for r in caplog.records)


def test_admin_seeded_normally_on_real_mongo(monkeypatch):
    import seed_data

    monkeypatch.setenv("ADMIN_EMAIL", "ops@example.com")
    monkeypatch.setenv("ADMIN_PASSWORD", STRONG)

    async def go(db):
        await seed_data.run_all_seeds()
        await seed_data.run_all_seeds()  # idempotent
        return await db.users.find({"role": "platform_admin"}, {"_id": 0, "email": 1}).to_list(10)

    assert _with_db(monkeypatch, go) == [{"email": "ops@example.com"}]
