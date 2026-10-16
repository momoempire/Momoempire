"""Financing at checkout: monthly-payment offers embedded in quotes/invoices.

Flow:
    * Shop config (tenant-scoped): GET/PUT /tenants/financing/settings —
      enable flag, provider choice (demo / wisetack). Writes are owner/admin
      only. The live provider target is Wisetack; shops connect their OWN
      Wisetack merchant accounts later (Stripe Connect model), so no
      platform-level keys exist. Demo is the default and is clearly labeled.
    * Tenant preview / staff quoting: GET /tenants/financing/offers.
    * Customer-facing (public quote/invoice pages, token-authenticated):
      GET /public/financing/offers and POST /public/financing/applications.
      Demo-only in v1; the live path is stubbed in services/financing.py.

Collections: settings live on the tenant doc (`tenants.financing_settings`);
applications are stored tenant-scoped in `financing_applications`.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin
from services.financing import (
    FinancingError,
    FinancingNotConfigured,
    FinancingOffer,
    WisetackProvider,
    get_provider,
)

router = APIRouter(prefix="/tenants/financing", tags=["financing"])
public_router = APIRouter(prefix="/public/financing", tags=["financing-public"])

DEFAULT_SETTINGS = {"enabled": True, "provider": "demo", "merchant_id": None}


# ---------- models ----------

class FinancingSettingsIn(BaseModel):
    enabled: bool = True
    provider: Literal["demo", "wisetack"] = "demo"
    merchant_id: Optional[str] = None  # shop's own Wisetack merchant account id
    # NOTE: no api_key field in v1. Wisetack API keys are per-shop secrets and
    # must land in encrypted vault storage (TODO(LIVE)) before live mode —
    # never as a plaintext DB field. The Settings UI shows the field disabled.


class ApplicationIn(BaseModel):
    amount_cents: int = Field(gt=0)
    term_months: int = Field(gt=0, le=84)
    customer_name: str = Field(min_length=1)
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
    quote_id: Optional[str] = None
    invoice_id: Optional[str] = None


class PublicApplicationIn(BaseModel):
    token: str = Field(min_length=1)  # public quote/invoice token (auth)
    term_months: int = Field(gt=0, le=84)
    customer_name: str = Field(min_length=1)
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None


# ---------- helpers ----------

async def _get_settings(db, tenant_id: str) -> dict:
    t = await db.tenants.find_one({"id": tenant_id}, {"_id": 0, "financing_settings": 1}) or {}
    settings = dict(DEFAULT_SETTINGS)
    settings.update(t.get("financing_settings") or {})
    return settings


def _is_live_ready(settings: dict) -> bool:
    """Wisetack is live-ready only when the shop connected its own merchant
    account AND an API key exists in encrypted storage (TODO(LIVE) — v1 has
    neither, so this is False and the demo provider is used)."""
    return bool(settings.get("provider") == "wisetack"
                and settings.get("merchant_id")
                and settings.get("wisetack_api_key_set"))


def _effective_provider(settings: dict):
    if settings.get("provider") == "wisetack" and _is_live_ready(settings):
        return get_provider("wisetack", merchant_id=settings.get("merchant_id"))
    # Default (and v1's only working mode): demo math, clearly labeled.
    return get_provider("demo")


def _offers_payload(offers: List[FinancingOffer], settings: dict) -> dict:
    provider = _effective_provider(settings)
    items = [o.as_dict() for o in offers]
    return {
        "enabled": bool(settings.get("enabled")),
        "provider": provider.name,
        "demo": provider.name == "demo",
        "demo_fallback": settings.get("provider") == "wisetack" and not _is_live_ready(settings),
        "offers": items,
        "as_low_as_cents": min((o.monthly_payment_cents for o in offers), default=None),
        "min_amount_cents": 1_000,
        "max_amount_cents": 2_500_000,
    }


async def _resolve_token(db, token: str) -> tuple[dict, str, int]:
    """Resolve a public quote/invoice token → (doc, tenant_id, financeable amount in cents)."""
    q = await db.estimates.find_one({"public_token": token}, {"_id": 0})
    if q and q.get("status") != "draft":
        return q, q["tenant_id"], int(round(float(q.get("total") or 0) * 100))
    inv = await db.invoices.find_one({"public_token": token}, {"_id": 0})
    if inv:
        due = max(0, int(inv.get("total_cents") or 0) - int(inv.get("amount_paid_cents") or 0))
        return inv, inv["tenant_id"], due
    raise HTTPException(404, "Not found")


async def _start_application(db, tenant_id: str, *, amount_cents: int, term_months: int,
                             customer: dict, quote_id: Optional[str],
                             invoice_id: Optional[str]) -> dict:
    settings = await _get_settings(db, tenant_id)
    if not settings.get("enabled"):
        raise HTTPException(400, "Financing is not enabled for this business")
    provider = _effective_provider(settings)
    if isinstance(provider, WisetackProvider):
        # Stubbed until TODO(LIVE) lands in services/financing.py.
        raise HTTPException(501, "Live Wisetack applications are not connected yet")
    try:
        result = await provider.create_application(
            amount_cents=amount_cents, term_months=term_months,
            customer=customer, quote_id=quote_id, invoice_id=invoice_id)
    except FinancingError as e:
        raise HTTPException(400, str(e))
    now = _now_iso()
    doc = {
        "id": _uuid(), "tenant_id": tenant_id,
        "provider_application_id": result.get("application_id"),
        "provider": provider.name, "demo": True,
        "status": result.get("status", "demo_pending"),
        "amount_cents": amount_cents, "term_months": term_months,
        "customer_name": customer.get("name", ""),
        "customer_email": customer.get("email"),
        "customer_phone": customer.get("phone"),
        "quote_id": quote_id, "invoice_id": invoice_id,
        "created_at": now, "updated_at": now,
    }
    await db.financing_applications.insert_one(doc)
    doc.pop("_id", None)
    return doc


# ---------- settings (tenant) ----------

@router.get("/settings")
async def get_settings(user: dict = Depends(require_tenant_user)):
    db = get_db()
    settings = await _get_settings(db, user["tenant_id"])
    return {**settings, "live_ready": _is_live_ready(settings)}


@router.put("/settings")
async def put_settings(data: FinancingSettingsIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Owner/admin only. Chooses the financing provider for this shop."""
    db = get_db()
    settings = {
        "enabled": bool(data.enabled),
        "provider": data.provider,
        "merchant_id": (data.merchant_id or "").strip() or None,
        "updated_at": _now_iso(),
    }
    res = await db.tenants.update_one(
        {"id": user["tenant_id"]}, {"$set": {"financing_settings": settings}})
    if res.matched_count == 0:
        raise HTTPException(404, "Tenant not found")
    return {**settings, "live_ready": _is_live_ready(settings)}


