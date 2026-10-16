"""Custom job forms / checklists (#36).

Structured on-site checklists techs complete from the field.

- Templates: reusable checklists per trade. POST/GET/PUT/DELETE /templates.
  Three trade defaults (HVAC install QC, plumbing safety, general job
  closeout) are lazily seeded once per tenant, idempotently.
- Instances: a template applied to a specific job (jobs are the read-only
  `appointments` collection; we verify the job exists but never modify it).
  POST /instances, GET /instances?job_id=, GET /instances/{id}.
- Field completion: POST /instances/{id}/items/{item_id}/complete with
  {done, value, note, photo_url} — item types: check | text | photo | number.
- Gate: GET /instances/{id}/gate returns {can_complete, blocking, progress}
  so job-completion UIs can refuse to close a job until required items are
  done. Advisory API only: this router never writes to the jobs collection.

All data is tenant-isolated via tenant_id. Zero live network calls.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user

router = APIRouter(prefix="/checklists", tags=["checklists"])

ITEM_TYPES = {"check", "text", "photo", "number"}
PROJ = {"_id": 0}


# ---------- pydantic models (module-local; models.py untouched) ----------

class TemplateItemIn(BaseModel):
    label: str
    type: str = "check"
    required: bool = False


class TemplateCreate(BaseModel):
    name: str
    trade: str = ""
    items: List[TemplateItemIn] = Field(default_factory=list)


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    trade: Optional[str] = None
    items: Optional[List[TemplateItemIn]] = None


class InstanceCreate(BaseModel):
    template_id: str
    job_id: str


class ItemCompleteIn(BaseModel):
    done: bool = True
    value: Optional[str] = None
    note: Optional[str] = None
    photo_url: Optional[str] = None


# ---------- defaults ----------

DEFAULT_TEMPLATES: List[Dict[str, Any]] = [
    {
        "name": "HVAC install QC",
        "trade": "hvac",
        "items": [
            {"label": "Thermostat mounted level and wired to system", "type": "check", "required": True},
            {"label": "Refrigerant charge verified within manufacturer spec", "type": "check", "required": True},
            {"label": "All line-set connections leak-tested", "type": "check", "required": True},
            {"label": "Drain line clear and draining to safe location", "type": "check", "required": True},
            {"label": "Static pressure (in. w.c.)", "type": "number", "required": False},
            {"label": "Filter size / notes", "type": "text", "required": False},
            {"label": "Completed install photo", "type": "photo", "required": True},
        ],
    },
    {
        "name": "Plumbing safety check",
        "trade": "plumbing",
        "items": [
            {"label": "Main water shutoff tagged and accessible", "type": "check", "required": True},
            {"label": "No visible leaks at joints or fixtures", "type": "check", "required": True},
            {"label": "Water heater TPR valve present and draining to safe location", "type": "check", "required": True},
            {"label": "Fixture(s) serviced", "type": "text", "required": False},
            {"label": "Under-sink plumbing photo", "type": "photo", "required": False},
        ],
    },
    {
        "name": "General job closeout",
        "trade": "",
        "items": [
            {"label": "Work area swept and debris removed", "type": "check", "required": True},
            {"label": "Customer walked through completed work", "type": "check", "required": True},
            {"label": "Customer signature / name", "type": "text", "required": False},
            {"label": "Total hours on site", "type": "number", "required": False},
            {"label": "Final walkthrough photo", "type": "photo", "required": False},
        ],
    },
]


# ---------- helpers ----------

def _new_item_id() -> str:
    return f"it_{_uuid()}"


def _validate_items(items: List[TemplateItemIn]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in items:
        itype = (it.type or "check").strip().lower()
        if itype not in ITEM_TYPES:
            raise HTTPException(400, f"Unknown item type '{it.type}': must be one of {sorted(ITEM_TYPES)}")
        label = (it.label or "").strip()
        if not label:
            raise HTTPException(400, "Item label is required")
        out.append({"id": _new_item_id(), "label": label, "type": itype, "required": bool(it.required)})
    return out


def _template_view(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "name": doc.get("name", ""),
        "trade": doc.get("trade", ""),
        "builtin": bool(doc.get("builtin")),
        "items": [dict(i) for i in (doc.get("items") or [])],
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


def _item_state_from_template(item: dict) -> dict:
    return {
        "item_id": item.get("id"),
        "label": item.get("label", ""),
        "type": item.get("type", "check"),
        "required": bool(item.get("required")),
        "done": False,
        "value": None,
        "note": None,
        "photo_url": None,
        "completed_at": None,
        "completed_by": None,
    }


def _progress_of(items: List[dict]) -> dict:
    required = [i for i in items if i.get("required")]
    done = [i for i in required if i.get("done")]
    return {"required_done": len(done), "required_total": len(required)}


def _blocking_labels(items: List[dict]) -> List[str]:
    return [i.get("label", "") for i in items if i.get("required") and not i.get("done")]


def _instance_view(doc: dict) -> dict:
    items = [dict(i) for i in (doc.get("items") or [])]
    prog = _progress_of(items)
    return {
        "id": doc.get("id"),
        "template_id": doc.get("template_id"),
        "template_name": doc.get("template_name", ""),
        "job_id": doc.get("job_id"),
        "items": items,
        "progress": prog,
        **({"gate": doc.get("gate")} if doc.get("gate") else {}),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


async def _ensure_seed(db, tenant_id: str) -> None:
    """Lazy idempotent seeding: insert the 3 trade defaults exactly once
    per tenant (only when the tenant has no builtin templates yet)."""
    existing = await db.checklist_templates.find_one(
        {"tenant_id": tenant_id, "builtin": True}, PROJ)
    if existing:
        return
    now = _now_iso()
    for spec in DEFAULT_TEMPLATES:
        items = [_template_item_dict(i) for i in spec["items"]]
        await db.checklist_templates.insert_one({
            "id": f"clt_{_uuid()}",
            "tenant_id": tenant_id,
            "name": spec["name"],
            "trade": spec.get("trade", ""),
            "builtin": True,
            "items": items,
            "created_at": now,
            "updated_at": now,
        })


def _template_item_dict(spec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": _new_item_id(),
        "label": spec["label"],
        "type": spec["type"],
        "required": bool(spec.get("required")),
    }


async def _get_template(db, tenant_id: str, template_id: str) -> dict:
    doc = await db.checklist_templates.find_one(
        {"id": template_id, "tenant_id": tenant_id}, PROJ)
    if not doc:
        raise HTTPException(404, "Checklist template not found")
    return doc


async def _get_instance(db, tenant_id: str, instance_id: str) -> dict:
    doc = await db.checklist_instances.find_one(
        {"id": instance_id, "tenant_id": tenant_id}, PROJ)
    if not doc:
        raise HTTPException(404, "Checklist instance not found")
    return doc


async def _get_job(db, tenant_id: str, job_id: str) -> dict:
    """Read-only job existence check (jobs live in `appointments`)."""
    job = await db.appointments.find_one({"id": job_id, "tenant_id": tenant_id}, PROJ)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def _validate_item_value(item_type: str, required: bool, body: ItemCompleteIn) -> None:
    """A required item needs evidence when completed: check -> done=True;
    text/number -> non-empty value; photo -> photo_url. Re-opening
    (done=false) is always allowed; the gate re-blocks the job."""
    if not body.done:
        return
    if not required:
        return
    if item_type == "check":
        return
    if item_type in {"text", "number"} and not (body.value or "").strip():
        raise HTTPException(400, "Required item needs a value")
    if item_type == "photo" and not (body.photo_url or "").strip():
        raise HTTPException(400, "Required item needs a photo_url")


# ---------- templates ----------

@router.get("/templates")
async def list_templates(trade: str = "", user: dict = Depends(require_tenant_user)):
    """All templates for the tenant; the 3 trade defaults are seeded on first use."""
    db = get_db()
    await _ensure_seed(db, user["tenant_id"])
    query: dict = {"tenant_id": user["tenant_id"]}
    if trade.strip():
        query["trade"] = trade.strip().lower()
    docs = await db.checklist_templates.find(query, PROJ).to_list(500)
    return {"templates": [_template_view(d) for d in docs], "count": len(docs)}


@router.post("/templates", status_code=201)
async def create_template(body: TemplateCreate, user: dict = Depends(require_tenant_user)):
    if not (body.name or "").strip():
        raise HTTPException(400, "Template name is required")
    db = get_db()
    await _ensure_seed(db, user["tenant_id"])
    now = _now_iso()
    doc = {
        "id": f"clt_{_uuid()}",
        "tenant_id": user["tenant_id"],
        "name": body.name.strip(),
        "trade": (body.trade or "").strip().lower(),
        "builtin": False,
        "items": _validate_items(body.items or []),
        "created_at": now,
        "updated_at": now,
    }
    await db.checklist_templates.insert_one(doc)
    return _template_view(doc)


@router.put("/templates/{template_id}")
async def update_template(template_id: str, body: TemplateUpdate,
                          user: dict = Depends(require_tenant_user)):
    db = get_db()
    await _ensure_seed(db, user["tenant_id"])
    await _get_template(db, user["tenant_id"], template_id)
    updates: dict = {"updated_at": _now_iso()}
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(400, "Template name cannot be empty")
        updates["name"] = body.name.strip()
    if body.trade is not None:
        updates["trade"] = body.trade.strip().lower()
    if body.items is not None:
        updates["items"] = _validate_items(body.items)
    await db.checklist_templates.update_one(
        {"id": template_id, "tenant_id": user["tenant_id"]}, {"$set": updates})
    doc = await db.checklist_templates.find_one(
        {"id": template_id, "tenant_id": user["tenant_id"]}, PROJ)
    return _template_view(doc)


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    await _ensure_seed(db, user["tenant_id"])
    await _get_template(db, user["tenant_id"], template_id)
    # Builtin templates protect themselves by being re-seedable: removing all
    # builtins simply re-seeds them, so deletion is a no-op trap we refuse.
    doc = await db.checklist_templates.find_one(
        {"id": template_id, "tenant_id": user["tenant_id"]}, PROJ)
    if doc.get("builtin"):
        raise HTTPException(400, "Built-in templates cannot be deleted")
    result = await db.checklist_templates.delete_one(
        {"id": template_id, "tenant_id": user["tenant_id"]})
    return {"ok": True, "deleted": result.deleted_count}


# ---------- instances ----------

@router.post("/instances", status_code=201)
async def create_instance(body: InstanceCreate, user: dict = Depends(require_tenant_user)):
    """Start a per-job checklist from a template. The job must exist in the
    tenant's appointments (read-only check)."""
    db = get_db()
    await _ensure_seed(db, user["tenant_id"])
    await _get_job(db, user["tenant_id"], body.job_id)
    template = await _get_template(db, user["tenant_id"], body.template_id)
    now = _now_iso()
    doc = {
        "id": f"cli_{_uuid()}",
        "tenant_id": user["tenant_id"],
        "template_id": template["id"],
        "template_name": template.get("name", ""),
        "job_id": body.job_id,
        "items": [_item_state_from_template(i) for i in (template.get("items") or [])],
        "created_at": now,
        "updated_at": now,
    }
    await db.checklist_instances.insert_one(doc)
    return _instance_view(doc)


