"""Tech time tracking / timesheets (#32): clock-in/out feeding payroll.

Zero live network calls. Everything is tenant-isolated.

Data:
- Entries live in the ``time_entries`` collection; each doc carries
  ``tenant_id`` and the tech snapshot (id + name from ``schedule_staff``).
- The weekly overtime threshold is per-tenant config, stored on the tenant
  doc as ``ot_weekly_hours`` (default 40.0); managers can change it via
  ``PUT /time-tracking/settings``.

Contract:
- One open entry per tech: a second ``POST /clock-in`` while an entry is
  open is rejected with 409.
- ``POST /clock-out`` closes the open entry and computes hours from the
  timestamps, so overnight shifts (22:00 -> 02:00) just work.
- Approved entries are immutable: ``PUT /entries/{id}`` on an approved entry
  returns 409. Corrections carry a mandatory audit note and append to
  ``correction_history``.
- ``GET /timesheet`` splits weekly hours into regular vs overtime at the
  tenant's ``ot_weekly_hours`` threshold.
- ``GET /payroll-export`` emits per-tech regular/OT totals in a
  payroll-ready shape (pairs with the bookkeeping app's payroll-lite).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from db import get_db
from security import require_tenant_owner_or_admin, require_tenant_user

router = APIRouter(prefix="/time-tracking", tags=["time-tracking"])

COLLECTION = "time_entries"
DEFAULT_OT_WEEKLY_HOURS = 40.0


# ---------- pydantic models ----------

class ClockInBody(BaseModel):
    tech_id: str = Field(..., min_length=1)
    job_id: Optional[str] = None
    at: Optional[datetime] = None  # defaults to now; allows backdated entries


class ClockOutBody(BaseModel):
    tech_id: str = Field(..., min_length=1)
    at: Optional[datetime] = None  # defaults to now


class CorrectionBody(BaseModel):
    clock_in: Optional[datetime] = None
    clock_out: Optional[datetime] = None
    note: Optional[str] = None
    correction_note: str = Field(..., min_length=1,
                                 description="Manager audit note (required)")


class ApproveBody(BaseModel):
    entry_ids: list[str] = Field(..., min_length=1)


class SettingsBody(BaseModel):
    ot_weekly_hours: float = Field(..., gt=0, le=168)

    @field_validator("ot_weekly_hours")
    @classmethod
    def _round(cls, v):
        return round(float(v), 2)


# ---------- helpers ----------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _parse_dt(value) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hours(clock_in: datetime, clock_out: datetime) -> float:
    secs = (clock_out - clock_in).total_seconds()
    if secs < 0:
        raise HTTPException(400, "clock_out is before clock_in")
    return round(secs / 3600.0, 2)


def _serialize(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "tech_id": doc.get("tech_id"),
        "tech_name": doc.get("tech_name", ""),
        "job_id": doc.get("job_id"),
        "clock_in": doc.get("clock_in"),
        "clock_out": doc.get("clock_out"),
        "hours": doc.get("hours"),
        "note": doc.get("note", ""),
        "approved": bool(doc.get("approved", False)),
        "approved_by": doc.get("approved_by"),
        "approved_at": doc.get("approved_at"),
        "correction_note": doc.get("correction_note"),
        "correction_history": doc.get("correction_history", []),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


def _week_bounds(week: Optional[str]) -> tuple[date, date]:
    """Accept 'YYYY-Www' (ISO week) or any ISO date; return (monday, sunday)."""
    if not week:
        today = _utcnow().date()
        monday = today - timedelta(days=today.weekday())
        return monday, monday + timedelta(days=6)
    w = week.strip()
    try:
        if "-W" in w.upper():
            iso_year, iso_week = w.upper().split("-W")
            monday = date.fromisocalendar(int(iso_year), int(iso_week), 1)
        else:
            d = date.fromisoformat(w)
            monday = d - timedelta(days=d.weekday())
        return monday, monday + timedelta(days=6)
    except (ValueError, TypeError):
        raise HTTPException(400, "week must be ISO 'YYYY-Www' or an ISO date")


def _month_bounds(period: Optional[str]) -> tuple[date, date]:
    """Accept 'YYYY-MM' (default: current month); return (first, last)."""
    if not period:
        today = _utcnow().date()
        return date(today.year, today.month, 1), today
    try:
        year, month = period.strip().split("-")
        year, month = int(year), int(month)
        first = date(year, month, 1)
        last = (date(year + (month == 12), (month % 12) + 1, 1)
                - timedelta(days=1))
        return first, last
    except (ValueError, TypeError):
        raise HTTPException(400, "period must be 'YYYY-MM'")


async def _get_tech(db, tech_id: str, tenant_id: str) -> dict:
    tech = await db.schedule_staff.find_one(
        {"id": tech_id, "tenant_id": tenant_id}, {"_id": 0})
    if not tech:
        raise HTTPException(404, "Tech not found")
    return tech


async def _ot_threshold(db, tenant_id: str) -> float:
    tenant = await db.tenants.find_one({"id": tenant_id}, {"_id": 0})
    try:
        return float((tenant or {}).get("ot_weekly_hours",
                                       DEFAULT_OT_WEEKLY_HOURS))
    except (TypeError, ValueError):
        return DEFAULT_OT_WEEKLY_HOURS


def _split_ot(total_hours: float, threshold: float) -> tuple[float, float]:
    overtime = round(max(0.0, total_hours - threshold), 2)
    regular = round(max(0.0, total_hours - overtime), 2)
    return regular, overtime


# ---------- clock in / out ----------

@router.post("/clock-in")
async def clock_in(body: ClockInBody, user: dict = Depends(require_tenant_user)):
    """Open a time entry for a tech. One open entry per tech is enforced."""
    db = get_db()
    tenant_id = user["tenant_id"]
    tech = await _get_tech(db, body.tech_id, tenant_id)

    open_entry = await db[COLLECTION].find_one(
        {"tenant_id": tenant_id, "tech_id": tech["id"], "clock_out": None},
        {"_id": 0})
    if open_entry:
        raise HTTPException(
            409, "Tech already has an open time entry; clock out first")

    at = _parse_dt(body.at) if body.at is not None else _utcnow()
    now = _utcnow()
    if at > now + timedelta(minutes=5):
        raise HTTPException(400, "clock-in time cannot be in the future")

    entry = {
        "id": uuid.uuid4().hex,
        "tenant_id": tenant_id,
        "tech_id": tech["id"],
        "tech_name": tech.get("name", ""),
        "job_id": body.job_id or None,
        "clock_in": _iso(at),
        "clock_out": None,
        "hours": None,
        "note": "",
        "approved": False,
        "approved_by": None,
        "approved_at": None,
        "correction_note": None,
        "correction_history": [],
        "created_at": _iso(now),
        "updated_at": _iso(now),
    }
    await db[COLLECTION].insert_one(entry)
    return {"ok": True, "entry": _serialize(entry)}


@router.post("/clock-out")
async def clock_out(body: ClockOutBody, user: dict = Depends(require_tenant_user)):
    """Close the tech's open entry and compute hours."""
    db = get_db()
    tenant_id = user["tenant_id"]
    tech = await _get_tech(db, body.tech_id, tenant_id)

    open_entry = await db[COLLECTION].find_one(
        {"tenant_id": tenant_id, "tech_id": tech["id"], "clock_out": None},
        {"_id": 0})
    if not open_entry:
        raise HTTPException(409, "No open time entry for this tech")

    at = _parse_dt(body.at) if body.at is not None else _utcnow()
    clock_in = _parse_dt(open_entry["clock_in"])
    hours = _hours(clock_in, at)

    now = _utcnow()
    await db[COLLECTION].update_one(
        {"id": open_entry["id"], "tenant_id": tenant_id},
        {"$set": {"clock_out": _iso(at), "hours": hours,
                  "updated_at": _iso(now)}})
    updated = await db[COLLECTION].find_one({"id": open_entry["id"]}, {"_id": 0})
    return {"ok": True, "entry": _serialize(updated)}


