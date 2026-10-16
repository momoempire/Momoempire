"""Zero-touch job lifecycle: lead -> paid without human hands.

Phase E (autonomous operations). An orchestration engine that advances jobs
(``appointments`` docs) through lifecycle stages automatically, firing
pluggable stage actions (SMS updates, invoice-draft generation, review
requests) at each transition while keeping a human override path and a full
audit trail.

Data model:
  - ``lifecycle_pipelines``: per-tenant pipeline definitions (stages + the
    auto-actions each stage fires on entry).
  - ``job_lifecycle_events``: audit log of every transition, override, and
    fired action, per job per tenant.

External sends are stub-logged (TODO(LIVE)): when the platform wires a live
SMS/voice provider, replace ``_action_send_sms`` / friends with the real
call; the event record already captures fired/skipped/failed.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin

logger = logging.getLogger("zero_touch")

router = APIRouter(prefix="/api/zero-touch", tags=["zero-touch"])

# --------------------------------------------------------------------------
# Lifecycle model
# --------------------------------------------------------------------------

STAGES = ["lead", "booked", "dispatched", "en_route", "on_site",
          "complete", "invoiced", "paid", "canceled"]

# The linear happy-path plus cancellation from any active stage.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "lead": {"booked", "canceled"},
    "booked": {"dispatched", "canceled"},
    "dispatched": {"en_route", "canceled"},
    "en_route": {"on_site", "canceled"},
    "on_site": {"complete", "canceled"},
    "complete": {"invoiced", "canceled"},
    "invoiced": {"paid", "canceled"},
    "paid": set(),
    "canceled": set(),
}

# Map lifecycle stages onto the appointment status vocabulary when it helps
# the dispatch board stay in sync.
_APPT_STATUS_MAP = {
    "booked": "scheduled",
    "dispatched": "confirmed",
    "en_route": "en_route",
    "on_site": "on_site",
    "complete": "completed",
}

STAGE_LABELS = {
    "lead": "Lead",
    "booked": "Booked",
    "dispatched": "Dispatched",
    "en_route": "En route",
    "on_site": "On site",
    "complete": "Complete",
    "invoiced": "Invoiced",
    "paid": "Paid",
    "canceled": "Canceled",
}

ACTION_NAMES = {"send_sms", "create_invoice_draft", "request_review"}


def default_pipeline() -> dict:
    """The seeded home-services pipeline: stages + entry actions."""
    stages = [
        {"stage": "lead", "label": STAGE_LABELS["lead"], "actions": []},
        {"stage": "booked", "label": STAGE_LABELS["booked"], "actions": [
            {"name": "send_sms", "template": "booking_confirmed"},
        ]},
        {"stage": "dispatched", "label": STAGE_LABELS["dispatched"], "actions": [
            {"name": "send_sms", "template": "tech_assigned"},
        ]},
        {"stage": "en_route", "label": STAGE_LABELS["en_route"], "actions": [
            {"name": "send_sms", "template": "tech_en_route"},
        ]},
        {"stage": "on_site", "label": STAGE_LABELS["on_site"], "actions": []},
        {"stage": "complete", "label": STAGE_LABELS["complete"], "actions": [
            {"name": "send_sms", "template": "job_complete"},
        ]},
        {"stage": "invoiced", "label": STAGE_LABELS["invoiced"], "actions": [
            {"name": "create_invoice_draft"},
        ]},
        {"stage": "paid", "label": STAGE_LABELS["paid"], "actions": [
            {"name": "request_review"},
        ]},
        {"stage": "canceled", "label": STAGE_LABELS["canceled"], "actions": []},
    ]
    return {"name": "Home Services Default", "stages": stages}


def _job_stage(job: dict) -> str:
    """Lifecycle stage of a job doc; legacy jobs default to 'lead'."""
    return job.get("lifecycle_stage") or "lead"


# --------------------------------------------------------------------------
# Pluggable stage actions (stubs; TODO(LIVE) where a provider is needed)
# --------------------------------------------------------------------------

async def _action_send_sms(db, tenant_id: str, job: dict, spec: dict) -> dict:
    """Stub: record an outbound customer SMS in the audit trail.

    TODO(LIVE): when the tenant has a live SMS provider configured, call it
    here (e.g. the Twilio integration) and store the provider message SID.
    """
    phone = (job.get("customer_phone") or "").strip()
    if not phone:
        return {"name": "send_sms", "status": "skipped",
                "detail": "no customer phone on job"}
    template = spec.get("template", "update")
    body = _sms_templates().get(template, template)
    logger.info("[zero-touch sms -> %s] %s (job %s)", phone, body, job.get("id"))
    await db.job_lifecycle_events.insert_one({
        "id": _uuid(), "tenant_id": tenant_id, "job_id": job["id"],
        "kind": "action", "action": "send_sms", "status": "fired",
        "detail": f"TODO(LIVE) sms to {phone}: {body}",
        "created_at": _now_iso(),
    })
    return {"name": "send_sms", "status": "fired",
            "detail": f"stub-logged sms to {phone} (TODO(LIVE))"}


def _sms_templates() -> dict[str, str]:
    return {
        "booking_confirmed": "Your appointment is confirmed. We'll see you soon!",
        "tech_assigned": "A technician has been assigned to your job.",
        "tech_en_route": "Your technician is on the way!",
        "job_complete": "Job complete! Thank you for your business.",
    }


async def _action_create_invoice_draft(db, tenant_id: str, job: dict, spec: dict) -> dict:
    """Create a draft invoice for the job in the existing ``invoices`` shape."""
    existing = await db.invoices.find_one(
        {"tenant_id": tenant_id, "job_id": job["id"], "status": {"$in": ["draft", "sent"]}},
        {"_id": 0, "id": 1})
    if existing:
        return {"name": "create_invoice_draft", "status": "skipped",
                "detail": f"draft invoice {existing['id']} already exists"}
    amount = job.get("quoted_total") or job.get("total") or 0
    inv = {
        "id": _uuid(), "tenant_id": tenant_id, "job_id": job["id"],
        "customer_name": job.get("customer_name", ""),
        "customer_phone": job.get("customer_phone", ""),
        "lines": [{"description": job.get("title") or "Service performed",
                   "amount": amount}],
        "total": amount, "status": "draft", "source": "zero-touch",
        "created_at": _now_iso(), "updated_at": _now_iso(),
    }
    await db.invoices.insert_one(inv)
    return {"name": "create_invoice_draft", "status": "fired",
            "detail": f"draft invoice {inv['id']}"}


async def _action_request_review(db, tenant_id: str, job: dict, spec: dict) -> dict:
    """Queue a review request for the job (review_requests collection)."""
    existing = await db.review_requests.find_one(
        {"tenant_id": tenant_id, "job_id": job["id"]}, {"_id": 0, "id": 1})
    if existing:
        return {"name": "request_review", "status": "skipped",
                "detail": f"review request {existing['id']} already exists"}
    req = {
        "id": _uuid(), "tenant_id": tenant_id, "job_id": job["id"],
        "customer_name": job.get("customer_name", ""),
        "customer_phone": job.get("customer_phone", ""),
        "status": "pending", "source": "zero-touch",
        "created_at": _now_iso(),
    }
    await db.review_requests.insert_one(req)
    return {"name": "request_review", "status": "fired",
            "detail": f"review request {req['id']}"}


_ACTION_HANDLERS = {
    "send_sms": _action_send_sms,
    "create_invoice_draft": _action_create_invoice_draft,
    "request_review": _action_request_review,
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

async def _get_job(db, tenant_id: str, job_id: str) -> dict:
    job = await db.appointments.find_one(
        {"id": job_id, "tenant_id": tenant_id}, {"_id": 0})
    if not job:
        raise HTTPException(404, "Job not found")
    return job


async def _get_pipeline(db, tenant_id: str) -> dict:
    """Tenant's active pipeline; lazily seeds the home-services default."""
    pipe = await db.lifecycle_pipelines.find_one(
        {"tenant_id": tenant_id, "active": True}, {"_id": 0})
    if pipe:
        return pipe
    now = _now_iso()
    pipe = {"id": _uuid(), "tenant_id": tenant_id, "active": True,
            **default_pipeline(), "created_at": now, "updated_at": now}
    await db.lifecycle_pipelines.insert_one(pipe)
    return pipe


