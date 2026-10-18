"""Bookkeeping link: sync customers, invoices and payments to the tenant's
OWN bookkeeping app (the user's QuickBooks competitor).

Deliberately no QuickBooks integration — see services/accounting.py.

Routes (all tenant-isolated, mounted under /api by server.py):
    GET/PUT  /tenants/bookkeeping-link/settings        — per-tenant config.
                                                       Writes are owner/admin
                                                       only. The raw API token
                                                       is write-only and is
                                                       never returned or logged.
    POST     /tenants/bookkeeping-link/test-connection — validate the token
                                                       against the bookkeeping
                                                       API (GET /auth/me).
    POST     /tenants/bookkeeping-link/health-check   — ping the bookkeeping
                                                       API and record the
                                                       connection health
                                                       (healthy/degraded/
                                                       revoked).
    POST     /tenants/bookkeeping-link/rotate-credential — validate a NEW
                                                       credential against
                                                       the bookkeeping API,
                                                       then swap it in. The
                                                       old credential keeps
                                                       working until the new
                                                       one proves valid.
    POST     /tenants/bookkeeping-link/disconnect     — remove the stored
                                                       credential locally
                                                       and stop syncing.
    GET      /tenants/bookkeeping-link/sync-status     — last sync time,
                                                       last error, counts,
                                                       health, migration.
    POST     /tenants/bookkeeping-link/invoices/{id}/sync — manual one-shot
                                                       sync of a paid invoice.

Credentials: the stored secret is either a dedicated INTEGRATION
credential issued by the bookkeeping app (long-lived, scoped, revocable
— the target state) or a LEGACY owner JWT pasted from the bookkeeping
app (7-day expiry; still supported so connected tenants keep syncing,
and flagged via the `migration` block so the tenant is prompted to
reconnect with an integration credential). Secrets are encrypted at
rest (services/token_vault.py) and never leave the server; only a
SHA-256 fingerprint prefix is exposed for support/rotation checks.

Health: `settings.health.status` is one of unknown / healthy / degraded
/ revoked. It is updated by health checks, test-connection, and every
sync attempt (successes and failures), so it stays current without a
scheduler; POST /cron/bookkeeping-health also sweeps all connected
tenants periodically. When the books side reports the credential
revoked, sync pauses (reason "credential_revoked") until a new
credential is saved — a dead credential is never retried silently.

Auto-sync: call_to_payment._apply_payment_success calls sync_invoice_paid()
when an invoice flips to paid and the tenant has auto-sync enabled. The
helper is best-effort — it never raises into the payment flow; failures
are logged and recorded on the tenant settings for the owner to see.

Collections: settings live on the tenant doc (`tenants.bookkeeping_link_settings`);
pushed provider ids live on each invoice doc (`invoices.bookkeeping_sync`)
so retries are idempotent: already-pushed steps are skipped.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso
from security import require_tenant_user, require_tenant_owner_or_admin
from services.accounting import (
    AccountingError,
    SyncCustomer,
    SyncInvoice,
    SyncInvoiceLine,
    SyncPayment,
    MindAtEaseProvider,
    idempotency_key,
    DEFAULT_API_BASE_URL,
)
from services.token_vault import (
    EncryptionNotConfigured,
    SecretDecryptionFailed,
    decrypt_secret,
    encrypt_secret,
)

log = logging.getLogger("bookkeeping_link")

router = APIRouter(prefix="/tenants/bookkeeping-link", tags=["bookkeeping-link"])

def _default_health() -> Dict[str, Any]:
    return {"status": "unknown", "checked_at": None, "detail": None,
            "consecutive_failures": 0}


DEFAULT_SETTINGS: Dict[str, Any] = {
    "enabled": False,
    "api_base_url": DEFAULT_API_BASE_URL,
    "api_token_set": False,
    "auto_sync_on_paid": True,
    "last_sync_at": None,
    "last_error": None,
    "counts": {"customers": 0, "invoices": 0, "payments": 0},
    "last_test_at": None,
    "last_test_ok": None,
    # Credential model (audit 2026-10-17): kind is "integration" for a
    # dedicated books-issued credential, "legacy_jwt" for a pasted owner
    # session token. Only the fingerprint (SHA-256 prefix) is ever shown.
    "credential_kind": None,
    "credential_fingerprint": None,
    "credential_saved_at": None,
    "credential_rotated_at": None,
    "credential_revoked_at": None,
    "disconnected_at": None,
    "health": _default_health(),
    # Bounded failure log (newest last): {at, invoice_id, kind, error}.
    "sync_failures": [],
}

MAX_RECORDED_FAILURES = 10


# ---------- models ----------

class BookkeepingSettingsIn(BaseModel):
    enabled: Optional[bool] = None
    api_base_url: Optional[str] = None
    api_token: Optional[str] = None  # write-only; never echoed back
    # Optional explicit credential classification ("integration" |
    # "legacy_jwt"). When omitted, the kind is inferred from the token
    # shape (a 3-segment JWT is a legacy session token).
    credential_kind: Optional[str] = None
    clear_token: bool = False
    auto_sync_on_paid: Optional[bool] = None


class RotateCredentialIn(BaseModel):
    api_token: str = Field(min_length=1)  # write-only; never echoed back
    api_base_url: Optional[str] = None
    credential_kind: Optional[str] = None


# ---------- helpers ----------

async def _get_settings(db, tenant_id: str) -> Dict[str, Any]:
    t = await db.tenants.find_one({"id": tenant_id}, {"_id": 0, "bookkeeping_link_settings": 1}) or {}
    settings = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_SETTINGS.items()}
    stored = t.get("bookkeeping_link_settings") or {}
    settings.update(stored)
    # Keep counts well-formed even if an old doc stored something odd.
    counts = {"customers": 0, "invoices": 0, "payments": 0}
    counts.update(settings.get("counts") or {})
    settings["counts"] = counts
    # Same defensive normalization for the audit-era fields: docs written
    # before they existed must read as unknown health / no failures, and
    # mutable defaults must never be shared across tenants.
    health = _default_health()
    health.update(settings.get("health") or {})
    settings["health"] = health
    settings["sync_failures"] = list(settings.get("sync_failures") or [])
    return settings


async def _save_settings(db, tenant_id: str, settings: Dict[str, Any]) -> None:
    await db.tenants.update_one(
        {"id": tenant_id},
        {"$set": {"bookkeeping_link_settings": settings, "updated_at": _now_iso()}},
    )


def _public_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(settings)
    out.pop("api_token_enc", None)  # the raw token NEVER leaves the server
    out["migration"] = _migration_info(settings)
    return out


# ---------- credential helpers ----------

def _looks_like_jwt(token: str) -> bool:
    parts = token.split(".")
    return len(parts) == 3 and all(parts)


def credential_fingerprint(token: str) -> str:
    """Non-sensitive identifier for a stored credential (SHA-256 prefix).

    Safe to expose: it lets support confirm WHICH credential is stored
    (e.g. after a rotation) without ever revealing the secret."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _classify_kind(token: str, explicit: Optional[str]) -> str:
    if explicit in ("integration", "legacy_jwt"):
        return explicit
    return "legacy_jwt" if _looks_like_jwt(token) else "integration"


