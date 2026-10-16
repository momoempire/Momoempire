"""Inventory / truck stock: know what's on every truck and in the warehouse.

Pairs with parts ordering (#21) which handles BUYING — this module handles
TRACKING.

- Stock locations: warehouses and trucks (a truck may have an assigned tech).
- Parts: SKU + name, optional link to a pricebook item (cost/unit follow).
- Movements: an append-only ledger (receive | transfer | use_on_job |
  adjust). ``use_on_job`` requires a job_id and decrements truck stock.
  Stock can never go negative — a movement that would drive a balance below
  zero is rejected (400).
- Alerts: low stock = quantity at or below the part's reorder_point.
- Job usage: per-job totals of parts consumed, feeding job costing.

Zero live network calls. Every query is tenant-scoped.

Collections: ``inventory_locations``, ``inventory_parts``,
``inventory_movements`` (the ledger), ``inventory_stock`` (materialized
balances, one doc per part x location, always derived from the ledger by the
movement handler below).
"""
from __future__ import annotations

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user

router = APIRouter(prefix="/inventory", tags=["inventory"])

LOCATIONS = "inventory_locations"
PARTS = "inventory_parts"
MOVEMENTS = "inventory_movements"
STOCK = "inventory_stock"

LocationType = Literal["warehouse", "truck"]
MovementType = Literal["receive", "transfer", "use_on_job", "adjust"]


# ---------- pydantic models ----------

class LocationIn(BaseModel):
    type: LocationType
    name: str = Field(min_length=1, max_length=120)
    tech_id: Optional[str] = None
    tech_name: Optional[str] = None


class LocationPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    tech_id: Optional[str] = None
    tech_name: Optional[str] = None
    active: Optional[bool] = None


class PartIn(BaseModel):
    sku: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=160)
    pricebook_item_id: Optional[str] = None
    reorder_point: int = Field(default=0, ge=0)
    unit: str = Field(default="ea", max_length=20)
    unit_cost: Optional[float] = Field(default=None, ge=0)
    active: bool = True


class PartPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    pricebook_item_id: Optional[str] = None
    reorder_point: Optional[int] = Field(default=None, ge=0)
    unit: Optional[str] = Field(default=None, max_length=20)
    unit_cost: Optional[float] = Field(default=None, ge=0)
    active: Optional[bool] = None


class MovementIn(BaseModel):
    part_id: str
    from_location_id: Optional[str] = None
    to_location_id: Optional[str] = None
    qty: int = Field(gt=0)
    type: MovementType
    job_id: Optional[str] = None
    note: Optional[str] = Field(default=None, max_length=500)


class AdjustIn(BaseModel):
    part_id: str
    location_id: str
    qty: int = Field(description="positive adds, negative removes")
    note: Optional[str] = Field(default=None, max_length=500)


# ---------- helpers ----------

def _doc(d: dict) -> dict:
    d.pop("_id", None)
    return d


async def _get_location(db, tenant_id: str, location_id: str) -> dict:
    loc = await db[LOCATIONS].find_one(
        {"id": location_id, "tenant_id": tenant_id, "active": {"$ne": False}}, {"_id": 0})
    if not loc:
        raise HTTPException(404, "Location not found")
    return loc


async def _get_part(db, tenant_id: str, part_id: str) -> dict:
    part = await db[PARTS].find_one(
        {"id": part_id, "tenant_id": tenant_id}, {"_id": 0})
    if not part:
        raise HTTPException(404, "Part not found")
    return part


async def _current_qty(db, tenant_id: str, part_id: str, location_id: str) -> int:
    s = await db[STOCK].find_one(
        {"tenant_id": tenant_id, "part_id": part_id, "location_id": location_id},
        {"_id": 0})
    return int(s.get("qty") or 0) if s else 0


async def _set_qty(db, tenant_id: str, part_id: str, location_id: str, qty: int) -> None:
    await db[STOCK].update_one(
        {"tenant_id": tenant_id, "part_id": part_id, "location_id": location_id},
        {"$set": {"qty": qty, "updated_at": _now_iso(),
                  "part_id": part_id, "location_id": location_id,
                  "tenant_id": tenant_id}},
        upsert=True)


