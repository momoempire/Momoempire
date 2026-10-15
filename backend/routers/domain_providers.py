"""Domain provider abstraction — pluggable. Default = 'manual' (CNAME wizard)."""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List
from db import get_db
from models import DomainSearchIn, DomainPurchaseIn
from security import require_tenant_user, require_platform_admin


class DomainResult(BaseModel):
    domain: str
    available: bool
    price_cents: int
    provider: str


class BaseProvider:
    key: str = "base"
    label: str = "Base"

    async def search(self, query: str) -> List[dict]:
        raise NotImplementedError

    async def purchase(self, domain: str, years: int) -> dict:
        raise NotImplementedError


class ManualProvider(BaseProvider):
    """No-op provider: user already owns a domain. We only accept it and route to CNAME wizard."""
    key = "manual"; label = "I already own a domain"

    async def search(self, query: str) -> List[dict]:
        return []

    async def purchase(self, domain: str, years: int) -> dict:
        return {"status": "manual", "message": "Add this domain in Website → Custom Domain and point its CNAME to the shown target."}


class DemoProvider(BaseProvider):
    """Deterministic demo results so the UX is testable without a registrar key."""
    key = "demo_registrar"; label = "Demo Registrar"

    TLDS = [".com", ".co", ".io", ".app", ".biz"]
    SUFFIXES = ["hq", "pro", "co", "studio", "works"]

    async def search(self, query: str) -> List[dict]:
        base = "".join(c for c in query.lower() if c.isalnum())[:30]
        if not base:
            return []
        results = []
        for tld in self.TLDS:
            price = 1299 if tld == ".com" else 999
            results.append({"domain": f"{base}{tld}", "available": tld != ".com" or len(base) > 7, "price_cents": price, "provider": self.key})
        for s in self.SUFFIXES:
            results.append({"domain": f"{base}{s}.com", "available": True, "price_cents": 1299, "provider": self.key})
        return results

    async def purchase(self, domain: str, years: int) -> dict:
        return {"status": "simulated", "domain": domain, "years": years, "cents": 1299 * years,
                "message": "Demo registrar purchase simulated. Add DNS records in your real registrar to activate."}


PROVIDERS: dict[str, BaseProvider] = {
    "manual": ManualProvider(),
    "demo_registrar": DemoProvider(),
}


# ---------- Tenant-facing router ----------
router = APIRouter(prefix="/tenants/domain-providers", tags=["domain-providers"])


@router.get("/catalog")
async def catalog(user: dict = Depends(require_tenant_user)):
    db = get_db()
    doc = await db.domain_provider_config.find_one({"id": "singleton"}, {"_id": 0}) or {"active": ["manual", "demo_registrar"], "default": "demo_registrar"}
    return {
        "active": doc.get("active", []),
        "default": doc.get("default", "manual"),
        "providers": [{"key": p.key, "label": p.label} for p in PROVIDERS.values() if p.key in doc.get("active", [])],
    }


@router.post("/search")
async def search(data: DomainSearchIn, user: dict = Depends(require_tenant_user), provider: str | None = None):
    db = get_db()
    cfg = await db.domain_provider_config.find_one({"id": "singleton"}, {"_id": 0}) or {"default": "demo_registrar"}
    key = provider or cfg.get("default", "demo_registrar")
    p = PROVIDERS.get(key)
    if not p:
        raise HTTPException(400, "Unknown provider")
    results = await p.search(data.query)
    return {"provider": p.key, "results": results}


@router.post("/purchase")
async def purchase(data: DomainPurchaseIn, user: dict = Depends(require_tenant_user)):
    db = get_db()
    cfg = await db.domain_provider_config.find_one({"id": "singleton"}, {"_id": 0}) or {"default": "demo_registrar"}
    key = data.provider or cfg.get("default", "demo_registrar")
    p = PROVIDERS.get(key)
    if not p:
        raise HTTPException(400, "Unknown provider")
    out = await p.purchase(data.domain, data.years)
    # Auto-register the domain in the tenant's own Domain list (routes to CNAME wizard)
    from models import Domain
    import os
    apex = os.environ.get("CNAME_TARGET_HOST", "office-engine.preview.emergentagent.com")
    d = Domain(tenant_id=user["tenant_id"], domain=data.domain.lower(), cname_target=apex)
    await db.domains.insert_one(dict(d.model_dump()))
    out["domain_record"] = d.model_dump()
    return out


# ---------- Admin-facing config ----------
class ProviderConfigIn(BaseModel):
    active: List[str]
    default: str


admin_router = APIRouter(prefix="/admin/domain-providers", tags=["admin-domain-providers"],
                         dependencies=[Depends(require_platform_admin)])


@admin_router.get("")
async def admin_catalog():
    db = get_db()
    doc = await db.domain_provider_config.find_one({"id": "singleton"}, {"_id": 0}) or {"active": ["manual", "demo_registrar"], "default": "demo_registrar"}
    doc["available"] = [{"key": p.key, "label": p.label} for p in PROVIDERS.values()]
    return doc


@admin_router.put("")
async def admin_set(data: ProviderConfigIn):
    db = get_db()
    for k in data.active + [data.default]:
        if k not in PROVIDERS:
            raise HTTPException(400, f"Unknown provider: {k}")
    await db.domain_provider_config.update_one(
        {"id": "singleton"},
        {"$set": {"id": "singleton", "active": data.active, "default": data.default}},
        upsert=True,
    )
    return {"status": "ok"}
