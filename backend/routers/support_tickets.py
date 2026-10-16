"""Helpdesk + ticketing with SLAs (Layer 1 of the customer-service excellence
initiative: Momo supporting its shops).

Shops (tenants) open support tickets; the platform team triages them. This is
the ticketing backbone the AI support agent (built separately) also writes to.

Ticket lifecycle:  open -> in_progress -> waiting_on_customer -> resolved -> closed
Priorities:        low | medium | high | urgent

SLA policies are per-priority (seeded defaults on first read, stored in
`sla_policies`, one doc per tenant):

    urgent: 15-min first response,  4-h  resolution
    high:    1-h  first response, 24-h  resolution
    medium:  4-h  first response, 72-h  resolution
    low:    24-h  first response,  5-d  resolution

Both the first-response timer and the resolution timer run from
``ticket.created_at``. First response = the first PUBLIC reply authored by
platform staff (``author_role="staff"``); internal notes never satisfy it.

**SLA clock pauses while the ticket is ``waiting_on_customer``.** The ticket
accumulates ``waiting_seconds`` each time it leaves that status, and the
currently-elapsed waiting segment is also excluded while it is still waiting,
so shops are never penalized for time the platform is blocked on them.

Auto-transitions on public replies (keeps the queue honest without a human
clicking):
- a public reply (staff or customer) on a ``waiting_on_customer`` ticket
  moves it back to ``in_progress``.
- a public CUSTOMER reply on a ``resolved`` ticket re-opens it to
  ``in_progress`` (the customer disagrees with the resolution).

Collections (all tenant-scoped):
- `support_tickets`: the ticket doc (status, priority, assignee, timestamps,
  waiting_seconds).
- `ticket_events`: audit trail (created, status_changed, priority_changed,
  assignee_changed, reply_added, resolved, reopened, sla_breached).
- `ticket_replies`: public replies + internal notes (``kind``).
- `sla_policies`: one doc per tenant with per-priority targets (auto-seeded).
- `kb_article_drafts`: the flywheel — a ticket resolved WITH a resolution
  note leaves a draft knowledge-base article (question = the ticket,
  answer = the human's resolution) for owner review, so the AI support
  agent can answer it next time.

Wave 8+ hardening (wave/service-excellence-plus):
- SLA breaches are now HANDLED, not just displayed: the first time a live
  ticket's snapshot reads breached, the ticket is stamped
  `sla_breach_recorded_at` and an `sla_breached` event lands in the audit
  trail (+ stubbed supervisor notification). Recorded from the monitoring
  surfaces (SLA overview + ticket detail); the stamp makes it idempotent.
- Resolve also stores `resolution_note`/`resolved_by` on the ticket,
  syncs the resolved case to the central service hub archive
  (services/service_hub.py — env-configured, OFF by default, best-effort),
  and dispatches a satisfaction pulse (routers/satisfaction.py).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin
from routers.satisfaction import dispatch_pulse
from services import service_hub as hub

router = APIRouter(prefix="/api/support-tickets", tags=["support-tickets"])

STATUSES = ("open", "in_progress", "waiting_on_customer", "resolved", "closed")
PRIORITIES = ("low", "medium", "high", "urgent")
FINAL_STATUSES = {"resolved", "closed"}

# Allowed status transitions. Re-opening is done via POST /{id}/reopen only,
# so `resolved`/`closed` have no PATCH exits — that keeps reopens auditable.
TRANSITIONS: Dict[str, set] = {
    "open": {"in_progress", "resolved", "closed"},
    "in_progress": {"open", "waiting_on_customer", "resolved", "closed"},
    "waiting_on_customer": {"in_progress", "resolved", "closed"},
    "resolved": set(),
    "closed": set(),
}

DEFAULT_SLA_MINUTES = {
    "urgent": {"first_response": 15, "resolution": 4 * 60},
    "high": {"first_response": 60, "resolution": 24 * 60},
    "medium": {"first_response": 4 * 60, "resolution": 72 * 60},
    "low": {"first_response": 24 * 60, "resolution": 5 * 24 * 60},
}

AT_RISK_RATIO = 0.8  # within 80% of the target -> at-risk


# ---------- models ----------

class TicketIn(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    priority: str = Field(default="medium", pattern="^(low|medium|high|urgent)$")
    category: str = Field(default="", max_length=80)  # billing, onboarding, bug, feature_request, ...
    contact_name: str = ""
    contact_email: str = ""
    contact_phone: str = ""


class TicketPatch(BaseModel):
    status: Optional[str] = Field(default=None, pattern="^(open|in_progress|waiting_on_customer|resolved|closed)$")
    priority: Optional[str] = Field(default=None, pattern="^(low|medium|high|urgent)$")
    assignee: Optional[str] = Field(default=None)  # staff id/email; null clears


class ReplyIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)
    kind: str = Field(default="public", pattern="^(public|internal)$")
    author_role: str = Field(default="customer", pattern="^(customer|staff)$")
    # author_role="staff" is how the platform team (and the AI support agent,
    # which posts here too) identifies itself; the shop is the "customer".


class ResolveIn(BaseModel):
    resolution_note: str = Field(default="", max_length=5000)


class ReopenIn(BaseModel):
    reason: str = Field(default="", max_length=1000)


class SlaPoliciesIn(BaseModel):
    # Per-priority targets in minutes; partial updates allowed.
    urgent: Optional[Dict[str, int]] = None
    high: Optional[Dict[str, int]] = None
    medium: Optional[Dict[str, int]] = None
    low: Optional[Dict[str, int]] = None


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


async def _log_event(db, tenant_id: str, ticket_id: str, kind: str,
                     actor_id: Optional[str], detail: Optional[Dict] = None) -> Dict:
    event = {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "ticket_id": ticket_id,
        "kind": kind,
        "actor_id": actor_id,
        "detail": detail or {},
        "at": _now_iso(),
    }
    await db.ticket_events.insert_one(event)
    event.pop("_id", None)
    return event


async def _get_ticket(db, tenant_id: str, ticket_id: str) -> Dict:
    ticket = await db.support_tickets.find_one({"tenant_id": tenant_id, "id": ticket_id})
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    return ticket


async def _get_policies(db, tenant_id: str) -> Dict:
    """Return the tenant's SLA policies, seeding defaults on first read."""
    doc = await db.sla_policies.find_one({"tenant_id": tenant_id})
    if doc:
        targets = doc.get("targets") or {}
        # Merge in case an older seed is missing a priority.
        merged = {p: {**DEFAULT_SLA_MINUTES[p], **targets.get(p, {})} for p in PRIORITIES}
        return merged
    targets = {p: dict(DEFAULT_SLA_MINUTES[p]) for p in PRIORITIES}
    doc = {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "targets": targets,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    await db.sla_policies.insert_one(doc)
    return targets


def _settle_waiting_segment(ticket: Dict, now: datetime) -> float:
    """Accumulate the just-finished waiting_on_customer segment into
    waiting_seconds. Returns the updated waiting_seconds."""
    waiting_seconds = float(ticket.get("waiting_seconds") or 0)
    if ticket.get("status") == "waiting_on_customer":
        seg_start = _parse_iso(ticket.get("status_changed_at", "")) or _parse_iso(ticket.get("created_at", ""))
        if seg_start:
            waiting_seconds += max(0.0, (now - seg_start).total_seconds())
    return waiting_seconds


async def _transition(db, ticket: Dict, new_status: str, actor_id: Optional[str],
                      allowed: bool = True) -> Dict:
    """Move a ticket to a new status, settling the waiting clock, stamping
    resolved_at, and logging the event."""
    old_status = ticket.get("status")
    if new_status == old_status:
        return ticket
    if allowed and new_status not in TRANSITIONS.get(old_status, set()):
        raise HTTPException(
            409,
            f"Invalid status transition: {old_status} -> {new_status}. "
            f"Re-open resolved/closed tickets via POST /{{id}}/reopen.",
        )
    now = datetime.now(timezone.utc)
    updates = {
        "status": new_status,
        "status_changed_at": now.isoformat(),
        "waiting_seconds": _settle_waiting_segment(ticket, now),
        "updated_at": now.isoformat(),
    }
    if new_status == "resolved":
        updates["resolved_at"] = now.isoformat()
    if old_status in FINAL_STATUSES:
        # Leaving a final state (only possible via reopen) clears resolution.
        updates["resolved_at"] = None
    await db.support_tickets.update_one(
        {"tenant_id": ticket["tenant_id"], "id": ticket["id"]}, {"$set": updates})
    ticket.update(updates)
    await _log_event(db, ticket["tenant_id"], ticket["id"], "status_changed",
                     actor_id, {"from": old_status, "to": new_status})
    return ticket


def _first_response_at(replies: List[Dict]) -> Optional[datetime]:
    """First public reply authored by platform staff (internal notes never count)."""
    staff_public = [r for r in replies
                    if r.get("kind") == "public" and r.get("author_role") == "staff"]
    staff_public.sort(key=lambda r: str(r.get("created_at", "")))
    return _parse_iso(staff_public[0].get("created_at", "")) if staff_public else None


def _timer_status(elapsed_min: float, target_min: float, finished: bool) -> str:
    """on_track | at_risk | breached | met. A finished timer is only ever
    met (finished in time) or breached."""
    if finished:
        return "met" if elapsed_min <= target_min else "breached"
    if elapsed_min > target_min:
        return "breached"
    if elapsed_min >= AT_RISK_RATIO * target_min:
        return "at_risk"
    return "on_track"


def _sla_snapshot(ticket: Dict, replies: List[Dict], policies: Dict,
                  now: datetime) -> Dict:
    """Compute first-response + resolution SLA state for one ticket."""
    priority = ticket.get("priority") or "medium"
    targets = policies.get(priority, DEFAULT_SLA_MINUTES["medium"])
    fr_target = float(targets["first_response"])
    res_target = float(targets["resolution"])

    created = _parse_iso(ticket.get("created_at", ""))
    if not created:
        created = now
    status = ticket.get("status")

    # ---- first response ----
    fr_at = _first_response_at(replies)
    fr_finished = fr_at is not None
    fr_end = fr_at or (_parse_iso(ticket.get("resolved_at", ""))
                       if status in FINAL_STATUSES else now) or now
    fr_elapsed = (fr_end - created).total_seconds() / 60.0

    # ---- resolution (active time only — waiting clock is paused) ----
    waiting_seconds = float(ticket.get("waiting_seconds") or 0)
    if status == "waiting_on_customer":
        # The currently-elapsed waiting segment also doesn't count.
        seg_start = _parse_iso(ticket.get("status_changed_at", "")) or created
        waiting_seconds += max(0.0, (now - seg_start).total_seconds())
    res_finished = status in FINAL_STATUSES
    res_end = _parse_iso(ticket.get("resolved_at", "")) if res_finished else now
    res_end = res_end or now
    active_seconds = max(0.0, (res_end - created).total_seconds() - waiting_seconds)
    res_elapsed = active_seconds / 60.0

    fr_state = _timer_status(fr_elapsed, fr_target, fr_finished)
    res_state = _timer_status(res_elapsed, res_target, res_finished)
    overall = "breached" if "breached" in {fr_state, res_state} \
        else ("at_risk" if "at_risk" in {fr_state, res_state} else "on_track")

    return {
        "priority": priority,
        "first_response": {
            "target_minutes": fr_target,
            "elapsed_minutes": round(fr_elapsed, 2),
            "responded": fr_finished,
            "status": fr_state,
        },
        "resolution": {
            "target_minutes": res_target,
            "elapsed_minutes": round(res_elapsed, 2),
            "resolved": res_finished,
            "waiting_seconds": round(waiting_seconds, 1),
            "status": res_state,
        },
        "overall": overall,
    }


async def _record_breach_if_new(db, tenant_id: str, ticket: Dict, sla: Dict) -> bool:
    """SLA breach handling: the first time a live (non-final) ticket's
    snapshot reads breached, stamp `sla_breach_recorded_at` on the ticket
    and write an `sla_breached` audit event (+ stubbed supervisor
    notification). The stamp makes repeat snapshots idempotent — a breach
    is an event, not a state we re-report on every read. Best-effort:
    returns False instead of raising."""
    if ticket.get("status") in FINAL_STATUSES:
        return False
    if sla.get("overall") != "breached" or ticket.get("sla_breach_recorded_at"):
        return False
    try:
        now = _now_iso()
        await db.support_tickets.update_one(
            {"tenant_id": tenant_id, "id": ticket["id"]},
            {"$set": {"sla_breach_recorded_at": now, "updated_at": now}})
        ticket["sla_breach_recorded_at"] = now
        await _log_event(db, tenant_id, ticket["id"], "sla_breached", None,
                         {"first_response": sla["first_response"]["status"],
                          "resolution": sla["resolution"]["status"],
                          "priority": sla.get("priority")})
        # TODO(LIVE): notify the support supervisor (SMS/Slack webhook).
        print(f"[SUPPORT-TICKETS sla breach] tenant={tenant_id} ticket={ticket['id']} "
              f"priority={sla.get('priority')} (supervisor notification stubbed)")
        return True
    except Exception:
        return False


async def _draft_kb_article(db, tenant_id: str, ticket: Dict,
                            resolution_note: str, actor_id: Optional[str]) -> None:
    """Flywheel: turn a human resolution into a draft KB article the owner
    can review and publish into the knowledge base the AI support agent
    retrieves from. Idempotent per ticket. Best-effort (never raises)."""
    try:
        existing = await db.kb_article_drafts.find_one(
            {"tenant_id": tenant_id, "source_ticket_id": ticket["id"]})
        if existing:
            return
        now = _now_iso()
        await db.kb_article_drafts.insert_one({
            "id": _uuid(),
            "tenant_id": tenant_id,
            "source_ticket_id": ticket["id"],
            "title": ticket.get("subject") or "Support resolution",
            "question": ticket.get("description") or ticket.get("subject") or "",
            "answer": resolution_note,
            "category": ticket.get("category") or "",
            "status": "draft",
            "created_by": actor_id,
            "created_at": now,
            "updated_at": now,
        })
    except Exception:
        pass


def _ticket_summary(ticket: Dict) -> Dict:
    return {
        "id": ticket.get("id"),
        "subject": ticket.get("subject"),
        "status": ticket.get("status"),
        "priority": ticket.get("priority"),
        "assignee": ticket.get("assignee"),
        "category": ticket.get("category"),
        "created_at": ticket.get("created_at"),
        "updated_at": ticket.get("updated_at"),
    }


# ---------- create / list ----------

@router.post("/")
async def create_ticket(data: TicketIn, user: dict = Depends(require_tenant_user)):
    """Open a support ticket (any tenant user — the shop — can file one)."""
    db = get_db()
    now = _now_iso()
    doc = {
        "id": _uuid(),
        "tenant_id": user["tenant_id"],
        "subject": data.subject,
        "description": data.description,
        "priority": data.priority,
        "category": data.category,
        "status": "open",
        "assignee": None,
        "contact_name": data.contact_name,
        "contact_email": data.contact_email,
        "contact_phone": data.contact_phone,
        "created_by": user.get("id"),
        "created_at": now,
        "updated_at": now,
        "status_changed_at": now,
        "waiting_seconds": 0.0,
        "resolved_at": None,
    }
    await db.support_tickets.insert_one(doc)
    await _log_event(db, user["tenant_id"], doc["id"], "created", user.get("id"),
                     {"priority": data.priority, "category": data.category})
    doc.pop("_id", None)
    return {"ok": True, **_ticket_summary(doc)}


@router.get("/")
async def list_tickets(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    sla_status: Optional[str] = None,
    assignee: Optional[str] = None,
    user: dict = Depends(require_tenant_user),
):
    """Ticket queue. Filter with ?status=, ?priority=, ?sla_status=on_track|at_risk|breached, ?assignee=."""
    if status is not None and status not in STATUSES:
        raise HTTPException(400, f"Unknown status: {status}")
    if priority is not None and priority not in PRIORITIES:
        raise HTTPException(400, f"Unknown priority: {priority}")
    db = get_db()
    tenant_id = user["tenant_id"]
    query: Dict = {"tenant_id": tenant_id}
    if status:
        query["status"] = status
    if priority:
        query["priority"] = priority
    if assignee:
        query["assignee"] = assignee
    docs = await db.support_tickets.find(query, {"_id": 0}).sort("created_at", -1).to_list(1000)
    policies = await _get_policies(db, tenant_id)
    now = datetime.now(timezone.utc)
    out = []
    for d in docs:
        replies = await db.ticket_replies.find(
            {"tenant_id": tenant_id, "ticket_id": d["id"]}, {"_id": 0}).to_list(10000)
        summary = _ticket_summary(d)
        summary["sla"] = _sla_snapshot(d, replies, policies, now)
        if sla_status and summary["sla"]["overall"] != sla_status:
            continue
        out.append(summary)
    return {"tickets": out, "count": len(out)}


# ---------- SLA overview (declared before /{id} so the routes don't collide) ----------

@router.get("/sla/overview")
async def sla_overview(user: dict = Depends(require_tenant_user)):
    """SLA health for the tenant's queue: breach counts, at-risk list, and
    per-priority breach counts. Only non-closed tickets count as breached or
    at-risk; resolved tickets that finished past their target are reported
    separately as resolved_breached."""
    db = get_db()
    tenant_id = user["tenant_id"]
    docs = await db.support_tickets.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(10000)
    policies = await _get_policies(db, tenant_id)
    now = datetime.now(timezone.utc)

    counts = {"on_track": 0, "at_risk": 0, "breached": 0}
    breaches_by_priority = {p: 0 for p in PRIORITIES}
    at_risk_list: List[Dict] = []
    breached_list: List[Dict] = []
    resolved_breached = 0

    for d in docs:
        replies = await db.ticket_replies.find(
            {"tenant_id": tenant_id, "ticket_id": d["id"]}, {"_id": 0}).to_list(10000)
        sla = _sla_snapshot(d, replies, policies, now)
        # Breach handling: a breach seen by the monitoring surface is
        # recorded (stamped + audited) exactly once per ticket.
        await _record_breach_if_new(db, tenant_id, d, sla)
        summary = {**_ticket_summary(d), "sla": sla}
        if d.get("status") == "closed":
            continue
        if d.get("status") in FINAL_STATUSES:
            if sla["overall"] == "breached":
                resolved_breached += 1
            continue
        counts[sla["overall"]] += 1
        if sla["overall"] == "breached":
            breaches_by_priority[d.get("priority") or "medium"] += 1
            breached_list.append(summary)
        elif sla["overall"] == "at_risk":
            at_risk_list.append(summary)

    return {
        "counts": counts,
        "open_total": sum(counts.values()),
        "breaches_by_priority": breaches_by_priority,
        "resolved_breached": resolved_breached,
        "at_risk": at_risk_list,
        "breached": breached_list,
        "targets": policies,
    }


# ---------- KB drafts (flywheel) ----------

@router.get("/kb-drafts")
async def list_kb_drafts(user: dict = Depends(require_tenant_owner_or_admin)):
    """Draft knowledge-base articles generated from resolved tickets
    (owner/admin). Review/edit/publish flow lives with the knowledge-base
    UI; this is the review queue the flywheel feeds."""
    db = get_db()
    docs = await db.kb_article_drafts.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}).sort("created_at", -1).to_list(1000)
    return {"drafts": docs, "count": len(docs)}


