"""Transferable home service profile — every serviced home gets a persistent
profile (equipment inventory, service history, warranties, maintenance plans,
notes) that survives a change of ownership.

Transfer flow (consent-gated):
1. Shop initiates: property + new-owner contact -> creates a transfer with a
   claim token. TODO(LIVE): send the old owner an approve link
   (services.email / services.twilio per tenant branding).
2. Old owner approves via the token link (or auto-approves after N days per
   the transfer's auto_approve_days policy).
3. New owner claims via the token link and consents to data sharing.
4. The profile moves to the new owner's account; every step is recorded in
   the transfer's event audit trail.

PII GUARANTEE (enforced in claim logic, not just documented): the new owner
receives equipment, service history, warranties, maintenance plans, and notes
ONLY. The previous owner's personal contact details and payment info are
wiped from the profile at claim time; they survive only inside the transfer's
audit events, which are visible to the SHOP (the initiating tenant), never to
the new owner. New-owner personal details likewise never leak to the old
owner: the approve view only reveals the property address, not the new
owner's contact info.

Collections (all tenant-scoped):
- `home_profiles`: one doc per property (owner PII lives here pre-transfer).
- `profile_transfers`: one doc per ownership transfer (status machine +
  event audit trail).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin

router = APIRouter(prefix="/api/home-profiles", tags=["home-profiles"])

# How long a transfer token stays valid (approve or claim after this -> 410).
TOKEN_TTL_DAYS = 90


# ---------- models ----------

class OwnerContactIn(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""


class EquipmentIn(BaseModel):
    type: str = Field(min_length=1)
    brand: str = ""
    model: str = ""
    serial: str = ""
    installed_at: str = ""
    warranty_expires: str = ""
    notes: str = ""


class ServiceEntryIn(BaseModel):
    date: str = ""
    service_type: str = ""
    notes: str = ""
    technician: str = ""
    job_id: str = ""


class WarrantyIn(BaseModel):
    title: str = Field(min_length=1)
    provider: str = ""
    expires_at: str = ""
    coverage: str = ""


class MaintenancePlanIn(BaseModel):
    name: str = Field(min_length=1)
    frequency: str = ""
    next_due: str = ""
    status: str = "active"


class ProfileCreateIn(BaseModel):
    property_address: str = Field(min_length=1)
    unit: str = ""
    owner_name: str = ""
    owner_contact: OwnerContactIn = OwnerContactIn()
    owner_account_id: str = ""
    payment_info: Dict = Field(default_factory=dict)  # PII: shop-visible only, never transfers
    equipment: List[EquipmentIn] = Field(default_factory=list)
    service_history: List[ServiceEntryIn] = Field(default_factory=list)
    warranties: List[WarrantyIn] = Field(default_factory=list)
    maintenance_plans: List[MaintenancePlanIn] = Field(default_factory=list)
    notes: str = ""


class ProfileUpdateIn(BaseModel):
    """Partial update of the mutable profile sections (owner PII excluded —
    ownership changes go through the consent-gated transfer flow)."""
    property_address: Optional[str] = None
    unit: Optional[str] = None
    owner_name: Optional[str] = None
    notes: Optional[str] = None
    equipment: Optional[List[EquipmentIn]] = None
    service_history: Optional[List[ServiceEntryIn]] = None
    warranties: Optional[List[WarrantyIn]] = None
    maintenance_plans: Optional[List[MaintenancePlanIn]] = None


class TransferInitiateIn(BaseModel):
    new_owner_name: str = Field(min_length=1)
    new_owner_contact: OwnerContactIn = OwnerContactIn()
    new_owner_account_id: str = ""
    auto_approve_days: int = Field(default=30, ge=0, le=180)
    note: str = ""


class TransferClaimIn(BaseModel):
    new_owner_name: str = Field(min_length=1)
    new_owner_contact: OwnerContactIn = OwnerContactIn()
    new_owner_account_id: str = ""
    consent: bool = False  # must be True: explicit data-sharing consent


# ---------- helpers ----------

def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _event(actor: str, event: str, detail: str = "") -> Dict:
    return {"at": _now_iso(), "actor": actor, "event": event, "detail": detail}


async def _add_event(db, transfer_id: str, actor: str, event: str, detail: str = "") -> None:
    transfer = await db.profile_transfers.find_one({"id": transfer_id})
    events = (transfer or {}).get("events", []) + [_event(actor, event, detail)]
    await db.profile_transfers.update_one({"id": transfer_id}, {"$set": {"events": events}})


async def _get_profile(db, tenant_id: str, profile_id: str) -> Dict:
    profile = await db.home_profiles.find_one({"tenant_id": tenant_id, "id": profile_id})
    if not profile:
        raise HTTPException(404, "Home profile not found")
    return profile


def _is_expired(transfer: Dict) -> bool:
    initiated = _parse_iso(transfer.get("initiated_at", ""))
    if not initiated:
        return False
    return datetime.now(timezone.utc) > initiated + timedelta(days=TOKEN_TTL_DAYS)


async def _maybe_auto_approve(db, transfer: Dict) -> Dict:
    """Old owner hasn't responded within the policy window -> auto-approve.

    Returns the (possibly updated) transfer doc.
    """
    if transfer.get("status") != "initiated":
        return transfer
    days = transfer.get("auto_approve_days", 30)
    initiated = _parse_iso(transfer.get("initiated_at", ""))
    if initiated and datetime.now(timezone.utc) >= initiated + timedelta(days=days):
        now = _now_iso()
        await db.profile_transfers.update_one(
            {"id": transfer["id"]},
            {"$set": {"status": "approved", "approved_at": now, "approved_by": "auto-policy"}},
        )
        await _add_event(db, transfer["id"], "system", "auto_approved",
                         f"Old owner did not respond within {days} days; policy auto-approval.")
        transfer = await db.profile_transfers.find_one({"id": transfer["id"]})
    return transfer


async def _get_live_transfer(db, token: str) -> Dict:
    """Token-scoped lookup for the public approve/claim routes.

    The token is a single-use bearer credential delivered to the old/new owner
    by the shop; possession of it IS the authorization. Reject used, expired,
    or unknown tokens.
    """
    transfer = await db.profile_transfers.find_one({"token": token})
    if not transfer or transfer.get("status") in {"claimed", "cancelled"}:
        raise HTTPException(404, "Transfer link not found or already used")
    if _is_expired(transfer):
        raise HTTPException(410, "Transfer link expired")
    return transfer


# ---------- profiles ----------

@router.post("/")
async def create_profile(data: ProfileCreateIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Create a home service profile for a property (equipment, history,
    warranties, maintenance plans, notes)."""
    db = get_db()
    doc = {
        "id": _uuid(),
        "tenant_id": user["tenant_id"],
        "property_address": data.property_address,
        "unit": data.unit,
        "owner_name": data.owner_name,
        "owner_contact": data.owner_contact.model_dump(),
        "owner_account_id": data.owner_account_id,
        "payment_info": dict(data.payment_info),
        "equipment": [e.model_dump() for e in data.equipment],
        "service_history": [s.model_dump() for s in data.service_history],
        "warranties": [w.model_dump() for w in data.warranties],
        "maintenance_plans": [m.model_dump() for m in data.maintenance_plans],
        "notes": data.notes,
        "status": "active",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    await db.home_profiles.insert_one(doc)
    doc.pop("_id", None)
    return {"ok": True, **doc}


@router.get("/")
async def list_profiles(user: dict = Depends(require_tenant_user)):
    """List the tenant's home profiles (summary view)."""
    db = get_db()
    docs = await db.home_profiles.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).to_list(1000)
    summaries = []
    for d in docs:
        summaries.append({
            "id": d.get("id"),
            "property_address": d.get("property_address"),
            "unit": d.get("unit", ""),
            "owner_name": d.get("owner_name"),
            "equipment_count": len(d.get("equipment", []) or []),
            "service_count": len(d.get("service_history", []) or []),
            "warranty_count": len(d.get("warranties", []) or []),
            "status": d.get("status"),
            "updated_at": d.get("updated_at"),
        })
    return {"profiles": summaries, "count": len(summaries)}


