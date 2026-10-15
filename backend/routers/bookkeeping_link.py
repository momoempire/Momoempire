"""Bookkeeping link: sync customers, invoices and payments to the tenant's
OWN bookkeeping app (the user's QuickBooks competitor).

Deliberately no QuickBooks integration — see services/accounting.py.

Routes (all tenant-isolated, mounted under /api by server.py):
    GET/PUT  /tenants/bookkeeping-link/settings        — per-tenant config.
                                                       Writes are owner/admin
                                                       only. The raw API token
                                                       is write-only and is
                                                       never returned or logged.
    POST     /tenants/bookkeeping-link/test-connection — validate the token
                                                       against the bookkeeping
                                                       API (GET /auth/me).
    GET      /tenants/bookkeeping-link/sync-status     — last sync time,
                                                       last error, counts.
    POST     /tenants/bookkeeping-link/invoices/{id}/sync — manual one-shot
                                                       sync of a paid invoice.

Auto-sync: call_to_payment._apply_payment_success calls sync_invoice_paid()
when an invoice flips to paid and the tenant has auto-sync enabled. The
helper is best-effort — it never raises into the payment flow; failures are
logged and recorded on the tenant settings for the owner to see.

Collections: settings live on the tenant doc (`tenants.bookkeeping_link_settings`);
pushed provider ids live on each invoice doc (`invoices.bookkeeping_sync`)
so retries are idempotent: already-pushed steps are skipped.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso
from security import require_tenant_user, require_tenant_owner_or_admin
from services.accounting import (
    AccountingError,
    SyncCustomer,
    SyncInvoice,
    SyncInvoiceLine,
    SyncPayment,
    MindAtEaseProvider,
    idempotency_key,
    DEFAULT_API_BASE_URL,
)
from services.token_vault import (
    EncryptionNotConfigured,
    SecretDecryptionFailed,
    decrypt_secret,
    encrypt_secret,
)

log = logging.getLogger("bookkeeping_link")

router = APIRouter(prefix="/tenants/bookkeeping-link", tags=["bookkeeping-link"])

DEFAULT_SETTINGS: Dict[str, Any] = {
    "enabled": False,
    "api_base_url": DEFAULT_API_BASE_URL,
    "api_token_set": False,
    "auto_sync_on_paid": True,
    "last_sync_at": None,
    "last_error": None,
    "counts": {"customers": 0, "invoices": 0, "payments": 0},
    "last_test_at": None,
    "last_test_ok": None,
}


# ---------- models ----------

class BookkeepingSettingsIn(BaseModel):
    enabled: Optional[bool] = None
    api_base_url: Optional[str] = None
    api_token: Optional[str] = None  # write-only; never echoed back
    clear_token: bool = False
    auto_sync_on_paid: Optional[bool] = None


# ---------- helpers ----------

async def _get_settings(db, tenant_id: str) -> Dict[str, Any]:
    t = await db.tenants.find_one({"id": tenant_id}, {"_id": 0, "bookkeeping_link_settings": 1}) or {}
    settings = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_SETTINGS.items()}
    stored = t.get("bookkeeping_link_settings") or {}
    settings.update(stored)
    # Keep counts well-formed even if an old doc stored something odd.
    counts = {"customers": 0, "invoices": 0, "payments": 0}
    counts.update(settings.get("counts") or {})
    settings["counts"] = counts
    return settings


async def _save_settings(db, tenant_id: str, settings: Dict[str, Any]) -> None:
    await db.tenants.update_one(
        {"id": tenant_id},
        {"$set": {"bookkeeping_link_settings": settings, "updated_at": _now_iso()}},
    )


def _public_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(settings)
    out.pop("api_token_enc", None)  # the raw token NEVER leaves the server
    return out


def _validate_base_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise HTTPException(400, "api_base_url is required")
    low = url.lower()
    if low.startswith("http://"):
        host = low[len("http://"):].split("/")[0].split(":")[0]
        if host not in ("localhost", "127.0.0.1", "::1"):
            raise HTTPException(400, "api_base_url must use https:// (http is only allowed for localhost)")
    elif not low.startswith("https://"):
        raise HTTPException(400, "api_base_url must be an https:// URL")
    return url.rstrip("/")


def _provider_for(settings: Dict[str, Any]) -> MindAtEaseProvider:
    enc = settings.get("api_token_enc")
    if not enc:
        raise HTTPException(400, "No bookkeeping-app API token saved — paste one in settings first")
    try:
        token = decrypt_secret(enc)
    except (EncryptionNotConfigured, SecretDecryptionFailed) as e:
        raise HTTPException(500, str(e))
    return MindAtEaseProvider(api_base_url=settings.get("api_base_url") or DEFAULT_API_BASE_URL,
                              api_token=token)


# ---------- settings ----------

@router.get("/settings")
async def get_settings(user: dict = Depends(require_tenant_user)):
    db = get_db()
    return _public_settings(await _get_settings(db, user["tenant_id"]))


@router.put("/settings")
async def put_settings(body: BookkeepingSettingsIn,
                       user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    settings = await _get_settings(db, user["tenant_id"])
    if body.enabled is not None:
        settings["enabled"] = bool(body.enabled)
    if body.auto_sync_on_paid is not None:
        settings["auto_sync_on_paid"] = bool(body.auto_sync_on_paid)
    if body.api_base_url is not None:
        settings["api_base_url"] = _validate_base_url(body.api_base_url)
    if body.clear_token:
        settings.pop("api_token_enc", None)
        settings["api_token_set"] = False
    elif body.api_token:
        try:
            settings["api_token_enc"] = encrypt_secret(body.api_token.strip())
        except EncryptionNotConfigured as e:
            raise HTTPException(500, str(e))
        settings["api_token_set"] = True
    await _save_settings(db, user["tenant_id"], settings)
    return _public_settings(settings)


@router.post("/test-connection")
async def test_connection(user: dict = Depends(require_tenant_owner_or_admin)):
    """Validate the saved token against the bookkeeping API's GET /auth/me."""
    db = get_db()
    settings = await _get_settings(db, user["tenant_id"])
    provider = _provider_for(settings)  # 400/500 if no token or undecryptable
    now = _now_iso()
    try:
        info = await provider.test_connection()
    except AccountingError as e:
        settings["last_test_at"] = now
        settings["last_test_ok"] = False
        await _save_settings(db, user["tenant_id"], settings)
        return {"ok": False, "detail": str(e)}
    settings["last_test_at"] = now
    settings["last_test_ok"] = True
    settings["last_error"] = None
    await _save_settings(db, user["tenant_id"], settings)
    who = info.get("user_name") or info.get("user_email") or "unknown user"
    shop = info.get("tenant_name") or "unknown shop"
    return {"ok": True, "detail": f"Connected as {who} ({shop})"}


