"""Equipment history: per-customer-location equipment records + service timeline.

Each shop tracks the physical units it services (furnace, condenser, water
heater, ...) at each customer's service address, plus the timeline of service
visits for that unit. This is the equipment record ServiceTitan techs see in
the driveway ("this 2019 Trane condenser had a capacitor replaced 4 months
ago").

Equipment also carries the platform's service-address field (street/city/
state/zip) — appointments and customers don't have one yet.

Collections (all tenant-scoped):
- `equipment`: the asset record — customer link, location label, full service
  address, make/model/serial, install date, type, notes, active flag.
- `equipment_service_events`: service visits tied to one piece of equipment,
  optionally linked to a job (appointment) id.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user

router = APIRouter(prefix="/tenants/equipment", tags=["equipment"])

# Suggested equipment types; the field is free-form so trade packs
# (HVAC/plumbing/electrical) can introduce their own later.
SUGGESTED_TYPES = [
    "furnace", "ac_condenser", "air_handler", "heat_pump", "thermostat",
    "water_heater", "boiler", "electrical_panel", "generator",
    "garbage_disposal", "well_pump", "septic_system", "other",
]


# ---------- models ----------

class ServiceAddress(BaseModel):
    street: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""


class EquipmentIn(BaseModel):
    customer_id: str
    customer_name: str = ""
    location_label: str = ""  # e.g. "Attic unit", "Basement", "Main floor"
    service_address: ServiceAddress = Field(default_factory=ServiceAddress)
    equipment_type: str = ""
    make: str = ""
    model: str = ""
    serial_number: str = ""
    install_date: Optional[str] = None  # ISO date
    notes: str = ""
    active: bool = True


class EquipmentUpdate(BaseModel):
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    location_label: Optional[str] = None
    service_address: Optional[ServiceAddress] = None
    equipment_type: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    install_date: Optional[str] = None
    notes: Optional[str] = None
    active: Optional[bool] = None


class ServiceEventIn(BaseModel):
    service_date: Optional[str] = None  # ISO date/datetime; defaults to now
    job_id: Optional[str] = None  # appointment id this visit was part of
    description: str
    tech: str = ""


# ---------- helpers ----------

def _address_summary(addr: dict) -> str:
    parts = [addr.get("street"), addr.get("city"), addr.get("state"), addr.get("zip")]
    return ", ".join(p for p in parts if p)


def _public_equipment(doc: dict) -> dict:
    addr = doc.get("service_address") or {}
    return {
        "id": doc.get("id"),
        "tenant_id": doc.get("tenant_id"),
        "customer_id": doc.get("customer_id", ""),
        "customer_name": doc.get("customer_name", ""),
        "location_label": doc.get("location_label", ""),
        "service_address": {
            "street": addr.get("street", ""),
            "city": addr.get("city", ""),
            "state": addr.get("state", ""),
            "zip": addr.get("zip", ""),
        },
        "address_summary": _address_summary(addr),
        "equipment_type": doc.get("equipment_type", ""),
        "make": doc.get("make", ""),
        "model": doc.get("model", ""),
        "serial_number": doc.get("serial_number", ""),
        "install_date": doc.get("install_date"),
        "notes": doc.get("notes", ""),
        "active": bool(doc.get("active", True)),
        "service_event_count": int(doc.get("service_event_count") or 0),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


def _public_event(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "equipment_id": doc.get("equipment_id"),
        "service_date": doc.get("service_date"),
        "job_id": doc.get("job_id"),
        "description": doc.get("description", ""),
        "tech": doc.get("tech", ""),
        "created_at": doc.get("created_at"),
    }


async def _get_equipment(db, equipment_id: str, tenant_id: str, *, include_inactive: bool = False) -> dict:
    q: dict = {"id": equipment_id, "tenant_id": tenant_id}
    if not include_inactive:
        q["active"] = True
    doc = await db.equipment.find_one(q, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Equipment record not found")
    return doc


def _matches_search(doc: dict, q: str) -> bool:
    needle = q.strip().lower()
    if not needle:
        return True
    addr = doc.get("service_address") or {}
    hay = " ".join([
        str(doc.get("location_label") or ""),
        str(doc.get("customer_name") or ""),
        str(doc.get("equipment_type") or ""),
        str(doc.get("make") or ""),
        str(doc.get("model") or ""),
        str(doc.get("serial_number") or ""),
        str(addr.get("street") or ""),
        str(addr.get("city") or ""),
        str(addr.get("state") or ""),
        str(addr.get("zip") or ""),
    ]).lower()
    return needle in hay


# ---------- endpoints ----------

@router.post("/", status_code=201)
async def create_equipment(data: EquipmentIn, user: dict = Depends(require_tenant_user)):
    db = get_db()
    now = _now_iso()
    doc = {
        "id": _uuid(),
        "tenant_id": user["tenant_id"],
        "customer_id": data.customer_id,
        "customer_name": data.customer_name,
        "location_label": data.location_label,
        "service_address": data.service_address.model_dump(),
        "equipment_type": data.equipment_type,
        "make": data.make,
        "model": data.model,
        "serial_number": data.serial_number,
        "install_date": data.install_date,
        "notes": data.notes,
        "active": bool(data.active),
        "service_event_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    await db.equipment.insert_one(doc)
    return _public_equipment(doc)


@router.get("/")
async def list_equipment(
    user: dict = Depends(require_tenant_user),
    customer_id: str = "",
    q: str = "",
    include_inactive: bool = False,
):
    """List equipment records. Filter by customer_id; `q` searches location,
    make/model/serial/type and the service address."""
    db = get_db()
    query: dict = {"tenant_id": user["tenant_id"]}
    if not include_inactive:
        query["active"] = True
    if customer_id:
        query["customer_id"] = customer_id
    rows = await db.equipment.find(query, {"_id": 0}).sort("created_at", -1).to_list(2000)
    items = [_public_equipment(r) for r in rows if _matches_search(r, q)]
    return {"equipment": items, "count": len(items)}


# NOTE: /by-job/... is registered before /{equipment_id} so the literal
# segment is not captured as an equipment id.
@router.get("/by-job/{job_id}")
async def equipment_by_job(job_id: str, user: dict = Depends(require_tenant_user)):
    """Equipment records linked to a job (appointment) via their service events."""
    db = get_db()
    tenant_id = user["tenant_id"]
    events = await db.equipment_service_events.find(
        {"tenant_id": tenant_id, "job_id": job_id}, {"_id": 0}
    ).to_list(1000)
    ids = sorted({e.get("equipment_id") for e in events if e.get("equipment_id")})
    records = await db.equipment.find(
        {"tenant_id": tenant_id, "id": {"$in": ids}}, {"_id": 0}
    ).to_list(1000)
    return {"job_id": job_id, "equipment": [_public_equipment(r) for r in records]}


@router.get("/{equipment_id}")
async def get_equipment(equipment_id: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    return _public_equipment(
        await _get_equipment(db, equipment_id, user["tenant_id"], include_inactive=True)
    )


@router.put("/{equipment_id}")
async def update_equipment(
    equipment_id: str, data: EquipmentUpdate, user: dict = Depends(require_tenant_user)
):
    db = get_db()
    tenant_id = user["tenant_id"]
    doc = await _get_equipment(db, equipment_id, tenant_id, include_inactive=True)

    patch: dict = {}
    for field in ("customer_id", "customer_name", "location_label", "equipment_type",
                  "make", "model", "serial_number", "install_date", "notes", "active"):
        val = getattr(data, field)
        if val is not None:
            patch[field] = val
    if data.service_address is not None:
        patch["service_address"] = data.service_address.model_dump()

    if doc.get("active") is False and patch.get("active") is not True:
        # A deactivated record can only be reactivated, not edited.
        raise HTTPException(400, "Cannot edit a deactivated equipment record — reactivate it first")
    patch["updated_at"] = _now_iso()

    await db.equipment.update_one({"id": equipment_id, "tenant_id": tenant_id}, {"$set": patch})
    updated = await db.equipment.find_one({"id": equipment_id, "tenant_id": tenant_id}, {"_id": 0})
    return _public_equipment(updated)


@router.delete("/{equipment_id}")
async def delete_equipment(equipment_id: str, user: dict = Depends(require_tenant_user)):
    """Soft delete — the record goes inactive; its service history survives."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await _get_equipment(db, equipment_id, tenant_id, include_inactive=True)
    await db.equipment.update_one(
        {"id": equipment_id, "tenant_id": tenant_id},
        {"$set": {"active": False, "updated_at": _now_iso()}},
    )
    return {"ok": True, "id": equipment_id, "active": False}