# ---------- offers (tenant) ----------

@router.get("/offers")
async def get_offers(
    amount_cents: int = Query(gt=0),
    user: dict = Depends(require_tenant_user),
):
    """Monthly-payment options for an amount — used by staff while quoting."""
    db = get_db()
    settings = await _get_settings(db, user["tenant_id"])
    if not settings.get("enabled"):
        raise HTTPException(400, "Financing is not enabled for this business")
    provider = _effective_provider(settings)
    try:
        offers = provider.get_offers(amount_cents)
    except FinancingNotConfigured as e:
        raise HTTPException(503, str(e))
    except FinancingError as e:
        raise HTTPException(400, str(e))
    return _offers_payload(offers, settings)


# ---------- applications (tenant, staff-assisted) ----------

@router.post("/applications", status_code=201)
async def create_application(data: ApplicationIn, user: dict = Depends(require_tenant_user)):
    db = get_db()
    return await _start_application(
        db, user["tenant_id"],
        amount_cents=data.amount_cents, term_months=data.term_months,
        customer={"name": data.customer_name, "email": data.customer_email,
                  "phone": data.customer_phone},
        quote_id=data.quote_id, invoice_id=data.invoice_id)


@router.get("/applications")
async def list_applications(user: dict = Depends(require_tenant_user)):
    db = get_db()
    rows = await db.financing_applications.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}
    ).sort("created_at", -1).to_list(200)
    return {"applications": rows, "count": len(rows)}


# ---------- public: offers + applications via quote/invoice token ----------

@public_router.get("/offers")
async def public_offers(token: str = Query(min_length=1)):
    """Customer-facing: monthly-payment options for a quote or invoice.
    Auth is the public token itself (same as the quote/invoice page)."""
    db = get_db()
    doc, tenant_id, amount_cents = await _resolve_token(db, token)
    settings = await _get_settings(db, tenant_id)
    if not settings.get("enabled"):
        raise HTTPException(404, "Financing is not available for this quote")
    if amount_cents <= 0:
        raise HTTPException(400, "Nothing left to finance on this quote")
    provider = _effective_provider(settings)
    try:
        offers = provider.get_offers(amount_cents)
    except FinancingNotConfigured as e:
        raise HTTPException(503, str(e))
    except FinancingError as e:
        raise HTTPException(400, str(e))
    payload = _offers_payload(offers, settings)
    return payload


@public_router.post("/applications", status_code=201)
async def public_application(data: PublicApplicationIn):
    """Customer starts a demo financing application from a quote/invoice page.
    Demo-only in v1 — no credit check, no lender contacted."""
    db = get_db()
    doc, tenant_id, amount_cents = await _resolve_token(db, data.token)
    if amount_cents <= 0:
        raise HTTPException(400, "Nothing left to finance on this quote")
    is_invoice = "total_cents" in doc
    return await _start_application(
        db, tenant_id,
        amount_cents=amount_cents, term_months=data.term_months,
        customer={"name": data.customer_name, "email": data.customer_email,
                  "phone": data.customer_phone},
        quote_id=None if is_invoice else doc.get("id"),
        invoice_id=doc.get("id") if is_invoice else None)
