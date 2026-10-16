"""Predictive revenue engine: ranked replacement/upsell opportunities.

Equipment age + season + service history → auto-generated replacement quotes
with financing pre-attached. Reads `equipment` / `equipment_service_events` /
`pricebook_items` (all tenant-scoped), ranks by the deterministic scoring
engine in `services/predictive_revenue.py`, and drafts quotes through the
existing `_create_estimate_doc` pipeline (same pattern as pricebook.py —
frozen prices, notes, expiry; never invents prices).

Collections (tenant-scoped):
- `revenue_dismissals`: hidden opportunities with a reason (feeds future tuning).
- `revenue_drafts`: links a quote id back to the equipment opportunity that
  created it, so repeated `draft-quote` calls stay idempotent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin
from routers.call_to_payment import _create_estimate_doc
from services.predictive_revenue import (
    expected_lifespan_years,
    score_equipment,
    suggest_items,
)

router = APIRouter(prefix="/tenants/revenue", tags=["revenue"])

# Only opportunities worth showing — below this the score is noise.
MIN_SCORE = 25


# ---------- models ----------

class DismissIn(BaseModel):
    reason: str = Field(default="", max_length=500)


# ---------- helpers ----------

def _equipment_label(doc: dict) -> str:
    etype = (doc.get("equipment_type") or "equipment").replace("_", " ")
    make = (doc.get("make") or "").strip()
    model = (doc.get("model") or "").strip()
    bits = [b for b in (make, model) if b]
    return f"{etype}{' (' + ' '.join(bits) + ')' if bits else ''}"


async def _opportunities(db, tenant_id: str) -> List[dict]:
    """All ranked, non-dismissed opportunities for the tenant."""
    dismissed = await db.revenue_dismissals.find(
        {"tenant_id": tenant_id}, {"_id": 0}
    ).to_list(5000)
    dismissed_ids = {d.get("equipment_id") for d in dismissed if d.get("equipment_id")}

    equipment = await db.equipment.find(
        {"tenant_id": tenant_id, "active": True}, {"_id": 0}
    ).to_list(2000)
    events = await db.equipment_service_events.find(
        {"tenant_id": tenant_id}, {"_id": 0}
    ).to_list(5000)
    by_equipment: dict = {}
    for e in events:
        by_equipment.setdefault(e.get("equipment_id"), []).append(e)
    catalog = await db.pricebook_items.find(
        {"tenant_id": tenant_id, "active": True}, {"_id": 0}
    ).to_list(2000)
    drafts = await db.revenue_drafts.find(
        {"tenant_id": tenant_id}, {"_id": 0}
    ).to_list(2000)
    drafted_ids = {d.get("equipment_id") for d in drafts if d.get("equipment_id")}

    now = datetime.now(timezone.utc)
    opps = []
    for eq in equipment:
        eid = eq.get("id")
        if not eid or eid in dismissed_ids:
            continue
        opp = score_equipment(eq, by_equipment.get(eid, []), now=now)
        if opp["replacement_score"] < MIN_SCORE:
            continue
        items = suggest_items(opp["equipment_type"], catalog)
        opp["suggested_items"] = items
        opp["estimated_value"] = round(sum(i["price"] for i in items), 2)
        opp["quote_drafted"] = eid in drafted_ids
        opps.append(opp)
    opps.sort(key=lambda o: (-o["replacement_score"], o["equipment_id"] or ""))
    return opps


def _financing_note(amount_cents: int) -> str:
    return (
        "Financing pre-attached — this quote is ready to send with one tap. "
        "Pull live monthly-payment offers for the customer at "
        f"GET /tenants/financing/offers?amount_cents={amount_cents} "
        "(staff quoting; mirrors the demo/Wisetack provider math). "
        "Customer-facing applications go through the quote's public link."
    )


# ---------- endpoints ----------

@router.get("/opportunities")
async def list_opportunities(
    user: dict = Depends(require_tenant_user),
    min_score: int = MIN_SCORE,
    include_low: bool = False,
):
    """Ranked replacement opportunities: equipment_id, customer, type, age,
    expected lifespan, 0–100 replacement score, season factor, suggested
    pricebook items, estimated value. Dismissed units are hidden."""
    db = get_db()
    opps = await _opportunities(db, user["tenant_id"])
    if include_low:
        threshold = max(0, min_score)
        opps = [o for o in opps if o["replacement_score"] >= threshold]
    return {
        "opportunities": opps,
        "count": len(opps),
        "scored_at": _now_iso(),
    }


@router.post("/opportunities/{equipment_id}/draft-quote")
async def draft_quote_for_opportunity(
    equipment_id: str, user: dict = Depends(require_tenant_owner_or_admin)
):
    """One-tap quote: freezes the suggested pricebook items (prices frozen at
    draft time, same pipeline as pricebook photo estimates) and attaches the
    financing note. Idempotent — repeats return the original quote."""
    db = get_db()
    tenant_id = user["tenant_id"]
    eq = await db.equipment.find_one(
        {"id": equipment_id, "tenant_id": tenant_id, "active": True}, {"_id": 0}
    )
    if not eq:
        raise HTTPException(404, "Equipment record not found")

    existing = await db.revenue_drafts.find_one(
        {"tenant_id": tenant_id, "equipment_id": equipment_id}, {"_id": 0}
    )
    if existing:
        quote = await db.estimates.find_one(
            {"id": existing.get("quote_id"), "tenant_id": tenant_id}, {"_id": 0}
        )
        if quote:
            return {
                "quote": quote, "equipment_id": equipment_id,
                "already_drafted": True, "financing_note": _financing_note(int(round((quote.get("total") or 0) * 100))),
            }

    catalog = await db.pricebook_items.find(
        {"tenant_id": tenant_id, "active": True}, {"_id": 0}
    ).to_list(2000)
    items = suggest_items(str(eq.get("equipment_type") or ""), catalog)
    if not items:
        raise HTTPException(
            409,
            "No replacement-style pricebook items matched this equipment type — "
            "add items like '<type> replacement' to the pricebook first",
        )

    events = await db.equipment_service_events.find(
        {"tenant_id": tenant_id, "equipment_id": equipment_id}, {"_id": 0}
    ).to_list(1000)
    opp = score_equipment(eq, events)
    lines = [
        {
            "description": it["name"],
            "quantity": 1.0,
            "unit_price": it["price"],
            "service_id": None,
            "pricebook_item_id": it["id"],
            "pricebook_version": it["version"],
        }
        for it in items
    ]
    total = round(sum(it["price"] for it in items), 2)
    title = f"Replacement recommendation — {_equipment_label(eq)}"
    notes = (
        f"Auto-generated by the predictive revenue engine (score "
        f"{opp['replacement_score']}/100; {opp['age_years']} yrs old vs "
        f"~{opp['expected_lifespan_years']} yr expected life; "
        f"{opp['recent_repairs_12mo']} repair(s) in the last 12 months). "
        + _financing_note(int(round(total * 100)))
    )
    quote = await _create_estimate_doc(
        db,
        tenant_id,
        lead_id=None,
        customer_name=eq.get("customer_name") or "Customer",
        customer_email=None,
        customer_phone="",
        title=title,
        lines=lines,
        notes=notes,
        expires_in_days=14,
    )
    await db.revenue_drafts.insert_one({
        "id": _uuid(),
        "tenant_id": tenant_id,
        "equipment_id": equipment_id,
        "quote_id": quote["id"],
        "score": opp["replacement_score"],
        "created_by": user.get("id"),
        "created_at": _now_iso(),
    })
    return {
        "quote": quote, "equipment_id": equipment_id,
        "already_drafted": False, "financing_note": _financing_note(int(round(total * 100))),
    }


@router.post("/opportunities/{equipment_id}/dismiss")
async def dismiss_opportunity(
    equipment_id: str, data: DismissIn,
    user: dict = Depends(require_tenant_owner_or_admin),
):
    """Hide an opportunity with a reason — reasons feed future tuning."""
    reason = (data.reason or "").strip()
    if not reason:
        raise HTTPException(400, "A dismissal reason is required (it feeds future tuning)")
    db = get_db()
    tenant_id = user["tenant_id"]
    eq = await db.equipment.find_one(
        {"id": equipment_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if not eq:
        raise HTTPException(404, "Equipment record not found")
    await db.revenue_dismissals.insert_one({
        "id": _uuid(),
        "tenant_id": tenant_id,
        "equipment_id": equipment_id,
        "reason": reason,
        "equipment_type": eq.get("equipment_type") or "",
        "score_at_dismissal": score_equipment(
            eq, await db.equipment_service_events.find(
                {"tenant_id": tenant_id, "equipment_id": equipment_id}, {"_id": 0}
            ).to_list(1000)
        )["replacement_score"],
        "dismissed_by": user.get("id"),
        "dismissed_at": _now_iso(),
    })
    return {"ok": True, "equipment_id": equipment_id}
