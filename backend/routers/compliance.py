"""Compliance Autopilot (Wave 6 / Phase E #5).

Checklist + reminder engine for shop-level regulatory upkeep: license
renewals, insurance certificates, tech certifications, EPA/OSHA
requirements, and FCC/10DLC consent hygiene. This is a checklist and
reminder system — it is NOT legal advice. The DISCLAIMER constant is
surfaced by every endpoint below and should be shown verbatim in any UI.

- Checklist (`compliance_items`): one document per tenant, lazily seeded
  with sensible defaults for the shop's trade (EPA 608 for HVAC,
  pesticide applicator for pest control, ...). Shops can add custom
  items and mark statuses.
- Document vault (`compliance_docs`): licenses/insurance/certs with
  expiry dates; expiring-soon listing sorted by urgency.
- Status: overall compliance score (0-100) + red/amber/green per
  category + upcoming deadlines (acts as the reminder feed).
- Report: audit-ready summary JSON.

Collections (all tenant-scoped): `compliance_items`, `compliance_docs`.
Zero live network calls.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin

router = APIRouter(prefix="/api/compliance", tags=["compliance"])

DISCLAIMER = (
    "Compliance checklists are reminders to help you stay organized — "
    "they are not legal advice. Always verify requirements with a "
    "licensed attorney or compliance professional for your trade and state."
)

PROJ = {"_id": 0}
EXPIRING_WITHIN_DAYS = 60

STATUSES = ("pending", "in_progress", "done")
CATEGORIES = (
    "license", "insurance", "certifications", "environmental",
    "safety", "marketing", "tax",
)
DOC_TYPES = ("license", "insurance", "cert", "other")


# ---------- seed catalog ----------

def _item(seed_id: str, name: str, category: str, description: str = "",
          required: bool = False, recurring: str = "annual") -> Dict[str, Any]:
    return {
        "seed_id": seed_id, "name": name, "category": category,
        "description": description, "required": required,
        "recurring": recurring, "status": "pending",
        "source": "seeded", "notes": "",
    }


BASE_ITEMS: List[Dict[str, Any]] = [
    _item("business-license", "Business license renewal", "license",
          "City/county business license is current.", required=True),
    _item("gl-insurance", "General liability insurance", "insurance",
          "Certificate of insurance on file and current.", required=True),
    _item("workers-comp", "Workers' compensation insurance", "insurance",
          "Required where you have employees."),
    _item("10dlc-registration", "10DLC brand & campaign registration", "marketing",
          "FCC 10DLC: keep SMS opt-in records for AI voice/SMS outreach.", required=True),
    _item("dnc-consent", "Do-not-call & consent hygiene", "marketing",
          "Scrub against DNC lists; retain consent records (see voice consent log)."),
    _item("quarterly-taxes", "Quarterly estimated tax payments", "tax",
          "Federal/state estimated payments are up to date.", recurring="quarterly"),
    _item("1099-filing", "1099 contractor filing prep", "tax",
          "W-9s collected for every 1099-eligible contractor."),
    _item("osha-general", "OSHA general duty compliance review", "safety",
          "Walk jobsites; log hazards and corrective actions.", recurring="quarterly"),
]

TRADE_EXTRAS: Dict[str, List[Dict[str, Any]]] = {
    "hvac": [
        _item("epa-608", "EPA Section 608 certification", "certifications",
              "Every tech who handles refrigerant must hold EPA 608.", required=True),
        _item("refrigerant-equipment", "Refrigerant recovery equipment calibration", "environmental",
              "Recovery/recycling equipment certified and leak-free."),
        _item("nate-cert", "NATE certification", "certifications",
              "Optional credibility booster for techs.", required=False),
    ],
    "plumbing": [
        _item("plumber-license", "State plumber license renewal", "license",
              "Journeyman/master license current for each tech.", required=True),
        _item("backflow-cert", "Backflow prevention assembly tester cert", "certifications",
              "Required where you test or install backflow devices."),
        _item("medical-gas", "Medical gas certification", "certifications",
              "Only if you work on medical gas systems."),
    ],
    "electrical": [
        _item("electrician-license", "State electrician license renewal", "license",
              "License + continuing education hours.", required=True),
        _item("osha-30", "OSHA 30 construction safety training", "safety",
              "30-hour cards for field leads."),
        _item("arc-flash", "Arc-flash / NFPA 70E training", "safety",
              "Required for energized work."),
    ],
    "roofing": [
        _item("mfr-cert", "Manufacturer certification", "certifications",
              "e.g. GAF/Owens Corning — needed for enhanced warranties."),
        _item("fall-protection", "Fall protection training (OSHA)", "safety",
              "Annual competent-person fall protection.", required=True),
    ],
    "pest_control": [
        _item("pesticide-applicator", "Pesticide applicator license", "license",
              "State applicator/operator license per tech.", required=True),
        _item("pesticide-storage", "Pesticide storage inspection", "environmental",
              "Locked storage, secondary containment, SDS binder."),
    ],
    "solar": [
        _item("nabcep", "NABCEP PV certification", "certifications",
              "Gold-standard credential for installers."),
        _item("electrical-permit", "Electrical permit compliance", "license",
              "Pull permits per AHJ for every install.", required=True),
    ],
    "painting": [
        _item("lead-rrp", "EPA Lead-Safe RRP certification", "environmental",
              "Required for work in pre-1978 homes.", required=True),
    ],
    "landscaping": [
        _item("pesticide-applicator", "Pesticide applicator license", "license",
              "If you apply herbicides/pesticides."),
    ],
    "pool_service": [
        _item("cpo-cert", "Certified Pool Operator (CPO) cert", "certifications",
              "Required in many states for commercial pools.", required=True),
        _item("chemical-storage", "Chemical storage safety review", "environmental",
              "Segregated, ventilated, labeled storage."),
    ],
    "cleaning": [
        _item("bonding", "Janitorial bond / fidelity bond", "insurance",
              "Protects clients against theft claims."),
    ],
    "handyman": [
        _item("contractor-license", "Contractor license (if required)", "license",
              "Many states require a license above a job-size threshold.", required=True),
    ],
    "appliance_repair": [
        _item("epa-608", "EPA Section 608 certification", "certifications",
              "Required if you service sealed refrigeration systems.", required=True),
    ],
    "garage_door": [
        _item("ul325", "UL 325 door safety compliance", "safety",
              "Entrapment protection on every install.", required=True),
    ],
    "tree_service": [
        _item("arborist-cert", "ISA arborist certification", "certifications",
              "Credibility + often required for municipal work."),
        _item("aerial-lift", "Aerial lift / chainsaw safety training", "safety",
              "ANSI Z133 compliance.", required=True),
    ],
    "restoration": [
        _item("iicrc", "IICRC certification (WRT/ASD)", "certifications",
              "Insurance carriers often require it."),
        _item("mold-license", "Mold remediation license", "license",
              "Required in FL, TX, LA, NY and others where you remediate mold."),
    ],
    "barbershop": [
        _item("barber-license", "Barber/cosmetology license renewal", "license",
              "Per-barber license + shop license.", required=True),
        _item("sanitation", "Sanitation & disinfection compliance", "safety",
              "State board sanitation rules; keep disinfectant logs.", required=True),
    ],
    "auto_repair": [
        _item("ase-cert", "ASE certifications", "certifications",
              "At least one ASE Master Tech on staff is a strong signal."),
        _item("hazmat-waste", "Hazardous waste disposal manifest", "environmental",
              "Oil, coolant, batteries, tires — documented disposal."),
    ],
    "kitchen_bath": [
        _item("contractor-license", "General contractor license", "license",
              "Required in most states for remodel work.", required=True),
        _item("lead-rrp", "EPA Lead-Safe RRP certification", "environmental",
              "Required for pre-1978 homes.", required=True),
    ],
    "home_inspection": [
        _item("hi-license", "Home inspector license", "license",
              "Licensed in ~35 states; E&O insurance often required.", required=True),
        _item("eo-insurance", "Errors & omissions insurance", "insurance",
              "Carriers/clients increasingly require it.", required=True),
    ],
}


def seed_items_for(trade: str) -> List[Dict[str, Any]]:
    """Default checklist items for a trade. Unknown trades get BASE_ITEMS."""
    key = (trade or "").strip().lower().replace(" ", "_").replace("&", "and")
    items = [dict(i) for i in BASE_ITEMS]
    for extra in TRADE_EXTRAS.get(key, []):
        items.append(dict(extra))
    for item in items:
        item["id"] = _uuid()
    return items


# ---------- date helpers ----------

def _parse_date(value: str) -> datetime:
    text = str(value).strip()
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _today_utc() -> datetime:
    return datetime.now(timezone.utc)


def _days_to_expiry(expiry_date: str) -> Optional[int]:
    try:
        return (_parse_date(expiry_date).date() - _today_utc().date()).days
    except (ValueError, TypeError, AttributeError):
        return None


def _urgency(days: Optional[int]) -> str:
    if days is None:
        return "unknown"
    if days < 0:
        return "expired"
    if days <= 30:
        return "red"
    if days <= EXPIRING_WITHIN_DAYS:
        return "amber"
    return "green"


def _category_color(done: int, total: int) -> str:
    if total == 0:
        return "green"
    pct = done / total
    if pct >= 1.0:
        return "green"
    if pct >= 0.5:
        return "amber"
    return "red"


# ---------- pydantic models (module-local) ----------

class CustomItemIn(BaseModel):
    name: str
    category: str = "license"
    description: str = ""
    required: bool = False
    recurring: str = "annual"

    @field_validator("category")
    @classmethod
    def _cat(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in CATEGORIES:
            raise ValueError(f"category must be one of {sorted(CATEGORIES)}")
        return v

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("name is required")
        return v.strip()


class ItemUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    document_id: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        return v


class DocumentIn(BaseModel):
    name: str
    type: str = "other"
    expiry_date: str = ""
    file_ref: str = ""
    file_name: str = ""
    notes: str = ""

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in DOC_TYPES:
            raise ValueError(f"type must be one of {sorted(DOC_TYPES)}")
        return v

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("name is required")
        return v.strip()

    @field_validator("expiry_date")
    @classmethod
    def _expiry(cls, v: str) -> str:
        if v:
            _parse_date(v)  # raises on bad format
        return v.strip() if v else ""


# ---------- internal helpers ----------

async def _get_or_seed(db, tenant_id: str, trade: str = "", state: str = "") -> Dict[str, Any]:
    doc = await db.compliance_items.find_one({"tenant_id": tenant_id}, PROJ)
    if doc:
        if trade and not doc.get("trade"):
            await db.compliance_items.update_one(
                {"tenant_id": tenant_id},
                {"$set": {"trade": trade, "state": state, "updated_at": _now_iso()}},
            )
            doc["trade"] = trade
            doc["state"] = state
        return doc
    items = seed_items_for(trade)
    doc = {
        "id": _uuid(), "tenant_id": tenant_id, "trade": trade, "state": state,
        "items": items, "created_at": _now_iso(), "updated_at": _now_iso(),
    }
    await db.compliance_items.insert_one(doc)
    return doc


def _score(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(items)
    done = sum(1 for i in items if i.get("status") == "done")
    pct = round(100 * done / total) if total else 100
    cats: Dict[str, Dict[str, int]] = {}
    for i in items:
        c = cats.setdefault(i.get("category", "license"), {"done": 0, "total": 0})
        c["total"] += 1
        if i.get("status") == "done":
            c["done"] += 1
    categories = {
        c: {
            "done": v["done"], "total": v["total"],
            "score": round(100 * v["done"] / v["total"]) if v["total"] else 100,
            "color": _category_color(v["done"], v["total"]),
        }
        for c, v in cats.items()
    }
    color = "green" if pct >= 80 else ("amber" if pct >= 50 else "red")
    return {"score": pct, "done": done, "total": total, "color": color, "categories": categories}


def _doc_with_urgency(doc: Dict[str, Any]) -> Dict[str, Any]:
    days = _days_to_expiry(doc.get("expiry_date") or "")
    out = dict(doc)
    out["days_to_expiry"] = days
    out["urgency"] = _urgency(days)
    return out


# ---------- endpoints ----------

@router.get("/tenants/checklist")
async def get_checklist(
    trade: str = "", state: str = "",
    user: dict = Depends(require_tenant_user),
):
    """Checklist for the shop's trade+state; seeds defaults on first call."""
    tenant_id = user["tenant_id"]
    db = get_db()
    doc = await _get_or_seed(db, tenant_id, trade, state)
    return {
        "tenant_id": tenant_id, "trade": doc.get("trade", ""), "state": doc.get("state", ""),
        "items": doc.get("items", []), "count": len(doc.get("items", [])),
        "disclaimer": DISCLAIMER,
    }


