"""Autonomous Reputation engine — review requests at the perfect moment, AI-drafted
(template-based, no LLM required) responses, sentiment tracking, and
review-site monitoring stubs.

Guardrails (enforced by policy, not just prose):
- NEVER incentivize reviews: request copy offers no discounts, coupons, gift
  cards, or other rewards in exchange for reviews (fake/incentivized reviews
  violate Google/Yelp policy). The request template contains no incentive
  language, and the policy note below is surfaced in the API.
- Respect opt-outs: customers on the review_opt_outs list never receive a
  review request.
- Owner always sees the queue: every auto-request and every auto-posted
  response is recorded and visible in the review inbox.

Collections (all tenant-scoped):
- `reviews`: ingested reviews from review sites (rating, source, text,
  response_status).
- `review_requests`: one doc per (tenant_id, job_id) — dedupe key prevents
  double-asking a customer about the same job.
- `review_responses`: AI-drafted responses, status draft/approved/posted.
- `reputation_policy`: one doc per tenant (auto-post rules).
- `review_opt_outs`: customers who opted out of review requests.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin

router = APIRouter(prefix="/api/reputation", tags=["reputation"])

# Review-site monitoring stubs — TODO(LIVE): wire real monitoring APIs
# (Google Business Profile API, Yelp Fusion, Facebook Graph) per tenant.
MONITORED_SITES: List[Dict] = [
    {"site": "google", "connected": False, "note": "TODO(LIVE): connect Google Business Profile API"},
    {"site": "yelp", "connected": False, "note": "TODO(LIVE): connect Yelp Fusion reviews endpoint"},
    {"site": "facebook", "connected": False, "note": "TODO(LIVE): connect Facebook Graph Page ratings"},
    {"site": "bbb", "connected": False, "note": "TODO(LIVE): connect BBB review monitoring"},
]

POLICY_NOTE = (
    "Reputation policy: never offer incentives, discounts, coupons, or rewards in "
    "exchange for reviews. Fake or incentivized reviews violate Google/Yelp terms "
    "and can get the business listing penalized. All review requests honor the "
    "tenant opt-out list."
)


# ---------- models ----------

class ReviewRequestIn(BaseModel):
    job_id: str = Field(min_length=1)
    customer_name: str = ""
    customer_phone: str = ""
    customer_email: str = ""
    channel: str = Field(default="sms", pattern="^(sms|email)$")
    signal_score: Optional[int] = Field(default=None, ge=1, le=5)  # 5-star-signal hint


class OptOutIn(BaseModel):
    customer_name: str = ""
    customer_phone: str = ""
    customer_email: str = ""


class PolicyIn(BaseModel):
    auto_post_5star: bool = True
    require_approval_low: bool = True


class ReviewSeedIn(BaseModel):
    """Seed an ingested review (used by monitoring ingestion / tests)."""
    rating: int = Field(ge=1, le=5)
    author_name: str = ""
    text: str = ""
    source: str = "manual"
    created_at: Optional[str] = None


# ---------- helpers ----------

def _sentiment(rating: int) -> str:
    if rating >= 4:
        return "positive"
    if rating == 3:
        return "neutral"
    return "negative"


def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _default_policy(tenant_id: str) -> Dict:
    return {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "auto_post_5star": True,
        "require_approval_low": True,
        "updated_at": _now_iso(),
    }


async def _get_policy(db, tenant_id: str) -> Dict:
    doc = await db.reputation_policy.find_one({"tenant_id": tenant_id})
    if doc:
        return doc
    return _default_policy(tenant_id)


def _draft_response_template(rating: int, business_name: str, author_name: str) -> Dict:
    """Deterministic template-based tone matching. No LLM call — the result is
    always labeled a DRAFT for owner review before anything is posted."""
    first = (author_name or "").split()[0] if author_name else "there"
    if rating >= 5:
        tone = "grateful"
        draft = (
            f"Hi {first}, thank you so much for the 5-star review! We're thrilled to "
            f"hear you had a great experience with {business_name}. We truly appreciate "
            f"your business and look forward to serving you again!"
        )
    elif rating == 4:
        tone = "appreciative"
        draft = (
            f"Hi {first}, thank you for the great review and for choosing {business_name}! "
            f"We're glad the visit went well. If there's anything we can do to earn "
            f"that fifth star next time, we'd love to hear about it."
        )
    else:
        tone = "empathetic_action"
        draft = (
            f"Hi {first}, thank you for your honest feedback — and we're sorry the "
            f"visit didn't meet your expectations. This isn't the standard we hold "
            f"ourselves to. A manager will reach out shortly to make this right, and "
            f"you can also contact us directly at any time. — The team at {business_name}"
        )
    return {"tone": tone, "draft": draft, "generated_by": "template-draft-v1", "status": "draft"}


async def _is_opted_out(db, tenant_id: str, phone: str, email: str) -> bool:
    """Check the tenant opt-out list by phone or email."""
    if phone:
        if await db.review_opt_outs.find_one({"tenant_id": tenant_id, "customer_phone": phone}):
            return True
    if email:
        if await db.review_opt_outs.find_one({"tenant_id": tenant_id, "customer_email": email}):
            return True
    return False


# ---------- overview ----------

@router.get("/tenants/overview")
async def reputation_overview(user: dict = Depends(require_tenant_user)):
    """Reputation KPIs: avg rating, review count, response rate, sentiment trend,
    plus review-site monitoring status (stubs)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    docs = await db.reviews.find({"tenant_id": tenant_id}, {"_id": 0}).sort("created_at", -1).to_list(1000)

    count = len(docs)
    avg_rating = round(sum(d.get("rating", 0) for d in docs) / count, 2) if count else 0.0
    responded = sum(1 for d in docs if d.get("response_status") in {"responded", "posted"})
    response_rate = round(responded / count, 3) if count else 0.0

    # Sentiment trend: last 4 full weeks, counts of positive/neutral/negative.
    now = datetime.now(timezone.utc)
    trend = []
    for w in range(4):
        start = now - timedelta(days=7 * (w + 1))
        end = now - timedelta(days=7 * w)
        bucket = {"week_start": start.date().isoformat(), "positive": 0, "neutral": 0, "negative": 0}
        for d in docs:
            dt = _parse_iso(d.get("created_at", ""))
            if dt and start <= dt < end:
                bucket[_sentiment(d.get("rating", 0))] += 1
        trend.append(bucket)
    trend.reverse()

    sentiment_totals = {
        "positive": sum(1 for d in docs if _sentiment(d.get("rating", 0)) == "positive"),
        "neutral": sum(1 for d in docs if _sentiment(d.get("rating", 0)) == "neutral"),
        "negative": sum(1 for d in docs if _sentiment(d.get("rating", 0)) == "negative"),
    }
    pending = await db.review_requests.find(
        {"tenant_id": tenant_id},
        {"_id": 0, "id": 1},
    ).to_list(100000)

    return {
        "avg_rating": avg_rating,
        "review_count": count,
        "response_rate": response_rate,
        "sentiment": sentiment_totals,
        "sentiment_trend": trend,
        "monitoring": MONITORED_SITES,
        "policy": await _get_policy(db, tenant_id),
        "review_requests_sent": len(pending),
        "policy_note": POLICY_NOTE,
    }


