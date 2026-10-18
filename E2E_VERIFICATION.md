# Track E — Financial-Sync & End-to-End Verification

**Program:** Momo Empire final audit (owner directive, 2026-10-17), Track E.
**Scope:** directive §4 (complete financial synchronization) + end-to-end
workflow verification, run against the two audit branches, plus formal
reconciliation of the integration contract between them.
**Branches verified (nothing merged):**

- Books: `audit/idempotency-integration-auth` (PR #144, head `441177a`,
  base main `653e49b`) — contract source: `INTEGRATION_CONTRACT.md`.
- Momo: `audit/final-integration-security` (PR #98, head `dfcc445`,
  base main `bee3e86e`).
- This branch: `audit/financial-e2e-verification`, created from the
  Track B head. It contains two small client fixes (§1, D-1/D-2), the
  verification harness (`backend/tests/test_track_e_financial_sync.py`),
  and this report.

**Method.** The real books FastAPI app (Track A branch) was served over
HTTP on localhost with Track A's own in-memory FakeDB (which emulates
the unique indexes the idempotency claim relies on), seeded with two
tenants, real integration credentials minted through the real issuance
endpoint, and owner JWTs minted by the app's own token factory. The
real Momo client/link code (Track B branch) ran against it over HTTP.
All data, credentials and payments are synthetic; no production data,
no real charges, no Stripe/Twilio network calls. **Result: 21/21
harness tests pass** against a fresh books server (the harness expects
a fresh fake-DB server; see "Reproduce" at the end). Regression check:
the failure/error sets of the adjacent suites
(`test_bookkeeping_credentials`, `test_seat_limits`,
`test_bookkeeping_link`, `test_call_to_payment`) are **byte-identical**
with and without this branch's fixes (3 failed / 17 errors, all
pre-existing environmental baseline); `test_csv_bridge` 15/15 and the
books `test_integration_sync` 25/25 pass.

---

## 1. Contract reconciliation — books contract vs Momo client

| # | Area | Contract (books) | Client (Momo, PR #98) | Verdict |
|---|------|------------------|------------------------|---------|
| D-1 | Health / connection test | §1.4, §4.2: `mae1.*` → `GET /api/integrations/me`; legacy JWT → `GET /api/auth/me` | `test_connection()` unconditionally called `GET /auth/me` and required a user `id` in the response | **DIVERGED — Confirmed Broken, FIXED on this branch** |
| D-2 | Credential-kind classification | §1.1: format marker is the `mae1.` prefix | Kind inferred from JWT shape (3 dot-segments); a `mae1.` credential has exactly 3 segments → classified `legacy_jwt`; the frontend never sends an explicit kind | **DIVERGED — Confirmed Broken, FIXED on this branch** |
| D-3 | Revocation signal | §1.3: revoked → `401 {"detail": "Integration credential revoked"}` | Scans 401/403 bodies for "revoked" in `code`/`error`/`detail`/`message` or raw text | **ALIGNED** — verified live end-to-end |
| D-4 | Base URL `/api` segment | Paths are `/api/...`; §0/§4 flag: "verify the configured base URL includes the `/api` segment" | `DEFAULT_API_BASE_URL` and the settings-UI placeholder are the bare origin (no `/api`); the provider concatenates base + path | **DIVERGED — Confirmed Broken (pre-existing), not fixed here** |
| D-5 | `Idempotency-Replayed` header / 409 handling | §4.3: treat replay header as success; payload-mismatch 409 = hard error | Client discards response headers entirely; replayed bodies are accepted as ordinary success; any 409 → hard sync failure | **ALIGNED IN EFFECT** (low) — see notes |
| D-6 | Rotation procedure | §1.2/§4.4: books rotation kills the old secret **immediately**; runbook = rotate in books → paste into Momo promptly | Momo rotate endpoint + UI copy imply "the old credential keeps working until the new one proves valid" | **PARTIALLY DIVERGED** (documentation/procedure, low) |
| D-7 | Settings UX copy | Target state: paste a books-issued integration credential | `en.json` token hint still says "Use your bookkeeping app owner's login token (from its sign-in)" | **DIVERGED** (copy, low) |
| D-8 | Expiry surfacing | §1.4: `/integrations/me` returns `expires_at`; owner replaces before expiry | Client now returns `expires_at` from `test_connection` (post-D-1 fix) but the link layer never stores/displays it → no advance lapse warning | **GAP** (small feature) — Requires Owner Decision |

