# CSV Bridge — files for QuickBooks & spreadsheet accountants

**Decision (user, 2026-10-17): CSV bridge now; QuickBooks only if deals
demand it.** This is a file-based handoff, not an integration: there is
**no QuickBooks API, no OAuth, no Intuit anything** in Momo. The live sync
path stays the [bookkeeping link](backend/routers/bookkeeping_link.py) to
the shop's **own** bookkeeping app. The bridge exists for the other chair
at the table — the accountant who lives in QuickBooks Online (QBO) or a
spreadsheet and wants a clean file to import.

Code: `backend/routers/csv_bridge.py` · UI: the "CSV bridge" section of
**Settings → Bookkeeping** (`frontend/src/components/BookkeepingSettings.jsx`)
· Tests: `backend/tests/test_csv_bridge.py`.

## Endpoints

All routes are tenant-scoped: a shop only ever sees its own rows.

| Method | Path | Who | What |
|---|---|---|---|
| GET | `/api/tenants/csv-bridge/export/customers` | any tenant user | Customers CSV (QBO import shape) |
| GET | `/api/tenants/csv-bridge/export/invoices` | any tenant user | Invoices CSV, one row per line item |
| GET | `/api/tenants/csv-bridge/export/payments` | any tenant user | Payments CSV (generic accounting shape) |
| POST | `/api/tenants/csv-bridge/import/customers` | owner/admin | Multipart customer CSV upload; `?dry_run=true` (default) plans, `?dry_run=false` commits |

Exports accept optional `?start=YYYY-MM-DD&end=YYYY-MM-DD` (end inclusive).
The date is each record's creation date — for payments, the date the money
was received. Bad date formats return `400`.

## Column maps

### Customers export → QBO customer import

Shaped for QuickBooks Online's customer importer, whose sample sheet leads
with **Customer** (the display name).

| Column | Source in Momo |
|---|---|
| Customer / Display Name | customer `name` |
| Company Name | customer `company` (only set when data/import provided it; usually blank) |
| First Name / Last Name | `name` split on the last space |
| Email | `email` |
| Phone | `phone` (Mobile is exported blank — Momo has one phone field) |
| Street / City / State / ZIP Code / Country | customer `service_address` (line1 + line2 joined into Street) |
| Notes | `notes` |

Momo has no customer fax/website fields; those QBO columns are simply not
part of this file.

### Invoices export → QBO invoice import

One row **per line item**; all rows of one invoice share the `InvoiceNo`
(Momo's invoice id — Momo has no human invoice numbers).

| Column | Source |
|---|---|
| InvoiceNo | invoice `id` |
| Customer | `customer_name` |
| InvoiceDate / DueDate | `created_at` / `due_at` (date part) |
| Item / ItemDescription | line `description` (invoice `title` when a line has none) |
| ItemQuantity / ItemRate / ItemAmount | line `quantity` / `unit_price` / qty × rate (dollars, 2 dp) |
| Status | Momo status verbatim: `draft`, `sent`, `paid`, `overdue`, `void` |
| AmountPaid / Balance | invoice-level, repeated on each line row |

An invoice with no lines exports as a single row priced at its total.
Invoices are read from **both** Momo invoice shapes (mini-CRM dollar
`total` and call-to-payment `total_cents`) and normalised to dollars.

**QuickBooks note:** QBO's invoice importer has no Status column — it
derives status from payments. `Status`, `AmountPaid` and `Balance` ride
along as extra columns so the accountant can reconcile by eye; to make
invoices show as paid inside QBO, enter the payments (next file) against
them there.

### Payments export → generic accounting

| Column | Source |
|---|---|
| Date | date the payment succeeded |
| InvoiceNo | the Momo invoice id it paid |
| Customer | invoice `customer_name` |
| Amount | dollars, 2 dp |
| Method / Provider | `card` / `stripe` for electronic payments; `manual` / `momo` for hand-recorded ones |
| Provider Payment ID | Stripe payment-intent id (blank for manual rows) |
| Status | `succeeded` (failed attempts are never exported) |

Two sources, deduplicated: (1) every succeeded row of Momo's
`invoice_payments` (Stripe), then (2) **one** row per fully-paid invoice
that has *no* electronic payment (it was marked paid by hand), dated by
its `paid_at`. So the payments total ties out to the invoices export's
AmountPaid column instead of silently dropping cash/check work.

## What the accountant does with the files (QBO)

1. **Customers:** QBO → Settings → Import data → Customers → upload
   `momo-customers-*.csv`, map columns (they line up with QBO's names),
   import.
2. **Invoices:** QBO → Settings → Import data → Invoices → upload
   `momo-invoices-*.csv`. Rows group into invoices by `InvoiceNo`.
3. **Payments:** QBO's importer does not take standalone payments; use
   `momo-payments-*.csv` as the record of what was received and apply
   "Receive payment" in QBO for the paid invoices (the file's
   InvoiceNo/Amount/Date/Provider Payment ID are the audit trail).
4. Re-export from Momo whenever fresh books are needed — each export is a
   full snapshot of the chosen date range, not a delta.

## Customer import (the way back in)

Upload a customers CSV — Momo's own export, a QBO customer export, or a
spreadsheet — and get a **per-row plan** back:

- `create` — no match in this tenant; a new customer will be added.
- `update` — matched, and listed fields will change (`updates: phone, …`).
- `skip` — matched and already identical.
- `error` — row rejected, with a readable reason (missing name, invalid
  email, too many columns); other rows still process.

**Dedupe rules (within the tenant only):** match by **email first**
(case-insensitive), then by **exact name** (case-insensitive). A stored
email is never overwritten by a name-matched row. Blank cells in the file
never erase stored data. Duplicates *inside* the file resolve in order, so
the second copy of a row plans as `skip`/`update` against the first.
Accepted headers include `Customer`, `Display Name`, `Name`,
`First Name` + `Last Name`, `Email`, `Phone`/`Mobile`, `Company Name`,
`Street`, `City`, `State`, `ZIP Code`/`Postal Code`, `Country`, `Notes`
(and the billing variants). Address columns land in the customer's
`service_address`.

**Dry run first, always:** `dry_run` defaults to `true` — the plan is
computed against an in-memory copy and nothing is written. The UI's
"Import now" button only unlocks for the exact file you just previewed.
Limits: 2 MB and 5,000 rows per file; invalid UTF-8 and missing
name-column headers are rejected with a `400` and an explanation.

## Honest limits

- **Files, not a live sync.** Nothing here talks to QuickBooks; if a shop
  needs two-way, always-current QBO sync, that is the separate,
  not-yet-approved provider build ("QuickBooks only if deals demand it").
- **Invoices are export-only.** Momo invoices carry no natural external
  key (ids and public tokens are server-generated; there is no invoice
  number), so an import could not dedupe safely and might double-bill a
  customer. Customers have natural keys (email, name), so they import.
- **Exports are capped at 10,000 records** per file; use date ranges for
  larger histories.
- **Amounts are dollars with two decimals**, in the shop's operating
  currency as recorded in Momo; no currency conversion happens here.
- Phone numbers and ZIP codes are passed through exactly as stored —
  spreadsheets that strip leading zeros will show that damage on
  re-import; the plan report makes every change visible before commit.
