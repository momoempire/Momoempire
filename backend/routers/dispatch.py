"""Dispatch board: day view of jobs (appointments) per technician, drag-and-drop assignment.

Jobs ARE appointments — this router reuses the `appointments` collection and the
`schedule_staff` collection. No parallel data model.

Assignment is tracked in `staff_id` (schedule_staff.id); the legacy `staff`
name string is kept in sync for the existing availability/conflict logic.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso
from security import require_tenant_user
from routers.call_to_payment import JobCompleteIn, mark_job_complete

router = APIRouter(prefix="/tenants/dispatch", tags=["dispatch"])

# scheduled/confirmed -> en_route -> on_site -> completed ; canceled is terminal from anywhere active.
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "scheduled": {"en_route", "on_site", "canceled"},
    "confirmed": {"en_route", "on_site", "canceled"},
    "en_route": {"on_site", "canceled"},
    "on_site": {"completed", "canceled"},
    "completed": set(),
    "canceled": set(),
    "no_show": set(),
}

_BOARD_STATUSES = {"scheduled", "confirmed", "en_route", "on_site", "completed"}


def _parse_dt(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(400, f"Invalid ISO datetime: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _job_card(appt: dict) -> dict:
    return {
        "id": appt.get("id"),
        "customer_name": appt.get("customer_name", ""),
        "customer_phone": appt.get("customer_phone", ""),
        "service_name": appt.get("service_name", ""),
        "start_at": appt.get("start_at"),
        "end_at": appt.get("end_at"),
        "status": appt.get("status", "scheduled"),
        "staff_id": appt.get("staff_id") or "",
        "staff": appt.get("staff") or "",
        "notes": appt.get("notes", ""),
        "quote_id": appt.get("quote_id"),
    }


async def _get_job(db, job_id: str, tenant_id: str) -> dict:
    job = await db.appointments.find_one({"id": job_id, "tenant_id": tenant_id}, {"_id": 0})
    if not job:
        raise HTTPException(404, "Job not found")
    return job


async def _get_tech(db, tech_id: str, tenant_id: str) -> dict:
    tech = await db.schedule_staff.find_one({"id": tech_id, "tenant_id": tenant_id}, {"_id": 0})
    if not tech:
        raise HTTPException(404, "Technician not found")
    return tech


@router.get("/board")
async def board(date: str, user: dict = Depends(require_tenant_user)):
    """Day board: unassigned jobs + one column per tech with their jobs in time order."""
    try:
        day = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    db = get_db()
    tenant_id = user["tenant_id"]

    techs = await db.schedule_staff.find({"tenant_id": tenant_id}, {"_id": 0}).sort("name", 1).to_list(200)
    columns = [{"id": t["id"], "name": t.get("name", ""), "role": t.get("role", ""), "jobs": []} for t in techs]
    by_tech = {t["id"]: col for t, col in zip(techs, columns)}

    # Fetch active appointments and filter to the day in Python — start_at is stored
    # in mixed ISO formats/timezones, so string-prefix matching would miss edge cases.
    appts = await db.appointments.find(
        {"tenant_id": tenant_id, "status": {"$in": sorted(_BOARD_STATUSES)}},
        {"_id": 0},
    ).to_list(2000)
    unassigned = []
    for a in appts:
        try:
            start = _parse_dt(a.get("start_at", ""))
        except HTTPException:
            continue
        if start.date() != day:
            continue
        card = _job_card(a)
        tech_id = a.get("staff_id") or ""
        if tech_id and tech_id in by_tech:
            by_tech[tech_id]["jobs"].append(card)
        else:
            unassigned.append(card)

    for col in columns:
        col["jobs"].sort(key=lambda j: j.get("start_at") or "")
    unassigned.sort(key=lambda j: j.get("start_at") or "")
    return {"date": date, "techs": columns, "unassigned": unassigned}


class AssignIn(BaseModel):
    job_id: str
    tech_id: str
    start_at: str  # ISO datetime for the slot


class MoveIn(BaseModel):
    job_id: str
    tech_id: Optional[str] = None  # None/"" unassigns the job
    start_at: Optional[str] = None  # None keeps the current time


async def _apply_move(db, tenant_id: str, job_id: str, tech_id: Optional[str], start_at: Optional[str]) -> dict:
    job = await _get_job(db, job_id, tenant_id)
    if job.get("status") in {"completed", "canceled"}:
        raise HTTPException(400, f"Cannot move a {job['status']} job")

    update: dict = {"updated_at": _now_iso()}
    if tech_id is not None:
        if tech_id:
            tech = await _get_tech(db, tech_id, tenant_id)
            update["staff_id"] = tech["id"]
            update["staff"] = tech.get("name", "")
        else:
            update["staff_id"] = ""
            update["staff"] = ""
    if start_at is not None:
        new_start = _parse_dt(start_at)
        try:
            old_start = _parse_dt(job.get("start_at", ""))
            old_end = _parse_dt(job.get("end_at", ""))
            duration = old_end - old_start
        except HTTPException:
            duration = timedelta(minutes=60)
        if duration.total_seconds() <= 0:
            duration = timedelta(minutes=60)
        update["start_at"] = new_start.isoformat()
        update["end_at"] = (new_start + duration).isoformat()

    await db.appointments.update_one({"id": job_id, "tenant_id": tenant_id}, {"$set": update})
    return await db.appointments.find_one({"id": job_id, "tenant_id": tenant_id}, {"_id": 0})


@router.post("/assign")
async def assign(data: AssignIn, user: dict = Depends(require_tenant_user)):
    """Assign a job to a tech at a time slot."""
    db = get_db()
    job = await _apply_move(db, user["tenant_id"], data.job_id, data.tech_id, data.start_at)
    return _job_card(job)


@router.post("/move")
async def move(data: MoveIn, user: dict = Depends(require_tenant_user)):
    """Drag-and-drop backend: move a job to another tech and/or time slot.
    tech_id "" unassigns; omitted fields are left unchanged."""
    db = get_db()
    job = await _apply_move(db, user["tenant_id"], data.job_id, data.tech_id, data.start_at)
    return _job_card(job)


class StatusIn(BaseModel):
    job_id: str
    status: str = Field(pattern="^(en_route|on_site|completed|canceled)$")


@router.post("/status")
async def set_status(data: StatusIn, user: dict = Depends(require_tenant_user)):
    """Advance a job through scheduled -> en_route -> on_site -> completed.
    Invalid transitions return 400. Completion shares logic with /c2p/jobs/complete."""
    db = get_db()
    tenant_id = user["tenant_id"]
    job = await _get_job(db, data.job_id, tenant_id)
    current = job.get("status", "scheduled")
    if data.status not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise HTTPException(400, f"Cannot transition job from {current!r} to {data.status!r}")
    if data.status == "completed":
        # Share completion logic (invoice flip etc.) with the call-to-payment flow.
        result = await mark_job_complete(JobCompleteIn(appointment_id=data.job_id), user)
        job = await db.appointments.find_one({"id": data.job_id, "tenant_id": tenant_id}, {"_id": 0})
        card = _job_card(job)
        card["invoice_id"] = (result or {}).get("invoice_id")
        return card
    now = _now_iso()
    await db.appointments.update_one(
        {"id": data.job_id, "tenant_id": tenant_id},
        {"$set": {"status": data.status, "updated_at": now,
                  f"{data.status}_at": now}},
    )
    job = await db.appointments.find_one({"id": data.job_id, "tenant_id": tenant_id}, {"_id": 0})
    return _job_card(job)