### D-1 detail (severity: HIGH — client side changed, here)

Every Momo connection-validation path (`POST /test-connection`,
`POST /health-check`, the cron health sweep, and the
validate-then-swap inside `POST /rotate-credential`) funnels through
`MindAtEaseProvider.test_connection()`. With a `mae1.*` credential that
call hit `GET /auth/me`, which books rejects for integration
credentials by design (contract §1.3). Proven live against the real
books app:

- `GET /api/integrations/me` + credential → **200** (correct tenant, scopes)
- `GET /api/auth/me` + same credential → **401 "Invalid session"**
- `GET /api/auth/me` + owner JWT → **200** (legacy path healthy)
- `GET /api/integrations/me` + owner JWT → **401 "Integration credential required"**

Consequences pre-fix: test-connection always failed for migrated
tenants; health checks marked working links degraded; and **rotation
through the Momo UI could never succeed for an integration credential**
(the candidate always "failed validation", HTTP 400). Saving via
settings PUT (unvalidated) still synced, which masked the break.
Additionally the `/integrations/me` response shape (`ok`, `client_id`,
`label`, `scopes`, `tenant_id`, `tenant_name`, `expires_at` — no user
`id`) was incompatible with the client's parsing. **Fix (this
branch):** `test_connection()` branches on the `mae1.` prefix exactly
as contract §4.2 prescribes and parses the §1.4 shape; legacy JWTs
keep the `/auth/me` path. The D-1 harness test fails on pristine
Track B code and passes with the fix (both verified).

### D-2 detail (severity: MEDIUM — client side changed, here)

`_classify_kind()` inferred kind from token shape: 3 dot-separated
segments = legacy JWT. A books credential `mae1.<client_id>.<secret>`
is also 3 segments, and neither the settings save nor the rotate
payload from the frontend carries an explicit `credential_kind`, so
**every real integration credential was stored as `legacy_jwt`** and
the "migration required" prompt never cleared for migrated tenants
(support/UX state wrong; sync itself unaffected). Track B's tests
never used a real `mae1.`-shaped token (they used `bk_live_opaque_123`).
**Fix (this branch):** check the `mae1.` prefix before the shape test
— the same marker books' own `is_integration_token` uses. Harness test
fails on pristine Track B code, passes with the fix.

### D-3 detail (aligned)

Books returns `401 {"detail": "Integration credential revoked"}` for a
revoked credential and `401 {"detail": "Integration credential
expired"}` for an expired one (generic `"Invalid integration
credential"` otherwise, revealing nothing). The client's body scan
matches the revoked detail → `kind="revoked"` → health `revoked` →
sync pauses with reason `credential_revoked`. Verified live through
the full flow: issue credential #2 in books → rotate from Momo
(validate-then-swap) → revoke in books → health check reports
`revoked`, sync pauses, provider raises `kind="revoked"`. A bad secret
yields `kind="auth"` with the generic books message (no oracle).

### D-4 detail (severity: HIGH for new self-serve connections)

The contract's canonical paths include `/api` and it explicitly
assigned the base-URL check to the Momo side. Momo's default
(`DEFAULT_API_BASE_URL = "https://bookkeeping-api-ymh6.onrender.com"`)
and the settings placeholder omit `/api`, and the provider builds
`{base}{path}` — so a tenant who accepts the default gets 404s on
every sync/test call and can never connect. This predates both audit
branches. Tenants whose stored `api_base_url` already includes `/api`
are unaffected, and stored settings override the default, so changing
the default + placeholder to include `/api` is a safe one-line-each
fix — left for the owner to approve with this report rather than
folded in silently, because it is a product-visible default.