# ---------- review requests (perfect-moment asks) ----------

@router.post("/tenants/requests")
async def create_review_request(data: ReviewRequestIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Trigger a review request for a completed job.

    Perfect-moment timing: a strong signal (signal_score >= 4, i.e. the tech
    flagged a 5-star-signal job) sends immediately; anything else is staggered
    +24h to avoid survey fatigue. Dedupes on (tenant_id, job_id) — one request
    per job. Honors the opt-out list. Copy contains NO incentives.
    """
    db = get_db()
    tenant_id = user["tenant_id"]

    existing = await db.review_requests.find_one({"tenant_id": tenant_id, "job_id": data.job_id})
    if existing:
        return {"ok": True, "duplicate": True, "id": existing.get("id"),
                "note": "A review request was already sent for this job."}

    if await _is_opted_out(db, tenant_id, data.customer_phone, data.customer_email):
        raise HTTPException(409, "Customer is on the review opt-out list; request not sent.")

    if not data.customer_phone and not data.customer_email:
        raise HTTPException(400, "customer_phone or customer_email is required")
    if data.channel == "sms" and not data.customer_phone:
        raise HTTPException(400, "customer_phone is required for SMS requests")
    if data.channel == "email" and not data.customer_email:
        raise HTTPException(400, "customer_email is required for email requests")

    # Perfect-moment timing: strong signal -> send now; otherwise stagger +24h.
    now = datetime.now(timezone.utc)
    immediate = (data.signal_score or 0) >= 4
    send_at = now if immediate else now + timedelta(hours=24)

    doc = {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "job_id": data.job_id,
        "customer_name": data.customer_name,
        "customer_phone": data.customer_phone,
        "customer_email": data.customer_email,
        "channel": data.channel,
        "signal_score": data.signal_score,
        "send_at": send_at.isoformat(),
        "status": "sent",
        "created_at": _now_iso(),
        "timing": "perfect-moment" if immediate else "staggered",
    }
    await db.review_requests.insert_one(doc)

    # TODO(LIVE): actually dispatch via services.twilio.send_sms / services.email.send_email
    # using the tenant's branded review link. Copy stays incentive-free per POLICY_NOTE.
    print(f"[REPUTATION review-request {data.channel} -> "
          f"{data.customer_phone or data.customer_email}] job={data.job_id} "
          f"timing={doc['timing']} (stubbed send)")
    doc.pop("_id", None)
    return {"ok": True, "duplicate": False, **doc}


# ---------- review inbox ----------

@router.get("/tenants/reviews")
async def list_reviews(
    needs_response: bool = False,
    rating: Optional[int] = None,
    user: dict = Depends(require_tenant_user),
):
    """Review inbox. Filter with ?needs_response=true or ?rating=5."""
    db = get_db()
    docs = await db.reviews.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).sort("created_at", -1).to_list(1000)
    # Attach the latest response draft (if any) so owners always see the queue.
    responses = await db.review_responses.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}).to_list(10000)
    by_review: Dict[str, Dict] = {}
    for resp in responses:
        rid = resp.get("review_id")
        if rid and (rid not in by_review or str(resp.get("created_at", "")) > str(by_review[rid].get("created_at", ""))):
            by_review[rid] = resp
    out = []
    for d in docs:
        if needs_response and d.get("response_status") in {"responded", "posted"}:
            continue
        if rating is not None and d.get("rating") != rating:
            continue
        resp = by_review.get(d.get("id"))
        if resp:
            d = {**d, "draft_text": resp.get("draft"), "draft_tone": resp.get("tone"),
                 "draft_status": resp.get("status")}
        out.append(d)
    return {"reviews": out, "count": len(out)}


@router.post("/tenants/reviews/seed")
async def seed_review(data: ReviewSeedIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Ingest a review (monitoring pipelines / tests). Real monitoring sites
    call this when new reviews are detected — TODO(LIVE)."""
    db = get_db()
    doc = {
        "id": _uuid(),
        "tenant_id": user["tenant_id"],
        "rating": data.rating,
        "author_name": data.author_name,
        "text": data.text,
        "source": data.source,
        "response_status": "needs_response",
        "created_at": data.created_at or _now_iso(),
    }
    await db.reviews.insert_one(doc)
    doc.pop("_id", None)
    return {"ok": True, **doc}


# ---------- AI-draft responses ----------

@router.post("/tenants/reviews/{review_id}/draft")
async def draft_response(review_id: str, user: dict = Depends(require_tenant_user)):
    """AI-draft a response (deterministic tone-matched template, no LLM).

    Tone: grateful for 5★, appreciative for 4★, empathetic+action for ≤3★.
    The result is always a DRAFT — nothing posts without owner approval
    unless the tenant's auto-post policy covers it.
    """
    db = get_db()
    review = await db.reviews.find_one({"tenant_id": user["tenant_id"], "id": review_id})
    if not review:
        raise HTTPException(404, "Review not found")

    tenant = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0, "name": 1}) or {}
    business_name = tenant.get("name") or "our business"
    tpl = _draft_response_template(review.get("rating", 0), business_name, review.get("author_name", ""))

    policy = await _get_policy(db, user["tenant_id"])
    auto_posted = bool(policy.get("auto_post_5star") and review.get("rating", 0) >= 5)

    now = _now_iso()
    response_doc = {
        "id": _uuid(),
        "tenant_id": user["tenant_id"],
        "review_id": review_id,
        "draft": tpl["draft"],
        "tone": tpl["tone"],
        "generated_by": tpl["generated_by"],
        "status": "posted" if auto_posted else "draft",
        "created_at": now,
        "auto_posted": auto_posted,
        "posted_at": now if auto_posted else None,
    }
    await db.review_responses.insert_one(response_doc)
    await db.reviews.update_one(
        {"tenant_id": user["tenant_id"], "id": review_id},
        {"$set": {"response_status": "posted" if auto_posted else "drafted",
                  "updated_at": now}},
    )
    # TODO(LIVE): when auto_posted, actually post via the review site's API.
    response_doc.pop("_id", None)
    return {"ok": True, "draft_labeled": True, **response_doc}


