"""Predictive cash flow: when money actually lands, not just what's billed.

Scheduled appointments -> expected completion (stage-based probability) ->
invoice -> historical per-customer days-to-pay -> projected cash-in by week,
minus recurring outflows (estimated payroll from settings + `sent` purchase
orders from parts ordering). Fully deterministic — no LLM.

Collections (tenant-scoped, read defensively; see services/cash_flow.py):
- `invoices` (`unpaid`/`overdue`/`partial`/`sent` = open; `paid` = history)
- `appointments` (`scheduled`/`confirmed`/`en_route`/`on_site` = active)
- `estimates` (job value via appointment `quote_id`)
- `purchase_orders` (`sent` = committed outflow)
- `tenant_settings`.`cash_flow` {starting_balance, safety_threshold,
  monthly_payroll, default_days_to_pay, supplier_payment_days,
  invoice_terms_days}

With zero invoices AND zero appointments the endpoints return clearly-labeled
synthetic demo data (`demo: true`) so the page is useful out of the box.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin
from services.cash_flow import (
    WEEKS,
    build_forecast,
    build_levers,
    load_inputs,
    make_demo_inputs,
)
from services.cash_flow import _cash_settings  # internal: normalize settings

router = APIRouter(prefix="/tenants/cash-flow", tags=["cash-flow"])


class StartingBalanceIn(BaseModel):
    balance: float = Field(ge=0, le=1_000_000_000)
    safety_threshold: float = Field(default=None, ge=0, le=1_000_000_000)
    monthly_payroll: float = Field(default=None, ge=0, le=100_000_000)


async def _forecast_for_tenant(tenant_id: str, now: datetime | None = None) -> dict:
    db = get_db()
    invoices, appointments, estimates_by_id, pos, settings_doc = await load_inputs(db, tenant_id)
    demo = not invoices and not appointments
    if demo:
        invoices, appointments, estimates_by_id, pos, settings = make_demo_inputs(now or datetime.now(timezone.utc))
        # Respect settings the shop explicitly saved (starting balance, safety
        # line, payroll) even while the forecast data itself is synthetic.
        saved = _cash_settings(settings_doc)
        for key in ("starting_balance", "safety_threshold", "monthly_payroll"):
            if saved.get(key):
                settings[key] = saved[key]
    else:
        settings = _cash_settings(settings_doc)
    return build_forecast(invoices, appointments, estimates_by_id, pos, settings, now=now, demo=demo)


@router.get("/forecast")
async def get_forecast(user: dict = Depends(require_tenant_user)):
    """12-week cash projection: per-week expected_in, expected_out, net,
    running_balance from the starting balance. `demo: true` when the shop has
    no invoices or appointments yet (synthetic data)."""
    return await _forecast_for_tenant(user["tenant_id"])


@router.post("/starting-balance")
async def set_starting_balance(data: StartingBalanceIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Set the current cash balance (and optionally the safety threshold /
    monthly payroll estimate) that the forecast runs from."""
    db = get_db()
    tenant_id = user["tenant_id"]
    patch = {"starting_balance": round(data.balance, 2)}
    if data.safety_threshold is not None:
        patch["safety_threshold"] = round(data.safety_threshold, 2)
    if data.monthly_payroll is not None:
        patch["monthly_payroll"] = round(data.monthly_payroll, 2)
    existing = await db.tenant_settings.find_one({"tenant_id": tenant_id}, {"_id": 0})
    merged = dict((existing or {}).get("cash_flow") or {})
    merged.update(patch)
    if existing:
        await db.tenant_settings.update_one(
            {"tenant_id": tenant_id},
            {"$set": {"cash_flow": merged, "updated_at": _now_iso()}},
        )
    else:
        await db.tenant_settings.insert_one({
            "id": _uuid(), "tenant_id": tenant_id, "cash_flow": merged,
            "created_at": _now_iso(), "updated_at": _now_iso(),
        })
    return {"ok": True, "cash_flow": merged}


@router.get("/warnings")
async def get_warnings(user: dict = Depends(require_tenant_user)):
    """Low-balance weeks: projected balance below the safety threshold, with
    the shortfall amount per week."""
    fc = await _forecast_for_tenant(user["tenant_id"])
    return {
        "warnings": fc["warnings"],
        "count": len(fc["warnings"]),
        "safety_threshold": fc["safety_threshold"],
        "demo": fc["demo"],
        "generated_at": fc["generated_at"],
    }


@router.get("/levers")
async def get_levers(user: dict = Depends(require_tenant_user)):
    """What-if levers ranked by projected cash impact: accelerate the top-5
    open invoices, defer sent purchase orders, require deposits on upcoming
    scheduled jobs."""
    db = get_db()
    tenant_id = user["tenant_id"]
    invoices, appointments, estimates_by_id, pos, settings_doc = await load_inputs(db, tenant_id)
    demo = not invoices and not appointments
    if demo:
        invoices, appointments, estimates_by_id, pos, settings = make_demo_inputs(datetime.now(timezone.utc))
        saved = _cash_settings(settings_doc)
        for key in ("starting_balance", "safety_threshold", "monthly_payroll"):
            if saved.get(key):
                settings[key] = saved[key]
    else:
        settings = _cash_settings(settings_doc)
    fc = build_forecast(invoices, appointments, estimates_by_id, pos, settings, demo=demo)
    levers = build_levers(fc, invoices, pos)
    return {"levers": levers, "count": len(levers), "demo": demo}


@router.get("/assumptions")
async def get_assumptions(user: dict = Depends(require_tenant_user)):
    """Show the model assumptions (avg days-to-pay + per-customer breakdown,
    job completion probabilities, terms) so the shop can sanity-check them."""
    fc = await _forecast_for_tenant(user["tenant_id"])
    assumptions = dict(fc["assumptions"])
    assumptions["forecast_weeks"] = WEEKS
    return {"assumptions": assumptions, "demo": fc["demo"], "generated_at": fc["generated_at"]}
