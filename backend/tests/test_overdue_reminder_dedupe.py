"""EMP-FIX-036 (original blocker 5): one overdue reminder per invoice per day, even when the
cron job runs twice, is retried, or runs on two workers at once.

Runs against a THROWAWAY MongoDB named by OVERDUE_TEST_MONGO_URL (each test makes and drops its
own database); skipped when unset. send_email is stubbed: no email leaves the box.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("JWT_SECRET", "overdue-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "overdue_test")

import db as dbmod  # noqa: E402
from routers import call_to_payment as c2p  # noqa: E402

MONGO_URL = os.environ.get("OVERDUE_TEST_MONGO_URL", "")
pytestmark = pytest.mark.skipif(not MONGO_URL, reason="set OVERDUE_TEST_MONGO_URL to a throwaway MongoDB")

NOW = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)


def _invoice(iid, **kw):
    doc = {"id": iid, "tenant_id": "t1", "status": "sent", "reminders_paused_at": None,
           "reminders_sent": 0, "max_reminders": 3, "customer_email": f"{iid}@example.com",
           "customer_name": "Ana", "title": "Repair", "total_cents": 10000, "amount_paid_cents": 0,
           "public_token": f"tok-{iid}", "due_at": (NOW - timedelta(days=2)).isoformat()}
    doc.update(kw)
    return doc


@pytest.fixture
def env(monkeypatch):
    from motor.motor_asyncio import AsyncIOMotorClient
    from pymongo import MongoClient

    name = f"overdue_{uuid.uuid4().hex[:10]}"
    sync = MongoClient(MONGO_URL)[name]
    sync.tenants.insert_one({"id": "t1", "name": "Acme", "lang": "en"})
    sent = []
    state = {"fail": False, "delay": 0.0}

    async def fake_send_email(*, to, subject, html, from_name=None, reply_to=None):
        await asyncio.sleep(state["delay"])  # widen the race window between concurrent runs
        sent.append(to)
        return None if state["fail"] else f"em_{len(sent)}"

    monkeypatch.setattr(c2p, "send_email", fake_send_email)
    loop = asyncio.new_event_loop()
    motor = AsyncIOMotorClient(MONGO_URL, io_loop=loop)
    monkeypatch.setattr(dbmod, "_client", motor)
    monkeypatch.setattr(dbmod, "_db", motor[name])

    def run(coro):
        return loop.run_until_complete(coro)

    yield sync, sent, state, run
    motor.close()
    loop.close()
    MongoClient(MONGO_URL).drop_database(name)


def test_running_the_job_twice_sends_once(env):
    sync, sent, _, run = env
    sync.invoices.insert_many([_invoice("i1"), _invoice("i2")])
    first = run(c2p.run_overdue_reminders(now=NOW))
    second = run(c2p.run_overdue_reminders(now=NOW + timedelta(hours=3)))  # same UTC day
    assert first["sent"] == 2 and second["sent"] == 0
    assert sorted(sent) == ["i1@example.com", "i2@example.com"]
    for iid in ("i1", "i2"):
        inv = sync.invoices.find_one({"id": iid})
        assert inv["reminders_sent"] == 1 and inv["status"] == "overdue" and inv["last_reminder_day"] == "2026-10-08"
    assert sync.reminder_claims.count_documents({}) == 2
    assert sync.reminder_claims.find_one({"_id": "overdue:i1:2026-10-08"})["status"] == "sent"


def test_concurrent_runs_send_once(env):
    sync, sent, state, run = env
    sync.invoices.insert_many([_invoice(f"c{i}") for i in range(5)])
    state["delay"] = 0.05

    async def five_at_once():
        return await asyncio.gather(*[c2p.run_overdue_reminders(now=NOW) for _ in range(5)])

    results = run(five_at_once())
    assert sum(r["sent"] for r in results) == 5
    assert sorted(sent) == sorted(f"c{i}@example.com" for i in range(5))
    assert all(d["reminders_sent"] == 1 for d in sync.invoices.find({}))


def test_next_day_sends_again_until_max(env):
    sync, sent, _, run = env
    sync.invoices.insert_one(_invoice("i1", max_reminders=2))
    for day in range(4):
        for _ in range(2):  # twice per day
            run(c2p.run_overdue_reminders(now=NOW + timedelta(days=day)))
    assert sent == ["i1@example.com", "i1@example.com"]
    assert sync.invoices.find_one({"id": "i1"})["reminders_sent"] == 2


def test_utc_day_boundary(env):
    sync, sent, _, run = env
    sync.invoices.insert_one(_invoice("i1"))
    run(c2p.run_overdue_reminders(now=datetime(2026, 10, 8, 23, 59, tzinfo=timezone.utc)))
    run(c2p.run_overdue_reminders(now=datetime(2026, 10, 9, 0, 1, tzinfo=timezone.utc)))
    assert len(sent) == 2  # a new UTC day is a new day


def test_failed_send_is_not_retried_the_same_day(env):
    sync, sent, state, run = env
    sync.invoices.insert_one(_invoice("i1"))
    state["fail"] = True
    run(c2p.run_overdue_reminders(now=NOW))
    run(c2p.run_overdue_reminders(now=NOW + timedelta(hours=1)))
    assert len(sent) == 1
    assert sync.reminder_claims.find_one({"_id": "overdue:i1:2026-10-08"})["status"] == "send_failed"
    state["fail"] = False
    run(c2p.run_overdue_reminders(now=NOW + timedelta(days=1)))
    assert len(sent) == 2


def test_paid_or_paused_between_scan_and_claim_never_sends(env, monkeypatch):
    sync, sent, _, run = env
    sync.invoices.insert_many([_invoice("paid"), _invoice("paused")])
    real_claim = c2p._claim_overdue_reminder

    async def claim_after_change(db, inv, now):
        # the invoice changes after the scan read it, before the claim
        if inv["id"] == "paid":
            await db.invoices.update_one({"id": "paid"}, {"$set": {"status": "paid"}})
        else:
            await db.invoices.update_one({"id": "paused"}, {"$set": {"reminders_paused_at": "2026-10-08T00:00:00Z"}})
        return await real_claim(db, inv, now)

    monkeypatch.setattr(c2p, "_claim_overdue_reminder", claim_after_change)
    assert run(c2p.run_overdue_reminders(now=NOW))["sent"] == 0
    assert sent == []
    assert {d["status"] for d in sync.reminder_claims.find({})} == {"skipped"}
    assert sync.invoices.find_one({"id": "paid"})["reminders_sent"] == 0


def test_not_due_paused_and_max_zero_are_skipped(env):
    sync, sent, _, run = env
    sync.invoices.insert_many([
        _invoice("future", due_at=(NOW + timedelta(days=1)).isoformat()),
        _invoice("paused", reminders_paused_at="2026-10-01T00:00:00Z"),
        _invoice("off", max_reminders=0),
        _invoice("done", reminders_sent=3),
    ])
    run(c2p.run_overdue_reminders(now=NOW))
    assert sent == []
    assert sync.invoices.find_one({"id": "off"})["reminders_sent"] == 0


def test_invoice_without_email_is_counted_once_per_day(env):
    sync, sent, _, run = env
    sync.invoices.insert_one(_invoice("noemail", customer_email=None))
    run(c2p.run_overdue_reminders(now=NOW))
    run(c2p.run_overdue_reminders(now=NOW))
    assert sent == []
    inv = sync.invoices.find_one({"id": "noemail"})
    assert inv["reminders_sent"] == 1 and inv["status"] == "overdue"
    assert sync.reminder_claims.find_one({"_id": "overdue:noemail:2026-10-08"})["status"] == "no_email"