async def _log_event(db, tenant_id: str, job_id: str, **fields) -> None:
    await db.job_lifecycle_events.insert_one({
        "id": _uuid(), "tenant_id": tenant_id, "job_id": job_id,
        "created_at": _now_iso(), **fields,
    })


async def _run_stage_actions(db, tenant_id: str, job: dict, pipeline: dict,
                             stage: str) -> list[dict]:
    """Fire this stage's configured actions; every outcome is recorded."""
    stage_def = next((s for s in pipeline.get("stages", [])
                      if s.get("stage") == stage), None)
    results: list[dict] = []
    for spec in (stage_def or {}).get("actions", []):
        name = spec.get("name")
        handler = _ACTION_HANDLERS.get(name)
        if not handler:
            results.append({"name": name, "status": "skipped",
                            "detail": "unknown action"})
            continue
        try:
            results.append(await handler(db, tenant_id, job, spec))
        except Exception as exc:  # noqa: BLE001 - automation must not kill the advance
            logger.exception("zero-touch action %s failed for job %s", name, job.get("id"))
            results.append({"name": name, "status": "failed", "detail": str(exc)})
    return results


async def _transition(db, tenant_id: str, job: dict, pipeline: dict,
                      to_stage: str, *, automatic: bool,
                      actor: Optional[dict], reason: Optional[str]) -> dict:
    from_stage = _job_stage(job)
    now = _now_iso()
    update = {"lifecycle_stage": to_stage, "updated_at": now,
              f"lifecycle_{to_stage}_at": now}
    appt_status = _APPT_STATUS_MAP.get(to_stage)
    if appt_status:
        update["status"] = appt_status
    await db.appointments.update_one(
        {"id": job["id"], "tenant_id": tenant_id}, {"$set": update})
    job.update(update)

    # Log the transition first so the timeline reads "entered stage X"
    # followed by that stage's fired actions.
    event = {"id": _uuid(), "tenant_id": tenant_id, "job_id": job["id"],
             "kind": "transition" if automatic else "override",
             "from_stage": from_stage, "to_stage": to_stage,
             "actor": actor or {"type": "system"},
             "reason": reason, "actions_fired": [],
             "automatic": automatic, "created_at": _now_iso()}
    await db.job_lifecycle_events.insert_one(event)
    actions = await _run_stage_actions(db, tenant_id, job, pipeline, to_stage)
    event["actions_fired"] = actions
    await db.job_lifecycle_events.update_one(
        {"id": event["id"]}, {"$set": {"actions_fired": actions}})
    return {"event_id": event["id"], "job_id": job["id"],
            "from_stage": from_stage, "to_stage": to_stage,
            "actions_fired": actions, "automatic": automatic}


