"""Plans + cost configuration — admin-managed, drives usage limits and gross-margin math."""
from fastapi import APIRouter, Depends, HTTPException
from db import get_db
from models import _now_iso
from models import Plan, PlanIn, CostConfig, CostConfigIn
from security import require_platform_admin

router = APIRouter(prefix="/admin/plans", tags=["admin-plans"], dependencies=[Depends(require_platform_admin)])


# Locked pricing (decided 2026-10-15): Lite $9.99 / Starter $39.99 / Pro $159.99 / Empire $369.99.
# Telephony bundled with minute allowances; overage in cents/min or cents/sms.
# NOTE: keys were renamed from the old growth/ai_office/high_volume scheme. On any
# DB seeded before this change, delete the stale keys via the admin plans API.
DEFAULT_PLANS = [
    {"key": "trial", "name": "Free Trial / Sandbox", "price_cents": 0, "trial_days": 60, "sort_order": 1,
     "description": "Two-month sandbox for testing with family, friends, or a few real calls.",
     "limits": {"ai_minutes": 30, "calls": 50, "sms": 100, "ai_interactions": 300, "locations": 1, "users": 1, "phone_numbers": 0, "personas": 1, "integrations": 1},
     "features": ["Basic AI receptionist", "Limited calls", "No branded page", "No customer portal"]},
    {"key": "lite", "name": "Lite", "price_cents": 999, "sort_order": 5,
     "description": "A taste of the AI office.",
     "limits": {"ai_minutes": 10, "calls": 25, "sms": 50, "ai_interactions": 500, "locations": 1, "users": 1, "phone_numbers": 1, "personas": 1, "integrations": 1},
     "overage": {"ai_minutes": 35, "calls": 5, "sms": 2},
     "features": ["Core dispatch & scheduling", "Pricebook", "Invoicing & payments", "AI receptionist (10 min/mo)", "1 phone number"]},
    {"key": "starter", "name": "Starter", "price_cents": 3999, "sort_order": 10,
     "description": "Say goodbye to voicemail jail.",
     "limits": {"ai_minutes": 60, "calls": 150, "sms": 200, "ai_interactions": 1500, "locations": 1, "users": 2, "phone_numbers": 1, "personas": 1, "integrations": 2},
     "overage": {"ai_minutes": 30, "calls": 5, "sms": 2},
     "features": ["Everything in Lite", "AI photo estimates", "GPS tracking", "Review requests", "60 AI minutes/mo", "Up to 2 users"]},
    {"key": "pro", "name": "Pro", "price_cents": 15999, "sort_order": 20,
     "description": "Your full AI office. Unlimited users.",
     "limits": {"ai_minutes": 300, "calls": 800, "sms": 1000, "ai_interactions": 8000, "locations": 2, "users": 25, "phone_numbers": 3, "personas": 2, "integrations": 10},
     "overage": {"ai_minutes": 25, "calls": 4, "sms": 1.5},
     "features": ["Everything in Starter", "Unlimited users", "Full AI receptionist", "Marketing automation", "Outbound voice AI", "3 phone numbers"]},
    {"key": "empire", "name": "Empire", "price_cents": 36999, "sort_order": 30,
     "description": "Superpowers for growing empires.",
     "limits": {"ai_minutes": 1000, "calls": 2500, "sms": 5000, "ai_interactions": 25000, "locations": 10, "users": 100, "phone_numbers": 10, "personas": 5, "integrations": 25},
     "overage": {"ai_minutes": 20, "calls": 3, "sms": 1},
     "features": ["Everything in Pro", "Multi-location command center", "Predictive AI suite", "Cloned owner voice", "White-label experience", "AI success manager & AI staff"]},
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