@router.get("/sync-status")
async def sync_status(user: dict = Depends(require_tenant_user)):
    db = get_db()
    s = await _get_settings(db, user["tenant_id"])
    return {
        "enabled": s.get("enabled", False),
        "auto_sync_on_paid": s.get("auto_sync_on_paid", True),
        "api_token_set": s.get("api_token_set", False),
        "last_sync_at": s.get("last_sync_at"),
        "last_error": s.get("last_error"),
        "counts": s.get("counts"),
        "last_test_at": s.get("last_test_at"),
        "last_test_ok": s.get("last_test_ok"),
    }


# ---------- sync engine ----------

def _invoice_to_sync_invoice(inv: dict, customer_provider_id: Optional[str]) -> SyncInvoice:
    lines: List[SyncInvoiceLine] = []
    for ln in inv.get("lines") or []:
        qty = float(ln.get("quantity") or 1)
        unit = float(ln.get("unit_price") or 0)
        lines.append(SyncInvoiceLine(
            name=str(ln.get("description") or "Service"),
            amount_cents=int(round(qty * unit * 100)),
        ))
    if not lines:
        # Fall back to a single line for the invoice total so nothing is lost.
        lines = [SyncInvoiceLine(name=str(inv.get("title") or "Invoice"),
                                 amount_cents=int(inv.get("total_cents") or 0))]
    due = (inv.get("due_at") or "")[:10] or None
    return SyncInvoice(
        customer_name=inv.get("customer_name") or "Customer",
        lines=lines,
        total_cents=int(inv.get("total_cents") or 0),
        title=inv.get("title"),
        customer_provider_id=customer_provider_id,
        due_date=due,
    )