async def _resolve_cost(db, tenant_id: str, part: dict) -> float:
    """Unit cost snapshot: explicit part cost first, else linked pricebook item."""
    if part.get("unit_cost") is not None:
        return float(part["unit_cost"])
    pb_id = part.get("pricebook_item_id")
    if pb_id:
        item = await db.pricebook_items.find_one(
            {"id": pb_id, "tenant_id": tenant_id}, {"_id": 0})
        if item and item.get("cost") is not None:
            return float(item["cost"])
    return 0.0


async def _apply_movement(db, tenant_id: str, part_id: str, from_loc: Optional[str],
                          to_loc: Optional[str], qty: int, mtype: str,
                          job_id: Optional[str], note: Optional[str],
                          actor: Optional[str] = None) -> dict:
    """Validate and apply a ledger movement, keeping balances non-negative."""
    part = await _get_part(db, tenant_id, part_id)
    if from_loc:
        await _get_location(db, tenant_id, from_loc)
    if to_loc:
        await _get_location(db, tenant_id, to_loc)

    if from_loc:
        cur = await _current_qty(db, tenant_id, part_id, from_loc)
        if cur - qty < 0:
            raise HTTPException(
                400,
                f"Insufficient stock: have {cur} {part.get('unit', 'ea')}, "
                f"movement needs {qty}. Negative stock is not allowed.")
        await _set_qty(db, tenant_id, part_id, from_loc, cur - qty)
    if to_loc:
        cur = await _current_qty(db, tenant_id, part_id, to_loc)
        await _set_qty(db, tenant_id, part_id, to_loc, cur + qty)

    doc = {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "part_id": part_id,
        "from_location_id": from_loc,
        "to_location_id": to_loc,
        "qty": qty,
        "type": mtype,
        "job_id": job_id,
        "note": note,
        "unit_cost": await _resolve_cost(db, tenant_id, part),
        "created_by": actor,
        "created_at": _now_iso(),
    }
    await db[MOVEMENTS].insert_one(doc)
    return _doc(doc)


def _serialize_location(loc: dict) -> dict:
    return {
        "id": loc["id"], "type": loc.get("type"), "name": loc.get("name"),
        "tech_id": loc.get("tech_id"), "tech_name": loc.get("tech_name"),
        "active": loc.get("active", True),
        "created_at": loc.get("created_at"),
    }


def _serialize_part(part: dict) -> dict:
    return {
        "id": part["id"], "sku": part.get("sku"), "name": part.get("name"),
        "pricebook_item_id": part.get("pricebook_item_id"),
        "reorder_point": int(part.get("reorder_point") or 0),
        "unit": part.get("unit", "ea"), "unit_cost": part.get("unit_cost"),
        "active": part.get("active", True),
        "created_at": part.get("created_at"),
    }


# ---------- locations ----------

@router.post("/locations", status_code=201)
async def create_location(data: LocationIn, user: dict = Depends(require_tenant_user)):
    """Add a warehouse or truck stock location."""
    db = get_db()
    tenant_id = user["tenant_id"]
    doc = {
        "id": _uuid(), "tenant_id": tenant_id, "type": data.type,
        "name": data.name.strip(),
        "tech_id": data.tech_id, "tech_name": data.tech_name,
        "active": True, "created_at": _now_iso(),
    }
    await db[LOCATIONS].insert_one(doc)
    return {"location": _serialize_location(_doc(doc))}


@router.get("/locations")
async def list_locations(user: dict = Depends(require_tenant_user)):
    """All stock locations for this tenant (warehouses + trucks)."""
    db = get_db()
    locs = await db[LOCATIONS].find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}).to_list(500)
    return {"locations": [_serialize_location(l) for l in sorted(locs, key=lambda x: x.get("name", ""))]}


