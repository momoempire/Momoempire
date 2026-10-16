"""AI photo estimates — customer/tech uploads a photo, AI suggests line items.

Flow:
    POST /upload      → validate + store photo (tenant-scoped doc)
    POST /{id}/analyze → vision provider suggests line items; the router maps
                         each suggestion onto the tenant's pricebook by
                         description similarity (unmatched → generic line)
    POST /{id}/approve → human edits/confirms lines → flows into the EXISTING
                         quote-draft pipeline (routers/call_to_payment.
                         _create_estimate_doc, same helper the pricebook
                         quote-draft uses). Nothing here duplicates it.

Prices freeze into quote lines at draft time (pricebook semantics), so a
later catalog change never rewrites a sent quote.

Collections (all tenant-scoped):
- `photo_estimates`: the upload + analysis + approval state; image bytes are
  stored on the doc (10MB cap keeps it inside BSON limits) and are never
  returned by read endpoints.
"""
from __future__ import annotations

import re
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from routers.call_to_payment import _create_estimate_doc
from security import require_tenant_user
from services.vision import SuggestedItem, VisionNotConfigured, resolve_provider

router = APIRouter(prefix="/tenants/photo-estimates", tags=["photo-estimates"])

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10MB — matches knowledge_docs upload cap


# ---------- models ----------

class ApprovedLineIn(BaseModel):
    """One human-confirmed line. A human may adjust description/qty/price,
    or accept the pricebook match (price then frozen from the catalog row)."""
    description: str = Field(min_length=1)
    qty: float = Field(default=1.0, gt=0)
    unit_price: float = Field(default=0.0, ge=0)
    pricebook_item_id: Optional[str] = None


class ApproveIn(BaseModel):
    customer_name: str = Field(min_length=1)
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
    title: str = "Photo estimate"
    lines: List[ApprovedLineIn] = Field(min_length=1)
    notes: str = ""
    expires_in_days: int = 14


# ---------- helpers ----------

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set:
    return set(_TOKEN_RE.findall(str(text or "").lower()))


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def match_pricebook_item(description: str, catalog: List[dict]) -> Optional[dict]:
    """Match a free-text suggestion against active pricebook rows.

    Score = Jaccard similarity between the description's tokens and the
    catalog row's (name + description + sku). Returns the best row at or
    above 0.25, else None. Pure function — unit-tested directly.
    """
    desc_tokens = _tokens(description)
    best = None
    best_score = 0.0
    for row in catalog:
        if not row.get("active", True):
            continue
        hay = " ".join([
            str(row.get("name") or ""),
            str(row.get("description") or ""),
            str(row.get("sku") or ""),
        ])
        score = _jaccard(desc_tokens, _tokens(hay))
        if score > best_score:
            best_score = score
            best = row
    if best is None or best_score < 0.25:
        return None
    return best


