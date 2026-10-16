"""Overage billing.

Design: at the end of a billing period (or on-demand), compare used vs limit for
metered metrics (ai_minutes, calls, sms, ai_interactions). Charge the overage
units × plan.overage[metric] cents.

- Mode C (record + conditionally charge): we always upsert an `overage_items`
  row per (tenant, period, metric). If the tenant has an active Stripe
  subscription, we also create a Stripe invoice item on the next scheduled run
  (idempotency via overage_items.stripe_invoice_item_id).

Called by cron or by admin via POST /api/admin/overage/run.
"""
import os
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from db import get_db
from security import require_platform_admin, require_tenant_user
from routers.usage import _usage_totals, period_key, _get_plan_limits
from models import _uuid, _now_iso

import stripe
from services.stripe_config import configure_stripe_api_key

configure_stripe_api_key()

METERED = ["ai_minutes", "calls", "sms", "ai_interactions"]


async def _compute_tenant_overage(tenant_id: str, period: str | None = None) -> dict:
    """Compute per-metric overage (used - limit) × overage_cents for a tenant."""
    db = get_db()
    period = period or period_key()
    plan_key, limits = await _get_plan_limits(tenant_id)
    plan = await db.plans.find_one({"key": plan_key}, {"_id": 0})
    rates = (plan or {}).get("overage", {}) or {}
    totals = await _usage_totals(tenant_id)
    out = {"tenant_id": tenant_id, "period": period, "plan": plan_key, "items": [], "total_cents": 0}
    for metric in METERED:
        limit = float(limits.get(metric, 0) or 0)
        used = float(totals.get(metric, 0) or 0)
        rate = float(rates.get(metric, 0) or 0)
        over = max(0.0, used - limit)
        cents = round(over * rate, 2)
        if limit <= 0 or over <= 0 or rate <= 0:
            continue
        out["items"].append({
            "metric": metric, "limit": limit, "used": used,
            "overage_units": over, "rate_cents": rate, "amount_cents": cents,
        })
        out["total_cents"] += cents
    out["total_cents"] = round(out["total_cents"], 2)
    return out


async def _upsert_overage_items(summary: dict) -> list:
    """Persist overage summary rows per metric (idempotent per tenant/period/metric)."""
    db = get_db()
    now = _now_iso()
    saved = []
    for it in summary["items"]:
        q = {"tenant_id": summary["tenant_id"], "period": summary["period"], "metric": it["metric"]}
        existing = await db.overage_items.find_one(q)
        doc = {
            **q,
            "plan": summary["plan"],
            "limit": it["limit"], "used": it["used"],
            "overage_units": it["overage_units"], "rate_cents": it["rate_cents"],
            "amount_cents": it["amount_cents"],
            "updated_at": now,
        }
        if existing:
            doc["id"] = existing["id"]
            doc["created_at"] = existing.get("created_at", now)
            doc["stripe_invoice_item_id"] = existing.get("stripe_invoice_item_id")
            doc["charged"] = existing.get("charged", False)
            await db.overage_items.update_one(q, {"$set": doc})
        else:
            doc["id"] = _uuid()
            doc["created_at"] = now
            doc["stripe_invoice_item_id"] = None
            doc["charged"] = False
            await db.overage_items.insert_one(doc)
        saved.append(doc)
    return saved


async def _charge_overage_to_stripe(tenant: dict, items: list) -> list:
    """If tenant has stripe_customer_id, create InvoiceItems for uncharged overage rows."""
    db = get_db()
    cust = tenant.get("stripe_customer_id")
    if not cust:
        return []
    if not configure_stripe_api_key():
        # Never use a placeholder key; production logs an error from configure_stripe_api_key.
        return []
    charged = []
    for row in items:
        if row.get("charged") or row.get("stripe_invoice_item_id") or row["amount_cents"] <= 0:
            continue
        try:
            inv_item = stripe.InvoiceItem.create(
                customer=cust,
                amount=int(round(row["amount_cents"])),
                currency="usd",
                description=f"Overage · {row['metric']} · {row['overage_units']:.2f} units ({row['period']})",
                metadata={"tenant_id": tenant["id"], "metric": row["metric"], "period": row["period"]},
            )
            await db.overage_items.update_one(
                {"id": row["id"]},
                {"$set": {"charged": True, "stripe_invoice_item_id": inv_item.id, "updated_at": _now_iso()}},
            )
            charged.append({"metric": row["metric"], "amount_cents": row["amount_cents"], "stripe_invoice_item_id": inv_item.id})
        except Exception as e:
            await db.overage_items.update_one(
                {"id": row["id"]},
                {"$set": {"last_error": str(e), "updated_at": _now_iso()}},
            )
    return charged


# ---------- Tenant-side view ----------
tenant_router = APIRouter(prefix="/usage", tags=["usage"])


@tenant_router.get("/overage")
async def my_overage(user: dict = Depends(require_tenant_user)):
    """What my tenant currently owes in overage this period."""
    return await _compute_tenant_overage(user["tenant_id"])


# ---------- Admin controls ----------
admin_router = APIRouter(prefix="/admin/overage", tags=["admin-overage"],
                         dependencies=[Depends(require_platform_admin)])


@admin_router.get("/preview")
async def preview_all():
    """Dry-run across all tenants for current period."""
    db = get_db()
    tenants = await db.tenants.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(1000)
    out = []
    for t in tenants:
        s = await _compute_tenant_overage(t["id"])
        if s["total_cents"] > 0:
            s["tenant_name"] = t.get("name")
            out.append(s)
    return out


@admin_router.post("/run")
async def run_overage(dry_run: bool = False, charge: bool = True):
    """Compute, persist (and optionally charge) overages for all tenants, current period."""
    db = get_db()
    tenants = await db.tenants.find({}, {"_id": 0}).to_list(1000)
    results = []
    for t in tenants:
        summary = await _compute_tenant_overage(t["id"])
        if summary["total_cents"] <= 0:
            continue
        saved = [] if dry_run else await _upsert_overage_items(summary)
        charged = []
        if not dry_run and charge:
            charged = await _charge_overage_to_stripe(t, saved)
        results.append({
            "tenant_id": t["id"], "tenant_name": t.get("name"),
            "period": summary["period"], "total_cents": summary["total_cents"],
            "items": summary["items"], "charged_stripe": charged,
        })
    return {"ran_at": datetime.now(timezone.utc).isoformat(), "dry_run": dry_run, "charged": charge, "results": results}


@admin_router.get("/history")
async def history(tenant_id: str | None = None, period: str | None = None):
    db = get_db()
    q: dict = {}
    if tenant_id: q["tenant_id"] = tenant_id
    if period: q["period"] = period
    return await db.overage_items.find(q, {"_id": 0}).sort("created_at", -1).to_list(500)
