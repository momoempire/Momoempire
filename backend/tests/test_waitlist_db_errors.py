"""PR #14 follow-ups from Watcher QC: EMP-WL-041 (launch blocker), WL-040, WL-042, WL-043, WL-044.

Fake-DB tests always run. Real-MongoDB tests run when WAITLIST_TEST_MONGO_URL points at a THROWAWAY
MongoDB (they create and drop their own database). No email, Turnstile or other network calls.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from bson import ObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError, OperationFailure, ServerSelectionTimeoutError

os.environ.setdefault("JWT_SECRET", "wl041-test")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "wl041")

from routers import marketing as mk  # noqa: E402
from tests.test_fix_waitlist_hardening import FakeDB  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
WL = "/api/public/waitlist"
MONGO_URL = os.environ.get("WAITLIST_TEST_MONGO_URL", "")
EMAIL = "victim.person@example.com"


@pytest.fixture
def env(monkeypatch):
    db = FakeDB()
    sent = []

    async def fake_send(email):
        sent.append(email)

    monkeypatch.setattr(mk, "get_db", lambda: db)
    monkeypatch.setattr(mk, "_send_waitlist_confirmation", fake_send)
    monkeypatch.setattr(mk, "_WAITLIST_INDEX_READY", False)
    monkeypatch.setattr(mk, "_WAITLIST_INDEX_OK", None)
    monkeypatch.setattr(mk, "_INDEX_RETRY_DELAYS", (0, 0))
    monkeypatch.delenv("TURNSTILE_ENABLED", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    for lim in (mk._DEMO_LIMIT, mk._WAITLIST_IP_MINUTE, mk._WAITLIST_IP_HOUR):
        lim.reset()
    app = FastAPI()
    app.include_router(mk.router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False), db, sent


def _raise_on_upsert(db, exc):
    real = db.waitlist.update_one

    async def update_one(q, upd, upsert=False):
        if upsert:
            raise exc
        return await real(q, upd, upsert=upsert)
    db.waitlist.update_one = update_one


def _no_email_in_logs(caplog):
    text = "\n".join(r.getMessage() + (str(r.exc_info) if r.exc_info else "") for r in caplog.records)
    assert EMAIL not in text.lower()


# ---------------- EMP-WL-041: only DuplicateKeyError means "already on the list" ----------------
def test_duplicate_key_race_is_a_normal_signup_reply(env):
    c, db, sent = env
    _raise_on_upsert(db, DuplicateKeyError("E11000 duplicate key error collection: x index: email_1 dup key: { email: \"" + EMAIL + "\" }"))
    r = c.post(WL, json={"email": EMAIL})
    assert r.status_code == 200 and r.json() == {"ok": True, "status": "received"}
    assert sent == []  # the other request owns the row and any email


@pytest.mark.parametrize("exc", [
    ServerSelectionTimeoutError("127.0.0.1:27017: [Errno 111] Connection refused"),
    OperationFailure("not primary", code=10107),
    RuntimeError("Event loop is closed"),  # Watcher's harness case
    ValueError("boom"),
])
def test_other_db_errors_are_503_not_success(env, caplog, exc):
    c, db, sent = env
    _raise_on_upsert(db, exc)
    with caplog.at_level(logging.INFO, logger="marketing"):
        r = c.post(WL, json={"email": EMAIL, "name": "Pat"})
    assert r.status_code == 503
    assert r.json() == {"detail": mk.WAITLIST_UNAVAILABLE}
    assert EMAIL not in r.text and type(exc).__name__ not in r.text  # no internals in the reply
    assert db.waitlist.docs == [] and sent == []
    assert any(rec.levelno >= logging.ERROR and type(exc).__name__ in rec.getMessage() for rec in caplog.records)
    _no_email_in_logs(caplog)


def test_db_down_before_upsert_is_503(env, caplog):
    c, db, _ = env

    def broken_find(*a, **k):
        raise ServerSelectionTimeoutError("no servers")
    db.waitlist.find = broken_find  # the dedupe-before-index step hits the DB first
    with caplog.at_level(logging.INFO, logger="marketing"):
        r = c.post(WL, json={"email": EMAIL})
    assert r.status_code == 503 and r.json() == {"detail": mk.WAITLIST_UNAVAILABLE}
    _no_email_in_logs(caplog)


def test_ten_signups_with_broken_db_store_nothing_and_never_say_success(env):
    c, db, _ = env
    _raise_on_upsert(db, RuntimeError("Event loop is closed"))
    mk._WAITLIST_IP_MINUTE.limit = 1000
    try:
        codes = [c.post(WL, json={"email": f"u{i}@example.com"}).status_code for i in range(10)]
    finally:
        mk._WAITLIST_IP_MINUTE.limit = 5
    assert codes == [503] * 10 and db.waitlist.docs == []


def test_frontends_show_the_server_message():
    # The landing form and the estimator show errMessage(err), i.e. the API's "detail" string.
    for rel in ("frontend/src/pages/Landing.jsx", "frontend/src/pages/Estimate.jsx"):
        src = (BACKEND.parent / rel).read_text()
        assert "errMessage(err)" in src, rel
    api = (BACKEND.parent / "frontend/src/lib/api.js").read_text()
    assert "detail" in api


# ---------------- EMP-WL-040: index build failure is loud ----------------
def _fail_create_index(db, times, exc=None):
    calls = {"n": 0}
    real = db.waitlist.create_index

    async def create_index(*a, **k):
        calls["n"] += 1
        if calls["n"] <= times:
            raise exc or OperationFailure("E11000 duplicate key error", code=11000)
        return await real(*a, **k)
    db.waitlist.create_index = create_index
    return calls


def test_index_build_retries_then_succeeds(env):
    _, db, _ = env
    calls = _fail_create_index(db, 1)
    asyncio.run(mk._ensure_waitlist_index(db))
    assert calls["n"] == 2 and mk._WAITLIST_INDEX_OK is True and mk.waitlist_index_healthy()
    (args, kwargs), = db.waitlist.indexes
    assert args == ("email",) and kwargs == {"unique": True, "partialFilterExpression": {"email": {"$gt": ""}}}


def test_index_build_failure_is_loud_and_unhealthy(env, caplog):
    c, db, _ = env
    calls = _fail_create_index(db, 99)
    with caplog.at_level(logging.WARNING, logger="marketing"):
        asyncio.run(mk._ensure_waitlist_index(db))
    assert calls["n"] == 3  # 1 try + 2 retries
    assert mk._WAITLIST_INDEX_OK is False and not mk.waitlist_index_healthy()
    crit = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert crit and "NO unique email index" in crit[0].getMessage()
    # Signups still work (and say so honestly); the problem shows on /api/health.
    assert c.post(WL, json={"email": "a@example.com"}).status_code == 200


@pytest.mark.parametrize("app_env, refuses", [("production", True), ("staging", True), ("", False), ("development", False)])
def test_startup_refuses_in_production_when_index_fails(env, monkeypatch, app_env, refuses):
    _, db, _ = env
    _fail_create_index(db, 99)
    monkeypatch.setenv("APP_ENV", app_env)
    if refuses:
        with pytest.raises(RuntimeError, match="unique email index"):
            asyncio.run(mk._waitlist_startup())
    else:
        asyncio.run(mk._waitlist_startup())
    assert mk._WAITLIST_INDEX_OK is False


def test_startup_with_db_down_does_not_refuse(env, monkeypatch):
    _, db, _ = env
    monkeypatch.setenv("APP_ENV", "production")

    def broken_find(*a, **k):
        raise ServerSelectionTimeoutError("no servers")
    db.waitlist.find = broken_find
    asyncio.run(mk._waitlist_startup())  # transient: retried before the first signup
    assert mk._WAITLIST_INDEX_OK is None and mk.waitlist_index_healthy()


def test_existing_non_unique_index_is_never_dropped_and_fails_loudly(env):
    _, db, _ = env

    async def info():
        return {"_id_": {}, "email_1": {"key": [("email", 1)]}}
    db.waitlist.index_information = info
    asyncio.run(mk._ensure_waitlist_index(db))
    assert mk._WAITLIST_INDEX_OK is False and db.waitlist.indexes == []


def test_existing_unique_index_from_pr11_is_kept(env):
    _, db, _ = env

    async def info():
        return {"_id_": {}, "email_1": {"key": [("email", 1)], "unique": True}}
    db.waitlist.index_information = info
    asyncio.run(mk._ensure_waitlist_index(db))
    assert mk._WAITLIST_INDEX_OK is True and db.waitlist.indexes == []


def test_health_reports_degraded_without_details(monkeypatch):
    import server

    class PingDB:
        async def command(self, *_):
            return {"ok": 1}
    monkeypatch.setattr(server, "get_db", lambda: PingDB())
    monkeypatch.setattr(mk, "_WAITLIST_INDEX_OK", False)
    assert asyncio.run(server.health()) == {"status": "degraded"}
    monkeypatch.setattr(mk, "_WAITLIST_INDEX_OK", True)
    assert asyncio.run(server.health())["status"] == "ok"
    monkeypatch.setattr(mk, "_WAITLIST_INDEX_OK", None)  # not built yet (e.g. before first signup)
    assert asyncio.run(server.health())["status"] == "ok"


# ---------------- EMP-WL-042: created_at as BSON date or string ----------------
def test_doc_time_handles_datetimes_strings_and_objectids():
    early_dt = datetime(2026, 10, 1, 9, 0)  # naive, as pymongo returns BSON dates (UTC)
    later_str = "2026-10-02T09:00:00+00:00"
    assert mk._doc_time({"created_at": early_dt}) < mk._doc_time({"created_at": later_str})
    assert mk._doc_time({"created_at": "2026-10-01T08:00:00Z"}) < mk._doc_time({"created_at": early_dt})
    assert mk._doc_time({"created_at": "2026-10-01T03:30:00-05:00"}) < mk._doc_time({"created_at": early_dt})
    oid = ObjectId.from_datetime(datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert mk._doc_time({"_id": oid, "created_at": "not a date"}) == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert mk._doc_time({"_id": "string-id"}) == mk._NEVER


def test_dedupe_keeps_the_true_earliest_with_mixed_created_at(env):
    _, db, _ = env
    db.waitlist.docs.extend([
        {"_id": "b", "email": "Dee@Example.com", "name": "later", "created_at": "2026-10-03T00:00:00+00:00"},
        {"_id": "a", "email": "dee@example.com", "name": "earliest", "created_at": datetime(2026, 10, 1)},
        {"_id": "c", "email": "DEE@example.com ", "name": "middle", "created_at": datetime(2026, 10, 2, tzinfo=timezone.utc)},
    ])
    asyncio.run(mk.dedupe_waitlist(db, apply=True))
    assert [d["name"] for d in db.waitlist.docs] == ["earliest"]
    assert sorted(d["name"] for d in db.waitlist_duplicates.docs) == ["later", "middle"]


# ---------------- EMP-WL-043: --workers on the command line ----------------
@pytest.mark.parametrize("argv, env, expected", [
    (["uvicorn", "server:app", "--workers", "2"], {}, 2),
    (["uvicorn", "server:app", "--workers=3"], {}, 3),
    (["uvicorn", "server:app", "--workers", "1"], {"WEB_CONCURRENCY": "4"}, 1),  # CLI wins, like uvicorn
    (["uvicorn", "server:app"], {"UVICORN_WORKERS": "2"}, 2),
    (["uvicorn", "server:app"], {"WEB_CONCURRENCY": "2"}, 2),
    (["uvicorn", "server:app"], {}, 1),
    (["uvicorn", "server:app", "--workers", "x"], {}, 1),
])
def test_configured_workers(argv, env, expected):
    assert mk.configured_workers(argv, env)[0] == expected


def test_workers_flag_logs_error(caplog):
    with caplog.at_level(logging.ERROR, logger="marketing"):
        mk.check_single_worker(["uvicorn", "server:app", "--workers", "2"], {})
    assert any("--workers 2" in r.getMessage() for r in caplog.records)
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger="marketing"):
        mk.check_single_worker(["uvicorn", "server:app"], {})
    assert not caplog.records


# ---------------- EMP-WL-044: legacy live tests never default to a public server ----------------
def test_legacy_suites_default_to_a_dead_local_url():
    for f in sorted((BACKEND / "tests").glob("*.py")):
        if f.name == Path(__file__).name:
            continue
        src = f.read_text()
        assert "emergentagent.com" not in src, f.name
    for name in ("backend_test.py", "test_phase2.py", "test_phase4.py", "test_phase6.py",
                 "test_phase10_new_features.py", "test_iteration10_cloudflare_prep.py"):
        assert '"http://127.0.0.1:9"' in (BACKEND / "tests" / name).read_text(), name


# ---------------- real MongoDB ----------------
async def _real(dbname, fn):
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    try:
        return await fn(client[dbname])
    finally:
        await client.drop_database(dbname)
        client.close()


@pytest.mark.skipif(not MONGO_URL, reason="set WAITLIST_TEST_MONGO_URL to a throwaway MongoDB")
def test_real_mongo_rows_without_email_no_longer_block_the_index(monkeypatch):
    monkeypatch.setattr(mk, "_WAITLIST_INDEX_READY", False)
    monkeypatch.setattr(mk, "_WAITLIST_INDEX_OK", None)
    monkeypatch.setattr(mk, "_INDEX_RETRY_DELAYS", (0, 0))

    async def run(db):
        await db.waitlist.insert_many([{"name": "no email 1"}, {"name": "no email 2"}, {"email": ""}, {"email": ""},
                                       {"email": "A@Example.com", "created_at": datetime(2026, 10, 1)},
                                       {"email": "a@example.com", "created_at": "2026-09-30T00:00:00+00:00"}])
        await mk._ensure_waitlist_index(db)
        info = await db.waitlist.index_information()
        kept = await db.waitlist.find_one({"email": "a@example.com"})
        dup_err = None
        try:
            await db.waitlist.insert_one({"email": "a@example.com"})
        except DuplicateKeyError as e:
            dup_err = e
        return info, kept, dup_err, await db.waitlist.count_documents({})
    info, kept, dup_err, count = asyncio.run(_real(f"wl040_{uuid.uuid4().hex[:8]}", run))
    assert mk._WAITLIST_INDEX_OK is True
    assert info["email_1"]["unique"] is True
    assert kept["created_at"] == "2026-09-30T00:00:00+00:00"  # string date earlier than the BSON date
    assert dup_err is not None and count == 5


@pytest.mark.skipif(not MONGO_URL, reason="set WAITLIST_TEST_MONGO_URL to a throwaway MongoDB")
def test_real_mongo_existing_non_unique_index_fails_loudly(monkeypatch):
    monkeypatch.setattr(mk, "_WAITLIST_INDEX_READY", False)
    monkeypatch.setattr(mk, "_WAITLIST_INDEX_OK", None)
    monkeypatch.setattr(mk, "_INDEX_RETRY_DELAYS", (0, 0))
    monkeypatch.setenv("APP_ENV", "production")

    async def run(db):
        await db.waitlist.create_index("email")  # non-unique, same name email_1
        monkeypatch.setattr(mk, "get_db", lambda: db)
        try:
            await mk._waitlist_startup()
            return None, await db.waitlist.index_information()
        except RuntimeError as e:
            return e, await db.waitlist.index_information()
    err, info = asyncio.run(_real(f"wl040_{uuid.uuid4().hex[:8]}", run))
    assert err is not None and not mk.waitlist_index_healthy()
    assert "unique" not in info["email_1"]  # not dropped or changed


@pytest.mark.skipif(not MONGO_URL, reason="set WAITLIST_TEST_MONGO_URL to a throwaway MongoDB")
def test_real_mongo_unreachable_db_gives_503(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    import httpx
    monkeypatch.setattr(mk, "_WAITLIST_INDEX_READY", False)
    monkeypatch.setattr(mk, "_WAITLIST_INDEX_OK", None)
    monkeypatch.delenv("TURNSTILE_ENABLED", raising=False)
    for lim in (mk._WAITLIST_IP_MINUTE, mk._WAITLIST_IP_HOUR):
        lim.reset()

    async def run():
        dead = AsyncIOMotorClient("mongodb://127.0.0.1:9", serverSelectionTimeoutMS=300)["x"]
        monkeypatch.setattr(mk, "get_db", lambda: dead)
        app = FastAPI()
        app.include_router(mk.router, prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            return await c.post(WL, json={"email": EMAIL})
    r = asyncio.run(run())
    assert r.status_code == 503 and r.json() == {"detail": mk.WAITLIST_UNAVAILABLE}
