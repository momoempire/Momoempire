"""EMP-WL-061: a down MongoDB must not make a waitlist signup hang for 30 s before the 503.

The "down DB" test needs no MongoDB (it points at a closed local port). The real-MongoDB test runs
when WAITLIST_TEST_MONGO_URL points at a THROWAWAY MongoDB (creates and drops its own database).
No email, Turnstile or other network calls.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

import httpx
import pytest
from fastapi import FastAPI

os.environ.setdefault("JWT_SECRET", "wl061-test")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "wl061")

import db as dbmod  # noqa: E402
from routers import marketing as mk  # noqa: E402

WL = "/api/public/waitlist"
MONGO_URL = os.environ.get("WAITLIST_TEST_MONGO_URL", "")


@pytest.fixture
def fresh(monkeypatch):
    """Real db.get_db / get_waitlist_db wiring (nothing patched), fresh clients, fresh limiter."""
    monkeypatch.setattr(mk, "get_db", dbmod.get_db)
    monkeypatch.setattr(dbmod, "_client", None)
    monkeypatch.setattr(dbmod, "_db", None)
    monkeypatch.setattr(dbmod, "_waitlist_client", None)
    monkeypatch.setattr(mk, "_WAITLIST_INDEX_READY", False)
    monkeypatch.setattr(mk, "_WAITLIST_INDEX_OK", None)
    monkeypatch.setattr(mk, "_INDEX_RETRY_DELAYS", (0, 0))
    monkeypatch.delenv("TURNSTILE_ENABLED", raising=False)
    sent = []

    async def fake_send(email):
        sent.append(email)

    monkeypatch.setattr(mk, "_send_waitlist_confirmation", fake_send)
    for lim in (mk._WAITLIST_IP_MINUTE, mk._WAITLIST_IP_HOUR):
        lim.reset()
    yield monkeypatch
    for c in (dbmod._client, dbmod._waitlist_client):
        if c is not None:
            c.close()


async def _post(json):
    app = FastAPI()
    app.include_router(mk.router, prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.post(WL, json=json)


@pytest.mark.parametrize("raw,expected", [(None, 5000), ("", 5000), ("1500", 1500), ("abc", 5000),
                                          ("10", 500), ("999999", 30000)])
def test_timeout_setting(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("WAITLIST_DB_TIMEOUT_MS", raising=False)
    else:
        monkeypatch.setenv("WAITLIST_DB_TIMEOUT_MS", raw)
    assert dbmod.waitlist_db_timeout_ms() == expected


def test_waitlist_client_has_short_deadlines(fresh):
    fresh.setenv("MONGO_URL", "mongodb://127.0.0.1:9")
    fresh.setenv("WAITLIST_DB_TIMEOUT_MS", "1200")
    opts = dbmod.get_waitlist_db().client.options
    assert opts.server_selection_timeout == 1.2
    assert opts.pool_options.connect_timeout == 1.2
    assert opts.timeout == 1.2
    # the rest of the app keeps the driver defaults
    assert dbmod.get_db().client.options.server_selection_timeout == 30


def test_down_db_gives_503_fast(fresh):
    fresh.setenv("MONGO_URL", "mongodb://127.0.0.1:9")  # nothing listens here
    fresh.setenv("WAITLIST_DB_TIMEOUT_MS", "800")
    t0 = time.monotonic()
    r = asyncio.run(_post({"email": "down.db@example.com"}))
    elapsed = time.monotonic() - t0
    assert r.status_code == 503 and r.json() == {"detail": mk.WAITLIST_UNAVAILABLE}
    assert elapsed < 5, f"took {elapsed:.1f}s (the driver default would be ~30 s)"


def test_patched_get_db_is_still_used(fresh):
    """Tests and the waitlist-only app inject their own DB through marketing.get_db."""
    sentinel = object()
    fresh.setattr(mk, "get_db", lambda: sentinel)
    assert mk._signup_db() is sentinel
    # Also when db.get_db itself was replaced before marketing was imported (as
    # test_waitlist_only_mode.py does): both names point at the same replacement.
    fake = lambda: sentinel  # noqa: E731
    fresh.setattr(dbmod, "get_db", fake)
    fresh.setattr(mk, "get_db", fake)
    assert mk._signup_db() is sentinel


def test_unpatched_uses_the_short_timeout_client(fresh):
    fresh.setenv("MONGO_URL", "mongodb://127.0.0.1:9")
    assert mk._signup_db().client is dbmod.get_waitlist_db().client


@pytest.mark.skipif(not MONGO_URL, reason="set WAITLIST_TEST_MONGO_URL to a throwaway MongoDB")
def test_real_mongo_signup_through_the_short_timeout_client(fresh):
    name = f"wl061_{uuid.uuid4().hex[:8]}"
    fresh.setenv("MONGO_URL", MONGO_URL)
    fresh.setenv("DB_NAME", name)

    async def run():
        r = await _post({"email": "Real.Person@Example.com"})
        rows = await dbmod.get_waitlist_db().waitlist.find({}, {"_id": 0, "email": 1}).to_list(10)
        await dbmod.get_waitlist_db().client.drop_database(name)
        return r, rows
    r, rows = asyncio.run(run())
    assert r.status_code == 200, r.text
    assert rows == [{"email": "real.person@example.com"}]
