"""Public status page + proactive issue monitoring (Layer 1 of the
customer-service excellence initiative: Momo supporting its shops).

Public surface (NO auth — this is the page customers and shop owners check
during an outage):
- GET /api/platform-status/public/status    overall status + component list
- GET /api/platform-status/public/incidents  recent incidents with updates

Tenant-authenticated surface:
- GET    /components              list components
- POST   /components              (owner) register a component
- PATCH  /components/{id}         (owner) set component status
- GET    /incidents               list incidents
- POST   /incidents               (owner) open an incident
- POST   /incidents/{id}/updates  (owner) append a public update
- POST   /incidents/{id}/resolve  (owner) mark resolved
- GET    /monitors                list synthetic monitors
- POST   /monitors                (owner) define a monitor
- POST   /monitors/{id}/check     ingest a check result; auto-opens an
  incident (and pages the on-call, stubbed) after 3 consecutive failures.

Component statuses: operational / degraded / partial_outage / major_outage.
Incident statuses: investigating -> identified -> monitoring -> resolved.

Collections (tenant-scoped except the two /public/* routes):
- `status_components`, `status_incidents`, `status_updates`,
  `status_monitors`, `monitor_results`.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin

router = APIRouter(prefix="/api/platform-status", tags=["platform-status"])

# Default components seeded per tenant on first read of the public status page.
DEFAULT_COMPONENTS = [
    {"key": "api", "name": "Platform API", "description": "Core REST API powering the dashboard and integrations"},
    {"key": "ai_receptionist", "name": "AI receptionist", "description": "AI call answering and booking assistant"},
    {"key": "voice_pipeline", "name": "Voice pipeline", "description": "Real-time call transcription and voice synthesis"},
    {"key": "sms_delivery", "name": "SMS delivery", "description": "Outbound SMS and messaging delivery"},
    {"key": "web_app", "name": "Web app", "description": "Shop dashboard and customer portals"},
]

COMPONENT_STATUSES = {"operational", "degraded", "partial_outage", "major_outage"}
INCIDENT_STATUSES = {"investigating", "identified", "monitoring", "resolved"}

# Auto-open an incident after this many consecutive monitor failures.
FAILURE_THRESHOLD = 3


# ---------- request models ----------

class ComponentIn(BaseModel):
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""


class ComponentStatusIn(BaseModel):
    status: str = Field(pattern="^(operational|degraded|partial_outage|major_outage)$")


class IncidentIn(BaseModel):
    title: str = Field(min_length=1)
    component_id: Optional[str] = None
    severity: str = Field(default="minor", pattern="^(minor|major|critical)$")
    status: str = Field(default="investigating",
                        pattern="^(investigating|identified|monitoring|resolved)$")


class IncidentUpdateIn(BaseModel):
    status: Optional[str] = Field(default=None,
                                  pattern="^(investigating|identified|monitoring|resolved)$")
    message: str = Field(min_length=1)


class MonitorIn(BaseModel):
    name: str = Field(min_length=1)
    component_id: str = Field(min_length=1)
    check_type: str = Field(default="http", pattern="^(http|ping|synthetic)$")
    target: str = ""
    interval_seconds: int = Field(default=60, ge=10)


class MonitorCheckIn(BaseModel):
    success: bool
    latency_ms: Optional[float] = None
    detail: str = ""


# ---------- helpers ----------

def _overall_status(components: List[Dict]) -> str:
    """Worst component status rolls up to the overall platform status."""
    worst = "operational"
    rank = {"operational": 0, "degraded": 1, "partial_outage": 2, "major_outage": 3}
    for c in components:
        if rank.get(c.get("status", "operational"), 0) > rank.get(worst, 0):
            worst = c.get("status", "operational")
    return worst


async def _ensure_components(db, tenant_id: str) -> List[Dict]:
    """Seed the default component set for a tenant on first use."""
    docs = await db.status_components.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(100)
    if not docs:
        now = _now_iso()
        for c in DEFAULT_COMPONENTS:
            await db.status_components.insert_one({
                "id": _uuid(), "tenant_id": tenant_id,
                "key": c["key"], "name": c["name"], "description": c["description"],
                "status": "operational",
                "created_at": now, "updated_at": now,
            })
        docs = await db.status_components.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(100)
    return docs


async def _get_incident(db, tenant_id: str, incident_id: str) -> Dict:
    inc = await db.status_incidents.find_one({"tenant_id": tenant_id, "id": incident_id})
    if not inc:
        raise HTTPException(404, "Incident not found")
    return inc


async def _attach_updates(db, tenant_id: str, incident: Dict) -> Dict:
    updates = await db.status_updates.find(
        {"tenant_id": tenant_id, "incident_id": incident["id"]}, {"_id": 0}
    ).sort("created_at", 1).to_list(500)
    out = dict(incident)
    out.pop("_id", None)
    out["updates"] = updates
    return out


def _page_on_call(tenant_id: str, incident: Dict):
    """Stubbed on-call paging. TODO(LIVE): route to the tenant's PagerDuty /
    OpsGenie integration or the configured on-call SMS number."""
    print(f"[PLATFORM-STATUS page {tenant_id}] incident={incident['id']} "
          f"title={incident.get('title')} severity={incident.get('severity')} (stubbed page)")


async def _auto_open_incident(db, tenant_id: str, monitor: Dict, consecutive_failures: int) -> Optional[Dict]:
    """Auto-open (or update) an incident for a repeatedly failing monitor.

    Dedupe is done in code (equality scan over the tenant's incidents) so the
    behavior is identical on real mongo and the in-memory test double.
    """
    open_inc = None
    for inc in await db.status_incidents.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(1000):
        if inc.get("auto_monitor_id") == monitor["id"] and inc.get("status") != "resolved":
            open_inc = inc
            break

    if open_inc:
        return open_inc

    now = _now_iso()
    severity = "major" if consecutive_failures >= 5 else "minor"
    incident_id = _uuid()
    incident = {
        "id": incident_id,
        "tenant_id": tenant_id,
        "title": f"{monitor.get('name')} failing checks",
        "component_id": monitor.get("component_id"),
        "severity": severity,
        "status": "investigating",
        "auto_monitor_id": monitor["id"],
        "consecutive_failures": consecutive_failures,
        "created_at": now,
        "updated_at": now,
        "resolved_at": None,
    }
    await db.status_incidents.insert_one(incident)
    await db.status_updates.insert_one({
        "id": _uuid(),
        "tenant_id": tenant_id,
        "incident_id": incident_id,
        "status": "investigating",
        "message": (f"Automated monitoring detected {consecutive_failures} consecutive "
                    f"check failures for '{monitor.get('name')}'. The team is investigating."),
        "created_at": now,
    })
    # Degrade the linked component so the public page reflects reality.
    if monitor.get("component_id"):
        await db.status_components.update_one(
            {"tenant_id": tenant_id, "id": monitor["component_id"]},
            {"$set": {"status": "degraded" if severity == "minor" else "partial_outage",
                      "updated_at": now}},
        )
    _page_on_call(tenant_id, incident)
    return incident


# ---------- public routes (NO auth) ----------

@router.get("/public/status")
async def public_status(tenant: str):
    """Public status page payload: overall status + all components.

    Takes ?tenant=<tenant_id> so each shop's page is addressable.
    """
    db = get_db()
    if not tenant:
        raise HTTPException(400, "tenant query parameter is required")
    components = await _ensure_components(db, tenant)
    return {
        "tenant_id": tenant,
        "status": _overall_status(components),
        "components": components,
        "updated_at": _now_iso(),
    }


@router.get("/public/incidents")
async def public_incidents(tenant: str, limit: int = 10):
    """Public incident timeline for ?tenant=<tenant_id>, newest first, each
    with its public update history."""
    db = get_db()
    if not tenant:
        raise HTTPException(400, "tenant query parameter is required")
    incidents = await db.status_incidents.find(
        {"tenant_id": tenant}, {"_id": 0}
    ).sort("created_at", -1).to_list(max(1, min(limit, 50)))
    return {"incidents": [await _attach_updates(db, tenant, inc) for inc in incidents]}


# ---------- components (tenant auth) ----------

@router.get("/components")
async def list_components(user: dict = Depends(require_tenant_user)):
    db = get_db()
    docs = await _ensure_components(db, user["tenant_id"])
    return {"components": docs, "count": len(docs)}


@router.post("/components")
async def create_component(data: ComponentIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Register an extra platform component (the 5 defaults are seeded)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await _ensure_components(db, tenant_id)
    existing = await db.status_components.find_one({"tenant_id": tenant_id, "key": data.key})
    if existing:
        raise HTTPException(409, "A component with this key already exists")
    now = _now_iso()
    doc = {
        "id": _uuid(), "tenant_id": tenant_id,
        "key": data.key, "name": data.name, "description": data.description,
        "status": "operational",
        "created_at": now, "updated_at": now,
    }
    await db.status_components.insert_one(doc)
    doc.pop("_id", None)
    return {"ok": True, **doc}


@router.patch("/components/{component_id}")
async def set_component_status(component_id: str, data: ComponentStatusIn,
                               user: dict = Depends(require_tenant_owner_or_admin)):
    """Set a component's operational status (manual override / recovery)."""
    db = get_db()
    comp = await db.status_components.find_one(
        {"tenant_id": user["tenant_id"], "id": component_id})
    if not comp:
        raise HTTPException(404, "Component not found")
    now = _now_iso()
    await db.status_components.update_one(
        {"tenant_id": user["tenant_id"], "id": component_id},
        {"$set": {"status": data.status, "updated_at": now}},
    )
    return {"ok": True, "id": component_id, "status": data.status, "updated_at": now}


# ---------- incidents (tenant auth) ----------

@router.get("/incidents")
async def list_incidents(user: dict = Depends(require_tenant_user)):
    db = get_db()
    incidents = await db.status_incidents.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}
    ).sort("created_at", -1).to_list(1000)
    return {"incidents": [await _attach_updates(db, user["tenant_id"], inc) for inc in incidents]}


