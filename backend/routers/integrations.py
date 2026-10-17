"""Tenant integration configs (Twilio, Gmail, etc.).

Status honesty rule: a config is only "connected" after a LIVE
verification against the provider succeeds (services/integration_verify).
Saving complete-looking fields used to mark the integration "connected"
without any check — that lied about wrong/revoked credentials. Now:

    disabled or missing fields      → "disconnected"
    saved, no live check exists     → "configured" (never "connected")
    live check passed               → "connected" (+ last_checked_at)
    live check failed               → "error" (+ status_detail message)

Re-running a check (to reconnect after fixing credentials) is
POST /tenants/integrations/{key}/verify.
"""
from fastapi import APIRouter, HTTPException, Depends
from db import get_db
from models import _now_iso
from models import IntegrationConfig, IntegrationConfigIn
from security import require_tenant_owner_or_admin
from services.integration_verify import verify_integration

router = APIRouter(prefix="/tenants/integrations", tags=["integrations"])

KNOWN_KEYS = {
    "twilio":    {"label": "Twilio",         "needs": ["account_sid", "auth_token", "phone_number"]},
    "gmail":     {"label": "Gmail",          "needs": ["email", "client_id", "client_secret"]},
    "stripe":    {"label": "Stripe",         "needs": []},  # already provisioned at platform level
    "google_my_business": {"label": "Google My Business", "needs": ["client_id", "client_secret"]},
}


async def _assess(key: str, enabled: bool, config: dict, missing: list) -> dict:
    """Decide the honest status for a config. Returns status, status_detail,
    last_checked_at and a verification summary for the API response."""
    if not enabled:
        detail = "Integration is disabled."
        return {"status": "disconnected", "status_detail": detail, "last_checked_at": None,
                "verification": {"attempted": False, "ok": False, "detail": detail}}
    if missing:
        detail = f"Missing required fields: {', '.join(missing)}."
        return {"status": "disconnected", "status_detail": detail, "last_checked_at": None,
                "verification": {"attempted": False, "ok": False, "detail": detail}}
    result = await verify_integration(key, config or {})
    if result is None:
        detail = ("Configuration saved. No live verification is available for this "
                  "integration, so it is not marked connected.")
        return {"status": "configured", "status_detail": detail, "last_checked_at": None,
                "verification": {"attempted": False, "ok": False, "detail": detail}}
    ok, detail = result
    return {"status": "connected" if ok else "error",
            "status_detail": detail,
            "last_checked_at": _now_iso(),
            "verification": {"attempted": True, "ok": ok, "detail": detail}}


@router.get("/catalog")
async def catalog():
    return [{"key": k, **v} for k, v in KNOWN_KEYS.items()]


@router.get("")
async def list_mine(user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    rows = await db.tenant_integrations.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0},
    ).to_list(50)
    return rows


@router.post("")
async def upsert(data: IntegrationConfigIn, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    if data.key not in KNOWN_KEYS:
        raise HTTPException(400, "Unknown integration key")
    existing = await db.tenant_integrations.find_one({"tenant_id": user["tenant_id"], "key": data.key})
    needs = KNOWN_KEYS[data.key]["needs"]
    missing = [k for k in needs if not (data.config or {}).get(k)]
    assessed = await _assess(data.key, data.enabled, data.config or {}, missing)
    patch = {
        "key": data.key, "enabled": data.enabled, "config": data.config,
        "status": assessed["status"], "status_detail": assessed["status_detail"],
        "last_checked_at": assessed["last_checked_at"], "updated_at": _now_iso(),
    }
    if existing:
        await db.tenant_integrations.update_one(
            {"tenant_id": user["tenant_id"], "key": data.key},
            {"$set": patch},
        )
    else:
        cfg = IntegrationConfig(tenant_id=user["tenant_id"], **data.model_dump(),
                                status=assessed["status"],
                                status_detail=assessed["status_detail"],
                                last_checked_at=assessed["last_checked_at"])
        await db.tenant_integrations.insert_one(cfg.model_dump())
    doc = await db.tenant_integrations.find_one({"tenant_id": user["tenant_id"], "key": data.key}, {"_id": 0})
    return {"integration": doc, "missing": missing, "verification": assessed["verification"]}


@router.post("/{key}/verify")
async def verify_saved(key: str, user: dict = Depends(require_tenant_owner_or_admin)):
    """Re-run the live check against the SAVED config (the reconnect path
    after fixing credentials). Returns the refreshed integration doc."""
    db = get_db()
    if key not in KNOWN_KEYS:
        raise HTTPException(400, "Unknown integration key")
    existing = await db.tenant_integrations.find_one({"tenant_id": user["tenant_id"], "key": key})
    if not existing:
        raise HTTPException(404, "Integration not configured yet")
    needs = KNOWN_KEYS[key]["needs"]
    config = existing.get("config") or {}
    missing = [k for k in needs if not config.get(k)]
    assessed = await _assess(key, bool(existing.get("enabled")), config, missing)
    await db.tenant_integrations.update_one(
        {"tenant_id": user["tenant_id"], "key": key},
        {"$set": {"status": assessed["status"], "status_detail": assessed["status_detail"],
                  "last_checked_at": assessed["last_checked_at"], "updated_at": _now_iso()}},
    )
    doc = await db.tenant_integrations.find_one({"tenant_id": user["tenant_id"], "key": key}, {"_id": 0})
    return {"integration": doc, "missing": missing, "verification": assessed["verification"]}


@router.delete("/{key}")
async def disconnect(key: str, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    await db.tenant_integrations.delete_one({"tenant_id": user["tenant_id"], "key": key})
    return {"status": "ok"}
