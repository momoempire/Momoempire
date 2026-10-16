"""Platform cron endpoints. Must ack 2xx fast and enqueue the actual work."""
import os
import hmac
import asyncio
from fastapi import APIRouter, Request, HTTPException
from db import get_db
from routers.overage import _compute_tenant_overage, _upsert_overage_items, _charge_overage_to_stripe
from models import _now_iso

router = APIRouter(prefix="/cron", tags=["cron"])


def _authorized(request: Request) -> bool:
    expected = os.environ.get("WEBHOOK_CRON_SECRET") or ""
    header = request.headers.get("authorization", "")
    if not expected or not header.lower().startswith("bearer "):
        return False
    return hmac.compare_digest(header.split(" ", 1)[1].strip(), expected)


async def _run_followups_due():
    db = get_db()
    now = _now_iso()
    due = await db.followup_jobs.find(
        {"status": "scheduled", "send_at": {"$lte": now}}, {"_id": 0}
    ).to_list(1000)
    for j in due:
        if j.get("channel") == "sms" and j.get("to"):
            try:
                from routers.usage import record_usage
                from services.twilio import send_sms
                res = await send_sms(tenant_id=j["tenant_id"], to=j["to"], body=j["body"])
                await record_usage(j["tenant_id"], "sms", 1, {"followup_job_id": j["id"], "cron": True, "twilio_status": res.get("status")})
            except Exception as e:
                print(f"[CRON FOLLOWUP send error] {e}")
            print(f"[CRON FOLLOWUP->{j['to']}] {j['body']}")
        await db.followup_jobs.update_one(
            {"id": j["id"]},
            {"$set": {"status": "sent", "sent_at": _now_iso()}},
        )


async def _run_overage_nightly():
    db = get_db()
    tenants = await db.tenants.find({}, {"_id": 0}).to_list(2000)
    for t in tenants:
        try:
            summary = await _compute_tenant_overage(t["id"])
            if summary["total_cents"] <= 0:
                continue
            saved = await _upsert_overage_items(summary)
            await _charge_overage_to_stripe(t, saved)
        except Exception as e:
            print(f"[CRON OVERAGE] tenant {t.get('id')} failed: {e}")


async def _run_appointment_reminders():
    """Send SMS reminders for appointments 20-28 hours away (idempotent per appointment)."""
    from datetime import datetime, timezone, timedelta
    from services.twilio import send_sms
    db = get_db()
    now = datetime.now(timezone.utc)
    window_start = (now + timedelta(hours=20)).isoformat()
    window_end = (now + timedelta(hours=28)).isoformat()
    appts = await db.appointments.find(
        {"status": {"$in": ["scheduled", "confirmed"]},
         "start_at": {"$gte": window_start, "$lte": window_end},
         "reminder_sent_at": {"$exists": False}},
        {"_id": 0},
    ).to_list(1000)
    for a in appts:
        if not a.get("customer_phone"):
            continue
        tenant = await db.tenants.find_one({"id": a["tenant_id"]}, {"_id": 0, "name": 1}) or {}
        autom = await db.automation_settings.find_one({"tenant_id": a["tenant_id"]}, {"_id": 0}) or {}
        template = autom.get("reminder_sms_template") or "Hi {name}, reminder for your {service} appointment on {time}. Reply C to confirm."
        when = a.get("start_at", "")
        body = template.format(name=a.get("customer_name", "there"),
                              service=a.get("service_name") or "appointment",
                              time=when[:16].replace("T", " "))
        try:
            res = await send_sms(tenant_id=a["tenant_id"], to=a["customer_phone"], body=body)
            await db.appointments.update_one(
                {"id": a["id"]},
                {"$set": {"reminder_sent_at": _now_iso(), "reminder_status": res.get("status")}},
            )
            print(f"[CRON APPT-REMINDER->{a['customer_phone']}] {body}")
        except Exception as e:
            print(f"[CRON APPT-REMINDER] failed for {a.get('id')}: {e}")


@router.post("/followups")
async def cron_followups(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    if not _authorized(request):
        raise HTTPException(401, "unauthorized")
    asyncio.create_task(_run_followups_due())
    return {"accepted": True}


@router.post("/overage-nightly")
async def cron_overage(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    if not _authorized(request):
        raise HTTPException(401, "unauthorized")
    asyncio.create_task(_run_overage_nightly())
    return {"accepted": True}


@router.post("/weekly-digest")
async def cron_weekly_digest(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    if not _authorized(request):
        raise HTTPException(401, "unauthorized")
    from routers.growth import _send_digest_to_all
    asyncio.create_task(_send_digest_to_all())
    return {"accepted": True}


@router.post("/appointment-reminders")
async def cron_appointment_reminders(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    if not _authorized(request):
        raise HTTPException(401, "unauthorized")
    asyncio.create_task(_run_appointment_reminders())
    return {"accepted": True}


@router.post("/daily-standup")
async def cron_daily_standup(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    if not _authorized(request):
        raise HTTPException(401, "unauthorized")
    from routers.post_job import _send_standup_to_all
    asyncio.create_task(_send_standup_to_all())
    return {"accepted": True}


@router.post("/callbacks-due")
async def cron_callbacks_due(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    if not _authorized(request):
        raise HTTPException(401, "unauthorized")
    from routers.phase9 import _run_due_callbacks
    asyncio.create_task(_run_due_callbacks())
    return {"accepted": True}


@router.post("/appt-confirmations")
async def cron_appt_confirmations(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    if not _authorized(request):
        raise HTTPException(401, "unauthorized")
    from routers.phase9 import _run_appt_confirmations
    asyncio.create_task(_run_appt_confirmations())
    return {"accepted": True}



@router.post("/repeat-reminders")
async def cron_repeat_reminders(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    if not _authorized(request):
        raise HTTPException(401, "unauthorized")
    from routers.repeat import run_repeat_reminders
    asyncio.create_task(run_repeat_reminders())
    return {"accepted": True}


@router.post("/overdue-reminders")
async def cron_overdue_reminders(request: Request):
    # Cron endpoints must ack 2xx immediately; enqueue/background the actual work.
    if not _authorized(request):
        raise HTTPException(401, "unauthorized")
    from routers.call_to_payment import run_overdue_reminders
    asyncio.create_task(run_overdue_reminders())
    return {"accepted": True}