@router.post("/tenants/reviews/{review_id}/approve")
async def approve_response(review_id: str, user: dict = Depends(require_tenant_owner_or_admin)):
    """Owner approves a drafted response and marks it posted.

    TODO(LIVE): actually POST the approved text to the review site
    (Google Business Profile / Yelp) via the tenant's connected account.
    """
    db = get_db()
    review = await db.reviews.find_one({"tenant_id": user["tenant_id"], "id": review_id})
    if not review:
        raise HTTPException(404, "Review not found")

    resp = await db.review_responses.find_one(
        {"tenant_id": user["tenant_id"], "review_id": review_id})
    if not resp or resp.get("status") != "draft":
        raise HTTPException(409, "No draft response awaiting approval for this review")

    # TODO(LIVE): post resp["draft"] to the review site here.
    print(f"[REPUTATION post-response {review.get('source')}] review={review_id} "
          f"(stubbed site post)")
    now = _now_iso()
    await db.review_responses.update_one(
        {"tenant_id": user["tenant_id"], "id": resp["id"]},
        {"$set": {"status": "posted", "posted_at": now, "approved_by": user.get("id")}},
    )
    await db.reviews.update_one(
        {"tenant_id": user["tenant_id"], "id": review_id},
        {"$set": {"response_status": "posted", "responded_at": now, "updated_at": now}},
    )
    return {"ok": True, "posted": True, "review_id": review_id}


