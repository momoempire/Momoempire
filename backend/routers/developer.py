"""Public API + webhooks (#35): integration ecosystem.

Two routers:

- `router` — APIRouter(prefix="/developer", tags=["developer"]): API key
  management + webhook subscription management. Tenant-JWT authenticated,
  owner/admin only. Mount under the /api APIRouter like every other
  tenant router: api.include_router(developer_router).
- `public_api_router` — APIRouter(prefix="/api/v1"): machine API
  authenticated by API keys (NOT JWT). Mount at APP level, NOT under the
  /api APIRouter (its prefix already contains /api/v1):
  app.include_router(public_api_router).

Auth model:
- API keys look like `mk_live_<32 urlsafe chars>`. Only a SHA-256 hash of
  the raw key is stored; the raw key is returned exactly ONCE at issuance.
- Key auth reads `Authorization: Bearer <key>` (or the `X-API-Key` header),
  resolves the tenant from the key, constant-time-compares the hash, and
  rejects revoked keys (401) and out-of-scope calls (403).
- Public surface is rate-limited per key via the shared rate_limit module
  (Redis when REDIS_URL is set, in-memory fallback) — 600 req/min/key.

Webhooks:
- Subscriptions carry a per-subscription signing secret (`whsec_...`).
  The secret is stored Fernet-encrypted with a key derived from
  JWT_SECRET (no new env var) and shown exactly ONCE at creation.
- Deliveries are signed HMAC-SHA256: header `X-Momo-Signature:
  sha256=<hex>` over the string "<iso-timestamp>.<canonical-json-body>",
  plus `X-Momo-Timestamp`. Use build_signature()/verify_signature().
- emit_webhook(tenant_id, event, payload) is the helper OTHER routers
  call (see TODO integration points below). This router emits from its
  own endpoints too.

TODO integration points (other routers — deliberately NOT modified here):
- dispatch.py mark_job_complete -> emit_webhook(tid, "job.completed", {...})
- crm.py mark_invoice_paid     -> emit_webhook(tid, "invoice.paid", {...})
- crm.py create_invoice        -> (optional) a "invoice.created" event
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from db import get_db
from models import Appointment, AppointmentIn, Customer, CustomerIn, _now_iso
from rate_limit import check_rate_limit, rate_limit_headers
from security import require_tenant_owner_or_admin

log = logging.getLogger("developer")

router = APIRouter(prefix="/developer", tags=["developer"])
public_api_router = APIRouter(prefix="/api/v1", tags=["public-api"])

# ---------- constants ----------

KEY_PREFIX = "mk_live_"
SECRET_PREFIX = "whsec_"

SCOPES = frozenset({
    "customers:read", "customers:write",
    "jobs:read", "jobs:write",
    "invoices:read",
})

WEBHOOK_EVENTS = frozenset({
    "job.created", "job.completed", "invoice.paid", "customer.created",
})

RATE_LIMIT_PER_MIN = 600
DELIVERY_TIMEOUT_S = 10.0
DELIVERY_MAX_ATTEMPTS = 3

SIGNATURE_HEADER = "X-Momo-Signature"
TIMESTAMP_HEADER = "X-Momo-Timestamp"


# ---------- signing ----------

def build_signature(secret: str, timestamp: str, body: bytes) -> str:
    """HMAC-SHA256 signature: 'sha256=<hex>' over '<timestamp>.<body>'."""
    msg = timestamp.encode("utf-8") + b"." + body
    return "sha256=" + hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verify_signature(secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    """Constant-time signature check (receivers use this to verify us)."""
    try:
        return hmac.compare_digest(build_signature(secret, timestamp, body), signature)
    except Exception:
        return False


def _canonical_json(obj: dict) -> bytes:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")


# ---------- secret encryption (no new env var) ----------

def _webhook_fernet() -> Fernet:
    master = os.environ.get("JWT_SECRET", "")
    if not master:
        raise HTTPException(500, "Server misconfigured: JWT_SECRET is not set")
    kdf = HKDF(algorithm=hashes.SHA256(), length=32,
               salt=b"momo-webhook-secret", info=b"webhook-signing")
    return Fernet(base64.urlsafe_b64encode(kdf.derive(master.encode("utf-8"))))


def _encrypt_secret(plaintext: str) -> str:
    return _webhook_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def _decrypt_secret(ciphertext: str) -> str:
    try:
        return _webhook_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError) as e:
        raise HTTPException(500, f"Could not decrypt webhook secret: {e}")


# ---------- API keys ----------

def _key_hash(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _new_raw_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(24)


def _key_hint(raw_key: str) -> str:
    # Identification only: prefix + last 4 chars, safe to display.
    return f"{KEY_PREFIX}\u2022\u2022\u2022\u2022{raw_key[-4:]}"


def _extract_raw_key(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip() or None
    header_key = request.headers.get("X-API-Key", "").strip()
    return header_key or None


def require_api_key(*required_scopes: str):
    """Key-auth dependency: 401 on missing/bad/revoked key, 403 on missing
    scope, 429 when the per-key rate bucket is exhausted."""
    async def _dep(request: Request) -> dict:
        raw = _extract_raw_key(request)
        if not raw:
            raise HTTPException(401, "API key required (Authorization: Bearer mk_live_...)")
        if not raw.startswith(KEY_PREFIX):
            raise HTTPException(401, "Invalid API key")
        presented = _key_hash(raw)
        db = get_db()
        doc = await db.api_keys.find_one({"key_hash": presented}, {"_id": 0})
        if not doc or not hmac.compare_digest(doc.get("key_hash", ""), presented):
            raise HTTPException(401, "Invalid API key")
        if doc.get("revoked"):
            raise HTTPException(401, "API key revoked")
        granted = set(doc.get("scopes") or [])
        missing = [s for s in required_scopes if s not in granted]
        if missing:
            raise HTTPException(403, f"Missing required scope(s): {', '.join(missing)}")
        allowed, retry_after = await check_rate_limit(
            f"public-api:{doc['id']}", RATE_LIMIT_PER_MIN, 60.0)
        if not allowed:
            raise HTTPException(429, "Public API rate limit exceeded",
                                headers=rate_limit_headers(retry_after))
        await db.api_keys.update_one({"id": doc["id"]},
                                     {"$set": {"last_used_at": _now_iso()}})
        return {"tenant_id": doc["tenant_id"], "key_id": doc["id"],
                "key_name": doc.get("name", ""), "scopes": sorted(granted)}
    return _dep


def _key_public(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "name": doc.get("name", ""),
        "scopes": doc.get("scopes", []),
        "key_hint": doc.get("key_hint", ""),
        "created_at": doc.get("created_at"),
        "created_by": doc.get("created_by"),
        "last_used_at": doc.get("last_used_at"),
        "revoked": bool(doc.get("revoked")),
        "revoked_at": doc.get("revoked_at"),
    }


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(min_length=1)


@router.post("/keys")
async def create_api_key(data: ApiKeyIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Issue an API key. The raw key is returned ONLY here — store it now."""
    unknown = [s for s in data.scopes if s not in SCOPES]
    if unknown:
        raise HTTPException(400, f"Unknown scope(s): {', '.join(unknown)}. "
                                 f"Valid scopes: {', '.join(sorted(SCOPES))}")
    db = get_db()
    raw_key = _new_raw_key()
    doc = {
        "id": uuid.uuid4().hex,
        "tenant_id": user["tenant_id"],
        "name": data.name.strip(),
        "scopes": sorted(set(data.scopes)),
        "key_hash": _key_hash(raw_key),
        "key_hint": _key_hint(raw_key),
        "created_at": _now_iso(),
        "created_by": user.get("id"),
        "last_used_at": None,
        "revoked": False,
        "revoked_at": None,
    }
    await db.api_keys.insert_one(doc)
    out = _key_public(doc)
    out["key"] = raw_key  # one-time reveal
    return out