@router.put("/locations/{location_id}")
async def update_location(location_id: str, data: LocationPatch,
                          user: dict = Depends(require_tenant_user)):
    """Rename a location or reassign a truck's tech."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await _get_location(db, tenant_id, location_id)
    patch = {k: v for k, v in data.model_dump().items() if v is not None}
    if "name" in patch:
        patch["name"] = patch["name"].strip()
    if patch:
        await db[LOCATIONS].update_one({"id": location_id, "tenant_id": tenant_id},
                                       {"$set": patch})
    loc = await db[LOCATIONS].find_one({"id": location_id, "tenant_id": tenant_id}, {"_id": 0})
    return {"location": _serialize_location(_doc(loc))}


@router.delete("/locations/{location_id}")
async def delete_location(location_id: str, user: dict = Depends(require_tenant_user)):
    """Delete a location. Blocked while it still holds stock (move parts first)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await _get_location(db, tenant_id, location_id)
    nonzero = await db[STOCK].find(
        {"tenant_id": tenant_id, "location_id": location_id, "qty": {"$gt": 0}},
        {"_id": 0}).to_list(5)
    if nonzero:
        raise HTTPException(
            409, "Location still holds stock — transfer or use the parts first.")
    await db[LOCATIONS].delete_one({"id": location_id, "tenant_id": tenant_id})
    await db[STOCK].delete_many({"tenant_id": tenant_id, "location_id": location_id})
    return {"ok": True, "deleted": location_id}


# ---------- parts ----------

@router.post("/parts", status_code=201)
async def create_part(data: PartIn, user: dict = Depends(require_tenant_user)):
    """Catalog a tracked part. SKU is unique per tenant. A pricebook link
    brings in the item's cost for job costing."""
    db = get_db()
    tenant_id = user["tenant_id"]
    sku = data.sku.strip()
    existing = await db[PARTS].find_one(
        {"tenant_id": tenant_id, "sku": sku}, {"_id": 0})
    if existing:
        raise HTTPException(409, f"SKU '{sku}' is already cataloged.")
    if data.pricebook_item_id:
        item = await db.pricebook_items.find_one(
            {"id": data.pricebook_item_id, "tenant_id": tenant_id}, {"_id": 0})
        if not item:
            raise HTTPException(404, "Pricebook item not found")
    doc = {
        "id": _uuid(), "tenant_id": tenant_id, "sku": sku,
        "name": data.name.strip(),
        "pricebook_item_id": data.pricebook_item_id,
        "reorder_point": int(data.reorder_point or 0),
        "unit": data.unit or "ea",
        "unit_cost": float(data.unit_cost) if data.unit_cost is not None else None,
        "active": data.active,
        "created_at": _now_iso(),
    }
    await db[PARTS].insert_one(doc)
    return {"part": _serialize_part(_doc(doc))}


@router.get("/parts")
async def list_parts(active_only: bool = Query(True),
                     user: dict = Depends(require_tenant_user)):
    """All cataloged parts for this tenant."""
    db = get_db()
    q: dict = {"tenant_id": user["tenant_id"]}
    if active_only:
        q["active"] = {"$ne": False}
    parts = await db[PARTS].find(q, {"_id": 0}).to_list(2000)
    return {"parts": [_serialize_part(p) for p in sorted(parts, key=lambda x: x.get("sku", ""))]}


