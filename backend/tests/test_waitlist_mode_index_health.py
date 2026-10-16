"""Waitlist-only app: degraded health + startup unique-index build (gap noted on PR #18), the
credentials-header filter (EMP-WL-052) and the per-test lock reset (EMP-WL-046).

PR #18's marketing.py provides waitlist_index_healthy() and _waitlist_startup(). These tests
stand in fakes for them, so they run with or without #18 (the merged-stack run uses the real ones).
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("JWT_SECRET", "x")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "x")


class _DB:
    def __init__(self, ok=True):
        self.ok = ok

    async def command(self, *a, **k):
        if not self.ok:
            raise RuntimeError("down")
        return {"ok": 1}


def _full_app():
    full = FastAPI()
    full.add_middleware(CORSMiddleware, allow_origins=["https://good.example"], allow_credentials=True,
                        allow_methods=["*"], allow_headers=["*"])
    return full


@pytest.fixture
def wl_app(monkeypatch):
    import db
    from routers import marketing
    import waitlist_mode

    state = {"healthy": True, "startups": 0, "db": _DB()}

    async def fake_startup():
        state["startups"] += 1

    monkeypatch.setattr(db, "get_db", lambda: state["db"])
    async def _close():
        return None
    monkeypatch.setattr(db, "close_db", _close)
    monkeypatch.setattr(marketing, "waitlist_index_healthy", lambda: state["healthy"], raising=False)
    monkeypatch.setattr(marketing, "_waitlist_startup", fake_startup, raising=False)
    return waitlist_mode.build_waitlist_app(_full_app()), state


def test_waitlist_app_runs_the_index_build_at_startup(wl_app):
    app, state = wl_app
    assert state["startups"] == 0
    with TestClient(app):
        assert state["startups"] == 1


def test_waitlist_health_degraded_without_unique_index(wl_app):
    app, state = wl_app
    with TestClient(app) as c:
        assert c.get("/api/health").json() == {"status": "ok"}
        state["healthy"] = False  # index build failed (EMP-WL-040)
        r = c.get("/api/health")
        assert r.status_code == 200 and r.json() == {"status": "degraded"}  # no details
        assert c.head("/api/health").status_code == 200
        state["healthy"], state["db"] = True, _DB(ok=False)
        assert c.get("/api/health").json() == {"status": "degraded"}


def test_startup_failure_refuses_to_start(monkeypatch):
    # With #18, _waitlist_startup raises in production/staging when the index can't be built;
    # the waitlist app must propagate that (refuse to start), like the full app.
    import db
    from routers import marketing
    import waitlist_mode

    async def failing_startup():
        raise RuntimeError("Refusing to start: the waitlist unique email index could not be built")

    monkeypatch.setattr(db, "get_db", lambda: _DB())
    monkeypatch.setattr(marketing, "_waitlist_startup", failing_startup, raising=False)
    app = waitlist_mode.build_waitlist_app(_full_app())
    with pytest.raises(RuntimeError, match="Refusing to start"):
        with TestClient(app):
            pass


def test_works_without_pr18_helpers(monkeypatch):
    import db
    from routers import marketing
    import waitlist_mode

    monkeypatch.setattr(db, "get_db", lambda: _DB())
    for name in ("waitlist_index_healthy", "_waitlist_startup"):
        if hasattr(marketing, name):
            monkeypatch.delattr(marketing, name)
    app = waitlist_mode.build_waitlist_app(_full_app())
    assert app.router.on_startup == []
    with TestClient(app) as c:
        assert c.get("/api/health").json() == {"status": "ok"}


# ---- EMP-WL-052: Access-Control-Allow-Credentials only alongside Allow-Origin ----
def _guarded(origins=("https://good.example",)):
    from waitlist_guard import install_waitlist_guard
    app = FastAPI()

    @app.get("/x")
    async def x():
        return {"ok": True}

    app.add_middleware(CORSMiddleware, allow_origins=list(origins), allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    install_waitlist_guard(app)
    return app


def test_disallowed_origin_gets_no_credentials_header():
    c = TestClient(_guarded())
    good = c.get("/x", headers={"Origin": "https://good.example"})
    assert good.headers["access-control-allow-origin"] == "https://good.example"
    assert good.headers["access-control-allow-credentials"] == "true"
    evil = c.get("/x", headers={"Origin": "https://evil.example"})
    assert evil.status_code == 200
    assert "access-control-allow-origin" not in evil.headers
    assert "access-control-allow-credentials" not in evil.headers
    pre = c.options("/x", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert pre.status_code == 400 and "access-control-allow-credentials" not in pre.headers
    pre_ok = c.options("/x", headers={"Origin": "https://good.example", "Access-Control-Request-Method": "GET"})
    assert pre_ok.status_code == 200 and pre_ok.headers["access-control-allow-credentials"] == "true"


def test_install_is_idempotent_and_orders_middleware():
    from waitlist_guard import install_waitlist_guard
    app = _guarded()
    install_waitlist_guard(app)
    names = [m.cls.__name__ for m in app.user_middleware]
    assert names == ["DropOrphanCredentialsHeader", "CORSMiddleware", "WaitlistPostGuard"]


# ---- EMP-WL-046: the module-level index lock is fresh for every test ----
def _use_lock_in_new_loop():
    from routers import marketing
    lock = getattr(marketing, "_WAITLIST_INDEX_LOCK", None)
    if lock is None:
        pytest.skip("no module-level waitlist lock on this base (PR #14 adds it)")

    async def contend():
        async with lock:
            # A second waiter binds the lock to this loop.
            t = asyncio.ensure_future(lock.acquire())
            await asyncio.sleep(0)
        await t
        lock.release()

    asyncio.run(contend())
    return lock


def test_lock_reset_part1_bind_lock_to_a_loop():
    _use_lock_in_new_loop()


def test_lock_reset_part2_fresh_lock_in_next_test():
    # Without the conftest reset this raises "is bound to a different event loop" (Python 3.10+).
    _use_lock_in_new_loop()


def test_conftest_resets_lock():
    src = (BACKEND / "tests" / "conftest.py").read_text()
    assert "_WAITLIST_INDEX_LOCK = asyncio.Lock()" in src and "autouse=True" in src
