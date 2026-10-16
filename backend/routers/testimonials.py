"""Testimonial Auto-Converter — 5-star reviews become shareable testimonials.

Flow:
1. Review captured with rating >= 5 → create `testimonials` record with
   `status=consent_pending` and a one-time `consent_token`.
2. Primary ask: email with "Yes, share my review" link.
3. Followup: SMS via Twilio ("Reply YES to share.") scheduled a few hours after
   the email if no response.
4. Customer clicks link → sees polished AI testimonial preview → approves or
   rejects. Approval → `admin_review`.
5. Admin approves → `published` and visible on the public tenant page.

The AI polish uses the AI key (AI_API_KEY) with a safe deterministic fallback.
"""
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from db import get_db
from models import _uuid, _now_iso
from security import require_tenant_user, require_tenant_owner_or_admin
from services.email import send_email, followup_html
from services.twilio import send_sms

logger = logging.getLogger("testimonials")
router = APIRouter(prefix="/testimonials", tags=["testimonials"])
public_router = APIRouter(prefix="/public/testimonials", tags=["public-testimonials"])


# ---------- Models ----------
class TestimonialSeedIn(BaseModel):
    """Manual seed (e.g. from Reviews UI when importing an existing 5-star review)."""
    customer_name: str
    customer_email: Optional[EmailStr] = None
    customer_phone: Optional[str] = None
    rating: int = Field(ge=1, le=5)
    original_text: str


class TestimonialDecision(BaseModel):
    decision: Literal["approve", "reject"]
    admin_note: Optional[str] = None


class CustomerConsentIn(BaseModel):
    decision: Literal["approve", "reject"]
    edited_text: Optional[str] = None  # customer may tweak polished version


# ---------- LLM polish ----------
async def polish_testimonial(text: str, name: str, biz: str, lang: str = "en") -> str:
    """Polish a raw review into a short shareable testimonial. Safe fallback included."""
    text = (text or "").strip()
    if not text:
        return ""
    # Deterministic fallback: tidy whitespace, strip over-long rambles.
    def _fallback() -> str:
        cleaned = " ".join(text.split())
        if len(cleaned) > 240:
            cleaned = cleaned[:237].rsplit(" ", 1)[0] + "..."
        return cleaned

    from llm_portable import ai_api_key
    key = ai_api_key()
    if not key:
        return _fallback()
    try:
        from llm_portable import LlmChat, UserMessage
        if lang.startswith("es"):
            system = (
                "Eres un editor de marketing. Convierte una reseña bruta de un cliente en un "
                "testimonio breve (máx. 240 caracteres), cálido y en primera persona, en ESPAÑOL. "
                "NO inventes hechos. Mantén el tono y la autenticidad del cliente. "
                "Devuelve SOLO el testimonio sin comillas ni etiquetas."
            )
            user = f"Negocio: {biz}\nCliente: {name}\nReseña:\n{text}"
        else:
            system = (
                "You are a marketing editor. Convert a raw customer review into a short, "
                "warm, first-person testimonial (max 240 chars). DO NOT invent facts. "
                "Keep the customer's tone and authenticity. Return ONLY the testimonial "
                "with no quotes or labels."
            )
            user = f"Business: {biz}\nCustomer: {name}\nReview:\n{text}"
        chat = LlmChat(
            api_key=key,
            session_id=f"testi-{hash(text) & 0xffff}",
            system_message=system,
        ).with_model("openai", "gpt-6-sol")
        raw = await chat.send_message(UserMessage(text=user))
        polished = (raw or "").strip().strip('"').strip("'")
        if not polished or len(polished) > 400:
            return _fallback()
        return polished
    except Exception as e:
        logger.warning("polish fallback: %s", e)
        return _fallback()


