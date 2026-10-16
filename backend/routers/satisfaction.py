"""Post-resolution satisfaction pulses — the missing feedback loop of the
customer-service excellence initiative (both layers).

A pulse is a one-question satisfaction check sent when a case closes:
"How did we do? 1-5". It fires for BOTH layers:
- Layer 1: a shop's support ticket is resolved (routers/support_tickets.py
  dispatches one via `dispatch_pulse`).
- Layer 2: a shop's customer inbox thread is resolved (routers/inbox.py
  dispatches one).
- Post-job pulses dispatch with source_type="job" (the Phase D promise:
  "post-job satisfaction pulse feeding tech leaderboards").

Responses feed two places:
- `GET /summary` — response rate, average rating, distribution, per-tech
  averages: the service-quality signal owners watch.
- Tech leaderboards: a 5-star response with a tech attached writes a
  `5_star_review` gamification event (idempotent on the pulse id, points
  from the tenant's gamification_rules — the same doc shape
  routers/gamification.py writes), so pulses literally feed the Phase D
  leaderboards.

Delivery is stubbed: TODO(LIVE) — dispatch records the pulse and prints a
stub send line; the real send goes out over the pulse's channel (Twilio
SMS / email provider) with a signed response link. The respond endpoint
is tenant-authenticated until that signed link exists.

Collections (all tenant-scoped):
- `satisfaction_pulses`: one doc per (source_type, source_id) — re-dispatch
  for the same source is a no-op returning the existing pulse.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin

router = APIRouter(prefix="/api/satisfaction", tags=["satisfaction"])

SOURCE_TYPES = ("ticket", "thread", "job")
DEFAULT_FIVE_STAR_POINTS = 25  # mirrors gamification.DEFAULT_POINT_VALUES


# ---------- models ----------

class DispatchIn(BaseModel):
    source_type: str = Field(pattern="^(ticket|thread|job)$")
    source_id: str = Field(min_length=1)
    customer_name: str = ""
    customer_contact: str = ""
    channel: str = Field(default="sms", pattern="^(sms|email|chat)$")
    tech_id: Optional[str] = None
    tech_name: str = ""


class RespondIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=1000)


# ---------- helpers ----------

async def dispatch_pulse(db, tenant_id: str, *, source_type: str, source_id: str,
                         customer_name: str = "", customer_contact: str = "",
                         channel: str = "sms", tech_id: Optional[str] = None,
                         tech_name: str = "") -> Optional[Dict]:
    """Create (or return the existing) satisfaction pulse for a resolved
    source and stub-send it. Idempotent per (tenant, source_type,
    source_id). Best-effort: returns None instead of raising so resolution
    hooks can call it without endangering the resolution itself."""
    try:
        existing = await db.satisfaction_pulses.find_one(
            {"tenant_id": tenant_id, "source_type": source_type, "source_id": source_id})
        if existing:
            existing.pop("_id", None)
            return existing
        now = _now_iso()
        doc = {
            "id": _uuid(),
            "tenant_id": tenant_id,
            "source_type": source_type,
            "source_id": source_id,
            "customer_name": customer_name or "",
            "customer_contact": customer_contact or "",
            "channel": channel or "sms",
            "tech_id": tech_id,
            "tech_name": tech_name or "",
            "status": "sent",
            "rating": None,
            "comment": "",
            "sent_at": now,
            "responded_at": None,
            "created_at": now,
            "updated_at": now,
        }
        await db.satisfaction_pulses.insert_one(doc)
        # TODO(LIVE): real dispatch over the channel with a signed respond
        # link (Twilio SMS / email provider), like outreach_queue dispatch.
        print(f"[SATISFACTION pulse {doc['channel']} -> {doc['customer_contact'] or 'n/a'}] "
              f"source={source_type}:{source_id} (stubbed send)")
        doc.pop("_id", None)
        return doc
    except Exception:
        return None


async def _five_star_points(db, tenant_id: str) -> int:
    """Point value for a 5-star review: tenant gamification_rules override,
    else the gamification default — same merge rules as gamification.py."""
    doc = await db.gamification_rules.find_one({"tenant_id": tenant_id})
    if doc and isinstance(doc.get("point_values"), dict):
        v = doc["point_values"].get("5_star_review")
        if isinstance(v, int) and v >= 0:
            return v
    return DEFAULT_FIVE_STAR_POINTS


async def _award_five_star(db, tenant_id: str, pulse: Dict) -> Optional[Dict]:
    """Write the gamification event a 5-star pulse earns. Idempotent on
    (tenant, tech, event_type, ref_id=pulse id) — the same contract
    routers/gamification.py enforces."""
    tech_id = pulse.get("tech_id")
    if not tech_id:
        return None
    existing = await db.gamification_events.find_one(
        {"tenant_id": tenant_id, "tech_id": tech_id,
         "event_type": "5_star_review", "ref_id": pulse["id"]})
    if existing:
        return existing
    doc = {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "tech_id": tech_id,
        "tech_name": pulse.get("tech_name") or "",
        "event_type": "5_star_review",
        "ref_id": pulse["id"],
        "notes": f"5-star satisfaction pulse for {pulse.get('source_type')} {pulse.get('source_id')}",
        "points": await _five_star_points(db, tenant_id),
        "created_at": _now_iso(),
    }
    await db.gamification_events.insert_one(doc)
    return doc


# ---------- endpoints ----------

@router.post("/pulses/dispatch")
async def dispatch(data: DispatchIn, user: dict = Depends(require_tenant_user)):
    """Send (stubbed) a satisfaction pulse for a resolved ticket/thread/job.
    Re-dispatching the same source returns the existing pulse."""
    db = get_db()
    pulse = await dispatch_pulse(
        db, user["tenant_id"],
        source_type=data.source_type, source_id=data.source_id,
        customer_name=data.customer_name, customer_contact=data.customer_contact,
        channel=data.channel, tech_id=data.tech_id, tech_name=data.tech_name)
    if pulse is None:
        raise HTTPException(500, "Could not create satisfaction pulse")
    return {"ok": True, "pulse": pulse}


@router.post("/pulses/{pulse_id}/respond")
async def respond(pulse_id: str, data: RespondIn, user: dict = Depends(require_tenant_user)):
    """Record the customer's 1-5 rating (+ optional comment).

    Tenant-authenticated until the signed public response link ships
    (TODO(LIVE)). A 5-star response with a tech attached also writes the
    gamification event that feeds the tech leaderboard.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    pulse = await db.satisfaction_pulses.find_one({"tenant_id": tenant_id, "id": pulse_id})
    if not pulse:
        raise HTTPException(404, "Pulse not found")
    if pulse.get("status") == "responded":
        raise HTTPException(409, "This pulse has already been answered")
    now = _now_iso()
    await db.satisfaction_pulses.update_one(
        {"tenant_id": tenant_id, "id": pulse_id},
        {"$set": {"status": "responded", "rating": data.rating,
                  "comment": data.comment, "responded_at": now, "updated_at": now}})
    pulse.update({"status": "responded", "rating": data.rating,
                  "comment": data.comment, "responded_at": now, "updated_at": now})

    award = None
    if data.rating == 5 and pulse.get("tech_id"):
        try:
            award = await _award_five_star(db, tenant_id, pulse)
        except Exception:
            award = None  # leaderboard hiccup must never eat the response
    pulse.pop("_id", None)
    return {"ok": True, "pulse": pulse,
            "leaderboard_event": ({"id": award.get("id"), "points": award.get("points")}
                                  if award else None)}