### D-5 detail (aligned in effect, low)

The client cannot see the `Idempotency-Replayed` header (`_request`
returns parsed JSON only). Behaviorally this is safe today: a replayed
response is the stored success body verbatim, so `push_*` extract the
same ids and treat it as success — proven live (provider redelivery
returns the identical entry number; the raw HTTP redelivery shows
`Idempotency-Replayed: true` with a byte-identical body and no new
journal entry). The payload-mismatch 409 surfaces as
`AccountingError(kind="http")` and fails the sync permanently for that
invoice — the contract's "hard error" semantics. The server's
in-progress 409 (sent only after its own ~15 s bounded wait) would
also surface as a hard failure instead of a retry; under the server's
design the client should essentially never see it. Optional future
hardening: surface the replay header in provider results for
observability.

### D-6 detail (procedure, low)

Track B's working assumption of a dual-secret grace window does not
exist in the contract: books rotation replaces the secret in place and
the old one dies immediately (verified in books code + Track A test
`test_rotation_kills_old_secret`). The Momo validate-then-swap flow is
still contract-compatible — proven live — **when the operator issues a
second credential in books, pastes it into Momo's rotate flow, then
revokes the old one**. The unsafe reading of the current Momo copy
("verified before it replaces the current one — sync never breaks")
is an operator who rotates *in books first*: sync then 401s until the
paste happens (idempotent catch-up makes the gap safe, not broken).
Recommend a runbook note + copy tweak on the Momo side; no code change
needed on either side.

### Aligned items verified (no divergence)

Credential format/prefix detection; `books:sync`-only scoping;
credential accepted **only** on the three sync endpoints +
`/integrations/me` and rejected elsewhere; JWT rejected on
`/integrations/me`; tenant always derived from the credential record
(cross-tenant payment attempt → 404, proven live); request/response
payload shapes for all three pushes; pay response `entry_number`;
idempotency-key derivation (SHA-256 of tenant + local record + step —
matches contract §4.3 exactly); journal audit actor
(`integration+<client_id>@system.local`); books-side expiry behavior
(401 + generic surfacing as "needs attention" → client `degraded`).

---

## 2. Financial sync matrix (§4)

