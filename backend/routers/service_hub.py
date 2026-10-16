"""Service-hub sync status + manual re-sync (operator surface for
services/service_hub.py).

The hub sync itself is automatic and best-effort: escalations and ticket
resolutions push to the central hub archive as they happen (hooks in
conversation_intel.py / support_tickets.py), stamped `hub_sync` on the
source doc. This router exists so an owner can SEE the state and RETRY
the backlog after a hub outage:

- GET  /api/service-hub/status       — configured? (env, OFF/demo default),
                                       per-tenant synced / pending / error
                                       counts across escalations + tickets.
- POST /api/service-hub/sync-pending — push every not-yet-synced
                                       escalation and every resolved,
                                       not-yet-synced ticket for the
                                       tenant. Safe to re-run: pushes are
                                       idempotency-keyed at the hub.

When the hub is not configured both endpoints still work — status reports
demo mode and sync-pending is a no-op — so the UI never breaks pre-launch.
"""
from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, Depends

from db import get_db
from security import require_tenant_user, require_tenant_owner_or_admin
from services import service_hub as hub

router = APIRouter(prefix="/api/service-hub", tags=["service-hub"])


async def _counts(db, tenant_id: str) -> Dict:
    out = {}
    for label, coll_name in (("escalations", "escalations"),
                             ("support_tickets", "support_tickets")):
        docs = await getattr(db, coll_name).find(
            {"tenant_id": tenant_id}, {"_id": 0, "hub_sync": 1, "status": 1}).to_list(10000)
        counts = {"total": len(docs), "synced": 0, "error": 0, "pending": 0}
        for d in docs:
            state = (d.get("hub_sync") or {}).get("status")
            if state == "synced":
                counts["synced"] += 1
            elif state == "error":
                counts["error"] += 1
            else:
                counts["pending"] += 1
        out[label] = counts
    return out


@router.get("/status")
async def status(user: dict = Depends(require_tenant_user)):
    """Hub configuration (no secrets — only whether the key is set) and
    this tenant's archive-sync counts."""
    db = get_db()
    s = hub.hub_settings()
    return {
        "configured": s["configured"],
        "enabled": s["enabled"],
        "url": s["url"] or None,
        "api_key_set": s["api_key_set"],
        "mode": "live" if s["configured"] else "demo (off — set SERVICE_HUB_ENABLED, SERVICE_HUB_URL, SERVICE_HUB_API_KEY)",
        "counts": await _counts(db, user["tenant_id"]),
    }


@router.post("/sync-pending")
async def sync_pending(user: dict = Depends(require_tenant_owner_or_admin)):
    """Retry the tenant's backlog: unsynced/errored escalations (any
    status — open escalations sync too, then re-sync on resolution) and
    resolved unsynced tickets. Owner/admin only."""
    db = get_db()
    tenant_id = user["tenant_id"]
    if not hub.hub_settings()["configured"]:
        return {"ok": True, "configured": False,
                "escalations": {"attempted": 0, "synced": 0},
                "tickets": {"attempted": 0, "synced": 0},
                "note": "Service hub not configured (demo mode) — nothing to sync."}

    esc_docs = await db.escalations.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(10000)
    # Due for a push: never synced / last push errored, OR resolved since
    # the last successful push (hub_sync.for_status tracks what the hub
    # has actually seen — the resolution is the learning-loop payload).
    esc_todo = [d for d in esc_docs
                if (d.get("hub_sync") or {}).get("status") != "synced"
                or (d.get("status") == "resolved"
                    and (d.get("hub_sync") or {}).get("for_status") != "resolved")]
    esc_synced = 0
    for esc in esc_todo:
        res = await hub.sync_escalation(db, tenant_id, esc)
        if res.get("synced"):
            esc_synced += 1

    ticket_docs = await db.support_tickets.find(
        {"tenant_id": tenant_id}, {"_id": 0}).to_list(10000)
    ticket_todo = [d for d in ticket_docs
                   if d.get("status") in {"resolved", "closed"}
                   and (d.get("hub_sync") or {}).get("status") != "synced"]
    ticket_synced = 0
    for ticket in ticket_todo:
        res = await hub.sync_ticket(db, tenant_id, ticket)
        if res.get("synced"):
            ticket_synced += 1

    return {
        "ok": True, "configured": True,
        "escalations": {"attempted": len(esc_todo), "synced": esc_synced},
        "tickets": {"attempted": len(ticket_todo), "synced": ticket_synced},
        "counts": await _counts(db, tenant_id),
    }
