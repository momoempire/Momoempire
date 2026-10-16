"""Parts ordering from the job: pick parts for a job, check truck stock, order the rest.

Flow:
  1. POST /tenants/jobs/{job_id}/parts  -- tech lists parts a job needs; the
     system auto-checks truck-stock collections (routers/inventory.py) and
     flags each line as covered / partial / needs_order.
  2. GET  /tenants/jobs/{job_id}/parts  -- job parts with fulfillment status.
  3. POST /tenants/orders               -- create a supplier purchase order
     from the unfulfilled (to_order) job parts; status starts as draft.
  4. POST /tenants/orders/{id}/send     -- mark sent (stub supplier API call;
     clearly marked for real distributor integration later).
  5. POST /tenants/orders/{id}/receive  -- mark received (parts landed).
  6. GET  /tenants/orders               -- purchase-order inbox with status filter.

Collections (all tenant-scoped):
- `job_parts`: per-job part needs {id, tenant_id, job_id, name, sku, qty,
  supplier_id, supplier_ref, unit_cost_estimate, truck_available,
  fulfilled_qty, to_order_qty, fulfillment_status, created_by, created_at}.
- `purchase_orders`: supplier POs {id, tenant_id, supplier_id, supplier_name,
  status (draft|sent|received), lines [{job_part_id, job_id, name, sku,
  qty, unit_cost}], sent_at, received_at, stub_transmission, created_at}.

Supplier catalog data is read from the supplier_pricing module's collections
(`supplier_catalogs`, `supplier_feed_lines`) — see routers/supplier_pricing.py.
No import of that router is needed; we read its collections directly (defensive:
if they do not exist yet, everything still works with zero unit costs).

TODO(LIVE): the send endpoint's _transmit_to_supplier() is a stub. A real
distributor integration (EDI 850 / distributor REST API, modeled on the
supplier-pricing feed placeholder) plugs in there per supplier feed_type.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user

router = APIRouter(prefix="/parts-orders", tags=["parts-ordering"])

JOB_PARTS = "job_parts"
PURCHASE_ORDERS = "purchase_orders"

# Inventory collections owned by routers/inventory.py (read-only here).
INV_PARTS = "inventory_parts"
INV_STOCK = "inventory_stock"
INV_LOCATIONS = "inventory_locations"

# Supplier collections owned by routers/supplier_pricing.py (read-only here).
SUP_CATALOGS = "supplier_catalogs"
SUP_FEED_LINES = "supplier_feed_lines"

FULFILLMENT_STATUSES = ("covered", "partial", "needs_order")
ORDER_STATUSES = ("draft", "sent", "received")


# ---------- pydantic models ----------

class JobPartIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sku: Optional[str] = Field(default=None, max_length=60)
    qty: int = Field(ge=1, le=10000)
    supplier_ref: Optional[str] = Field(default=None, max_length=120)
    supplier_id: Optional[str] = Field(default=None, max_length=60)
    notes: Optional[str] = Field(default=None, max_length=500)


class PartsAddIn(BaseModel):
    parts: List[JobPartIn] = Field(min_length=1, max_length=200)


class OrderLineIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    sku: Optional[str] = Field(default=None, max_length=60)
    qty: int = Field(ge=1, le=10000)
    unit_cost: Optional[float] = Field(default=None, ge=0)
    job_part_id: Optional[str] = None
    job_id: Optional[str] = None


class OrderCreateIn(BaseModel):
    supplier_id: Optional[str] = None
    supplier_name: Optional[str] = None
    lines: Optional[List[OrderLineIn]] = None
    part_ids: Optional[List[str]] = None  # job_parts ids to source lines from
    job_ids: Optional[List[str]] = None   # pull ALL unfulfilled parts for jobs


# ---------- helpers ----------

def _doc(d: dict) -> dict:
    d.pop("_id", None)
    return d


def _clean(sku: Optional[str]) -> Optional[str]:
    if not sku:
        return None
    s = sku.strip()
    return s or None


async def _truck_stock_by_sku(db, tenant_id: str) -> Dict[str, int]:
    """Map normalized sku -> total on-truck qty across all truck locations."""
    try:
        locs = await getattr(db, INV_LOCATIONS).find(
            {"tenant_id": tenant_id, "active": {"$ne": False}}, {"_id": 0}
        ).to_list(1000)
    except Exception:
        return {}
    truck_ids = {l.get("id") for l in locs if l.get("type") == "truck" and l.get("id")}
    if not truck_ids:
        return {}
    try:
        parts = await getattr(db, INV_PARTS).find({"tenant_id": tenant_id}, {"_id": 0}).to_list(10000)
        stock = await getattr(db, INV_STOCK).find({"tenant_id": tenant_id}, {"_id": 0}).to_list(20000)
    except Exception:
        return {}
    sku_to_part_ids: Dict[str, set] = {}
    for p in parts:
        sku = _clean(p.get("sku"))
        if sku:
            sku_to_part_ids.setdefault(sku.lower(), set()).add(p.get("id"))
    part_to_truck_qty: Dict[str, int] = {}
    for s in stock:
        if s.get("location_id") in truck_ids:
            part_to_truck_qty[s.get("part_id")] = (
                part_to_truck_qty.get(s.get("part_id"), 0) + int(s.get("qty") or 0)
            )
    out: Dict[str, int] = {}
    for sku, pids in sku_to_part_ids.items():
        out[sku] = sum(part_to_truck_qty.get(pid, 0) for pid in pids)
    return out


async def _supplier_cost_map(db, tenant_id: str, supplier_id: Optional[str]) -> Dict[str, float]:
    """Map normalized sku -> supplier unit cost from the latest cost feed."""
    if not supplier_id:
        return {}
    try:
        lines = await getattr(db, SUP_FEED_LINES).find(
            {"tenant_id": tenant_id, "supplier_id": supplier_id}, {"_id": 0}
        ).to_list(10000)
    except Exception:
        return {}
    return {str(l.get("sku", "")).strip().lower(): float(l.get("cost") or 0.0)
            for l in lines if l.get("sku")}


def _fulfillment(needed: int, truck_qty: int) -> dict:
    if truck_qty >= needed:
        status = "covered"
    elif truck_qty > 0:
        status = "partial"
    else:
        status = "needs_order"
    return {
        "truck_available": truck_qty,
        "fulfilled_qty": min(needed, truck_qty),
        "to_order_qty": max(0, needed - truck_qty),
        "fulfillment_status": status,
    }


async def _get_job_part(db, tenant_id: str, part_id: str) -> dict:
    part = await getattr(db, JOB_PARTS).find_one({"id": part_id, "tenant_id": tenant_id}, {"_id": 0})
    if not part:
        raise HTTPException(404, "Job part not found")
    return part


async def _get_order(db, tenant_id: str, order_id: str) -> dict:
    order = await getattr(db, PURCHASE_ORDERS).find_one(
        {"id": order_id, "tenant_id": tenant_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Purchase order not found")
    return order


async def _resolve_supplier(db, tenant_id: str, supplier_id: Optional[str],
                            supplier_name: Optional[str]) -> dict:
    """Resolve a supplier; falls back to a local ref when no catalog exists."""
    if supplier_id:
        try:
            sup = await getattr(db, SUP_CATALOGS).find_one(
                {"id": supplier_id, "tenant_id": tenant_id}, {"_id": 0})
        except Exception:
            sup = None
        if not sup:
            raise HTTPException(404, "Supplier not found")
        return {"supplier_id": sup["id"], "supplier_name": sup.get("name")}
    if supplier_name and supplier_name.strip():
        return {"supplier_id": None, "supplier_name": supplier_name.strip()}
    raise HTTPException(422, "supplier_id or supplier_name is required")


# TODO(LIVE): real distributor transmission.
# Currently a clearly-marked stub. When a supplier has a live feed_type
# (EDI 850 / distributor REST API -- see supplier_pricing.py placeholders),
# dispatch per-supplier here instead of recording a stub transmission.
def _transmit_to_supplier(order: dict) -> dict:
    return {
        "channel": "stub",
        "ok": True,
        "note": (
            "TODO(LIVE): no real supplier integration yet. Wire EDI 850 / "
            "distributor REST transmission here per supplier feed_type."
        ),
        "transmitted_at": _now_iso(),
        "line_count": len(order.get("lines", [])),
    }


# ---------- job parts ----------

@router.post("/tenants/jobs/{job_id}/parts", status_code=201)
async def add_job_parts(job_id: str, data: PartsAddIn,
                        user: dict = Depends(require_tenant_user)):
    """Add parts needed for a job; auto-checks truck stock and flags coverage."""
    tenant_id = user["tenant_id"]
    db = get_db()
    truck_stock = await _truck_stock_by_sku(db, tenant_id)
    cost_cache: Dict[str, float] = {}
    created = []
    for part in data.parts:
        sku = _clean(part.sku)
        truck_qty = truck_stock.get(sku.lower(), 0) if sku else 0
        fulfill = _fulfillment(part.qty, truck_qty)
        cost_map = cost_cache.get(part.supplier_id or "")
        if cost_map is None and part.supplier_id:
            cost_map = await _supplier_cost_map(db, tenant_id, part.supplier_id)
            cost_cache[part.supplier_id] = cost_map
        unit_cost = (cost_map or {}).get(sku.lower()) if sku else None
        doc = {
            "id": _uuid(),
            "tenant_id": tenant_id,
            "job_id": job_id,
            "name": part.name.strip(),
            "sku": sku,
            "qty": part.qty,
            "supplier_id": part.supplier_id,
            "supplier_ref": (part.supplier_ref or "").strip() or None,
            "notes": (part.notes or "").strip() or None,
            "unit_cost_estimate": unit_cost,
            "created_by": user.get("id"),
            "created_at": _now_iso(),
            **fulfill,
        }
        await getattr(db, JOB_PARTS).insert_one(doc)
        created.append(_doc(dict(doc)))
    return {"job_id": job_id, "parts": created}


@router.get("/tenants/jobs/{job_id}/parts")
async def list_job_parts(job_id: str, user: dict = Depends(require_tenant_user)):
    """Parts list for a job with fulfillment status."""
    db = get_db()
    rows = await getattr(db, JOB_PARTS).find(
        {"tenant_id": user["tenant_id"], "job_id": job_id}, {"_id": 0}
    ).to_list(1000)
    rows = [_doc(dict(r)) for r in rows]
    covered = sum(1 for r in rows if r.get("fulfillment_status") == "covered")
    to_order = sum(r.get("to_order_qty") or 0 for r in rows)
    return {
        "job_id": job_id,
        "parts": rows,
        "summary": {
            "total_parts": len(rows),
            "covered_parts": covered,
            "lines_to_order": sum(1 for r in rows if (r.get("to_order_qty") or 0) > 0),
            "units_to_order": to_order,
        },
    }


# ---------- purchase orders ----------

@router.post("/tenants/orders", status_code=201)
async def create_order(data: OrderCreateIn, user: dict = Depends(require_tenant_user)):
    """Create a supplier purchase order from unfulfilled job parts (status=draft)."""
    tenant_id = user["tenant_id"]
    db = get_db()
    supplier = await _resolve_supplier(db, tenant_id, data.supplier_id, data.supplier_name)
    cost_map = await _supplier_cost_map(db, tenant_id, supplier["supplier_id"])

    lines: List[dict] = []
    sources: List[dict] = []

    if data.part_ids:
        for pid in data.part_ids:
            sources.append(await _get_job_part(db, tenant_id, pid))
    if data.job_ids:
        for jid in data.job_ids:
            rows = await getattr(db, JOB_PARTS).find(
                {"tenant_id": tenant_id, "job_id": jid}, {"_id": 0}
            ).to_list(1000)
            sources.extend(rows)

    for jp in sources:
        need = int(jp.get("to_order_qty") or 0)
        if need <= 0:
            continue
        sku = _clean(jp.get("sku"))
        lines.append({
            "job_part_id": jp.get("id"),
            "job_id": jp.get("job_id"),
            "name": jp.get("name"),
            "sku": sku,
            "qty": need,
            "unit_cost": cost_map.get(sku.lower()) if sku else None,
        })

    if data.lines:
        for ln in data.lines:
            lines.append({
                "job_part_id": ln.job_part_id,
                "job_id": ln.job_id,
                "name": ln.name.strip(),
                "sku": _clean(ln.sku),
                "qty": ln.qty,
                "unit_cost": ln.unit_cost,
            })

    if not lines:
        raise HTTPException(422, "No parts to order: lines, part_ids, or job_ids with unfulfilled parts required")

    doc = {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "supplier_id": supplier["supplier_id"],
        "supplier_name": supplier["supplier_name"],
        "status": "draft",
        "lines": lines,
        "estimated_total": round(
            sum(float(l.get("unit_cost") or 0.0) * l["qty"] for l in lines), 2),
        "created_by": user.get("id"),
        "created_at": _now_iso(),
        "sent_at": None,
        "received_at": None,
        "stub_transmission": None,
    }
    await getattr(db, PURCHASE_ORDERS).insert_one(doc)
    return _doc(dict(doc))


@router.get("/tenants/orders")
async def list_orders(status: Optional[str] = None,
                      user: dict = Depends(require_tenant_user)):
    """List purchase orders, optionally filtered by status."""
    if status and status not in ORDER_STATUSES:
        raise HTTPException(422, f"status must be one of {ORDER_STATUSES}")
    db = get_db()
    q = {"tenant_id": user["tenant_id"]}
    if status:
        q["status"] = status
    rows = await getattr(db, PURCHASE_ORDERS).find(q, {"_id": 0}).to_list(1000)
    rows = [_doc(dict(r)) for r in rows]
    rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return {"orders": rows, "count": len(rows)}


@router.post("/tenants/orders/{order_id}/send")
async def send_order(order_id: str, user: dict = Depends(require_tenant_user)):
    """Mark a draft order as sent (stub supplier API call)."""
    db = get_db()
    order = await _get_order(db, user["tenant_id"], order_id)
    if order["status"] != "draft":
        raise HTTPException(409, f"Only draft orders can be sent (current: {order['status']})")
    transmission = _transmit_to_supplier(order)
    await getattr(db, PURCHASE_ORDERS).update_one(
        {"id": order_id, "tenant_id": user["tenant_id"]},
        {"$set": {"status": "sent", "sent_at": _now_iso(),
                  "stub_transmission": transmission}},
    )
    order = await _get_order(db, user["tenant_id"], order_id)
    return _doc(order)


@router.post("/tenants/orders/{order_id}/receive")
async def receive_order(order_id: str, user: dict = Depends(require_tenant_user)):
    """Mark a sent order as received (parts landed at the shop)."""
    db = get_db()
    order = await _get_order(db, user["tenant_id"], order_id)
    if order["status"] != "sent":
        raise HTTPException(409, f"Only sent orders can be received (current: {order['status']})")
    await getattr(db, PURCHASE_ORDERS).update_one(
        {"id": order_id, "tenant_id": user["tenant_id"]},
        {"$set": {"status": "received", "received_at": _now_iso()}},
    )
    order = await _get_order(db, user["tenant_id"], order_id)
    return _doc(order)