All rows ran live over HTTP (Momo client/link code → real books app)
unless marked "books API" (raw HTTP against the books sync surface
with the tenant's integration credential) or "handler-level".

| Scenario | Result | Evidence |
|---|---|---|
| Successful payment | **PASS** | Full sync (customer → invoice → payment) via `sync_invoice_paid`; books invoice $250.00 fully paid, exactly 2 new journal entries (issue + payment), ledger delta balanced (Σ=0), AR 1200 nets 0, revenue 4020 −$250, one cash account +$250 |
| Partial payments | **PASS (books API); Momo path differs** | Books `/pay`: $100 of $300 → paid_cents 10000, balance 20000, not paid; +$200 → paid, balance 0. Momo's sync never sends partials: Stripe partials only flip Momo to paid at completion, and sync then posts the full total once (code-verified + journey-verified) |
| Overpayment | **PASS** | $150 on a $100 invoice → `overpayment_cents` 5000 parked as a customer credit (2210); journal balanced |
| Refunds — books side | **PASS** | Credit refund via owner JWT posts a balanced entry and zeroes the credit (2210 nets 0 over the whole flow) |
| Refunds — Momo → books sync | **CONFIRMED BROKEN (gap)** | `_apply_refund` adjusts Momo only (`amount_paid_cents`, status); nothing calls the books link. Proven live: after a full $180 refund of a synced invoice, books still shows it paid $180 with no new journal entries. Books has no payment-refund endpoint on the sync surface (refunds exist only as owner-driven credit refunds) |
| Chargebacks / disputes | **CONFIRMED BROKEN (gap)** | Momo represents a dispute as markers only (`dispute_id`, `disputed_at`; no amount/status/financial effect — `_apply_dispute`); books has **no** dispute/chargeback representation at all (no fields, no endpoints). Nothing syncs. A lost dispute would leave both systems showing the money as settled |
| Failed payment | **PASS** | Unpaid invoice → sync returns `not_paid`, zero books trace (customers/invoices counts unchanged); paying a settled invoice errors instead of double-posting |
| Canceled / voided invoice | **CONFIRMED BROKEN (gap)** | Momo's model has a `void` status but no router transition to it was found; CRM offers a hard DELETE instead. Neither void nor delete syncs; books has no void endpoint (credit notes are its correction mechanism). A synced invoice canceled in Momo stays live in books |
| Payment retry after failure | **PASS** | A failed keyed request records a `failed` idempotency record; an identical retry re-claims it (attempts 1→2 observed) and re-executes; same key + *fixed* payload → 409 by design (retry must be the same logical operation). Recovery-to-success after failure is covered by Track A's `test_interrupted_then_retried_sync_recovers` |
| Duplicate delivery (same key) | **PASS** | Provider-level and raw redelivery of a payment: stored response replayed verbatim with `Idempotency-Replayed: true`, same entry number, journal entry count unchanged, exactly one payment on the invoice |
| Interrupted sync (write done, response lost) | **PASS** | Seeded crash remnant (invoice + journal in books, `sync_key`-stamped, outcome record lost): the retry adopted the stamped invoice (no duplicate), the payment posted exactly once (+1 journal entry). The deeper stale-`processing` takeover and concurrency cases are covered by Track A's tests against the same fake-index semantics |
| Reconciliation | **PASS** | For every synced invoice: Momo total == books total == books paid; journal deltas balance to zero; AR/revenue/cash movements tie to the invoice amounts (delta method against tenant-wide balances) |
| Tenant separation | **PASS** | Identical `Idempotency-Key` strings under two tenants create independent records/customers; tenant A's credential paying tenant B's invoice → 404 (tenant comes from the credential, never the request) |
| Currency handling | **PASS (spot check)** | Books posts an explicit-currency invoice (EUR) with the currency recorded; the Momo client never sends a currency, and Momo's Invoice model is USD-only, so synced invoices land as USD — correct today; if MXN shop invoicing ships, the client must send currency or amounts would book as USD |
| Repeat customer, second invoice | **CONFIRMED BROKEN** | See below |
| CRM mark-paid | **CONFIRMED BROKEN (gap)** | See below |
| CSV bridge | **PASS** | Exports (customers/invoices/payments) return QBO-shaped CSV containing the seeded records; customer import is dry-run by default, dedupes on email/name, and a dry run creates nothing. Existing `test_csv_bridge` suite: 15/15 |
| QuickBooks API | **ABSENT, as directed** | No Intuit client/OAuth anywhere; `get_provider("quickbooks")` raises; "QuickBooks" appears only in comments/docstrings and CSV shaping. The CSV bridge is the only path, per the directive |

### Confirmed Broken — repeat-customer sync block (severity: HIGH)

Momo pushes the customer once **per invoice** (the idempotency key is
derived from tenant + *invoice* + step), and books' natural duplicate
guard (same normalized name AND same email or phone → 409 "Customer
already exists", with `existing_id` in the body) rejects the second
push. The client does not adopt the returned `existing_id`, so
`sync_invoice_paid` fails the whole sync. Proven live: a repeat
customer's first invoice syncs; their second paid invoice returns
`{"synced": false}` with a 409 error recorded on the tenant settings.
This predates both audit branches (per-invoice keys and the natural
guard both predate the audit) — the audit's end-to-end pass is what
exposes it. Impact: in production, every repeat customer's later paid
invoices would silently stop syncing (the failure is recorded in
`last_error`/`sync_failures` and degrades health, so it is visible,
not silent). Fix directions (owner's choice): (a) client adopts
`existing_id` from the 409 body — smallest change, uses books'
existing response contract; (b) books returns the existing customer
for keyed re-pushes; (c) Momo keys the customer step by customer
identity and caches the provider id per customer.

### Confirmed Broken — CRM mark-paid never syncs (severity: MEDIUM)

`POST /tenants/invoices/{id}/mark-paid` (CRM) flips Momo state only;
`sync_invoice_paid` has exactly two callers — the Stripe success
handler and the link's manual endpoint. Proven live: after mark-paid,
books shows no trace; the manual "Sync now" endpoint then syncs the
same invoice successfully (the existing recovery path). Cash/check
payments recorded via mark-paid therefore never reach the books until
someone presses Sync now.

---

## 3. End-to-end workflow (§7 journey), two industries × two tenants

Journey: inquiry → AI receptionist → lead → appointment → tech
assignment → estimate → invoice → payment → books sync → follow-up.
Run for an **HVAC** tenant (Diagnostic Visit; AC compressor
replacement, $250) and a **plumbing** tenant (Water Heater Install,
$300), each with its own books tenant and credential. Both journeys
pass end-to-end in the harness.

| Leg | Status | Notes |
|---|---|---|
| Inquiry | **Ran at handler level** | Lead captured with `source="ai_receptionist"` — the record the receptionist flow produces. The public waitlist form endpoint exists but was not driven (needs full HTTP request plumbing + its rate limiter) |
| AI receptionist | **Not runnable live here** | Inbound voice is Twilio-driven and signature-gated; no Twilio credentials or carrier egress exist in this sandbox, and no live LLM key is available. Not faked: the leg's logic is covered at mock level by the pre-existing `test_e2e_journey_smoke.py` (LLM structured output stubbed at the module seam, deterministic no-key fallback exercised — green in this run) and `test_outbound_voice.py` (green). `test_twilio_webhook_signatures.py` has a pre-existing import error in this environment, identical on pristine Track B |
| Lead | **Ran live (handler)** | Real `POST /tenants/leads` handler, both tenants |
| Appointment | **Ran live (handler)** | Real create-appointment handler; service names taken from each industry's pack appointment types |
| Tech assignment | **Ran live (handler)** | Real dispatch `/assign` against `schedule_staff`; assignment card verified |
| Estimate | **Ran live (handler)** | Real CRM estimate create + send (owner-approval opens online acceptance) |
| Estimate → invoice | **Ran live (handler)** | The **real public quote-accept flow** (`/public/c2p/quotes/{token}/accept`) created the job appointment + invoice and marked the estimate approved |
| Payment | **Ran at handler level** | The real `_apply_payment_success` — the exact handler the signed Stripe webhook dispatches to — with a synthetic PaymentIntent and the `invoice_payments` record the live flow would have created (same pattern as the pre-existing smoke test). Live Stripe is not runnable in this sandbox (no test keys/network); webhook signature verification itself is covered by the pre-existing smoke test with a local test secret |
| Books sync | **Ran LIVE over HTTP** | Auto-sync fired from inside the payment handler; the real Momo client pushed customer → invoice → payment to the real books app; books journal gained exactly the issue + payment entries; totals reconcile ($250 / $300) |
| Follow-up | **Ran live (handler)** | Completing the job appointment fired the real post-job automation; the 60-day win-back follow-up job was scheduled in the DB (asserted). Thank-you SMS/email sends were stubbed at the `post_job` module boundary (no Twilio/SMTP in this sandbox); the payment receipt email was stubbed the same way |

---

## 4. Findings by classification

### Confirmed Fixed (on this branch, for owner review — NOT merged)

- **D-1** — `test_connection()` ignored the contract's health-endpoint
  split, breaking test-connection, health checks, and credential
  rotation for every migrated (integration-credential) tenant.
  Fixed in `backend/services/accounting.py` (branch on `mae1.`,
  parse the §1.4 response shape). Regression test fails pre-fix,
  passes post-fix.
- **D-2** — Real `mae1.*` credentials were auto-classified as
  `legacy_jwt`, so the migration state never cleared. Fixed in
  `backend/routers/bookkeeping_link.py` (`mae1.` prefix checked
  before the JWT shape test). Regression test fails pre-fix, passes
  post-fix.

### Confirmed Broken (not fixed — exceed a client-only, unambiguous fix or need a side decision)

1. **Repeat-customer sync block** (HIGH) — §2 above. Pre-existing;
   fix direction is an owner choice (client `existing_id` adoption
   recommended).
2. **Refunds never sync to books** (HIGH) — Momo-side refunds leave
   books showing the invoice paid; books exposes refunds only as
   owner-driven credit refunds, not on the sync surface.
3. **Chargebacks/disputes unrepresented in books, marker-only in
   Momo** (MEDIUM) — no financial effect is recorded anywhere for a
   dispute; a lost dispute leaves both systems overstated.
4. **Canceled/voided/deleted invoices never sync** (MEDIUM) — no Momo
   void transition found; CRM hard-delete and any void leave the
   books copy live; books has no void endpoint.
5. **CRM mark-paid never triggers sync** (MEDIUM) — manual recovery
   exists (Sync now endpoint).
6. **Default base URL omits `/api`** (HIGH for new connections,
   pre-existing) — D-4; safe one-line fix awaiting owner approval.

### Needs Testing (could not be fully exercised in this sandbox)

- Idempotency under **concurrent** duplicate submissions against a
  real Mongo/Atlas (the fake DB serializes; Track A's fake-index
  tests + the unique-index design cover the logic — retest at deploy).
- **Expired-credential** live behavior (books rejects with 401 per
  Track A's unit test; the API cannot mint an already-expired
  credential — minimum life is 1 day).
- Live **Twilio** receptionist turn and live **Stripe** (test-mode)
  payment → webhook → sync, post-deploy with test keys.
- EUR **payment-side** FX realization (invoice posting verified;
  the pay path's best-effort FX leg was not exercised end-to-end).
- D-5's in-progress-409 path (requires beating the server's 15 s
  bounded wait).

### Requires Owner Decision

- Fix direction for the repeat-customer block (client vs books vs
  keying change).
- Whether refunds / disputes / voids should sync at all — that is new
  scope (books sync-surface endpoints + client methods + a dispute
  state model in books), not an audit fix.
- D-4 default base-URL change (recommended: include `/api` in the
  default and the UI placeholder).
- D-8 expiry-warning surfacing (store `expires_at` from health
  checks; warn ≤30 days out).
- D-6/D-7 copy updates (rotation runbook wording; the stale
  login-token hint in the settings UI).

### Explicitly verified as NOT problems

Tenant isolation of the sync surface (credential-bound tenants,
identical keys independent, cross-tenant 404); books-side
idempotency (replay verbatim + header, 409 on payload change,
failed-record reclaim, crash-window `sync_key` adoption); books
partial/overpayment/credit/refund accounting with balanced journals;
the CSV bridge (working, dry-run-safe); the absence of any
QuickBooks API integration.

---

## Reproduce

1. Books harness (scratch, not committed): serve the Track A books
   app with Track A's FakeDB — two seeded tenants, credentials issued
   through the real endpoint, owner JWTs, one seeded crash-window
   remnant, USD/EUR rates — writing `/tmp/track-e/state.json` +
   per-second snapshots to `/tmp/track-e/books_state.json`.
2. `cd backend && python -m pytest tests/test_track_e_financial_sync.py`
   → **21 passed** (module skips cleanly when the harness server is
   absent). Note the harness expects a fresh books server: the
   crash-window and journey scenarios are single-shot per server
   lifetime (a second run against a dirty server fails those three
   for state-pollution reasons, not product reasons).
3. Sandbox note: the environment's proxy variables break httpx URL
   parsing for localhost (bracketed IPv6 `NO_PROXY` entries); the
   harness scrubs them in-process. This is a sandbox artifact, not a
   product defect.
