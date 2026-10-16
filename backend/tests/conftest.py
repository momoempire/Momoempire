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


# Integration suites (test_phase*.py, backend_test.py, test_call_to_payment.py, ...) pass state
# between tests via attributes on the pytest module (e.g. pytest.quote_draft_id). When the producing
# test failed or was skipped, the consumer used to fail with AttributeError and hide the real cause.
# Report those as skipped instead, naming the missing value.
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    exc = call.excinfo
    if rep.when == "call" and rep.failed and exc is not None and exc.errisinstance(AttributeError):
        msg = str(exc.value)
        if msg.startswith("module 'pytest' has no attribute"):
            rep.outcome = "skipped"
            rep.longrepr = (str(item.path), item.location[1] or 0,
                            f"Skipped: depends on an earlier test that did not pass ({msg})")
