"""AI home report after every visit — a beautiful homeowner-facing summary of a
completed job: what was done (job summary), before/after photos, equipment
health snapshot, technician notes, and template-based (no LLM required)
maintenance recommendations with priority levels.

The shop shares the report via a magic link: a long-lived token in the
report doc (same token pattern as home_hub's homeowner tokens). The homeowner
opens a clean public view with no login; every view is recorded for the
shop's open tracking.

Collections (all tenant-scoped):
- `home_reports`: the report doc, incl. `share_token` for the magic link.
- `home_report_events`: created / sent / viewed events (open tracking).
- reads: `appointments` (jobs ARE appointments), `photo_estimates` (before/
  after photo refs), `equipment` (health snapshot), `tenants` (business name).

Photo storage follows the photo-estimates pattern: photo docs hold the image
bytes, but the report (and the public view) only ever carries the public
shape — never the raw bytes. TODO(LIVE): serve photo bytes via signed URLs
from object storage so the public page can render real images.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin
from timeutil import is_expired

router = APIRouter(prefix="/api/home-reports", tags=["home-reports"])

TOKEN_TTL_DAYS = 365  # magic links stay shareable; revoke by regenerating


# ---------- models ----------

class GenerateIn(BaseModel):
    job_id: str = Field(min_length=1)
    before_photo_ids: List[str] = Field(default_factory=list)
    after_photo_ids: List[str] = Field(default_factory=list)
    technician_notes: str = ""


class SendIn(BaseModel):
    channel: str = Field(default="link", pattern="^(sms|email|link)$")


# ---------- template-based AI recommendations (no LLM required) ----------

# keyword groups -> recommendations; each rec: (priority, title, text, due_hint)
_RECOMMENDATION_RULES: List[Dict] = [
    {
        "keywords": {"hvac", "furnace", "ac ", "air conditioner", "heat pump", "filter", "coil", "refrigerant", "duct"},
        "recs": [
            ("medium", "Replace the air filter",
             "A clean filter keeps airflow strong, lowers energy use, and protects the blower motor.",
             "Every 1–3 months"),
            ("medium", "Schedule an annual tune-up",
             "Yearly maintenance catches small issues (capacitors, contactors, drainage) before they cause a breakdown.",
             "Once a year"),
        ],
    },
    {
        "keywords": {"furnace", "heat pump"},
        "recs": [
            ("high", "Safety check on combustion / electrical components",
             "Heating equipment should get a yearly safety inspection — heat exchangers, burners, and electrical connections.",
             "Before heating season"),
        ],
    },
    {
        "keywords": {"plumbing", "water heater", "drain", "faucet", "leak", "pipe", "toilet", "sump"},
        "recs": [
            ("medium", "Flush the water heater",
             "Sediment buildup shortens tank life and raises energy costs. A yearly flush keeps it running efficiently.",
             "Once a year"),
            ("low", "Check under-sink supply lines",
             "A quick look for corrosion or drips under sinks catches leaks early — water damage is far more expensive.",
             "Every 6 months"),
        ],
    },
    {
        "keywords": {"electrical", "panel", "breaker", "outlet", "wiring", "lighting", "ev charger", "generator"},
        "recs": [
            ("high", "Test smoke & CO detectors",
             "Press the test button monthly and replace batteries yearly — working detectors save lives.",
             "Monthly test"),
            ("low", "Label or map the breaker panel",
             "A clearly labeled panel makes future troubleshooting faster and safer.",
             "When convenient"),
        ],
    },
    {
        "keywords": {"roof", "shingle", "gutter", "flashing"},
        "recs": [
            ("medium", "Clean gutters & downspouts",
             "Clogged gutters push water under shingles and against the foundation.",
             "Spring and fall"),
            ("low", "Annual roof inspection",
             "A yearly look at shingles, flashing, and seals spots wear before it becomes a leak.",
             "Once a year"),
        ],
    },
    {
        "keywords": {"pest", "termite", "rodent", "mosquito"},
        "recs": [
            ("medium", "Keep up the treatment barrier",
             "Pest control works best as a routine — lapses let populations rebound quickly.",
             "Per your service plan"),
        ],
    },
    {
        "keywords": {"pool", "spa", "hot tub"},
        "recs": [
            ("medium", "Keep water chemistry balanced",
             "Test chlorine/pH regularly — balanced water protects equipment and swimmers.",
             "2–3x per week in season"),
        ],
    },
    {
        "keywords": {"garage door", "opener", "spring"},
        "recs": [
            ("medium", "Lubricate moving parts",
             "A yearly spray of the rollers, hinges, and springs keeps the door quiet and smooth.",
             "Once a year"),
        ],
    },
]

_GENERIC_RECS = [
    ("low", "Keep your service records",
     "Hold onto this report — a documented service history protects warranties and helps with future resale.",
     "Ongoing"),
    ("low", "Know what's under warranty",
     "Parts and labor from this visit may be warrantied. Check your invoice or ask us for the details.",
     "When convenient"),
]


def _build_recommendations(service_text: str) -> List[Dict]:
    """Deterministic template matching. Labeled as template-generated; the
    shop owner can edit before sending."""
    hay = f" {service_text.lower()} "
    recs: List[Dict] = []
    seen = set()
    for rule in _RECOMMENDATION_RULES:
        if any(k in hay for k in rule["keywords"]):
            for priority, title, text, due in rule["recs"]:
                if title not in seen:
                    seen.add(title)
                    recs.append({"priority": priority, "title": title, "text": text,
                                 "due": due, "generated_by": "template-recs-v1"})
    for priority, title, text, due in _GENERIC_RECS:
        if title not in seen:
            seen.add(title)
            recs.append({"priority": priority, "title": title, "text": text,
                         "due": due, "generated_by": "template-recs-v1"})
    return recs


# ---------- helpers ----------

def _public_photo(doc: dict) -> Dict:
    """Public shape of a photo_estimates doc — same idea as photo_estimates'
    _public_estimate: metadata only, never the raw image bytes."""
    return {
        "id": doc.get("id"),
        "filename": doc.get("filename"),
        "content_type": doc.get("content_type"),
        "size_bytes": doc.get("size_bytes"),
        "trade": doc.get("trade"),
        "notes": doc.get("notes", ""),
        "captured_at": doc.get("created_at"),
    }


def _equipment_health(equipment_type: str, install_date: Optional[str]) -> str:
    """Template-based health label from age. A label, not a diagnosis."""
    try:
        installed = datetime.fromisoformat(str(install_date).replace("Z", "+00:00"))
        if installed.tzinfo is None:
            installed = installed.replace(tzinfo=timezone.utc)
        age_years = (datetime.now(timezone.utc) - installed).days / 365.25
    except (ValueError, TypeError, AttributeError):
        return "unknown"
    if age_years > 12:
        return "aging — consider replacement planning"
    if age_years > 7:
        return "mid-life — keep up maintenance"
    return "healthy"


async def _equipment_snapshot(db, tenant_id: str, job: dict) -> List[Dict]:
    """Best-effort equipment snapshot for the job's customer (template labels)."""
    out: List[Dict] = []
    customer_id = job.get("customer_id") or ""
    customer_phone = job.get("customer_phone") or ""
    docs = await db.equipment.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(200)
    for d in docs:
        if customer_id and d.get("customer_id") == customer_id:
            pass
        elif customer_phone and d.get("customer_phone") == customer_phone:
            pass
        else:
            continue
        out.append({
            "equipment_type": d.get("equipment_type", ""),
            "make": d.get("make", ""),
            "model": d.get("model", ""),
            "serial_number": d.get("serial_number", ""),
            "install_date": d.get("install_date"),
            "health": _equipment_health(d.get("equipment_type", ""), d.get("install_date")),
        })
    return out