@router.post("/{equipment_id}/service-events", status_code=201)
async def add_service_event(
    equipment_id: str, data: ServiceEventIn, user: dict = Depends(require_tenant_user)
):
    """Append a service visit to the equipment's timeline. An event can link
    back to the job (appointment) the visit was part of."""
    if not data.description.strip():
        raise HTTPException(400, "Description is required")
    db = get_db()
    tenant_id = user["tenant_id"]
    doc = await _get_equipment(db, equipment_id, tenant_id, include_inactive=True)
    if not doc.get("active", True):
        raise HTTPException(400, "Cannot log service events on a deactivated equipment record")

    now = _now_iso()
    event = {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "equipment_id": equipment_id,
        "service_date": data.service_date or now,
        "job_id": data.job_id,
        "description": data.description.strip(),
        "tech": data.tech,
        "created_at": now,
    }
    await db.equipment_service_events.insert_one(event)
    await db.equipment.update_one(
        {"id": equipment_id, "tenant_id": tenant_id},
        {"$set": {
            "service_event_count": int(doc.get("service_event_count") or 0) + 1,
            "updated_at": now,
        }},
    )
    return _public_event(event)


@router.get("/{equipment_id}/history")
async def equipment_history(equipment_id: str, user: dict = Depends(require_tenant_user)):
    """Service timeline for a piece of equipment, newest first."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await _get_equipment(db, equipment_id, tenant_id, include_inactive=True)
    rows = await db.equipment_service_events.find(
        {"equipment_id": equipment_id, "tenant_id": tenant_id}, {"_id": 0}
    ).to_list(1000)
    events = sorted(rows, key=lambda e: (e.get("service_date") or "", e.get("created_at") or ""),
                    reverse=True)
    return {"equipment_id": equipment_id, "events": [_public_event(e) for e in events]}


@router.get("/types/suggested")
async def suggested_types(user: dict = Depends(require_tenant_user)):
    return {"suggested": SUGGESTED_TYPES}