# ---------- detail ----------

@router.get("/{ticket_id}")
async def get_ticket(ticket_id: str, user: dict = Depends(require_tenant_user)):
    """Ticket detail: summary + SLA snapshot + reply thread + event audit trail."""
    db = get_db()
    tenant_id = user["tenant_id"]
    ticket = await _get_ticket(db, tenant_id, ticket_id)
    replies = await db.ticket_replies.find(
        {"tenant_id": tenant_id, "ticket_id": ticket_id}, {"_id": 0}
    ).sort("created_at", 1).to_list(10000)
    events = await db.ticket_events.find(
        {"tenant_id": tenant_id, "ticket_id": ticket_id}, {"_id": 0}
    ).sort("at", 1).to_list(10000)
    policies = await _get_policies(db, tenant_id)
    sla = _sla_snapshot(ticket, replies, policies, datetime.now(timezone.utc))
    await _record_breach_if_new(db, tenant_id, ticket, sla)
    ticket.pop("_id", None)
    return {
        "ticket": ticket,
        "sla": sla,
        "replies": replies,
        "events": events,
        "reply_count": len(replies),
        "internal_note_count": sum(1 for r in replies if r.get("kind") == "internal"),
    }


# ---------- triage ----------

@router.patch("/{ticket_id}")
async def patch_ticket(ticket_id: str, data: TicketPatch,
                       user: dict = Depends(require_tenant_owner_or_admin)):
    """Triage a ticket: change status / priority / assignee (owner/admin only).
    Status moves follow the lifecycle; invalid jumps are rejected (409)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    ticket = await _get_ticket(db, tenant_id, ticket_id)
    patch = data.model_dump(exclude_unset=True)

    if patch.get("status") is not None:
        ticket = await _transition(db, ticket, patch["status"], user.get("id"))
    if patch.get("priority") is not None and patch["priority"] != ticket.get("priority"):
        old = ticket.get("priority")
        now = _now_iso()
        await db.support_tickets.update_one(
            {"tenant_id": tenant_id, "id": ticket_id},
            {"$set": {"priority": patch["priority"], "updated_at": now}})
        ticket["priority"] = patch["priority"]
        ticket["updated_at"] = now
        await _log_event(db, tenant_id, ticket_id, "priority_changed",
                         user.get("id"), {"from": old, "to": patch["priority"]})
    if "assignee" in patch and patch["assignee"] != ticket.get("assignee"):
        old = ticket.get("assignee")
        now = _now_iso()
        await db.support_tickets.update_one(
            {"tenant_id": tenant_id, "id": ticket_id},
            {"$set": {"assignee": patch["assignee"], "updated_at": now}})
        ticket["assignee"] = patch["assignee"]
        ticket["updated_at"] = now
        await _log_event(db, tenant_id, ticket_id, "assignee_changed",
                         user.get("id"), {"from": old, "to": patch["assignee"]})

    ticket.pop("_id", None)
    return {"ok": True, **_ticket_summary(ticket)}


# ---------- replies: public replies vs internal notes ----------

@router.post("/{ticket_id}/replies")
async def add_reply(ticket_id: str, data: ReplyIn, user: dict = Depends(require_tenant_user)):
    """Add a public reply or an internal note.

    - kind="public": visible to the customer (the shop).
    - kind="internal": staff-only note; customers may not post them.
    - author_role="staff": the platform support team (or the AI support
      agent, which posts here too). Only staff PUBLIC replies satisfy the
      first-response SLA timer.

    Auto-transitions: a public reply on a ``waiting_on_customer`` ticket
    moves it back to ``in_progress``; a public CUSTOMER reply on a
    ``resolved`` ticket re-opens it to ``in_progress``.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    ticket = await _get_ticket(db, tenant_id, ticket_id)

    if data.kind == "internal" and data.author_role != "staff":
        raise HTTPException(403, "Internal notes can only be posted by platform staff")
    if ticket.get("status") == "closed":
        raise HTTPException(409, "Ticket is closed; re-open it before replying")

    now = _now_iso()
    doc = {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "ticket_id": ticket_id,
        "body": data.body,
        "kind": data.kind,
        "author_role": data.author_role,
        "author_id": user.get("id"),
        "created_at": now,
    }
    await db.ticket_replies.insert_one(doc)
    await _log_event(db, tenant_id, ticket_id, "reply_added", user.get("id"),
                     {"kind": data.kind, "author_role": data.author_role})

    auto_status = None
    if data.kind == "public":
        if ticket.get("status") == "waiting_on_customer":
            ticket = await _transition(db, ticket, "in_progress", user.get("id"))
            auto_status = "in_progress"
        elif data.author_role == "customer" and ticket.get("status") == "resolved":
            ticket = await _transition(db, ticket, "in_progress", user.get("id"),
                                       allowed=False)  # reopen path, bypasses PATCH map
            auto_status = "in_progress"
            await _log_event(db, tenant_id, ticket_id, "reopened", user.get("id"),
                             {"reason": "customer replied after resolution"})

    doc.pop("_id", None)
    return {"ok": True, "auto_status": auto_status, **doc}