@router.put("/parts/{part_id}")
async def update_part(part_id: str, data: PartPatch,
                      user: dict = Depends(require_tenant_user)):
    """Edit a part (reorder point, cost, name, pricebook link, active flag)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await _get_part(db, tenant_id, part_id)
    patch = {k: v for k, v in data.model_dump().items() if v is not None}
    if "name" in patch:
        patch["name"] = patch["name"].strip()
    if "pricebook_item_id" in patch and patch["pricebook_item_id"]:
        item = await db.pricebook_items.find_one(
            {"id": patch["pricebook_item_id"], "tenant_id": tenant_id}, {"_id": 0})
        if not item:
            raise HTTPException(404, "Pricebook item not found")
    if "unit_cost" in patch and patch["unit_cost"] is not None:
        patch["unit_cost"] = float(patch["unit_cost"])
    if patch:
        await db[PARTS].update_one({"id": part_id, "tenant_id": tenant_id},
                                   {"$set": patch})
    part = await db[PARTS].find_one({"id": part_id, "tenant_id": tenant_id}, {"_id": 0})
    return {"part": _serialize_part(_doc(part))}


# ---------- movements (append-only ledger) ----------

@router.post("/movements", status_code=201)
async def record_movement(data: MovementIn, user: dict = Depends(require_tenant_user)):
    """Append one ledger movement.

    - receive: stock arrives from outside (to_location required).
    - transfer: from_location -> to_location.
    - use_on_job: part consumed on a job; job_id required, decrements the
      from_location (usually a truck).
    - adjust: use POST /movements/adjust for negative corrections.

    Movements are append-only: there is deliberately no update or delete
    endpoint. Stock can never go negative.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    mtype: str = data.type

    if mtype == "receive":
        if not data.to_location_id or data.from_location_id:
            raise HTTPException(400, "receive needs to_location only")
    elif mtype == "transfer":
        if not data.from_location_id or not data.to_location_id:
            raise HTTPException(400, "transfer needs from_location and to_location")
        if data.from_location_id == data.to_location_id:
            raise HTTPException(400, "transfer locations must differ")
    elif mtype == "use_on_job":
        if not data.from_location_id or data.to_location_id:
            raise HTTPException(400, "use_on_job needs from_location only")
        if not data.job_id:
            raise HTTPException(400, "use_on_job requires job_id")
        job = await db.appointments.find_one(
            {"id": data.job_id, "tenant_id": tenant_id}, {"_id": 0})
        if not job:
            raise HTTPException(404, "Job not found")
    elif mtype == "adjust":
        raise HTTPException(400, "adjust uses POST /inventory/movements/adjust")
    else:
        raise HTTPException(400, f"Unknown movement type '{mtype}'")

    actor = user.get("id") or user.get("email")
    mv = await _apply_movement(db, tenant_id, data.part_id,
                               data.from_location_id, data.to_location_id,
                               data.qty, mtype, data.job_id, data.note, actor)
    return {"movement": mv}


@router.post("/movements/adjust", status_code=201)
async def record_adjustment(data: AdjustIn, user: dict = Depends(require_tenant_user)):
    """Manual stock correction. qty > 0 adds, qty < 0 removes (still can't go
    negative). Append-only like every other movement."""
    if data.qty == 0:
        raise HTTPException(400, "adjustment qty cannot be zero")
    db = get_db()
    tenant_id = user["tenant_id"]
    if data.qty > 0:
        mv = await _apply_movement(db, tenant_id, data.part_id, None,
                                   data.location_id, data.qty, "adjust",
                                   None, data.note, user.get("id"))
    else:
        mv = await _apply_movement(db, tenant_id, data.part_id, data.location_id,
                                   None, abs(data.qty), "adjust",
                                   None, data.note, user.get("id"))
    return {"movement": mv}


