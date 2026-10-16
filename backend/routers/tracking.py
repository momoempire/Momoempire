"""GPS tech tracking: live technician locations, customer ETA view, SMS notifications.

Collections:
- tech_locations: one doc per tech (tenant-scoped): current position plus a capped
  history of the last 50 points.
- appointments: extended with `eta_token` (unguessable per-job share token, created
  when a job goes en_route or via the share endpoint) and optional `service_lat` /
  `service_lng` job coordinates used for v1 ETA math.

V1 ETA = haversine straight-line distance at ~30mph. Real road routing with
traffic is a fast-follow; the endpoint degrades gracefully to status-only text
("On the way") when coordinates are missing instead of failing.
"""
from __future__ import annotations

import logging
import math
import os
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from public_links import eta_link
from security import require_tenant_user
from services.twilio import send_sms

logger = logging.getLogger("tracking")

router = APIRouter(prefix="/tenants/tracking", tags=["tracking"])
public_router = APIRouter(prefix="/public", tags=["tracking-public"])

_HISTORY_CAP = 50  # points of short history kept per tech
_AVG_MPH = 30.0    # v1 ETA assumption (straight line, city driving)


# ---------- ETA math ----------

def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles between two lat/lng points."""
    r = 3958.8  # earth radius in miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def eta_text_for(tech_lat: Optional[float], tech_lng: Optional[float],
                 job_lat: Optional[float], job_lng: Optional[float],
                 status: str) -> str:
    """v1 ETA text. Missing coordinates -> status-only text, never an error."""
    if tech_lat is not None and tech_lng is not None and job_lat is not None and job_lng is not None:
        miles = haversine_miles(tech_lat, tech_lng, job_lat, job_lng)
        minutes = miles / _AVG_MPH * 60
        if minutes < 2:
            return "Arriving now"
        return f"About {int(round(minutes))} min away"
    if status == "on_site":
        return "Tech has arrived"
    if status == "completed":
        return "Job completed"
    return "On the way"


# ---------- helpers ----------

async def _get_tech(db, tech_id: str, tenant_id: str) -> dict:
    tech = await db.schedule_staff.find_one({"id": tech_id, "tenant_id": tenant_id}, {"_id": 0})
    if not tech:
        raise HTTPException(404, "Technician not found")
    return tech


async def _get_job(db, job_id: str, tenant_id: str) -> dict:
    job = await db.appointments.find_one({"id": job_id, "tenant_id": tenant_id}, {"_id": 0})
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def _check_post_auth(user: dict, tech_id: str) -> None:
    """Owner/admin can post for any tech in their tenant; a tech can only post
    their own location (linked via the user's staff_id)."""
    if user.get("role") in {"owner", "admin"}:
        return
    if user.get("staff_id") and user["staff_id"] == tech_id:
        return
    raise HTTPException(403, "You can only post your own location")


async def ensure_eta_token(db, tenant_id: str, job: dict) -> str:
    """Return the job's ETA share token, creating (unguessable) one if needed."""
    token = job.get("eta_token")
    if token:
        return token
    token = secrets.token_urlsafe(32)
    await db.appointments.update_one(
        {"id": job["id"], "tenant_id": tenant_id},
        {"$set": {"eta_token": token, "updated_at": _now_iso()}},
    )
    return token


async def maybe_send_eta_sms(db, tenant_id: str, job: dict) -> None:
    """Best-effort customer ETA SMS when a job goes en_route. Never raises —
    a failed notification must not break the dispatch status update."""
    try:
        phone = (job.get("customer_phone") or "").strip()
        if not phone:
            return
        # Guard: only send when this tenant actually has SMS configured.
        row = await db.integrations.find_one(
            {"tenant_id": tenant_id, "key": "twilio"}, {"_id": 0, "config": 1})
        cfg = (row or {}).get("config") or {}
        if not (cfg.get("account_sid") and cfg.get("auth_token")):
            return
        token = await ensure_eta_token(db, tenant_id, job)
        link = eta_link(token)
        tenant = await db.tenants.find_one({"id": tenant_id}, {"_id": 0, "name": 1})
        biz = (tenant or {}).get("name") or "your service provider"
        first = (job.get("staff") or "").split()
        tech_first = first[0] if first else "your technician"
        body = f"Hi from {biz}: {tech_first} is on the way! Track live arrival: {link}"
        await send_sms(tenant_id=tenant_id, to=phone, body=body)
    except Exception as e:  # noqa: BLE001 - notification path must never break dispatch
        logger.exception("ETA SMS failed (dispatch update unaffected): %s", e)


# ---------- request models ----------

class LocationPost(BaseModel):
    tech_id: str
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    job_id: Optional[str] = None
    timestamp: Optional[str] = None  # ISO; server time used when omitted


class ShareIn(BaseModel):
    lat: Optional[float] = Field(default=None, ge=-90, le=90)   # job site coords (optional)
    lng: Optional[float] = Field(default=None, ge=-180, le=180)


# ---------- tenant endpoints ----------

@router.post("/location")
async def post_location(data: LocationPost, user: dict = Depends(require_tenant_user)):
    """Tech (or owner/admin) posts a live position. Upserts the current doc and
    appends to the capped short history."""
    db = get_db()
    tenant_id = user["tenant_id"]
    _check_post_auth(user, data.tech_id)
    tech = await _get_tech(db, data.tech_id, tenant_id)
    job_id = data.job_id or ""
    if job_id:
        await _get_job(db, job_id, tenant_id)  # tenant-scoped; 404 if not ours

    now = _now_iso()
    point = {"lat": data.lat, "lng": data.lng, "ts": data.timestamp or now, "job_id": job_id}
    current = await db.tech_locations.find_one(
        {"tech_id": data.tech_id, "tenant_id": tenant_id}, {"_id": 0})
    history = (current or {}).get("history") or []
    history.append(point)
    history = history[-_HISTORY_CAP:]
    doc = {
        "id": (current or {}).get("id") or _uuid(),
        "tenant_id": tenant_id,
        "tech_id": data.tech_id,
        "lat": data.lat,
        "lng": data.lng,
        "job_id": job_id,
        "updated_at": now,
        "history": history,
    }
    if current:
        await db.tech_locations.update_one(
            {"id": current["id"], "tenant_id": tenant_id}, {"$set": doc})
    else:
        await db.tech_locations.insert_one(doc)
    return {"tech_id": data.tech_id, "name": tech.get("name", ""), "lat": data.lat,
            "lng": data.lng, "updated_at": now, "current_job_id": job_id or None}


@router.get("/live")
async def live_positions(user: dict = Depends(require_tenant_user)):
    """Current positions of all techs for the tenant (dispatch-board feed)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    techs = await db.schedule_staff.find({"tenant_id": tenant_id}, {"_id": 0}).sort("name", 1).to_list(500)
    locs = await db.tech_locations.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(500)
    by_tech = {l["tech_id"]: l for l in locs}
    out = []
    for t in techs:
        loc = by_tech.get(t["id"])
        out.append({
            "tech_id": t["id"],
            "name": t.get("name", ""),
            "lat": (loc or {}).get("lat"),
            "lng": (loc or {}).get("lng"),
            "updated_at": (loc or {}).get("updated_at"),
            "current_job_id": ((loc or {}).get("job_id") or None),
        })
    return {"techs": out}


@router.post("/jobs/{job_id}/share")
async def share_eta(job_id: str, data: ShareIn, user: dict = Depends(require_tenant_user)):
    """Create (or refresh) the customer ETA share link for a job. Optionally
    attach job-site coordinates for ETA math."""
    db = get_db()
    tenant_id = user["tenant_id"]
    job = await _get_job(db, job_id, tenant_id)
    update: dict = {"eta_token": secrets.token_urlsafe(32), "updated_at": _now_iso()}
    if data.lat is not None and data.lng is not None:
        update["service_lat"] = data.lat
        update["service_lng"] = data.lng
    await db.appointments.update_one(
        {"id": job["id"], "tenant_id": tenant_id}, {"$set": update})
    token = update["eta_token"]
    return {"job_id": job["id"], "eta_token": token, "eta_url": eta_link(token)}


@router.delete("/jobs/{job_id}/share")
async def revoke_eta(job_id: str, user: dict = Depends(require_tenant_user)):
    """Revoke the customer ETA share link for a job."""
    db = get_db()
    tenant_id = user["tenant_id"]
    job = await _get_job(db, job_id, tenant_id)
    if not job.get("eta_token"):
        raise HTTPException(404, "No ETA share link for this job")
    await db.appointments.update_one(
        {"id": job["id"], "tenant_id": tenant_id},
        {"$unset": {"eta_token": ""}, "$set": {"updated_at": _now_iso()}})
    return {"job_id": job["id"], "revoked": True}


# ---------- public customer ETA view ----------

@public_router.get("/eta/{token}")
async def public_eta(token: str):
    """Public ETA page data. Returns ONLY: tech first name, live lat/lng,
    ETA text, job status. No customer data, no addresses, no other jobs."""
    db = get_db()
    if not token:
        raise HTTPException(404, "This tracking link is invalid or has expired.")
    job = await db.appointments.find_one({"eta_token": token}, {"_id": 0})
    if not job or not job.get("eta_token"):
        raise HTTPException(404, "This tracking link is invalid or has expired.")

    tenant_id = job.get("tenant_id")
    tech_id = job.get("staff_id") or ""
    first = (job.get("staff") or "").split()
    tech_first = first[0] if first else "your technician"
    loc = None
    if tech_id and tenant_id:
        loc = await db.tech_locations.find_one(
            {"tech_id": tech_id, "tenant_id": tenant_id}, {"_id": 0})

    eta_text = eta_text_for(
        (loc or {}).get("lat"), (loc or {}).get("lng"),
        job.get("service_lat"), job.get("service_lng"),
        job.get("status", "en_route"))
    return {
        "tech_first_name": tech_first,
        "lat": (loc or {}).get("lat"),
        "lng": (loc or {}).get("lng"),
        "location_updated_at": (loc or {}).get("updated_at"),
        "eta_text": eta_text,
        "status": job.get("status", "en_route"),
    }