@router.post("/tenants/checklist/items", status_code=201)
async def add_custom_item(
    payload: CustomItemIn,
    user: dict = Depends(require_tenant_owner_or_admin),
):
    """Add a custom checklist item (owner/admin)."""
    tenant_id = user["tenant_id"]
    db = get_db()
    doc = await _get_or_seed(db, tenant_id)
    item = {
        "id": _uuid(), "name": payload.name, "category": payload.category,
        "description": payload.description, "required": payload.required,
        "recurring": payload.recurring, "status": "pending",
        "source": "custom", "notes": "",
    }
    doc["items"].append(item)
    await db.compliance_items.update_one(
        {"tenant_id": tenant_id},
        {"$set": {"items": doc["items"], "updated_at": _now_iso()}},
    )
    return {"item": item, "disclaimer": DISCLAIMER}


@router.put("/tenants/checklist/items/{item_id}")
async def update_item(
    item_id: str,
    payload: ItemUpdate,
    user: dict = Depends(require_tenant_user),
):
    """Update an item's status / notes / linked document."""
    tenant_id = user["tenant_id"]
    db = get_db()
    doc = await _get_or_seed(db, tenant_id)
    for item in doc["items"]:
        if item.get("id") == item_id:
            if payload.status is not None:
                item["status"] = payload.status
            if payload.notes is not None:
                item["notes"] = payload.notes
            if payload.document_id is not None:
                item["document_id"] = payload.document_id
            item["updated_at"] = _now_iso()
            await db.compliance_items.update_one(
                {"tenant_id": tenant_id},
                {"$set": {"items": doc["items"], "updated_at": _now_iso()}},
            )
            return {"item": item, "disclaimer": DISCLAIMER}
    raise HTTPException(404, "Checklist item not found")