# ---------- entries ----------

@router.get("/entries")
async def list_entries(
    tech_id: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None, alias="from"),
    date_to: Optional[str] = Query(default=None, alias="to"),
    open_only: bool = Query(default=False),
    user: dict = Depends(require_tenant_user),
):
    """List entries for this tenant; filter by tech and ISO date range."""
    db = get_db()
    tenant_id = user["tenant_id"]
    docs = await db[COLLECTION].find({"tenant_id": tenant_id}, {"_id": 0}).to_list(10000)

    def in_range(doc) -> bool:
        try:
            d = _parse_dt(doc.get("clock_in")).date()
        except (TypeError, ValueError):
            return False
        if date_from:
            try:
                if d < date.fromisoformat(date_from):
                    return False
            except ValueError:
                raise HTTPException(400, "from must be an ISO date")
        if date_to:
            try:
                if d > date.fromisoformat(date_to):
                    return False
            except ValueError:
                raise HTTPException(400, "to must be an ISO date")
        return True

    docs = [d for d in docs if in_range(d)]
    if tech_id:
        docs = [d for d in docs if d.get("tech_id") == tech_id]
    if open_only:
        docs = [d for d in docs if d.get("clock_out") is None]
    docs.sort(key=lambda d: str(d.get("clock_in") or ""), reverse=True)
    return {"entries": [_serialize(d) for d in docs], "count": len(docs)}