# ---------- Core create / trigger ----------
async def start_testimonial_flow(tenant_id: str, review: dict) -> Optional[dict]:
    """Called from the reviews pipeline when rating >= 5."""
    if int(review.get("rating") or 0) < 5:
        return None
    db = get_db()
    tenant = await db.tenants.find_one({"id": tenant_id}, {"_id": 0, "name": 1, "lang": 1, "slug": 1}) or {}
    biz = tenant.get("name") or "us"
    lang = (tenant.get("lang") or "en").lower()
    frontend_base = os.environ.get("FRONTEND_URL") or os.environ.get("REACT_APP_BACKEND_URL") or os.environ.get("APP_BASE_URL") or ""
    # Idempotency: don't re-seed if we already have one for this review
    existing = await db.testimonials.find_one(
        {"tenant_id": tenant_id, "review_id": review.get("id")}, {"_id": 0}
    )
    if existing:
        return existing
    polished = await polish_testimonial(
        text=review.get("text", ""),
        name=review.get("customer_name", ""),
        biz=biz,
        lang=lang,
    )
    doc = {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "review_id": review.get("id"),
        "customer_name": review.get("customer_name") or "A happy customer",
        "customer_email": review.get("customer_email"),
        "customer_phone": review.get("customer_phone"),
        "rating": int(review["rating"]),
        "original_text": review.get("text", ""),
        "polished_text": polished,
        "status": "consent_pending",
        "consent_token": _uuid(),
        "consent_sent_channels": {"email": None, "sms": None},
        "customer_approved": False,
        "admin_approved": False,
        "customer_decided_at": None,
        "admin_decided_at": None,
        "admin_note": None,
        "published_at": None,
        "lang": lang,
        "created_at": _now_iso(),
    }
    await db.testimonials.insert_one(doc)

    # 1) Email ask (primary channel)
    consent_url = f"{frontend_base}/t/consent/{doc['consent_token']}" if frontend_base else f"/t/consent/{doc['consent_token']}"
    if doc["customer_email"]:
        if lang.startswith("es"):
            subject = f"¿Compartimos tu reseña, {doc['customer_name']}?"
            msg = (
                f"¡Hola {doc['customer_name']}! Gracias por la reseña de 5 estrellas de {biz}. "
                f"¿Nos permites compartirla como testimonio? "
                f'<br/><br/><a href="{consent_url}" style="background:#111;color:#fff;padding:10px 16px;border-radius:8px;text-decoration:none">Sí, compartir mi reseña</a>'
                f'<br/><br/>Previsualiza y edita si lo deseas.'
            )
        else:
            subject = f"May we share your review, {doc['customer_name']}?"
            msg = (
                f"Hi {doc['customer_name']} — thank you for the 5-star review of {biz}! "
                f"May we feature it as a testimonial? "
                f'<br/><br/><a href="{consent_url}" style="background:#111;color:#fff;padding:10px 16px;border-radius:8px;text-decoration:none">Yes, share my review</a>'
                f"<br/><br/>You'll see a preview you can tweak before we publish."
            )
        try:
            html = followup_html(business=biz, message=msg)
            res = await send_email(to=doc["customer_email"], subject=subject, html=html, from_name=biz)
            await db.testimonials.update_one(
                {"id": doc["id"]}, {"$set": {"consent_sent_channels.email": res}}
            )
        except Exception as e:
            logger.warning("testi email send failed: %s", e)

    # 2) Schedule SMS followup 4h later (if phone)
    if doc["customer_phone"]:
        send_at = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
        if lang.startswith("es"):
            body = f"Hola {doc['customer_name']}, ¿podemos compartir tu reseña? Responde SI o abre: {consent_url}"
        else:
            body = f"Hi {doc['customer_name']} — may we share your review? Reply YES or open: {consent_url}"
        await db.followup_jobs.update_one(
            {"tenant_id": tenant_id, "kind": "testi-consent-sms", "to": doc["customer_phone"], "testimonial_id": doc["id"]},
            {"$set": {
                "id": _uuid(), "tenant_id": tenant_id, "testimonial_id": doc["id"],
                "kind": "testi-consent-sms", "channel": "sms",
                "to": doc["customer_phone"], "body": body,
                "send_at": send_at, "status": "scheduled",
                "trigger_label": "testimonial-consent", "created_at": _now_iso(),
            }},
            upsert=True,
        )

    doc.pop("_id", None)
    return doc


