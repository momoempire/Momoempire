"""Post-job automation + review-response drafter + daily standup + weekly social post.

Core design: everything reuses services/email + services/twilio + the AI key (AI_API_KEY).
Deterministic fallbacks keep every endpoint 200-OK even when LLM/channel is missing.
"""
import os
import re
import json
import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from db import get_db
from models import _uuid, _now_iso
from security import require_tenant_user, require_tenant_owner_or_admin
from services.email import send_email, followup_html
from services.twilio import send_sms

logger = logging.getLogger("post_job")

router = APIRouter(prefix="/growth", tags=["growth-plus"])


# ---------- Post-job thank-you + win-back ----------
async def run_post_job(tenant_id: str, appt_id: str) -> dict:
    """Immediate thank-you SMS + schedule a 60-day win-back nudge."""
    db = get_db()
    appt = await db.appointments.find_one({"id": appt_id, "tenant_id": tenant_id}, {"_id": 0})
    if not appt:
        return {"skipped": "appointment-missing"}
    tenant = await db.tenants.find_one({"id": tenant_id}, {"_id": 0}) or {}
    review_url = tenant.get("review_url") or ""
    biz = tenant.get("name", "us")
    name = appt.get("customer_name", "there")
    service = appt.get("service_name") or "your appointment"
    phone = appt.get("customer_phone", "")
    email = appt.get("customer_email", "")

    # 1) Thank-you now
    thanks_body = (
        f"Hi {name} — thanks for choosing {biz} today! We hope {service} went great. "
        f"If you have 10 seconds, we'd love a review: {review_url}" if review_url
        else f"Hi {name} — thanks for choosing {biz} today! Please reach back out if we can help again."
    )
    sent = {"sms": None, "email": None}
    if phone:
        try:
            res = await send_sms(tenant_id=tenant_id, to=phone, body=thanks_body)
            sent["sms"] = res.get("status")
        except Exception as e:
            logger.warning("thank-you sms failed: %s", e)
    if email:
        try:
            html = followup_html(business=biz, message=thanks_body)
            sent["email"] = await send_email(to=email, subject=f"Thank you from {biz}", html=html, from_name=biz)
        except Exception as e:
            logger.warning("thank-you email failed: %s", e)

    # 2) Mark customer last_contacted_at + capture appointment
    if appt.get("customer_id"):
        await db.customers.update_one(
            {"id": appt["customer_id"], "tenant_id": tenant_id},
            {"$set": {"last_contacted_at": _now_iso(), "last_service_at": _now_iso()}},
        )

    # 3) Schedule 60-day win-back SMS job (idempotent per appointment)
    send_at = (datetime.now(timezone.utc) + timedelta(days=60)).isoformat()
    body60 = f"Hi {name}, it's {biz}. It's been a couple months since {service} — want 10% off your next visit if we book this week?"
    if phone:
        await db.followup_jobs.update_one(
            {"tenant_id": tenant_id, "appointment_id": appt_id, "kind": "winback-60d"},
            {"$set": {
                "id": _uuid(), "tenant_id": tenant_id, "lead_id": appt.get("lead_id"),
                "appointment_id": appt_id, "kind": "winback-60d",
                "channel": "sms", "to": phone, "body": body60,
                "send_at": send_at, "status": "scheduled",
                "trigger_label": "post-job", "created_at": _now_iso(),
            }},
            upsert=True,
        )

    # 3b) Schedule a 2-hour review-request SMS (if review_url is set)
    try:
        from routers.phase9 import schedule_review_request_2h
        await schedule_review_request_2h(tenant_id, appt)
    except Exception as e:
        logger.warning("review-request-2h schedule failed: %s", e)

    # 4) Log a post_job_run record so UI can show history
    await db.post_job_runs.insert_one({
        "id": _uuid(), "tenant_id": tenant_id, "appointment_id": appt_id,
        "customer_name": name, "service": service,
        "thanks_sent": sent, "winback_scheduled_for": send_at if phone else None,
        "created_at": _now_iso(),
    })
    return {"thanks_sent": sent, "winback_scheduled_for": send_at if phone else None}


@router.post("/post-job/{appt_id}/run")
async def manual_post_job(appt_id: str, user: dict = Depends(require_tenant_user)):
    return await run_post_job(user["tenant_id"], appt_id)


@router.get("/post-job/runs")
async def list_post_job_runs(user: dict = Depends(require_tenant_user), limit: int = 100):
    db = get_db()
    rows = await db.post_job_runs.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return rows


