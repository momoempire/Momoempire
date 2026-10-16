"""Scheduling engine: staff, appointment types, availability calculation."""
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timedelta, timezone
from db import get_db
from models import _now_iso
from models import AppointmentType, AppointmentTypeIn, StaffMember, AvailabilityQuery
from security import require_tenant_user, require_tenant_owner_or_admin

router = APIRouter(prefix="/tenants/scheduling", tags=["scheduling"])


def _parse_hours(hr: str) -> tuple[int, int] | None:
    """Parse '09:00-17:00' → (start_minutes, end_minutes). None for closed."""
    hr = (hr or "").strip().lower()
    if not hr or hr == "closed":
        return None
    try:
        a, b = hr.split("-")
        sh, sm = [int(x) for x in a.strip().split(":")]
        eh, em = [int(x) for x in b.strip().split(":")]
        return (sh * 60 + sm, eh * 60 + em)
    except Exception:
        return None


# ---------- Appointment Types ----------
@router.get("/types")
async def list_types(user: dict = Depends(require_tenant_user)):
    db = get_db()
    return await db.appointment_types.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).sort("name", 1).to_list(200)


@router.post("/types")
async def create_type(data: AppointmentTypeIn, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    t = AppointmentType(tenant_id=user["tenant_id"], **data.model_dump())
    await db.appointment_types.insert_one(dict(t.model_dump()))
    return t.model_dump()


@router.put("/types/{tid}")
async def update_type(tid: str, data: AppointmentTypeIn, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    res = await db.appointment_types.update_one(
        {"id": tid, "tenant_id": user["tenant_id"]},
        {"$set": data.model_dump()},
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Not found")
    return await db.appointment_types.find_one({"id": tid}, {"_id": 0})


@router.delete("/types/{tid}")
async def delete_type(tid: str, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    res = await db.appointment_types.delete_one({"id": tid, "tenant_id": user["tenant_id"]})
    if res.deleted_count == 0:
        raise HTTPException(404, "Not found")
    return {"status": "ok"}


# ---------- Staff ----------
@router.get("/staff")
async def list_staff(user: dict = Depends(require_tenant_user)):
    db = get_db()
    return await db.schedule_staff.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).sort("name", 1).to_list(100)


@router.post("/staff")
async def create_staff(data: StaffMember, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    doc = data.model_dump()
    doc["tenant_id"] = user["tenant_id"]
    doc["created_at"] = _now_iso()
    await db.schedule_staff.insert_one(dict(doc))
    return doc


@router.put("/staff/{sid}")
async def update_staff(sid: str, data: StaffMember, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    res = await db.schedule_staff.update_one(
        {"id": sid, "tenant_id": user["tenant_id"]},
        {"$set": data.model_dump()},
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Not found")
    return await db.schedule_staff.find_one({"id": sid}, {"_id": 0})


@router.delete("/staff/{sid}")
async def delete_staff(sid: str, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    res = await db.schedule_staff.delete_one({"id": sid, "tenant_id": user["tenant_id"]})
    if res.deleted_count == 0:
        raise HTTPException(404, "Not found")
    return {"status": "ok"}


# ---------- Availability engine ----------
@router.post("/availability")
async def availability(q: AvailabilityQuery, user: dict = Depends(require_tenant_user)):
    """Compute open slots within [from_iso, to_iso] in 30-minute steps.
    Respects tenant hours, appointment-type duration + buffer + travel, staff schedule (if given),
    and existing appointments."""
    db = get_db()
    tenant = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0})
    appt_type = None
    duration = 60
    buffer = 15
    travel = 0
    if q.appointment_type_id:
        appt_type = await db.appointment_types.find_one({"id": q.appointment_type_id, "tenant_id": user["tenant_id"]})
        if appt_type:
            duration = appt_type["duration_minutes"]
            buffer = appt_type.get("buffer_minutes", 15)
            travel = appt_type.get("travel_minutes", 0)
    slot_minutes = duration + buffer + travel

    staff = None
    if q.staff_id:
        staff = await db.schedule_staff.find_one({"id": q.staff_id, "tenant_id": user["tenant_id"]})
        if not staff:
            raise HTTPException(404, "Staff not found")

    start = datetime.fromisoformat(q.from_iso.replace("Z", "+00:00"))
    end = datetime.fromisoformat(q.to_iso.replace("Z", "+00:00"))
    if end <= start or (end - start).days > 30:
        raise HTTPException(400, "Range must be 1..30 days and end>start")

    tenant_hours = tenant.get("hours") or {}
    staff_hours = (staff or {}).get("weekly_hours") or {}
    off_dates = set((staff or {}).get("off_dates") or [])

    # Load appointments in range
    appts = await db.appointments.find(
        {"tenant_id": user["tenant_id"], "start_at": {"$gte": start.isoformat(), "$lte": end.isoformat()}},
        {"_id": 0, "start_at": 1, "end_at": 1, "staff": 1},
    ).to_list(500)
    occupied = []
    for a in appts:
        try:
            s = datetime.fromisoformat(a["start_at"].replace("Z", "+00:00"))
            e = datetime.fromisoformat(a["end_at"].replace("Z", "+00:00")) if a.get("end_at") else s + timedelta(minutes=60)
            # Buffer around existing appointments
            occupied.append((s - timedelta(minutes=buffer), e + timedelta(minutes=buffer)))
        except Exception:
            continue

    slots = []
    cur_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while cur_day <= end:
        day_key = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][cur_day.weekday()]
        date_iso = cur_day.date().isoformat()
        if date_iso in off_dates:
            cur_day += timedelta(days=1); continue
        th = _parse_hours(tenant_hours.get(day_key))
        sh = _parse_hours(staff_hours.get(day_key)) if staff_hours else th
        if not th:
            cur_day += timedelta(days=1); continue
        # Effective window = intersection of tenant + staff
        s_min = max(th[0], sh[0] if sh else th[0])
        e_min = min(th[1], sh[1] if sh else th[1])
        if e_min - s_min < duration:
            cur_day += timedelta(days=1); continue

        t = s_min
        while t + duration <= e_min:
            slot_start = cur_day + timedelta(minutes=t)
            slot_end = slot_start + timedelta(minutes=duration)
            if slot_start < start or slot_end > end:
                t += 30; continue
            # Check conflict
            conflict = any(not (slot_end <= o_s or slot_start >= o_e) for (o_s, o_e) in occupied)
            if not conflict:
                slots.append({"start": slot_start.isoformat(), "end": slot_end.isoformat()})
                if len(slots) >= 100:
                    break
            t += 30
        if len(slots) >= 100:
            break
        cur_day += timedelta(days=1)

    return {"duration_minutes": duration, "buffer_minutes": buffer, "travel_minutes": travel, "slots": slots}
