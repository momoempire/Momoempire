"""Subcontractor management: profiles, insurance compliance, sub dispatch,
payments, and 1099-ready earnings summaries.

All data is tenant-isolated (every query filters on ``tenant_id``). Pay writes
are owner/admin-only; profile CRUD and dispatch are available to any tenant
user. Zero live network calls.

Collections:
- ``subcontractors`` — profile docs (id, tenant_id, name, trade, phone,
  rate_type hourly/flat, rate, insurance_expiry YYYY-MM-DD, license,
  w9_on_file, active, created_at/updated_at)
- ``sub_payments`` — payment docs (id, tenant_id, sub_id, job_id, amount,
  date, method, note, recorded_by, created_at)
- ``appointments`` — job assignment: dispatch writes ``sub_id``/``sub_name``
  on the job, parallel to dispatch.py's ``staff_id``/``staff`` so the
  dispatch board keeps working unchanged.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from db import get_db
from security import require_tenant_owner_or_admin, require_tenant_user

router = APIRouter(prefix="/subcontractors", tags=["subcontractors"])

# 1099-NEC filing threshold (IRS): businesses must file for nonemployees paid
# >= $600 in a calendar year.
FILING_THRESHOLD = 600.0
INSURANCE_ALERT_DAYS = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- models ----------

class SubCreate(BaseModel):
    name: str = Field(..., min_length=1)
    trade: str = Field(..., min_length=1)
    phone: str = ""
    rate_type: Literal["hourly", "flat"] = "hourly"
    rate: float = Field(0.0, ge=0)
    insurance_expiry: Optional[str] = None  # YYYY-MM-DD
    license: str = ""
    w9_on_file: bool = False
    active: bool = True


class SubUpdate(BaseModel):
    name: Optional[str] = None
    trade: Optional[str] = None
    phone: Optional[str] = None
    rate_type: Optional[Literal["hourly", "flat"]] = None
    rate: Optional[float] = Field(None, ge=0)
    insurance_expiry: Optional[str] = None
    license: Optional[str] = None
    w9_on_file: Optional[bool] = None
    active: Optional[bool] = None


class DispatchIn(BaseModel):
    job_id: str = Field(..., min_length=1)


class PaymentIn(BaseModel):
    job_id: str = Field(..., min_length=1)
    amount: float = Field(..., gt=0)
    date: str = Field(..., min_length=1)  # YYYY-MM-DD
    method: str = "check"
    note: str = ""


# ---------- helpers ----------

def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise HTTPException(400, f"{field} must be a valid YYYY-MM-DD date")


async def _get_sub(db, tenant_id: str, sub_id: str) -> dict:
    sub = await db.subcontractors.find_one(
        {"id": sub_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if not sub:
        raise HTTPException(404, "Subcontractor not found")
    return sub


def _insurance_status(expiry: Optional[str]) -> dict:
    """Compliance status for an insurance expiry date.

    Returns {"status": expired|expiring_soon|valid|missing,
             "days_until_expiry": int|None}.
    "expiring_soon" covers <= 30 days out (inclusive); past dates are
    "expired". An unset date is "missing".
    """
    if not expiry:
        return {"status": "missing", "days_until_expiry": None}
    try:
        exp = date.fromisoformat(expiry)
    except (ValueError, TypeError):
        return {"status": "missing", "days_until_expiry": None}
    days = (exp - date.today()).days
    if days < 0:
        return {"status": "expired", "days_until_expiry": days}
    if days <= INSURANCE_ALERT_DAYS:
        return {"status": "expiring_soon", "days_until_expiry": days}
    return {"status": "valid", "days_until_expiry": days}


def _sub_out(sub: dict) -> dict:
    sub = dict(sub)
    sub["insurance"] = _insurance_status(sub.get("insurance_expiry"))
    return sub


# ---------- CRUD ----------

@router.post("/subs")
async def create_sub(data: SubCreate, user: dict = Depends(require_tenant_user)):
    """Create a subcontractor profile."""
    db = get_db()
    tenant_id = user["tenant_id"]
    if data.insurance_expiry:
        _parse_date(data.insurance_expiry, "insurance_expiry")
    doc = {
        "id": uuid4().hex,
        "tenant_id": tenant_id,
        "name": data.name.strip(),
        "trade": data.trade.strip(),
        "phone": data.phone.strip(),
        "rate_type": data.rate_type,
        "rate": float(data.rate),
        "insurance_expiry": data.insurance_expiry,
        "license": data.license.strip(),
        "w9_on_file": bool(data.w9_on_file),
        "active": bool(data.active),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    await db.subcontractors.insert_one(doc)
    return _sub_out(doc)


@router.get("/subs")
async def list_subs(
    active_only: bool = Query(False),
    user: dict = Depends(require_tenant_user),
):
    """List this tenant's subcontractors, with compliance badges computed."""
    db = get_db()
    query: dict = {"tenant_id": user["tenant_id"]}
    if active_only:
        query["active"] = True
    subs = await db.subcontractors.find(query, {"_id": 0}).to_list(1000)
    subs = [_sub_out(s) for s in subs]
    subs.sort(key=lambda s: s.get("name", "").lower())
    return {"subcontractors": subs, "count": len(subs)}


