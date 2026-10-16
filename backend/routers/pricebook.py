"""Flat-rate pricebook: versioned catalog of services/parts/labor that feeds quotes.

Shops price from the book, not from hourly math. Prices are frozen into quote
line items at draft time (see `_create_estimate_doc`), and every price/cost
change writes a row to `pricebook_versions` so history is auditable. Items are
soft-deleted only — history must survive.

Collections (all tenant-scoped):
- `pricebook_items`: the live catalog row.
- `pricebook_versions`: one row per pricing version of an item.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin
from routers.call_to_payment import _create_estimate_doc

router = APIRouter(prefix="/tenants/pricebook", tags=["pricebook"])

# Suggested categories; the field itself is free-form so trade packs
# (HVAC/plumbing/electrical) can introduce their own later.
SUGGESTED_CATEGORIES = ["service", "part", "labor", "addon"]


# ---------- models ----------

class PricebookOptionIn(BaseModel):
    name: str
    price: float = 0.0  # added to the item's flat price when selected


class PricebookItemIn(BaseModel):
    name: str
    category: str = "service"
    price: float = Field(ge=0)
    cost: float = Field(ge=0, default=0.0)
    sku: Optional[str] = None
    description: str = ""
    options: List[PricebookOptionIn] = Field(default_factory=list)
    active: bool = True


class PricebookItemUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    price: Optional[float] = Field(default=None, ge=0)
    cost: Optional[float] = Field(default=None, ge=0)
    sku: Optional[str] = None
    description: Optional[str] = None
    options: Optional[List[PricebookOptionIn]] = None
    active: Optional[bool] = None


class QuoteSelectionIn(BaseModel):
    item_id: str
    qty: float = 1.0
    option_ids: List[str] = Field(default_factory=list)


class PricebookQuoteDraftIn(BaseModel):
    customer_name: str
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
    title: str
    selections: List[QuoteSelectionIn]
    notes: str = ""
    expires_in_days: int = 14


# ---------- helpers ----------

def _public_item(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "name": doc.get("name", ""),
        "category": doc.get("category", "service"),
        "price": float(doc.get("price") or 0),
        "cost": float(doc.get("cost") or 0),
        "margin": round(float(doc.get("price") or 0) - float(doc.get("cost") or 0), 2),
        "sku": doc.get("sku"),
        "description": doc.get("description", ""),
        "options": doc.get("options") or [],
        "active": bool(doc.get("active", True)),
        "version": int(doc.get("version") or 1),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


async def _get_item(db, item_id: str, tenant_id: str, *, include_inactive: bool = False) -> dict:
    q: dict = {"id": item_id, "tenant_id": tenant_id}
    if not include_inactive:
        q["active"] = True
    item = await db.pricebook_items.find_one(q, {"_id": 0})
    if not item:
        raise HTTPException(404, "Pricebook item not found")
    return item


def _pricing_changed(old: dict, patch: dict) -> bool:
    if "price" in patch and float(patch["price"]) != float(old.get("price") or 0):
        return True
    if "cost" in patch and float(patch["cost"]) != float(old.get("cost") or 0):
        return True
    if "options" in patch:
        old_opts = {(o.get("name"), float(o.get("price") or 0)) for o in (old.get("options") or [])}
        new_opts = {(o.get("name"), float(o.get("price") or 0)) for o in (patch["options"] or [])}
        if old_opts != new_opts:
            return True
    return False


async def _record_version(db, item: dict, user: dict) -> None:
    """Snapshot the item's pricing into the version history."""
    await db.pricebook_versions.insert_one({
        "id": _uuid(),
        "item_id": item["id"],
        "tenant_id": item["tenant_id"],
        "version": int(item.get("version") or 1),
        "snapshot": {
            "name": item.get("name"),
            "price": float(item.get("price") or 0),
            "cost": float(item.get("cost") or 0),
            "options": item.get("options") or [],
        },
        "changed_by": user.get("id"),
        "changed_at": _now_iso(),
    })


def _matches_search(item: dict, q: str) -> bool:
    needle = q.strip().lower()
    if not needle:
        return True
    hay = " ".join([
        str(item.get("name") or ""),
        str(item.get("description") or ""),
        str(item.get("sku") or ""),
        str(item.get("category") or ""),
    ]).lower()
    return needle in hay


# ---------- endpoints ----------

@router.get("/items")
async def list_items(
    user: dict = Depends(require_tenant_user),
    q: str = "",
    category: str = "",
    active_only: bool = True,
):
    """Searchable catalog. `q` matches name/description/SKU/category."""
    db = get_db()
    query: dict = {"tenant_id": user["tenant_id"]}
    if active_only:
        query["active"] = True
    if category:
        query["category"] = category
    rows = await db.pricebook_items.find(query, {"_id": 0}).sort("name", 1).to_list(2000)
    items = [_public_item(r) for r in rows if _matches_search(r, q)]
    return {"items": items, "count": len(items)}


@router.get("/categories")
async def list_categories(user: dict = Depends(require_tenant_user)):
    """Categories actually in use by this tenant's catalog."""
    db = get_db()
    rows = await db.pricebook_items.find({"tenant_id": user["tenant_id"]}, {"_id": 0, "category": 1}).to_list(2000)
    seen = sorted({str(r.get("category") or "service") for r in rows})
    return {"categories": seen, "suggested": SUGGESTED_CATEGORIES}