async def _report_tracking(db, report: dict) -> Dict:
    events = await db.home_report_events.find(
        {"tenant_id": report["tenant_id"], "report_id": report["id"]}, {"_id": 0}
    ).to_list(10000)
    views = [e for e in events if e.get("type") == "viewed"]
    sends = [e for e in events if e.get("type") == "sent"]
    first_view = min((e.get("created_at", "") for e in views), default=None)
    last_view = max((e.get("created_at", "") for e in views), default=None)
    return {
        "views": len(views),
        "first_viewed_at": first_view,
        "last_viewed_at": last_view,
        "sends": len(sends),
    }


async def _record_event(db, report: dict, event_type: str,
                        channel: Optional[str] = None, extra: Optional[Dict] = None):
    doc = {
        "id": _uuid(),
        "tenant_id": report["tenant_id"],
        "report_id": report["id"],
        "type": event_type,
        "channel": channel,
        "day": datetime.now(timezone.utc).date().isoformat(),  # for view dedupe
        "extra": extra or {},
        "created_at": _now_iso(),
    }
    await db.home_report_events.insert_one(doc)
    return doc


def _shop_view(report: dict, tracking: Dict) -> Dict:
    doc = dict(report)
    doc.pop("_id", None)
    doc["tracking"] = tracking
    return doc