# ---------- policy ----------

@router.post("/tenants/policy")
async def set_policy(data: PolicyIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Set the auto-post policy: auto-post 5★ drafts, require approval otherwise."""
    db = get_db()
    doc = {
        "tenant_id": user["tenant_id"],
        "auto_post_5star": data.auto_post_5star,
        "require_approval_low": data.require_approval_low,
        "updated_at": _now_iso(),
    }
    existing = await db.reputation_policy.find_one({"tenant_id": user["tenant_id"]})
    if existing:
        await db.reputation_policy.update_one({"tenant_id": user["tenant_id"]}, {"$set": doc})
    else:
        doc["id"] = _uuid()
        await db.reputation_policy.insert_one(doc)
    doc.pop("_id", None)
    return {"ok": True, **doc}


# ---------- opt-outs ----------

@router.post("/tenants/opt-outs")
async def add_opt_out(data: OptOutIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Add a customer to the review opt-out list (phone or email required)."""
    if not data.customer_phone and not data.customer_email:
        raise HTTPException(400, "customer_phone or customer_email is required")
    db = get_db()
    doc = {
        "id": _uuid(),
        "tenant_id": user["tenant_id"],
        "customer_name": data.customer_name,
        "customer_phone": data.customer_phone,
        "customer_email": data.customer_email,
        "created_at": _now_iso(),
    }
    await db.review_opt_outs.insert_one(doc)
    doc.pop("_id", None)
    return {"ok": True, **doc}


@router.get("/tenants/opt-outs")
async def list_opt_outs(user: dict = Depends(require_tenant_user)):
    db = get_db()
    docs = await db.review_opt_outs.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).to_list(1000)
    return {"opt_outs": docs, "count": len(docs)}