def _apply_credential(settings: Dict[str, Any], token: str,
                      explicit_kind: Optional[str] = None, *, rotated: bool = False) -> None:
    """Store a new credential (encrypted) and reset credential state.

    Raises EncryptionNotConfigured (caller maps to HTTP 500) — fail closed.
    """
    token = token.strip()
    fingerprint = credential_fingerprint(token)
    replacing = bool(settings.get("api_token_set")) and \
        settings.get("credential_fingerprint") != fingerprint
    settings["api_token_enc"] = encrypt_secret(token)
    settings["api_token_set"] = True
    settings["credential_kind"] = _classify_kind(token, explicit_kind)
    settings["credential_fingerprint"] = fingerprint
    now = _now_iso()
    if not settings.get("credential_saved_at"):
        settings["credential_saved_at"] = now
    if rotated or replacing:
        settings["credential_rotated_at"] = now
    # A fresh credential clears any revocation pause and stale health.
    settings["credential_revoked_at"] = None
    settings["disconnected_at"] = None
    settings["health"] = _default_health()


def _migration_info(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Whether this tenant must reconnect under the new credential model.

    Legacy JWT tenants keep syncing (no forced breakage); they are
    prompted to reconnect because the pasted token expires (~7 days)
    while a dedicated integration credential does not.
    """
    if settings.get("credential_revoked_at"):
        return {"required": True, "reason": "credential_revoked",
                "message": "The bookkeeping app revoked this credential. "
                           "Reconnect with a new integration credential."}
    if settings.get("api_token_set") and settings.get("credential_kind") == "legacy_jwt":
        return {"required": True, "reason": "legacy_jwt",
                "message": "This link uses a pasted login token that expires "
                           "after about 7 days. Reconnect with a dedicated "
                           "integration credential so sync never lapses."}
    return {"required": False, "reason": None, "message": None}


# ---------- connection health ----------

def _health_from_error(e: AccountingError) -> str:
    return "revoked" if getattr(e, "kind", "") == "revoked" else "degraded"


async def check_connection_health(db, tenant_id: str) -> Dict[str, Any]:
    """Ping the bookkeeping API with the stored credential and record the
    outcome on the tenant settings. Returns {ok, health, detail} plus the
    migration block. Raises HTTPException(400) when no credential is saved.
    """
    settings = await _get_settings(db, tenant_id)
    provider = _provider_for(settings)  # 400/500 when unusable — nothing to ping
    now = _now_iso()
    health = dict(settings.get("health") or _default_health())
    try:
        info = await provider.test_connection()
    except AccountingError as e:
        health["status"] = _health_from_error(e)
        health["checked_at"] = now
        health["detail"] = str(e)[:300]
        health["consecutive_failures"] = int(health.get("consecutive_failures") or 0) + 1
        if health["status"] == "revoked":
            settings["credential_revoked_at"] = now
            settings["last_error"] = str(e)[:300]
        settings["health"] = health
        await _save_settings(db, tenant_id, settings)
        return {"ok": False, "health": health, "detail": str(e),
                "migration": _migration_info(settings)}
    health.update({"status": "healthy", "checked_at": now, "detail": None,
                   "consecutive_failures": 0})
    settings["health"] = health
    settings["last_error"] = None
    await _save_settings(db, tenant_id, settings)
    who = info.get("user_name") or info.get("user_email") or "unknown user"
    shop = info.get("tenant_name") or "unknown shop"
    return {"ok": True, "health": health,
            "detail": f"Connected as {who} ({shop})",
            "migration": _migration_info(settings)}


async def sweep_bookkeeping_health(db) -> Dict[str, Any]:
    """Periodic sweep (cron): health-check every connected tenant.

    One tenant's failure never stops the sweep. Returns a small summary.
    """
    tenants = await db.tenants.find(
        {"bookkeeping_link_settings.enabled": True,
         "bookkeeping_link_settings.api_token_set": True},
        {"_id": 0, "id": 1}).to_list(2000)
    checked = healthy = 0
    for t in tenants:
        try:
            res = await check_connection_health(db, t["id"])
            checked += 1
            healthy += 1 if res.get("ok") else 0
        except Exception as e:  # never let one tenant break the sweep
            log.warning("bookkeeping health sweep failed tenant=%s: %s", t.get("id"), e)
    return {"checked": checked, "healthy": healthy}


def _validate_base_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise HTTPException(400, "api_base_url is required")
    low = url.lower()
    if low.startswith("http://"):
        host = low[len("http://"):].split("/")[0].split(":")[0]
        if host not in ("localhost", "127.0.0.1", "::1"):
            raise HTTPException(400, "api_base_url must use https:// (http is only allowed for localhost)")
    elif not low.startswith("https://"):
        raise HTTPException(400, "api_base_url must be an https:// URL")
    return url.rstrip("/")


def _provider_for(settings: Dict[str, Any]) -> MindAtEaseProvider:
    enc = settings.get("api_token_enc")
    if not enc:
        raise HTTPException(400, "No bookkeeping-app API token saved — paste one in settings first")
    try:
        token = decrypt_secret(enc)
    except (EncryptionNotConfigured, SecretDecryptionFailed) as e:
        raise HTTPException(500, str(e))
    return MindAtEaseProvider(api_base_url=settings.get("api_base_url") or DEFAULT_API_BASE_URL,
                              api_token=token)


# ---------- settings ----------

@router.get("/settings")
async def get_settings(user: dict = Depends(require_tenant_user)):
    db = get_db()
    return _public_settings(await _get_settings(db, user["tenant_id"]))


@router.put("/settings")
async def put_settings(body: BookkeepingSettingsIn,
                       user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    settings = await _get_settings(db, user["tenant_id"])
    if body.enabled is not None:
        settings["enabled"] = bool(body.enabled)
    if body.auto_sync_on_paid is not None:
        settings["auto_sync_on_paid"] = bool(body.auto_sync_on_paid)
    if body.api_base_url is not None:
        settings["api_base_url"] = _validate_base_url(body.api_base_url)
    if body.clear_token:
        settings.pop("api_token_enc", None)
        settings["api_token_set"] = False
        settings["credential_kind"] = None
        settings["credential_fingerprint"] = None
        settings["health"] = _default_health()
    elif body.api_token:
        if body.credential_kind is not None and \
                body.credential_kind not in ("integration", "legacy_jwt"):
            raise HTTPException(400, "credential_kind must be 'integration' or 'legacy_jwt'")
        try:
            _apply_credential(settings, body.api_token, body.credential_kind)
        except EncryptionNotConfigured as e:
            raise HTTPException(500, str(e))
    await _save_settings(db, user["tenant_id"], settings)
    return _public_settings(settings)


@router.post("/test-connection")
async def test_connection(user: dict = Depends(require_tenant_owner_or_admin)):
    """Validate the saved token against the bookkeeping API's GET /auth/me."""
    db = get_db()
    settings = await _get_settings(db, user["tenant_id"])
    provider = _provider_for(settings)  # 400/500 if no token or undecryptable
    now = _now_iso()
    try:
        info = await provider.test_connection()
    except AccountingError as e:
        settings["last_test_at"] = now
        settings["last_test_ok"] = False
        health = dict(settings.get("health") or _default_health())
        health["status"] = _health_from_error(e)
        health["checked_at"] = now
        health["detail"] = str(e)[:300]
        health["consecutive_failures"] = int(health.get("consecutive_failures") or 0) + 1
        settings["health"] = health
        if health["status"] == "revoked":
            settings["credential_revoked_at"] = now
        await _save_settings(db, user["tenant_id"], settings)
        return {"ok": False, "detail": str(e)}
    settings["last_test_at"] = now
    settings["last_test_ok"] = True
    settings["last_error"] = None
    health = dict(settings.get("health") or _default_health())
    health.update({"status": "healthy", "checked_at": now, "detail": None,
                   "consecutive_failures": 0})
    settings["health"] = health
    await _save_settings(db, user["tenant_id"], settings)
    who = info.get("user_name") or info.get("user_email") or "unknown user"
    shop = info.get("tenant_name") or "unknown shop"
    return {"ok": True, "detail": f"Connected as {who} ({shop})"}


@router.post("/health-check")
async def health_check(user: dict = Depends(require_tenant_owner_or_admin)):
    """Ping the bookkeeping API and record connection health.

    Surfaces healthy / degraded / revoked so a dying credential is seen
    BEFORE it silently stops syncing invoices."""
    db = get_db()
    return await check_connection_health(db, user["tenant_id"])


@router.post("/rotate-credential")
async def rotate_credential(body: RotateCredentialIn,
                            user: dict = Depends(require_tenant_owner_or_admin)):
    """Swap in a NEW credential — validated against the bookkeeping API
    BEFORE it replaces the stored one, so a typo can never strand the
    tenant on a dead credential. Server-side rotation (issuing/revoking
    credentials) happens in the bookkeeping app; this is the client half.
    """
    db = get_db()
    settings = await _get_settings(db, user["tenant_id"])
    base_url = settings.get("api_base_url") or DEFAULT_API_BASE_URL
    if body.api_base_url is not None:
        base_url = _validate_base_url(body.api_base_url)
    if body.credential_kind is not None and \
            body.credential_kind not in ("integration", "legacy_jwt"):
        raise HTTPException(400, "credential_kind must be 'integration' or 'legacy_jwt'")
    candidate = MindAtEaseProvider(api_base_url=base_url,
                                   api_token=body.api_token.strip())
    try:
        await candidate.test_connection()
    except AccountingError as e:
        # Old credential untouched — sync keeps working with it.
        raise HTTPException(400, f"New credential was rejected: {e}")
    try:
        _apply_credential(settings, body.api_token, body.credential_kind, rotated=True)
    except EncryptionNotConfigured as e:
        raise HTTPException(500, str(e))
    settings["api_base_url"] = base_url
    now = _now_iso()
    health = dict(settings.get("health") or _default_health())
    health.update({"status": "healthy", "checked_at": now, "detail": None,
                   "consecutive_failures": 0})
    settings["health"] = health
    await _save_settings(db, user["tenant_id"], settings)
    return _public_settings(settings)


@router.post("/disconnect")
async def disconnect(user: dict = Depends(require_tenant_owner_or_admin)):
    """Remove the stored credential locally and stop syncing.

    This is the client half of revocation: the secret is wiped from
    Momo's database and no further sync is attempted. Revoking the
    credential on the bookkeeping side (so it can never be used again)
    is done in the bookkeeping app's integration settings."""
    db = get_db()
    settings = await _get_settings(db, user["tenant_id"])
    settings.pop("api_token_enc", None)
    settings["api_token_set"] = False
    settings["enabled"] = False
    settings["credential_kind"] = None
    settings["credential_fingerprint"] = None
    settings["credential_revoked_at"] = None
    settings["disconnected_at"] = _now_iso()
    settings["health"] = _default_health()
    await _save_settings(db, user["tenant_id"], settings)
    return _public_settings(settings)


@router.get("/sync-status")
async def sync_status(user: dict = Depends(require_tenant_user)):
    db = get_db()
    s = await _get_settings(db, user["tenant_id"])
    return {
        "enabled": s.get("enabled", False),
        "auto_sync_on_paid": s.get("auto_sync_on_paid", True),
        "api_token_set": s.get("api_token_set", False),
        "credential_kind": s.get("credential_kind"),
        "credential_fingerprint": s.get("credential_fingerprint"),
        "last_sync_at": s.get("last_sync_at"),
        "last_error": s.get("last_error"),
        "counts": s.get("counts"),
        "last_test_at": s.get("last_test_at"),
        "last_test_ok": s.get("last_test_ok"),
        "health": s.get("health"),
        "migration": _migration_info(s),
        "recent_failures": (s.get("sync_failures") or [])[-3:],
    }


# ---------- sync engine ----------

def _invoice_to_sync_invoice(inv: dict, customer_provider_id: Optional[str]) -> SyncInvoice:
    lines: List[SyncInvoiceLine] = []
    for ln in inv.get("lines") or []:
        qty = float(ln.get("quantity") or 1)
        unit = float(ln.get("unit_price") or 0)
        lines.append(SyncInvoiceLine(
            name=str(ln.get("description") or "Service"),
            amount_cents=int(round(qty * unit * 100)),
        ))
    if not lines:
        # Fall back to a single line for the invoice total so nothing is lost.
        lines = [SyncInvoiceLine(name=str(inv.get("title") or "Invoice"),
                                 amount_cents=int(inv.get("total_cents") or 0))]
    due = (inv.get("due_at") or "")[:10] or None
    return SyncInvoice(
        customer_name=inv.get("customer_name") or "Customer",
        lines=lines,
        total_cents=int(inv.get("total_cents") or 0),
        title=inv.get("title"),
        customer_provider_id=customer_provider_id,
        due_date=due,
    )


async def sync_invoice_paid(db, tenant_id: str, invoice: dict) -> Dict[str, Any]:
    """Push one paid invoice (customer → invoice → payment) to the tenant's
    own bookkeeping app. Idempotent: steps already recorded on the invoice's
    `bookkeeping_sync` are skipped. Best-effort: NEVER raises — failures are
    logged and recorded on the tenant settings."""
    inv_id = invoice.get("id")
    settings = await _get_settings(db, tenant_id)
    if not settings.get("enabled"):
        return {"synced": False, "reason": "disabled"}
    if not settings.get("auto_sync_on_paid", True):
        return {"synced": False, "reason": "auto_sync_off"}
    if invoice.get("status") != "paid":
        return {"synced": False, "reason": "not_paid"}
    if settings.get("credential_revoked_at"):
        # The books side revoked this credential: sync stays paused until
        # a new credential is saved. The revocation was already recorded
        # when detected — don't re-record it on every invoice.
        return {"synced": False, "reason": "credential_revoked"}

    try:
        provider = _provider_for(settings)
    except HTTPException as e:
        return await _record_failure(db, tenant_id, inv_id, f"not configured: {e.detail}",
                                     kind="config")

    bk = dict(invoice.get("bookkeeping_sync") or {})
    steps: List[str] = []
    try:
        if not bk.get("customer_provider_id"):
            cid = await provider.push_customer(
                SyncCustomer(name=invoice.get("customer_name") or "Customer",
                             email=invoice.get("customer_email"),
                             phone=invoice.get("customer_phone")),
                idempotency_key=idempotency_key(tenant_id, inv_id, "customer"))
            bk["customer_provider_id"] = cid
            steps.append("customer")
        if not bk.get("invoice_provider_id"):
            iid = await provider.push_invoice(
                _invoice_to_sync_invoice(invoice, bk.get("customer_provider_id")),
                idempotency_key=idempotency_key(tenant_id, inv_id, "invoice"))
            bk["invoice_provider_id"] = iid
            steps.append("invoice")
        if not bk.get("payment_provider_id"):
            pid = await provider.push_payment(
                SyncPayment(invoice_provider_id=bk["invoice_provider_id"],
                            amount_cents=int(invoice.get("total_cents") or 0),
                            method="card"),
                idempotency_key=idempotency_key(tenant_id, inv_id, "payment"))
            bk["payment_provider_id"] = pid
            steps.append("payment")

        bk["synced_at"] = _now_iso()
        await db.invoices.update_one(
            {"id": inv_id, "tenant_id": tenant_id},
            {"$set": {"bookkeeping_sync": bk, "updated_at": _now_iso()}},
        )
        counts = settings.get("counts") or {}
        step_to_count = {"customer": "customers", "invoice": "invoices", "payment": "payments"}
        for step in steps:
            key = step_to_count[step]
            counts[key] = counts.get(key, 0) + 1
        settings["counts"] = counts
        settings["last_sync_at"] = _now_iso()
        settings["last_error"] = None
        if steps:
            # The provider was actually exercised and answered: healthy.
            health = dict(settings.get("health") or _default_health())
            health.update({"status": "healthy", "checked_at": _now_iso(),
                           "detail": None, "consecutive_failures": 0})
            settings["health"] = health
        await _save_settings(db, tenant_id, settings)
        return {"synced": True, "steps": steps,
                "customer_provider_id": bk.get("customer_provider_id"),
                "invoice_provider_id": bk.get("invoice_provider_id"),
                "payment_provider_id": bk.get("payment_provider_id")}
    except Exception as e:  # AccountingError and anything unexpected — never propagate
        log.warning("bookkeeping-link sync failed tenant=%s invoice=%s: %s",
                    tenant_id, inv_id, e)
        # Record the attempt on the invoice itself (steps already pushed
        # stay recorded, so a retry resumes instead of duplicating).
        bk["attempts"] = int(bk.get("attempts") or 0) + 1
        bk["last_attempt_at"] = _now_iso()
        bk["last_error"] = str(e)[:300]
        try:
            await db.invoices.update_one(
                {"id": inv_id, "tenant_id": tenant_id},
                {"$set": {"bookkeeping_sync": bk, "updated_at": _now_iso()}},
            )
        except Exception:
            pass  # recording must never break the never-raises contract
        return await _record_failure(db, tenant_id, inv_id, str(e)[:300],
                                     kind=getattr(e, "kind", "error"))


async def _record_failure(db, tenant_id: str, inv_id: Optional[str], msg: str,
                          kind: str = "error") -> Dict[str, Any]:
    """Record a sync failure: last_error (legacy field), the bounded
    failure log, and connection health. Nothing fails silently."""
    settings = await _get_settings(db, tenant_id)
    settings["last_error"] = msg
    now = _now_iso()
    failures = list(settings.get("sync_failures") or [])
    failures.append({"at": now, "invoice_id": inv_id, "kind": kind, "error": msg})
    settings["sync_failures"] = failures[-MAX_RECORDED_FAILURES:]
    health = dict(settings.get("health") or _default_health())
    health["consecutive_failures"] = int(health.get("consecutive_failures") or 0) + 1
    health["checked_at"] = now
    health["detail"] = msg
    if kind == "revoked":
        health["status"] = "revoked"
        settings["credential_revoked_at"] = settings.get("credential_revoked_at") or now
    else:
        health["status"] = "degraded"
    settings["health"] = health
    await _save_settings(db, tenant_id, settings)
    return {"synced": False, "reason": "error", "error": msg}


@router.post("/invoices/{invoice_id}/sync")
async def manual_sync(invoice_id: str, user: dict = Depends(require_tenant_user)):
    """One-shot manual sync of a paid invoice (the UI's 'Sync now' button)."""
    db = get_db()
    inv = await db.invoices.find_one(
        {"id": invoice_id, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not inv:
        raise HTTPException(404, "Invoice not found")
    if inv.get("status") != "paid":
        raise HTTPException(400, "Only paid invoices can be synced")
    return await sync_invoice_paid(db, user["tenant_id"], inv)
