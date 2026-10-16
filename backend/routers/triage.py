"""AI triage self-booking (#16).

Website widget flow: a customer describes their problem in plain words,
the widget AI-triages it (category / urgency / estimated duration), offers
real open slots from the tenant's schedule, and submits a booking request
that stays PENDING until the shop confirms or declines it in the dashboard.

Public surface (NO auth; tenant resolved from the `shop_slug` path param or
an optional `?token=` public token):
  POST /public/triage/{shop_slug}/analyze  {description} -> triage result
  GET  /public/triage/{shop_slug}/slots?category=&urgency=
  POST /public/triage/{shop_slug}/book      {triage_id, slot_start, slot_end,
                                             customer_name, customer_phone}

Tenant dashboard (auth required; owner/admin for settings + confirm/decline):
  GET  /triage/settings
  PUT  /triage/settings
  GET  /triage/requests[?status=]
  POST /triage/requests/{id}/confirm
  POST /triage/requests/{id}/decline

The classifier below is a deterministic keyword+rules engine.
TODO(LIVE): swap `classify()` for an LLM triage call grounded on the tenant's
trade knowledge base; keep this as the offline fallback.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso
from security import require_tenant_owner_or_admin, require_tenant_user

public_router = APIRouter(prefix="/public/triage", tags=["triage-public"])
router = APIRouter(prefix="/triage", tags=["triage"])

# ---------- input models ----------

class AnalyzeIn(BaseModel):
    description: str = Field(..., min_length=1, max_length=2000)


class BookIn(BaseModel):
    triage_id: str
    slot_start: str  # ISO
    slot_end: str    # ISO
    customer_name: str = Field(..., min_length=1, max_length=120)
    customer_phone: str = Field(..., min_length=1, max_length=40)


class TriageSettingsIn(BaseModel):
    enabled: bool = True
    covered_categories: list[str] = Field(default_factory=list)  # [] = all
    slot_buffer_minutes: int = Field(default=15, ge=0, le=120)
    lead_days: int = Field(default=7, ge=1, le=30)
    emergency_phone: str = ""
    emergency_message: str = ""


# ---------- deterministic triage classifier ----------

EMERGENCY_KEYWORDS = [
    "gas leak", "smell gas", "smells like gas", "burst pipe", "flooding", "flood",
    "sparking", "electrical fire", "smoke coming", "carbon monoxide", "co alarm",
    "sewage backup", "raw sewage", "no power", "power is out", "water everywhere",
    "gushing water", "pipe burst", "flames", "shock",
]

SAME_DAY_KEYWORDS = [
    "today", "asap", "urgent", "right now", "immediately", "can't wait",
    "emergency", "hurry", "tonight",
]

CATEGORY_KEYWORDS = {
    "plumbing": ["leak", "drip", "clog", "toilet", "drain", "faucet", "pipe",
                 "water heater", "sewage", "sink", "shower", "bathtub", "garbage disposal"],
    "hvac": ["ac", "a/c", "air condition", "furnace", "thermostat", "blowing warm",
             "no heat", "no cool", "heat pump", "air filter", "duct", "not cooling",
             "not heating", "hvac"],
    "electrical": ["outlet", "breaker", "wiring", "switch", "panel", "flicker",
                   "light fixture", "ceiling fan", "electric", "power", "voltage"],
    "appliance": ["fridge", "refrigerator", "freezer", "washer", "dryer",
                  "dishwasher", "oven", "stove", "microwave", "ice maker"],
    "pest": ["ants", "roaches", "cockroach", "rats", "mice", "mouse", "wasps",
             "bees", "termites", "spiders", "mosquitoes", "bed bugs", "pest"],
    "garage_door": ["garage door", "garage opener", "spring broke", "door stuck"],
    "roofing": ["roof", "shingle", "leak in ceiling", "ceiling stain", "gutter"],
}

CATEGORY_DURATIONS = {
    "plumbing": 90, "hvac": 90, "electrical": 60, "appliance": 60,
    "pest": 45, "garage_door": 60, "roofing": 120,
}

GENERIC_QUESTIONS = [
    "Which trade is this about — plumbing, heating/cooling, electrical, appliances, or something else?",
    "When did the problem start?",
    "Is anything actively leaking, sparking, or unsafe right now?",
]

CATEGORY_QUESTIONS = {
    "plumbing": ["Where is the leak or clog — sink, toilet, shower, or under a fixture?",
                 "Can you shut off the water to it if needed?"],
    "hvac": ["Is the system not heating, not cooling, or making a strange noise?",
             "Is the thermostat display on?"],
    "electrical": ["Is the breaker tripped, or is there no power at all?",
                   "Do you see any sparks, smoke, or burning smell?"],
    "appliance": ["What brand and model is the appliance?",
                  "Is it completely dead or partially working?"],
    "pest": ["What kind of pest are you seeing, and where?",
             "How long has this been going on?"],
    "garage_door": ["Is the door stuck open, stuck closed, or off its track?",
                    "Did a spring or cable visibly break?"],
    "roofing": ["Is water coming in during rain, or is this storm damage?",
                "How old is the roof?"],
}

CONFIDENCE_THRESHOLD = 0.5


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def classify(description: str) -> dict:
    """Deterministic keyword+rules triage.

    Returns category (or None), urgency (emergency/same-day/scheduled),
    estimated duration, confidence (0..1), matched keywords, and clarifying
    questions when confidence is below CONFIDENCE_THRESHOLD.

    TODO(LIVE): replace with an LLM call grounded on the tenant's services
    and trade knowledge base; keep this engine as the offline fallback.
    """
    text = _normalize(description)
    matched: list[str] = []

    def _hits(keywords: list[str]) -> list[str]:
        return [k for k in keywords if k in text]

    emergency_hits = _hits(EMERGENCY_KEYWORDS)
    if emergency_hits:
        urgency = "emergency"
        matched.extend(emergency_hits)
    elif _hits(SAME_DAY_KEYWORDS):
        urgency = "same-day"
    else:
        urgency = "scheduled"

    best_category: Optional[str] = None
    best_hits: list[str] = []
    for category, keywords in CATEGORY_KEYWORDS.items():
        hits = _hits(keywords)
        if len(hits) > len(best_hits):
            best_category, best_hits = category, hits
    matched.extend(best_hits)

    if len(best_hits) >= 2:
        confidence = 0.95
    elif len(best_hits) == 1 and len(text) >= 10:
        confidence = 0.7
    elif best_category and len(text) >= 30:
        confidence = 0.6
    elif len(text) >= 20:
        confidence = 0.35
    else:
        confidence = 0.2

    duration = CATEGORY_DURATIONS.get(best_category or "", 60)

    questions: list[str] = []
    if confidence < CONFIDENCE_THRESHOLD:
        questions = list(GENERIC_QUESTIONS)
        if best_category:
            questions = CATEGORY_QUESTIONS.get(best_category, []) + GENERIC_QUESTIONS[:1]

    return {
        "category": best_category,
        "urgency": urgency,
        "estimated_duration_minutes": duration,
        "confidence": confidence,
        "matched_keywords": sorted(set(matched)),
        "clarifying_questions": questions,
    }


# ---------- tenant resolution (public) ----------

async def _public_tenant(db, shop_slug: str, token: Optional[str] = None) -> dict:
    """Resolve the tenant for a public widget call.

    Primary: the `shop_slug` path param (public business slug). A `?token=`
    query param can override it when the tenant has a `triage_public_token`
    set in their triage settings.
    """
    tenant = None
    if token:
        tenant = await db.tenants.find_one(
            {"triage_public_token": token, "status": "active"},
            {"_id": 0, "password_hash": 0},
        )
    if tenant is None:
        tenant = await db.tenants.find_one(
            {"slug": shop_slug, "status": "active"},
            {"_id": 0, "password_hash": 0},
        )
    if not tenant:
        raise HTTPException(404, "Business not found")
    return tenant


def _settings(tenant: dict) -> dict:
    """Tenant triage settings merged over defaults."""
    saved = tenant.get("triage_settings") or {}
    return {
        "enabled": saved.get("enabled", True),
        "covered_categories": saved.get("covered_categories", []),
        "slot_buffer_minutes": int(saved.get("slot_buffer_minutes", 15)),
        "lead_days": int(saved.get("lead_days", 7)),
        "emergency_phone": saved.get("emergency_phone", ""),
        "emergency_message": saved.get("emergency_message", ""),
        "public_token": saved.get("public_token", ""),
    }


# ---------- slot engine (public-safe mirror of scheduling availability) ----------

def _parse_hours(hr: str) -> Optional[tuple[int, int]]:
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


async def _category_duration(db, tenant: dict, category: Optional[str], settings: dict) -> tuple[int, int]:
    """(duration, buffer) — prefer the tenant's matching appointment type,
    fall back to the category defaults and the tenant's slot buffer."""
    duration = CATEGORY_DURATIONS.get(category or "", 60)
    buffer = settings["slot_buffer_minutes"]
    if category:
        appt_type = await db.appointment_types.find_one(
            {"tenant_id": tenant["id"]}, {"_id": 0})
        # look for an appointment type whose name mentions the category
        if appt_type:
            types = await db.appointment_types.find(
                {"tenant_id": tenant["id"]}, {"_id": 0}).to_list(200)
            for t in types:
                if (category or "").replace("_", " ") in (t.get("name") or "").lower():
                    duration = int(t.get("duration_minutes") or duration)
                    buffer = int(t.get("buffer_minutes", buffer))
                    break
    return duration, buffer