# ---------- AI review response drafter ----------
class ReviewResponseIn(BaseModel):
    review_text: str
    rating: Optional[int] = None  # 1-5
    customer_name: Optional[str] = None


def _rule_review_response(biz: str, text: str, rating: int | None, name: str | None) -> dict:
    t = (text or "").lower()
    negative = (rating or 5) <= 3 or any(w in t for w in ["bad", "worst", "terrible", "rude", "never", "disappointed"])
    greeting = f"Hi {name}," if name else "Hi,"
    if negative:
        reply = (
            f"{greeting} thanks for the honest feedback — this isn't the experience we want anyone to have. "
            f"I'd love to make it right. Please reach me directly and we'll fix this ASAP. — {biz}"
        )
        tone = "apologetic"
    elif (rating or 5) == 5:
        reply = (
            f"{greeting} thank you so much for the kind words! It really means a lot to the whole {biz} team. "
            f"We're glad we hit the mark and look forward to helping again."
        )
        tone = "grateful"
    else:
        reply = (
            f"{greeting} thanks for taking the time to share this with us. If there's anything else we can "
            f"do to earn that fifth star, please let us know — we're always listening. — {biz}"
        )
        tone = "neutral"
    return {"reply": reply, "tone": tone, "method": "rules"}


@router.post("/review-response")
async def draft_review_response(data: ReviewResponseIn, user: dict = Depends(require_tenant_user)):
    db = get_db()
    tenant = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0, "name": 1}) or {}
    biz = tenant.get("name", "our team")
    fallback = _rule_review_response(biz, data.review_text, data.rating, data.customer_name)
    from llm_portable import ai_api_key
    key = ai_api_key()
    if not key:
        return fallback
    try:
        from llm_portable import LlmChat, UserMessage
        system = (
            "You draft public replies to customer reviews on Google/Yelp for a small service business. "
            "Rules: warm, specific, ~2 sentences, never defensive. For negative reviews: acknowledge, apologize "
            "briefly, invite them to contact the business directly to make it right. For positive: thank them "
            "specifically and invite them back. Sign off with the business name. Return JSON only: "
            '{"reply":"...","tone":"grateful|apologetic|neutral"}'
        )
        chat = LlmChat(api_key=key, session_id=f"review-resp-{user['tenant_id'][:6]}",
                      system_message=system).with_model("openai", "gpt-6-sol")
        prompt = (
            f"Business: {biz}\nReviewer: {data.customer_name or 'unknown'}\n"
            f"Rating: {data.rating or 'unknown'}/5\nReview: {data.review_text[:1500]}"
        )
        raw = await chat.send_message(UserMessage(text=prompt))
        text = raw if isinstance(raw, str) else getattr(raw, "content", str(raw))
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return fallback
        parsed = json.loads(m.group(0))
        parsed["method"] = "llm"
        if not parsed.get("reply"):
            return fallback
        return parsed
    except Exception as e:
        logger.warning("review-response LLM failed: %s", e)
        return fallback


# ---------- Daily owner standup ----------
async def _standup_payload(tenant_id: str) -> dict:
    db = get_db()
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).isoformat()
    todays = await db.appointments.find(
        {"tenant_id": tenant_id, "status": {"$in": ["scheduled", "confirmed"]},
         "start_at": {"$gte": now.isoformat(), "$lte": tomorrow}},
        {"_id": 0},
    ).sort("start_at", 1).to_list(50)
    hot = await db.leads.find({"tenant_id": tenant_id, "lead_score.label": "hot",
                              "status": {"$in": ["new", "contacted"]}}, {"_id": 0}).to_list(20)
    pending = await db.followup_jobs.count_documents({"tenant_id": tenant_id, "status": "scheduled",
                                                      "send_at": {"$lte": (now + timedelta(days=1)).isoformat()}})
    missed_yday = await db.conversations.count_documents({
        "tenant_id": tenant_id, "status": {"$in": ["missed", "voicemail"]},
        "created_at": {"$gte": (now - timedelta(days=1)).isoformat()},
    })
    return {
        "appointments": todays, "hot_leads": hot, "pending_followups": pending,
        "missed_yesterday": missed_yday, "generated_at": now.isoformat(),
    }


@router.get("/standup/preview")
async def standup_preview(user: dict = Depends(require_tenant_user)):
    return await _standup_payload(user["tenant_id"])