async def _get_report(db, tenant_id: str, report_id: str) -> Dict:
    doc = await db.home_reports.find_one({"tenant_id": tenant_id, "id": report_id})
    if not doc:
        raise HTTPException(404, "Home report not found")
    return doc


# ---------- generate ----------

@router.post("/generate")
async def generate_report(data: GenerateIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Build a home report from a COMPLETED job (appointments collection).

    Dedupes on (tenant_id, job_id) — one report per job. Pulls the job summary,
    before/after photo refs (validated tenant-owned photo_estimates docs),
    an equipment health snapshot for the customer, and template-based
    maintenance recommendations. Issues the share token (magic link).
    """
    db = get_db()
    tenant_id = user["tenant_id"]

    existing = await db.home_reports.find_one({"tenant_id": tenant_id, "job_id": data.job_id})
    if existing:
        return {"ok": True, "duplicate": True, "id": existing.get("id"),
                "share_token": existing.get("share_token"),
                "note": "A report was already generated for this job."}

    job = await db.appointments.find_one({"tenant_id": tenant_id, "id": data.job_id})
    if not job:
        raise HTTPException(404, "Job not found")
    if job.get("status") != "completed":
        raise HTTPException(409, "Reports can only be generated for completed jobs")

    # Validate photo refs: tenant-owned, photo_estimates docs; never copy bytes.
    async def _resolve_photo_ids(ids: List[str]) -> List[Dict]:
        out = []
        for pid in ids:
            p = await db.photo_estimates.find_one({"tenant_id": tenant_id, "id": pid})
            if not p:
                raise HTTPException(404, f"Photo not found: {pid}")
            out.append(_public_photo(p))
        return out

    before_photos = await _resolve_photo_ids(data.before_photo_ids)
    after_photos = await _resolve_photo_ids(data.after_photo_ids)

    tenant = await db.tenants.find_one({"id": tenant_id}, {"_id": 0, "name": 1}) or {}
    service_name = job.get("service_name") or job.get("title") or "Service visit"
    notes = job.get("notes") or ""
    service_text = f"{service_name} {notes}"
    equipment = await _equipment_snapshot(db, tenant_id, job)
    if equipment:
        service_text += " " + " ".join(e.get("equipment_type", "") for e in equipment)

    token = f"hr_{secrets.token_urlsafe(32)}"
    now = _now_iso()
    report = {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "job_id": data.job_id,
        "business_name": tenant.get("name") or "Your service provider",
        "customer_name": job.get("customer_name") or "",
        "service_summary": service_name,
        "service_detail": notes,
        "service_date": job.get("completed_at") or job.get("end_at") or job.get("start_at") or now,
        "technician": job.get("staff") or job.get("tech_name") or "",
        "technician_notes": data.technician_notes or "",
        "photos": {"before": before_photos, "after": after_photos},
        "equipment_snapshot": equipment,
        "recommendations": _build_recommendations(service_text),
        "share_token": token,
        "token_expires_at": (datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS)).isoformat(),
        "token_revoked": False,
        "status": "draft",
        "sent_at": None,
        "sent_channel": None,
        "created_by": user.get("id"),
        "created_at": now,
        "updated_at": now,
    }
    await db.home_reports.insert_one(report)
    await _record_event(db, report, "created")
    report.pop("_id", None)
    return {"ok": True, "duplicate": False, "id": report["id"],
            "share_token": token, "report": report}


# ---------- public homeowner view (token only, NO auth) ----------

@router.get("/public/{token}")
async def public_report(token: str):
    """Homeowner-facing view: magic-link token only. Records a `viewed`
    event for the shop's open tracking (deduped: one view per token per day).
    """
    db = get_db()
    report = await db.home_reports.find_one({"share_token": token})
    if not report or report.get("token_revoked"):
        raise HTTPException(404, "Report not found")
    if is_expired(report.get("token_expires_at")):
        raise HTTPException(404, "Report link has expired")

    # Open tracking: one `viewed` event per report per UTC day.
    today = datetime.now(timezone.utc).date().isoformat()
    already = await db.home_report_events.find_one(
        {"report_id": report["id"], "type": "viewed", "day": today})
    if not already:
        await _record_event(db, report, "viewed")

    return {
        "business_name": report.get("business_name"),
        "customer_name": report.get("customer_name"),
        "service_summary": report.get("service_summary"),
        "service_detail": report.get("service_detail"),
        "service_date": report.get("service_date"),
        "technician": report.get("technician"),
        "technician_notes": report.get("technician_notes"),
        "photos": report.get("photos") or {"before": [], "after": []},
        "equipment_snapshot": report.get("equipment_snapshot") or [],
        "recommendations": report.get("recommendations") or [],
        "generated_at": report.get("created_at"),
        "note": "Recommendations are general maintenance suggestions, not a diagnosis.",
    }


# ---------- shop views ----------

@router.get("/")
async def list_reports(user: dict = Depends(require_tenant_user)):
    """List the tenant's reports, newest first, with open-tracking summaries."""
    db = get_db()
    docs = await db.home_reports.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}
    ).sort("created_at", -1).to_list(1000)
    out = []
    for d in docs:
        out.append(_shop_view(d, await _report_tracking(db, d)))
    return {"reports": out, "count": len(out)}