@router.post("/tenants/documents", status_code=201)
async def register_document(
    payload: DocumentIn,
    user: dict = Depends(require_tenant_owner_or_admin),
):
    """Register a compliance document in the vault (owner/admin)."""
    tenant_id = user["tenant_id"]
    db = get_db()
    doc = {
        "id": _uuid(), "tenant_id": tenant_id, "name": payload.name,
        "type": payload.type, "expiry_date": payload.expiry_date,
        "file_ref": payload.file_ref, "file_name": payload.file_name,
        "notes": payload.notes, "created_at": _now_iso(), "updated_at": _now_iso(),
    }
    await db.compliance_docs.insert_one(doc)
    out = _doc_with_urgency({k: v for k, v in doc.items() if k != "_id"})
    return {"document": out, "disclaimer": DISCLAIMER}


@router.get("/tenants/documents/expiring")
async def expiring_documents(
    user: dict = Depends(require_tenant_user),
):
    """Docs expiring within 60 days (expired ones first), sorted by urgency."""
    tenant_id = user["tenant_id"]
    db = get_db()
    cursor = db.compliance_docs.find({"tenant_id": tenant_id}, PROJ)
    docs = await _collect(cursor)
    enriched = [_doc_with_urgency(d) for d in docs]
    due = [d for d in enriched
           if d["days_to_expiry"] is not None and d["days_to_expiry"] <= EXPIRING_WITHIN_DAYS]
    due.sort(key=lambda d: (d["days_to_expiry"] is None, d["days_to_expiry"] or 0))
    return {"documents": due, "count": len(due),
            "window_days": EXPIRING_WITHIN_DAYS, "disclaimer": DISCLAIMER}


