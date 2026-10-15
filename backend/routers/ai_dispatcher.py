"""AI dispatcher: the dispatch board assigns itself; a human approves.

Builds on the dispatch data model (appointments = jobs, schedule_staff =
techs, tech_locations = GPS) without duplicating it. Suggestions are persisted
in the tenant-scoped `dispatch_suggestions` collection so an approval can
reference them by id. Assignment writes go through the existing dispatch
`_apply_move` path — never a parallel write.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin
from routers.dispatch import _apply_move
from services.ai_dispatcher import (
    suggest_for_day,
    MIN_AUTO_ASSIGN_SCORE,
)

logger = logging.getLogger("ai_dispatcher")

router = APIRouter(prefix="/tenants/ai-dispatcher", tags=["ai-dispatcher"])

# Jobs we will ever suggest an assignment for (same "bookable" set the board shows).
_SUGGESTIBLE_STATUSES = {"scheduled", "confirmed"}
# Everything that counts as "already on someone's day" for conflict/load math.
_DAY_STATUSES = {"scheduled", "confirmed", "en_route", "on_site"}


def _parse_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise HTTPException(400, "date must be YYYY-MM-DD")


def _job_day(job: dict):
    try:
        dt = datetime.fromisoformat((job.get("start_at") or "").replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.date()


def _job_card(job: dict) -> dict:
    return {
        "id": job.get("id"),
        "customer_name": job.get("customer_name", ""),
        "service_name": job.get("service_name", ""),
        "start_at": job.get("start_at"),
        "end_at": job.get("end_at"),
        "status": job.get("status", "scheduled"),
        "staff_id": job.get("staff_id") or "",
        "staff": job.get("staff") or "",
    }


async def _load_day(db, tenant_id: str, day):
    """Unassigned suggestible jobs, techs, locations, and per-tech day loads."""
    appts = await db.appointments.find(
        {"tenant_id": tenant_id, "status": {"$in": sorted(_DAY_STATUSES)}},
        {"_id": 0},
    ).to_list(2000)
    unassigned, jobs_by_tech = [], {}
    for a in appts:
        if _job_day(a) != day:
            continue
        tech_id = a.get("staff_id") or ""
        if tech_id and a.get("status") in _DAY_STATUSES:
            jobs_by_tech.setdefault(tech_id, []).append(a)
        if not tech_id and a.get("status") in _SUGGESTIBLE_STATUSES:
            unassigned.append(a)
    unassigned.sort(key=lambda j: j.get("start_at") or "")
    techs = await db.schedule_staff.find({"tenant_id": tenant_id}, {"_id": 0}).sort("name", 1).to_list(500)
    locs = await db.tech_locations.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(500)
    return unassigned, techs, {l.get("tech_id"): l for l in locs}, jobs_by_tech


def _suggestion_doc(tenant_id: str, date: str, job: dict, cand: dict,
                    alternates: list, status: str = "pending") -> dict:
    return {
        "id": f"sug_{_uuid()}",
        "tenant_id": tenant_id,
        "date": date,
        "job_id": job.get("id"),
        "tech_id": cand["tech_id"],
        "score": cand["score"],
        "reasons": cand["reasons"],
        "alternates": [
            {"tech_id": a["tech_id"], "tech_name": a["tech_name"], "score": a["score"]}
            for a in alternates
        ],
        "status": status,
        "created_at": _now_iso(),
    }


def _suggestion_view(doc: dict, job: dict, tech_name: str) -> dict:
    return {
        "id": doc["id"],
        "date": doc["date"],
        "job_id": doc["job_id"],
        "job": _job_card(job) if job else {"id": doc["job_id"]},
        "tech_id": doc["tech_id"],
        "tech_name": tech_name,
        "score": doc["score"],
        "reasons": doc.get("reasons", []),
        "alternates": doc.get("alternates", []),
        "status": doc.get("status", "pending"),
    }


async def _persist_plan(db, tenant_id: str, date: str, plan: list) -> list:
    """Expire yesterday's pending suggestions for the date and store the new plan."""
    await db.dispatch_suggestions.update_many(
        {"tenant_id": tenant_id, "date": date, "status": "pending"},
        {"$set": {"status": "expired", "updated_at": _now_iso()}},
    )
    docs = [_suggestion_doc(tenant_id, date, item["job"], item["candidate"], item["alternates"])
            for item in plan]
    for doc in docs:
        await db.dispatch_suggestions.insert_one(doc)
    return docs


def _build_plan(unassigned: list, techs: list, locations: dict,
                jobs_by_tech: dict, min_score: float = MIN_AUTO_ASSIGN_SCORE) -> list:
    """Top suggestion per job, filtered to the auto-assign score floor."""
    plan = []
    for entry in suggest_for_day(unassigned, techs, locations, jobs_by_tech):
        cands = entry["candidates"]
        if not cands or cands[0]["score"] < min_score:
            continue
        job = next(j for j in unassigned if j.get("id") == entry["job_id"])
        plan.append({"job": job, "candidate": cands[0], "alternates": cands[1:3]})
    return plan


