"""Custom domain CNAME wizard + DNS verify."""
import os
import socket
from fastapi import APIRouter, HTTPException, Depends
from db import get_db
from models import Domain, DomainIn
from models import _now_iso
from security import require_tenant_owner_or_admin

router = APIRouter(prefix="/tenants/domains", tags=["domains"])

APEX_HOST = os.environ.get("CNAME_TARGET_HOST", "office-engine.preview.emergentagent.com")


@router.get("")
async def list_domains(user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    return await db.domains.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).sort("created_at", -1).to_list(50)


@router.post("")
async def add_domain(data: DomainIn, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    domain = data.domain.strip().lower().removeprefix("http://").removeprefix("https://").split("/")[0]
    if not domain or "." not in domain:
        raise HTTPException(400, "Invalid domain")
    if await db.domains.find_one({"domain": domain}):
        raise HTTPException(400, "Domain already added")
    d = Domain(tenant_id=user["tenant_id"], domain=domain, cname_target=APEX_HOST)
    doc = d.model_dump()
    await db.domains.insert_one(doc)
    doc.pop("_id", None)
    return doc


@router.post("/{domain_id}/verify")
async def verify_domain(domain_id: str, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    d = await db.domains.find_one({"id": domain_id, "tenant_id": user["tenant_id"]})
    if not d:
        raise HTTPException(404, "Domain not found")
    status = "failed"
    detail = ""
    try:
        # Try to resolve CNAME chain → verify ends at APEX_HOST
        target = d["domain"]
        # Follow CNAME via gethostbyname_ex which returns aliaslist for cname chain
        name, aliases, _ = socket.gethostbyname_ex(target)
        chain = [name, *aliases]
        if any(APEX_HOST in c for c in chain) or APEX_HOST in name:
            status = "verified"
        else:
            detail = f"CNAME resolves to {chain} (expected {APEX_HOST})"
    except Exception as e:
        detail = f"DNS lookup failed: {e}"
    await db.domains.update_one(
        {"id": domain_id},
        {"$set": {"status": status, "last_checked_at": _now_iso()}},
    )
    out = await db.domains.find_one({"id": domain_id}, {"_id": 0})
    out["detail"] = detail
    return out


@router.delete("/{domain_id}")
async def remove_domain(domain_id: str, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    res = await db.domains.delete_one({"id": domain_id, "tenant_id": user["tenant_id"]})
    if res.deleted_count == 0:
        raise HTTPException(404, "Not found")
    return {"status": "ok"}
