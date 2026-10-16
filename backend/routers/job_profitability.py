"""Real-time per-job profitability (#11): live job margin for techs in the
driveway and owner dashboards that move from backward-looking to live.

Jobs ARE appointments (see routers/dispatch.py). Revenue comes from the
invoice linked to the appointment, falling back to the approved quote
(``estimates``) when no invoice exists yet. Labor = tech hourly rate x hours
(logged, else the scheduled window); parts = pricebook ``cost`` fields on
the invoice lines; overhead = per-tenant setting (per-hour or pct-of-revenue).
All money math is Decimal in services/job_profitability.py; this router only
serializes (Decimals become strings so no precision is lost in JSON).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from db import get_db
from security import require_tenant_user, require_tenant_owner_or_admin
from services.job_profitability import (
    aggregate_breakdowns,
    default_settings,
    job_breakdown,
    merge_settings,
    normalize_settings,
)

router = APIRouter(prefix="/tenants/profitability", tags=["profitability"])


# --------------------------------------------------------------- schemas ---

class ProfitabilitySettingsIn(BaseModel):
    default_hourly_rate: Optional[float] = Field(default=None, ge=0)
    hourly_rates: Optional[Dict[str, float]] = None  # tech_id -> rate, or "role:<role>" -> rate
    overhead_rate: Optional[float] = Field(default=None, ge=0)
    overhead_method: Optional[str] = None  # "per-hour" | "pct-of-revenue"


# -------------------------------------------------------------- helpers ---

def _jsonable(value: Any) -> Any:
    """Recursively convert Decimals to strings for JSON output."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


async def _load_settings(db, tenant_id: str) -> dict:
    stored = await db.profitability_settings.find_one({"id": "profitability", "tenant_id": tenant_id}, {"_id": 0})
    return {**default_settings(), **(stored or {})}


async def _revenue_doc_for(db, job: dict, tenant_id: str) -> Optional[dict]:
    """Invoice linked to the job first, else the quote the job was booked from."""
    inv = await db.invoices.find_one(
        {"appointment_id": job["id"], "tenant_id": tenant_id}, {"_id": 0}
    )
    if inv:
        return inv
    quote_id = job.get("quote_id")
    if quote_id:
        return await db.estimates.find_one({"id": quote_id, "tenant_id": tenant_id}, {"_id": 0})
    return None


async def _pricebook_map(db, lines: List[dict], tenant_id: str) -> dict:
    ids = {str(l.get("pricebook_item_id") or l.get("service_id")) for l in lines or []}
    ids.discard("None")
    ids.discard("")
    if not ids:
        return {}
    rows = await db.pricebook_items.find(
        {"tenant_id": tenant_id, "id": {"$in": sorted(ids)}}, {"_id": 0}
    ).to_list(500)
    return {r["id"]: r for r in rows}


async def _breakdown_for(db, job: dict, tenant_id: str, settings: dict) -> dict:
    revenue_doc = await _revenue_doc_for(db, job, tenant_id)
    pricebook = await _pricebook_map(db, (revenue_doc or {}).get("lines") or [], tenant_id)
    bd = job_breakdown(job, revenue_doc, pricebook, settings)
    bd["job_type"] = bd["service_name"] or "other"
    return bd


def _in_range(value: Any, start, end) -> bool:
    """Match by completed_at when present, else start_at; unparseable -> False."""
    for key in ("completed_at", "start_at"):
        raw = value.get(key)
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return start <= dt.date() <= end
    return False


# -------------------------------------------------------------- endpoints ---

@router.get("/jobs/{job_id}")
async def job_margin(job_id: str, user: dict = Depends(require_tenant_user)):
    """Live margin breakdown for one job: revenue, labor, parts, overhead,
    gross profit and margin %. Missing data degrades gracefully."""
    db = get_db()
    tenant_id = user["tenant_id"]
    job = await db.appointments.find_one({"id": job_id, "tenant_id": tenant_id}, {"_id": 0})
    if not job:
        raise HTTPException(404, "Job not found")
    settings = await _load_settings(db, tenant_id)
    bd = await _breakdown_for(db, job, tenant_id, settings)
    return _jsonable(bd)


@router.get("/dashboard")
async def period_dashboard(
    user: dict = Depends(require_tenant_user),
    from_: str = Query(..., alias="from", description="Start date YYYY-MM-DD"),
    to: str = Query(..., alias="to", description="End date YYYY-MM-DD"),
):
    """Period summary: revenue, cost, profit and margin overall, per tech and
    per job type. Jobs are dated by completed_at (fallback: start_at)."""
    try:
        start = datetime.strptime(from_, "%Y-%m-%d").date()
        end = datetime.strptime(to, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise HTTPException(400, "from/to must be YYYY-MM-DD")
    if end < start:
        raise HTTPException(400, "to must be on or after from")

    db = get_db()
    tenant_id = user["tenant_id"]
    settings = await _load_settings(db, tenant_id)

    jobs = await db.appointments.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(2000)
    in_period = [j for j in jobs if _in_range(j, start, end)]

    breakdowns = [await _breakdown_for(db, j, tenant_id, settings) for j in in_period]
    summary = aggregate_breakdowns(breakdowns)
    return {"from": from_, "to": to, **_jsonable(summary)}


@router.get("/settings")
async def get_settings(user: dict = Depends(require_tenant_user)):
    """Current per-tenant profitability settings (owner-configured rates)."""
    db = get_db()
    return _jsonable(await _load_settings(db, user["tenant_id"]))


@router.put("/settings")
async def put_settings(data: ProfitabilitySettingsIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Owner/admin only: set default + per-tech/per-role hourly rates, the
    overhead rate, and the overhead method (per-hour | pct-of-revenue)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    payload = data.model_dump(exclude_none=True)
    try:
        patch = normalize_settings(payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    stored = await db.profitability_settings.find_one({"id": "profitability", "tenant_id": tenant_id}, {"_id": 0})
    merged = merge_settings(stored, patch)
    await db.profitability_settings.update_one(
        {"id": "profitability", "tenant_id": tenant_id},
        {"$set": {**merged, "id": "profitability", "tenant_id": tenant_id,
                  "updated_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    return _jsonable(merged)