@router.get("/suggestions")
async def suggestions(date: str, user: dict = Depends(require_tenant_user)):
    """Ranked assignment suggestions for the day's unassigned jobs.

    Regenerates and persists the suggestion set for (tenant, date); older
    pending suggestions for the date are expired first.
    """
    day = _parse_date(date)
    db = get_db()
    tenant_id = user["tenant_id"]
    unassigned, techs, locations, jobs_by_tech = await _load_day(db, tenant_id, day)
    if not techs:
        return {"date": date, "suggestions": [], "unassigned_count": len(unassigned),
                "note": "No technicians on staff yet."}
    plan = _build_plan(unassigned, techs, locations, jobs_by_tech, min_score=0.0)
    docs = await _persist_plan(db, tenant_id, date, plan)
    by_job = {j.get("id"): j for j in unassigned}
    by_tech = {t.get("id"): t for t in techs}
    return {
        "date": date,
        "unassigned_count": len(unassigned),
        "suggestions": [
            _suggestion_view(doc, by_job.get(doc["job_id"]),
                             (by_tech.get(doc["tech_id"]) or {}).get("name", ""))
            for doc in docs
        ],
    }


class AutoAssignIn(BaseModel):
    date: str
    dry_run: bool = True


@router.post("/auto-assign")
async def auto_assign(data: AutoAssignIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Full-day auto-assign plan. dry_run=true previews; false applies everything.

    Only jobs whose top suggestion meets the score floor are included.
    """
    day = _parse_date(data.date)
    db = get_db()
    tenant_id = user["tenant_id"]
    unassigned, techs, locations, jobs_by_tech = await _load_day(db, tenant_id, day)
    plan = _build_plan(unassigned, techs, locations, jobs_by_tech)

    if data.dry_run:
        return {
            "date": data.date,
            "dry_run": True,
            "writes": 0,
            "plan": [
                {"job_id": p["job"]["id"], "tech_id": p["candidate"]["tech_id"],
                 "tech_name": p["candidate"]["tech_name"], "score": p["candidate"]["score"],
                 "reasons": p["candidate"]["reasons"]}
                for p in plan
            ],
        }

    applied, failed = [], []
    docs = await _persist_plan(db, tenant_id, data.date, plan)
    for doc in docs:
        try:
            # Reuse the dispatch assignment path: keeps time, syncs staff names,
            # rejects completed/canceled jobs.
            job = await _apply_move(db, tenant_id, doc["job_id"], doc["tech_id"], None)
            await db.dispatch_suggestions.update_one(
                {"id": doc["id"], "tenant_id": tenant_id},
                {"$set": {"status": "accepted", "applied_at": _now_iso()}},
            )
            applied.append({"suggestion_id": doc["id"], "job": _job_card(job)})
        except HTTPException as e:
            failed.append({"suggestion_id": doc["id"], "job_id": doc["job_id"],
                           "error": e.detail})
            logger.warning("auto-assign failed for job %s: %s", doc["job_id"], e.detail)
    return {"date": data.date, "dry_run": False, "writes": len(applied),
            "applied": applied, "failed": failed}


@router.post("/suggestions/{suggestion_id}/accept")
async def accept_suggestion(suggestion_id: str, user: dict = Depends(require_tenant_owner_or_admin)):
    """Approve one suggestion: applies the assignment through the dispatch path."""
    db = get_db()
    tenant_id = user["tenant_id"]
    sug = await db.dispatch_suggestions.find_one(
        {"id": suggestion_id, "tenant_id": tenant_id}, {"_id": 0})
    if not sug:
        raise HTTPException(404, "Suggestion not found")
    if sug.get("status") != "pending":
        raise HTTPException(400, f"Suggestion is already {sug.get('status')}")

    # Write through the existing dispatch assignment path (keeps the job's time).
    job = await _apply_move(db, tenant_id, sug["job_id"], sug["tech_id"], None)

    await db.dispatch_suggestions.update_one(
        {"id": suggestion_id, "tenant_id": tenant_id},
        {"$set": {"status": "accepted", "applied_at": _now_iso()}},
    )
    # Supersede the other pending suggestions for the same job.
    await db.dispatch_suggestions.update_many(
        {"tenant_id": tenant_id, "job_id": sug["job_id"], "date": sug.get("date"),
         "status": "pending"},
        {"$set": {"status": "expired", "updated_at": _now_iso()}},
    )
    return {"suggestion_id": suggestion_id, "job": _job_card(job)}