# ---------- resolve / reopen ----------

@router.post("/{ticket_id}/resolve")
async def resolve_ticket(ticket_id: str, data: ResolveIn,
                         user: dict = Depends(require_tenant_owner_or_admin)):
    """Mark a ticket resolved. An optional resolution_note is posted as a
    public staff reply so the customer always sees what was done."""
    db = get_db()
    tenant_id = user["tenant_id"]
    ticket = await _get_ticket(db, tenant_id, ticket_id)
    if ticket.get("status") == "resolved":
        raise HTTPException(409, "Ticket is already resolved")
    if ticket.get("status") == "closed":
        raise HTTPException(409, "Ticket is closed; re-open it before resolving")

    if data.resolution_note:
        now = _now_iso()
        reply = {
            "id": _uuid(),
            "tenant_id": tenant_id,
            "ticket_id": ticket_id,
            "body": data.resolution_note,
            "kind": "public",
            "author_role": "staff",
            "author_id": user.get("id"),
            "created_at": now,
        }
        await db.ticket_replies.insert_one(reply)
        await _log_event(db, tenant_id, ticket_id, "reply_added", user.get("id"),
                         {"kind": "public", "author_role": "staff",
                          "note": "resolution summary"})

    ticket = await _transition(db, ticket, "resolved", user.get("id"))
    await _log_event(db, tenant_id, ticket_id, "resolved", user.get("id"), {})

    # Store the human resolution on the ticket itself (the learning-loop
    # payload + the KB flywheel's source material).
    note = data.resolution_note or ""
    await db.support_tickets.update_one(
        {"tenant_id": tenant_id, "id": ticket_id},
        {"$set": {"resolution_note": note, "resolved_by": user.get("id")}})
    ticket["resolution_note"] = note
    ticket["resolved_by"] = user.get("id")

    # Flywheel: a resolution note becomes a draft KB article.
    if note:
        await _draft_kb_article(db, tenant_id, ticket, note, user.get("id"))

    # Learning loop: sync the resolved case to the hub archive
    # (OFF by default, best-effort — never blocks resolution).
    try:
        await hub.sync_ticket(db, tenant_id, ticket, note)
    except Exception:
        pass

    # Satisfaction pulse to the shop contact (best-effort).
    await dispatch_pulse(
        db, tenant_id, source_type="ticket", source_id=ticket_id,
        customer_name=ticket.get("contact_name") or "",
        customer_contact=ticket.get("contact_email") or ticket.get("contact_phone") or "",
        channel="email" if ticket.get("contact_email") else "sms")
    return {"ok": True, **_ticket_summary(ticket)}


