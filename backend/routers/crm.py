"""Estimates + Invoices for the mini CRM."""
import logging

from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
from db import get_db
from models import _now_iso
from models import Estimate, EstimateIn, Invoice, InvoiceIn
from security import require_tenant_user

log = logging.getLogger("crm")

router = APIRouter(prefix="/tenants", tags=["crm"])


def _total(lines):
    return sum(float(l.get("quantity", 1)) * float(l.get("unit_price", 0)) for l in (lines or []))


# ---------- Estimates ----------
@router.get("/estimates")
async def list_estimates(user: dict = Depends(require_tenant_user)):
    db = get_db()
    return await db.estimates.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).sort("created_at", -1).to_list(500)


@router.post("/estimates")
async def create_estimate(data: EstimateIn, user: dict = Depends(require_tenant_user)):
    db = get_db()
    e = Estimate(tenant_id=user["tenant_id"], **data.model_dump())
    doc = e.model_dump()
    doc["total"] = _total(doc.get("lines"))
    await db.estimates.insert_one(dict(doc))
    return doc


@router.put("/estimates/{eid}")
async def update_estimate(eid: str, data: EstimateIn, user: dict = Depends(require_tenant_user)):
    db = get_db()
    patch = data.model_dump()
    patch["total"] = _total(patch.get("lines"))
    patch["updated_at"] = _now_iso()
    res = await db.estimates.update_one({"id": eid, "tenant_id": user["tenant_id"]}, {"$set": patch})
    if res.matched_count == 0:
        raise HTTPException(404, "Not found")
    return await db.estimates.find_one({"id": eid}, {"_id": 0})


@router.post("/estimates/{eid}/send")
async def mark_estimate_sent(eid: str, user: dict = Depends(require_tenant_user)):
    """Send also approves (Brann, 2026-10-11, EMP-W-CF-022): the customer can then accept the
    estimate online at /q/<public_token>. expires_at is left as it is."""
    db = get_db()
    now = _now_iso()
    patch = {"status": "sent", "owner_approved": True, "approved_at": now,
             "approved_by": user.get("id"), "updated_at": now}
    res = await db.estimates.update_one({"id": eid, "tenant_id": user["tenant_id"]}, {"$set": patch})
    if res.matched_count == 0:
        raise HTTPException(404, "Not found")
    return await db.estimates.find_one({"id": eid}, {"_id": 0})


@router.delete("/estimates/{eid}")
async def delete_estimate(eid: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    res = await db.estimates.delete_one({"id": eid, "tenant_id": user["tenant_id"]})
    if res.deleted_count == 0:
        raise HTTPException(404, "Not found")
    return {"status": "ok"}


# ---------- Invoices ----------
@router.get("/invoices")
async def list_invoices(user: dict = Depends(require_tenant_user)):
    db = get_db()
    return await db.invoices.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).sort("created_at", -1).to_list(500)


@router.post("/invoices")
async def create_invoice(data: InvoiceIn, user: dict = Depends(require_tenant_user)):
    db = get_db()
    inv = Invoice(tenant_id=user["tenant_id"], **data.model_dump())
    doc = inv.model_dump()
    doc["total"] = _total(doc.get("lines"))
    await db.invoices.insert_one(dict(doc))
    return doc


@router.post("/invoices/{iid}/mark-paid")
async def mark_invoice_paid(iid: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    res = await db.invoices.update_one(
        {"id": iid, "tenant_id": user["tenant_id"]},
        {"$set": {"status": "paid", "paid_at": datetime.now(timezone.utc).isoformat()}},
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Not found")
    inv = await db.invoices.find_one({"id": iid}, {"_id": 0})
    # Bookkeeping link: a manual mark-paid IS a payment (cash/check) —
    # fire the same books sync the Stripe success handler fires (fix
    # track 2026-10-17; before this, mark-paid invoices never reached
    # books until someone pressed "Sync now"). Best-effort: the helper
    # never raises, and any failure is recorded on the tenant's
    # bookkeeping-link settings, not surfaced as a mark-paid failure.
    try:
        from routers.bookkeeping_link import sync_invoice_paid as _bk_sync
        if inv:
            await _bk_sync(db, user["tenant_id"], inv)
    except Exception as e:  # pragma: no cover - sync must never break CRM
        log.warning("bookkeeping-link mark-paid sync failed: %s", e)
    return inv


@router.delete("/invoices/{iid}")
async def delete_invoice(iid: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    res = await db.invoices.delete_one({"id": iid, "tenant_id": user["tenant_id"]})
    if res.deleted_count == 0:
        raise HTTPException(404, "Not found")
    return {"status": "ok"}