# ---------- Owner / admin endpoints ----------
@router.post("/seed")
async def seed_from_review(payload: TestimonialSeedIn, user: dict = Depends(require_tenant_user)):
    if payload.rating < 5:
        raise HTTPException(400, "Only 5-star reviews can be converted into testimonials")
    review_like = {
        "id": _uuid(),
        "customer_name": payload.customer_name,
        "customer_email": payload.customer_email,
        "customer_phone": payload.customer_phone,
        "rating": payload.rating,
        "text": payload.original_text,
    }
    result = await start_testimonial_flow(user["tenant_id"], review_like)
    if not result:
        raise HTTPException(400, "Could not start testimonial flow")
    return result


@router.get("")
async def list_testimonials(
    user: dict = Depends(require_tenant_user),
    status: Optional[str] = None,
    limit: int = 100,
):
    db = get_db()
    q: dict = {"tenant_id": user["tenant_id"]}
    if status:
        q["status"] = status
    rows = await db.testimonials.find(q, {"_id": 0, "consent_token": 0}).sort("created_at", -1).to_list(limit)
    return rows


@router.post("/{tid}/decision")
async def admin_decision(tid: str, payload: TestimonialDecision, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    t = await db.testimonials.find_one({"id": tid, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not t:
        raise HTTPException(404, "Testimonial not found")
    if t["status"] not in ("admin_review", "consent_received"):
        raise HTTPException(400, f"Not in admin_review (current: {t['status']})")
    patch = {
        "admin_approved": payload.decision == "approve",
        "admin_decided_at": _now_iso(),
        "admin_note": payload.admin_note,
        "status": "published" if payload.decision == "approve" else "rejected",
    }
    if payload.decision == "approve":
        patch["published_at"] = _now_iso()
    await db.testimonials.update_one({"id": tid, "tenant_id": user["tenant_id"]}, {"$set": patch})
    return {"ok": True, **patch}


@router.delete("/{tid}")
async def delete_testimonial(tid: str, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    res = await db.testimonials.delete_one({"id": tid, "tenant_id": user["tenant_id"]})
    if res.deleted_count == 0:
        raise HTTPException(404, "Not found")
    return {"ok": True}


# ---------- Public endpoints ----------
@public_router.get("/consent/{token}")
async def consent_page(token: str):
    db = get_db()
    t = await db.testimonials.find_one({"consent_token": token}, {"_id": 0, "tenant_id": 1, "customer_name": 1, "original_text": 1, "polished_text": 1, "status": 1, "rating": 1, "lang": 1})
    if not t:
        raise HTTPException(404, "Invalid or expired link")
    tenant = await db.tenants.find_one({"id": t["tenant_id"]}, {"_id": 0, "name": 1}) or {}
    return {
        "business": tenant.get("name"),
        "customer_name": t.get("customer_name"),
        "rating": t.get("rating"),
        "original_text": t.get("original_text"),
        "polished_text": t.get("polished_text"),
        "status": t.get("status"),
        "lang": t.get("lang") or "en",
    }


@public_router.post("/consent/{token}")
async def customer_decision(token: str, payload: CustomerConsentIn):
    db = get_db()
    t = await db.testimonials.find_one({"consent_token": token}, {"_id": 0})
    if not t:
        raise HTTPException(404, "Invalid or expired link")
    if t["status"] != "consent_pending":
        return {"ok": True, "status": t["status"], "note": "Already decided"}
    patch = {
        "customer_approved": payload.decision == "approve",
        "customer_decided_at": _now_iso(),
    }
    if payload.edited_text:
        patch["polished_text"] = payload.edited_text.strip()[:400]
    if payload.decision == "approve":
        patch["status"] = "admin_review"
    else:
        patch["status"] = "rejected"
    await db.testimonials.update_one({"id": t["id"]}, {"$set": patch})
    return {"ok": True, "status": patch["status"]}


@public_router.get("/by-slug/{slug}")
async def published_for_slug(slug: str, limit: int = 20):
    db = get_db()
    tenant = await db.tenants.find_one({"slug": slug}, {"_id": 0, "id": 1}) or {}
    if not tenant:
        raise HTTPException(404, "Business not found")
    rows = await db.testimonials.find(
        {"tenant_id": tenant["id"], "status": "published"},
        {"_id": 0, "consent_token": 0, "customer_email": 0, "customer_phone": 0},
    ).sort("published_at", -1).to_list(limit)
    return rows