@router.post("/{ticket_id}/reopen")
async def reopen_ticket(ticket_id: str, data: ReopenIn,
                        user: dict = Depends(require_tenant_owner_or_admin)):
    """Re-open a resolved or closed ticket back to in_progress (audited).
    The resolution clock resumes from the ticket's accumulated waiting time."""
    db = get_db()
    tenant_id = user["tenant_id"]
    ticket = await _get_ticket(db, tenant_id, ticket_id)
    if ticket.get("status") not in FINAL_STATUSES:
        raise HTTPException(409, "Only resolved or closed tickets can be re-opened")

    ticket = await _transition(db, ticket, "in_progress", user.get("id"), allowed=False)
    await _log_event(db, tenant_id, ticket_id, "reopened", user.get("id"),
                     {"reason": data.reason} if data.reason else {})
    return {"ok": True, **_ticket_summary(ticket)}


# ---------- SLA policy management ----------

@router.get("/sla/policies")
async def get_sla_policies(user: dict = Depends(require_tenant_owner_or_admin)):
    """Read the tenant's SLA targets per priority (defaults seeded on first read)."""
    db = get_db()
    return {"ok": True, "targets": await _get_policies(db, user["tenant_id"])}


@router.patch("/sla/policies")
async def set_sla_policies(data: SlaPoliciesIn,
                           user: dict = Depends(require_tenant_owner_or_admin)):
    """Tune per-priority SLA targets (minutes). Partial updates allowed."""
    db = get_db()
    tenant_id = user["tenant_id"]
    targets = await _get_policies(db, tenant_id)  # seeds defaults first
    patch = data.model_dump(exclude_unset=True)
    for prio, vals in patch.items():
        if prio not in PRIORITIES or not vals:
            continue
        for key in ("first_response", "resolution"):
            if key in vals and vals[key] is not None:
                if vals[key] <= 0:
                    raise HTTPException(400, f"{prio}.{key} must be positive minutes")
                targets[prio][key] = vals[key]
    now = _now_iso()
    await db.sla_policies.update_one(
        {"tenant_id": tenant_id},
        {"$set": {"targets": targets, "updated_at": now}})
    return {"ok": True, "targets": targets}
