"""Outbound voice AI (roadmap item #10) — a sales team that never sleeps.

Missed-call callbacks in 60 seconds, overdue-invoice reminder calls,
reschedule nudges, seasonal tune-up campaigns.

Every endpoint is tenant-scoped: tenant_id comes from the auth dependency,
never from client input. Reads need require_tenant_user, writes need
require_tenant_owner_or_admin (per contract).

Call execution itself is provider-side: launches enqueue priority-ordered
jobs into `voice_jobs` (a worker/cron drains them through the configured
VoiceProvider), while `voice_calls` keeps the per-call log. The default
provider is MockVoiceProvider (zero network); the Twilio skeleton in
services.outbound_voice is TODO(LIVE).

FCC consent guard: a campaign refuses to enqueue any number whose customer
record does not have voice_consent=True (unknown numbers count as
no-consent for proactive campaigns). Missed-call callbacks allow unknown
numbers (implied consent — the caller reached out first) but still refuse
an explicit opt-out.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin
from services.outbound_voice import (
    TEMPLATES,
    customer_consent,
    estimate_minutes,
    planned_call_order,
    render_script,
    template_variables,
)

logger = logging.getLogger("outbound_voice")

router = APIRouter(prefix="/tenants/voice", tags=["voice"])


# ---------------------------------------------------------------------------
# Pydantic models (kept here per contract; backend/models.py untouched)
# ---------------------------------------------------------------------------

class AudienceMember(BaseModel):
    customer_id: Optional[str] = Field(default=None, max_length=80)
    phone: str = Field(..., min_length=7, max_length=32)
    variables: dict[str, Any] = Field(default_factory=dict)
    priority: Optional[str] = Field(default=None, description="high|normal")


class CampaignCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    template_id: str = Field(..., description="One of the GET /templates ids")
    audience: list[AudienceMember] = Field(..., min_length=1, max_length=500)
    scheduled_at: Optional[str] = Field(default=None, description="ISO datetime")


class LaunchRequest(BaseModel):
    dry_run: bool = True


class MissedCallTrigger(BaseModel):
    customer_id: Optional[str] = Field(default=None, max_length=80)
    phone: str = Field(..., min_length=7, max_length=32)
    customer_name: Optional[str] = Field(default=None, max_length=80)
    shop_name: Optional[str] = Field(default="your shop", max_length=80)
    callback_number: Optional[str] = Field(default="", max_length=32)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tenant(user: dict) -> str:
    tid = (user or {}).get("tenant_id")
    if not tid:
        raise HTTPException(status_code=403, detail="No tenant assigned")
    return tid


async def _get_campaign(db, tenant_id: str, campaign_id: str) -> dict:
    doc = await db.voice_campaigns.find_one(
        {"tenant_id": tenant_id, "id": campaign_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return doc


async def _build_plan(db, tenant_id: str, campaign: dict) -> tuple[list[dict], list[dict]]:
    """Render + order the call plan; return (planned, blocked).

    planned: [{order, phone, customer_id, script, priority}]
    blocked: [{phone, reason}] — numbers failing the FCC consent guard.
    """
    tpl = TEMPLATES.get(campaign["template_id"])
    if not tpl:
        raise HTTPException(status_code=422, detail="Unknown template")
    ordered = planned_call_order([m for m in campaign["audience"]])
    planned: list[dict] = []
    blocked: list[dict] = []
    for i, member in enumerate(ordered):
        consent = await customer_consent(
            db, tenant_id, customer_id=member.get("customer_id"),
            phone=member.get("phone", ""))
        if consent is not True:
            blocked.append({
                "phone": member.get("phone"),
                "reason": ("opted_out" if consent is False else "no_consent_record"),
            })
            continue
        try:
            script = render_script(tpl["script"], member.get("variables") or {})
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        planned.append({
            "order": i,
            "phone": member["phone"],
            "customer_id": member.get("customer_id"),
            "script": script,
            "priority": member.get("priority") or "normal",
        })
    return planned, blocked


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

@router.get("/templates")
async def list_templates(user: dict = Depends(require_tenant_user)):
    _tenant(user)
    return [
        {"id": t["id"], "name": t["name"], "use_case": t["use_case"],
         "variables": t["variables"], "script": t["script"]}
        for t in TEMPLATES.values()
    ]


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------

@router.get("/campaigns")
async def list_campaigns(user: dict = Depends(require_tenant_user)):
    tid = _tenant(user)
    db = get_db()
    rows = await db.voice_campaigns.find({"tenant_id": tid}, {"_id": 0}).to_list(200)
    rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return rows


@router.post("/campaigns")
async def create_campaign(body: CampaignCreate,
                          user: dict = Depends(require_tenant_owner_or_admin)):
    tid = _tenant(user)
    try:
        template_variables(body.template_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Unknown template")
    db = get_db()
    doc = {
        "id": _uuid(),
        "tenant_id": tid,
        "name": body.name,
        "template_id": body.template_id,
        "audience": [m.model_dump() for m in body.audience],
        "scheduled_at": body.scheduled_at,
        "status": "draft",
        "created_at": _now_iso(),
        "created_by": user.get("id"),
    }
    await db.voice_campaigns.insert_one(doc)
    logger.info("voice campaign created tenant=%s id=%s n=%d",
                tid, doc["id"], len(doc["audience"]))
    return {k: v for k, v in doc.items() if k != "_id"}


@router.post("/campaigns/{campaign_id}/launch")
async def launch_campaign(campaign_id: str, body: LaunchRequest,
                          user: dict = Depends(require_tenant_owner_or_admin)):
    """dry_run=true -> exactly what WOULD happen, zero side effects.
    dry_run=false -> consent gate first, then enqueue priority-ordered
    jobs into voice_jobs + log rows into voice_calls."""
    tid = _tenant(user)
    db = get_db()
    campaign = await _get_campaign(db, tid, campaign_id)
    if campaign.get("status") not in ("draft", "scheduled"):
        raise HTTPException(status_code=409,
                            detail=f"campaign already {campaign.get('status')}")

    planned, blocked = await _build_plan(db, tid, campaign)
    phones_in_order = [p["phone"] for p in planned]

    if body.dry_run:
        return {
            "dry_run": True,
            "campaign_id": campaign_id,
            "call_list": planned,
            "order": phones_in_order,
            "estimated_minutes": estimate_minutes(len(planned)),
            "blocked": blocked,
        }

    if blocked:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "FCC consent guard: campaign refused — "
                           "these numbers lack voice consent",
                "blocked": blocked,
            })

    call_ids: list[str] = []
    for p in planned:
        call_id = _uuid()
        await db.voice_calls.insert_one({
            "id": call_id,
            "tenant_id": tid,
            "campaign_id": campaign_id,
            "phone": p["phone"],
            "customer_id": p.get("customer_id"),
            "script": p["script"],
            "priority": p["priority"],
            "status": "queued",
            "outcome": "pending",
            "duration_seconds": 0,
            "transcript": "[transcript will appear after the call completes]",
            "created_at": _now_iso(),
        })
        await db.voice_jobs.insert_one({
            "id": _uuid(),
            "tenant_id": tid,
            "kind": "campaign_call",
            "campaign_id": campaign_id,
            "call_id": call_id,
            "priority": p["priority"],
            "due_at": _now_iso(),  # enqueue now; worker drains in order
            "status": "queued",
            "created_at": _now_iso(),
        })
        call_ids.append(call_id)

    await db.voice_campaigns.update_one(
        {"tenant_id": tid, "id": campaign_id},
        {"$set": {"status": "launched", "launched_at": _now_iso(),
                  "enqueued": len(call_ids)}})
    logger.info("voice campaign launched tenant=%s id=%s enqueued=%d",
                tid, campaign_id, len(call_ids))
    return {"dry_run": False, "campaign_id": campaign_id,
            "enqueued": len(call_ids), "order": phones_in_order,
            "estimated_minutes": estimate_minutes(len(call_ids)),
            "call_ids": call_ids}


@router.get("/campaigns/{campaign_id}/calls")
async def campaign_call_log(campaign_id: str,
                            user: dict = Depends(require_tenant_user)):
    tid = _tenant(user)
    db = get_db()
    await _get_campaign(db, tid, campaign_id)
    rows = await db.voice_calls.find(
        {"tenant_id": tid, "campaign_id": campaign_id}, {"_id": 0}).to_list(500)
    rows.sort(key=lambda r: (r.get("priority") == "high" and 0 or 1,
                             r.get("created_at", "")))
    return rows


# ---------------------------------------------------------------------------
# Job queue (what the worker/cron drains)
# ---------------------------------------------------------------------------

@router.get("/jobs")
async def list_jobs(user: dict = Depends(require_tenant_user)):
    tid = _tenant(user)
    db = get_db()
    rows = await db.voice_jobs.find(
        {"tenant_id": tid, "status": "queued"}, {"_id": 0}).to_list(200)
    rows.sort(key=lambda r: (r.get("priority") == "high" and 0 or 1,
                             r.get("due_at", "")))
    return rows


# ---------------------------------------------------------------------------
# Missed-call trigger: priority callback job due within 60s
# ---------------------------------------------------------------------------

@router.post("/triggers/missed-call")
async def missed_call_trigger(body: MissedCallTrigger,
                              user: dict = Depends(require_tenant_owner_or_admin)):
    """Create a priority callback job due within 60 seconds.

    Execution is provider-side: a worker/cron drains voice_jobs and places
    the call through the configured VoiceProvider. This endpoint only
    creates the job.
    """
    tid = _tenant(user)
    db = get_db()
    consent = await customer_consent(db, tid, customer_id=body.customer_id,
                                     phone=body.phone)
    if consent is False:
        raise HTTPException(
            status_code=409,
            detail="FCC consent guard: customer opted out of voice calls")

    tpl = TEMPLATES["missed_call_callback"]
    name = body.customer_name
    if not name and body.customer_id:
        row = await db.customers.find_one(
            {"tenant_id": tid, "id": body.customer_id}, {"_id": 0, "name": 1})
        name = (row or {}).get("name")
    script = render_script(tpl["script"], {
        "customer_name": name or "there",
        "shop_name": body.shop_name or "your shop",
        "callback_number": body.callback_number or "",
    })
    due_at = datetime.now(timezone.utc) + timedelta(seconds=60)
    job = {
        "id": _uuid(),
        "tenant_id": tid,
        "kind": "missed_call_callback",
        "priority": "high",
        "customer_id": body.customer_id,
        "phone": body.phone,
        "script": script,
        "due_at": due_at.isoformat(),
        "status": "queued",
        "created_at": _now_iso(),
    }
    await db.voice_jobs.insert_one(job)
    logger.info("missed-call callback queued tenant=%s phone=%s due=%s",
                tid, body.phone, job["due_at"])
    return {k: v for k, v in job.items() if k != "_id"}
