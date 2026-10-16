"""Service-hub adapter: sync Momo's escalated conversations and human
resolutions to the central customer-service hub (ccempireproject100K/
service-hub) — the empire-wide escalation archive that powers the
learning loop (every human-resolved case becomes training context so the
AI needs humans less over time).

Design (mirrors services/accounting.py + routers/bookkeeping_link.py):
    * Provider-abstracted: `ServiceHubClient` is the HTTP provider; the
      payload builders and sync helpers depend only on its contract.
    * Env-configured, OFF by default: nothing happens unless
      SERVICE_HUB_ENABLED=1 AND SERVICE_HUB_URL AND SERVICE_HUB_API_KEY
      are set. Unconfigured == demo mode == sync helpers return
      {"synced": False, "reason": "not_configured"} and stamp NOTHING —
      zero behavior change on the live conversation/ticket paths.
    * Best-effort, always: sync helpers NEVER raise. Network failures are
      retried (bounded, with backoff), then logged and stamped onto the
      source doc as `hub_sync: {status: "error", error, at}` so the
      operator endpoint (POST /api/service-hub/sync-pending) can retry
      them later. A hub outage can never break a live conversation.
    * Idempotent: every push carries an Idempotency-Key derived from
      (tenant, record type, source id, status) — re-syncing the same
      record state never creates a duplicate archive entry.

Receiving contract (hub side, POST {SERVICE_HUB_URL}/api/archive/escalations):
    Headers: Authorization: Bearer <SERVICE_HUB_API_KEY>
             Idempotency-Key: <sha256 hex>
             Content-Type: application/json
    Body:    ArchiveRecord (schema "momo.service_escalation.v1") — see
             build_archive_record(). The hub answers 200/201 with
             {"id": <archive id>}; anything else is an error.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

from models import _now_iso

log = logging.getLogger("service_hub")

SCHEMA = "momo.service_escalation.v1"
ARCHIVE_PATH = "/api/archive/escalations"
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (0.5, 1.5)  # between attempts 1->2 and 2->3


class ServiceHubError(Exception):
    """Anything the hub could not do (network, auth, API error)."""


# ---------- configuration (env, OFF/demo by default) ----------

def hub_settings() -> Dict[str, Any]:
    """Read hub config from the environment. `configured` is True only when
    the operator has explicitly enabled the sync AND supplied endpoint+key."""
    enabled = (os.getenv("SERVICE_HUB_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}
    url = (os.getenv("SERVICE_HUB_URL") or "").strip().rstrip("/")
    api_key = (os.getenv("SERVICE_HUB_API_KEY") or "").strip()
    try:
        timeout = float(os.getenv("SERVICE_HUB_TIMEOUT_SECONDS") or 10.0)
    except ValueError:
        timeout = 10.0
    return {
        "enabled": enabled,
        "url": url,
        "api_key_set": bool(api_key),
        "timeout_seconds": timeout,
        "configured": bool(enabled and url and api_key),
    }


def _api_key() -> str:
    return (os.getenv("SERVICE_HUB_API_KEY") or "").strip()


def idempotency_key(*parts: str) -> str:
    """Deterministic idempotency key from tenant + record + state."""
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


# ---------- provider ----------

class ServiceHubClient:
    """HTTP provider for the central service hub's archive endpoint."""

    name = "service-hub"

    def __init__(self, base_url: str, api_key: str, timeout_seconds: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def push_archive_record(self, record: Dict[str, Any], *, idem_key: str) -> str:
        """POST one archive record. Returns the hub archive id.
        Raises ServiceHubError on any failure (caller handles retries)."""
        url = f"{self.base_url}{ARCHIVE_PATH}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Idempotency-Key": idem_key,
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(url, json=record, headers=headers)
        except httpx.HTTPError as e:
            raise ServiceHubError(f"hub unreachable: {e}") from e
        if resp.status_code not in (200, 201):
            raise ServiceHubError(
                f"hub returned {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except ValueError:
            return ""
        return str(data.get("id") or data.get("archive_id") or "")


def _client_from_env() -> Optional[ServiceHubClient]:
    s = hub_settings()
    if not s["configured"]:
        return None
    return ServiceHubClient(s["url"], _api_key(), s["timeout_seconds"])


# ---------- payload builders ----------

def build_archive_record(
    *,
    tenant_id: str,
    record_type: str,  # "escalation" | "support_ticket"
    source_id: str,
    status: str,  # "open" | "resolved"
    channel: str = "",
    customer_name: str = "",
    thread_id: Optional[str] = None,
    rule_key: Optional[str] = None,
    severity: str = "",
    priority: str = "",
    subject: str = "",
    reason: str = "",
    transcript: Optional[List[Dict[str, Any]]] = None,
    resolution_note: str = "",
    resolved_by: Optional[str] = None,
    resolved_at: Optional[str] = None,
    created_at: str = "",
    updated_at: str = "",
) -> Dict[str, Any]:
    """The archive record shape — the contract with the hub (see module
    docstring). Resolution is null until a human has resolved the case;
    that pair (escalation + human resolution) is the learning-loop unit."""
    resolution = None
    if status == "resolved":
        resolution = {
            "note": resolution_note or "",
            "resolved_by": resolved_by,
            "resolved_at": resolved_at,
        }
    return {
        "schema": SCHEMA,
        "source": "momo",
        "tenant_id": tenant_id,
        "record_type": record_type,
        "source_id": source_id,
        "thread_id": thread_id,
        "channel": channel or "",
        "customer_name": customer_name or "",
        "rule_key": rule_key,
        "severity": severity or "",
        "priority": priority or "",
        "subject": subject or "",
        "reason": reason or "",
        "transcript": transcript or [],
        "status": status,
        "resolution": resolution,
        "created_at": created_at or "",
        "updated_at": updated_at or "",
    }


def escalation_record(tenant_id: str, esc: Dict[str, Any],
                      transcript: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Build an archive record from a conversation_intel `escalations` doc."""
    return build_archive_record(
        tenant_id=tenant_id,
        record_type="escalation",
        source_id=str(esc.get("id") or ""),
        status="resolved" if esc.get("status") == "resolved" else "open",
        channel=esc.get("channel") or "",
        customer_name=esc.get("customer_name") or "",
        thread_id=esc.get("thread_id"),
        rule_key=esc.get("rule_key"),
        severity=esc.get("severity") or "",
        reason=esc.get("reason") or "",
        transcript=transcript if transcript is not None else esc.get("transcript") or [],
        resolution_note=esc.get("resolution_note") or "",
        resolved_by=esc.get("resolved_by"),
        resolved_at=esc.get("resolved_at"),
        created_at=esc.get("created_at") or "",
        updated_at=esc.get("updated_at") or esc.get("resolved_at") or esc.get("created_at") or "",
    )


def ticket_record(tenant_id: str, ticket: Dict[str, Any],
                  resolution_note: str = "") -> Dict[str, Any]:
    """Build an archive record from a support_tickets doc (Layer-1 human
    resolutions are learning-loop gold, same as Layer-2 escalations)."""
    return build_archive_record(
        tenant_id=tenant_id,
        record_type="support_ticket",
        source_id=str(ticket.get("id") or ""),
        status="resolved" if ticket.get("status") in {"resolved", "closed"} else "open",
        customer_name=ticket.get("contact_name") or "",
        severity="",
        priority=ticket.get("priority") or "",
        subject=ticket.get("subject") or "",
        reason=ticket.get("description") or "",
        resolution_note=resolution_note or ticket.get("resolution_note") or "",
        resolved_by=ticket.get("resolved_by"),
        resolved_at=ticket.get("resolved_at"),
        created_at=ticket.get("created_at") or "",
        updated_at=ticket.get("updated_at") or "",
    )


# ---------- sync engine (best-effort, never raises) ----------

async def _push_with_retries(client: ServiceHubClient, record: Dict[str, Any],
                             idem_key: str) -> str:
    last_err: Optional[Exception] = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return await client.push_archive_record(record, idem_key=idem_key)
        except ServiceHubError as e:
            last_err = e
            if attempt < MAX_ATTEMPTS - 1:
                await asyncio.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
    raise ServiceHubError(str(last_err) if last_err else "hub push failed")


async def _sync_record(db, collection: str, tenant_id: str, source_id: str,
                       record: Dict[str, Any]) -> Dict[str, Any]:
    """Push one record and stamp the outcome on the source doc as
    `hub_sync`. NEVER raises."""
    client = _client_from_env()
    if client is None:
        # OFF/demo: zero behavior change — not even a stamp.
        return {"synced": False, "reason": "not_configured"}
    idem = idempotency_key(tenant_id, record["record_type"], source_id, record["status"])
    coll = getattr(db, collection)
    try:
        archive_id = await _push_with_retries(client, record, idem)
        await coll.update_one(
            {"tenant_id": tenant_id, "id": source_id},
            {"$set": {"hub_sync": {"status": "synced", "archive_id": archive_id,
                                   "for_status": record["status"],
                                   "at": _now_iso(), "error": None}}})
        return {"synced": True, "archive_id": archive_id}
    except Exception as e:  # ServiceHubError or anything unexpected — never propagate
        msg = str(e)[:300]
        log.warning("service-hub sync failed tenant=%s %s=%s: %s",
                    tenant_id, record["record_type"], source_id, msg)
        try:
            await coll.update_one(
                {"tenant_id": tenant_id, "id": source_id},
                {"$set": {"hub_sync": {"status": "error", "archive_id": None,
                                       "for_status": record["status"],
                                       "at": _now_iso(), "error": msg}}})
        except Exception:
            pass
        return {"synced": False, "reason": "error", "error": msg}


async def sync_escalation(db, tenant_id: str, esc: Dict[str, Any],
                          transcript: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Best-effort sync of one escalation (+ its human resolution once
    resolved) to the hub archive. NEVER raises."""
    try:
        record = escalation_record(tenant_id, esc, transcript)
        return await _sync_record(db, "escalations", tenant_id, record["source_id"], record)
    except Exception as e:  # defensive: payload building itself must not break callers
        log.warning("service-hub escalation sync failed tenant=%s: %s", tenant_id, e)
        return {"synced": False, "reason": "error", "error": str(e)[:300]}


async def sync_ticket(db, tenant_id: str, ticket: Dict[str, Any],
                      resolution_note: str = "") -> Dict[str, Any]:
    """Best-effort sync of one support ticket (+ resolution) to the hub
    archive. NEVER raises."""
    try:
        record = ticket_record(tenant_id, ticket, resolution_note)
        return await _sync_record(db, "support_tickets", tenant_id, record["source_id"], record)
    except Exception as e:
        log.warning("service-hub ticket sync failed tenant=%s: %s", tenant_id, e)
        return {"synced": False, "reason": "error", "error": str(e)[:300]}