async def _occupied_windows(db, tenant_id: str, start: datetime, end: datetime,
                            buffer: int) -> list[tuple[datetime, datetime]]:
    appts = await db.appointments.find(
        {"tenant_id": tenant_id,
         "start_at": {"$gte": start.isoformat(), "$lte": end.isoformat()}},
        {"_id": 0, "start_at": 1, "end_at": 1},
    ).to_list(500)
    occupied = []
    for a in appts:
        try:
            s = datetime.fromisoformat(str(a["start_at"]).replace("Z", "+00:00"))
            e = (datetime.fromisoformat(str(a.get("end_at")).replace("Z", "+00:00"))
                 if a.get("end_at") else s + timedelta(minutes=60))
            occupied.append((s - timedelta(minutes=buffer), e + timedelta(minutes=buffer)))
        except Exception:
            continue
    return occupied


def _overlaps(slot_start: datetime, slot_end: datetime,
              occupied: list[tuple[datetime, datetime]]) -> bool:
    return any(not (slot_end <= o_s or slot_start >= o_e) for (o_s, o_e) in occupied)


async def find_open_slots(db, tenant: dict, category: Optional[str], urgency: str) -> dict:
    """Compute real open slots for the public widget. Returns only
    start/end/duration — no staff names, no appointment details."""
    settings = _settings(tenant)
    duration, buffer = await _category_duration(db, tenant, category, settings)

    now = datetime.now(timezone.utc)
    if urgency == "emergency":
        end = now + timedelta(days=1)
    elif urgency == "same-day":
        end = now + timedelta(days=2)
    else:
        end = now + timedelta(days=settings["lead_days"])

    tenant_hours = tenant.get("hours") or {}
    occupied = await _occupied_windows(db, tenant["id"], now, end, buffer)

    slots: list[dict] = []
    cur_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    while cur_day <= end and len(slots) < 50:
        day_key = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][cur_day.weekday()]
        window = _parse_hours(tenant_hours.get(day_key))
        if window and window[1] - window[0] >= duration:
            t = window[0]
            while t + duration <= window[1] and len(slots) < 50:
                slot_start = cur_day + timedelta(minutes=t)
                slot_end = slot_start + timedelta(minutes=duration)
                if slot_start >= now and not _overlaps(slot_start, slot_end, occupied):
                    slots.append({"start": slot_start.isoformat(),
                                  "end": slot_end.isoformat()})
                t += 30
        cur_day += timedelta(days=1)

    return {
        "duration_minutes": duration,
        "buffer_minutes": buffer,
        "slots": slots,
    }