@router.post("/items")
async def create_item(data: PricebookItemIn, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    now = _now_iso()
    options = [{"id": _uuid(), "name": o.name, "price": float(o.price)} for o in data.options]
    doc = {
        "id": _uuid(),
        "tenant_id": user["tenant_id"],
        "name": data.name,
        "category": data.category or "service",
        "price": float(data.price),
        "cost": float(data.cost),
        "sku": data.sku,
        "description": data.description,
        "options": options,
        "active": bool(data.active),
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    await db.pricebook_items.insert_one(doc)
    await _record_version(db, doc, user)
    return _public_item(doc)


@router.get("/items/{item_id}")
async def get_item(item_id: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    return _public_item(await _get_item(db, item_id, user["tenant_id"], include_inactive=True))


@router.put("/items/{item_id}")
async def update_item(
    item_id: str, data: PricebookItemUpdate, user: dict = Depends(require_tenant_owner_or_admin)
):
    """Update an item. Price/cost/option changes bump the version and are
    recorded in history; already-sent quotes keep the frozen price they went
    out with (prices live on the quote lines, not the catalog row)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    item = await _get_item(db, item_id, tenant_id, include_inactive=True)

    patch: dict = {}
    for field in ("name", "category", "sku", "description", "active"):
        val = getattr(data, field)
        if val is not None:
            patch[field] = val
    for field in ("price", "cost"):
        val = getattr(data, field)
        if val is not None:
            patch[field] = float(val)
    if data.options is not None:
        patch["options"] = [{"id": _uuid(), "name": o.name, "price": float(o.price)} for o in data.options]

    if _pricing_changed(item, patch):
        patch["version"] = int(item.get("version") or 1) + 1
    patch["updated_at"] = _now_iso()

    await db.pricebook_items.update_one({"id": item_id, "tenant_id": tenant_id}, {"$set": patch})
    updated = await db.pricebook_items.find_one({"id": item_id, "tenant_id": tenant_id}, {"_id": 0})
    if _pricing_changed(item, patch):
        await _record_version(db, updated, user)
    return _public_item(updated)


@router.delete("/items/{item_id}")
async def delete_item(item_id: str, user: dict = Depends(require_tenant_owner_or_admin)):
    """Soft delete — the row goes inactive but history survives."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await _get_item(db, item_id, tenant_id, include_inactive=True)
    await db.pricebook_items.update_one(
        {"id": item_id, "tenant_id": tenant_id},
        {"$set": {"active": False, "updated_at": _now_iso()}},
    )
    return {"ok": True, "id": item_id, "active": False}


@router.get("/items/{item_id}/history")
async def item_history(item_id: str, user: dict = Depends(require_tenant_user)):
    """Price/version history, oldest first."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await _get_item(db, item_id, tenant_id, include_inactive=True)
    rows = await db.pricebook_versions.find(
        {"item_id": item_id, "tenant_id": tenant_id}, {"_id": 0}
    ).sort("version", 1).to_list(500)
    return {"item_id": item_id, "versions": rows}


@router.post("/quote-draft")
async def quote_draft_from_pricebook(data: PricebookQuoteDraftIn, user: dict = Depends(require_tenant_user)):
    """Build a draft quote from pricebook items. Prices (item + selected
    options) are frozen into the quote lines at draft time; the quote then
    flows through the normal owner-approval flow in /c2p."""
    if not data.selections:
        raise HTTPException(400, "Select at least one pricebook item")
    db = get_db()
    tenant_id = user["tenant_id"]

    item_ids = [s.item_id for s in data.selections]
    catalog = await db.pricebook_items.find(
        {"tenant_id": tenant_id, "id": {"$in": item_ids}, "active": True}, {"_id": 0}
    ).to_list(500)
    by_id = {c["id"]: c for c in catalog}

    lines: List[dict] = []
    for sel in data.selections:
        item = by_id.get(sel.item_id)
        if not item:
            raise HTTPException(404, f"Pricebook item not found or inactive: {sel.item_id}")
        option_map = {o.get("id"): o for o in (item.get("options") or [])}
        chosen = []
        add_on = 0.0
        for oid in sel.option_ids:
            opt = option_map.get(oid)
            if not opt:
                raise HTTPException(404, f"Option {oid} not found on item {item['id']}")
            chosen.append(opt.get("name", ""))
            add_on += float(opt.get("price") or 0)
        qty = float(sel.qty or 1)
        if qty <= 0:
            raise HTTPException(400, "Quantity must be positive")
        unit_price = round(float(item.get("price") or 0) + add_on, 2)
        desc = item.get("name", "")
        if chosen:
            desc += " (" + ", ".join(chosen) + ")"
        lines.append({
            "description": desc,
            "quantity": qty,
            "unit_price": unit_price,
            "service_id": None,
            "pricebook_item_id": item["id"],
            "pricebook_version": int(item.get("version") or 1),
        })

    return await _create_estimate_doc(
        db,
        tenant_id,
        lead_id=None,
        customer_name=data.customer_name,
        customer_email=data.customer_email,
        customer_phone=data.customer_phone or "",
        title=data.title,
        lines=lines,
        notes=data.notes,
        expires_in_days=data.expires_in_days,
    )
