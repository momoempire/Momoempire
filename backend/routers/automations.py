"""Automation preferences per tenant."""
from fastapi import APIRouter, Depends, HTTPException
from db import get_db
from models import _now_iso
from models import AutomationSettings
from security import require_tenant_owner_or_admin, require_tenant_user

router = APIRouter(prefix="/tenants/automations", tags=["automations"])


@router.get("")
async def get_settings(user: dict = Depends(require_tenant_user)):
    db = get_db()
    doc = await db.tenant_automations.find_one({"tenant_id": user["tenant_id"]}, {"_id": 0})
    if not doc:
        return {"tenant_id": user["tenant_id"], **AutomationSettings().model_dump()}
    return doc


@router.put("")
async def put_settings(data: AutomationSettings, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    payload = {"tenant_id": user["tenant_id"], **data.model_dump(), "updated_at": _now_iso()}
    await db.tenant_automations.update_one(
        {"tenant_id": user["tenant_id"]},
        {"$set": payload},
        upsert=True,
    )
    return payload