@router.get("/keys")
async def list_api_keys(user: dict = Depends(require_tenant_owner_or_admin)):
    """Key metadata only — raw keys are never retrievable after issuance."""
    db = get_db()
    docs = await db.api_keys.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).to_list(200)
    return [_key_public(d) for d in docs]


@router.post("/keys/{kid}/revoke")
async def revoke_api_key(kid: str, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    doc = await db.api_keys.find_one({"id": kid, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "API key not found")
    if not doc.get("revoked"):
        await db.api_keys.update_one(
            {"id": kid, "tenant_id": user["tenant_id"]},
            {"$set": {"revoked": True, "revoked_at": _now_iso()}})
        doc = await db.api_keys.find_one({"id": kid, "tenant_id": user["tenant_id"]}, {"_id": 0})
    return _key_public(doc)


# ---------- public surface (/api/v1) ----------

def _strip(doc: dict) -> dict:
    d = dict(doc)
    d.pop("_id", None)
    return d


@public_api_router.get("/customers")
async def v1_list_customers(limit: int = 100, key: dict = Depends(require_api_key("customers:read"))):
    limit = max(1, min(limit, 500))
    db = get_db()
    docs = await db.customers.find({"tenant_id": key["tenant_id"]}, {"_id": 0}) \
        .sort("created_at", -1).to_list(limit)
    return [_strip(d) for d in docs]


@public_api_router.get("/customers/{cid}")
async def v1_get_customer(cid: str, key: dict = Depends(require_api_key("customers:read"))):
    db = get_db()
    doc = await db.customers.find_one({"id": cid, "tenant_id": key["tenant_id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Customer not found")
    return _strip(doc)


@public_api_router.post("/customers")
async def v1_create_customer(data: CustomerIn, background: BackgroundTasks,
                             key: dict = Depends(require_api_key("customers:write"))):
    db = get_db()
    cust = Customer(tenant_id=key["tenant_id"], **data.model_dump())
    doc = cust.model_dump()
    await db.customers.insert_one(dict(doc))
    background.add_task(emit_webhook, key["tenant_id"], "customer.created",
                        {"id": doc["id"], "name": doc["name"]})
    return _strip(doc)


@public_api_router.get("/jobs")
async def v1_list_jobs(limit: int = 100, key: dict = Depends(require_api_key("jobs:read"))):
    """Jobs ARE appointments (same collection as the dispatch board)."""
    limit = max(1, min(limit, 500))
    db = get_db()
    docs = await db.appointments.find({"tenant_id": key["tenant_id"]}, {"_id": 0}) \
        .sort("created_at", -1).to_list(limit)
    return [_strip(d) for d in docs]


@public_api_router.get("/jobs/{jid}")
async def v1_get_job(jid: str, key: dict = Depends(require_api_key("jobs:read"))):
    db = get_db()
    doc = await db.appointments.find_one({"id": jid, "tenant_id": key["tenant_id"]}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Job not found")
    return _strip(doc)


@public_api_router.post("/jobs")
async def v1_create_job(data: AppointmentIn, background: BackgroundTasks,
                        key: dict = Depends(require_api_key("jobs:write"))):
    db = get_db()
    appt = Appointment(tenant_id=key["tenant_id"], **data.model_dump())
    doc = appt.model_dump()
    await db.appointments.insert_one(dict(doc))
    background.add_task(emit_webhook, key["tenant_id"], "job.created",
                        {"id": doc["id"], "customer_name": doc["customer_name"],
                         "service_name": doc["service_name"], "status": doc["status"]})
    return _strip(doc)


@public_api_router.get("/invoices")
async def v1_list_invoices(limit: int = 100, key: dict = Depends(require_api_key("invoices:read"))):
    limit = max(1, min(limit, 500))
    db = get_db()
    docs = await db.invoices.find({"tenant_id": key["tenant_id"]}, {"_id": 0}) \
        .sort("created_at", -1).to_list(limit)
    return [_strip(d) for d in docs]


# ---------- webhooks: subscriptions ----------

def _validate_webhook_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise HTTPException(400, "url is required")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(400, "url must be a valid http(s) URL")
    return url


def _validate_events(events: list) -> list[str]:
    if not events:
        raise HTTPException(400, "events must be a non-empty list")
    unknown = [e for e in events if e not in WEBHOOK_EVENTS]
    if unknown:
        raise HTTPException(400, f"Unknown event(s): {', '.join(unknown)}. "
                                 f"Valid events: {', '.join(sorted(WEBHOOK_EVENTS))}")
    return sorted(set(events))


def _new_signing_secret() -> str:
    return SECRET_PREFIX + secrets.token_hex(24)


def _webhook_public(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "url": doc.get("url"),
        "events": doc.get("events", []),
        "secret_hint": doc.get("secret_hint", ""),
        "active": bool(doc.get("active", True)),
        "created_at": doc.get("created_at"),
        "last_delivery_at": doc.get("last_delivery_at"),
    }


class WebhookIn(BaseModel):
    url: str
    events: list[str] = Field(min_length=1)


@router.post("/webhooks")
async def create_webhook(data: WebhookIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Subscribe a URL to events. The signing secret is returned ONLY here."""
    url = _validate_webhook_url(data.url)
    events = _validate_events(data.events)
    db = get_db()
    raw_secret = _new_signing_secret()
    doc = {
        "id": uuid.uuid4().hex,
        "tenant_id": user["tenant_id"],
        "url": url,
        "events": events,
        "secret_enc": _encrypt_secret(raw_secret),
        "secret_hint": f"{SECRET_PREFIX}\u2022\u2022\u2022\u2022{raw_secret[-4:]}",
        "active": True,
        "created_at": _now_iso(),
        "created_by": user.get("id"),
        "last_delivery_at": None,
    }
    await db.webhook_subscriptions.insert_one(doc)
    out = _webhook_public(doc)
    out["secret"] = raw_secret  # one-time reveal; receivers store it to verify us
    return out


@router.get("/webhooks")
async def list_webhooks(user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    docs = await db.webhook_subscriptions.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}).to_list(200)
    return [_webhook_public(d) for d in docs]


@router.delete("/webhooks/{wid}")
async def delete_webhook(wid: str, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    res = await db.webhook_subscriptions.delete_one({"id": wid, "tenant_id": user["tenant_id"]})
    if res.deleted_count == 0:
        raise HTTPException(404, "Webhook not found")
    return {"status": "ok"}


# ---------- webhooks: delivery ----------

async def _post_webhook(url: str, body: bytes, headers: dict) -> tuple[int, str]:
    """One HTTP delivery attempt. Returns (status_code, error_or_empty).

    Kept module-level so tests can monkeypatch it (no live network).
    """
    import httpx
    async with httpx.AsyncClient(timeout=DELIVERY_TIMEOUT_S, follow_redirects=False) as client:
        resp = await client.post(url, content=body, headers=headers)
        return resp.status_code, ""


async def _attempt_delivery(db, sub: dict, event: str, envelope: dict) -> dict:
    body = _canonical_json(envelope)
    ts = datetime.now(timezone.utc).isoformat()
    secret = _decrypt_secret(sub["secret_enc"])
    headers = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: build_signature(secret, ts, body),
        TIMESTAMP_HEADER: ts,
        "User-Agent": "Momo-Webhooks/1.0",
    }
    attempts = 0
    status = "failed"
    status_code: Optional[int] = None
    error = ""
    for i in range(DELIVERY_MAX_ATTEMPTS):
        attempts += 1
        try:
            status_code, error = await _post_webhook(sub["url"], body, headers)
            if 200 <= status_code < 300:
                status = "delivered"
                error = ""
                break
            error = f"HTTP {status_code}"
        except Exception as e:  # noqa: BLE001 - delivery must never raise
            error = f"{type(e).__name__}: {str(e)[:200]}"
            status_code = None
        if i < DELIVERY_MAX_ATTEMPTS - 1:
            await asyncio.sleep(0.5 * (i + 1))
    delivery = {
        "id": uuid.uuid4().hex,
        "subscription_id": sub["id"],
        "tenant_id": sub["tenant_id"],
        "event": event,
        "url": sub["url"],
        "status": status,
        "attempts": attempts,
        "status_code": status_code,
        "error": (error or "")[:500],
        "created_at": _now_iso(),
    }
    await db.webhook_deliveries.insert_one(delivery)
    await db.webhook_subscriptions.update_one(
        {"id": sub["id"]},
        {"$set": {"last_delivery_at": delivery["created_at"]}})
    return delivery


async def emit_webhook(tenant_id: str, event: str, payload: dict) -> list[dict]:
    """Emit helper other routers call, e.g.:

        await emit_webhook(tid, "job.completed", {"id": job_id, ...})

    Fans out to every ACTIVE subscription of the tenant registered for
    `event`. Never raises (delivery failures are recorded in the log).
    """
    if event not in WEBHOOK_EVENTS:
        log.warning("emit_webhook: unknown event %r ignored", event)
        return []
    db = get_db()
    envelope = {
        "id": f"evt_{uuid.uuid4().hex}",
        "event": event,
        "tenant_id": tenant_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data": payload or {},
    }
    deliveries: list[dict] = []
    try:
        subs = await db.webhook_subscriptions.find(
            {"tenant_id": tenant_id, "active": True}, {"_id": 0}).to_list(200)
        for sub in subs:
            if event not in (sub.get("events") or []):
                continue
            try:
                deliveries.append(await _attempt_delivery(db, sub, event, envelope))
            except Exception as e:  # noqa: BLE001 - one bad sub must not kill the fan-out
                log.warning("emit_webhook: delivery error for sub %s: %s",
                            sub.get("id"), e)
    except Exception as e:  # noqa: BLE001 - emitting must never break the caller
        log.warning("emit_webhook: fan-out error: %s", e)
    return deliveries


@router.get("/webhooks/{wid}/deliveries")
async def list_deliveries(wid: str, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    sub = await db.webhook_subscriptions.find_one(
        {"id": wid, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not sub:
        raise HTTPException(404, "Webhook not found")
    docs = await db.webhook_deliveries.find(
        {"subscription_id": wid, "tenant_id": user["tenant_id"]}, {"_id": 0}) \
        .sort("created_at", -1).to_list(50)
    return [_strip(d) for d in docs]


# ---------- webhooks: test mode (no network) ----------

_SAMPLE_PAYLOADS: dict[str, dict] = {
    "job.created": {"id": "job_sample_1", "customer_name": "Sample Customer",
                    "service_name": "AC tune-up", "status": "scheduled"},
    "job.completed": {"id": "job_sample_1", "customer_name": "Sample Customer",
                      "service_name": "AC tune-up", "status": "completed",
                      "completed_at": "2026-10-15T18:00:00Z"},
    "invoice.paid": {"id": "inv_sample_1", "customer_name": "Sample Customer",
                     "total": 249.99, "status": "paid"},
    "customer.created": {"id": "cus_sample_1", "name": "Sample Customer"},
}


@router.post("/webhooks/{wid}/test")
async def test_webhook(wid: str, user: dict = Depends(require_tenant_owner_or_admin)):
    """Sign a sample payload with the subscription's real secret but do NOT
    send it anywhere (no network). Returns exactly what WOULD be delivered."""
    db = get_db()
    sub = await db.webhook_subscriptions.find_one(
        {"id": wid, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not sub:
        raise HTTPException(404, "Webhook not found")
    event = (sub.get("events") or ["job.created"])[0]
    envelope = {
        "id": "evt_sample",
        "event": event,
        "tenant_id": user["tenant_id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data": _SAMPLE_PAYLOADS.get(event, {}),
    }
    body = _canonical_json(envelope)
    ts = datetime.now(timezone.utc).isoformat()
    secret = _decrypt_secret(sub["secret_enc"])
    return {
        "subscription_id": sub["id"],
        "url": sub["url"],
        "event": event,
        "delivered": False,
        "note": "Test mode: signed but NOT sent. Verify the signature with your "
                "signing secret before going live.",
        "timestamp": ts,
        "payload": envelope,
        "headers": {
            "Content-Type": "application/json",
            SIGNATURE_HEADER: build_signature(secret, ts, body),
            TIMESTAMP_HEADER: ts,
        },
    }