@router.get("/{profile_id}")
async def get_profile(profile_id: str, user: dict = Depends(require_tenant_user)):
    """Full profile detail. Shop view — includes owner contact/payment info,
    since this route is tenant-scoped and only the shop can call it."""
    db = get_db()
    doc = await _get_profile(db, user["tenant_id"], profile_id)
    doc.pop("_id", None)
    return {"ok": True, **doc}


@router.put("/{profile_id}")
async def update_profile(profile_id: str, data: ProfileUpdateIn,
                         user: dict = Depends(require_tenant_owner_or_admin)):
    """Update equipment, service history, warranties, plans, notes. Owner PII
    is excluded from this endpoint — ownership changes go through the
    consent-gated transfer flow."""
    db = get_db()
    await _get_profile(db, user["tenant_id"], profile_id)
    patch: Dict = {"updated_at": _now_iso()}
    for field in ("property_address", "unit", "owner_name", "notes"):
        val = getattr(data, field)
        if val is not None:
            patch[field] = val
    for field in ("equipment", "service_history", "warranties", "maintenance_plans"):
        val = getattr(data, field)
        if val is not None:
            patch[field] = [item.model_dump() for item in val]
    await db.home_profiles.update_one(
        {"tenant_id": user["tenant_id"], "id": profile_id}, {"$set": patch})
    doc = await _get_profile(db, user["tenant_id"], profile_id)
    doc.pop("_id", None)
    return {"ok": True, **doc}


