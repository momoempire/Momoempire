"""Homeowner Home Hub: a persistent, Carfax-like home service profile per homeowner.

Every homeowner gets a shareable profile link showing their home's service
history, equipment, upcoming appointments, and invoices/receipts. The shop
manages home profiles (address, equipment, maintenance reminders); the
homeowner views them through a long-lived token, and can request service
from the same page.

Collections (all tenant-scoped):
- `home_profiles`: the home record — homeowner contact, service address,
  embedded equipment list, maintenance reminders, notes.
- `home_hub_tokens`: homeowner access tokens (one or more per home),
  1-year expiry, revocable.
- Reused: `appointments`, `invoices`, `equipment`, `equipment_service_events`,
  `leads` (service requests become leads).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user
from timeutil import is_expired

router = APIRouter(prefix="/api/home-hub", tags=["home-hub"])

TOKEN_TTL_DAYS = 365


# ---------- models ----------

class ServiceAddress(BaseModel):
    street: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""


class EquipmentItem(BaseModel):
    equipment_type: str = ""
    location_label: str = ""  # e.g. "Attic", "Garage"
    make: str = ""
    model: str = ""
    serial_number: str = ""
    install_date: Optional[str] = None  # ISO date
    notes: str = ""


class ReminderIn(BaseModel):
    title: str
    due_date: str  # ISO date
    recurring: str = "never"  # never | monthly | quarterly | yearly
    notes: str = ""


class HomeProfileIn(BaseModel):
    homeowner_name: str = Field(min_length=1)
    email: str = ""
    phone: str = ""
    customer_id: str = ""
    address: ServiceAddress = Field(default_factory=ServiceAddress)
    property_type: str = ""
    year_built: Optional[int] = None
    sqft: Optional[int] = None
    equipment: List[EquipmentItem] = []
    reminders: List[ReminderIn] = []
    notes: str = ""


class HomeProfileUpdate(BaseModel):
    homeowner_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    customer_id: Optional[str] = None
    address: Optional[ServiceAddress] = None
    property_type: Optional[str] = None
    year_built: Optional[int] = None
    sqft: Optional[int] = None
    equipment: Optional[List[EquipmentItem]] = None
    reminders: Optional[List[ReminderIn]] = None
    notes: Optional[str] = None


class HomeServiceRequest(BaseModel):
    description: str
    service_type: str = ""
    preferred_date: str = ""
    preferred_time: str = ""
    contact_name: str = ""
    contact_phone: str = ""
    contact_email: str = ""


# ---------- helpers ----------

def _address_summary(addr: dict) -> str:
    parts = [addr.get("street"), addr.get("city"), addr.get("state"), addr.get("zip")]
    return ", ".join(p for p in parts if p)


def _issue_token(db, tenant_id: str, home_id: str) -> dict:
    token = f"hh_{secrets.token_urlsafe(32)}"
    rec = {
        "id": _uuid(),
        "token": token,
        "tenant_id": tenant_id,
        "home_id": home_id,
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS)).isoformat(),
        "created_at": _now_iso(),
        "revoked": False,
    }
    return rec, token


async def _token_to_home(db, homeowner_token: str) -> dict:
    """Resolve a public homeowner token to its home profile. 401 on any failure."""
    rec = await db.home_hub_tokens.find_one({"token": homeowner_token, "revoked": {"$ne": True}})
    if not rec:
        raise HTTPException(401, "Invalid token")
    if is_expired(rec.get("expires_at")):
        raise HTTPException(401, "Token expired")
    home = await db.home_profiles.find_one({"id": rec["home_id"], "tenant_id": rec["tenant_id"]}, {"_id": 0})
    if not home:
        raise HTTPException(404, "Home profile not found")
    return home


async def _timeline_for_home(db, home: dict) -> list:
    """Merged, newest-first service timeline for a home profile.

    Sources: appointments, invoices, equipment service events linked either
    to the profile's customer_id or its phone number (same matching the
    customer portal uses).
    """
    tenant_id = home["tenant_id"]
    items = []
    phone = home.get("phone", "__none__")
    cust_id = home.get("customer_id") or "__none__"

    def _matches(d: dict) -> bool:
        return d.get("customer_id") == cust_id or d.get("customer_phone") == phone

    appts = await db.appointments.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(500)
    for a in appts:
        if not _matches(a):
            continue
        items.append({
            "date": a.get("start_at", a.get("created_at")),
            "type": "appointment",
            "title": a.get("service_name") or "Service visit",
            "detail": f"{a.get('status', '')} · tech: {a.get('staff', '')}".strip(" ·"),
            "ref_id": a.get("id"),
        })

    invs = await db.invoices.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(500)
    for i in invs:
        if not _matches(i):
            continue
        items.append({
            "date": i.get("paid_at") or i.get("created_at"),
            "type": "invoice",
            "title": i.get("title") or "Invoice",
            "detail": f"{i.get('status', '')} · ${i.get('total', 0):.2f}",
            "ref_id": i.get("id"),
        })

    # equipment service events for this customer's equipment records
    eq_docs = await db.equipment.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(500)
    eq_ids = {e.get("id") for e in eq_docs if _matches(e)}
    if eq_ids:
        events = await db.equipment_service_events.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(1000)
        for e in events:
            if e.get("equipment_id") not in eq_ids:
                continue
            items.append({
                "date": e.get("service_date") or e.get("created_at"),
                "type": "service_event",
                "title": e.get("description") or "Service performed",
                "detail": e.get("notes", ""),
                "ref_id": e.get("id"),
            })

    items.sort(key=lambda x: str(x["date"] or ""), reverse=True)
    return items


def _serialize_home(home: dict) -> dict:
    out = dict(home)
    out.pop("_id", None)
    addr = out.get("address") or {}
    out["address_summary"] = _address_summary(addr)
    return out


# ---------- public homeowner view ----------

@router.get("/profile/{homeowner_token}")
async def homeowner_profile(homeowner_token: str):
    """Public home hub: profile, equipment, reminders, timeline, upcoming visits."""
    db = get_db()
    home = await _token_to_home(db, homeowner_token)
    tenant = await db.tenants.find_one({"id": home["tenant_id"]}, {"_id": 0}) or {}

    timeline = await _timeline_for_home(db, home)
    now = _now_iso()
    upcoming = [t for t in timeline if t["type"] == "appointment" and str(t["date"] or "") >= now]
    history = [t for t in timeline if t not in upcoming]

    reminders = sorted((home.get("reminders") or []), key=lambda r: str(r.get("due_date") or ""))

    return {
        "profile": _serialize_home(home),
        "shop": {
            "name": tenant.get("name", ""),
            "phone": tenant.get("contact_phone", ""),
            "email": tenant.get("contact_email", ""),
        },
        "equipment": home.get("equipment", []),
        "reminders": reminders,
        "upcoming": upcoming,
        "history": history,
    }


# ---------- shop-side management ----------

@router.get("/tenants/homes")
async def list_homes(user: dict = Depends(require_tenant_user), search: str = "", limit: int = 100):
    db = get_db()
    q = {"tenant_id": user["tenant_id"]}
    homes = await db.home_profiles.find(q, {"_id": 0}).to_list(max(1, min(limit, 500)))
    if search:
        s = search.lower()
        homes = [h for h in homes if s in str(h.get("homeowner_name", "")).lower()
                 or s in str(h.get("phone", "")).lower()
                 or s in str((h.get("address") or {}).get("street", "")).lower()]
    return {"homes": [_serialize_home(h) for h in homes]}


@router.post("/tenants/homes")
async def upsert_home(data: HomeProfileIn, home_id: str = "", user: dict = Depends(require_tenant_user)):
    """Create a home profile, or update it when home_id is provided."""
    db = get_db()
    if home_id:
        home = await db.home_profiles.find_one({"id": home_id, "tenant_id": user["tenant_id"]})
        if not home:
            raise HTTPException(404, "Home profile not found")
        patch = data.model_dump()
        patch["updated_at"] = _now_iso()
        await db.home_profiles.update_one({"id": home_id}, {"$set": patch})
        updated = await db.home_profiles.find_one({"id": home_id}, {"_id": 0})
        return {"home": _serialize_home(updated), "updated": True}

    now = _now_iso()
    home = data.model_dump()
    home.update({
        "id": _uuid(),
        "tenant_id": user["tenant_id"],
        "created_at": now,
        "updated_at": now,
    })
    await db.home_profiles.insert_one(home)
    rec, token = _issue_token(db, user["tenant_id"], home["id"])
    await db.home_hub_tokens.insert_one(rec)
    return JSONResponse(
        status_code=201,
        content={"home": _serialize_home({k: v for k, v in home.items() if k != "_id"}),
                 "share_token": token},
    )


@router.get("/tenants/homes/{home_id}/history")
async def home_history(home_id: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    home = await db.home_profiles.find_one({"id": home_id, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not home:
        raise HTTPException(404, "Home profile not found")
    return {"home_id": home_id, "history": await _timeline_for_home(db, home)}


@router.post("/tenants/homes/{home_id}/request", status_code=201)
async def home_request_shop(home_id: str, data: HomeServiceRequest, user: dict = Depends(require_tenant_user)):
    """Shop-side: log a service request for a home (becomes a lead)."""
    return await _create_request(get_db(), home_id, user["tenant_id"], data)


@router.post("/request/{homeowner_token}", status_code=201)
async def home_request_public(homeowner_token: str, data: HomeServiceRequest):
    """Homeowner-side: request service straight from the home hub page."""
    db = get_db()
    home = await _token_to_home(db, homeowner_token)
    return await _create_request(db, home["id"], home["tenant_id"], data, home=home)


async def _create_request(db, home_id: str, tenant_id: str, data: HomeServiceRequest, home: dict = None):
    home = home or await db.home_profiles.find_one({"id": home_id, "tenant_id": tenant_id}, {"_id": 0})
    if not home:
        raise HTTPException(404, "Home profile not found")
    lead = {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "name": data.contact_name or home.get("homeowner_name", ""),
        "phone": data.contact_phone or home.get("phone", ""),
        "email": data.contact_email or home.get("email"),
        "source": "home-hub",
        "status": "new",
        "home_id": home_id,
        "notes": f"Service: {data.service_type}. Preferred: {data.preferred_date} {data.preferred_time}. Details: {data.description}",
        "created_at": _now_iso(),
    }
    await db.leads.insert_one(lead)
    return {"status": "ok", "lead_id": lead["id"]}


@router.post("/tenants/homes/{home_id}/token")
async def rotate_home_token(home_id: str, user: dict = Depends(require_tenant_user)):
    """Issue a fresh homeowner token (revokes existing ones)."""
    db = get_db()
    home = await db.home_profiles.find_one({"id": home_id, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not home:
        raise HTTPException(404, "Home profile not found")
    old = await db.home_hub_tokens.find({"home_id": home_id, "tenant_id": user["tenant_id"]}).to_list(100)
    for t in old:
        await db.home_hub_tokens.update_one({"id": t["id"]}, {"$set": {"revoked": True}})
    rec, token = _issue_token(db, user["tenant_id"], home_id)
    await db.home_hub_tokens.insert_one(rec)
    return {"share_token": token}
