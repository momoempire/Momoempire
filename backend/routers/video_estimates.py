"""Async video estimates — homeowner uploads a walkthrough video, shop reviews it.

Flow:
    POST /tenants/requests        → shop creates a request (customer, job
                                    type, notes); returns a public upload
                                    token link the shop texts/emails to the
                                    homeowner
    POST /upload/{token}          → homeowner uploads the video via the token
                                    (NO auth — token is the capability); the
                                    request flips to `uploaded`
    POST /tenants/requests/{id}/review → tech adds timestamped notes; when
                                    ready, converts to an estimate through
                                    the EXISTING quote pipeline
                                    (routers/call_to_payment.
                                    _create_estimate_doc, same helper the
                                    photo estimates use). Nothing here
                                    duplicates quote creation.
    GET  /tenants/requests/{id}   → detail with the notes timeline
    GET  /tenants/requests/{id}/video → stream the stored video (auth)

Video bytes live on disk through the pluggable storage stub in
`services/video_storage.py` (local-dir today, S3/R2 later). They are never
embedded in JSON docs — the doc only carries metadata + a storage key.

Statuses: requested → uploaded → in-review → estimated

Collections (all tenant-scoped):
- `video_estimate_requests`
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from routers.call_to_payment import _create_estimate_doc
from security import require_tenant_user
from services.video_storage import load_video_bytes, save_video_bytes

router = APIRouter(prefix="/api/video-estimates", tags=["video-estimates"])

MAX_VIDEO_BYTES = 100 * 1024 * 1024  # 100MB cap per walkthrough video
TOKEN_TTL_DAYS = int(os.getenv("VIDEO_ESTIMATES_TOKEN_TTL_DAYS", "7"))

STATUSES = ("requested", "uploaded", "in-review", "estimated")

# Frontend route the homeowner hits with their token (coordinator wires a
# public route later; the dashboard page surfaces this link with a copy
# button in the meantime).
UPLOAD_PAGE_PATH = "/video-upload"


# ---------- models ----------

class CreateRequestIn(BaseModel):
    customer_name: str = Field(min_length=1)
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
    job_type: str = Field(default="", description="e.g. HVAC repair, roof leak")
    notes: str = ""


class TimestampedNoteIn(BaseModel):
    """One tech note pinned to a moment in the video."""
    timestamp_sec: float = Field(ge=0, le=24 * 3600,
                                 description="Seconds into the video")
    note: str = Field(min_length=1)


class ReviewLineIn(BaseModel):
    description: str = Field(min_length=1)
    qty: float = Field(default=1.0, gt=0)
    unit_price: float = Field(default=0.0, ge=0)
    pricebook_item_id: Optional[str] = None


class ReviewIn(BaseModel):
    """Review step. Timestamped notes flip the request to `in-review`;
    including `lines` also converts it into an estimate (status
    `estimated`, linked `quote_id`)."""
    timestamped_notes: List[TimestampedNoteIn] = Field(default_factory=list)
    lines: List[ReviewLineIn] = Field(default_factory=list)
    title: str = "Video estimate"
    expires_in_days: int = 14


# ---------- helpers ----------

def _upload_link(token: str) -> str:
    return f"{UPLOAD_PAGE_PATH}/{token}"


def _token_expires_at() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=max(1, TOKEN_TTL_DAYS))).isoformat()


def _token_expired(doc: dict) -> bool:
    try:
        return datetime.now(timezone.utc) > datetime.fromisoformat(doc["expires_at"])
    except Exception:
        return True  # fail closed on a malformed expiry


def _public_request(doc: dict) -> dict:
    """Everything except storage internals (never sent to the client)."""
    return {
        "id": doc.get("id"),
        "customer_name": doc.get("customer_name"),
        "customer_email": doc.get("customer_email"),
        "customer_phone": doc.get("customer_phone"),
        "job_type": doc.get("job_type"),
        "notes": doc.get("notes", ""),
        "status": doc.get("status"),
        "upload_token": doc.get("upload_token"),
        "upload_link": doc.get("upload_link"),
        "expires_at": doc.get("expires_at"),
        "video": doc.get("video"),
        "review_notes": doc.get("review_notes") or [],
        "quote_id": doc.get("quote_id"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


async def _get_request(db, request_id: str, tenant_id: str) -> dict:
    doc = await db.video_estimate_requests.find_one(
        {"id": request_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(404, "Video estimate request not found")
    return doc


# ---------- endpoints ----------

@router.post("/tenants/requests")
async def create_request(
    data: CreateRequestIn,
    user: dict = Depends(require_tenant_user),
):
    """Create a video-estimate request and hand back the shareable upload
    link the shop sends to the homeowner."""
    db = get_db()
    token = _uuid()
    doc = {
        "id": _uuid(),
        "tenant_id": user["tenant_id"],
        "customer_name": data.customer_name.strip(),
        "customer_email": data.customer_email,
        "customer_phone": data.customer_phone,
        "job_type": data.job_type.strip(),
        "notes": data.notes or "",
        "status": "requested",
        "upload_token": token,
        "upload_link": _upload_link(token),
        "expires_at": _token_expires_at(),
        "video": None,
        "review_notes": [],
        "quote_id": None,
        "created_by": user.get("id"),
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    await db.video_estimate_requests.insert_one(doc)
    return _public_request(doc)


@router.get("/tenants/requests")
async def list_requests(
    status: Optional[str] = None,
    user: dict = Depends(require_tenant_user),
):
    """List requests with their pipeline status. Optional ?status= filter."""
    db = get_db()
    query = {"tenant_id": user["tenant_id"]}
    if status is not None:
        if status not in STATUSES:
            raise HTTPException(400, f"Unknown status '{status}'")
        query["status"] = status
    docs = await db.video_estimate_requests.find(
        query, {"_id": 0}
    ).sort("created_at", -1).to_list(500)
    return {"requests": [_public_request(d) for d in docs]}


@router.get("/tenants/requests/{request_id}")
async def get_request_detail(
    request_id: str, user: dict = Depends(require_tenant_user)
):
    """Request detail, with the timestamped review notes as a timeline."""
    db = get_db()
    doc = await _get_request(db, request_id, user["tenant_id"])
    out = _public_request(doc)
    out["review_notes"] = sorted(
        out["review_notes"], key=lambda n: n.get("timestamp_sec", 0)
    )
    return out


@router.post("/upload/{token}")
async def upload_video(
    token: str,
    file: UploadFile = File(...),
    duration_seconds: Optional[float] = None,
):
    """Homeowner upload via the token link. No auth — the token is the
    capability. Validates MIME + size, stores bytes through the pluggable
    storage stub, flips the request to `uploaded`."""
    db = get_db()
    doc = await db.video_estimate_requests.find_one({"upload_token": token})
    if not doc:
        raise HTTPException(404, "Upload link not found")
    if _token_expired(doc):
        raise HTTPException(410, "This upload link has expired")
    if doc.get("status") != "requested":
        raise HTTPException(409, f"Video already submitted (status: {doc.get('status')})")

    content_type = (file.content_type or "").lower()
    if not content_type.startswith("video/"):
        raise HTTPException(400, f"Expected a video file, got '{file.content_type}'")
    video_bytes = await file.read()
    if not video_bytes:
        raise HTTPException(400, "Empty video")
    if len(video_bytes) > MAX_VIDEO_BYTES:
        raise HTTPException(400, "Max 100MB per video")

    storage_key = save_video_bytes(
        tenant_id=doc["tenant_id"],
        request_id=doc["id"],
        filename=file.filename or "walkthrough",
        content_type=content_type,
        data=video_bytes,
    )

    video_meta = {
        "filename": file.filename,
        "content_type": content_type,
        "size_bytes": len(video_bytes),
        "storage_key": storage_key,
        "duration_seconds": duration_seconds,
        "uploaded_at": _now_iso(),
    }
    await db.video_estimate_requests.update_one(
        {"id": doc["id"], "tenant_id": doc["tenant_id"]},
        {"$set": {
            "status": "uploaded",
            "video": video_meta,
            "updated_at": _now_iso(),
        }},
    )
    doc["status"] = "uploaded"
    doc["video"] = video_meta
    return _public_request(doc)


@router.post("/tenants/requests/{request_id}/review")
async def review_request(
    request_id: str,
    data: ReviewIn,
    user: dict = Depends(require_tenant_user),
):
    """Tech review: append timestamped notes (status → `in-review`) and
    optionally convert to an estimate (status → `estimated`, `quote_id`
    linked through the existing quote pipeline)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    doc = await _get_request(db, request_id, tenant_id)
    if doc.get("status") == "estimated":
        raise HTTPException(409, "Already converted — see quote_id")
    if doc.get("status") == "requested":
        raise HTTPException(409, "No video uploaded yet")

    notes = list(doc.get("review_notes") or [])
    for n in data.timestamped_notes:
        notes.append({
            "timestamp_sec": n.timestamp_sec,
            "note": n.note,
            "author_id": user.get("id"),
            "created_at": _now_iso(),
        })

    patch: dict = {"review_notes": notes, "updated_at": _now_iso()}

    quote_id = None
    if data.lines:
        pb_ids = [l.pricebook_item_id for l in data.lines if l.pricebook_item_id]
        catalog: dict = {}
        if pb_ids:
            rows = await db.pricebook_items.find(
                {"tenant_id": tenant_id, "id": {"$in": pb_ids}, "active": True}, {"_id": 0}
            ).to_list(500)
            catalog = {r["id"]: r for r in rows}

        lines: List[dict] = []
        for line in data.lines:
            if line.pricebook_item_id:
                item = catalog.get(line.pricebook_item_id)
                if not item:
                    raise HTTPException(
                        404, f"Pricebook item not found or inactive: {line.pricebook_item_id}")
                lines.append({
                    "description": line.description or item.get("name", ""),
                    "quantity": float(line.qty),
                    "unit_price": round(float(item.get("price") or 0), 2),
                    "service_id": None,
                    "pricebook_item_id": item["id"],
                    "pricebook_version": int(item.get("version") or 1),
                })
            else:
                lines.append({
                    "description": line.description,
                    "quantity": float(line.qty),
                    "unit_price": round(float(line.unit_price), 2),
                    "service_id": None,
                })

        quote = await _create_estimate_doc(
            db,
            tenant_id,
            lead_id=None,
            customer_name=doc.get("customer_name"),
            customer_email=doc.get("customer_email"),
            customer_phone=doc.get("customer_phone") or "",
            title=data.title,
            lines=lines,
            notes=f" [video-estimate {request_id}]",
            expires_in_days=data.expires_in_days,
        )
        quote_id = quote["id"]
        patch["quote_id"] = quote_id
        patch["status"] = "estimated"
    else:
        patch["status"] = "in-review"

    await db.video_estimate_requests.update_one(
        {"id": request_id, "tenant_id": tenant_id}, {"$set": patch}
    )
    return {"id": request_id, "status": patch["status"],
            "quote_id": quote_id, "review_notes": len(notes)}


@router.get("/tenants/requests/{request_id}/video")
async def stream_video(
    request_id: str, user: dict = Depends(require_tenant_user)
):
    """Stream the stored walkthrough video to the shop (auth + tenant-scoped)."""
    db = get_db()
    doc = await _get_request(db, request_id, user["tenant_id"])
    video = doc.get("video") or {}
    storage_key = video.get("storage_key")
    if not storage_key:
        raise HTTPException(404, "No video uploaded yet")
    payload = load_video_bytes(storage_key)
    if payload is None:
        raise HTTPException(404, "Video file not found in storage")

    data, content_type = payload
    return StreamingResponse(
        iter([data]),
        media_type=content_type or "video/mp4",
        headers={"Content-Disposition": f'inline; filename="{video.get("filename") or "video"}"'},
    )