# ---------- public endpoints ----------

@public_router.post("/{shop_slug}/analyze")
async def analyze(shop_slug: str, data: AnalyzeIn, token: Optional[str] = None):
    """AI triage for a plain-words problem description. No auth."""
    db = get_db()
    tenant = await _public_tenant(db, shop_slug, token)
    settings = _settings(tenant)
    if not settings["enabled"]:
        raise HTTPException(403, "Online booking is not enabled for this business")

    description = data.description.strip()
    if not description:
        raise HTTPException(400, "description is required")

    result = classify(description)
    category = result["category"]
    covered = (not settings["covered_categories"]) or (
        category in settings["covered_categories"])

    triage_id = uuid.uuid4().hex
    await db.triage_analyses.insert_one({
        "id": triage_id,
        "tenant_id": tenant["id"],
        "description": description,
        "category": category,
        "urgency": result["urgency"],
        "confidence": result["confidence"],
        "matched_keywords": result["matched_keywords"],
        "estimated_duration_minutes": result["estimated_duration_minutes"],
        "covered": covered,
        "created_at": _now_iso(),
    })

    out = {
        "triage_id": triage_id,
        "shop": {"slug": tenant.get("slug"), "name": tenant.get("name")},
        "category": category,
        "urgency": result["urgency"],
        "estimated_duration_minutes": result["estimated_duration_minutes"],
        "confidence": result["confidence"],
        "covered": covered,
        "clarifying_questions": result["clarifying_questions"],
        "matched_keywords": result["matched_keywords"],
    }
    if result["urgency"] == "emergency":
        out["emergency_phone"] = settings["emergency_phone"]
        out["emergency_message"] = settings["emergency_message"] or (
            "This sounds urgent. If anyone is in danger, call 911 first, "
            "then reach us at the number above.")
    return out


