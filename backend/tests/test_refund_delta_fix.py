"""CB-1 regression tests (2026-10-17): _apply_refund split-state
double-subtraction.

Track D (PR #101) made _apply_refund delta-based: Stripe's
charge.amount_refunded is CUMULATIVE per charge, and only the delta since
the last recorded refund state may be subtracted. But the "last recorded
state" lives in two stores — refunded_cents on the payment record, or on
the invoice itself for payments that predate per-payment records (the
legacy path). _apply_refund read exactly ONE store: the payment record
when one existed, else the invoice. A charge whose early refunds were
recorded on the invoice (legacy era) and whose payment record appeared
later (success-event redelivery upsert, backfill) had its already-applied
refunds subtracted a SECOND time from the invoice's paid balance:

    $200 paid, refund $50 (legacy: invoice store = 5000, paid = 15000)
    payment record appears (no refund state on it)
    refund $50 more (charge cumulative 10000)
      pre-fix:  delta = 10000 - 0     -> paid = 5000   (WRONG, $50 twice)
      post-fix: delta = 10000 - 5000  -> paid = 10000  (correct)

The fix reconciles the two stores inside _apply_refund: the invoice-level
figure is consulted only for the charge named by last_payment_intent_id
(the only charge legacy refunds can ever belong to), the applied total is
max() of the stores that describe this charge, and the reconciled total
is written back to every store the charge owns so they cannot split
again.

Tests 1-3 fail on PR #101's head (pre-fix) and pass after. Test 4 is a
precision guard for the fix itself (passes before and after): another
charge's invoice-level baseline must never leak into this charge's delta.
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "audit-test-secret")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "audit_test")
os.environ.pop("REDIS_URL", None)
os.environ.pop("BOOKKEEPING_LINK_ENCRYPTION_KEY", None)

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("TWO_FACTOR_ENCRYPTION_KEY", Fernet.generate_key().decode())

import routers.call_to_payment as c2p_mod  # noqa: E402


# ---------- fake mongo (same semantics as test_audit_security_review) ----------

def _match(doc, query):
    for k, cond in (query or {}).items():
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

    async def find_one(self, query, proj=None):
        for d in self.docs:
            if _match(d, query):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("id") or doc.get("_id"))

    async def update_one(self, query, update):
        matched = 0
        for d in self.docs:
            if _match(d, query):
                matched += 1
                for k, v in update.get("$set", {}).items():
                    d[k] = v
                for k in update.get("$unset", {}):
                    d.pop(k, None)
                break
        return SimpleNamespace(matched_count=matched, modified_count=matched)


class FakeDB:
    def __init__(self):
        self.tenants = FakeCollection()
        self.invoices = FakeCollection()
        self.invoice_payments = FakeCollection()
        self.webhook_events = FakeCollection()


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def fake_db():
    return FakeDB()


# ---------- scenario helpers ----------

def _seed_legacy_paid_invoice(db):
    """$200 invoice paid in the pre-records era: NO invoice_payments row;
    refunds resolve via last_payment_intent_id and record on the invoice."""
    run(db.tenants.insert_one({"id": "t1", "stripe_connect_id": "acct_A"}))
    run(db.invoices.insert_one({
        "id": "inv1", "tenant_id": "t1", "total_cents": 20000,
        "amount_paid_cents": 20000, "status": "paid",
        "last_payment_intent_id": "pi_1",
    }))


def _payment_record_appears(db):
    """The per-payment record shows up later (success-event redelivery
    upsert / backfill), carrying NO refund state."""
    run(db.invoice_payments.insert_one({
        "id": "ip1", "tenant_id": "t1", "invoice_id": "inv1",
        "stripe_payment_intent_id": "pi_1", "connected_account": "acct_A",
        "amount_cents": 20000, "status": "succeeded",
    }))


def _refund(db, cumulative_cents):
    run(c2p_mod._apply_refund(
        db, {"payment_intent": "pi_1", "amount_refunded": cumulative_cents}, "acct_A"))


def _invoice(db):
    return run(db.invoices.find_one({"id": "inv1"}))


# ---------- regression tests (fail pre-fix, pass post-fix) ----------

def test_sequential_partial_refunds_across_split_state(fake_db):
    _seed_legacy_paid_invoice(fake_db)
    _refund(fake_db, 5000)  # $50 refunded; legacy store records it
    inv = _invoice(fake_db)
    assert inv["amount_paid_cents"] == 15000
    assert inv["refunded_cents"] == 5000

    _payment_record_appears(fake_db)

    _refund(fake_db, 10000)  # $50 more (charge cumulative $100)
    inv = _invoice(fake_db)
    assert inv["amount_paid_cents"] == 10000  # pre-fix: 5000 (double-subtract)

    _refund(fake_db, 20000)  # final $100 (charge cumulative $200)
    inv = _invoice(fake_db)
    assert inv["amount_paid_cents"] == 0


def test_replayed_refund_event_moves_totals_once_across_split_state(fake_db):
    _seed_legacy_paid_invoice(fake_db)
    _refund(fake_db, 5000)
    _payment_record_appears(fake_db)

    _refund(fake_db, 10000)
    _refund(fake_db, 10000)  # replayed delivery, new event id, same charge state
    inv = _invoice(fake_db)
    assert inv["amount_paid_cents"] == 10000  # pre-fix: 5000
    assert inv["last_refund_amount"] == 0  # second delivery applied a zero delta


def test_full_refund_zeroes_paid_exactly_and_reconciles_stores(fake_db):
    _seed_legacy_paid_invoice(fake_db)
    _refund(fake_db, 5000)
    _payment_record_appears(fake_db)
    _refund(fake_db, 10000)
    assert _invoice(fake_db)["amount_paid_cents"] == 10000  # pre-fix: 5000

    _refund(fake_db, 20000)  # full refund of the $200 charge
    inv = _invoice(fake_db)
    assert inv["amount_paid_cents"] == 0
    assert inv["status"] != "paid"
    # Both stores hold the reconciled cumulative, so neither can go stale.
    assert inv["refunded_cents"] == 20000
    rec = run(fake_db.invoice_payments.find_one({"stripe_payment_intent_id": "pi_1"}))
    assert rec["refunded_cents"] == 20000


# ---------- precision guard for the fix (passes pre- and post-fix) ----------

def test_other_charges_invoice_level_state_never_leaks(fake_db):
    """The invoice-level refunded_cents belongs to the last-intent charge
    only. pi_1 (has a record, $30 already applied) must compute its delta
    from its own record — never from pi_2's legacy baseline."""
    run(fake_db.tenants.insert_one({"id": "t1", "stripe_connect_id": "acct_A"}))
    run(fake_db.invoices.insert_one({
        "id": "inv1", "tenant_id": "t1", "total_cents": 20000,
        # $30 (pi_1) + $40 (pi_2) already refunded and applied.
        "amount_paid_cents": 13000, "status": "sent",
        "last_payment_intent_id": "pi_2",
        "refunded_cents": 4000,  # pi_2's legacy baseline
    }))
    run(fake_db.invoice_payments.insert_one({
        "id": "ip1", "tenant_id": "t1", "invoice_id": "inv1",
        "stripe_payment_intent_id": "pi_1", "connected_account": "acct_A",
        "amount_cents": 10000, "status": "succeeded", "refunded_cents": 3000,
    }))
    run(fake_db.invoice_payments.insert_one({
        "id": "ip2", "tenant_id": "t1", "invoice_id": "inv1",
        "stripe_payment_intent_id": "pi_2", "connected_account": "acct_A",
        "amount_cents": 10000, "status": "succeeded",
    }))

    _refund(fake_db, 5000)  # pi_1 cumulative $50 -> delta must be $20
    inv = _invoice(fake_db)
    assert inv["amount_paid_cents"] == 11000
    assert inv["last_refund_amount"] == 2000
    # pi_2's invoice-level baseline is untouched by pi_1's refund.
    assert inv["refunded_cents"] == 4000