@router.post("/incidents")
async def open_incident(data: IncidentIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Manually open an incident."""
    db = get_db()
    tenant_id = user["tenant_id"]
    now = _now_iso()
    doc = {
        "id": _uuid(), "tenant_id": tenant_id,
        "title": data.title, "component_id": data.component_id,
        "severity": data.severity, "status": data.status,
        "auto_monitor_id": None,
        "created_at": now, "updated_at": now, "resolved_at": None,
    }
    await db.status_incidents.insert_one(doc)
    await db.status_updates.insert_one({
        "id": _uuid(), "tenant_id": tenant_id, "incident_id": doc["id"],
        "status": data.status,
        "message": f"Incident opened: {data.title}",
        "created_at": now,
    })
    doc.pop("_id", None)
    return {"ok": True, **doc}


@router.post("/incidents/{incident_id}/updates")
async def add_incident_update(incident_id: str, data: IncidentUpdateIn,
                              user: dict = Depends(require_tenant_owner_or_admin)):
    """Append a public status update; optionally advances the incident status."""
    db = get_db()
    tenant_id = user["tenant_id"]
    inc = await _get_incident(db, tenant_id, incident_id)
    now = _now_iso()
    update_doc = {
        "id": _uuid(), "tenant_id": tenant_id, "incident_id": incident_id,
        "status": data.status or inc["status"],
        "message": data.message,
        "created_at": now, "author": user.get("id"),
    }
    await db.status_updates.insert_one(update_doc)
    patch = {"updated_at": now}
    if data.status:
        patch["status"] = data.status
        if data.status == "resolved":
            patch["resolved_at"] = now
    await db.status_incidents.update_one(
        {"tenant_id": tenant_id, "id": incident_id}, {"$set": patch})
    update_doc.pop("_id", None)
    return {"ok": True, **update_doc}


@router.post("/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: str, user: dict = Depends(require_tenant_owner_or_admin)):
    """Mark an incident resolved: closes the timeline, restores the linked
    component to operational, and posts a final public update."""
    db = get_db()
    tenant_id = user["tenant_id"]
    inc = await _get_incident(db, tenant_id, incident_id)
    now = _now_iso()
    await db.status_incidents.update_one(
        {"tenant_id": tenant_id, "id": incident_id},
        {"$set": {"status": "resolved", "resolved_at": now, "updated_at": now}},
    )
    if inc.get("component_id"):
        await db.status_components.update_one(
            {"tenant_id": tenant_id, "id": inc["component_id"]},
            {"$set": {"status": "operational", "updated_at": now}},
        )
    final_update = {
        "id": _uuid(), "tenant_id": tenant_id, "incident_id": incident_id,
        "status": "resolved",
        "message": "Resolved. All systems operational.",
        "created_at": now, "author": user.get("id"),
    }
    await db.status_updates.insert_one(final_update)
    final_update.pop("_id", None)
    return {"ok": True, "id": incident_id, "status": "resolved", "resolved_at": now,
            "final_update": final_update}


# ---------- synthetic monitors (tenant auth) ----------

@router.get("/monitors")
async def list_monitors(user: dict = Depends(require_tenant_user)):
    db = get_db()
    monitors = await db.status_monitors.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}
    ).sort("created_at", -1).to_list(1000)
    out = []
    for m in monitors:
        recent = await db.monitor_results.find(
            {"tenant_id": user["tenant_id"], "monitor_id": m["id"]}, {"_id": 0}
        ).sort("checked_at", -1).to_list(10)
        out.append({**m, "recent_results": recent})
    return {"monitors": out, "count": len(out)}


@router.post("/monitors")
async def create_monitor(data: MonitorIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Define a synthetic monitor against a component."""
    db = get_db()
    tenant_id = user["tenant_id"]
    comp = await db.status_components.find_one({"tenant_id": tenant_id, "id": data.component_id})
    if not comp:
        raise HTTPException(404, "Component not found")
    now = _now_iso()
    doc = {
        "id": _uuid(), "tenant_id": tenant_id,
        "name": data.name, "component_id": data.component_id,
        "check_type": data.check_type, "target": data.target,
        "interval_seconds": data.interval_seconds,
        "consecutive_failures": 0,
        "enabled": True,
        "created_at": now, "updated_at": now,
    }
    await db.status_monitors.insert_one(doc)
    doc.pop("_id", None)
    return {"ok": True, **doc}


@router.post("/monitors/{monitor_id}/check")
async def ingest_check(monitor_id: str, data: MonitorCheckIn,
                       user: dict = Depends(require_tenant_user)):
    """Ingest one synthetic check result.

    Failure counter logic: consecutive_failures increments on each failed
    check and resets to 0 on the first success. Hitting FAILURE_THRESHOLD (3)
    auto-opens an incident and pages the on-call (stubbed TODO(LIVE)).
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    monitor = await db.status_monitors.find_one({"tenant_id": tenant_id, "id": monitor_id})
    if not monitor:
        raise HTTPException(404, "Monitor not found")

    now = _now_iso()
    failures = 0 if data.success else (monitor.get("consecutive_failures", 0) + 1)
    await db.monitor_results.insert_one({
        "id": _uuid(), "tenant_id": tenant_id, "monitor_id": monitor_id,
        "success": data.success, "latency_ms": data.latency_ms,
        "detail": data.detail, "checked_at": now,
    })
    await db.status_monitors.update_one(
        {"tenant_id": tenant_id, "id": monitor_id},
        {"$set": {"consecutive_failures": failures, "updated_at": now}},
    )

    incident = None
    if failures >= FAILURE_THRESHOLD:
        monitor = {**monitor, "consecutive_failures": failures}
        incident = await _auto_open_incident(db, tenant_id, monitor, failures)

    return {
        "ok": True, "monitor_id": monitor_id, "success": data.success,
        "consecutive_failures": failures,
        "incident": (incident["id"] if incident else None),
    }
