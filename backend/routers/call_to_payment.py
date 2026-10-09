"""Round 2 — Call-to-Payment vertical flow.

Reuses existing primitives:
    Lead → Estimate (as 'quote') → Appointment (as 'job') → Invoice → Payment

Adds:
    * Stripe Connect Standard onboarding per tenant (direct charges).
    * Grounded quote drafting from the tenant's own Services catalog (never invents prices).
    * Owner-approval gate before a quote reaches the customer.
    * Customer acceptance via public token → appointment + invoice (deposit or full).
    * Idempotent connected-account webhook handling via `webhook_events` collection.
    * Overdue-reminder cron that stops when paid and respects paused_at.
    * Owner "needs attention" aggregator.

Design notes:
    * Platform subscriptions (tenants paying us) stay in `routers/payments.py`.
    * Customer payments to tenants are DIRECT CHARGES — PaymentIntent is created with
      the `Stripe-Account` request-option so funds settle on the connected account.
      No application fee is taken (configurable per tenant later).
    * Duplicate webhooks are safe: each `event.id` is inserted into `webhook_events`
      with a unique index; a duplicate upsert returns a 200 without re-processing.
"""
from __future__ import annotations
import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Literal
import stripe
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field, EmailStr

from db import get_db
from models import _uuid, _now_iso
from models_phase2 import EstimateLine
from security import require_tenant_user, require_tenant_owner_or_admin
from services.email import send_email, followup_html

log = logging.getLogger("c2p")
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY") or ""

router = APIRouter(prefix="/c2p", tags=["call-to-payment"])
public_router = APIRouter(prefix="/public/c2p", tags=["call-to-payment-public"])

# ---------------- helpers ----------------
def _sum_lines(lines: List[dict]) -> float:
    total = 0.0
    for l in lines or []:
        total += float(l.get("quantity") or 1) * float(l.get("unit_price") or 0)
    return round(total, 2)


async def _ensure_customer(db, tenant_id: str, name: str, email: Optional[str], phone: Optional[str]) -> str:
    """De-dupe customers by (tenant, email) or (tenant, phone). Returns customer id."""
    q = {"tenant_id": tenant_id, "$or": []}
    if email:
        q["$or"].append({"email": email})
    if phone:
        q["$or"].append({"phone": phone})
    if q["$or"]:
        existing = await db.customers.find_one(q, {"id": 1, "_id": 0})
        if existing:
            return existing["id"]
    doc = {
        "id": _uuid(), "tenant_id": tenant_id, "name": name,
        "email": email or None, "phone": phone or "", "notes": "", "tags": [],
        "created_at": _now_iso(),
    }
    await db.customers.insert_one(doc)
    return doc["id"]


# ================== STRIPE CONNECT STANDARD ==================
@router.post("/connect/onboard")
async def connect_onboard(request: Request, user: dict = Depends(require_tenant_owner_or_admin)):
    """Create a Stripe Standard connected account for this tenant and return
    an onboarding account_link URL. Idempotent — reuses existing account."""
    if not stripe.api_key:
        raise HTTPException(503, "Stripe not configured")
    db = get_db()
    t = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0})
    if not t:
        raise HTTPException(404, "Tenant not found")
    acct_id = t.get("stripe_connect_id")
    if not acct_id:
        try:
            acct = stripe.Account.create(
                type="standard",
                email=user.get("email"),
                business_profile={"name": t.get("name", "")},
                metadata={"tenant_id": user["tenant_id"]},
            )
        except stripe.error.InvalidRequestError as e:
            msg = str(e)
            if "signed up for Connect" in msg or "Connect" in msg:
                raise HTTPException(503, "Stripe Connect is not enabled on this platform account. Enable it at https://dashboard.stripe.com/connect, then retry.")
            raise HTTPException(502, f"Stripe error: {msg[:200]}")
        except stripe.error.StripeError as e:
            raise HTTPException(502, f"Stripe error: {str(e)[:200]}")
        acct_id = acct["id"]
        await db.tenants.update_one(
            {"id": user["tenant_id"]},
            {"$set": {
                "stripe_connect_id": acct_id,
                "stripe_connect_status": "pending",
                "updated_at": _now_iso(),
            }},
        )
    frontend = os.environ.get("FRONTEND_URL") or str(request.base_url).rstrip("/")
    try:
        link = stripe.AccountLink.create(
            account=acct_id,
            refresh_url=f"{frontend}/app/billing?connect=refresh",
            return_url=f"{frontend}/app/billing?connect=return",
            type="account_onboarding",
        )
    except stripe.error.StripeError as e:
        raise HTTPException(502, f"Could not create onboarding link: {str(e)[:200]}")
    return {"url": link["url"], "account_id": acct_id}