# --------------------------------------------------------------------------
# Request models
# --------------------------------------------------------------------------

class PipelineStageIn(BaseModel):
    stage: str
    label: Optional[str] = None
    actions: list[dict] = Field(default_factory=list)


class PipelineIn(BaseModel):
    name: str = "Custom pipeline"
    stages: list[PipelineStageIn]


class AdvanceIn(BaseModel):
    idempotency_key: Optional[str] = None


class StageIn(BaseModel):
    reason: str = Field(min_length=1)


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

@router.get("/tenants/pipelines")
async def list_pipelines(user: dict = Depends(require_tenant_user)):
    """Pipeline definitions for this tenant (default seeded on first read)."""
    db = get_db()
    pipe = await _get_pipeline(db, user["tenant_id"])
    return {"pipelines": [pipe]}


@router.post("/tenants/pipelines")
async def save_pipeline(data: PipelineIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Create/replace the tenant's lifecycle pipeline (owner/admin)."""
    unknown = {s.stage for s in data.stages} - set(STAGES)
    if unknown:
        raise HTTPException(400, f"Unknown stage(s): {sorted(unknown)}")
    for s in data.stages:
        for a in s.actions:
            if a.get("name") not in ACTION_NAMES:
                raise HTTPException(400, f"Unknown action: {a.get('name')!r}")
    db = get_db()
    now = _now_iso()
    doc = {
        "id": _uuid(), "tenant_id": user["tenant_id"], "active": True,
        "name": data.name,
        "stages": [{"stage": s.stage, "label": s.label or STAGE_LABELS[s.stage],
                    "actions": s.actions} for s in data.stages],
        "created_at": now, "updated_at": now,
        "created_by": user.get("id"),
    }
    await db.lifecycle_pipelines.update_many(
        {"tenant_id": user["tenant_id"], "active": True},
        {"$set": {"active": False}})
    await db.lifecycle_pipelines.insert_one(doc)
    return {"pipeline": doc}


@router.post("/tenants/jobs/{job_id}/advance")
async def advance_job(job_id: str, data: AdvanceIn,
                      user: dict = Depends(require_tenant_user)):
    """Advance a job to its next lifecycle stage (automatic transition).

    Validates the transition, fires the stage's actions, and writes an audit
    entry. Idempotent: pass ``idempotency_key`` and a replay returns the
    original result without re-firing actions.
    """
    db = get_db()
    tid = user["tenant_id"]
    if data.idempotency_key:
        prior = await db.job_lifecycle_events.find_one(
            {"tenant_id": tid, "job_id": job_id, "kind": "transition",
             "idempotency_key": data.idempotency_key}, {"_id": 0})
        if prior:
            return {"job_id": job_id, "idempotent_replay": True,
                    "to_stage": prior["to_stage"],
                    "actions_fired": prior.get("actions_fired", [])}
    job = await _get_job(db, tid, job_id)
    pipeline = await _get_pipeline(db, tid)
    current = _job_stage(job)
    nexts = ALLOWED_TRANSITIONS.get(current, set())
    if not nexts:
        return {"job_id": job_id, "advanced": False, "stage": current,
                "detail": "terminal stage"}
    # Prefer the linear happy path over cancel.
    nxt = sorted(nexts - {"canceled"})[0] if (nexts - {"canceled"}) else "canceled"
    result = await _transition(db, tid, job, pipeline, nxt,
                               automatic=True, actor={"type": "system"}, reason=None)
    result["advanced"] = True
    if data.idempotency_key:
        await db.job_lifecycle_events.update_one(
            {"id": result["event_id"]},
            {"$set": {"idempotency_key": data.idempotency_key}})
    return result


@router.post("/tenants/jobs/{job_id}/stage/{stage}")
async def set_stage(job_id: str, stage: str, data: StageIn,
                    user: dict = Depends(require_tenant_user)):
    """Human override: move a job directly to any stage with a reason.

    Audit-logged as ``kind: override`` so the autonomy dashboard can count
    human interventions.
    """
    if stage not in STAGES:
        raise HTTPException(400, f"Unknown stage: {stage!r}")
    db = get_db()
    tid = user["tenant_id"]
    job = await _get_job(db, tid, job_id)
    pipeline = await _get_pipeline(db, tid)
    current = _job_stage(job)
    if current == stage:
        return {"job_id": job_id, "stage": stage, "unchanged": True}
    actor = {"type": "user", "user_id": user.get("id"), "role": user.get("role")}
    result = await _transition(db, tid, job, pipeline, stage,
                               automatic=False, actor=actor, reason=data.reason)
    result["override"] = True
    return result


@router.get("/tenants/jobs/{job_id}/timeline")
async def job_timeline(job_id: str, user: dict = Depends(require_tenant_user)):
    """Full audit timeline for a job: transitions, overrides, fired actions."""
    db = get_db()
    await _get_job(db, user["tenant_id"], job_id)
    events = await db.job_lifecycle_events.find(
        {"tenant_id": user["tenant_id"], "job_id": job_id},
        {"_id": 0}).sort("created_at", 1).to_list(500)
    return {"job_id": job_id, "events": events, "count": len(events)}


@router.get("/tenants/events/recent")
async def recent_events(user: dict = Depends(require_tenant_user),
                        limit: int = Query(50, ge=1, le=200)):
    """Most recent lifecycle events across all of this tenant's jobs."""
    db = get_db()
    events = await db.job_lifecycle_events.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}
    ).sort("created_at", -1).to_list(limit)
    return {"events": events, "count": len(events)}


