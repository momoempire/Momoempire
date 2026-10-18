# Financial-Sync Design Notes — Chargebacks & Voids (OWNER DECISION)

Produced by the financial-sync fix track (2026-10-17), alongside fixes
F1–F4. These two gaps were confirmed by Track E (PR #102,
`E2E_VERIFICATION.md`) but are **design decisions, not mechanical
defects** — nothing here is built. Each proposal needs an owner
decision before implementation.

## 1. Chargeback / dispute representation in books

**State today (verified by Track E):** a Stripe dispute lands in Momo
as a marker only — `stripe_dispute_id` + `has_dispute` on the invoice.
The invoice stays `paid`, the customer is never notified, and nothing
reaches books: books keeps showing the money as received and the sale
as final even after Stripe claws the funds back. There is no dispute
concept anywhere in books.

**Why it matters:** a lost chargeback is economically a forced refund
plus a fee. Until books represents it, the shop's AR/cash/revenue are
overstated from the moment Stripe withdraws the funds.

**Proposal (recommended shape):**
- Momo forwards dispute lifecycle events on the existing sync surface:
  `charge.dispute.created` (funds withdrawn) posts a **provisional**
  reversal in books — Dr 1200 / Cr card-clearing (1050), source
  `"chargeback"`, payment record `kind="chargeback"` — reusing the
  refund endpoint's machinery with a distinct kind so reports can
  separate voluntary refunds from forced ones.
- Dispute fee (Stripe's per-dispute charge) posts separately:
  Dr a dispute-fees expense account / Cr 1050, source
  `"chargeback_fee"`. (Books needs one new expense account code —
  owner picks it.)
- `charge.dispute.closed` with status `won` **reverses** the
  provisional entry (books reinstates paid); status `lost` leaves it
  standing and marks the invoice's dispute terminal. Idempotency keys
  follow the established pattern (`dispute:<dispute_id>:<event>`), so
  Stripe's at-least-once delivery stays exactly-once in the ledger.
- Momo-side, disputes should also notify the shop (today: silent) —
  separate product decision, listed here because E flagged it.

**Owner decisions needed:**
1. Provisional posting on dispute-opened (books shows the exposure
   immediately, small churn on wins) vs terminal-only posting on
   dispute-lost (books stays clean until final, but understates risk
   during the dispute window, which can run 2–3 months).
2. The expense account for dispute fees.
3. Whether a won dispute's reversal is automatic (proposed) or a
   manual books action.

## 2. Void / cancel representation

**State today (verified by Track E):** Momo has **no void transition**
— the CRM hard-deletes an unpaid invoice (paid invoices cannot be
deleted) — and books has **no void endpoint**. Books' existing
correction mechanism is the credit note (liability account 2210),
which answers a different question (customer credit) than voiding
(annulling a document that should never have counted).

**Why it matters:** a synced invoice that is later deleted in Momo
leaves a live, revenue-bearing invoice in books forever, with no
signal. Even before sync, hard delete destroys the audit trail an
accounting system exists to keep.

**Proposal:**
- **Momo:** add a `void` status transition for invoices (replacing
  hard delete for anything beyond `draft`). Delete remains only for
  never-issued drafts. Voiding records who/when/why.
- **Books, unpaid void:** add `POST /invoices/{id}/void` to the sync
  surface (same auth + idempotency rules as the refund endpoint in
  `INTEGRATION_CONTRACT_ADDENDUM.md`): status → `void`, and the
  original issue entry is reversed (Dr 4020 / Cr 1200,
  source `"invoice_void"`). The invoice row is retained, never
  deleted — books already retains voided/credited history this way.
- **Books, paid void:** a paid invoice is never voided directly —
  money moved, so the correction is refund first (the F2 endpoint),
  then void of the now-unpaid invoice; or a credit note when the shop
  keeps the money as customer credit. The void endpoint rejects a
  paid/partial invoice with 422 pointing at those two paths.

**Owner decisions needed:**
1. Whether Momo's hard delete of unpaid invoices is removed entirely
   (proposed) or kept for invoices that were never synced.
2. Paid-void policy: refund-then-void only (proposed), or also allow
   void-with-credit-note in one action.
3. Whether void requires a reason code (proposed: optional free text;
   required for paid-history documents).

— End of design notes. Implementation is a separate, separately
approved track.