@router.get("/connect/status")
async def connect_status(user: dict = Depends(require_tenant_user)):
    db = get_db()
    t = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0}) or {}
    acct_id = t.get("stripe_connect_id")
    if not acct_id or not stripe.api_key:
        return {"connected": False, "charges_enabled": False, "payouts_enabled": False}
    try:
        acct = stripe.Account.retrieve(acct_id)
        charges = bool(acct.get("charges_enabled"))
        payouts = bool(acct.get("payouts_enabled"))
        status = "active" if (charges and payouts) else "pending"
        if t.get("stripe_connect_status") != status:
            await db.tenants.update_one(
                {"id": user["tenant_id"]},
                {"$set": {"stripe_connect_status": status, "updated_at": _now_iso()}},
            )
        return {
            "connected": True, "account_id": acct_id, "status": status,
            "charges_enabled": charges, "payouts_enabled": payouts,
            "requirements_due": (acct.get("requirements") or {}).get("currently_due") or [],
        }
    except Exception as e:
        log.exception("connect status failed: %s", e)
        return {"connected": True, "status": "error", "error": str(e)[:200]}


@router.post("/connect/disconnect")
async def connect_disconnect(user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    await db.tenants.update_one(
        {"id": user["tenant_id"]},
        {"$set": {"stripe_connect_id": None, "stripe_connect_status": "disconnected", "updated_at": _now_iso()}},
    )
    return {"ok": True}


# ================== QUOTES (grounded, owner-approved) ==================
class QuoteDraftIn(BaseModel):
    lead_id: Optional[str] = None
    customer_name: str
    customer_email: Optional[EmailStr] = None
    customer_phone: Optional[str] = None
    title: str
    service_ids: List[str] = Field(default_factory=list)  # grounded prices
    extra_lines: List[EstimateLine] = Field(default_factory=list)  # owner-added
    notes: str = ""
    expires_in_days: int = 14


class QuoteDecisionIn(BaseModel):
    decision: Literal["approve_and_send", "reject"]
    customer_note: Optional[str] = None


@router.post("/quotes/draft")
async def draft_quote(data: QuoteDraftIn, user: dict = Depends(require_tenant_user)):
    """Draft a quote. Line items for `service_ids` are pulled from the tenant's
    own services catalog — the AI/caller NEVER sets prices. Owner can add
    `extra_lines` by hand (anything the AI said in free-form must be reviewed)."""
    db = get_db()
    lines: List[dict] = []
    if data.service_ids:
        svcs = await db.services.find(
            {"tenant_id": user["tenant_id"], "id": {"$in": data.service_ids}}, {"_id": 0}
        ).to_list(100)
        for s in svcs:
            lines.append({
                "description": s["name"],
                "quantity": 1,
                "unit_price": float(s.get("price") or 0),
                "service_id": s["id"],
            })
    for l in data.extra_lines or []:
        lines.append({
            "description": l.description,
            "quantity": float(l.quantity or 1),
            "unit_price": float(l.unit_price or 0),
            "service_id": None,
        })
    cust_id = await _ensure_customer(
        db, user["tenant_id"], data.customer_name, data.customer_email, data.customer_phone or ""
    )
    doc = {
        "id": _uuid(),
        "tenant_id": user["tenant_id"],
        "lead_id": data.lead_id,
        "customer_id": cust_id,
        "customer_name": data.customer_name,
        "customer_email": data.customer_email,
        "customer_phone": data.customer_phone or "",
        "title": data.title,
        "lines": lines,
        "notes": data.notes,
        "total": _sum_lines(lines),
        "status": "draft",            # draft → sent → approved → declined
        "owner_approved": False,
        "public_token": _uuid(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=max(1, data.expires_in_days))).isoformat(),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    await db.estimates.insert_one(doc)
    doc.pop("_id", None)
    return doc


@router.get("/quotes")
async def list_quotes(user: dict = Depends(require_tenant_user), status: Optional[str] = None):
    db = get_db()
    q: dict = {"tenant_id": user["tenant_id"]}
    if status:
        q["status"] = status
    rows = await db.estimates.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
    return rows


@router.post("/quotes/{qid}/decision")
async def owner_decision(qid: str, data: QuoteDecisionIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Owner approval gate. Only approved quotes get a public link emailed."""
    db = get_db()
    quote = await db.estimates.find_one({"id": qid, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not quote:
        raise HTTPException(404, "Quote not found")
    if quote["status"] not in ("draft", "sent"):
        raise HTTPException(400, f"Cannot decide on a {quote['status']} quote")
    if data.decision == "reject":
        await db.estimates.update_one(
            {"id": qid, "tenant_id": user["tenant_id"]},
            {"$set": {"status": "declined", "owner_approved": False, "updated_at": _now_iso()}},
        )
        return {"ok": True, "status": "declined"}
    patch = {"status": "sent", "owner_approved": True, "updated_at": _now_iso()}
    await db.estimates.update_one({"id": qid, "tenant_id": user["tenant_id"]}, {"$set": patch})

    # Email customer with public link
    tenant = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0, "name": 1, "slug": 1}) or {}
    from public_links import quote_link
    link = quote_link(quote["public_token"])
    if quote.get("customer_email"):
        msg = (
            f"Hi {quote['customer_name']},<br/><br/>"
            f"Your quote for <b>{quote['title']}</b> is ready. "
            f"Total: <b>${quote['total']:.2f}</b>."
            + (f"<br/><br/><i>{data.customer_note}</i>" if data.customer_note else "")
            + f'<br/><br/><a href="{link}" style="background:#111;color:#fff;padding:10px 16px;'
              f'border-radius:8px;text-decoration:none">Review & accept</a>'
        )
        try:
            html = followup_html(business=tenant.get("name", "us"), message=msg)
            await send_email(
                to=quote["customer_email"],
                subject=f"Your quote: {quote['title']}",
                html=html,
                from_name=tenant.get("name", "AI Office"),
            )
        except Exception as e:
            log.warning("quote email send failed: %s", e)
    return {"ok": True, "status": "sent", "public_link": link}


# -------- Public quote endpoints (customer-facing) --------
# EMP-W-CF-022: a draft is not public yet (same 404 as an unknown token), and expires_at is
# enforced. TODO(Brann): expired quotes stay viewable (marked expired) but can't be accepted;
# re-approving an expired quote does not extend expires_at.
def _quote_expired(q: dict, now: datetime | None = None) -> bool:
    raw = q.get("expires_at")
    if not raw:
        return False  # CRM estimates may have no expiry
    try:
        exp = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False  # unparseable legacy value: treat as no expiry rather than block the customer
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) >= exp


@public_router.get("/quotes/{token}")
async def public_quote(token: str):
    db = get_db()
    q = await db.estimates.find_one({"public_token": token}, {"_id": 0})
    if not q or q.get("status") == "draft":
        raise HTTPException(404, "Not found")
    q["expired"] = _quote_expired(q)
    q["can_accept"] = bool(q.get("owner_approved")) and not q["expired"] \
        and q.get("status") not in ("declined", "approved", "accepted")
    # Hide owner's internal notes
    q.pop("notes", None)
    tenant = await db.tenants.find_one({"id": q["tenant_id"]}, {"_id": 0, "name": 1, "slug": 1, "stripe_connect_id": 1}) or {}
    q["business"] = {"name": tenant.get("name"), "slug": tenant.get("slug"),
                     "accepts_online_pay": bool(tenant.get("stripe_connect_id"))}
    return q


class QuoteAcceptIn(BaseModel):
    deposit_cents: int = Field(default=0, ge=0)  # 0 = pay in full after job; >0 = deposit now
    scheduled_at: Optional[str] = None  # ISO; optional booking preference


@public_router.post("/quotes/{token}/accept")
async def accept_quote(token: str, data: QuoteAcceptIn):
    """Customer accepts the quote → creates an Appointment (job) + an Invoice.
    Idempotent: a second accept returns the same invoice/appointment."""
    db = get_db()
    quote = await db.estimates.find_one({"public_token": token}, {"_id": 0})
    if not quote or quote.get("status") == "draft":
        raise HTTPException(404, "Quote not found")
    if not quote.get("owner_approved"):
        raise HTTPException(400, "Quote not approved yet")
    if quote["status"] in ("declined",):
        raise HTTPException(400, "Quote was declined")

    # Idempotency: existing invoice linked to this quote?
    existing_inv = await db.invoices.find_one({"quote_id": quote["id"]}, {"_id": 0})
    if existing_inv:
        return {"ok": True, "idempotent": True, "invoice": existing_inv}
    if _quote_expired(quote):  # after the idempotent re-accept, which must keep working
        raise HTTPException(400, "Quote has expired")

    now = _now_iso()
    # Create appointment (job)
    service_id = next((l.get("service_id") for l in quote["lines"] if l.get("service_id")), None)
    svc = await db.services.find_one({"id": service_id, "tenant_id": quote["tenant_id"]}, {"_id": 0}) if service_id else None
    dur = int((svc or {}).get("duration_minutes") or 60)
    start = data.scheduled_at or (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0).isoformat()
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except Exception:
        start_dt = datetime.now(timezone.utc) + timedelta(days=3)
    end_dt = start_dt + timedelta(minutes=dur)
    appt = {
        "id": _uuid(), "tenant_id": quote["tenant_id"],
        "customer_id": quote["customer_id"],
        "customer_name": quote["customer_name"],
        "customer_phone": quote.get("customer_phone", ""),
        "customer_email": quote.get("customer_email"),
        "lead_id": quote.get("lead_id"),
        "service_id": service_id,
        "service_name": (svc or {}).get("name") or quote["title"],
        "staff": "", "start_at": start_dt.isoformat(), "end_at": end_dt.isoformat(),
        "status": "scheduled", "notes": "", "quote_id": quote["id"],
        "created_at": now,
    }
    # Guard: no double-booking at same start_at for same staff
    conflict = await db.appointments.find_one({
        "tenant_id": quote["tenant_id"], "staff": appt["staff"],
        "start_at": appt["start_at"], "status": {"$in": ["scheduled", "confirmed"]},
    }, {"id": 1, "_id": 0})
    if conflict:
        start_dt = start_dt + timedelta(minutes=dur)
        appt["start_at"] = start_dt.isoformat()
        appt["end_at"] = (start_dt + timedelta(minutes=dur)).isoformat()
    await db.appointments.insert_one(appt)

    # Create invoice (deposit now if requested; remainder on job completion)
    total_cents = int(round(float(quote["total"]) * 100))
    deposit = min(max(0, data.deposit_cents), total_cents)
    invoice = {
        "id": _uuid(), "tenant_id": quote["tenant_id"],
        "quote_id": quote["id"], "appointment_id": appt["id"],
        "customer_id": quote["customer_id"],
        "customer_name": quote["customer_name"],
        "customer_email": quote.get("customer_email"),
        "customer_phone": quote.get("customer_phone", ""),
        "title": quote["title"],
        "lines": quote["lines"],
        "total_cents": total_cents,
        "deposit_cents": deposit,
        "amount_paid_cents": 0,
        "status": "sent",  # draft/sent/paid/overdue/void
        "public_token": _uuid(),
        "due_at": (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(),
        "paid_at": None,
        "reminders_sent": 0,
        "reminders_paused_at": None,
        "created_at": now, "updated_at": now,
    }
    await db.invoices.insert_one(invoice)
    await db.estimates.update_one(
        {"id": quote["id"]},
        {"$set": {"status": "approved", "accepted_at": now, "updated_at": now}},
    )

    invoice.pop("_id", None)
    return {"ok": True, "invoice": invoice, "appointment_id": appt["id"]}


# ================== INVOICES & DIRECT-CHARGE PAYMENTS ==================
@router.get("/invoices")
async def list_invoices(user: dict = Depends(require_tenant_user), status: Optional[str] = None):
    db = get_db()
    q: dict = {"tenant_id": user["tenant_id"]}
    if status:
        q["status"] = status
    rows = await db.invoices.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
    return rows


class JobCompleteIn(BaseModel):
    appointment_id: str


@router.post("/jobs/complete")
async def mark_job_complete(data: JobCompleteIn, user: dict = Depends(require_tenant_user)):
    """Mark an appointment completed. Flips the related invoice from any deposit-
    only state into 'full balance due' (status stays 'sent' until paid)."""
    db = get_db()
    appt = await db.appointments.find_one({"id": data.appointment_id, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not appt:
        raise HTTPException(404, "Appointment not found")
    now = _now_iso()
    await db.appointments.update_one(
        {"id": appt["id"], "tenant_id": user["tenant_id"]},
        {"$set": {"status": "completed", "completed_at": now}},
    )
    inv = await db.invoices.find_one({"appointment_id": appt["id"], "tenant_id": user["tenant_id"]}, {"_id": 0})
    return {"ok": True, "invoice_id": inv["id"] if inv else None}


@public_router.get("/invoices/{token}")
async def public_invoice(token: str):
    db = get_db()
    inv = await db.invoices.find_one({"public_token": token}, {"_id": 0})
    if not inv:
        raise HTTPException(404, "Not found")
    tenant = await db.tenants.find_one({"id": inv["tenant_id"]}, {"_id": 0, "name": 1, "stripe_connect_id": 1}) or {}
    inv["business"] = {"name": tenant.get("name"),
                       "accepts_online_pay": bool(tenant.get("stripe_connect_id"))}
    inv["amount_due_cents"] = max(0, int(inv["total_cents"]) - int(inv.get("amount_paid_cents") or 0))
    return inv


class PayIntentIn(BaseModel):
    amount_cents: Optional[int] = None  # None → pay the full remaining balance; else partial


@public_router.post("/invoices/{token}/pay")
async def create_invoice_payment(token: str, data: PayIntentIn):
    """Create a Stripe PaymentIntent on the tenant's CONNECTED account (direct charge).
    Returns `client_secret` for Stripe.js on the frontend. Supports partial payments."""
    db = get_db()
    inv = await db.invoices.find_one({"public_token": token}, {"_id": 0})
    if not inv:
        raise HTTPException(404, "Not found")
    if inv["status"] == "paid":
        raise HTTPException(400, "Already paid")
    tenant = await db.tenants.find_one({"id": inv["tenant_id"]}, {"_id": 0}) or {}
    acct_id = tenant.get("stripe_connect_id")
    if not acct_id or not stripe.api_key:
        raise HTTPException(503, "Business has not finished Stripe onboarding")
    remaining = max(0, int(inv["total_cents"]) - int(inv.get("amount_paid_cents") or 0))
    amount = int(data.amount_cents) if data.amount_cents else remaining
    if amount <= 0 or amount > remaining:
        raise HTTPException(400, f"Amount must be between 1 and {remaining} cents")

    try:
        intent = stripe.PaymentIntent.create(
            amount=amount,
            currency="usd",
            description=f"Invoice {inv['id'][:8]} — {inv['title']}",
            metadata={
                "tenant_id": inv["tenant_id"],
                "invoice_id": inv["id"],
                "invoice_token": token,
            },
            # DIRECT CHARGE — settles on the connected account.
            stripe_account=acct_id,
        )
    except stripe.error.StripeError as e:
        log.exception("stripe intent failed: %s", e)
        raise HTTPException(502, f"Stripe error: {str(e)[:200]}")

    # Record payment attempt (idempotent on payment_intent id).
    await db.invoice_payments.update_one(
        {"stripe_payment_intent_id": intent["id"]},
        {"$set": {
            "id": _uuid(),
            "tenant_id": inv["tenant_id"],
            "invoice_id": inv["id"],
            "stripe_payment_intent_id": intent["id"],
            "connected_account": acct_id,
            "amount_cents": amount,
            "status": "requires_payment_method",
            "created_at": _now_iso(),
        }},
        upsert=True,
    )
    return {
        "client_secret": intent["client_secret"],
        "amount_cents": amount,
        "payment_intent_id": intent["id"],
        # Needed by Stripe.js on the public /i/:token page for a direct charge.
        # Connected account ids and publishable keys are not secrets.
        "stripe_account": acct_id,
        "publishable_key": os.environ.get("STRIPE_PUBLISHABLE_KEY") or None,
    }


# ================== CONNECTED-ACCOUNT WEBHOOK ==================
@public_router.post("/stripe/connect-webhook", include_in_schema=False)
async def connect_webhook(request: Request):
    """Receives events from Stripe for connected accounts.
    Idempotent: duplicate `event.id` deliveries are detected and short-circuited.
    """
    if not stripe.api_key:
        raise HTTPException(503, "Stripe not configured")
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    secret = os.environ.get("STRIPE_CONNECT_WEBHOOK_SECRET") or os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, secret) if secret else None
        if event is None:  # dev / local — accept but still require shape
            import json as _json
            event = _json.loads(payload)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(400, "Invalid signature")
    except Exception:
        raise HTTPException(400, "Bad payload")

    db = get_db()
    ev_id = event.get("id") or f"ev_{_uuid()}"
    ev_type = event.get("type") or ""
    # Idempotency gate: insert raises on duplicate (we create a unique index on startup).
    try:
        await db.webhook_events.insert_one({
            "_id": ev_id, "type": ev_type, "received_at": _now_iso(),
            "connected_account": event.get("account"),
        })
    except Exception:
        return {"ok": True, "duplicate": True}

    obj = (event.get("data") or {}).get("object") or {}
    try:
        if ev_type == "payment_intent.succeeded":
            await _apply_payment_success(db, obj)
        elif ev_type == "payment_intent.payment_failed":
            await _apply_payment_failure(db, obj)
        elif ev_type == "charge.refunded":
            await _apply_refund(db, obj)
        elif ev_type == "charge.dispute.created":
            await _apply_dispute(db, obj)
        elif ev_type == "account.updated":
            await _apply_account_update(db, obj)
        else:
            log.info("c2p webhook ignored type=%s", ev_type)
    except Exception as e:
        log.exception("c2p webhook handling failed: %s", e)
        # Mark the event so it can be retried manually; still ack 200 to avoid loops.
        await db.webhook_events.update_one({"_id": ev_id}, {"$set": {"error": str(e)[:400]}})
    return {"ok": True}


async def _apply_payment_success(db, pi: dict):
    inv_id = (pi.get("metadata") or {}).get("invoice_id")
    if not inv_id:
        return
    inv = await db.invoices.find_one({"id": inv_id}, {"_id": 0})
    if not inv:
        return
    amount = int(pi.get("amount_received") or pi.get("amount") or 0)
    new_paid = int(inv.get("amount_paid_cents") or 0) + amount
    total = int(inv["total_cents"])
    status = "paid" if new_paid >= total else "sent"
    patch = {
        "amount_paid_cents": new_paid,
        "status": status,
        "updated_at": _now_iso(),
        "last_payment_intent_id": pi.get("id"),
    }
    if status == "paid":
        patch["paid_at"] = _now_iso()
        patch["reminders_paused_at"] = _now_iso()  # stop reminders
    await db.invoices.update_one({"id": inv_id}, {"$set": patch})
    await db.invoice_payments.update_one(
        {"stripe_payment_intent_id": pi.get("id")},
        {"$set": {"status": "succeeded", "succeeded_at": _now_iso(), "amount_received_cents": amount},
         "$setOnInsert": {"id": _uuid(), "tenant_id": inv["tenant_id"], "invoice_id": inv_id,
                          "stripe_payment_intent_id": pi.get("id"),
                          "amount_cents": amount, "created_at": _now_iso()}},
        upsert=True,
    )
    # Receipt email (best-effort)
    if status == "paid" and inv.get("customer_email"):
        try:
            tenant = await db.tenants.find_one({"id": inv["tenant_id"]}, {"_id": 0, "name": 1}) or {}
            total_dollars = f"${total/100:.2f}"
            html = followup_html(
                business=tenant.get("name", "us"),
                message=(f"Hi {inv['customer_name']}, thank you! We received your payment of "
                         f"<b>{total_dollars}</b> for <b>{inv['title']}</b>. "
                         "This email is your receipt."),
            )
            await send_email(to=inv["customer_email"], subject="Payment received — receipt",
                             html=html, from_name=tenant.get("name", "AI Office"))
        except Exception as e:
            log.warning("receipt email failed: %s", e)


async def _apply_payment_failure(db, pi: dict):
    inv_id = (pi.get("metadata") or {}).get("invoice_id")
    tenant_id = (pi.get("metadata") or {}).get("tenant_id")
    if not inv_id:
        return
    err = (pi.get("last_payment_error") or {}).get("message") or "declined"
    await db.invoice_payments.update_one(
        {"stripe_payment_intent_id": pi.get("id")},
        {"$set": {"status": "failed", "failed_at": _now_iso(), "failure_reason": err},
         "$setOnInsert": {"id": _uuid(), "tenant_id": tenant_id, "invoice_id": inv_id,
                          "stripe_payment_intent_id": pi.get("id"),
                          "amount_cents": int(pi.get("amount") or 0),
                          "created_at": _now_iso()}},
        upsert=True,
    )


async def _apply_refund(db, charge: dict):
    pi_id = charge.get("payment_intent")
    if not pi_id:
        return
    inv = await db.invoices.find_one({"last_payment_intent_id": pi_id}, {"_id": 0})
    if not inv:
        return
    refunded = int(charge.get("amount_refunded") or 0)
    new_paid = max(0, int(inv.get("amount_paid_cents") or 0) - refunded)
    status = "paid" if new_paid >= int(inv["total_cents"]) else ("sent" if new_paid > 0 else "sent")
    patch = {"amount_paid_cents": new_paid, "status": status, "updated_at": _now_iso(),
             "last_refund_amount": refunded}
    if status != "paid":
        patch["paid_at"] = None
        patch["reminders_paused_at"] = None  # resume reminders if balance owed again
    await db.invoices.update_one({"id": inv["id"]}, {"$set": patch})


async def _apply_dispute(db, dispute: dict):
    pi_id = dispute.get("payment_intent")
    inv = await db.invoices.find_one({"last_payment_intent_id": pi_id}, {"_id": 0}) if pi_id else None
    if not inv:
        return
    await db.invoices.update_one(
        {"id": inv["id"]},
        {"$set": {"dispute_id": dispute.get("id"), "disputed_at": _now_iso(), "updated_at": _now_iso()}},
    )


async def _apply_account_update(db, acct: dict):
    tenant_id = (acct.get("metadata") or {}).get("tenant_id")
    if not tenant_id:
        return
    status = "active" if (acct.get("charges_enabled") and acct.get("payouts_enabled")) else "pending"
    await db.tenants.update_one(
        {"id": tenant_id},
        {"$set": {"stripe_connect_status": status, "updated_at": _now_iso()}},
    )


# ================== OVERDUE REMINDERS ==================
class ReminderSettingsIn(BaseModel):
    paused: bool = False
    max_reminders: int = 3


@router.put("/invoices/{iid}/reminders")
async def set_reminder_settings(iid: str, data: ReminderSettingsIn, user: dict = Depends(require_tenant_user)):
    db = get_db()
    patch = {
        "reminders_paused_at": _now_iso() if data.paused else None,
        "max_reminders": max(0, min(10, int(data.max_reminders))),
        "updated_at": _now_iso(),
    }
    res = await db.invoices.update_one({"id": iid, "tenant_id": user["tenant_id"]}, {"$set": patch})
    if res.matched_count == 0:
        raise HTTPException(404, "Not found")
    return {"ok": True, **patch}


async def run_overdue_reminders() -> dict:
    """Daily cron. Scans invoices past due_at that aren't paid or paused."""
    db = get_db()
    now = datetime.now(timezone.utc)
    sent = 0
    invs = await db.invoices.find({
        "status": {"$in": ["sent", "overdue"]},
        "reminders_paused_at": None,
    }, {"_id": 0}).to_list(5000)
    for inv in invs:
        try:
            due = datetime.fromisoformat((inv.get("due_at") or now.isoformat()).replace("Z", "+00:00"))
            if now < due:
                continue
            if int(inv.get("reminders_sent") or 0) >= int(inv.get("max_reminders") or 3):
                continue
            # Build & send
            if inv.get("customer_email"):
                tenant = await db.tenants.find_one({"id": inv["tenant_id"]}, {"_id": 0, "name": 1, "lang": 1}) or {}
                lang = (tenant.get("lang") or "en").lower()
                from public_links import invoice_link
                link = invoice_link(inv["public_token"])
                remaining = (int(inv["total_cents"]) - int(inv.get("amount_paid_cents") or 0)) / 100
                if lang.startswith("es"):
                    subject = "Recordatorio: factura vencida"
                    msg = (f"Hola {inv['customer_name']}, su factura por <b>{inv['title']}</b> está "
                           f"vencida. Saldo pendiente: <b>${remaining:.2f}</b>. "
                           f'<a href="{link}">Pagar en línea</a>.')
                else:
                    subject = "Reminder: invoice past due"
                    msg = (f"Hi {inv['customer_name']}, your invoice for <b>{inv['title']}</b> is "
                           f"past due. Remaining balance: <b>${remaining:.2f}</b>. "
                           f'<a href="{link}">Pay online</a>.')
                html = followup_html(business=tenant.get("name", "us"), message=msg)
                await send_email(to=inv["customer_email"], subject=subject, html=html,
                                 from_name=tenant.get("name", "AI Office"))
            await db.invoices.update_one(
                {"id": inv["id"]},
                {"$set": {"status": "overdue", "last_reminder_at": _now_iso(),
                          "updated_at": _now_iso()}, "$inc": {"reminders_sent": 1}},
            )
            sent += 1
        except Exception as e:
            log.warning("overdue reminder failed id=%s: %s", inv.get("id"), e)
    return {"sent": sent, "scanned": len(invs)}


# ================== NEEDS-ATTENTION AGGREGATOR ==================
@router.get("/needs-attention")
async def needs_attention(user: dict = Depends(require_tenant_user)):
    db = get_db()
    tid = user["tenant_id"]
    pending_quotes = await db.estimates.count_documents({"tenant_id": tid, "status": "draft"})
    sent_quotes = await db.estimates.count_documents({"tenant_id": tid, "status": "sent"})
    overdue = await db.invoices.count_documents({"tenant_id": tid, "status": "overdue"})
    unpaid = await db.invoices.count_documents({"tenant_id": tid, "status": "sent"})
    failed = await db.invoice_payments.count_documents({"tenant_id": tid, "status": "failed"})
    unassigned_leads = await db.leads.count_documents({"tenant_id": tid, "status": "new"})
    return {
        "pending_quotes": pending_quotes,
        "awaiting_customer_quotes": sent_quotes,
        "overdue_invoices": overdue,
        "unpaid_invoices": unpaid,
        "failed_payments": failed,
        "unassigned_leads": unassigned_leads,
    }