@router.get("/alerts")
async def insurance_alerts(user: dict = Depends(require_tenant_user)):
    """Insurance-expiry alerts: expired subs plus subs expiring in <= 30 days.

    Purely derived from this tenant's own subcontractor profiles.
    """
    db = get_db()
    subs = await db.subcontractors.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}
    ).to_list(1000)
    expired: list[dict] = []
    expiring_soon: list[dict] = []
    for s in subs:
        info = _insurance_status(s.get("insurance_expiry"))
        if info["status"] == "expired":
            expired.append(_alert_row(s, info))
        elif info["status"] == "expiring_soon":
            expiring_soon.append(_alert_row(s, info))
    expiring_soon.sort(key=lambda r: r["days_until_expiry"])
    expired.sort(key=lambda r: r["days_until_expiry"])
    return {
        "expired": expired,
        "expiring_soon": expiring_soon,
        "alert_days": INSURANCE_ALERT_DAYS,
        "count": len(expired) + len(expiring_soon),
    }


def _alert_row(sub: dict, info: dict) -> dict:
    return {
        "sub_id": sub["id"],
        "name": sub.get("name", ""),
        "trade": sub.get("trade", ""),
        "phone": sub.get("phone", ""),
        "insurance_expiry": sub.get("insurance_expiry"),
        "status": info["status"],
        "days_until_expiry": info["days_until_expiry"],
        "active": bool(sub.get("active", True)),
    }


@router.get("/subs/{sub_id}")
async def get_sub(sub_id: str, user: dict = Depends(require_tenant_user)):
    """One subcontractor profile with its compliance status."""
    db = get_db()
    return _sub_out(await _get_sub(db, user["tenant_id"], sub_id))


@router.patch("/subs/{sub_id}")
async def update_sub(
    sub_id: str, data: SubUpdate, user: dict = Depends(require_tenant_user)
):
    """Update a subcontractor profile (partial)."""
    db = get_db()
    await _get_sub(db, user["tenant_id"], sub_id)
    update: dict = {}
    for field in ("name", "trade", "phone", "rate_type", "rate",
                  "insurance_expiry", "license", "w9_on_file", "active"):
        value = getattr(data, field)
        if value is None:
            continue
        if field == "insurance_expiry":
            _parse_date(value, "insurance_expiry")
        elif field in ("name", "trade", "phone", "license"):
            value = value.strip()
        elif field == "rate":
            value = float(value)
        elif field in ("w9_on_file", "active"):
            value = bool(value)
        update[field] = value
    if not update:
        raise HTTPException(400, "No fields to update")
    update["updated_at"] = _now_iso()
    await db.subcontractors.update_one(
        {"id": sub_id, "tenant_id": user["tenant_id"]}, {"$set": update}
    )
    return _sub_out(await _get_sub(db, user["tenant_id"], sub_id))


@router.delete("/subs/{sub_id}")
async def delete_sub(sub_id: str, user: dict = Depends(require_tenant_user)):
    """Remove a subcontractor profile. Payment history is kept for 1099s."""
    db = get_db()
    await _get_sub(db, user["tenant_id"], sub_id)
    await db.subcontractors.delete_one({"id": sub_id, "tenant_id": user["tenant_id"]})
    return {"ok": True, "deleted": sub_id}


# ---------- dispatch ----------

