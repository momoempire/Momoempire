"""AI onboarding: upload old invoices/customers -> preview -> one-click import.

A new shop uploads a CSV (or XLSX when openpyxl is installed) of old invoices or
a customer list. The platform parses it, infers the column mapping, and returns a
PREVIEW — detected customers, line items grouped into pricebook candidates — with
nothing written to business collections. The shop confirms (optionally edits the
mapping) and POST /import creates customer records + pricebook items by reusing
the real creation functions from the customers (routers.tenants) and pricebook
(routers.pricebook) routers. Imports are idempotent via ``onboarding_batch_id``.

Telephony auto-provisioning lives here too: POST /telephony/provision uses the
provider-abstracted layer in services/telephony.py (mock by default, deterministic;
TwilioSubaccountProvider is a TODO(LIVE) skeleton) and persists one `telephony`
doc per tenant recording provider, subaccount SID, numbers, mode (provisioned |
ported | both), and the bundled fee model (telephony included in the flat
platform fee; usage recorded, no charging logic).
"""
from __future__ import annotations

import csv
import io
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_owner_or_admin
from services.telephony import FEE_MODEL, MODES, get_telephony_provider

router = APIRouter(prefix="/tenants/onboarding", tags=["onboarding"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_ROWS = 2000


# ---------- column-mapping inference ----------

_FIELD_SYNONYMS: Dict[str, List[str]] = {
    "customer_name": [
        "customer", "customer name", "customername", "client", "client name",
        "name", "bill to", "bill_to", "full name", "contact name",
    ],
    "email": ["email", "email address", "e-mail", "emailaddress"],
    "phone": ["phone", "phone number", "telephone", "mobile", "cell", "contact phone"],
    "address": [
        "address", "street", "street address", "service address", "service_address",
        "location", "addr",
    ],
    "item": [
        "item", "description", "service", "service name", "line item", "line_item",
        "product", "work performed", "job description", "jobdescription",
    ],
    "price": [
        "price", "amount", "unit price", "unit_price", "total", "line total",
        "line_total", "charge", "extended", "extended price",
    ],
    "quantity": ["qty", "quantity", "hours"],
    "date": ["date", "invoice date", "invoice_date", "service date", "service_date"],
    "invoice_id": ["invoice", "invoice #", "invoice id", "invoice_id", "inv #", "inv"],
}


def _norm_header(h: str) -> str:
    return (h or "").strip().lower().replace("_", " ").replace("\ufeff", "")


def infer_column_mapping(headers: List[str]) -> Dict[str, Any]:
    """Map known fields -> the raw CSV header that best matches.

    Returns {"mapping": {field: header}, "unmapped_columns": [header, ...]}.
    """
    mapping: Dict[str, str] = {}
    used: set = set()
    for field, synonyms in _FIELD_SYNONYMS.items():
        for h in headers:
            if h in used:
                continue
            if _norm_header(h) in synonyms or _norm_header(h) == field.replace("_", " "):
                mapping[field] = h
                used.add(h)
                break
    unmapped = [h for h in headers if h not in used]
    return {"mapping": mapping, "unmapped_columns": unmapped}


def _parse_price(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    s = str(raw).strip().replace("$", "").replace(",", "")
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v >= 0 else None


def _clean_email(raw: Any) -> Optional[str]:
    s = (str(raw or "")).strip()
    return s if "@" in s else None


# ---------- parsing ----------

async def _read_rows(file: UploadFile) -> List[Dict[str, str]]:
    filename = (file.filename or "").lower()
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (max 5 MB)")
    if filename.endswith((".xlsx", ".xls")):
        try:
            import openpyxl  # type: ignore
        except ImportError:
            raise HTTPException(
                422,
                "XLSX support is not installed (openpyxl missing); "
                "upload a CSV file instead.",
            )
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        ws = wb.active
        rows_iter = list(ws.iter_rows(values_only=True))
        if not rows_iter:
            raise HTTPException(422, "Spreadsheet is empty")
        headers = [str(c).strip() if c is not None else "" for c in rows_iter[0]]
        out = []
        for r in rows_iter[1:MAX_ROWS + 1]:
            out.append({h: ("" if v is None else str(v)) for h, v in zip(headers, r)})
        return out
    # CSV path (default)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(422, "Could not decode file as UTF-8 CSV")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(422, "No header row found in CSV")
    headers = [str(h).strip().replace("\ufeff", "") for h in reader.fieldnames]
    out = []
    for i, row in enumerate(reader):
        if i >= MAX_ROWS:
            break
        out.append({headers[k]: (row.get(orig) or "") for k, orig in enumerate(reader.fieldnames)})
    return out


def _detect_file_type(mapping: Dict[str, str]) -> str:
    return "invoices" if "item" in mapping or "price" in mapping else "customers"


def _build_preview(rows: List[Dict[str, str]], mapping: Dict[str, str]) -> Dict[str, Any]:
    file_type = _detect_file_type(mapping)
    customers: Dict[str, Dict[str, Any]] = {}
    items: Dict[str, Dict[str, Any]] = {}
    mh = mapping  # field -> raw header
    for row in rows:
        name = (row.get(mh.get("customer_name", ""), "") or "").strip()
        if name:
            key = name.lower()
            c = customers.setdefault(key, {
                "name": name,
                "email": _clean_email(row.get(mh.get("email", ""), "")),
                "phone": (row.get(mh.get("phone", ""), "") or "").strip(),
                "address": (row.get(mh.get("address", ""), "") or "").strip(),
                "invoice_count": 0,
                "total_spent": 0.0,
            })
            c["invoice_count"] += 1
            p = _parse_price(row.get(mh.get("price", ""), ""))
            if p is not None:
                c["total_spent"] = round(c["total_spent"] + p, 2)
        desc = (row.get(mh.get("item", ""), "") or "").strip()
        if desc:
            key = desc.lower()
            it = items.setdefault(key, {
                "name": desc, "prices": [], "occurrences": 0,
            })
            it["occurrences"] += 1
            p = _parse_price(row.get(mh.get("price", ""), ""))
            if p is not None:
                it["prices"].append(p)
    pricebook_candidates = []
    for it in items.values():
        prices = it.pop("prices")
        suggested = round(sum(prices) / len(prices), 2) if prices else 0.0
        pricebook_candidates.append({
            "name": it["name"],
            "suggested_price": suggested,
            "occurrences": it["occurrences"],
            "min_price": min(prices) if prices else None,
            "max_price": max(prices) if prices else None,
        })
    pricebook_candidates.sort(key=lambda c: c["occurrences"], reverse=True)
    return {
        "file_type": file_type,
        "row_count": len(rows),
        "customers": sorted(customers.values(), key=lambda c: c["invoice_count"], reverse=True),
        "pricebook_candidates": pricebook_candidates,
    }


# ---------- endpoints ----------

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user: dict = Depends(require_tenant_owner_or_admin),
):
    """Parse an uploaded CSV/XLSX and return a PREVIEW. Writes nothing to
    business collections (only a server-side draft for the wizard to confirm)."""
    rows = await _read_rows(file)
    if not rows:
        raise HTTPException(422, "No data rows found in file")
    headers = list(rows[0].keys())
    inferred = infer_column_mapping(headers)
    preview = _build_preview(rows, inferred["mapping"])
    upload_id = _uuid()
    db = get_db()
    await db.onboarding_uploads.insert_one({
        "id": upload_id,
        "tenant_id": user["tenant_id"],
        "created_by": user.get("id"),
        "filename": file.filename,
        "rows": rows,
        "column_mapping": inferred["mapping"],
        "preview": preview,
        "created_at": _now_iso(),
    })
    return {
        "upload_id": upload_id,
        "file_type": preview["file_type"],
        "row_count": preview["row_count"],
        "column_mapping": inferred["mapping"],
        "unmapped_columns": inferred["unmapped_columns"],
        "customers": preview["customers"],
        "pricebook_candidates": preview["pricebook_candidates"],
    }


class ImportIn(BaseModel):
    upload_id: str
    onboarding_batch_id: str = Field(min_length=1)
    mapping: Optional[Dict[str, str]] = None  # field -> CSV header, overrides inference
    create_customers: bool = True
    create_pricebook_items: bool = True


@router.post("/import")
async def import_confirmed(data: ImportIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Create customer + pricebook records from a confirmed preview.

    Idempotent via ``onboarding_batch_id``: a retry with the same batch id
    returns the original summary without creating duplicates.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    existing = await db.onboarding_batches.find_one(
        {"tenant_id": tenant_id, "batch_id": data.onboarding_batch_id}, {"_id": 0}
    )
    if existing:
        return {**existing, "idempotent_replay": True}

    draft = await db.onboarding_uploads.find_one(
        {"tenant_id": tenant_id, "id": data.upload_id}, {"_id": 0}
    )
    if not draft:
        raise HTTPException(404, "Upload draft not found (re-upload the file)")

    # Local import so the heavy routers load only on this path.
    from routers.tenants import CustomerIn, create_customer
    from routers.pricebook import PricebookItemIn, create_item

    mapping = dict(draft["column_mapping"] or {})
    for field, header in (data.mapping or {}).items():
        if field in _FIELD_SYNONYMS and header:
            mapping[field] = header
    preview = _build_preview(draft["rows"], mapping)

    customers_created, customers_skipped = [], []
    if data.create_customers:
        for c in preview["customers"]:
            dupe = await db.customers.find_one(
                {"tenant_id": tenant_id, "name": c["name"]}, {"_id": 0}
            )
            if dupe:
                customers_skipped.append({"name": c["name"], "reason": "already exists"})
                continue
            created = await create_customer(
                CustomerIn(name=c["name"], email=c["email"], phone=c["phone"] or ""),
                user,
            )
            customers_created.append({"id": created["id"], "name": created["name"]})

    items_created, items_skipped = [], []
    if data.create_pricebook_items:
        for cand in preview["pricebook_candidates"]:
            dupe = await db.pricebook_items.find_one(
                {"tenant_id": tenant_id, "name": cand["name"]}, {"_id": 0}
            )
            if dupe:
                items_skipped.append({"name": cand["name"], "reason": "already exists"})
                continue
            created = await create_item(
                PricebookItemIn(
                    name=cand["name"],
                    category="service",
                    price=cand["suggested_price"] or 0.0,
                    cost=0.0,
                    description=f"Imported via AI onboarding ({cand['occurrences']} occurrences)",
                ),
                user,
            )
            items_created.append({"id": created["id"], "name": created["name"]})

    summary = {
        "tenant_id": tenant_id,
        "batch_id": data.onboarding_batch_id,
        "upload_id": data.upload_id,
        "status": "complete",
        "customers_created": customers_created,
        "customers_skipped": customers_skipped,
        "pricebook_items_created": items_created,
        "pricebook_items_skipped": items_skipped,
        "idempotent_replay": False,
        "created_at": _now_iso(),
    }
    await db.onboarding_batches.insert_one({**summary, "id": _uuid()})
    return {k: v for k, v in summary.items() if k != "id"}


class TelephonyProvisionIn(BaseModel):
    mode: str = Field(default="both")  # provisioned | ported | both
    area_code: Optional[str] = None
    port_numbers: List[str] = Field(default_factory=list)
    count: int = Field(default=1, ge=1, le=5)


@router.post("/telephony/provision")
async def provision_telephony(data: TelephonyProvisionIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Provision the business's telephony (subaccount + number(s)).

    Deterministic + idempotent per tenant: re-provisioning returns the existing
    record. Records the bundled fee model (telephony included in the flat
    platform fee; usage allowance tracked, no charging logic).
    """
    if data.mode not in MODES:
        raise HTTPException(422, f"mode must be one of {MODES}")
    db = get_db()
    tenant_id = user["tenant_id"]
    existing = await db.telephony.find_one({"tenant_id": tenant_id}, {"_id": 0})
    if existing:
        return {**existing, "idempotent_replay": True}

    provider = get_telephony_provider()
    try:
        result = await provider.provision(
            tenant_id=tenant_id,
            tenant_name=user.get("tenant_name") or tenant_id,
            mode=data.mode,
            area_code=data.area_code,
            port_numbers=data.port_numbers,
            count=data.count,
        )
    except NotImplementedError as e:
        raise HTTPException(501, str(e))

    doc = {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "provider": result["provider"],
        "subaccount_sid": result["subaccount_sid"],
        "numbers": result["numbers"],
        "mode": result["mode"],
        "fee_model": {**FEE_MODEL, "usage": {"sms_segments": 0, "voice_minutes": 0}},
        "created_by": user.get("id"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    await db.telephony.insert_one(doc)
    return {k: v for k, v in doc.items() if k not in ("id", "_id")}


@router.get("/telephony")
async def get_telephony(user: dict = Depends(require_tenant_owner_or_admin)):
    """Read the tenant's provisioned telephony record (if any)."""
    db = get_db()
    doc = await db.telephony.find_one({"tenant_id": user["tenant_id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Telephony not provisioned yet")
    return doc
