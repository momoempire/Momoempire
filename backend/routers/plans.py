"""Plans + cost configuration — admin-managed, drives usage limits and gross-margin math."""
from fastapi import APIRouter, Depends, HTTPException
from db import get_db
from models import _now_iso
from models import Plan, PlanIn, CostConfig, CostConfigIn
from security import require_platform_admin

router = APIRouter(prefix="/admin/plans", tags=["admin-plans"], dependencies=[Depends(require_platform_admin)])


DEFAULT_PLANS = [
    {"key": "trial", "name": "Free Trial / Sandbox", "price_cents": 0, "trial_days": 60, "sort_order": 1,
     "description": "Two-month sandbox for testing with family, friends, or a few real calls.",
     "limits": {"ai_minutes": 30, "calls": 50, "sms": 100, "ai_interactions": 300, "locations": 1, "users": 1, "phone_numbers": 0, "personas": 1, "integrations": 1},
     "features": ["Basic AI receptionist", "Limited calls", "No branded page", "No customer portal"]},
    {"key": "starter", "name": "Starter", "price_cents": 1999, "sort_order": 10,
     "description": "Say goodbye to voicemail jail.",
     "limits": {"ai_minutes": 100, "calls": 150, "sms": 300, "ai_interactions": 1500, "locations": 1, "users": 2, "phone_numbers": 1, "personas": 1, "integrations": 2},
     "overage": {"ai_minutes": 25, "calls": 5, "sms": 2},
     "features": ["Basic AI receptionist", "Basic SMS", "Basic business configuration", "Basic branded presence", "Usage warnings"]},
    {"key": "growth", "name": "Growth", "price_cents": 4999, "sort_order": 20,
     "description": "Capture every lead, book every job.",
     "limits": {"ai_minutes": 300, "calls": 500, "sms": 1000, "ai_interactions": 5000, "locations": 1, "users": 5, "phone_numbers": 2, "personas": 1, "integrations": 5},
     "overage": {"ai_minutes": 20, "calls": 4, "sms": 1.5},
     "features": ["Advanced receptionist", "Appointment booking", "Follow-ups", "Missed-call recovery", "More SMS", "More automation"]},
    {"key": "ai_office", "name": "AI Office", "price_cents": 9999, "sort_order": 30,
     "description": "Your full digital office.",
     "limits": {"ai_minutes": 650, "calls": 1500, "sms": 3000, "ai_interactions": 15000, "locations": 2, "users": 10, "phone_numbers": 3, "personas": 2, "integrations": 10},
     "overage": {"ai_minutes": 15, "calls": 3, "sms": 1},
     "features": ["Full AI employee", "Scheduling", "Customer management", "Lead recovery", "Reviews", "Business intelligence", "AI business advisor", "Customer portal"]},
    {"key": "high_volume", "name": "High Volume", "price_cents": 19999, "sort_order": 40,
     "description": "For busy shops with multiple locations.",
     "limits": {"ai_minutes": 1400, "calls": 4000, "sms": 8000, "ai_interactions": 40000, "locations": 5, "users": 25, "phone_numbers": 10, "personas": 5, "integrations": 25},
     "overage": {"ai_minutes": 12, "calls": 2, "sms": 0.75},
     "features": ["Higher call volume", "Multiple locations", "More phone numbers", "Advanced integrations", "Advanced automation", "Multiple AI personas", "Advanced reporting"]},
    {"key": "enterprise", "name": "Enterprise", "price_cents": 0, "interval": "custom", "sort_order": 50,
     "description": "Custom pricing. Fair-use policies apply.",
     "limits": {}, "overage": {},
     "features": ["Custom AI minutes", "Custom locations", "Custom users", "Custom phone numbers", "Custom integrations", "Multiple AI employees/personas", "Custom SMS volume", "Priority support", "Custom workflows"]},
]


async def seed_plans():
    db = get_db()
    for cfg in DEFAULT_PLANS:
        if await db.plans.find_one({"key": cfg["key"]}):
            continue
        p = Plan(**cfg)
        await db.plans.insert_one(dict(p.model_dump()))
    # Seed default cost config
    if not await db.cost_config.find_one({"id": "singleton"}):
        default = CostConfig(costs={"ai_minutes": 8, "calls": 2, "sms": 0.5, "storage_gb": 1, "payment_processing_bps": 290})
        await db.cost_config.insert_one(dict(default.model_dump()))


@router.get("")
async def list_plans():
    db = get_db()
    return await db.plans.find({}, {"_id": 0}).sort("sort_order", 1).to_list(100)


@router.get("/public")
async def public_plans():
    """Public endpoint for pricing page — only is_public=true."""
    db = get_db()
    return await db.plans.find({"is_public": True}, {"_id": 0}).sort("sort_order", 1).to_list(100)


@router.post("")
async def create_plan(data: PlanIn):
    db = get_db()
    if await db.plans.find_one({"key": data.key}):
        raise HTTPException(400, "Plan key already exists")
    p = Plan(**data.model_dump())
    await db.plans.insert_one(dict(p.model_dump()))
    return p.model_dump()


@router.put("/{key}")
async def update_plan(key: str, data: PlanIn):
    db = get_db()
    patch = data.model_dump()
    patch["updated_at"] = _now_iso()
    res = await db.plans.update_one({"key": key}, {"$set": patch})
    if res.matched_count == 0:
        raise HTTPException(404, "Plan not found")
    return await db.plans.find_one({"key": key}, {"_id": 0})


@router.delete("/{key}")
async def delete_plan(key: str):
    db = get_db()
    res = await db.plans.delete_one({"key": key})
    if res.deleted_count == 0:
        raise HTTPException(404, "Plan not found")
    return {"status": "ok"}


# ---------- Cost config ----------
@router.get("/costs/current")
async def get_costs():
    db = get_db()
    doc = await db.cost_config.find_one({"id": "singleton"}, {"_id": 0})
    return doc or {"costs": {}, "updated_at": None}


@router.put("/costs/current")
async def set_costs(data: CostConfigIn):
    db = get_db()
    await db.cost_config.update_one(
        {"id": "singleton"},
        {"$set": {"id": "singleton", "costs": data.costs, "updated_at": _now_iso()}},
        upsert=True,
    )
    return await db.cost_config.find_one({"id": "singleton"}, {"_id": 0})


# Public plan read (no auth)
public_router = APIRouter(prefix="/plans", tags=["plans-public"])


@public_router.get("")
async def public_plans_list():
    db = get_db()
    rows = await db.plans.find({"is_public": True}, {"_id": 0, "stripe_price_id": 0}).sort("sort_order", 1).to_list(100)
    return rows
