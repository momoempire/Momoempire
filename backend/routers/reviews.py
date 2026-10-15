"""Review Autopilot — tenant sends requests, customers respond via public link."""
import os
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone
from pydantic import BaseModel
from db import get_db
from models import ReviewRequest, ReviewRequestIn
from security import require_tenant_user
from routers.usage import record_usage
from services.email import send_email, review_request_html
from services.twilio import send_sms

router = APIRouter(prefix="/tenants/reviews", tags=["reviews"])


@router.get("")
async def list_reviews(user: dict = Depends(require_tenant_user)):
    db = get_db()
    return await db.review_requests.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).sort("created_at", -1).to_list(500)


@router.post("")
async def create_review_request(data: ReviewRequestIn, user: dict = Depends(require_tenant_user)):
    db = get_db()
    rr = ReviewRequest(tenant_id=user["tenant_id"], **data.model_dump())
    doc = rr.model_dump()
    await db.review_requests.insert_one(doc)
    doc.pop("_id", None)
    channel = data.channel
    tenant = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0, "name": 1, "review_url": 1}) or {}
    biz = tenant.get("name", "us")
    frontend = os.environ.get("FRONTEND_URL") or ""
    review_url = tenant.get("review_url") or (f"{frontend.rstrip('/')}/reviews/{doc['public_token']}" if frontend else f"/reviews/{doc['public_token']}")
    msg = data.message or f"Hi {data.customer_name}, thanks for choosing {biz}! Could you share a quick review? {review_url}"
    try:
        if channel == "sms" and data.customer_phone:
            res = await send_sms(tenant_id=user["tenant_id"], to=data.customer_phone, body=msg)
            await record_usage(user["tenant_id"], "sms", 1, {"review_request_id": doc["id"], "twilio_status": res.get("status")})
        elif channel == "email" and data.customer_email:
            html = review_request_html(business=biz, review_url=review_url, customer_name=data.customer_name)
            await send_email(to=data.customer_email, subject=f"Quick favor from {biz}?",
                            html=html, from_name=biz)
    except Exception as e:
        print(f"[REVIEW-REQ send error] {e}")
    print(f"[REVIEW-REQ {channel}->{data.customer_phone or data.customer_email}] {msg} (token={doc['public_token']})")
    return doc


# ---------- Public (customer-facing) submission ----------
public_router = APIRouter(prefix="/public/reviews", tags=["reviews-public"])


class ReviewSubmit(BaseModel):
    rating: int
    comment: str = ""


@public_router.get("/{token}")
async def get_review_request(token: str):
    db = get_db()
    r = await db.review_requests.find_one({"public_token": token}, {"_id": 0})
    if not r:
        raise HTTPException(404, "Request not found")
    tenant = await db.tenants.find_one({"id": r["tenant_id"]}, {"_id": 0, "ai_employee": 0})
    return {"request": r, "tenant": {"name": tenant.get("name"), "slug": tenant.get("slug"), "branding": tenant.get("branding")}}


@public_router.post("/{token}")
async def submit_review(token: str, data: ReviewSubmit):
    db = get_db()
    r = await db.review_requests.find_one({"public_token": token})
    if not r:
        raise HTTPException(404, "Request not found")
    if data.rating < 1 or data.rating > 5:
        raise HTTPException(400, "Rating 1-5")
    await db.review_requests.update_one(
        {"public_token": token},
        {"$set": {
            "rating": data.rating, "comment": data.comment, "status": "responded",
            "responded_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    # Auto-convert 5-star reviews into testimonials (consent-first flow).
    if data.rating >= 5:
        try:
            from routers.testimonials import start_testimonial_flow
            await start_testimonial_flow(r["tenant_id"], {
                "id": r.get("id") or token,
                "customer_name": r.get("customer_name"),
                "customer_email": r.get("customer_email"),
                "customer_phone": r.get("customer_phone"),
                "rating": data.rating,
                "text": data.comment or "",
            })
        except Exception as e:
            print(f"[REVIEW->TESTIMONIAL] {e}")
    return {"status": "ok"}
