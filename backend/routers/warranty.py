"""Warranty & callback tracking (#14): plug the silent profit leak of repeat visits.

Tenants register equipment-unit and per-job labor warranties (coverage type,
start/end, terms). A coverage check answers "is this unit/job covered?" for
dispatchers and techs. Callbacks — repeat visits to the same equipment unit
within 60 days — are auto-flagged from the equipment service history, with an
escalation rule (3rd visit inside a rolling 60-day window -> `escalated`) that
requires manager review. A stats endpoint reports callback rates by tech, by
equipment type, and a monthly trend.

Collections (all tenant-scoped):
- `warranties`: the warranty record — coverage_type, equipment_id / job_id,
  customer link, start/end ISO dates, terms, active flag.
- `warranty_callback_resolutions`: manager resolutions for open/escalated
  callbacks, keyed by deterministic callback id.

Zero live network calls. All endpoints tenant-isolated via
`require_tenant_user`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from security import require_tenant_user

router = APIRouter(prefix="/warranty", tags=["warranty"])

# Repeat visits to the same equipment unit within this window are callbacks.
CALLBACK_WINDOW_DAYS = 60
# A visit that is the Nth-or-later visit inside the rolling window escalates.
ESCALATION_VISITS = 3

COVERAGE_TYPES = ["equipment", "labor", "parts", "workmanship"]


# ---------- pydantic models (kept in this module, NOT in models.py) ----------

class WarrantyIn(BaseModel):
    customer_id: str = ""
    customer_name: str = ""
    equipment_id: Optional[str] = None  # required unless coverage is job-scoped
    job_id: Optional[str] = None        # required for per-job labor warranties
    coverage_type: str = Field(default="equipment", description="equipment|labor|parts|workmanship")
    start_date: str                     # ISO date (YYYY-MM-DD) or datetime
    end_date: str                       # ISO date (YYYY-MM-DD) or datetime
    terms: str = ""
    active: bool = True


class WarrantyUpdate(BaseModel):
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    terms: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    active: Optional[bool] = None


class ResolveIn(BaseModel):
    note: str = ""
    resolved_by: str = ""


# ---------- helpers ----------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    """Now, in UTC. Monkeypatchable in tests for deterministic date math."""
    return datetime.now(timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _date_of(value: Optional[str]) -> Optional[datetime.date]:
    dt = _parse_iso(value)
    return dt.date() if dt else None


def _coverage_status(start: Optional[str], end: Optional[str], today) -> str:
    """active | upcoming | expired from a [start, end] date range."""
    s = _date_of(start)
    e = _date_of(end)
    if s is None or e is None:
        return "expired"  # unparseable dates are never treated as live coverage
    if today < s:
        return "upcoming"
    if today > e:
        return "expired"
    return "active"


def _days_remaining(end: Optional[str], today) -> Optional[int]:
    e = _date_of(end)
    return (e - today).days if e else None


def _validate_warranty_dates(start: str, end: str):
    s = _date_of(start)
    e = _date_of(end)
    if s is None:
        raise HTTPException(400, "start_date must be an ISO date (YYYY-MM-DD)")
    if e is None:
        raise HTTPException(400, "end_date must be an ISO date (YYYY-MM-DD)")
    if e < s:
        raise HTTPException(400, "end_date must not be before start_date")
    return s, e


def _public_warranty(doc: dict, today) -> dict:
    status = _coverage_status(doc.get("start_date"), doc.get("end_date"), today)
    return {
        "id": doc.get("id"),
        "tenant_id": doc.get("tenant_id"),
        "customer_id": doc.get("customer_id", ""),
        "customer_name": doc.get("customer_name", ""),
        "equipment_id": doc.get("equipment_id"),
        "job_id": doc.get("job_id"),
        "coverage_type": doc.get("coverage_type", "equipment"),
        "start_date": doc.get("start_date"),
        "end_date": doc.get("end_date"),
        "terms": doc.get("terms", ""),
        "active": bool(doc.get("active", True)),
        "status": status,
        "days_remaining": _days_remaining(doc.get("end_date"), today) if status == "active" else None,
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


def _within_window(later: datetime, earlier: datetime) -> bool:
    """Inclusive 60-day boundary: exactly 60 days still counts."""
    delta = later - earlier
    return timedelta(0) <= delta <= timedelta(days=CALLBACK_WINDOW_DAYS)


async def _detect_callbacks(db, tenant_id: str, now: datetime) -> List[dict]:
    """Flag repeat visits from the equipment service history.

    For each equipment unit, visits are sorted by date. A visit is a callback
    when at least one prior visit for the same unit is within 60 days (the
    boundary is inclusive). When a visit is the 3rd-or-later inside a rolling
    60-day window it becomes `escalated`. Manager resolutions (stored in
    `warranty_callback_resolutions`) flip any callback to `resolved`.
    """
    rows = await db.equipment_service_events.find(
        {"tenant_id": tenant_id}, {"_id": 0}
    ).to_list(10000)

    equipment_ids = sorted({r.get("equipment_id") for r in rows if r.get("equipment_id")})
    equipment_docs = await db.equipment.find(
        {"tenant_id": tenant_id, "id": {"$in": equipment_ids}}, {"_id": 0}
    ).to_list(10000)
    eq_by_id = {e.get("id"): e for e in equipment_docs}

    resolutions = await db.warranty_callback_resolutions.find(
        {"tenant_id": tenant_id}, {"_id": 0}
    ).to_list(10000)
    resolved_by_id = {r.get("callback_id"): r for r in resolutions if r.get("callback_id")}

    callbacks: List[dict] = []
    for eq_id in equipment_ids:
        unit_events = [r for r in rows if r.get("equipment_id") == eq_id]
        parsed = []
        for r in unit_events:
            dt = _parse_iso(r.get("service_date"))
            if dt is not None:
                parsed.append((dt, r))
        parsed.sort(key=lambda p: p[0])
        for i, (dt, r) in enumerate(parsed):
            prior_in_window = [p for p in parsed[:i] if _within_window(dt, p[0])]
            if not prior_in_window:
                continue  # first visit in a fresh window — not a callback
            visits_in_window = len(prior_in_window) + 1
            cb_id = f"{eq_id}:{r.get('id')}"
            eq = eq_by_id.get(eq_id, {})
            status = "escalated" if visits_in_window >= ESCALATION_VISITS else "open"
            resolution = resolved_by_id.get(cb_id)
            record = {
                "id": cb_id,
                "equipment_id": eq_id,
                "equipment_type": eq.get("equipment_type", ""),
                "equipment_label": " ".join(
                    x for x in (eq.get("make", ""), eq.get("model", ""))
                    if x
                ).strip() or eq.get("location_label", ""),
                "customer_id": eq.get("customer_id", ""),
                "customer_name": eq.get("customer_name", ""),
                "service_event_id": r.get("id"),
                "service_date": r.get("service_date"),
                "job_id": r.get("job_id"),
                "description": r.get("description", ""),
                "tech": r.get("tech", ""),
                "previous_visit_date": prior_in_window[-1][1].get("service_date"),
                "visits_in_window": visits_in_window,
                "status": status,
                "resolved_at": None,
                "resolved_by": "",
                "resolution_note": "",
            }
            if resolution:
                record["status"] = "resolved"
                record["resolved_at"] = resolution.get("resolved_at")
                record["resolved_by"] = resolution.get("resolved_by", "")
                record["resolution_note"] = resolution.get("note", "")
            callbacks.append(record)

    callbacks.sort(key=lambda c: str(c.get("service_date") or ""), reverse=True)
    return callbacks


# ---------- warranties ----------

@router.post("/warranties", status_code=201)
async def create_warranty(data: WarrantyIn, user: dict = Depends(require_tenant_user)):
    """Register an equipment-unit or per-job labor warranty."""
    if data.coverage_type not in COVERAGE_TYPES:
        raise HTTPException(400, f"coverage_type must be one of {COVERAGE_TYPES}")
    if not data.equipment_id and not data.job_id:
        raise HTTPException(400, "One of equipment_id or job_id is required")
    if data.coverage_type == "labor" and not data.job_id:
        raise HTTPException(400, "Labor warranties are per-job: job_id is required")
    _validate_warranty_dates(data.start_date, data.end_date)

    db = get_db()
    now = _now_iso()
    doc = {
        "id": _uuid(),
        "tenant_id": user["tenant_id"],
        "customer_id": data.customer_id,
        "customer_name": data.customer_name,
        "equipment_id": data.equipment_id,
        "job_id": data.job_id,
        "coverage_type": data.coverage_type,
        "start_date": data.start_date,
        "end_date": data.end_date,
        "terms": data.terms,
        "active": bool(data.active),
        "created_at": now,
        "updated_at": now,
    }
    await db.warranties.insert_one(doc)
    return _public_warranty(doc, _utcnow().date())


@router.get("/warranties")
async def list_warranties(
    user: dict = Depends(require_tenant_user),
    customer: str = "",
    equipment: str = "",
    status: str = "all",
):
    """List warranties. Filter by customer (id or name substring), equipment
    id, and computed status (active|upcoming|expired|all)."""
    status = (status or "all").strip().lower()
    if status not in ("all", "active", "upcoming", "expired"):
        raise HTTPException(400, "status must be one of all|active|upcoming|expired")
    db = get_db()
    today = _utcnow().date()
    rows = await db.warranties.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).to_list(5000)

    needle = customer.strip().lower()
    items = []
    for r in rows:
        if equipment and r.get("equipment_id") != equipment:
            continue
        if needle and needle not in (
            f"{r.get('customer_id', '')} {r.get('customer_name', '')}".lower()
        ):
            continue
        pub = _public_warranty(r, today)
        if status != "all" and pub["status"] != status:
            continue
        items.append(pub)
    items.sort(key=lambda w: str(w.get("end_date") or ""))
    return {"warranties": items, "count": len(items)}


@router.get("/warranties/{warranty_id}")
async def get_warranty(warranty_id: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    doc = await db.warranties.find_one(
        {"id": warranty_id, "tenant_id": user["tenant_id"]}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(404, "Warranty not found")
    return _public_warranty(doc, _utcnow().date())


@router.put("/warranties/{warranty_id}")
async def update_warranty(
    warranty_id: str, data: WarrantyUpdate, user: dict = Depends(require_tenant_user)
):
    db = get_db()
    doc = await db.warranties.find_one(
        {"id": warranty_id, "tenant_id": user["tenant_id"]}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(404, "Warranty not found")

    patch: dict = {}
    for field in ("customer_id", "customer_name", "terms", "active"):
        val = getattr(data, field)
        if val is not None:
            patch[field] = val
    start = data.start_date if data.start_date is not None else doc.get("start_date")
    end = data.end_date if data.end_date is not None else doc.get("end_date")
    if data.start_date is not None or data.end_date is not None:
        _validate_warranty_dates(start, end)
        patch["start_date"] = start
        patch["end_date"] = end
    patch["updated_at"] = _now_iso()
    await db.warranties.update_one(
        {"id": warranty_id, "tenant_id": user["tenant_id"]}, {"$set": patch}
    )
    updated = await db.warranties.find_one(
        {"id": warranty_id, "tenant_id": user["tenant_id"]}, {"_id": 0}
    )
    return _public_warranty(updated, _utcnow().date())


# ---------- coverage ----------
@router.get("/coverage")
async def check_coverage(
    equipment_id: str = "",
    job_id: str = "",
    user: dict = Depends(require_tenant_user),
):
    """Coverage check: is this equipment unit (or job's labor) under warranty
    right now? Returns covered?, nearest expiry, and the matching warranties."""
    if not equipment_id and not job_id:
        raise HTTPException(400, "One of equipment_id or job_id is required")
    db = get_db()
    today = _utcnow().date()
    rows = await db.warranties.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).to_list(5000)

    matching = []
    for r in rows:
        if not r.get("active", True):
            continue
        if equipment_id and r.get("equipment_id") != equipment_id:
            continue
        if job_id and r.get("job_id") != job_id:
            continue
        pub = _public_warranty(r, today)
        matching.append(pub)

    live = [w for w in matching if w["status"] == "active"]
    live.sort(key=lambda w: str(w.get("end_date") or ""))
    return {
        "covered": len(live) > 0,
        "expires": live[0]["end_date"] if live else None,
        "days_remaining": live[0]["days_remaining"] if live else None,
        "warranties": matching,
        "equipment_id": equipment_id or None,
        "job_id": job_id or None,
    }


# ---------- callbacks ----------

@router.get("/callbacks")
async def list_callbacks(
    user: dict = Depends(require_tenant_user),
    status: str = "all",
    tech: str = "",
):
    """Auto-flagged callbacks: repeat visits for the same equipment unit
    within 60 days. Filter by status (open|escalated|resolved|all) and tech."""
    status = (status or "all").strip().lower()
    if status not in ("all", "open", "escalated", "resolved"):
        raise HTTPException(400, "status must be one of all|open|escalated|resolved")
    db = get_db()
    callbacks = await _detect_callbacks(db, user["tenant_id"], _utcnow())
    needle = tech.strip().lower()
    items = [
        c for c in callbacks
        if (status == "all" or c["status"] == status)
        and (not needle or needle in str(c.get("tech", "")).lower())
    ]
    return {
        "callbacks": items,
        "count": len(items),
        "open": sum(1 for c in callbacks if c["status"] == "open"),
        "escalated": sum(1 for c in callbacks if c["status"] == "escalated"),
    }


@router.post("/callbacks/{callback_id}/resolve")
async def resolve_callback(
    callback_id: str, data: ResolveIn, user: dict = Depends(require_tenant_user)
):
    """Manager review: resolve an open or escalated callback."""
    db = get_db()
    callbacks = await _detect_callbacks(db, user["tenant_id"], _utcnow())
    target = next((c for c in callbacks if c["id"] == callback_id), None)
    if not target:
        raise HTTPException(404, "Callback not found")
    if target["status"] == "resolved":
        return target  # idempotent

    now = _now_iso()
    await db.warranty_callback_resolutions.update_one(
        {"tenant_id": user["tenant_id"], "callback_id": callback_id},
        {"$set": {
            "tenant_id": user["tenant_id"],
            "callback_id": callback_id,
            "resolved_at": now,
            "resolved_by": data.resolved_by or user.get("id", ""),
            "note": data.note,
        }},
        upsert=True,
    )
    target["status"] = "resolved"
    target["resolved_at"] = now
    target["resolved_by"] = data.resolved_by or user.get("id", "")
    target["resolution_note"] = data.note
    return target


# ---------- stats ----------

@router.get("/stats")
async def warranty_stats(user: dict = Depends(require_tenant_user)):
    """Callback-rate dashboard: overall rate, by tech, by equipment type, and
    a 6-month trend, plus warranty inventory counts."""
    db = get_db()
    tenant_id = user["tenant_id"]
    now = _utcnow()
    today = now.date()

    events = await db.equipment_service_events.find(
        {"tenant_id": tenant_id}, {"_id": 0}
    ).to_list(10000)
    callbacks = await _detect_callbacks(db, tenant_id, now)
    callback_event_ids = {c["service_event_id"] for c in callbacks}

    equipment_docs = await db.equipment.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(10000)
    eq_type = {e.get("id"): (e.get("equipment_type") or "unknown") for e in equipment_docs}

    def _rate(cb: int, total: int) -> float:
        return round(cb / total * 100, 1) if total else 0.0

    total_visits = len(events)
    total_callbacks = len(callbacks)

    by_tech: dict = {}
    for e in events:
        t = (e.get("tech") or "Unassigned").strip() or "Unassigned"
        slot = by_tech.setdefault(t, {"tech": t, "visits": 0, "callbacks": 0})
        slot["visits"] += 1
        if e.get("id") in callback_event_ids:
            slot["callbacks"] += 1
    tech_rows = sorted(by_tech.values(), key=lambda r: r["visits"], reverse=True)
    for r in tech_rows:
        r["rate_pct"] = _rate(r["callbacks"], r["visits"])

    by_type: dict = {}
    for e in events:
        et = eq_type.get(e.get("equipment_id"), "unknown")
        slot = by_type.setdefault(et, {"equipment_type": et, "visits": 0, "callbacks": 0})
        slot["visits"] += 1
        if e.get("id") in callback_event_ids:
            slot["callbacks"] += 1
    type_rows = sorted(by_type.values(), key=lambda r: r["visits"], reverse=True)
    for r in type_rows:
        r["rate_pct"] = _rate(r["callbacks"], r["visits"])

    months: List[dict] = []
    year, month = now.year, now.month
    for _ in range(6):
        key = f"{year:04d}-{month:02d}"
        months.append({"month": key, "visits": 0, "callbacks": 0})
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    month_index = {m["month"]: m for m in months}
    for e in events:
        dt = _parse_iso(e.get("service_date"))
        if not dt:
            continue
        slot = month_index.get(f"{dt.year:04d}-{dt.month:02d}")
        if slot is None:
            continue
        slot["visits"] += 1
        if e.get("id") in callback_event_ids:
            slot["callbacks"] += 1
    for m in months:
        m["rate_pct"] = _rate(m["callbacks"], m["visits"])
    trend = list(reversed(months))

    warranties = await db.warranties.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(5000)
    active_n = upcoming_n = expired_n = 0
    expiring_30d = 0
    for w in warranties:
        st = _coverage_status(w.get("start_date"), w.get("end_date"), today)
        if st == "active":
            active_n += 1
            dr = _days_remaining(w.get("end_date"), today)
            if dr is not None and dr <= 30:
                expiring_30d += 1
        elif st == "upcoming":
            upcoming_n += 1
        else:
            expired_n += 1

    return {
        "total_visits": total_visits,
        "total_callbacks": total_callbacks,
        "callback_rate_pct": _rate(total_callbacks, total_visits),
        "escalated_open": sum(1 for c in callbacks if c["status"] == "escalated"),
        "open_callbacks": sum(1 for c in callbacks if c["status"] == "open"),
        "by_tech": tech_rows,
        "by_equipment_type": type_rows,
        "trend": trend,
        "warranties": {
            "active": active_n,
            "upcoming": upcoming_n,
            "expired": expired_n,
            "expiring_30d": expiring_30d,
        },
    }