def _public_estimate(doc: dict) -> dict:
    """Everything except the raw image bytes (never sent to the client)."""
    return {
        "id": doc.get("id"),
        "filename": doc.get("filename"),
        "content_type": doc.get("content_type"),
        "size_bytes": doc.get("size_bytes"),
        "trade": doc.get("trade"),
        "notes": doc.get("notes", ""),
        "status": doc.get("status"),
        "provider": doc.get("provider"),
        "suggestions": doc.get("suggestions") or [],
        "quote_id": doc.get("quote_id"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


async def _get_estimate(db, estimate_id: str, tenant_id: str) -> dict:
    doc = await db.photo_estimates.find_one(
        {"id": estimate_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(404, "Photo estimate not found")
    return doc


# ---------- endpoints ----------

@router.post("/upload")
async def upload_photo(
    file: UploadFile = File(...),
    notes: str = Form(""),
    trade: str = Form(""),
    user: dict = Depends(require_tenant_user),
):
    """Store a job photo for AI analysis. Validates MIME + size, then returns
    the estimate record in `uploaded` status."""
    db = get_db()
    content_type = (file.content_type or "").lower()
    if not content_type.startswith("image/"):
        raise HTTPException(400, f"Expected an image file, got '{file.content_type}'")
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(400, "Empty image")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(400, "Max 10MB per image")

    doc = {
        "id": _uuid(),
        "tenant_id": user["tenant_id"],
        "filename": file.filename,
        "content_type": content_type,
        "size_bytes": len(image_bytes),
        "image_bytes": image_bytes,
        "notes": notes or "",
        "trade": trade or "",
        "status": "uploaded",
        "provider": None,
        "suggestions": [],
        "quote_id": None,
        "created_by": user.get("id"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    await db.photo_estimates.insert_one(doc)
    return _public_estimate(doc)


@router.get("/{estimate_id}")
async def get_estimate(estimate_id: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    return _public_estimate(await _get_estimate(db, estimate_id, user["tenant_id"]))


@router.post("/{estimate_id}/analyze")
async def analyze_photo(
    estimate_id: str,
    user: dict = Depends(require_tenant_user),
):
    """Run the vision provider, map suggestions onto the pricebook, store them.

    If the resolved live provider isn't configured (TODO(LIVE) skeleton) or
    raises, the mock provider steps in so the flow is always usable in demo.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    doc = await _get_estimate(db, estimate_id, tenant_id)

    catalog = await db.pricebook_items.find(
        {"tenant_id": tenant_id, "active": True}, {"_id": 0}
    ).to_list(2000)

    context = {
        "trade": doc.get("trade"),
        "notes": doc.get("notes"),
        "mime_type": doc.get("content_type"),
        "pricebook_names": [c.get("name") for c in catalog if c.get("name")],
    }

    provider = resolve_provider()
    used_mock_fallback = False
    try:
        suggestions = await provider.suggest_line_items(doc["image_bytes"], context)
    except VisionNotConfigured:
        # Live skeleton with no live implementation yet → deterministic demo.
        used_mock_fallback = True
        suggestions = await resolve_provider("mock").suggest_line_items(
            doc["image_bytes"], context
        )

    mapped: List[dict] = []
    for s in suggestions:
        item = match_pricebook_item(s.description, catalog)
        mapped.append(SuggestedItem(
            description=s.description,
            confidence=s.confidence,
            qty=s.qty,
            suggested_pricebook_item_id=item.get("id") if item else None,
            pricebook_match_name=item.get("name") if item else None,
            mock=s.mock or used_mock_fallback,
        ).as_dict())

    await db.photo_estimates.update_one(
        {"id": estimate_id, "tenant_id": tenant_id},
        {"$set": {
            "status": "analyzed",
            "provider": provider.name if not used_mock_fallback else "mock",
            "mock_fallback": used_mock_fallback,
            "suggestions": mapped,
            "updated_at": _now_iso(),
        }},
    )
    return {"id": estimate_id, "status": "analyzed",
            "provider": provider.name if not used_mock_fallback else "mock",
            "suggestions": mapped}


@router.post("/{estimate_id}/approve")
async def approve_estimate(
    estimate_id: str,
    data: ApproveIn,
    user: dict = Depends(require_tenant_user),
):
    """Human approve/edit step → flows into the existing quote-draft pipeline.

    Each approved line either references a live pricebook row (price frozen
    from the catalog, same semantics as the pricebook quote-draft) or ships
    as a generic line with the human's unit price. Nothing about quote
    creation is reimplemented here — _create_estimate_doc does it all.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    doc = await _get_estimate(db, estimate_id, tenant_id)
    if doc.get("status") == "approved":
        raise HTTPException(409, "Already approved — see quote_id")

    pb_ids = [l.pricebook_item_id for l in data.lines if l.pricebook_item_id]
    catalog: dict = {}
    if pb_ids:
        rows = await db.pricebook_items.find(
            {"tenant_id": tenant_id, "id": {"$in": pb_ids}, "active": True}, {"_id": 0}
        ).to_list(500)
        catalog = {r["id"]: r for r in rows}

    lines: List[dict] = []
    for line in data.lines:
        if line.pricebook_item_id:
            item = catalog.get(line.pricebook_item_id)
            if not item:
                raise HTTPException(
                    404, f"Pricebook item not found or inactive: {line.pricebook_item_id}")
            unit_price = round(float(item.get("price") or 0), 2)
            lines.append({
                "description": line.description or item.get("name", ""),
                "quantity": float(line.qty),
                "unit_price": unit_price,
                "service_id": None,
                "pricebook_item_id": item["id"],
                "pricebook_version": int(item.get("version") or 1),
            })
        else:
            lines.append({
                "description": line.description,
                "quantity": float(line.qty),
                "unit_price": round(float(line.unit_price), 2),
                "service_id": None,
            })

    quote = await _create_estimate_doc(
        db,
        tenant_id,
        lead_id=None,
        customer_name=data.customer_name,
        customer_email=data.customer_email,
        customer_phone=data.customer_phone or "",
        title=data.title,
        lines=lines,
        notes=(data.notes or "") + f" [photo-estimate {estimate_id}]",
        expires_in_days=data.expires_in_days,
    )

    await db.photo_estimates.update_one(
        {"id": estimate_id, "tenant_id": tenant_id},
        {"$set": {
            "status": "approved",
            "quote_id": quote["id"],
            "updated_at": _now_iso(),
        }},
    )
    return {"id": estimate_id, "status": "approved",
            "quote_id": quote["id"], "quote": quote}
