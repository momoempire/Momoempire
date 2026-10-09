"""Shared pytest fixtures for backend/tests."""
import asyncio
import sys

import pytest


@pytest.fixture(autouse=True)
def _fresh_waitlist_index_lock():
    """EMP-WL-046: routers/marketing.py keeps a module-level asyncio.Lock for the waitlist index
    build. Tests run many event loops (asyncio.run, TestClient) in one process, and on Python 3.10+
    a lock that was waited on in one loop raises "is bound to a different event loop" in the next.
    A real server has one loop, so this is test isolation only: give every test a fresh lock.
    Does not import the module (only resets it if some test already did)."""
    mk = sys.modules.get("routers.marketing")
    if mk is not None and isinstance(getattr(mk, "_WAITLIST_INDEX_LOCK", None), asyncio.Lock):
        mk._WAITLIST_INDEX_LOCK = asyncio.Lock()
    yield
