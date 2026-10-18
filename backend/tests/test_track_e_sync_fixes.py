"""Financial-sync fix track (audit 2026-10-17) — live-harness proofs.

Same method as Track E's harness (tests/test_track_e_financial_sync.py,
whose scaffolding this module imports): the REAL Momo link/client code
against the REAL books app (now including the fix track's refund
endpoint) served over HTTP on 127.0.0.1:8791 with Track A's FakeDB.
Synthetic data only; the module skips without the harness server.

Proofs:
  * F1 — a repeat customer's THREE sequential paid invoices all sync;
    books creates the customer exactly once.
  * F2 — refunds reflect into books (partial adjusts, full reopens),
    and duplicate refund deliveries post exactly once at both the
    link layer (cumulative-delta guard) and the books layer
    (idempotency replay of the link's own key).
  * F3 — mark-paid with the link disabled stays local and never
    breaks the CRM action (the enabled-path proof is Track E's
    updated test_mark_paid_does_not_sync_but_manual_sync_does).

Run serially with Track E's module (both drive one shared books
server):  python -m pytest tests/test_track_e_financial_sync.py \
    tests/test_track_e_sync_fixes.py -n 0
"""
from __future__ import annotations

import os

import pytest

STATE_FILE = "/tmp/track-e/state.json"

pytestmark = pytest.mark.skipif(
    not os.path.exists(STATE_FILE),
    reason="Track E books harness server not running (no state file)")

try:  # tests/ is a package in this repo — relative import is the norm
    from .test_track_e_financial_sync import (  # noqa: E402
        FakeDB, MT_HVAC, USER_HVAC, BASE, bind_db, paid_invoice,
        provider, run, seed_link, settle_bt, uniq, wait_bt)
except ImportError:  # pragma: no cover - bare-module import mode
    from test_track_e_financial_sync import (  # noqa: E402
        FakeDB, MT_HVAC, USER_HVAC, BASE, bind_db, paid_invoice,
        provider, run, seed_link, settle_bt, uniq, wait_bt)
import routers.bookkeeping_link as bk_mod  # noqa: E402
import routers.call_to_payment as c2p_mod  # noqa: E402
import routers.crm as crm_mod  # noqa: E402
from services.accounting import SyncRefund, idempotency_key  # noqa: E402


def test_repeat_customer_three_paid_invoices_sync_all(monkeypatch):
    """F1 proof: without the fix, invoice #2 and #3 failed with the
    natural-dedup 409 (client ignored existing_id). Now all three sync
    against ONE books customer."""
    fake = FakeDB()
    bind_db(monkeypatch, fake, bk_mod)
    seed_link(fake, MT_HVAC)
    before = settle_bt(MT_HVAC)
    name, email = uniq("Loyal"), f"{uniq('loyal')}@example.com"
    totals = [10000, 8000, 12500]
    outs = []
    for i, total in enumerate(totals):
        inv = paid_invoice(uniq(f"inv-loyal{i}"), total, name, email)
        fake.invoices.docs.append(inv)
        out = run(bk_mod.sync_invoice_paid(fake, MT_HVAC, inv))
        assert out["synced"] is True, out
        outs.append(out)
    assert len({o["customer_provider_id"] for o in outs}) == 1
    after = wait_bt(MT_HVAC, lambda d: len(d["invoices"]) ==
                    len(before["invoices"]) + 3)
    assert len(after["customers"]) == len(before["customers"]) + 1
    assert after["journal_entries"] == before["journal_entries"] + 6
    cust_id = outs[0]["customer_provider_id"]
    mine = [i for i in after["invoices"] if i["customer_id"] == cust_id]
    assert len(mine) == 3
    assert all(i["status"] == "paid" for i in mine)
    assert sorted(i["total_cents"] for i in mine) == sorted(totals)


