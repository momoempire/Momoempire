"""Multi-location management (#34): branches of one shop, tenant-isolated.

For shops with 2-20 branches. Locations belong to a tenant; jobs
(appointments), techs (schedule_staff) and equipment are linked to a location
through the lightweight ``location_assignments`` mapping collection — other
routers' schemas are never modified.

The first API call for a tenant lazily provisions a default "Main" location
(idempotent: only when the tenant has zero locations).

Revenue per location = sum of invoice ``total`` for invoices linked
(``appointment_id``) to the location's jobs, excluding ``void``/``draft``
invoices. Jobs without invoices contribute 0. This matches the platform
convention in routers/job_profitability.py (invoice linked to appointment).

Zero live network calls. Everything is tenant-scoped.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from security import require_tenant_user, require_tenant_owner_or_admin

router = APIRouter(prefix="/locations", tags=["locations"])


# ---------------------------------------------------------------- models ---

class AddressIn(BaseModel):
    street: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""


class LocationIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    address: AddressIn = Field(default_factory=AddressIn)
    timezone: str = ""
    phone: str = ""
    manager: str = ""
    active: bool = True


class LocationUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    address: Optional[AddressIn] = None
    timezone: Optional[str] = None
    phone: Optional[str] = None
    manager: Optional[str] = None
    active: Optional[bool] = None


class AssignIn(BaseModel):
    entity: str = Field(description="job | tech | equipment")
    entity_id: str = Field(min_length=1)
    location_id: str = Field(min_length=1)


# --------------------------------------------------------------- helpers ---

_ENTITY_COLLECTIONS: Dict[str, str] = {
    "job": "appointments",
    "tech": "schedule_staff",
    "equipment": "equipment",
}

_NON_REVENUE_STATUSES = ("void", "draft")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k != "_id"}


async def _ensure_default_location(db, tenant_id: str) -> dict:
    """Lazily provision the default "Main" location for a tenant.

    Idempotent: only creates it when the tenant has no locations at all.
    """
    existing = await db.locations.find_one({"tenant_id": tenant_id}, {"_id": 0})
    if existing:
        return existing
    doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "name": "Main",
        "address": {"street": "", "city": "", "state": "", "zip": ""},
        "timezone": "",
        "phone": "",
        "manager": "",
        "active": True,
        "is_default": True,
        "created_at": _now_iso(),
    }
    await db.locations.insert_one(doc)
    return _clean(doc)


async def _get_location(db, tenant_id: str, location_id: str) -> dict:
    """Tenant-scoped lookup: another tenant's id 404s exactly like a missing one."""
    loc = await db.locations.find_one(
        {"id": location_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if not loc:
        raise HTTPException(404, "Location not found")
    return loc


async def _rollups(db, tenant_id: str) -> List[dict]:
    """Per-location rollups for the whole tenant, computed from bulk loads.

    Returns rollups sorted with the default location first, then by name.
    Each rollup: location_id, name, active, is_default, jobs_count, revenue,
    active_techs.
    """
    locations = await db.locations.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(500)

    assigns = await db.location_assignments.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(20000)
    jobs_at: Dict[str, set] = {}
    techs_at: Dict[str, set] = {}
    for a in assigns:
        loc_id = a.get("location_id")
        eid = a.get("entity_id")
        if not loc_id or not eid:
            continue
        if a.get("entity") == "job":
            jobs_at.setdefault(loc_id, set()).add(eid)
        elif a.get("entity") == "tech":
            techs_at.setdefault(loc_id, set()).add(eid)

    jobs = await db.appointments.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(20000)
    job_ids = {j["id"] for j in jobs}

    invoices = await db.invoices.find(
        {"tenant_id": tenant_id, "status": {"$nin": list(_NON_REVENUE_STATUSES)}},
        {"_id": 0},
    ).to_list(20000)
    revenue_by_job: Dict[str, float] = {}
    for inv in invoices:
        jid = inv.get("appointment_id")
        if jid:
            revenue_by_job[jid] = revenue_by_job.get(jid, 0.0) + float(inv.get("total") or 0)

    techs = await db.schedule_staff.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(10000)
    tech_ids = {t["id"] for t in techs}

    out: List[dict] = []
    for loc in locations:
        loc_job_ids = {jid for jid in jobs_at.get(loc["id"], set()) if jid in job_ids}
        loc_tech_ids = {tid for tid in techs_at.get(loc["id"], set()) if tid in tech_ids}
        revenue = round(sum(revenue_by_job.get(jid, 0.0) for jid in loc_job_ids), 2)
        out.append({
            "location_id": loc["id"],
            "name": loc.get("name", ""),
            "active": bool(loc.get("active", True)),
            "is_default": bool(loc.get("is_default", False)),
            "jobs_count": len(loc_job_ids),
            "revenue": revenue,
            "active_techs": len(loc_tech_ids),
        })
    out.sort(key=lambda r: (not r["is_default"], r["name"].lower()))
    return out


async def _unassigned(db, tenant_id: str) -> dict:
    """Jobs (and their invoice revenue) not mapped to any location."""
    jobs = await db.appointments.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(20000)
    assigns = await db.location_assignments.find(
        {"tenant_id": tenant_id, "entity": "job"}, {"_id": 0}
    ).to_list(20000)
    assigned = {a.get("entity_id") for a in assigns}
    unassigned_ids = [j["id"] for j in jobs if j["id"] not in assigned]
    revenue = 0.0
    if unassigned_ids:
        invoices = await db.invoices.find(
            {"tenant_id": tenant_id,
             "appointment_id": {"$in": unassigned_ids},
             "status": {"$nin": list(_NON_REVENUE_STATUSES)}},
            {"_id": 0},
        ).to_list(20000)
        revenue = round(sum(float(i.get("total") or 0) for i in invoices), 2)
    return {"jobs_count": len(unassigned_ids), "revenue": revenue}


# ------------------------------------------------------------------ CRUD ---

@router.get("/")
async def list_locations(user: dict = Depends(require_tenant_user)):
    """List the tenant's locations. First use lazily provisions "Main"."""
    db = get_db()
    await _ensure_default_location(db, user["tenant_id"])
    rows = await db.locations.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).to_list(500)
    rows.sort(key=lambda l: (not l.get("is_default", False), str(l.get("name", "")).lower()))
    return {"locations": rows, "count": len(rows)}


@router.post("/")
async def create_location(data: LocationIn, user: dict = Depends(require_tenant_user)):
    """Create a branch location. The default "Main" is provisioned first so a
    tenant always has a fallback location."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await _ensure_default_location(db, tenant_id)
    doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "name": data.name.strip(),
        "address": data.address.model_dump(),
        "timezone": data.timezone.strip(),
        "phone": data.phone.strip(),
        "manager": data.manager.strip(),
        "active": data.active,
        "is_default": False,
        "created_at": _now_iso(),
    }
    await db.locations.insert_one(doc)
    return _clean(doc)


# ------------------------------------------------------------ assignment ---

@router.post("/assign")
async def assign(data: AssignIn, user: dict = Depends(require_tenant_user)):
    """Assign a job, tech, or equipment item to a location.

    Stored as a lightweight mapping in ``location_assignments`` (one location
    per entity — re-assigning moves it). Other routers' schemas are untouched.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    await _ensure_default_location(db, tenant_id)

    entity = (data.entity or "").strip().lower()
    if entity not in _ENTITY_COLLECTIONS:
        raise HTTPException(
            400, f"entity must be one of: {', '.join(sorted(_ENTITY_COLLECTIONS))}"
        )
    loc = await _get_location(db, tenant_id, data.location_id)

    collection = _ENTITY_COLLECTIONS[entity]
    ent = await getattr(db, collection).find_one(
        {"id": data.entity_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if not ent:
        raise HTTPException(404, f"{entity} not found")

    await db.location_assignments.update_one(
        {"tenant_id": tenant_id, "entity": entity, "entity_id": data.entity_id},
        {"$set": {
            "location_id": loc["id"],
            "assigned_at": _now_iso(),
        }},
        upsert=True,
    )
    return {
        "ok": True,
        "entity": entity,
        "entity_id": data.entity_id,
        "location_id": loc["id"],
        "location_name": loc.get("name", ""),
    }


@router.delete("/assign")
async def unassign(entity: str, entity_id: str, user: dict = Depends(require_tenant_user)):
    """Remove an entity's location assignment (it becomes unassigned)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    ent = (entity or "").strip().lower()
    if ent not in _ENTITY_COLLECTIONS:
        raise HTTPException(
            400, f"entity must be one of: {', '.join(sorted(_ENTITY_COLLECTIONS))}"
        )
    res = await db.location_assignments.delete_one(
        {"tenant_id": tenant_id, "entity": ent, "entity_id": entity_id}
    )
    return {"ok": True, "removed": res.deleted_count > 0}


@router.put("/{location_id}")
async def update_location(location_id: str, data: LocationUpdate,
                          user: dict = Depends(require_tenant_user)):
    """Update a location. id / tenant_id / is_default are not editable."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await _ensure_default_location(db, tenant_id)
    await _get_location(db, tenant_id, location_id)
    patch = {k: v for k, v in data.model_dump(exclude_unset=True).items()}
    if "name" in patch:
        patch["name"] = patch["name"].strip()
    if "address" in patch and patch["address"] is not None:
        patch["address"] = patch["address"]
    if not patch:
        raise HTTPException(400, "Nothing to update")
    await db.locations.update_one(
        {"id": location_id, "tenant_id": tenant_id}, {"$set": patch}
    )
    return await db.locations.find_one(
        {"id": location_id, "tenant_id": tenant_id}, {"_id": 0}
    )


@router.delete("/{location_id}")
async def deactivate_location(location_id: str, user: dict = Depends(require_tenant_user)):
    """Close a location (soft delete: active=False). Assignments are kept so
    history stays intact; reactivate with PUT."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await _ensure_default_location(db, tenant_id)
    loc = await _get_location(db, tenant_id, location_id)
    await db.locations.update_one(
        {"id": location_id, "tenant_id": tenant_id}, {"$set": {"active": False}}
    )
    loc["active"] = False
    return loc


# ------------------------------------------------------------- scoping ---

# NOTE: /consolidated is declared before /{location_id} so "consolidated"
# is never captured as a location id.

@router.get("/consolidated")
async def consolidated(user: dict = Depends(require_tenant_owner_or_admin)):
    """Cross-location rollup for owners/admins: per-location breakdown plus
    totals. Totals always equal the sum of the per-location rows; unassigned
    jobs are reported separately so nothing is double-counted."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await _ensure_default_location(db, tenant_id)
    rows = await _rollups(db, tenant_id)
    totals = {
        "jobs_count": sum(r["jobs_count"] for r in rows),
        "revenue": round(sum(r["revenue"] for r in rows), 2),
        "active_techs": sum(r["active_techs"] for r in rows),
    }
    return {
        "tenant_id": tenant_id,
        "location_count": len(rows),
        "locations": rows,
        "totals": totals,
        "unassigned": await _unassigned(db, tenant_id),
    }


@router.get("/{location_id}/jobs")
async def jobs_at_location(location_id: str, user: dict = Depends(require_tenant_user)):
    """Jobs (appointments) assigned to this location, most recent first."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await _ensure_default_location(db, tenant_id)
    await _get_location(db, tenant_id, location_id)
    assigns = await db.location_assignments.find(
        {"tenant_id": tenant_id, "entity": "job", "location_id": location_id},
        {"_id": 0},
    ).to_list(10000)
    job_ids = [a["entity_id"] for a in assigns]
    jobs = []
    if job_ids:
        jobs = await db.appointments.find(
            {"tenant_id": tenant_id, "id": {"$in": job_ids}}, {"_id": 0}
        ).to_list(10000)
    jobs.sort(key=lambda j: str(j.get("start_at") or ""), reverse=True)
    return {"location_id": location_id, "jobs": jobs, "count": len(jobs)}


@router.get("/{location_id}/dashboard")
async def location_dashboard(location_id: str, user: dict = Depends(require_tenant_user)):
    """Per-location rollup: revenue, jobs count, active techs, jobs by status."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await _ensure_default_location(db, tenant_id)
    location = await _get_location(db, tenant_id, location_id)
    rollup = next(
        (r for r in await _rollups(db, tenant_id) if r["location_id"] == location_id),
        None,
    )
    by_status: Dict[str, int] = {}
    assigns = await db.location_assignments.find(
        {"tenant_id": tenant_id, "entity": "job", "location_id": location_id},
        {"_id": 0},
    ).to_list(10000)
    job_ids = [a["entity_id"] for a in assigns]
    if job_ids:
        jobs = await db.appointments.find(
            {"tenant_id": tenant_id, "id": {"$in": job_ids}}, {"_id": 0}
        ).to_list(10000)
        for j in jobs:
            st = j.get("status") or "scheduled"
            by_status[st] = by_status.get(st, 0) + 1
    return {
        "location": location,
        "jobs_count": rollup["jobs_count"],
        "revenue": rollup["revenue"],
        "active_techs": rollup["active_techs"],
        "jobs_by_status": by_status,
    }

