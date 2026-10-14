"""Phase 9 features: 24h appointment auto-confirmation, review-request autopilot (2h),
call-back scheduler, service-area heatmap, Google Calendar ICS export for standup."""
import os
from brand import brand_name
from site_contact import contact_email
import re
import json
import logging
import httpx
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional, List
from db import get_db
from models import _uuid, _now_iso
from security import require_tenant_user, require_tenant_owner_or_admin
from services.twilio import send_sms

logger = logging.getLogger("phase9")

router = APIRouter(prefix="/growth", tags=["growth-phase9"])


# ---------- Call-back scheduler ----------
class CallbackIn(BaseModel):
    lead_id: Optional[str] = None
    conversation_id: Optional[str] = None
    phone: str
    name: str = ""
    call_at: str                 # ISO datetime
    note: str = ""


@router.post("/callbacks")
async def schedule_callback(data: CallbackIn, user: dict = Depends(require_tenant_user)):
    db = get_db()
    try:
        datetime.fromisoformat(data.call_at.replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(400, "call_at must be ISO datetime")
    doc = {
        "id": _uuid(), "tenant_id": user["tenant_id"], "kind": "callback",
        "channel": "voice", "to": data.phone, "name": data.name, "note": data.note,
        "lead_id": data.lead_id, "conversation_id": data.conversation_id,
        "send_at": data.call_at, "status": "scheduled",
        "created_at": _now_iso(),
    }
    await db.followup_jobs.insert_one(doc.copy())
    doc.pop("_id", None)
    return doc


@router.get("/callbacks")
async def list_callbacks(user: dict = Depends(require_tenant_user), status: Optional[str] = None):
    db = get_db()
    q = {"tenant_id": user["tenant_id"], "kind": "callback"}
    if status: q["status"] = status
    return await db.followup_jobs.find(q, {"_id": 0}).sort("send_at", 1).to_list(200)


@router.post("/callbacks/{cid}/cancel")
async def cancel_callback(cid: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    res = await db.followup_jobs.update_one(
        {"id": cid, "tenant_id": user["tenant_id"], "kind": "callback", "status": "scheduled"},
        {"$set": {"status": "cancelled", "updated_at": _now_iso()}},
    )
    if res.matched_count == 0:
        raise HTTPException(404, "not scheduled")
    return {"status": "ok"}


async def _run_due_callbacks():
    """Cron handler: for callback jobs whose time is due, place a Twilio voice call."""
    db = get_db()
    now_iso = _now_iso()
    due = await db.followup_jobs.find(
        {"kind": "callback", "status": "scheduled", "send_at": {"$lte": now_iso}}, {"_id": 0}
    ).to_list(500)
    base = (os.environ.get("PUBLIC_BACKEND_URL") or "").rstrip("/")
    for j in due:
        try:
            tcfg = await db.integrations.find_one({"tenant_id": j["tenant_id"], "key": "twilio"}, {"_id": 0, "config": 1}) or {}
            cfg = tcfg.get("config", {}) or {}
            sid = cfg.get("account_sid") or os.environ.get("TWILIO_ACCOUNT_SID")
            tok = cfg.get("auth_token") or os.environ.get("TWILIO_AUTH_TOKEN")
            frm = cfg.get("from_number") or os.environ.get("TWILIO_FROM_NUMBER")
            if sid and tok and frm and base:
                from twilio.rest import Client
                client = Client(sid, tok)
                tenant = await db.tenants.find_one({"id": j["tenant_id"]}, {"_id": 0, "name": 1}) or {}
                twiml_url = f"{base}/api/twilio/outbound-callback?tenant_id={j['tenant_id']}&name={j.get('name','')}&note={j.get('note','')}"
                call = client.calls.create(to=j["to"], from_=frm, url=twiml_url)
                await db.followup_jobs.update_one({"id": j["id"]},
                    {"$set": {"status": "sent", "sent_at": _now_iso(), "twilio_call_sid": call.sid}})
                logger.info("placed callback to %s (sid=%s)", j["to"], call.sid)
            else:
                # Demo mode — notify owner by SMS instead
                tenant = await db.tenants.find_one({"id": j["tenant_id"]}, {"_id": 0, "name": 1, "contact_phone": 1}) or {}
                owner_phone = tenant.get("contact_phone") or ""
                if owner_phone:
                    await send_sms(tenant_id=j["tenant_id"], to=owner_phone,
                                  body=f"Time to call {j.get('name') or j['to']} ({j['to']}). Note: {j.get('note','(none)')}")
                await db.followup_jobs.update_one({"id": j["id"]},
                    {"$set": {"status": "sent", "sent_at": _now_iso(), "demo": True}})
        except Exception as e:
            logger.exception("callback dispatch failed: %s", e)
            await db.followup_jobs.update_one({"id": j["id"]},
                {"$set": {"status": "error", "error": str(e), "updated_at": _now_iso()}})


# ---------- 24h appointment auto-confirmation ----------
async def _run_appt_confirmations():
    """For appts 20-28h away, send a confirmation SMS and listen for YES/RESCHEDULE."""
    db = get_db()
    now = datetime.now(timezone.utc)
    start = (now + timedelta(hours=20)).isoformat()
    end = (now + timedelta(hours=28)).isoformat()
    appts = await db.appointments.find(
        {"status": {"$in": ["scheduled", "confirmed"]},
         "start_at": {"$gte": start, "$lte": end},
         "confirm_sent_at": {"$exists": False}},
        {"_id": 0},
    ).to_list(1000)
    for a in appts:
        if not a.get("customer_phone"):
            continue
        tenant = await db.tenants.find_one({"id": a["tenant_id"]}, {"_id": 0, "name": 1}) or {}
        when = a.get("start_at", "")[:16].replace("T", " ")
        body = (
            f"Hi {a.get('customer_name','there')}, this is {tenant.get('name','us')}. "
            f"We're set for {a.get('service_name','your appointment')} on {when}. "
            "Reply YES to confirm or RESCHEDULE for a new time."
        )
        try:
            res = await send_sms(tenant_id=a["tenant_id"], to=a["customer_phone"], body=body)
            await db.appointments.update_one(
                {"id": a["id"]},
                {"$set": {"confirm_sent_at": _now_iso(), "confirm_status": res.get("status")}},
            )
        except Exception as e:
            logger.exception("appt confirm failed for %s: %s", a.get("id"), e)


# Public endpoint called from the SMS inbound hook to process YES/RESCHEDULE
async def handle_confirmation_reply(tenant_id: str, from_phone: str, body: str) -> dict | None:
    """Returns a reply string + action, or None if message isn't a confirmation."""
    db = get_db()
    text = (body or "").strip().upper()
    if text not in {"YES", "Y", "CONFIRM", "RESCHEDULE", "R", "CANCEL", "NO", "N"}:
        return None
    # Find pending appt for this caller in the next 48h
    now = datetime.now(timezone.utc)
    appt = await db.appointments.find_one(
        {"tenant_id": tenant_id, "customer_phone": from_phone,
         "start_at": {"$gte": now.isoformat(), "$lte": (now + timedelta(hours=48)).isoformat()},
         "status": {"$in": ["scheduled", "confirmed"]}},
        {"_id": 0}, sort=[("start_at", 1)],
    )
    if not appt:
        return None
    if text in {"YES", "Y", "CONFIRM"}:
        await db.appointments.update_one({"id": appt["id"]}, {"$set": {"status": "confirmed", "confirmed_at": _now_iso()}})
        return {"reply": "Great — you're confirmed! See you soon.", "action": {"type": "appt_confirmed", "appointment_id": appt["id"]}}
    if text in {"CANCEL", "NO", "N"}:
        await db.appointments.update_one({"id": appt["id"]}, {"$set": {"status": "canceled", "canceled_at": _now_iso()}})
        return {"reply": "No problem, your slot is released. Reach out any time to rebook.", "action": {"type": "appt_canceled", "appointment_id": appt["id"]}}
    # RESCHEDULE
    await db.appointments.update_one({"id": appt["id"]}, {"$set": {"status": "canceled", "canceled_at": _now_iso(), "reschedule_requested": True}})
    return {"reply": "Got it — we'll call shortly to pick a new time. Or reply here with a day/time that works for you.", "action": {"type": "appt_reschedule", "appointment_id": appt["id"]}}


# ---------- Review-request autopilot (2h after completion) ----------
async def schedule_review_request_2h(tenant_id: str, appt: dict):
    """Called from post_job.run_post_job; adds a 2h-delayed review SMS."""
    db = get_db()
    if not appt.get("customer_phone"):
        return
    tenant = await db.tenants.find_one({"id": tenant_id}, {"_id": 0}) or {}
    review_url = tenant.get("review_url") or ""
    if not review_url:
        return
    biz = tenant.get("name", "us")
    name = appt.get("customer_name", "there")
    send_at = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    body = f"Hi {name}, hope everything went well with {biz}! If you have 10 seconds, a quick review would mean the world: {review_url}"
    await db.followup_jobs.update_one(
        {"tenant_id": tenant_id, "appointment_id": appt["id"], "kind": "review-request-2h"},
        {"$set": {
            "id": _uuid(), "tenant_id": tenant_id, "appointment_id": appt["id"],
            "kind": "review-request-2h", "channel": "sms",
            "to": appt["customer_phone"], "body": body,
            "send_at": send_at, "status": "scheduled", "created_at": _now_iso(),
        }},
        upsert=True,
    )


# ---------- Service-area heatmap ----------
_ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
_CITY_STATE_RE = re.compile(r",\s*([A-Za-z .'-]+?)[,\s]+([A-Z]{2})\b")


def _extract_zip(address: str | None) -> str | None:
    if not address:
        return None
    m = _ZIP_RE.search(address)
    return m.group(1) if m else None


def _extract_city(address: str | None) -> str | None:
    if not address:
        return None
    m = _CITY_STATE_RE.search(address)
    return f"{m.group(1).strip()}, {m.group(2)}" if m else None


async def _geocode_zip(zip_code: str, country: str = "us") -> dict | None:
    db = get_db()
    cached = await db.geocode_cache.find_one({"zip": zip_code, "country": country}, {"_id": 0})
    if cached:
        return cached
    try:
        async with httpx.AsyncClient(timeout=10) as hc:
            r = await hc.get(
                "https://nominatim.openstreetmap.org/search",
                params={"postalcode": zip_code, "country": country, "format": "json", "limit": 1},
                headers={"User-Agent": f"ai-office-platform/1.0 (contact: {contact_email()})"},
            )
        rows = r.json() or []
        if not rows:
            return None
        row = rows[0]
        doc = {"zip": zip_code, "country": country, "lat": float(row["lat"]), "lon": float(row["lon"]),
               "display_name": row.get("display_name"), "cached_at": _now_iso()}
        await db.geocode_cache.insert_one(doc.copy())
        doc.pop("_id", None)
        return doc
    except Exception as e:
        logger.warning("geocode failed for %s: %s", zip_code, e)
        return None


@router.get("/heatmap")
async def service_area_heatmap(user: dict = Depends(require_tenant_user), days: int = 90, country: str = "us"):
    """Aggregate leads + customers by ZIP; geocode top ZIPs so UI can map them."""
    db = get_db()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    buckets: dict[str, dict] = {}
    cursor = db.leads.find(
        {"tenant_id": user["tenant_id"], "created_at": {"$gte": since}},
        {"_id": 0, "notes": 1, "address": 1, "name": 1},
    )
    async for row in cursor:
        addr = row.get("address") or row.get("notes") or ""
        z = _extract_zip(addr)
        c = _extract_city(addr)
        key = z or c or "Unknown"
        b = buckets.setdefault(key, {"zip": z, "city": c, "label": key, "leads": 0, "customers": 0})
        b["leads"] += 1
    async for row in db.customers.find({"tenant_id": user["tenant_id"]}, {"_id": 0, "address": 1}):
        addr = row.get("address") or ""
        z = _extract_zip(addr)
        c = _extract_city(addr)
        key = z or c or "Unknown"
        b = buckets.setdefault(key, {"zip": z, "city": c, "label": key, "leads": 0, "customers": 0})
        b["customers"] += 1

    rows = sorted(buckets.values(), key=lambda r: -(r["leads"] + r["customers"]))[:12]
    # Geocode top 12 ZIPs for map pins
    for r in rows:
        if r.get("zip"):
            geo = await _geocode_zip(r["zip"], country=country)
            if geo:
                r["lat"] = geo["lat"]; r["lon"] = geo["lon"]
    totals = {"regions": len(buckets), "leads_total": sum(r["leads"] for r in buckets.values()),
             "customers_total": sum(r["customers"] for r in buckets.values()),
             "days": days}
    return {"totals": totals, "rows": rows}


# ---------- Google Calendar ICS for standup ----------
def _ics_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


@router.get("/standup/today.ics")
async def standup_ics(user: dict = Depends(require_tenant_user)):
    """Return today's standup as a single VEVENT that Google Calendar can import."""
    db = get_db()
    tenant = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0}) or {}
    now = datetime.now(timezone.utc)
    end_of_day = now.replace(hour=23, minute=59, second=0, microsecond=0)
    today = now.isoformat()
    tomorrow = (now + timedelta(days=1)).isoformat()
    appts = await db.appointments.find(
        {"tenant_id": user["tenant_id"], "status": {"$in": ["scheduled", "confirmed"]},
         "start_at": {"$gte": today, "$lte": tomorrow}}, {"_id": 0},
    ).sort("start_at", 1).to_list(50)
    hot = await db.leads.count_documents({"tenant_id": user["tenant_id"], "lead_score.label": "hot",
                                          "status": {"$in": ["new", "contacted"]}})
    pending = await db.followup_jobs.count_documents({"tenant_id": user["tenant_id"], "status": "scheduled",
                                                       "send_at": {"$lte": (now + timedelta(days=1)).isoformat()}})
    biz = tenant.get("name", "Your business")

    lines = [f"- {a.get('customer_name','?')}: {a.get('service_name','appt')} @ {a.get('start_at','')[11:16]}" for a in appts]
    descr = "\n".join([
        f"{biz} · daily standup",
        f"Appointments: {len(appts)}", f"Hot leads: {hot}", f"Follow-ups queued: {pending}",
        "", "Today's schedule:", *(lines or ["(no appointments)"]),
    ])

    uid = f"aio-standup-{user['tenant_id']}-{now.strftime('%Y%m%d')}@mindateasesolutions.com"
    dtstamp = now.strftime("%Y%m%dT%H%M%SZ")
    dtstart = now.strftime("%Y%m%dT%H%M%SZ")
    dtend = end_of_day.strftime("%Y%m%dT%H%M%SZ")
    ics = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        f"PRODID:-//{brand_name()}//Standup//EN\r\n"
        "CALSCALE:GREGORIAN\r\n"
        "METHOD:PUBLISH\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"DTSTAMP:{dtstamp}\r\n"
        f"DTSTART:{dtstart}\r\n"
        f"DTEND:{dtend}\r\n"
        f"SUMMARY:{_ics_escape(biz + ' · morning standup')}\r\n"
        f"DESCRIPTION:{_ics_escape(descr)}\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    return Response(ics, media_type="text/calendar", headers={
        "Content-Disposition": f'attachment; filename="standup-{now.strftime("%Y%m%d")}.ics"',
    })