@router.get("/pulses")
async def list_pulses(status: Optional[str] = None,
                      source_type: Optional[str] = None,
                      user: dict = Depends(require_tenant_user)):
    """Pulse list, newest first. Filter with ?status=sent|responded, ?source_type=."""
    db = get_db()
    q: Dict = {"tenant_id": user["tenant_id"]}
    if status:
        q["status"] = status
    if source_type:
        q["source_type"] = source_type
    docs = await db.satisfaction_pulses.find(q, {"_id": 0}).sort("created_at", -1).to_list(1000)
    return {"pulses": docs, "count": len(docs)}


@router.get("/summary")
async def summary(user: dict = Depends(require_tenant_user)):
    """Service-quality rollup: sent/responded counts, response rate, average
    rating, 1-5 distribution, and per-tech averages (leaderboard feed)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    docs = await db.satisfaction_pulses.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(10000)
    responded = [d for d in docs if d.get("status") == "responded" and d.get("rating")]
    distribution = {str(i): 0 for i in range(1, 6)}
    for d in responded:
        distribution[str(d["rating"])] = distribution.get(str(d["rating"]), 0) + 1
    avg = round(sum(d["rating"] for d in responded) / len(responded), 2) if responded else 0.0

    by_tech: Dict[str, Dict] = {}
    for d in responded:
        tid = d.get("tech_id")
        if not tid:
            continue
        slot = by_tech.setdefault(tid, {"tech_id": tid, "tech_name": d.get("tech_name") or "",
                                         "responses": 0, "total": 0})
        slot["responses"] += 1
        slot["total"] += d["rating"]
    tech_rows: List[Dict] = [
        {"tech_id": t["tech_id"], "tech_name": t["tech_name"],
         "responses": t["responses"], "avg_rating": round(t["total"] / t["responses"], 2)}
        for t in by_tech.values()
    ]
    tech_rows.sort(key=lambda r: (-r["avg_rating"], -r["responses"]))

    return {
        "sent": len(docs),
        "responded": len(responded),
        "response_rate": round(len(responded) / len(docs), 3) if docs else 0.0,
        "avg_rating": avg,
        "distribution": distribution,
        "by_tech": tech_rows,
        "detractors": sum(1 for d in responded if d["rating"] <= 2),
        "promoters": sum(1 for d in responded if d["rating"] == 5),
    }