@router.put("/entries/{entry_id}")
async def correct_entry(entry_id: str, body: CorrectionBody,
                        user: dict = Depends(require_tenant_owner_or_admin)):
    """Manager correction of an entry. Requires an audit note; approved
    entries are locked (immutable)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    doc = await db[COLLECTION].find_one(
        {"id": entry_id, "tenant_id": tenant_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Entry not found")
    if doc.get("approved"):
        raise HTTPException(409, "Approved entries are locked; cannot edit")

    new_in = _parse_dt(body.clock_in) if body.clock_in is not None else _parse_dt(doc["clock_in"])
    if body.clock_out is not None:
        new_out = _parse_dt(body.clock_out)
    elif doc.get("clock_out"):
        new_out = _parse_dt(doc["clock_out"])
    else:
        new_out = None
    hours = _hours(new_in, new_out) if new_out else None

    now = _utcnow()
    history = list(doc.get("correction_history") or [])
    history.append({
        "at": _iso(now),
        "by": user.get("id"),
        "note": body.correction_note,
    })
    update = {
        "clock_in": _iso(new_in),
        "clock_out": _iso(new_out) if new_out else None,
        "hours": hours,
        "correction_note": body.correction_note,
        "correction_history": history,
        "updated_at": _iso(now),
    }
    if body.note is not None:
        update["note"] = body.note
    await db[COLLECTION].update_one(
        {"id": entry_id, "tenant_id": tenant_id}, {"$set": update})
    updated = await db[COLLECTION].find_one({"id": entry_id}, {"_id": 0})
    return {"ok": True, "entry": _serialize(updated)}


# ---------- timesheets ----------

@router.get("/timesheet")
async def get_timesheet(
    tech_id: str = Query(..., min_length=1),
    week: Optional[str] = Query(default=None),
    user: dict = Depends(require_tenant_user),
):
    """Weekly timesheet for one tech: daily totals plus regular vs overtime
    split at the tenant's ``ot_weekly_hours`` threshold."""
    db = get_db()
    tenant_id = user["tenant_id"]
    tech = await _get_tech(db, tech_id, tenant_id)
    monday, sunday = _week_bounds(week)
    threshold = await _ot_threshold(db, tenant_id)

    docs = await db[COLLECTION].find(
        {"tenant_id": tenant_id, "tech_id": tech["id"]}, {"_id": 0}).to_list(10000)
    days = [{"date": (monday + timedelta(days=i)).isoformat(),
             "weekday": (monday + timedelta(days=i)).strftime("%a"),
             "hours": 0.0, "entry_count": 0} for i in range(7)]
    by_date = {d["date"]: d for d in days}

    entries = []
    for doc in docs:
        d = _parse_dt(doc.get("clock_in")).date()
        if monday <= d <= sunday and doc.get("hours") is not None:
            day = by_date[d.isoformat()]
            hrs = float(doc.get("hours") or 0)
            day["hours"] = round(day["hours"] + hrs, 2)
            day["entry_count"] += 1
        entries.append(_serialize(doc))
    entries = [e for e in entries
               if monday <= _parse_dt(e["clock_in"]).date() <= sunday]
    entries.sort(key=lambda e: str(e.get("clock_in") or ""))

    total = round(sum(d["hours"] for d in days), 2)
    regular, overtime = _split_ot(total, threshold)
    return {
        "tech_id": tech["id"],
        "tech_name": tech.get("name", ""),
        "week_start": monday.isoformat(),
        "week_end": sunday.isoformat(),
        "ot_weekly_hours": threshold,
        "days": days,
        "regular_hours": regular,
        "overtime_hours": overtime,
        "total_hours": total,
        "entries": entries,
        "approved": bool(entries) and all(e["approved"] for e in entries),
    }