async def sync_invoice_paid(db, tenant_id: str, invoice: dict) -> Dict[str, Any]:
    """Push one paid invoice (customer → invoice → payment) to the tenant's
    own bookkeeping app. Idempotent: steps already recorded on the invoice's
    `bookkeeping_sync` are skipped. Best-effort: NEVER raises — failures are
    logged and recorded on the tenant settings."""
    inv_id = invoice.get("id")
    settings = await _get_settings(db, tenant_id)
    if not settings.get("enabled"):
        return {"synced": False, "reason": "disabled"}
    if not settings.get("auto_sync_on_paid", True):
        return {"synced": False, "reason": "auto_sync_off"}
    if invoice.get("status") != "paid":
        return {"synced": False, "reason": "not_paid"}

    try:
        provider = _provider_for(settings)
    except HTTPException as e:
        return await _record_failure(db, tenant_id, inv_id, f"not configured: {e.detail}")

    bk = dict(invoice.get("bookkeeping_sync") or {})
    steps: List[str] = []
    try:
        if not bk.get("customer_provider_id"):
            cid = await provider.push_customer(
                SyncCustomer(name=invoice.get("customer_name") or "Customer",
                             email=invoice.get("customer_email"),
                             phone=invoice.get("customer_phone")),
                idempotency_key=idempotency_key(tenant_id, inv_id, "customer"))
            bk["customer_provider_id"] = cid
            steps.append("customer")
        if not bk.get("invoice_provider_id"):
            iid = await provider.push_invoice(
                _invoice_to_sync_invoice(invoice, bk.get("customer_provider_id")),
                idempotency_key=idempotency_key(tenant_id, inv_id, "invoice"))
            bk["invoice_provider_id"] = iid
            steps.append("invoice")
        if not bk.get("payment_provider_id"):
            pid = await provider.push_payment(
                SyncPayment(invoice_provider_id=bk["invoice_provider_id"],
                            amount_cents=int(invoice.get("total_cents") or 0),
                            method="card"),
                idempotency_key=idempotency_key(tenant_id, inv_id, "payment"))
            bk["payment_provider_id"] = pid
            steps.append("payment")

        bk["synced_at"] = _now_iso()
        await db.invoices.update_one(
            {"id": inv_id, "tenant_id": tenant_id},
            {"$set": {"bookkeeping_sync": bk, "updated_at": _now_iso()}},
        )
        counts = settings.get("counts") or {}
        step_to_count = {"customer": "customers", "invoice": "invoices", "payment": "payments"}
        for step in steps:
            key = step_to_count[step]
            counts[key] = counts.get(key, 0) + 1
        settings["counts"] = counts
        settings["last_sync_at"] = _now_iso()
        settings["last_error"] = None
        await _save_settings(db, tenant_id, settings)
        return {"synced": True, "steps": steps,
                "customer_provider_id": bk.get("customer_provider_id"),
                "invoice_provider_id": bk.get("invoice_provider_id"),
                "payment_provider_id": bk.get("payment_provider_id")}
    except Exception as e:  # AccountingError and anything unexpected — never propagate
        log.warning("bookkeeping-link sync failed tenant=%s invoice=%s: %s",
                    tenant_id, inv_id, e)
        return await _record_failure(db, tenant_id, inv_id, str(e)[:300])


async def _record_failure(db, tenant_id: str, inv_id: Optional[str], msg: str) -> Dict[str, Any]:
    settings = await _get_settings(db, tenant_id)
    settings["last_error"] = msg
    await _save_settings(db, tenant_id, settings)
    return {"synced": False, "reason": "error", "error": msg}


@router.post("/invoices/{invoice_id}/sync")
async def manual_sync(invoice_id: str, user: dict = Depends(require_tenant_user)):
    """One-shot manual sync of a paid invoice (the UI's 'Sync now' button)."""
    db = get_db()
    inv = await db.invoices.find_one(
        {"id": invoice_id, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not inv:
        raise HTTPException(404, "Invoice not found")
    if inv.get("status") != "paid":
        raise HTTPException(400, "Only paid invoices can be synced")
    return await sync_invoice_paid(db, user["tenant_id"], inv)