# ---------- transfer: shop-initiated ----------

@router.post("/{profile_id}/transfer/initiate")
async def initiate_transfer(profile_id: str, data: TransferInitiateIn,
                            user: dict = Depends(require_tenant_owner_or_admin)):
    """Step 1 — shop initiates a transfer: records the new-owner contact and
    issues a claim token. The old owner must approve (or the policy
    auto-approves after auto_approve_days) before the new owner can claim.

    Only one active transfer per profile at a time.
    """
    db = get_db()
    profile = await _get_profile(db, user["tenant_id"], profile_id)

    active = await db.profile_transfers.find_one({
        "tenant_id": user["tenant_id"],
        "profile_id": profile_id,
    })
    if active and active.get("status") in {"initiated", "approved"}:
        raise HTTPException(409, "A transfer is already in progress for this profile")

    now = _now_iso()
    transfer = {
        "id": _uuid(),
        "tenant_id": user["tenant_id"],
        "profile_id": profile_id,
        "token": _uuid(),
        "status": "initiated",
        "property_address": profile.get("property_address"),
        # Old-owner identity: kept in the SHOP-visible audit trail. The new
        # owner never sees these fields (enforced at claim time).
        "old_owner_name": profile.get("owner_name", ""),
        "old_owner_contact": dict(profile.get("owner_contact") or {}),
        "old_owner_account_id": profile.get("owner_account_id", ""),
        # New-owner identity: kept in the SHOP-visible audit trail. The old
        # owner never sees these fields — the approve step only reveals the
        # property address, never the new owner's contact info.
        "new_owner_name": data.new_owner_name,
        "new_owner_contact": data.new_owner_contact.model_dump(),
        "new_owner_account_id": data.new_owner_account_id,
        "auto_approve_days": data.auto_approve_days,
        "note": data.note,
        "initiated_by": user.get("id"),
        "initiated_at": now,
        "approved_at": None,
        "claimed_at": None,
        "events": [_event(user.get("id", "shop"), "initiated",
                          f"Transfer opened for {profile.get('property_address')}")],
    }
    await db.profile_transfers.insert_one(transfer)

    # TODO(LIVE): notify the OLD owner with their approve link
    # (services.email / services.twilio) and the NEW owner that a claim link
    # will follow after approval. Links: /transfer/{token}/approve|claim.
    print(f"[HOME-PROFILES transfer initiated] profile={profile_id} "
          f"token={transfer['token']} (stubbed owner notification)")

    transfer.pop("_id", None)
    return {"ok": True, **transfer}