async def _get_job(db, tenant_id: str, job_id: str) -> dict:
    job = await db.appointments.find_one(
        {"id": job_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.post("/subs/{sub_id}/dispatch")
async def dispatch_to_sub(
    sub_id: str, data: DispatchIn, user: dict = Depends(require_tenant_user)
):
    """Assign a job to a subcontractor: records sub_id/sub_name on the
    appointment (parallel to dispatch.py's staff_id/staff) so the dispatch
    board keeps working unchanged."""
    db = get_db()
    tenant_id = user["tenant_id"]
    sub = await _get_sub(db, tenant_id, sub_id)
    if not sub.get("active", True):
        raise HTTPException(400, "Cannot dispatch to an inactive subcontractor")
    job = await _get_job(db, tenant_id, data.job_id)
    if job.get("status") in {"completed", "canceled"}:
        raise HTTPException(400, f"Cannot dispatch a {job['status']} job")
    await db.appointments.update_one(
        {"id": job["id"], "tenant_id": tenant_id},
        {"$set": {
            "sub_id": sub["id"],
            "sub_name": sub.get("name", ""),
            "updated_at": _now_iso(),
        }},
    )
    return {
        "ok": True,
        "job_id": job["id"],
        "sub_id": sub["id"],
        "sub_name": sub.get("name", ""),
    }


@router.get("/subs/{sub_id}/jobs")
async def sub_jobs(sub_id: str, user: dict = Depends(require_tenant_user)):
    """Jobs dispatched to this subcontractor (most recent first)."""
    db = get_db()
    sub = await _get_sub(db, user["tenant_id"], sub_id)
    jobs = await db.appointments.find(
        {"tenant_id": user["tenant_id"], "sub_id": sub_id}, {"_id": 0}
    ).to_list(1000)
    cards = [
        {
            "id": j.get("id"),
            "start_at": j.get("start_at"),
            "status": j.get("status", ""),
            "customer_name": (j.get("customer") or {}).get("name", "")
            if isinstance(j.get("customer"), dict) else j.get("customer_name", ""),
            "service": j.get("service") or j.get("title") or "",
        }
        for j in jobs
    ]
    cards.sort(key=lambda c: c.get("start_at") or "", reverse=True)
    return {"sub_id": sub["id"], "sub_name": sub.get("name", ""),
            "jobs": cards, "count": len(cards)}


# ---------- payments ----------

@router.post("/subs/{sub_id}/payments")
async def record_payment(
    sub_id: str,
    data: PaymentIn,
    user: dict = Depends(require_tenant_owner_or_admin),
):
    """Record a payment to a subcontractor. Owner/admin only."""
    db = get_db()
    tenant_id = user["tenant_id"]
    sub = await _get_sub(db, tenant_id, sub_id)
    job = await _get_job(db, tenant_id, data.job_id)
    pay_date = _parse_date(data.date, "date")
    doc = {
        "id": uuid4().hex,
        "tenant_id": tenant_id,
        "sub_id": sub["id"],
        "sub_name": sub.get("name", ""),
        "job_id": job["id"],
        "amount": float(data.amount),
        "date": pay_date.isoformat(),
        "method": data.method.strip() or "check",
        "note": data.note.strip(),
        "recorded_by": user.get("id", ""),
        "created_at": _now_iso(),
    }
    await db.sub_payments.insert_one(doc)
    return {"ok": True, "payment": {k: v for k, v in doc.items() if k != "_id"}}


@router.get("/subs/{sub_id}/payments")
async def list_payments(
    sub_id: str,
    year: Optional[int] = Query(None),
    user: dict = Depends(require_tenant_user),
):
    """Payment history for a sub, optionally filtered to a calendar year."""
    db = get_db()
    sub = await _get_sub(db, user["tenant_id"], sub_id)
    payments = await db.sub_payments.find(
        {"tenant_id": user["tenant_id"], "sub_id": sub_id}, {"_id": 0}
    ).to_list(5000)
    if year is not None:
        payments = [p for p in payments if (p.get("date") or "")[:4] == str(year)]
    payments.sort(key=lambda p: p.get("date", ""), reverse=True)
    return {"sub_id": sub["id"], "sub_name": sub.get("name", ""),
            "year": year, "payments": payments, "count": len(payments)}


@router.get("/subs/{sub_id}/pay-summary")
async def pay_summary(
    sub_id: str,
    year: Optional[int] = Query(None),
    user: dict = Depends(require_tenant_user),
):
    """1099-ready earnings summary for a sub: total paid per year, per-job
    breakdown, W-9 status, and a ready-to-file indicator (W-9 on file and
    total >= the $600 1099-NEC filing threshold)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    sub = await _get_sub(db, tenant_id, sub_id)
    year = year if year is not None else date.today().year

    payments = await db.sub_payments.find(
        {"tenant_id": tenant_id, "sub_id": sub_id}, {"_id": 0}
    ).to_list(5000)
    year_payments = [p for p in payments if (p.get("date") or "")[:4] == str(year)]
    year_payments.sort(key=lambda p: p.get("date", ""))

    total = round(sum(float(p.get("amount") or 0) for p in year_payments), 2)

    by_job: dict[str, dict] = {}
    for p in year_payments:
        jid = p.get("job_id") or "unassigned"
        entry = by_job.setdefault(jid, {"job_id": jid, "job_label": "",
                                        "total": 0.0, "payments": 0})
        entry["total"] = round(entry["total"] + float(p.get("amount") or 0), 2)
        entry["payments"] += 1
    per_job = sorted(by_job.values(), key=lambda e: e["total"], reverse=True)
    for entry in per_job:
        if entry["job_id"] != "unassigned":
            job = await db.appointments.find_one(
                {"id": entry["job_id"], "tenant_id": tenant_id}, {"_id": 0}
            )
            if job:
                cust = job.get("customer")
                cust_name = cust.get("name", "") if isinstance(cust, dict) else ""
                label = (job.get("service") or job.get("title") or "").strip()
                entry["job_label"] = f"{label} — {cust_name}".strip(" —") or entry["job_id"]

    w9 = bool(sub.get("w9_on_file"))
    ready = w9 and total >= FILING_THRESHOLD
    return {
        "sub_id": sub["id"],
        "sub_name": sub.get("name", ""),
        "trade": sub.get("trade", ""),
        "year": year,
        "total_paid": total,
        "payment_count": len(year_payments),
        "w9_on_file": w9,
        "filing_threshold": FILING_THRESHOLD,
        "ready_to_file": ready,
        "per_job": per_job,
        "payments": year_payments,
    }