@public_router.get("/{shop_slug}/slots")
async def public_slots(shop_slug: str, category: str = "", urgency: str = "",
                       token: Optional[str] = None):
    """Real open slots for the widget. No auth; tenant-scoped by slug."""
    db = get_db()
    tenant = await _public_tenant(db, shop_slug, token)
    settings = _settings(tenant)
    if not settings["enabled"]:
        raise HTTPException(403, "Online booking is not enabled for this business")

    cat = (category or "").strip().lower() or None
    urg = (urgency or "").strip().lower() or "scheduled"
    if urg not in {"emergency", "same-day", "scheduled"}:
        urg = "scheduled"

    slots = await find_open_slots(db, tenant, cat, urg)
    return {
        "shop": {"slug": tenant.get("slug"), "name": tenant.get("name")},
        "category": cat,
        "urgency": urg,
        **slots,
    }


@public_router.post("/{shop_slug}/book")
async def public_book(shop_slug: str, data: BookIn, token: Optional[str] = None):
    """Create a booking request. Status starts PENDING — the shop must
    confirm it in the dashboard before it becomes an appointment."""
    db = get_db()
    tenant = await _public_tenant(db, shop_slug, token)
    settings = _settings(tenant)
    if not settings["enabled"]:
        raise HTTPException(403, "Online booking is not enabled for this business")

    triage = await db.triage_analyses.find_one(
        {"id": data.triage_id, "tenant_id": tenant["id"]}, {"_id": 0})
    if not triage:
        raise HTTPException(404, "Triage session not found or expired")

    try:
        slot_start = datetime.fromisoformat(data.slot_start.replace("Z", "+00:00"))
        slot_end = datetime.fromisoformat(data.slot_end.replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(400, "slot_start/slot_end must be ISO datetimes")
    if slot_end <= slot_start or slot_start < datetime.now(timezone.utc) - timedelta(minutes=5):
        raise HTTPException(400, "Slot is in the past")

    duration, buffer = await _category_duration(db, tenant, triage.get("category"), settings)
    occupied = await _occupied_windows(db, tenant["id"], slot_start, slot_end, buffer)
    if _overlaps(slot_start, slot_end, occupied):
        raise HTTPException(409, "That slot was just taken — please pick another")

    request_id = uuid.uuid4().hex
    await db.triage_requests.insert_one({
        "id": request_id,
        "tenant_id": tenant["id"],
        "triage_id": data.triage_id,
        "category": triage.get("category"),
        "urgency": triage.get("urgency"),
        "estimated_duration_minutes": triage.get("estimated_duration_minutes"),
        "description": triage.get("description"),
        "slot_start": slot_start.isoformat(),
        "slot_end": slot_end.isoformat(),
        "customer_name": data.customer_name.strip(),
        "customer_phone": data.customer_phone.strip(),
        "status": "pending",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    })
    return {
        "request_id": request_id,
        "status": "pending",
        "slot_start": slot_start.isoformat(),
        "slot_end": slot_end.isoformat(),
        "message": "Request received — the shop will confirm your booking shortly.",
    }


# ---------- tenant endpoints ----------

@router.get("/settings")
async def get_settings(user: dict = Depends(require_tenant_user)):
    """Triage + widget settings for this shop (owner/admin to change)."""
    db = get_db()
    tenant = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0})
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    return _settings(tenant)