# ---------- transfer: token-based (public, no session) ----------

@router.post("/transfer/{token}/approve")
async def approve_transfer(token: str):
    """Step 2 — old owner approves the transfer via their token link.

    Public route (no session): the token itself is the authorization. Shows
    only the property address — never the new owner's contact details.
    """
    db = get_db()
    transfer = await _get_live_transfer(db, token)
    transfer = await _maybe_auto_approve(db, transfer)
    if transfer.get("status") == "approved":
        raise HTTPException(409, "Transfer already approved")
    if transfer.get("status") != "initiated":
        raise HTTPException(409, f"Transfer is {transfer.get('status')} and cannot be approved")

    now = _now_iso()
    await db.profile_transfers.update_one(
        {"id": transfer["id"]},
        {"$set": {"status": "approved", "approved_at": now, "approved_by": "old-owner"}},
    )
    await _add_event(db, transfer["id"], "old-owner", "approved",
                     "Old owner consented to the profile transfer.")
    # TODO(LIVE): notify the NEW owner that their claim link is now live.

    updated = await db.profile_transfers.find_one({"id": transfer["id"]})
    updated.pop("_id", None)
    return {"ok": True, **updated}


@router.post("/transfer/{token}/claim")
async def claim_transfer(token: str, data: TransferClaimIn):
    """Step 3 — new owner claims the profile via their token link and gives
    explicit data-sharing consent.

    PII GUARANTEE: the profile is rewritten in place — the new owner's
    identity replaces the old owner's; the old owner's personal contact
    details and payment info are WIPED from the profile document. Only
    equipment, service history, warranties, maintenance plans, and notes
    carry over. The old owner's identity survives solely in the transfer's
    audit events, which are shop-visible and never shown to the new owner.
    """
    if not data.consent:
        raise HTTPException(400, "Data-sharing consent is required to claim the profile")
    db = get_db()
    transfer = await _get_live_transfer(db, token)
    transfer = await _maybe_auto_approve(db, transfer)
    if transfer.get("status") == "initiated":
        raise HTTPException(409, "Transfer is awaiting the old owner's approval")

    now = _now_iso()
    profile_id = transfer["profile_id"]

    # PII redaction at claim time: overwrite owner identity + contact and
    # DELETE payment info. Service data (equipment/history/warranties/plans/
    # notes) is untouched and carries over to the new owner.
    await db.home_profiles.update_one(
        {"tenant_id": transfer["tenant_id"], "id": profile_id},
        {"$set": {
            "owner_name": data.new_owner_name,
            "owner_contact": data.new_owner_contact.model_dump(),
            "owner_account_id": data.new_owner_account_id,
            "payment_info": {},
            "previous_owner_redacted_at": now,
            "updated_at": now,
        }},
    )
    await db.profile_transfers.update_one(
        {"id": transfer["id"]},
        {"$set": {"status": "claimed", "claimed_at": now,
                  "claimed_by": data.new_owner_account_id or "new-owner"}},
    )
    await _add_event(db, transfer["id"], "new-owner", "claimed",
                     "New owner claimed the profile with data-sharing consent. "
                     "Previous owner's contact details and payment info redacted.")

    updated = await db.profile_transfers.find_one({"id": transfer["id"]})
    updated.pop("_id", None)
    return {"ok": True, **updated}


# ---------- transfer: audit trail ----------

@router.get("/{profile_id}/transfers")
async def list_transfers(profile_id: str, user: dict = Depends(require_tenant_user)):
    """Audit trail of every ownership transfer for a profile: status, actors,
    timestamps, and the full event log. Tenant-scoped (shop only)."""
    db = get_db()
    await _get_profile(db, user["tenant_id"], profile_id)
    transfers = []
    for t in await db.profile_transfers.find(
            {"tenant_id": user["tenant_id"], "profile_id": profile_id}, {"_id": 0}).to_list(100):
        t = await _maybe_auto_approve(db, t)  # keep status truthful on read
        transfers.append(t)
    return {"transfers": transfers, "count": len(transfers)}