@router.get("/instances")
async def list_instances(job_id: str = "", user: dict = Depends(require_tenant_user)):
    db = get_db()
    query: dict = {"tenant_id": user["tenant_id"]}
    if job_id.strip():
        query["job_id"] = job_id.strip()
    docs = await db.checklist_instances.find(query, PROJ).to_list(500)
    return {"instances": [_instance_view(d) for d in docs], "count": len(docs)}


@router.get("/instances/{instance_id}")
async def get_instance(instance_id: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    return _instance_view(await _get_instance(db, user["tenant_id"], instance_id))


@router.delete("/instances/{instance_id}")
async def delete_instance(instance_id: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    await _get_instance(db, user["tenant_id"], instance_id)
    result = await db.checklist_instances.delete_one(
        {"id": instance_id, "tenant_id": user["tenant_id"]})
    return {"ok": True, "deleted": result.deleted_count}


# ---------- field completion ----------

@router.post("/instances/{instance_id}/items/{item_id}/complete")
async def complete_item(instance_id: str, item_id: str, body: ItemCompleteIn,
                        user: dict = Depends(require_tenant_user)):
    """Mark one checklist item done (or re-open it with done=false). Required
    items need evidence: a value for text/number, a photo_url for photo."""
    db = get_db()
    doc = await _get_instance(db, user["tenant_id"], instance_id)
    items = [dict(i) for i in (doc.get("items") or [])]
    target = next((i for i in items if i.get("item_id") == item_id), None)
    if not target:
        raise HTTPException(404, "Checklist item not found")

    _validate_item_value(target.get("type", "check"), bool(target.get("required")), body)

    now = _now_iso()
    for i in items:
        if i.get("item_id") == item_id:
            i["done"] = bool(body.done)
            i["value"] = ((body.value or "").strip() or None) if body.done else None
            i["note"] = (body.note or "").strip() or None
            i["photo_url"] = (body.photo_url or "").strip() or None
            i["completed_at"] = now if body.done else None
            i["completed_by"] = user.get("id") if body.done else None

    await db.checklist_instances.update_one(
        {"id": instance_id, "tenant_id": user["tenant_id"]},
        {"$set": {"items": items, "updated_at": now}})
    doc["items"] = items
    return _instance_view(doc)


# ---------- gate ----------

@router.get("/instances/{instance_id}/gate")
async def job_completion_gate(instance_id: str, user: dict = Depends(require_tenant_user)):
    """Advisory gate for job-completion UIs: can_complete is True only when
    every required item is done. blocking lists the labels still open."""
    db = get_db()
    doc = await _get_instance(db, user["tenant_id"], instance_id)
    items = [dict(i) for i in (doc.get("items") or [])]
    blocking = _blocking_labels(items)
    progress = _progress_of(items)
    return {
        "instance_id": instance_id,
        "job_id": doc.get("job_id"),
        "can_complete": len(blocking) == 0,
        "blocking": blocking,
        "progress": progress,
    }
