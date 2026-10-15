"""Tenant integration configs (Twilio, Gmail, etc.) — stored but inactive in demo mode."""
from fastapi import APIRouter, HTTPException, Depends
from db import get_db
from models import _now_iso
from models import IntegrationConfig, IntegrationConfigIn
from security import require_tenant_owner_or_admin

router = APIRouter(prefix="/tenants/integrations", tags=["integrations"])

KNOWN_KEYS = {
    "twilio":    {"label": "Twilio",         "needs": ["account_sid", "auth_token", "phone_number"]},
    "gmail":     {"label": "Gmail",          "needs": ["email", "client_id", "client_secret"]},
    "stripe":    {"label": "Stripe",         "needs": []},  # already provisioned at platform level
    "google_my_business": {"label": "Google My Business", "needs": ["client_id", "client_secret"]},
}


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
    status = "connected" if (data.enabled and not missing) else "disconnected"
    patch = {
        "key": data.key, "enabled": data.enabled, "config": data.config,
        "status": status, "updated_at": _now_iso(),
    }
    if existing:
        await db.tenant_integrations.update_one(
            {"tenant_id": user["tenant_id"], "key": data.key},
            {"$set": patch},
        )
    else:
        cfg = IntegrationConfig(tenant_id=user["tenant_id"], **data.model_dump(), status=status)
        await db.tenant_integrations.insert_one(cfg.model_dump())
    doc = await db.tenant_integrations.find_one({"tenant_id": user["tenant_id"], "key": data.key}, {"_id": 0})
    return {"integration": doc, "missing": missing}


@router.delete("/{key}")
async def disconnect(key: str, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    await db.tenant_integrations.delete_one({"tenant_id": user["tenant_id"], "key": key})
    return {"status": "ok"}