@router.put("/settings")
async def update_settings(data: TriageSettingsIn,
                          user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    tenant = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0})
    if not tenant:
        raise HTTPException(404, "Tenant not found")

    settings = data.model_dump()
    # generate a public widget token on demand so the shop can embed the
    # widget without exposing its slug-only URL
    if settings.get("public_token") and not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", settings["public_token"]):
        raise HTTPException(400, "public_token must be 8-64 URL-safe characters")
    await db.tenants.update_one(
        {"id": user["tenant_id"]},
        {"$set": {"triage_settings": settings}},
    )
    if settings.get("public_token"):
        await db.tenants.update_one(
            {"id": user["tenant_id"]},
            {"$set": {"triage_public_token": settings["public_token"]}},
        )
    return {"ok": True, "settings": settings}


@router.get("/requests")
async def list_requests(status: str = "", user: dict = Depends(require_tenant_user)):
    """Booking requests from the widget (default: pending first)."""
    db = get_db()
    query: dict = {"tenant_id": user["tenant_id"]}
    if status:
        if status not in {"pending", "confirmed", "declined"}:
            raise HTTPException(400, "Invalid status filter")
        query["status"] = status
    docs = await db.triage_requests.find(query, {"_id": 0}).to_list(200)
    order = {"pending": 0, "confirmed": 1, "declined": 2}
    docs.sort(key=lambda d: (order.get(d.get("status"), 9), d.get("created_at", "")))
    return {"requests": docs, "count": len(docs)}


@router.post("/requests/{request_id}/confirm")
async def confirm_request(request_id: str,
                          user: dict = Depends(require_tenant_owner_or_admin)):
    """Confirm a booking request -> creates the real appointment."""
    db = get_db()
    req = await db.triage_requests.find_one(
        {"id": request_id, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not req:
        raise HTTPException(404, "Request not found")
    if req.get("status") != "pending":
        raise HTTPException(400, f"Request is already {req.get('status')}")

    # re-verify the slot is still free before confirming
    tenant = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0})
    settings = _settings(tenant or {})
    try:
        slot_start = datetime.fromisoformat(req["slot_start"].replace("Z", "+00:00"))
        slot_end = datetime.fromisoformat(req["slot_end"].replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(400, "Request has an invalid slot")
    occupied = await _occupied_windows(db, user["tenant_id"], slot_start, slot_end,
                                       settings["slot_buffer_minutes"])
    if _overlaps(slot_start, slot_end, occupied):
        raise HTTPException(409, "Slot is no longer available")

    appointment = {
        "id": uuid.uuid4().hex,
        "tenant_id": user["tenant_id"],
        "customer_name": req.get("customer_name", ""),
        "customer_phone": req.get("customer_phone", ""),
        "service_name": (req.get("category") or "service").replace("_", " ").title(),
        "start_at": slot_start.isoformat(),
        "end_at": slot_end.isoformat(),
        "status": "scheduled",
        "notes": f"Triage: {req.get('description', '')} "
                 f"(urgency: {req.get('urgency')}, confidence: {req.get('confidence')})".strip(),
        "created_at": _now_iso(),
    }
    await db.appointments.insert_one(appointment)
    await db.triage_requests.update_one(
        {"id": request_id, "tenant_id": user["tenant_id"]},
        {"$set": {"status": "confirmed", "appointment_id": appointment["id"],
                  "updated_at": _now_iso()}},
    )
    return {"ok": True, "status": "confirmed", "appointment_id": appointment["id"]}


@router.post("/requests/{request_id}/decline")
async def decline_request(request_id: str,
                          user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    res = await db.triage_requests.update_one(
        {"id": request_id, "tenant_id": user["tenant_id"], "status": "pending"},
        {"$set": {"status": "declined", "updated_at": _now_iso()}},
    )
    if res.matched_count == 0:
        existing = await db.triage_requests.find_one(
            {"id": request_id, "tenant_id": user["tenant_id"]}, {"_id": 0})
        if not existing:
            raise HTTPException(404, "Request not found")
        raise HTTPException(400, f"Request is already {existing.get('status')}")
    return {"ok": True, "status": "declined"}