@router.get("/movements")
async def list_movements(part_id: Optional[str] = Query(None),
                         location_id: Optional[str] = Query(None),
                         type: Optional[str] = Query(None),
                         job_id: Optional[str] = Query(None),
                         limit: int = Query(200, le=1000),
                         user: dict = Depends(require_tenant_user)):
    """Movement history, newest first. Filter by part, location, type, job."""
    db = get_db()
    tenant_id = user["tenant_id"]
    q: dict = {"tenant_id": tenant_id}
    if part_id:
        q["part_id"] = part_id
    if location_id:
        q["$or"] = [{"from_location_id": location_id},
                    {"to_location_id": location_id}]
    if type:
        q["type"] = type
    if job_id:
        q["job_id"] = job_id
    docs = await db[MOVEMENTS].find(q, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return {"movements": docs, "count": len(docs)}


# ---------- stock & alerts ----------

@router.get("/stock")
async def get_stock(location_id: Optional[str] = Query(None),
                    low_only: bool = Query(False),
                    user: dict = Depends(require_tenant_user)):
    """Current on-hand quantities. Omit location_id for every location."""
    db = get_db()
    tenant_id = user["tenant_id"]
    q: dict = {"tenant_id": tenant_id}
    if location_id:
        await _get_location(db, tenant_id, location_id)
        q["location_id"] = location_id
    docs = await db[STOCK].find(q, {"_id": 0}).to_list(5000)
    parts = {p["id"]: p for p in await db[PARTS].find(
        {"tenant_id": tenant_id}, {"_id": 0}).to_list(5000)}
    locs = {l["id"]: l for l in await db[LOCATIONS].find(
        {"tenant_id": tenant_id}, {"_id": 0}).to_list(500)}
    rows = []
    for s in docs:
        part = parts.get(s["part_id"])
        loc = locs.get(s["location_id"])
        if not part:
            continue
        rp = int(part.get("reorder_point") or 0)
        qty = int(s.get("qty") or 0)
        row = {
            "part_id": s["part_id"], "sku": part.get("sku"),
            "part_name": part.get("name"), "unit": part.get("unit", "ea"),
            "location_id": s["location_id"],
            "location_name": loc.get("name") if loc else None,
            "location_type": loc.get("type") if loc else None,
            "qty": qty, "reorder_point": rp,
            "low_stock": qty <= rp,
        }
        if low_only and not row["low_stock"]:
            continue
        rows.append(row)
    rows.sort(key=lambda r: (r["location_name"] or "", r["sku"] or ""))
    return {"stock": rows, "count": len(rows)}


@router.get("/alerts")
async def get_alerts(user: dict = Depends(require_tenant_user)):
    """Low-stock alerts: every part x location at or below reorder point."""
    db = get_db()
    tenant_id = user["tenant_id"]
    docs = await db[STOCK].find({"tenant_id": tenant_id}, {"_id": 0}).to_list(5000)
    parts = {p["id"]: p for p in await db[PARTS].find(
        {"tenant_id": tenant_id}, {"_id": 0}).to_list(5000)}
    locs = {l["id"]: l for l in await db[LOCATIONS].find(
        {"tenant_id": tenant_id}, {"_id": 0}).to_list(500)}
    alerts = []
    for s in docs:
        part = parts.get(s["part_id"])
        if not part or part.get("active") is False:
            continue
        rp = int(part.get("reorder_point") or 0)
        qty = int(s.get("qty") or 0)
        if qty <= rp:
            loc = locs.get(s["location_id"])
            alerts.append({
                "part_id": s["part_id"], "sku": part.get("sku"),
                "part_name": part.get("name"),
                "location_id": s["location_id"],
                "location_name": loc.get("name") if loc else None,
                "location_type": loc.get("type") if loc else None,
                "qty": qty, "reorder_point": rp,
                "out_of_stock": qty == 0,
            })
    alerts.sort(key=lambda a: (not a["out_of_stock"], a["qty"]))
    return {"alerts": alerts, "count": len(alerts)}


@router.get("/job-usage/{job_id}")
async def get_job_usage(job_id: str, user: dict = Depends(require_tenant_user)):
    """Parts consumed on a job — feeds job costing.

    Groups the job's use_on_job movements by part with quantities, the
    cost snapshot taken at movement time, and the extended cost.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    mvs = await db[MOVEMENTS].find(
        {"tenant_id": tenant_id, "job_id": job_id, "type": "use_on_job"},
        {"_id": 0}).to_list(5000)
    parts = {p["id"]: p for p in await db[PARTS].find(
        {"tenant_id": tenant_id}, {"_id": 0}).to_list(5000)}
    totals: dict = {}
    for mv in mvs:
        pid = mv["part_id"]
        part = parts.get(pid, {})
        entry = totals.setdefault(pid, {
            "part_id": pid, "sku": part.get("sku"), "part_name": part.get("name"),
            "unit": part.get("unit", "ea"),
            "unit_cost": float(mv.get("unit_cost") or 0),
            "qty_used": 0,
        })
        entry["qty_used"] += int(mv.get("qty") or 0)
        if mv.get("unit_cost") and entry["unit_cost"] == 0:
            entry["unit_cost"] = float(mv["unit_cost"])
    lines = sorted(totals.values(), key=lambda x: x["part_name"] or "")
    for line in lines:
        line["total_cost"] = round(line["qty_used"] * line["unit_cost"], 2)
    total_cost = round(sum(l["total_cost"] for l in lines), 2)
    return {
        "job_id": job_id,
        "lines": lines,
        "line_count": len(lines),
        "total_qty": sum(l["qty_used"] for l in lines),
        "total_cost": total_cost,
    }