@router.get("/tenants/status")
async def compliance_status(
    user: dict = Depends(require_tenant_user),
):
    """Overall score + per-category red/amber/green + upcoming deadlines."""
    tenant_id = user["tenant_id"]
    db = get_db()
    doc = await _get_or_seed(db, tenant_id)
    items = doc.get("items", [])
    score = _score(items)
    cursor = db.compliance_docs.find({"tenant_id": tenant_id}, PROJ)
    docs = [_doc_with_urgency(d) for d in await _collect(cursor)]
    upcoming = [d for d in docs
                if d["days_to_expiry"] is not None and d["days_to_expiry"] <= EXPIRING_WITHIN_DAYS]
    upcoming.sort(key=lambda d: d["days_to_expiry"])
    open_required = [i for i in items if i.get("required") and i.get("status") != "done"]
    return {
        "tenant_id": tenant_id, "trade": doc.get("trade", ""), "state": doc.get("state", ""),
        "score": score["score"], "color": score["color"],
        "done": score["done"], "total": score["total"],
        "categories": score["categories"],
        "open_required_items": len(open_required),
        "upcoming_deadlines": upcoming,
        "disclaimer": DISCLAIMER,
    }


@router.get("/tenants/report")
async def compliance_report(
    user: dict = Depends(require_tenant_user),
):
    """Audit-ready compliance summary (JSON, shaped for printable views)."""
    tenant_id = user["tenant_id"]
    db = get_db()
    doc = await _get_or_seed(db, tenant_id)
    items = doc.get("items", [])
    score = _score(items)
    cursor = db.compliance_docs.find({"tenant_id": tenant_id}, PROJ)
    docs = [_doc_with_urgency(d) for d in await _collect(cursor)]
    by_category: Dict[str, List[Dict[str, Any]]] = {}
    for i in items:
        by_category.setdefault(i.get("category", "license"), []).append({
            "id": i.get("id"), "name": i.get("name"), "status": i.get("status"),
            "required": i.get("required"), "recurring": i.get("recurring"),
            "description": i.get("description", ""), "source": i.get("source", ""),
            "document_id": i.get("document_id", ""),
        })
    deadlines = [d for d in docs
                 if d["days_to_expiry"] is not None and d["days_to_expiry"] <= EXPIRING_WITHIN_DAYS]
    deadlines.sort(key=lambda d: d["days_to_expiry"])
    return {
        "tenant_id": tenant_id, "trade": doc.get("trade", ""), "state": doc.get("state", ""),
        "generated_at": _now_iso(),
        "score": score["score"], "color": score["color"],
        "done": score["done"], "total": score["total"],
        "items_by_category": by_category,
        "documents": docs,
        "upcoming_deadlines": deadlines,
        "disclaimer": DISCLAIMER,
    }


# ---------- tiny async-compat helpers ----------

async def _collect(cursor) -> List[Dict[str, Any]]:
    """Collect docs from a motor-style cursor or a plain iterable (tests)."""
    if hasattr(cursor, "__aiter__"):
        out = []
        async for d in cursor:
            out.append(_doc_to_dict(d))
        return out
    return [_doc_to_dict(d) for d in cursor]


def _doc_to_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items() if k != "_id"}
