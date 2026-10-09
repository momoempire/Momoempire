"""Stripe-powered subscription checkout. Uses admin-configured Stripe price IDs when set."""
import os
import stripe
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel, Field
from db import get_db
from security import require_tenant_user

router = APIRouter(prefix="/payments", tags=["payments"])

from services.stripe_config import configure_stripe_api_key, require_stripe_api_key, construct_stripe_event

configure_stripe_api_key()


class CheckoutRequest(BaseModel):
    plan_id: str                   # Plan.key
    origin_url: str
    quantity: int = Field(1, ge=1, le=5)


async def _get_db_plan(plan_id: str) -> dict:
    db = get_db()
    plan = await db.plans.find_one({"key": plan_id}, {"_id": 0})
    if not plan:
        raise HTTPException(400, "Unknown plan")
    return plan


@router.get("/plans")
async def list_plans():
    """Checkout-ready plans (public → paid, non-enterprise)."""
    db = get_db()
    plans = await db.plans.find(
        {"is_public": True, "price_cents": {"$gt": 0}, "key": {"$ne": "enterprise"}},
        {"_id": 0},
    ).sort("sort_order", 1).to_list(100)
    return plans


@router.post("/checkout")
async def create_checkout(req: CheckoutRequest, user: dict = Depends(require_tenant_user)):
    db = get_db()
    plan = await _get_db_plan(req.plan_id)
    if plan.get("price_cents", 0) <= 0:
        raise HTTPException(400, "Plan not purchasable — contact sales")

    line_item: dict
    if plan.get("stripe_price_id"):
        # Real Stripe product/price configured by admin
        line_item = {"price": plan["stripe_price_id"], "quantity": req.quantity}
    else:
        # Ad-hoc price (test mode / before admin wires Stripe)
        line_item = {
            "price_data": {
                "currency": "usd",
                "product_data": {"name": f"AI Office — {plan['name']}"},
                "unit_amount": plan["price_cents"],
                "recurring": {"interval": "month"},
            },
            "quantity": req.quantity,
        }

    require_stripe_api_key()
    try:
        session = stripe.checkout.Session.create(
            line_items=[line_item],
            mode="subscription",
            success_url=f"{req.origin_url}/payment/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{req.origin_url}/payment/cancel",
            metadata={"tenant_id": user["tenant_id"], "plan_id": req.plan_id, "user_id": user["id"]},
        )
    except Exception as e:
        raise HTTPException(500, f"Stripe error: {e}")

    await db.payment_transactions.insert_one({
        "session_id": session.id,
        "tenant_id": user["tenant_id"],
        "user_id": user["id"],
        "plan_id": req.plan_id,
        "amount": plan["price_cents"] * req.quantity,
        "currency": "usd",
        "status": "initiated",
        "payment_status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"checkout_url": session.url, "session_id": session.id}


# EMP-W-CF-019: Checkout payment_status values that may activate a plan. "unpaid" never does
# (delayed methods such as bank debits finish later via checkout.session.async_payment_succeeded).
# TODO(Brann): "no_payment_required" (e.g. a 100% coupon or a free trial) keeps activating, as
# before this fix; confirm that is wanted.
ACTIVATING_PAYMENT_STATUSES = ("paid", "no_payment_required")


@router.get("/status/{session_id}")
async def payment_status(session_id: str):
    db = get_db()
    record = await db.payment_transactions.find_one({"session_id": session_id}, {"_id": 0})
    if not record:
        raise HTTPException(404, "Transaction not found")
    if record.get("payment_status") != "paid":
        try:
            if not configure_stripe_api_key():
                return {"session_id": record["session_id"], "status": record["status"], "payment_status": record["payment_status"]}
            s = stripe.checkout.Session.retrieve(session_id)
            # EMP-W-CF-019: a "complete" session can still be payment_status "unpaid" (delayed
            # payment methods); never activate a plan for that. See ACTIVATING_PAYMENT_STATUSES.
            if s.payment_status in ACTIVATING_PAYMENT_STATUSES:
                await db.payment_transactions.update_one(
                    {"session_id": session_id, "payment_status": {"$ne": "paid"}},
                    {"$set": {
                        "status": "completed", "payment_status": "paid",
                        "stripe_subscription_id": s.subscription,
                        "stripe_customer_id": s.customer,
                        "stripe_payment_intent_id": s.payment_intent,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }},
                )
                record = await db.payment_transactions.find_one({"session_id": session_id}, {"_id": 0})
                if record and record.get("tenant_id"):
                    await db.tenants.update_one(
                        {"id": record["tenant_id"]},
                        {"$set": {
                            "subscription_status": "active",
                            "plan_id": record.get("plan_id"),
                            "stripe_subscription_id": s.subscription,
                            "stripe_customer_id": s.customer,
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }},
                    )
        except stripe.error.StripeError:
            pass
    return {"session_id": record["session_id"], "status": record["status"], "payment_status": record["payment_status"]}


@router.post("/stripe/webhook", include_in_schema=False)
async def stripe_webhook(request: Request):
    db = get_db()
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    event = construct_stripe_event(payload, sig, "STRIPE_WEBHOOK_SECRET")
    obj, t = event["data"]["object"], event["type"]
    now = datetime.now(timezone.utc).isoformat()
    # EMP-W-CF-019: replay check. Stripe event ids are unique; a re-delivered or replayed event
    # (e.g. an old checkout.session.completed after a cancellation) is acknowledged and ignored.
    from pymongo.errors import DuplicateKeyError
    ev_id = event.get("id")
    if ev_id:
        try:
            await db.platform_webhook_events.insert_one({"_id": ev_id, "type": t, "received_at": now})
        except DuplicateKeyError:
            return {"status": "ok", "duplicate": True}
    try:
        if t in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            await db.payment_transactions.update_one(
                {"session_id": obj["id"], "payment_status": {"$ne": "paid"}},
                {"$set": {
                    "status": "completed",
                    "payment_status": obj.get("payment_status", "paid"),
                    "stripe_subscription_id": obj.get("subscription"),
                    "stripe_customer_id": obj.get("customer"),
                    "stripe_payment_intent_id": obj.get("payment_intent"),
                    "updated_at": now,
                }},
            )
            meta = obj.get("metadata") or {}
            if meta.get("tenant_id") and obj.get("payment_status", "paid") in ACTIVATING_PAYMENT_STATUSES:
                await db.tenants.update_one({"id": meta["tenant_id"]}, {"$set": {
                    "subscription_status": "active",
                    "plan_id": meta.get("plan_id"),
                    "stripe_subscription_id": obj.get("subscription"),
                    "stripe_customer_id": obj.get("customer"),
                    "updated_at": now,
                }})
    except Exception:
        if ev_id:  # let Stripe's retry through instead of treating it as a duplicate
            await db.platform_webhook_events.delete_one({"_id": ev_id})
        raise
    return {"status": "ok"}
