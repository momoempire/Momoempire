"""Trade packs: per-trade starter kits (pricebook, appointment types, scripts).

Workers author packs as YAML in ``backend/trade_packs/packs/``; these
endpoints let a tenant browse them and one-click apply a pack to their
shop. Applying is idempotent: pricebook items whose name already exists
for the tenant are skipped, and re-applying stores the latest pack
metadata in ``tenant_settings``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin
from trade_packs import list_packs, load_pack, validate_pack

router = APIRouter(prefix="/tenants/trade-packs", tags=["trade-packs"])


@router.get("")
@router.get("/")
async def packs_list(user: dict = Depends(require_tenant_user)):
    """Pack summaries: id, trade, tagline, accent (validity included)."""
    return {"packs": list_packs(), "count": len(list_packs())}


@router.get("/{pack_id}")
async def pack_detail(pack_id: str, user: dict = Depends(require_tenant_user)):
    """Full pack dict for one pack."""
    try:
        return load_pack(pack_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{pack_id}/apply")
async def apply_pack(pack_id: str, user: dict = Depends(require_tenant_owner_or_admin)):
    """Validate the pack and seed its pricebook items into this tenant's
    catalog. Items whose name already exists are skipped (idempotent).
    Pack metadata is stored on the tenant's ``tenant_settings`` doc."""
    try:
        pack = load_pack(pack_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    try:
        validate_pack(pack)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    db = get_db()
    tenant_id = user["tenant_id"]
    now = _now_iso()

    existing = await db.pricebook_items.find(
        {"tenant_id": tenant_id}, {"_id": 0, "name": 1}
    ).to_list(5000)
    known_names = {str(r.get("name") or "") for r in existing}

    applied = 0
    skipped = 0
    for item in pack["pricebook"]:
        name = str(item.get("name") or "")
        if name in known_names:
            skipped += 1
            continue
        doc = {
            "_id": _uuid(),
            "id": _uuid(),
            "tenant_id": tenant_id,
            "name": name,
            "category": str(item.get("category") or "service"),
            "price": float(item.get("price") or 0),
            "cost": float(item.get("cost") or 0),
            "description": str(item.get("description") or ""),
            "duration_min": item.get("duration_min"),
            "options": [],
            "active": True,
            "version": 1,
            "source": f"trade-pack:{pack_id}",
            "created_at": now,
            "updated_at": now,
        }
        await db.pricebook_items.insert_one(doc)
        known_names.add(name)
        applied += 1

    await db.tenant_settings.update_one(
        {"tenant_id": tenant_id},
        {"$set": {
            "tenant_id": tenant_id,
            "trade_pack": {
                "pack_id": pack_id,
                "applied_at": now,
                "appointment_types": pack.get("appointment_types") or [],
                "equipment_types": pack.get("equipment_types") or [],
            },
            "updated_at": now,
        }},
        upsert=True,
    )

    return {"applied": applied, "skipped": skipped, "pack_id": pack_id}
