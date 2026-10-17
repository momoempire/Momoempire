"""CSV bridge: file-based accounting handoff for shops whose accountant
lives in QuickBooks Online or a spreadsheet.

Product decision (user, 2026-10-17): **CSV bridge now; QuickBooks only if
deals demand it.** So this module is deliberately files-in / files-out —
NO QuickBooks API, NO OAuth, NO Intuit anything. It complements the live
bookkeeping link (routers/bookkeeping_link.py), which syncs to the tenant's
OWN bookkeeping app; the bridge is for the accountant on the other side of
the fence who wants a file they can import themselves.

Routes (all tenant-scoped, mounted under /api by server.py):
    GET  /tenants/csv-bridge/export/customers  — QBO-shaped customer CSV.
    GET  /tenants/csv-bridge/export/invoices  — QBO-shaped invoice CSV
                                                 (one row per line item).
    GET  /tenants/csv-bridge/export/payments   — generic payments CSV
                                                 (Stripe invoice payments,
                                                 plus one row per
                                                 manually-paid invoice that
                                                 has no electronic payment).
    POST /tenants/csv-bridge/import/customers  — multipart CSV upload.
                                                 ?dry_run=true (default)
                                                 returns a per-row plan;
                                                 ?dry_run=false executes it.

Exports are reads (any tenant user). Import is a write (owner/admin only) —
the same guard split as the bookkeeping link.

Invoices are EXPORT-ONLY: Momo invoices have no natural external key (ids
and public tokens are server-generated; there is no invoice number), so a
file import could not dedupe safely and might double-bill a customer.
Customers have natural keys (email, then exact name) and import cleanly.

See CSV_BRIDGE.md at the repo root for the column maps and limits.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin

log = logging.getLogger("csv_bridge")

router = APIRouter(prefix="/tenants/csv-bridge", tags=["csv-bridge"])

MAX_EXPORT_ROWS = 10000
MAX_IMPORT_BYTES = 2_000_000  # ~2 MB of CSV is a lot of customers
MAX_IMPORT_ROWS = 5000

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------- shared helpers ----------

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _csv_response(filename: str, header: List[str], rows: List[List[Any]]) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _validate_date(value: Optional[str], param: str) -> Optional[str]:
    if value is None or value == "":
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except (ValueError, TypeError):
        raise HTTPException(400, f"{param} must be a date like 2026-01-31 (YYYY-MM-DD)")
    return value


def _in_range(iso_value: Any, start: Optional[str], end: Optional[str]) -> bool:
    """Date-range test on the YYYY-MM-DD prefix of an ISO timestamp.
    `end` is inclusive (it names a whole day)."""
    day = str(iso_value or "")[:10]
    if not day:
        return start is None and end is None
    if start and day < start:
        return False
    if end and day > end:
        return False
    return True


def _money_from_cents(cents: Any) -> str:
    try:
        return f"{(int(cents or 0)) / 100:.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _money(value: Any) -> str:
    try:
        return f"{float(value or 0):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _invoice_total(inv: Dict[str, Any]) -> float:
    """Invoices exist in two shapes: the call-to-payment flow stores
    total_cents; the mini-CRM stores a dollar `total`. Handle both."""
    if inv.get("total_cents") is not None:
        try:
            return int(inv.get("total_cents") or 0) / 100
        except (TypeError, ValueError):
            return 0.0
    try:
        return float(inv.get("total") or 0)
    except (TypeError, ValueError):
        return 0.0


def _invoice_amount_paid(inv: Dict[str, Any]) -> float:
    if inv.get("amount_paid_cents") is not None:
        try:
            return int(inv.get("amount_paid_cents") or 0) / 100
        except (TypeError, ValueError):
            return 0.0
    if inv.get("status") == "paid":
        return _invoice_total(inv)
    return 0.0


# ---------- export: customers ----------

# Shaped for QuickBooks Online's customer import: its sample sheet leads
# with "Customer" (the display name) and recognises the contact/address
# columns below. Momo has no company field on most customers and no fax /
# website fields at all — those columns are exported empty, never invented.
CUSTOMER_COLUMNS = [
    "Customer", "Display Name", "Company Name", "First Name", "Last Name",
    "Email", "Phone", "Mobile", "Street", "City", "State", "ZIP Code",
    "Country", "Notes",
]


def _split_name(name: str) -> tuple:
    parts = (name or "").strip().rsplit(" ", 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


@router.get("/export/customers")
async def export_customers(start: Optional[str] = Query(None),
                           end: Optional[str] = Query(None),
                           user: dict = Depends(require_tenant_user)):
    start = _validate_date(start, "start")
    end = _validate_date(end, "end")
    db = get_db()
    rows = await db.customers.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}
    ).sort("created_at", 1).to_list(MAX_EXPORT_ROWS)
    out: List[List[Any]] = []
    for c in rows:
        if not _in_range(c.get("created_at"), start, end):
            continue
        addr = c.get("service_address") or {}
        street = " ".join(x for x in [str(addr.get("line1") or "").strip(),
                                      str(addr.get("line2") or "").strip()] if x)
        first, last = _split_name(str(c.get("name") or ""))
        out.append([
            c.get("name") or "", c.get("name") or "", c.get("company") or "",
            first, last, c.get("email") or "", c.get("phone") or "", "",
            street, addr.get("city") or "", addr.get("state") or "",
            addr.get("postal_code") or "", addr.get("country") or "",
            c.get("notes") or "",
        ])
    return _csv_response(f"momo-customers-{_today()}.csv", CUSTOMER_COLUMNS, out)


# ---------- export: invoices ----------

# Shaped for QuickBooks Online's invoice import: one row per line item,
# lines of one invoice sharing an InvoiceNo. QuickBooks has no Status
# column in its importer (it derives status from payments), so Status /
# AmountPaid / Balance ride along as extra bookkeeping columns — an
# accountant importing by hand uses them to reconcile, and the Payments
# CSV is what marks invoices paid inside QuickBooks.
INVOICE_COLUMNS = [
    "InvoiceNo", "Customer", "InvoiceDate", "DueDate", "Item",
    "ItemDescription", "ItemQuantity", "ItemRate", "ItemAmount",
    "Status", "AmountPaid", "Balance",
]


@router.get("/export/invoices")
async def export_invoices(start: Optional[str] = Query(None),
                          end: Optional[str] = Query(None),
                          user: dict = Depends(require_tenant_user)):
    start = _validate_date(start, "start")
    end = _validate_date(end, "end")
    db = get_db()
    invoices = await db.invoices.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}
    ).sort("created_at", 1).to_list(MAX_EXPORT_ROWS)
    out: List[List[Any]] = []
    for inv in invoices:
        if not _in_range(inv.get("created_at"), start, end):
            continue
        total = _invoice_total(inv)
        paid = _invoice_amount_paid(inv)
        head = [
            inv.get("id") or "", inv.get("customer_name") or "",
            str(inv.get("created_at") or "")[:10],
            str(inv.get("due_at") or "")[:10],
        ]
        tail = [inv.get("status") or "", f"{paid:.2f}", f"{total - paid:.2f}"]
        lines = inv.get("lines") or []
        if not lines:
            out.append(head + [inv.get("title") or "Invoice",
                               inv.get("title") or "", "1",
                               f"{total:.2f}", f"{total:.2f}"] + tail)
            continue
        for ln in lines:
            try:
                qty = float(ln.get("quantity") or 1)
            except (TypeError, ValueError):
                qty = 1.0
            try:
                rate = float(ln.get("unit_price") or 0)
            except (TypeError, ValueError):
                rate = 0.0
            desc = str(ln.get("description") or inv.get("title") or "Service")
            qty_s = str(int(qty)) if qty == int(qty) else str(qty)
            out.append(head + [desc, desc, qty_s, f"{rate:.2f}",
                               f"{qty * rate:.2f}"] + tail)
    return _csv_response(f"momo-invoices-{_today()}.csv", INVOICE_COLUMNS, out)


# ---------- export: payments ----------

# Generic accounting shape (not QuickBooks-specific): one row per received
# payment. Sources, in order: succeeded rows of invoice_payments (Stripe —
# the electronic record), then one row per fully-paid invoice that has NO
# electronic payment (marked paid by hand in Momo), so the accountant's
# totals tie out to the invoice export's AmountPaid column.
PAYMENT_COLUMNS = [
    "Date", "InvoiceNo", "Customer", "Amount", "Method",
    "Provider", "Provider Payment ID", "Status",
]


@router.get("/export/payments")
async def export_payments(start: Optional[str] = Query(None),
                          end: Optional[str] = Query(None),
                          user: dict = Depends(require_tenant_user)):
    start = _validate_date(start, "start")
    end = _validate_date(end, "end")
    db = get_db()
    tid = user["tenant_id"]
    invoices = await db.invoices.find(
        {"tenant_id": tid}, {"_id": 0}).to_list(MAX_EXPORT_ROWS)
    by_id = {inv.get("id"): inv for inv in invoices}
    payments = await db.invoice_payments.find(
        {"tenant_id": tid, "status": "succeeded"}, {"_id": 0}
    ).sort("created_at", 1).to_list(MAX_EXPORT_ROWS)

    out: List[List[Any]] = []
    invoiced_with_payments = set()
    for p in payments:
        inv = by_id.get(p.get("invoice_id")) or {}
        if inv:
            invoiced_with_payments.add(inv.get("id"))
        when = p.get("succeeded_at") or p.get("created_at") or ""
        if not _in_range(when, start, end):
            continue
        amount = p.get("amount_received_cents")
        if amount is None:
            amount = p.get("amount_cents")
        out.append([
            str(when)[:10], p.get("invoice_id") or "",
            inv.get("customer_name") or "", _money_from_cents(amount),
            "card", "stripe", p.get("stripe_payment_intent_id") or "",
            "succeeded",
        ])
    for inv in invoices:
        if inv.get("status") != "paid" or inv.get("id") in invoiced_with_payments:
            continue
        when = inv.get("paid_at") or ""
        if not when or not _in_range(when, start, end):
            continue
        out.append([
            str(when)[:10], inv.get("id") or "",
            inv.get("customer_name") or "",
            f"{_invoice_amount_paid(inv):.2f}",
            "manual", "momo", "", "succeeded",
        ])
    out.sort(key=lambda r: r[0])
    return _csv_response(f"momo-payments-{_today()}.csv", PAYMENT_COLUMNS, out)


# ---------- import: customers ----------

# Header aliases (lowercased, stripped) → canonical field. Accepts this
# module's own export headers first, then the common QuickBooks /
# spreadsheet variants, so an accountant can hand back an edited export.
_HEADER_ALIASES: Dict[str, str] = {}


def _alias(field: str, *names: str) -> None:
    for n in names:
        _HEADER_ALIASES[n] = field


_alias("name", "customer", "display name", "customer name", "name", "full name")
_alias("email", "email", "e-mail", "email address")
_alias("phone", "phone", "phone number", "mobile", "mobile phone", "telephone")
_alias("company", "company name", "company", "business name")
_alias("notes", "notes", "note")
_alias("street", "street", "street address", "address", "service address",
       "bill street", "billing street")
_alias("city", "city", "bill city", "billing city")
_alias("state", "state", "province", "bill state", "billing state")
_alias("zip", "zip code", "zip", "postal code", "postcode", "bill zip",
       "billing zip", "billing postal code")
_alias("country", "country", "bill country", "billing country")
_alias("first_name", "first name", "firstname")
_alias("last_name", "last name", "lastname")

_ADDRESS_DEFAULTS = {"line1": "", "line2": "", "city": "", "state": "",
                     "postal_code": "", "country": "US"}


def _plan_customer_import(existing: List[Dict[str, Any]],
                          parsed_rows: List[Dict[str, str]]):
    """Pure planning pass shared by dry-run and commit: returns one action
    dict per row. `existing` is mutated (in-memory copies) as rows are
    planned, so duplicates inside the file resolve against earlier rows
    exactly the way the commit pass will meet them in the database."""
    by_email: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, Dict[str, Any]] = {}
    for c in existing:
        em = str(c.get("email") or "").strip().lower()
        if em:
            by_email.setdefault(em, c)
        nm = str(c.get("name") or "").strip().lower()
        if nm:
            by_name.setdefault(nm, c)

    actions: List[Dict[str, Any]] = []
    for row_no, raw in parsed_rows:
        name = (raw.get("name") or "").strip()
        if not name:
            first = (raw.get("first_name") or "").strip()
            last = (raw.get("last_name") or "").strip()
            name = f"{first} {last}".strip()
        email = (raw.get("email") or "").strip()
        phone = (raw.get("phone") or "").strip()
        company = (raw.get("company") or "").strip()
        notes = (raw.get("notes") or "").strip()
        entry: Dict[str, Any] = {"row": row_no, "name": name, "email": email}

        if not name:
            entry.update(action="error", detail="missing customer name")
            actions.append(entry)
            continue
        if email and not EMAIL_RE.match(email):
            entry.update(action="error", detail=f"invalid email: {email}")
            actions.append(entry)
            continue

        match = None
        if email:
            match = by_email.get(email.lower())
        if match is None:
            match = by_name.get(name.lower())

        csv_addr: Dict[str, str] = {}
        if (raw.get("street") or "").strip():
            csv_addr["line1"] = raw["street"].strip()
        if (raw.get("city") or "").strip():
            csv_addr["city"] = raw["city"].strip()
        if (raw.get("state") or "").strip():
            csv_addr["state"] = raw["state"].strip()
        if (raw.get("zip") or "").strip():
            csv_addr["postal_code"] = raw["zip"].strip()
        if (raw.get("country") or "").strip():
            csv_addr["country"] = raw["country"].strip()

        if match is None:
            doc: Dict[str, Any] = {
                "id": _uuid(), "name": name,
                "email": email or None, "phone": phone,
                "notes": notes, "tags": [], "created_at": _now_iso(),
            }
            if company:
                doc["company"] = company
            if csv_addr:
                doc["service_address"] = {**_ADDRESS_DEFAULTS, **csv_addr}
            existing.append(doc)
            if email:
                by_email[email.lower()] = doc
            by_name[name.lower()] = doc
            entry.update(action="create", detail="new customer",
                         doc=doc)
            actions.append(entry)
            continue

        patch: Dict[str, Any] = {}
        if name and str(match.get("name") or "") != name:
            patch["name"] = name
        if email and not str(match.get("email") or "").strip():
            patch["email"] = email  # never overwrite a stored email
        if phone and str(match.get("phone") or "") != phone:
            patch["phone"] = phone
        if company and str(match.get("company") or "") != company:
            patch["company"] = company
        if notes and str(match.get("notes") or "") != notes:
            patch["notes"] = notes
        if csv_addr:
            current_addr = {**_ADDRESS_DEFAULTS,
                            **(match.get("service_address") or {})}
            merged = {**current_addr, **csv_addr}
            if merged != current_addr:
                patch["service_address"] = merged
        if not patch:
            entry.update(action="skip", detail="already up to date",
                         customer_id=match.get("id"))
        else:
            match.update(patch)
            if email:
                by_email.setdefault(email.lower(), match)
            entry.update(action="update",
                         detail="updates: " + ", ".join(sorted(patch.keys())),
                         customer_id=match.get("id"), patch=patch)
        actions.append(entry)
    return actions


@router.post("/import/customers")
async def import_customers(file: UploadFile = File(...),
                           dry_run: bool = Query(True),
                           user: dict = Depends(require_tenant_owner_or_admin)):
    raw_bytes = await file.read()
    if len(raw_bytes) > MAX_IMPORT_BYTES:
        raise HTTPException(400, f"File too large ({len(raw_bytes)} bytes; limit {MAX_IMPORT_BYTES})")
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "File is not valid UTF-8 text — export it as CSV (UTF-8) and retry")
    if not text.strip():
        raise HTTPException(400, "File is empty")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(400, "No header row found — the first row must name the columns")
    header_map: Dict[str, Optional[str]] = {}
    for h in reader.fieldnames:
        if h is None:
            continue
        header_map[h] = _HEADER_ALIASES.get(h.strip().lower())
    known = {v for v in header_map.values() if v}
    if "name" not in known and not ({"first_name", "last_name"} & known):
        accepted = ", ".join(sorted({k for k in _HEADER_ALIASES}))
        raise HTTPException(
            400, "Could not find a customer-name column. Accepted headers include: "
                 "Customer, Display Name, Name, First Name + Last Name. "
                 f"(Full list: {accepted})")

    parsed_rows = []
    for i, row in enumerate(reader):
        row_no = i + 2  # header is spreadsheet row 1
        if row.get(None):
            parsed_rows.append((row_no, {"__error__": "too many columns"}))
            continue
        if all((v is None or str(v).strip() == "") for v in row.values()):
            continue  # skip blank lines silently
        fields: Dict[str, str] = {}
        for raw_header, value in row.items():
            if raw_header is None:
                continue
            field = header_map.get(raw_header)
            if field and value is not None:
                fields[field] = str(value)
        parsed_rows.append((row_no, fields))
    if len(parsed_rows) > MAX_IMPORT_ROWS:
        raise HTTPException(400, f"Too many rows ({len(parsed_rows)}; limit {MAX_IMPORT_ROWS}) — split the file")

    # Rows with a structural error keep their place in the report.
    clean_rows, error_rows = [], {}
    for row_no, fields in parsed_rows:
        if "__error__" in fields:
            error_rows[row_no] = fields["__error__"]
        else:
            clean_rows.append((row_no, fields))

    db = get_db()
    tid = user["tenant_id"]
    existing_docs = await db.customers.find(
        {"tenant_id": tid}, {"_id": 0}).to_list(MAX_EXPORT_ROWS)
    working = [dict(d) for d in existing_docs]
    planned = _plan_customer_import(working, clean_rows)

    rows_out: List[Dict[str, Any]] = []
    summary = {"total": 0, "create": 0, "update": 0, "skip": 0, "error": 0}
    for row_no, _fields in parsed_rows:
        summary["total"] += 1
        if row_no in error_rows:
            summary["error"] += 1
            rows_out.append({"row": row_no, "action": "error", "name": "",
                             "email": "", "detail": error_rows[row_no]})
    by_row = {a["row"]: a for a in planned}
    for row_no, _fields in clean_rows:
        a = by_row[row_no]
        summary[a["action"]] += 1
        rows_out.append({"row": row_no, "action": a["action"],
                         "name": a["name"], "email": a["email"],
                         "detail": a["detail"],
                         **({"customer_id": a["customer_id"]}
                            if a.get("customer_id") else {})})
    rows_out.sort(key=lambda r: r["row"])

    if not dry_run:
        for a in planned:
            if a["action"] == "create":
                doc = dict(a["doc"])
                doc["tenant_id"] = tid
                await db.customers.insert_one(doc)
            elif a["action"] == "update":
                await db.customers.update_one(
                    {"id": a["customer_id"], "tenant_id": tid},
                    {"$set": a["patch"]})
        log.info("csv-bridge customer import tenant=%s summary=%s", tid, summary)

    return {"dry_run": dry_run, "summary": summary, "rows": rows_out}