def test_refund_sync_full_partial_and_duplicate(monkeypatch):
    """F2 proof: partial refund adjusts the books balance, a duplicate
    delivery of the same refund posts nothing new, and a full refund
    reopens the balance — with books' journal balanced throughout."""
    fake = FakeDB()
    bind_db(monkeypatch, fake, bk_mod, c2p_mod)
    seed_link(fake, MT_HVAC)
    before = settle_bt(MT_HVAC)

    # --- Invoice A ($200): partial refund of $50 via the webhook path.
    inv_a = paid_invoice(uniq("inv-ra"), 20000, uniq("RefA"),
                         "refa@example.com")
    fake.invoices.docs.append(inv_a)
    out_a = run(bk_mod.sync_invoice_paid(fake, MT_HVAC, inv_a))
    assert out_a["synced"] is True, out_a
    stored_a = next(d for d in fake.invoices.docs if d["id"] == inv_a["id"])
    stored_a["last_payment_intent_id"] = "pi_ra"
    run(c2p_mod._apply_refund(fake, {"payment_intent": "pi_ra",
                                     "amount_refunded": 5000}))
    snap = wait_bt(MT_HVAC, lambda d: any(
        i["id"] == out_a["invoice_provider_id"] and i["paid_cents"] == 15000
        for i in d["invoices"]))
    binv_a = [i for i in snap["invoices"]
              if i["id"] == out_a["invoice_provider_id"]][0]
    assert binv_a["status"] == "partial"
    stored_a = next(d for d in fake.invoices.docs if d["id"] == inv_a["id"])
    assert stored_a["bookkeeping_sync"]["refunded_cents_synced"] == 5000
    mid = settle_bt(MT_HVAC)

    # --- Duplicate delivery of the SAME refund (same cumulative total
    # out of Stripe): the link layer pushes nothing new.
    dup = run(bk_mod.sync_invoice_refund(fake, MT_HVAC, stored_a,
                                         refunded_total_cents=5000))
    assert dup["synced"] is True and dup["reason"] == "already_synced", dup
    after_dup = settle_bt(MT_HVAC)
    assert after_dup["journal_entries"] == mid["journal_entries"]

    # --- Invoice B ($180): full refund via the webhook path.
    inv_b = paid_invoice(uniq("inv-rb"), 18000, uniq("RefB"),
                         "refb@example.com")
    fake.invoices.docs.append(inv_b)
    out_b = run(bk_mod.sync_invoice_paid(fake, MT_HVAC, inv_b))
    assert out_b["synced"] is True, out_b
    stored_b = next(d for d in fake.invoices.docs if d["id"] == inv_b["id"])
    stored_b["last_payment_intent_id"] = "pi_rb"
    run(c2p_mod._apply_refund(fake, {"payment_intent": "pi_rb",
                                     "amount_refunded": 18000}))
    snap = wait_bt(MT_HVAC, lambda d: any(
        i["id"] == out_b["invoice_provider_id"] and i["paid_cents"] == 0
        for i in d["invoices"]))
    binv_b = [i for i in snap["invoices"]
              if i["id"] == out_b["invoice_provider_id"]][0]
    assert binv_b["status"] == "issued"

    # --- Provider-level redelivery with the link's own key: books
    # replays the recorded refund; no second entry is posted.
    stored_b = next(d for d in fake.invoices.docs if d["id"] == inv_b["id"])
    key_b = idempotency_key(MT_HVAC, inv_b["id"], "refund", "18000")
    entry = run(provider(MT_HVAC).push_refund(
        SyncRefund(invoice_provider_id=out_b["invoice_provider_id"],
                   amount_cents=18000),
        idempotency_key=key_b))
    assert entry == stored_b["bookkeeping_sync"]["refund_provider_ids"][-1]

    final = settle_bt(MT_HVAC)
    # A: issue+pay+refund (3), B: issue+pay+refund (3); duplicates: 0.
    assert final["journal_entries"] == before["journal_entries"] + 6
    delta = {c: final["balances"].get(c, 0) - before["balances"].get(c, 0)
             for c in set(final["balances"]) | set(before["balances"])}
    assert sum(delta.values()) == 0, delta
    assert delta.get("1200", 0) == 23000, delta   # 5,000 + 18,000 owed again
    assert delta.get("1050", 0) == 15000, delta   # A's un-refunded cash
    assert delta.get("4020", 0) == -38000, delta  # both sales' revenue


def test_mark_paid_with_link_disabled_stays_local(monkeypatch):
    """F3 guard: with the link disabled, mark-paid still succeeds and
    nothing reaches books (the sync helper's disabled gate holds)."""
    fake = FakeDB()
    bind_db(monkeypatch, fake, bk_mod, crm_mod)
    s = dict(bk_mod.DEFAULT_SETTINGS)
    s["health"] = bk_mod._default_health()
    s["sync_failures"] = []
    s.update({"enabled": False, "auto_sync_on_paid": True,
              "api_base_url": BASE})
    fake.tenants.docs.append({"id": MT_HVAC, "name": "Off Shop",
                              "bookkeeping_link_settings": s})
    before = settle_bt(MT_HVAC)
    inv = paid_invoice(uniq("inv-off"), 4000, uniq("Off"),
                       "off@example.com")
    inv["status"] = "sent"
    inv["amount_paid_cents"] = 0
    fake.invoices.docs.append(inv)
    out = run(crm_mod.mark_invoice_paid(inv["id"], user=USER_HVAC))
    assert out["status"] == "paid"
    after = settle_bt(MT_HVAC)
    assert len(after["invoices"]) == len(before["invoices"])
