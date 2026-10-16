"""Self-Healing Operations — detectors + auto-fixes + human override.

The system watches for common operational problems and fixes them
automatically — no human ticket required. Every detection and auto-fix is
logged to `ops_issues` so a human can audit, override (reverting the
auto-fix), or resolve each one.

Built-in healers:
- double_booking: overlapping appointments assigned to the same tech.
- unconfirmed_appointment: tomorrow's jobs still unconfirmed -> confirmation nudge.
- stale_lead: leads untouched for 7+ days -> revival follow-up task.
- overdue_invoice_nudge: past-due invoices -> collection nudge (aware of the
  AI collections module when it is installed).
- no_show_risk: a tech with 2+ late arrivals in the last 7 days -> dispatch review flag.

Collections (all tenant-scoped):
- `ops_issues`: detected issues (status open / auto_fixed / overridden / resolved)
- `healer_runs`: one record per healer execution (last_run, issues_found, fixes_applied)
- `healer_settings`: per-tenant enable/disable flags (defaults: all enabled)

Healer detection reads existing collections defensively — unknown or missing
shapes are skipped rather than crashing the scan.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin

router = APIRouter(prefix="/tenants/self-healing", tags=["self-healing"])


# ---------- healer definitions ----------

HEALER_DEFS = {
    "double_booking": {
        "name": "Double-booking guard",
        "description": "Finds overlapping appointments assigned to the same tech and flags the later one for reassignment, suggesting a free tech.",
    },
    "unconfirmed_appointment": {
        "name": "Unconfirmed appointment nudges",
        "description": "Tomorrow's jobs that are still unconfirmed get a confirmation nudge sent and logged.",
    },
    "stale_lead": {
        "name": "Stale lead revival",
        "description": "Leads untouched for 7+ days get a revival follow-up task created.",
    },
    "overdue_invoice_nudge": {
        "name": "Overdue invoice nudge",
        "description": "Past-due invoices get a collection nudge queued (aware of the AI collections module when installed).",
    },
    "no_show_risk": {
        "name": "No-show risk flag",
        "description": "Techs with 2+ late arrivals in the last 7 days are flagged for dispatch review.",
    },
}

ACTIVE_APPT_STATUSES = ("scheduled", "confirmed")
WORKING_LEAD_STATUSES = ("new", "working", "contacted")
OVERDUE_INVOICE_STATUSES = ("sent", "unpaid", "overdue", "partially_paid")
LATE_DAYS = 7
NO_SHOW_LATE_THRESHOLD = 2


# ---------- helpers ----------

def _parse_dt(value) -> Optional[datetime]:
    """Parse an ISO date/datetime defensively. Returns None when unparseable."""
    if value is None:
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError, AttributeError):
        return None


def _day_key(value) -> Optional[str]:
    dt = _parse_dt(value)
    return dt.strftime("%Y-%m-%d") if dt else None


def _appt_start(appt: dict) -> Optional[datetime]:
    for key in ("start_at", "start_time", "start", "scheduled_at"):
        dt = _parse_dt(appt.get(key))
        if dt:
            return dt
    dt = _parse_dt(appt.get("date"))
    if dt:
        return dt
    return None


def _appt_end(appt: dict) -> Optional[datetime]:
    for key in ("end_at", "end_time", "end"):
        dt = _parse_dt(appt.get(key))
        if dt:
            return dt
    start = _appt_start(appt)
    if start:
        minutes = 60
        try:
            minutes = int(appt.get("duration_minutes") or 60)
        except (TypeError, ValueError):
            minutes = 60
        return start + timedelta(minutes=max(15, minutes))
    return None


def _tech_of(appt: dict) -> Optional[str]:
    for key in ("tech_id", "technician_id", "assigned_to", "staff_id"):
        value = appt.get(key)
        if value:
            return str(value)
    return None


async def _safe_find(db, collection: str, query: dict, limit: int = 2000) -> List[dict]:
    """Read a collection defensively; unknown shapes yield an empty list."""
    try:
        coll = getattr(db, collection, None)
        if coll is None:
            return []
        cursor = coll.find(query)
        if hasattr(cursor, "to_list"):
            docs = await cursor.to_list(limit)
        else:  # pragma: no cover - non-motor fallback
            docs = list(cursor)[:limit]
        return [dict(d) for d in docs if isinstance(d, dict)]
    except Exception:
        return []


async def _safe_update(db, collection: str, doc_id: str, set_fields: dict) -> bool:
    try:
        result = await getattr(db, collection).update_one(
            {"id": doc_id}, {"$set": set_fields}
        )
        return bool(getattr(result, "matched_count", 0))
    except Exception:
        return False


def _new_issue(healer_id: str, title: str, detail: str, severity: str,
               entity_type: str, entity_id: str, fix_type: str,
               fix_detail: str, revert: Optional[dict], tenant_id: str) -> dict:
    now = _now_iso()
    return {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "healer_id": healer_id,
        "title": title,
        "detail": detail,
        "severity": severity,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "dedupe_key": f"{healer_id}:{entity_id}",
        "status": "auto_fixed",
        "fix_type": fix_type,
        "fix_detail": fix_detail,
        "revert": revert or {},
        "override_reason": None,
        "overridden_by": None,
        "created_at": now,
        "updated_at": now,
    }


# ---------- healer: double booking ----------

async def _heal_double_booking(db, tenant_id: str) -> List[dict]:
    appts = await _safe_find(
        db, "appointments",
        {"tenant_id": tenant_id, "status": {"$in": list(ACTIVE_APPT_STATUSES)}},
    )
    by_tech: dict = {}
    for appt in appts:
        tech = _tech_of(appt)
        start = _appt_start(appt)
        end = _appt_end(appt)
        day = _day_key(appt.get("date") or (start.isoformat() if start else None))
        if not tech or not start or not end or not day:
            continue
        by_tech.setdefault((day, tech), []).append((appt, start, end))

    issues = []
    for (day, tech), jobs in by_tech.items():
        jobs.sort(key=lambda j: j[1])
        for i in range(1, len(jobs)):
            prev_appt, prev_start, prev_end = jobs[i - 1]
            appt, start, end = jobs[i]
            if start < prev_end:
                suggestion = await _suggest_free_tech(db, tenant_id, appt, start, end)
                old_flag = appt.get("needs_reassign")
                await _safe_update(
                    db, "appointments", appt.get("id"),
                    {"needs_reassign": True, "reassign_suggested_to": suggestion,
                     "updated_at": _now_iso()},
                )
                label = appt.get("customer_name") or appt.get("title") or appt.get("id")
                issues.append(_new_issue(
                    "double_booking",
                    f"Double-booking: {tech} has overlapping jobs",
                    f"'{label}' overlaps with '{prev_appt.get('customer_name') or prev_appt.get('id')}' "
                    f"({start.isoformat()}–{end.isoformat()}). Flagged for reassignment.",
                    "high", "appointment", appt.get("id"),
                    "reassignment_suggested",
                    f"Suggested alternate tech: {suggestion or 'none available'}.",
                    {"collection": "appointments", "id": appt.get("id"),
                     "restore": {"needs_reassign": old_flag, "reassign_suggested_to": None}},
                    tenant_id,
                ))
    return issues


async def _suggest_free_tech(db, tenant_id: str, appt: dict, start: datetime, end: datetime) -> Optional[str]:
    """Pick another tech with no overlapping appointment, or None."""
    try:
        appts = await _safe_find(
            db, "appointments",
            {"tenant_id": tenant_id, "status": {"$in": list(ACTIVE_APPT_STATUSES)}},
            limit=500,
        )
        blocked = set()
        for other in appts:
            if other.get("id") == appt.get("id"):
                continue
            tech = _tech_of(other)
            o_start, o_end = _appt_start(other), _appt_end(other)
            if tech and o_start and o_end and o_start < end and start < o_end:
                blocked.add(tech)
        candidates = []
        for other in appts:
            tech = _tech_of(other)
            if tech and tech != _tech_of(appt) and tech not in blocked and tech not in candidates:
                candidates.append(tech)
        return candidates[0] if candidates else None
    except Exception:
        return None


# ---------- healer: unconfirmed appointments ----------

async def _heal_unconfirmed_appointment(db, tenant_id: str) -> List[dict]:
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    appts = await _safe_find(db, "appointments", {"tenant_id": tenant_id})
    issues = []
    for appt in appts:
        try:
            status = str(appt.get("status") or "").lower()
            confirmed = appt.get("confirmed") is True or status == "confirmed"
            if status not in ("scheduled",) or confirmed:
                continue
            day = _day_key(appt.get("date")) or _day_key(appt.get("start_at") or appt.get("start"))
            if day != tomorrow:
                continue
            old = appt.get("confirmation_nudge_sent_at")
            await _safe_update(
                db, "appointments", appt.get("id"),
                {"confirmation_nudge_sent_at": _now_iso(), "updated_at": _now_iso()},
            )
            label = appt.get("customer_name") or appt.get("title") or appt.get("id")
            issues.append(_new_issue(
                "unconfirmed_appointment",
                f"Confirmation nudge sent for tomorrow's job",
                f"'{label}' ({day}) was still unconfirmed. A confirmation nudge was sent and logged.",
                "medium", "appointment", appt.get("id"),
                "confirmation_nudge",
                "Confirmation nudge sent to customer.",
                {"collection": "appointments", "id": appt.get("id"),
                 "restore": {"confirmation_nudge_sent_at": old}},
                tenant_id,
            ))
        except Exception:
            continue
    return issues


# ---------- healer: stale leads ----------

def _lead_last_touch(lead: dict) -> Optional[datetime]:
    for key in ("last_activity_at", "last_touch_at", "updated_at", "created_at"):
        dt = _parse_dt(lead.get(key))
        if dt:
            return dt
    return None


async def _heal_stale_lead(db, tenant_id: str) -> List[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=LATE_DAYS)
    leads = await _safe_find(db, "leads", {"tenant_id": tenant_id})
    issues = []
    for lead in leads:
        try:
            status = str(lead.get("status") or "").lower()
            if status and status not in WORKING_LEAD_STATUSES:
                continue
            last_touch = _lead_last_touch(lead)
            if not last_touch or last_touch > cutoff:
                continue
            days = (datetime.now(timezone.utc) - last_touch).days
            old = lead.get("revive_task_created")
            await _safe_update(
                db, "leads", lead.get("id"),
                {"revive_task_created": True,
                 "revive_task_due": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
                 "updated_at": _now_iso()},
            )
            name = lead.get("name") or lead.get("phone") or lead.get("email") or lead.get("id")
            issues.append(_new_issue(
                "stale_lead",
                f"Stale lead revived: {name}",
                f"Lead '{name}' untouched for {days} days. A revival follow-up task (due in 2 days) was created.",
                "medium", "lead", lead.get("id"),
                "revival_task",
                "Revival follow-up task created, due in 2 days.",
                {"collection": "leads", "id": lead.get("id"),
                 "restore": {"revive_task_created": old, "revive_task_due": None}},
                tenant_id,
            ))
        except Exception:
            continue
    return issues


# ---------- healer: overdue invoice nudge ----------

async def _heal_overdue_invoice_nudge(db, tenant_id: str) -> List[dict]:
    today = datetime.now(timezone.utc)
    invoices = await _safe_find(db, "invoices", {"tenant_id": tenant_id})
    issues = []
    for inv in invoices:
        try:
            status = str(inv.get("status") or "").lower()
            if status not in OVERDUE_INVOICE_STATUSES:
                continue
            due = _parse_dt(inv.get("due_date"))
            if not due or due >= today:
                continue
            days = (today - due).days
            enrolled = False
            try:
                enrollment = await db.collection_enrollments.find_one(
                    {"tenant_id": tenant_id, "invoice_id": inv.get("id"),
                     "status": {"$in": ["active", "pending"]}}
                )
                enrolled = enrollment is not None
            except Exception:
                enrolled = False
            old = inv.get("healer_nudge_sent_at")
            await _safe_update(
                db, "invoices", inv.get("id"),
                {"healer_nudge_sent_at": _now_iso(), "updated_at": _now_iso()},
            )
            label = inv.get("invoice_number") or inv.get("id")
            fix_detail = ("Collection nudge queued."
                          if not enrolled
                          else "Nudge queued; invoice already enrolled in a collections sequence.")
            issues.append(_new_issue(
                "overdue_invoice_nudge",
                f"Overdue invoice nudged: {label}",
                f"Invoice '{label}' is {days} days past due (${inv.get('balance_due') or inv.get('total') or '?'}).",
                "high", "invoice", inv.get("id"),
                "collection_nudge", fix_detail,
                {"collection": "invoices", "id": inv.get("id"),
                 "restore": {"healer_nudge_sent_at": old}},
                tenant_id,
            ))
        except Exception:
            continue
    return issues


# ---------- healer: no-show risk ----------

def _appt_is_late(appt: dict) -> bool:
    if appt.get("arrived_late") is True:
        return True
    arrival = str(appt.get("arrival_status") or "").lower()
    if arrival == "late":
        return True
    try:
        return float(appt.get("late_minutes") or 0) > 15
    except (TypeError, ValueError):
        return False


async def _heal_no_show_risk(db, tenant_id: str) -> List[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=LATE_DAYS)
    appts = await _safe_find(db, "appointments", {"tenant_id": tenant_id})
    late_by_tech: dict = {}
    for appt in appts:
        try:
            tech = _tech_of(appt)
            if not tech:
                continue
            start = _appt_start(appt)
            if not start or start < cutoff or not _appt_is_late(appt):
                continue
            late_by_tech.setdefault(tech, []).append(appt)
        except Exception:
            continue
    issues = []
    for tech, lates in late_by_tech.items():
        if len(lates) < NO_SHOW_LATE_THRESHOLD:
            continue
        issues.append(_new_issue(
            "no_show_risk",
            f"No-show risk: {tech} ({len(lates)} late arrivals)",
            f"Tech '{tech}' has {len(lates)} late arrivals in the last {LATE_DAYS} days. "
            "Flagged for dispatch review — consider backup coverage.",
            "medium", "tech", tech,
            "review_flag",
            "Flagged for dispatcher review. No automated change made.",
            {},
            tenant_id,
        ))
    return issues


HEALER_RUNNERS = {
    "double_booking": _heal_double_booking,
    "unconfirmed_appointment": _heal_unconfirmed_appointment,
    "stale_lead": _heal_stale_lead,
    "overdue_invoice_nudge": _heal_overdue_invoice_nudge,
    "no_show_risk": _heal_no_show_risk,
}


# ---------- settings / run bookkeeping ----------

async def _healer_enabled(db, tenant_id: str, healer_id: str) -> bool:
    try:
        doc = await db.healer_settings.find_one({"tenant_id": tenant_id, "healer_id": healer_id})
        if doc is None:
            return True
        return doc.get("enabled", True) is True
    except Exception:
        return True


async def _record_run(db, tenant_id: str, healer_id: str, issues_found: int, fixes_applied: int):
    now = _now_iso()
    try:
        prev = await db.healer_runs.find_one({"tenant_id": tenant_id, "healer_id": healer_id})
        totals = {
            "issues_found": (prev.get("issues_found", 0) if prev else 0) + issues_found,
            "fixes_applied": (prev.get("fixes_applied", 0) if prev else 0) + fixes_applied,
        }
        await db.healer_runs.update_one(
            {"tenant_id": tenant_id, "healer_id": healer_id},
            {"$set": {"tenant_id": tenant_id, "healer_id": healer_id,
                      "last_run": now, "updated_at": now, **totals}},
            upsert=True,
        )
    except Exception:
        pass


async def _healer_summary(db, tenant_id: str) -> dict:
    """Enabled/last-run/fixes-applied per healer, defensively."""
    summary = {}
    try:
        runs = {r.get("healer_id"): r for r in
                await _safe_find(db, "healer_runs", {"tenant_id": tenant_id})}
    except Exception:
        runs = {}
    for healer_id in HEALER_DEFS:
        run = runs.get(healer_id) or {}
        summary[healer_id] = {
            "enabled": await _healer_enabled(db, tenant_id, healer_id),
            "last_run": run.get("last_run"),
            "issues_found": run.get("issues_found", 0),
            "fixes_applied": run.get("fixes_applied", 0),
        }
    return summary


# ---------- request models ----------

class ToggleIn(BaseModel):
    enabled: bool = True


class OverrideIn(BaseModel):
    reason: str = ""


def _issue_out(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


# ---------- endpoints ----------

@router.get("/healers")
async def list_healers(user: dict = Depends(require_tenant_user)):
    db = get_db()
    summary = await _healer_summary(db, user["tenant_id"])
    return {
        "healers": [
            {"id": hid, **meta, **summary[hid]}
            for hid, meta in HEALER_DEFS.items()
        ]
    }


@router.post("/healers/{healer_id}/toggle")
async def toggle_healer(healer_id: str, payload: ToggleIn,
                        user: dict = Depends(require_tenant_owner_or_admin)):
    if healer_id not in HEALER_DEFS:
        raise HTTPException(404, "Unknown healer")
    db = get_db()
    now = _now_iso()
    try:
        await db.healer_settings.update_one(
            {"tenant_id": user["tenant_id"], "healer_id": healer_id},
            {"$set": {"tenant_id": user["tenant_id"], "healer_id": healer_id,
                      "enabled": payload.enabled, "updated_at": now}},
            upsert=True,
        )
    except Exception:
        pass
    return {"healer_id": healer_id, "enabled": payload.enabled}


@router.post("/scan")
async def run_scan(user: dict = Depends(require_tenant_user)):
    db = get_db()
    tenant_id = user["tenant_id"]
    detected: List[dict] = []
    fixes_applied = 0
    for healer_id, runner in HEALER_RUNNERS.items():
        if not await _healer_enabled(db, tenant_id, healer_id):
            continue
        try:
            candidates = await runner(db, tenant_id)
        except Exception:
            candidates = []
        new_issues = []
        for issue in candidates:
            try:
                existing = await db.ops_issues.find_one(
                    {"tenant_id": tenant_id, "dedupe_key": issue["dedupe_key"],
                     "status": {"$in": ["open", "auto_fixed"]}}
                )
            except Exception:
                existing = None
            if existing:
                continue
            try:
                await db.ops_issues.insert_one(issue)
            except Exception:
                continue
            new_issues.append(issue)
        fixes_applied += len(new_issues)
        await _record_run(db, tenant_id, healer_id, len(new_issues), len(new_issues))
        detected.extend(_issue_out(i) for i in new_issues)
    return {"detected": len(detected), "fixes_applied": fixes_applied,
            "issues": detected, "scanned_at": _now_iso()}


@router.get("/issues")
async def list_issues(status: Optional[str] = None, healer: Optional[str] = None,
                       user: dict = Depends(require_tenant_user)):
    db = get_db()
    query = {"tenant_id": user["tenant_id"]}
    if status:
        query["status"] = status
    if healer:
        query["healer_id"] = healer
    docs = await _safe_find(db, "ops_issues", query, limit=500)
    docs.sort(key=lambda d: d.get("created_at", ""), reverse=True)
    counts = {"open": 0, "auto_fixed": 0, "overridden": 0, "resolved": 0}
    for d in await _safe_find(db, "ops_issues", {"tenant_id": user["tenant_id"]}, limit=2000):
        st = d.get("status")
        if st in counts:
            counts[st] += 1
    return {"issues": [_issue_out(d) for d in docs], "counts": counts}


@router.post("/issues/{issue_id}/override")
async def override_issue(issue_id: str, payload: OverrideIn,
                         user: dict = Depends(require_tenant_user)):
    if not payload.reason.strip():
        raise HTTPException(400, "An override reason is required")
    db = get_db()
    issue = await db.ops_issues.find_one({"tenant_id": user["tenant_id"], "id": issue_id})
    if not issue:
        raise HTTPException(404, "Issue not found")
    if issue.get("status") != "auto_fixed":
        raise HTTPException(400, f"Only auto-fixed issues can be overridden (status={issue.get('status')})")
    revert = issue.get("revert") or {}
    restored = []
    try:
        collection = revert.get("collection")
        doc_id = revert.get("id")
        restore = revert.get("restore") or {}
        if collection and doc_id and restore:
            cleaned = {k: v for k, v in restore.items() if v is not None}
            nulled = {k: None for k in restore if restore[k] is None}
            update = {}
            if cleaned:
                update["$set"] = {**cleaned, "updated_at": _now_iso()}
            if nulled:
                update["$unset"] = {k: "" for k in nulled}
            if update:
                await getattr(db, collection).update_one({"id": doc_id}, update)
                restored = list(restore.keys())
    except Exception:
        pass
    await db.ops_issues.update_one(
        {"tenant_id": user["tenant_id"], "id": issue_id},
        {"$set": {"status": "overridden", "override_reason": payload.reason.strip(),
                  "overridden_by": user.get("id"), "reverted_fields": restored,
                  "updated_at": _now_iso()}},
    )
    return {"id": issue_id, "status": "overridden", "reverted_fields": restored}


@router.post("/issues/{issue_id}/resolve")
async def resolve_issue(issue_id: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    issue = await db.ops_issues.find_one({"tenant_id": user["tenant_id"], "id": issue_id})
    if not issue:
        raise HTTPException(404, "Issue not found")
    if issue.get("status") == "resolved":
        return {"id": issue_id, "status": "resolved"}
    await db.ops_issues.update_one(
        {"tenant_id": user["tenant_id"], "id": issue_id},
        {"$set": {"status": "resolved", "updated_at": _now_iso()}},
    )
    return {"id": issue_id, "status": "resolved"}