@router.post("/timesheet/approve")
async def approve_timesheet(body: ApproveBody,
                            user: dict = Depends(require_tenant_owner_or_admin)):
    """Manager approval: locks the listed entries (they become immutable)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    now = _utcnow()
    approved = []
    for entry_id in body.entry_ids:
        doc = await db[COLLECTION].find_one(
            {"id": entry_id, "tenant_id": tenant_id}, {"_id": 0})
        if not doc or doc.get("approved"):
            continue
        if doc.get("clock_out") is None:
            raise HTTPException(
                409, "Open entries must be clocked out before approval")
        await db[COLLECTION].update_one(
            {"id": entry_id, "tenant_id": tenant_id},
            {"$set": {"approved": True, "approved_by": user.get("id"),
                      "approved_at": _iso(now), "updated_at": _iso(now)}})
        approved.append(entry_id)
    return {"ok": True, "approved": approved, "count": len(approved)}


# ---------- payroll export ----------

@router.get("/payroll-export")
async def payroll_export(
    period: Optional[str] = Query(default=None),
    user: dict = Depends(require_tenant_owner_or_admin),
):
    """Payroll-ready totals for a month ('YYYY-MM', default current month):
    per-tech regular hours, OT hours, and approval coverage."""
    db = get_db()
    tenant_id = user["tenant_id"]
    first, last = _month_bounds(period)
    threshold = await _ot_threshold(db, tenant_id)

    docs = await db[COLLECTION].find({"tenant_id": tenant_id}, {"_id": 0}).to_list(10000)
    per_tech: dict[str, dict] = {}
    for doc in docs:
        try:
            d = _parse_dt(doc.get("clock_in")).date()
        except (TypeError, ValueError):
            continue
        if not (first <= d <= last) or doc.get("hours") is None:
            continue
        t = per_tech.setdefault(doc.get("tech_id"), {
            "tech_id": doc.get("tech_id"),
            "tech_name": doc.get("tech_name", ""),
            "regular_hours": 0.0,
            "overtime_hours": 0.0,  # filled below (weekly threshold)
            "total_hours": 0.0,
            "entry_count": 0,
            "approved_count": 0,
        })
        t["total_hours"] = round(t["total_hours"] + float(doc.get("hours") or 0), 2)
        t["entry_count"] += 1
        if doc.get("approved"):
            t["approved_count"] += 1

    # OT is weekly, so re-split each tech's hours week by week.
    for t in per_tech.values():
        tech_docs = [d for d in docs
                     if d.get("tech_id") == t["tech_id"] and d.get("hours") is not None
                     and first <= _parse_dt(d.get("clock_in")).date() <= last]
        weeks: dict[date, float] = {}
        for d in tech_docs:
            dd = _parse_dt(d.get("clock_in")).date()
            mon = dd - timedelta(days=dd.weekday())
            weeks[mon] = round(weeks.get(mon, 0.0) + float(d.get("hours") or 0), 2)
        reg = ot = 0.0
        for wtotal in weeks.values():
            r, o = _split_ot(wtotal, threshold)
            reg, ot = round(reg + r, 2), round(ot + o, 2)
        t["regular_hours"] = reg
        t["overtime_hours"] = ot

    rows = sorted(per_tech.values(), key=lambda r: r["tech_name"])
    return {
        "period": period or f"{first.year:04d}-{first.month:02d}",
        "period_start": first.isoformat(),
        "period_end": last.isoformat(),
        "ot_weekly_hours": threshold,
        "techs": rows,
        "totals": {
            "regular_hours": round(sum(r["regular_hours"] for r in rows), 2),
            "overtime_hours": round(sum(r["overtime_hours"] for r in rows), 2),
            "total_hours": round(sum(r["total_hours"] for r in rows), 2),
        },
    }


# ---------- settings ----------

@router.get("/settings")
async def get_settings(user: dict = Depends(require_tenant_user)):
    """Tenant time-tracking config (currently the weekly OT threshold)."""
    db = get_db()
    return {"ot_weekly_hours": await _ot_threshold(db, user["tenant_id"])}


@router.put("/settings")
async def put_settings(body: SettingsBody,
                       user: dict = Depends(require_tenant_owner_or_admin)):
    """Manager-only: change the tenant's weekly overtime threshold."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await db.tenants.update_one(
        {"id": tenant_id}, {"$set": {"ot_weekly_hours": body.ot_weekly_hours}})
    return {"ok": True, "ot_weekly_hours": body.ot_weekly_hours}
