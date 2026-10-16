"""Maintenance Agreement Autopilot — agreement templates, enrollment,
auto-scheduling, smart renewals, and recurring-revenue tracking.

Recurring maintenance agreements (tune-ups, filter changes, seasonal
checkups) are a shop's most reliable revenue. This module automates the
lifecycle:

- Plan templates (`maintenance_plans`): name, visits_per_year, price,
  billing interval, trade.
- Agreements (`maintenance_agreements`): a customer enrolled on a plan,
  with a one-year term, covered equipment, and computed status
  (active / expiring / expired).
- Visits (`maintenance_visits`): scheduled service-visit stubs derived
  from `visits_per_year`. Scheduling is idempotent — scheduling the same
  period twice returns the existing visit instead of double-booking.
- Renewals: renewing extends the term another year and records the
  renewal so the renewal pipeline can be reported.

Collections (all tenant-scoped):
- `maintenance_plans`
- `maintenance_agreements`
- `maintenance_visits`
- `maintenance_renewals` (one record per renewal event)

Visit stubs are shaped to be importable into the appointments
collection (`customer_name`, `service_type`, `due_date`, `status`) so a
future sync step can promote them without remapping.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin

router = APIRouter(prefix="/tenants/maintenance", tags=["maintenance"])

BILLING_INTERVALS = ("monthly", "quarterly", "annual")
# Days before term end at which an agreement counts as "expiring".
EXPIRING_WITHIN_DAYS = 30


# ---------- date helpers ----------

def _parse_date(value: str) -> datetime:
    """Parse an ISO date or datetime string into an aware UTC datetime."""
    text = str(value).strip()
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _add_years(dt: datetime, years: int) -> datetime:
    """Add whole years, clamping Feb 29 -> Feb 28 on non-leap years."""
    target_year = dt.year + years
    day = min(dt.day, calendar.monthrange(target_year, dt.month)[1])
    return dt.replace(year=target_year, day=day)


def _today_utc() -> datetime:
    return datetime.now(timezone.utc)


def _term_end_iso(start_date: str, years: int = 1) -> str:
    """End of a term: one year after start, minus one day (inclusive end)."""
    return (_add_years(_parse_date(start_date), years) - timedelta(days=1)).isoformat()


def _status_for(agreement: dict) -> str:
    """Compute agreement status from its end_date."""
    try:
        end = _parse_date(agreement.get("end_date") or "")
    except (ValueError, TypeError, AttributeError):
        return "active"
    now = _today_utc()
    if end < now:
        return "expired"
    if end <= now + timedelta(days=EXPIRING_WITHIN_DAYS):
        return "expiring"
    return "active"


def _months_per_interval(interval: str) -> int:
    return {"monthly": 1, "quarterly": 3, "annual": 12}.get(interval, 12)


def _monthly_value(price: float, interval: str) -> float:
    """Normalize a plan price to a monthly recurring value."""
    return round(float(price) / _months_per_interval(interval), 2)


def _visit_period_days(visits_per_year: int) -> float:
    return 365.0 / max(1, visits_per_year)


# ---------- pydantic models ----------

class CustomerRef(BaseModel):
    id: Optional[str] = None
    name: str
    phone: Optional[str] = ""
    email: Optional[str] = ""
    address: Optional[str] = ""


class EquipmentRef(BaseModel):
    equipment_type: str = ""
    make: str = ""
    model: str = ""
    serial_number: str = ""
    location_label: str = ""


class PlanIn(BaseModel):
    name: str
    visits_per_year: int = Field(..., ge=1, le=12)
    price: float = Field(..., ge=0)
    billing_interval: str = "annual"
    trade: str = "general"
    active: bool = True


class AgreementIn(BaseModel):
    plan_id: str
    customer: CustomerRef
    start_date: str  # ISO date (YYYY-MM-DD) or datetime
    equipment: List[EquipmentRef] = Field(default_factory=list)


class AgreementOut(BaseModel):
    id: str
    plan_id: str
    plan_name: str = ""
    customer: dict = Field(default_factory=dict)
    equipment: List[dict] = Field(default_factory=list)
    start_date: str
    end_date: str
    visits_per_year: int
    price: float
    billing_interval: str
    trade: str = ""
    status: str
    renewal_count: int = 0
    monthly_value: float = 0.0
    created_at: str = ""


def _agreement_out(doc: dict) -> dict:
    doc = dict(doc)
    doc["status"] = _status_for(doc)
    doc["monthly_value"] = _monthly_value(doc.get("price", 0.0), doc.get("billing_interval", "annual"))
    return doc


# ---------- plan templates ----------

@router.post("/plans")
async def create_plan(payload: PlanIn, user: dict = Depends(require_tenant_owner_or_admin)):
    if payload.billing_interval not in BILLING_INTERVALS:
        raise HTTPException(400, f"billing_interval must be one of {BILLING_INTERVALS}")
    db = get_db()
    now = _now_iso()
    doc = {
        "id": _uuid(),
        "tenant_id": user["tenant_id"],
        "name": payload.name,
        "visits_per_year": payload.visits_per_year,
        "price": round(payload.price, 2),
        "billing_interval": payload.billing_interval,
        "trade": payload.trade,
        "active": payload.active,
        "created_at": now,
        "updated_at": now,
    }
    await db.maintenance_plans.insert_one(doc)
    return doc


@router.get("/plans")
async def list_plans(active_only: bool = True, user: dict = Depends(require_tenant_user)):
    db = get_db()
    query = {"tenant_id": user["tenant_id"]}
    if active_only:
        query["active"] = True
    cursor = db.maintenance_plans.find(query).sort("name", 1)
    plans = await cursor.to_list(500)
    return {"plans": plans, "count": len(plans)}


# ---------- agreements ----------

@router.post("/agreements", response_model=AgreementOut)
async def enroll_agreement(payload: AgreementIn, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    plan = await db.maintenance_plans.find_one(
        {"id": payload.plan_id, "tenant_id": user["tenant_id"]}
    )
    if not plan:
        raise HTTPException(404, "Plan not found")
    if not plan.get("active", True):
        raise HTTPException(400, "Plan is not active")
    try:
        _parse_date(payload.start_date)
    except (ValueError, TypeError):
        raise HTTPException(400, "start_date must be an ISO date")
    now = _now_iso()
    doc = {
        "id": _uuid(),
        "tenant_id": user["tenant_id"],
        "plan_id": plan["id"],
        "plan_name": plan["name"],
        "customer": payload.customer.model_dump(),
        "equipment": [e.model_dump() for e in payload.equipment],
        "start_date": payload.start_date,
        "end_date": _term_end_iso(payload.start_date),
        "visits_per_year": plan["visits_per_year"],
        "price": plan["price"],
        "billing_interval": plan["billing_interval"],
        "trade": plan.get("trade", ""),
        "renewal_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    await db.maintenance_agreements.insert_one(doc)
    return _agreement_out(doc)


@router.get("/agreements")
async def list_agreements(
    expiring_soon: bool = False,
    status: Optional[str] = None,
    user: dict = Depends(require_tenant_user),
):
    """List agreements with computed status. Filter with ?status=active|expiring|expired
    or the shortcut ?expiring_soon=true (agreements ending within 30 days)."""
    db = get_db()
    cursor = db.maintenance_agreements.find({"tenant_id": user["tenant_id"]}).sort("end_date", 1)
    agreements = [_agreement_out(d) for d in await cursor.to_list(2000)]
    if expiring_soon:
        agreements = [a for a in agreements if a["status"] == "expiring"]
    if status:
        if status not in ("active", "expiring", "expired"):
            raise HTTPException(400, "status must be active, expiring, or expired")
        agreements = [a for a in agreements if a["status"] == status]
    return {"agreements": agreements, "count": len(agreements)}


@router.get("/agreements/{agreement_id}")
async def get_agreement(agreement_id: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    doc = await db.maintenance_agreements.find_one(
        {"id": agreement_id, "tenant_id": user["tenant_id"]}
    )
    if not doc:
        raise HTTPException(404, "Agreement not found")
    return _agreement_out(doc)


@router.post("/agreements/{agreement_id}/renew")
async def renew_agreement(agreement_id: str, user: dict = Depends(require_tenant_owner_or_admin)):
    """Renew for another full term. The new term starts at the old end date
    (or today if already expired), runs one year, and a renewal record is
    stored for the revenue pipeline."""
    db = get_db()
    agreement = await db.maintenance_agreements.find_one(
        {"id": agreement_id, "tenant_id": user["tenant_id"]}
    )
    if not agreement:
        raise HTTPException(404, "Agreement not found")
    now = _today_utc()
    try:
        old_end = _parse_date(agreement["end_date"])
    except (ValueError, TypeError, AttributeError):
        old_end = _parse_date(agreement["start_date"])
        old_end = _add_years(old_end, agreement.get("renewal_count", 0) + 1) - timedelta(days=1)
    # Continuous terms: renew from the old end date, or today when lapsed.
    new_start = max(old_end + timedelta(days=1), now).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    new_end = _add_years(new_start, 1) - timedelta(days=1)
    new_renewal_count = agreement.get("renewal_count", 0) + 1

    renewal = {
        "id": _uuid(),
        "tenant_id": user["tenant_id"],
        "agreement_id": agreement["id"],
        "customer_name": agreement.get("customer", {}).get("name", ""),
        "renewed_at": _now_iso(),
        "previous_end_date": old_end.isoformat(),
        "new_start_date": new_start.isoformat(),
        "new_end_date": new_end.isoformat(),
        "price": agreement.get("price", 0.0),
        "billing_interval": agreement.get("billing_interval", "annual"),
        "renewal_number": new_renewal_count,
    }
    await db.maintenance_renewals.insert_one(renewal)

    await db.maintenance_agreements.update_one(
        {"id": agreement["id"], "tenant_id": user["tenant_id"]},
        {"$set": {
            "start_date": new_start.isoformat(),
            "end_date": new_end.isoformat(),
            "renewal_count": new_renewal_count,
            "updated_at": _now_iso(),
        }},
    )
    updated = await db.maintenance_agreements.find_one(
        {"id": agreement["id"], "tenant_id": user["tenant_id"]}
    )
    return {"agreement": _agreement_out(updated), "renewal": renewal}


@router.post("/agreements/{agreement_id}/schedule-visit")
async def schedule_visit(agreement_id: str, user: dict = Depends(require_tenant_user)):
    """Create the next scheduled service visit for an agreement.

    Visit N of `visits_per_year` is due `start_date + (N-1) * (365/visits_per_year)`.
    Idempotent: if the next visit already exists as `scheduled`, it is
    returned instead of creating a duplicate.
    """
    db = get_db()
    agreement = await db.maintenance_agreements.find_one(
        {"id": agreement_id, "tenant_id": user["tenant_id"]}
    )
    if not agreement:
        raise HTTPException(404, "Agreement not found")
    if _status_for(agreement) == "expired":
        raise HTTPException(400, "Cannot schedule visits for an expired agreement")

    vpy = max(1, int(agreement.get("visits_per_year", 1) or 1))
    period_days = _visit_period_days(vpy)
    start = _parse_date(agreement["start_date"])

    existing = await db.maintenance_visits.find(
        {"tenant_id": user["tenant_id"], "agreement_id": agreement["id"]}
    ).sort("visit_number", 1).to_list(vpy * 2)
    done_numbers = {int(v.get("visit_number", 0)) for v in existing}

    next_number = None
    for n in range(1, vpy + 1):
        if n not in done_numbers:
            next_number = n
            break
    if next_number is None:
        # All visits for this term already exist; return the latest scheduled one.
        latest = existing[-1] if existing else None
        if latest and latest.get("status") == "scheduled":
            return {"visit": latest, "created": False, "note": "all visits already scheduled"}
        raise HTTPException(400, "All visits for this term are already scheduled")

    # Idempotency check: same period already scheduled (e.g. duplicate request
    # that landed between the read above and this write path).
    for v in existing:
        if int(v.get("visit_number", 0)) == next_number and v.get("status") == "scheduled":
            return {"visit": v, "created": False}

    due = start + timedelta(days=(next_number - 1) * period_days)
    customer = agreement.get("customer", {}) or {}
    visit = {
        "id": _uuid(),
        "tenant_id": user["tenant_id"],
        "agreement_id": agreement["id"],
        "customer_id": customer.get("id"),
        "customer_name": customer.get("name", ""),
        "visit_number": next_number,
        "visits_in_term": vpy,
        # appointments-compatible shape: a visit is a scheduled job stub.
        "service_type": "Maintenance visit",
        "title": f"{agreement.get('plan_name', 'Maintenance')} — visit {next_number} of {vpy}",
        "due_date": due.date().isoformat(),
        "scheduled_date": due.date().isoformat(),
        "status": "scheduled",
        "source": "maintenance-autopilot",
        "equipment": agreement.get("equipment", []),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    await db.maintenance_visits.insert_one(visit)
    return {"visit": visit, "created": True}


@router.get("/agreements/{agreement_id}/visits")
async def list_visits(agreement_id: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    agreement = await db.maintenance_agreements.find_one(
        {"id": agreement_id, "tenant_id": user["tenant_id"]}
    )
    if not agreement:
        raise HTTPException(404, "Agreement not found")
    cursor = db.maintenance_visits.find(
        {"tenant_id": user["tenant_id"], "agreement_id": agreement_id}
    ).sort("visit_number", 1)
    visits = await cursor.to_list(200)
    return {"visits": visits, "count": len(visits)}


# ---------- revenue ----------

@router.get("/revenue")
async def revenue_summary(user: dict = Depends(require_tenant_user)):
    """MRR-style summary: active agreements, monthly recurring value, and
    the renewal pipeline (expiring agreements ranked by value)."""
    db = get_db()
    cursor = db.maintenance_agreements.find({"tenant_id": user["tenant_id"]})
    agreements = [_agreement_out(d) for d in await cursor.to_list(5000)]

    active = [a for a in agreements if a["status"] == "active"]
    expiring = [a for a in agreements if a["status"] == "expiring"]
    expired = [a for a in agreements if a["status"] == "expired"]

    mrr = round(sum(a["monthly_value"] for a in active + expiring), 2)
    pipeline = sorted(
        ({"agreement_id": a["id"], "customer_name": a["customer"].get("name", ""),
          "end_date": a["end_date"], "price": a["price"],
          "billing_interval": a["billing_interval"],
          "monthly_value": a["monthly_value"]} for a in expiring),
        key=lambda r: r["monthly_value"],
        reverse=True,
    )
    pipeline_value = round(sum(r["monthly_value"] for r in pipeline), 2)

    by_trade: dict = {}
    for a in active + expiring:
        trade = a.get("trade") or "general"
        bucket = by_trade.setdefault(trade, {"agreements": 0, "monthly_value": 0.0})
        bucket["agreements"] += 1
        bucket["monthly_value"] = round(bucket["monthly_value"] + a["monthly_value"], 2)

    return {
        "total_agreements": len(agreements),
        "active_agreements": len(active),
        "expiring_agreements": len(expiring),
        "expired_agreements": len(expired),
        "monthly_recurring_value": mrr,
        "annual_recurring_value": round(mrr * 12, 2),
        "renewal_pipeline": pipeline,
        "renewal_pipeline_monthly_value": pipeline_value,
        "by_trade": by_trade,
    }