@router.get("/{report_id}")
async def get_report(report_id: str, user: dict = Depends(require_tenant_user)):
    """Shop view of one report, including send/open tracking."""
    db = get_db()
    report = await _get_report(db, user["tenant_id"], report_id)
    return _shop_view(report, await _report_tracking(db, report))


@router.get("/{report_id}/events")
async def report_events(report_id: str, user: dict = Depends(require_tenant_user)):
    """Open/send tracking events for a report, newest first."""
    db = get_db()
    report = await _get_report(db, user["tenant_id"], report_id)
    events = await db.home_report_events.find(
        {"tenant_id": user["tenant_id"], "report_id": report["id"]}, {"_id": 0}
    ).sort("created_at", -1).to_list(10000)
    return {"report_id": report["id"], "events": events, "count": len(events)}


# ---------- send ----------

@router.post("/{report_id}/send")
async def send_report(report_id: str, data: SendIn,
                      user: dict = Depends(require_tenant_owner_or_admin)):
    """Mark a report sent (records a `sent` event with the channel).

    TODO(LIVE): actually dispatch the magic link via the tenant's SMS/email
    provider (services.twilio / services.email) to the job's customer contact.
    """
    db = get_db()
    report = await _get_report(db, user["tenant_id"], report_id)
    now = _now_iso()
    await db.home_reports.update_one(
        {"tenant_id": user["tenant_id"], "id": report_id},
        {"$set": {"status": "sent", "sent_at": now, "sent_channel": data.channel,
                  "updated_at": now}},
    )
    report = await _get_report(db, user["tenant_id"], report_id)
    await _record_event(db, report, "sent", channel=data.channel)
    print(f"[HOME-REPORT send {data.channel}] report={report_id} "
          f"token={report.get('share_token')} (stubbed dispatch)")
    return {"ok": True, "id": report_id, "status": "sent",
            "sent_channel": data.channel, "share_token": report.get("share_token")}