@router.post("/standup/send")
async def standup_send(user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    tenant = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0}) or {}
    p = await _standup_payload(user["tenant_id"])
    body_lines = [
        f"Good morning from {tenant.get('name','your AI office')}.",
        f"• Today's appointments: {len(p['appointments'])}",
        f"• Hot leads to call: {len(p['hot_leads'])}",
        f"• Follow-ups queued today: {p['pending_followups']}",
    ]
    if p["missed_yesterday"]:
        body_lines.append(f"• Missed calls yesterday: {p['missed_yesterday']}")
    body = "\n".join(body_lines)
    phone = tenant.get("contact_phone") or (user or {}).get("phone", "")
    email = tenant.get("contact_email") or (user or {}).get("email", "")
    sent = {"sms": None, "email": None}
    if phone:
        res = await send_sms(tenant_id=user["tenant_id"], to=phone, body=body)
        sent["sms"] = res.get("status")
    if email:
        html = followup_html(business=tenant.get("name", "Your business"), message=body)
        sent["email"] = await send_email(to=email, subject=f"{tenant.get('name','Your business')} · morning standup",
                                        html=html, from_name=tenant.get("name") or None)
    return {"sent": sent, "preview": body}


async def _send_standup_to_all():
    db = get_db()
    tenants = await db.tenants.find({"status": "active"}, {"_id": 0}).to_list(5000)
    for t in tenants:
        try:
            p = await _standup_payload(t["id"])
            if len(p["appointments"]) + len(p["hot_leads"]) + p["pending_followups"] == 0:
                continue  # skip silent days
            body = (f"Good morning from {t.get('name','your AI office')}.\n"
                    f"• Today's appointments: {len(p['appointments'])}\n"
                    f"• Hot leads to call: {len(p['hot_leads'])}\n"
                    f"• Follow-ups queued: {p['pending_followups']}")
            phone = t.get("contact_phone") or ""
            email = t.get("contact_email") or ""
            if phone:
                await send_sms(tenant_id=t["id"], to=phone, body=body)
            if email:
                html = followup_html(business=t.get("name", "Your business"), message=body)
                await send_email(to=email, subject=f"{t.get('name','Your business')} · morning standup",
                                html=html, from_name=t.get("name") or None)
        except Exception as e:
            logger.warning("standup for %s failed: %s", t.get("id"), e)


# ---------- Weekly social post draft ----------
@router.post("/social/draft")
async def draft_social_post(user: dict = Depends(require_tenant_user)):
    """One-tap: generate a Facebook/Instagram caption from this week's wins."""
    db = get_db()
    tenant = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0}) or {}
    biz = tenant.get("name", "our team")
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=7)).isoformat()
    completed = await db.appointments.count_documents({"tenant_id": user["tenant_id"], "status": "completed", "created_at": {"$gte": since}})
    new_leads = await db.leads.count_documents({"tenant_id": user["tenant_id"], "created_at": {"$gte": since}})
    reviews = await db.review_requests.count_documents({"tenant_id": user["tenant_id"], "status": "completed", "created_at": {"$gte": since}})

    fallback = {
        "caption": (
            f"✨ Big week at {biz}! {completed} happy customers served, "
            f"{new_leads} new neighbors welcomed, and {reviews} fresh reviews. "
            "Thanks for trusting us with your home 💛 Tag someone who still needs us to call them back!"
        ),
        "hashtags": ["#localbusiness", f"#{biz.lower().replace(' ', '')}", "#community"],
        "method": "rules",
    }
    from llm_portable import ai_api_key
    key = ai_api_key()
    if not key:
        return fallback
    try:
        from llm_portable import LlmChat, UserMessage
        system = (
            "You write short, warm social media captions for a local service business. "
            "~2-3 sentences, 1-2 emoji, add a soft CTA at the end. Return JSON only: "
            '{"caption":"...","hashtags":["#tag","#tag"]}'
        )
        chat = LlmChat(api_key=key, session_id=f"social-{user['tenant_id'][:6]}",
                      system_message=system).with_model("openai", "gpt-6-sol")
        prompt = (
            f"Business: {biz} ({tenant.get('industry_slug', 'small business')})\n"
            f"This week: {completed} jobs completed, {new_leads} new leads, {reviews} reviews received.\n"
            "Write a Monday morning Instagram/Facebook caption."
        )
        raw = await chat.send_message(UserMessage(text=prompt))
        text = raw if isinstance(raw, str) else getattr(raw, "content", str(raw))
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return fallback
        parsed = json.loads(m.group(0))
        parsed["method"] = "llm"
        if not parsed.get("caption"):
            return fallback
        return parsed
    except Exception as e:
        logger.warning("social draft failed: %s", e)
        return fallback
