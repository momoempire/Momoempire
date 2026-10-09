"""Waitlist follow-ups to PR #11 (Watcher QC): EMP-WL-021, 023, 024, 025 and EMP-WL-012.

Fake-DB tests always run. The real-MongoDB tests (unique index, dedupe, 10-way race, script) run
when WAITLIST_TEST_MONGO_URL points at a THROWAWAY MongoDB; they create and drop their own database.
No network beyond that, no email, no Turnstile calls.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "wlfu-test")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "wlfu")

from routers import marketing as mk  # noqa: E402
from tests.test_fix_waitlist_hardening import FakeDB  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
WL = "/api/public/waitlist"
MONGO_URL = os.environ.get("WAITLIST_TEST_MONGO_URL", "")


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
    return TestClient(app), db, sent


def _seed_dupes(db):
    db.waitlist.docs.extend([
        {"_id": 1, "id": "a", "email": "Pat@Example.com ", "name": "first", "created_at": "2026-10-01T10:00:00+00:00"},
        {"_id": 2, "id": "b", "email": "pat@example.com", "name": "second", "created_at": "2026-10-02T10:00:00+00:00",
         "confirmation_sent_at": "2026-10-02T10:00:00+00:00"},
        {"_id": 3, "id": "c", "email": "PAT@EXAMPLE.COM", "name": "third", "created_at": "2026-10-03T10:00:00+00:00"},
        {"_id": 4, "id": "d", "email": "solo@example.com", "name": "solo", "created_at": "2026-10-01T09:00:00+00:00"},
    ])


# ---------------- EMP-WL-012 / 024: normalized unique email + dedupe ----------------
def test_normalize_email():
    assert mk.normalize_email("  Pat@Example.COM ") == "pat@example.com"
    assert mk.normalize_email(None) == "" and mk.normalize_email(5) == ""


def test_dedupe_dry_run_changes_nothing(env):
    _, db, _ = env
    _seed_dupes(db)
    before = [dict(d) for d in db.waitlist.docs]
    stats = asyncio.run(mk.dedupe_waitlist(db, apply=False))
    assert stats == {"scanned": 4, "duplicate_groups": 1, "archived": 2, "normalized": 1, "dry_run": True}
    assert db.waitlist.docs == before and db.waitlist_duplicates.docs == []


def test_dedupe_keeps_earliest_archives_rest_and_is_idempotent(env, caplog):
    _, db, _ = env
    _seed_dupes(db)
    with caplog.at_level(logging.WARNING, logger="marketing"):
        stats = asyncio.run(mk.dedupe_waitlist(db, apply=True))
    assert stats["duplicate_groups"] == 1 and stats["archived"] == 2 and stats["normalized"] == 1
    assert "archived=2" in caplog.text
    kept = {d["_id"]: d for d in db.waitlist.docs}
    assert set(kept) == {1, 4}
    assert kept[1]["email"] == "pat@example.com" and kept[1]["email_original"] == "Pat@Example.com "
    assert kept[1]["name"] == "first"  # nothing merged in from later rows
    arch = {d["_id"]: d for d in db.waitlist_duplicates.docs}
    assert set(arch) == {2, 3}
    assert arch[2]["name"] == "second" and arch[2]["confirmation_sent_at"]  # full copy preserved
    assert all(a["duplicate_of"] == 1 and a["archived_at"] for a in arch.values())
    again = asyncio.run(mk.dedupe_waitlist(db, apply=True))
    assert again["duplicate_groups"] == again["archived"] == again["normalized"] == 0
    assert len(db.waitlist_duplicates.docs) == 2


def test_earliest_falls_back_to_objectid_time(env):
    from bson import ObjectId
    from datetime import datetime, timezone
    _, db, _ = env
    old = ObjectId.from_datetime(datetime(2026, 1, 1, tzinfo=timezone.utc))
    db.waitlist.docs.extend([
        {"_id": ObjectId(), "email": "x@example.com", "created_at": "2026-10-08T00:00:00+00:00"},
        {"_id": old, "email": "X@example.com"},  # legacy row without created_at
    ])
    asyncio.run(mk.dedupe_waitlist(db, apply=True))
    assert [d["_id"] for d in db.waitlist.docs] == [old]


def test_first_signup_dedupes_before_index_and_upserts_normalized(env):
    c, db, sent = env
    _seed_dupes(db)
    r = c.post(WL, json={"email": "  PAT@example.com"})
    assert r.status_code == 200 and r.json() == {"ok": True, "status": "received"}
    assert sorted(d["_id"] for d in db.waitlist.docs if "_id" in d) == [1, 4]
    assert len(db.waitlist.docs) == 2  # no new row: matched the normalized existing one
    assert sent == []  # existing entry, no email
    assert any(a == ("email",) and k.get("unique") for a, k in db.waitlist.indexes)
    c.post(WL, json={"email": "New@Example.com"})
    assert db.waitlist.docs[-1]["email"] == "new@example.com" and sent == ["New@example.com"]  # EmailStr lowercases the domain


def test_startup_hook_registered_and_never_crashes(monkeypatch):
    assert mk._waitlist_startup in mk.router.on_startup

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(mk, "get_db", boom)
    monkeypatch.setattr(mk, "_WAITLIST_INDEX_READY", False)
    asyncio.run(mk._waitlist_startup())  # logged, not raised
    assert mk._WAITLIST_INDEX_READY is False  # retried lazily on the first signup


def test_dedupe_script_is_dry_run_by_default():
    src = (BACKEND / "scripts" / "dedupe_waitlist.py").read_text()
    assert '"--apply"' in src and "dedupe_waitlist(db, apply=apply)" in src
    assert "DRY RUN" in src


# ---------------- real MongoDB (unique index, race, script) ----------------
needs_mongo = pytest.mark.skipif(not MONGO_URL, reason="set WAITLIST_TEST_MONGO_URL to a throwaway MongoDB")


async def _real_db_run(dbname, coro_fn):
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    try:
        return await coro_fn(client[dbname])
    finally:
        await client.drop_database(dbname)
        client.close()


@needs_mongo
def test_real_mongo_dedupe_then_unique_index_then_race(monkeypatch):
    import httpx
    from datetime import datetime, timezone
    sent = []

    async def fake_send(email):
        sent.append(email)

    async def scenario(db):
        await db.waitlist.insert_many([
            {"email": "dup@example.com", "name": "first", "created_at": "2026-10-01T00:00:00+00:00"},
            {"email": "dup@example.com", "name": "second", "created_at": "2026-10-02T00:00:00+00:00"},
            {"email": " DUP@Example.com", "name": "third", "created_at": "2026-10-03T00:00:00+00:00"},
            {"email": "Other@Example.com", "name": "other"},
        ])
        # With duplicates present, #11's plain index build fails (Watcher's edge case).
        with pytest.raises(Exception):
            await db.waitlist.create_index("email", unique=True)
        monkeypatch.setattr(mk, "get_db", lambda: db)
        monkeypatch.setattr(mk, "_send_waitlist_confirmation", fake_send)
        monkeypatch.setattr(mk, "_WAITLIST_INDEX_READY", False)
        monkeypatch.delenv("TURNSTILE_ENABLED", raising=False)
        for lim in (mk._WAITLIST_IP_MINUTE, mk._WAITLIST_IP_HOUR):
            lim.reset()
            monkeypatch.setattr(lim, "limit", 1000)
        app = FastAPI()
        app.include_router(mk.router, prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            codes = await asyncio.gather(*[c.post(WL, json={"email": "Race@Example.com "}) for _ in range(10)])
            dup = await c.post(WL, json={"email": "dup@EXAMPLE.com"})
        info = await db.waitlist.index_information()
        rows = await db.waitlist.find({}, {"_id": 0, "email": 1, "name": 1}).sort("email", 1).to_list(None)
        archived = await db.waitlist_duplicates.count_documents({})
        claims = await db.waitlist.count_documents({"email": "race@example.com", "confirmation_sent_at": {"$exists": True}})
        return [r.status_code for r in codes], dup.status_code, info, rows, archived, claims

    codes, dup_code, info, rows, archived, claims = asyncio.run(
        _real_db_run(f"wlfu_{uuid.uuid4().hex[:8]}", scenario))
    assert codes == [200] * 10 and dup_code == 200
    assert info["email_1"]["unique"] is True
    assert rows == [{"email": "dup@example.com", "name": "first"}, {"email": "other@example.com", "name": "other"},
                    {"email": "race@example.com", "name": ""}]
    assert archived == 2 and claims == 1 and sent == ["Race@example.com"]


@needs_mongo
def test_real_mongo_script_dry_run_then_apply():
    dbname = f"wlfu_script_{uuid.uuid4().hex[:8]}"

    async def seed(db):
        await db.waitlist.insert_many([{"email": "a@example.com"}, {"email": "A@example.com"}, {"email": "b@example.com"}])

    async def count(db):
        return await db.waitlist.count_documents({}), await db.waitlist_duplicates.count_documents({}), \
            await db.waitlist.index_information()

    from motor.motor_asyncio import AsyncIOMotorClient

    async def go():
        client = AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=3000)
        db = client[dbname]
        try:
            await seed(db)
            env = {**os.environ, "MONGO_URL": MONGO_URL, "DB_NAME": dbname}
            env.pop("TURNSTILE_ENABLED", None)
            run = lambda *a: subprocess.run([sys.executable, "scripts/dedupe_waitlist.py", *a], cwd=BACKEND,  # noqa: E731
                                            env=env, capture_output=True, text=True, timeout=120)
            dry = run()
            after_dry = await count(db)
            applied = run("--apply")
            after_apply = await count(db)
            again = run("--apply")
            return dry, after_dry, applied, after_apply, again
        finally:
            await client.drop_database(dbname)
            client.close()

    dry, after_dry, applied, after_apply, again = asyncio.run(go())
    assert dry.returncode == 0 and "DRY RUN" in dry.stdout and "to_archive=1" in dry.stdout
    assert after_dry[0] == 3 and after_dry[1] == 0 and "email_1" not in after_dry[2]
    assert applied.returncode == 0 and "APPLIED" in applied.stdout and "unique_index=ok" in applied.stdout
    assert after_apply[0] == 2 and after_apply[1] == 1 and after_apply[2]["email_1"]["unique"]
    assert again.returncode == 0 and "to_archive=0" in again.stdout
    assert "@example.com" not in dry.stdout + applied.stdout  # counts only, no addresses


# ---------------- EMP-WL-023: Turnstile on without secret refuses to start ----------------
def _import(module, **env_over):
    env = {k: v for k, v in os.environ.items() if not k.startswith("TURNSTILE_")}
    env.update({"JWT_SECRET": "x", "MONGO_URL": "mongodb://localhost:1", "DB_NAME": "t"}, **env_over)
    return subprocess.run([sys.executable, "-c", f"import {module}"], cwd=BACKEND, env=env,
                          capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("module", ["routers.marketing", "server"])
def test_turnstile_enabled_without_secret_refuses_to_start(module):
    r = _import(module, TURNSTILE_ENABLED="true")
    assert r.returncode != 0
    assert "TURNSTILE_ENABLED is on but TURNSTILE_SECRET_KEY is empty. Refusing to start" in r.stderr
    r2 = _import(module, TURNSTILE_ENABLED="true", TURNSTILE_SECRET_KEY="   ")
    assert r2.returncode != 0


@pytest.mark.parametrize("env_over", [{}, {"TURNSTILE_ENABLED": "false"}, {"TURNSTILE_ENABLED": "true", "TURNSTILE_SECRET_KEY": "s"}])
def test_turnstile_valid_configs_start(env_over):
    assert _import("routers.marketing", **env_over).returncode == 0


# ---------------- EMP-WL-021: one worker ----------------
def test_dockerfile_pins_one_worker():
    df = (REPO / "backend" / "Dockerfile").read_text()
    assert "ENV WEB_CONCURRENCY=1" in df
    cmd = [l for l in df.splitlines() if l.startswith("CMD")][0]
    assert "--workers 1" in cmd


def test_compose_sets_one_worker():
    yaml = pytest.importorskip("yaml")
    dc = yaml.safe_load((REPO / "docker-compose.yml").read_text())
    assert dc["services"]["backend"]["environment"]["WEB_CONCURRENCY"] == "1"
    assert "deploy" not in dc["services"]["backend"]  # no replicas


@pytest.mark.parametrize("value,warns", [("", False), ("1", False), ("2", True), ("4", True), ("junk", False)])
def test_multi_worker_logs_error(monkeypatch, caplog, value, warns):
    monkeypatch.setenv("WEB_CONCURRENCY", value)
    with caplog.at_level(logging.ERROR, logger="marketing"):
        mk.check_single_worker()
    assert ("rate limits are per process" in caplog.text) is warns


# ---------------- EMP-WL-025: configurable compose subnet ----------------
def _var_default(value):
    m = re.fullmatch(r"\$\{(\w+):-([^}]+)\}", value)
    assert m, value
    return m.group(1), m.group(2)


def test_compose_subnet_configurable_and_forwarded_ips_follow_gateway():
    yaml = pytest.importorskip("yaml")
    dc = yaml.safe_load((REPO / "docker-compose.yml").read_text())
    ipam = dc["networks"]["default"]["ipam"]["config"][0]
    sub_var, sub_default = _var_default(ipam["subnet"])
    gw_var, gw_default = _var_default(ipam["gateway"])
    assert (sub_var, gw_var) == ("AIO_COMPOSE_SUBNET", "AIO_COMPOSE_GATEWAY")
    assert ipaddress.ip_address(gw_default) in ipaddress.ip_network(sub_default)
    fwd = dc["services"]["backend"]["environment"]["FORWARDED_ALLOW_IPS"]
    assert _var_default(fwd) == (gw_var, gw_default)  # same variable + default: can't drift
    assert '"127.0.0.1:8001:8001"' in (REPO / "docker-compose.yml").read_text()


def test_docs_explain_gateway_match_and_one_worker():
    doc = (REPO / "docs" / "deploy" / "client-ip-and-proxies.md").read_text()
    assert "must equal the compose network's gateway" in doc
    assert "AIO_COMPOSE_SUBNET" in doc and "AIO_COMPOSE_GATEWAY" in doc
    assert "## One worker (EMP-WL-021)" in doc


def test_env_example_names_only_for_new_vars():
    ex = (REPO / "backend" / ".env.example").read_text()
    assert [l for l in ex.splitlines() if l.startswith("WEB_CONCURRENCY=")] == ["WEB_CONCURRENCY="]


@needs_mongo
def test_real_mongo_repeat_signup_fills_blanks_only(monkeypatch):
    """EMP-W-CF-031 on real MongoDB: {"$in": ["", None]} matches blank AND missing fields."""
    import httpx
    sent = []

    async def fake_send(email):
        sent.append(email)

    async def scenario(db):
        await db.waitlist.insert_one({"email": "legacy@example.com", "name": "Legacy"})  # no tier/note fields
        monkeypatch.setattr(mk, "get_db", lambda: db)
        monkeypatch.setattr(mk, "_send_waitlist_confirmation", fake_send)
        monkeypatch.setattr(mk, "_WAITLIST_INDEX_READY", False)
        monkeypatch.delenv("TURNSTILE_ENABLED", raising=False)
        for lim in (mk._WAITLIST_IP_MINUTE, mk._WAITLIST_IP_HOUR):
            lim.reset()
        app = FastAPI()
        app.include_router(mk.router, prefix="/api")
        est = {"source_detail": "estimator", "estimated_tier": "growth", "note": "<script>x</script>first"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r1 = await c.post(WL, json={"email": "legacy@example.com", **est})
            r2 = await c.post(WL, json={"email": "legacy@example.com", "source_detail": "estimator",
                                        "estimated_tier": "high_volume", "note": "second", "name": "Other"})
        return r1.status_code, r2.status_code, await db.waitlist.find_one({}, {"_id": 0})

    c1, c2, doc = asyncio.run(_real_db_run(f"wlfu_fill_{uuid.uuid4().hex[:8]}", scenario))
    assert c1 == c2 == 200
    assert doc["name"] == "Legacy"
    assert (doc["estimated_tier"], doc["source_detail"], doc["note"]) == ("growth", "estimator", "xfirst")
    assert sent == []