@router.get("/tenants/autonomy")
async def autonomy_dashboard(user: dict = Depends(require_tenant_user),
                             days: int = Query(30, ge=1, le=365)):
    """Autonomy KPIs: % fully autonomous jobs, human overrides, avg time/stage."""
    from datetime import datetime, timedelta, timezone
    db = get_db()
    tid = user["tenant_id"]
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    events = await db.job_lifecycle_events.find(
        {"tenant_id": tid, "created_at": {"$gte": since}}, {"_id": 0}
    ).sort("created_at", 1).to_list(5000)

    by_job: dict[str, list[dict]] = {}
    for e in events:
        by_job.setdefault(e["job_id"], []).append(e)

    finished = [evs for evs in by_job.values()
                if any(e.get("to_stage") == "paid" for e in evs)]
    fully_auto = [evs for evs in finished
                  if not any(e.get("kind") == "override" for e in evs)]
    overrides = sum(1 for e in events if e.get("kind") == "override")
    automatic = sum(1 for e in events if e.get("kind") == "transition" and e.get("automatic"))
    actions = sum(len(e.get("actions_fired", []) or [])
                  for e in events if e.get("kind") in {"transition", "override"})
    fired = sum(1 for e in events if e.get("kind") in {"transition", "override"}
                for a in (e.get("actions_fired", []) or []) if a.get("status") == "fired")

    # Avg time per stage: consecutive transitions of the same job.
    stage_secs: dict[str, list[float]] = {}
    for evs in by_job.values():
        trans = [e for e in evs if e.get("kind") in {"transition", "override"}]
        for prev, cur in zip(trans, trans[1:]):
            try:
                a = datetime.fromisoformat(prev["created_at"])
                b = datetime.fromisoformat(cur["created_at"])
                stage_secs.setdefault(cur.get("from_stage", "unknown"), []).append(
                    max(0.0, (b - a).total_seconds()))
            except (KeyError, ValueError):
                continue
    avg_per_stage = {s: round(sum(v) / len(v), 1) for s, v in stage_secs.items()}

    return {
        "days": days,
        "jobs_with_activity": len(by_job),
        "jobs_reached_paid": len(finished),
        "fully_autonomous_pct": round(len(fully_auto) / len(finished) * 100, 1) if finished else 0.0,
        "automatic_transitions": automatic,
        "human_overrides": overrides,
        "actions_triggered": actions,
        "actions_fired": fired,
        "avg_seconds_per_stage": avg_per_stage,
    }
