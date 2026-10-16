"""Industry intelligence briefs via GPT-6 Sol — cached per tenant."""
import os
from fastapi import APIRouter, HTTPException, Depends
from datetime import datetime, timezone, timedelta
from db import get_db
from models import IndustryBrief
from security import require_tenant_user

router = APIRouter(prefix="/tenants/industry-intel", tags=["industry-intel"])


@router.get("/brief")
async def get_brief(user: dict = Depends(require_tenant_user), refresh: bool = False):
    db = get_db()
    tid = user["tenant_id"]
    tenant = await db.tenants.find_one({"id": tid}, {"_id": 0})
    if not tenant or not tenant.get("industry_slug"):
        raise HTTPException(400, "Set your industry first")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    if not refresh:
        cached = await db.industry_briefs.find_one(
            {"tenant_id": tid, "created_at": {"$gte": cutoff}}, {"_id": 0},
            sort=[("created_at", -1)],
        )
        if cached:
            return cached

    industry = await db.industries.find_one({"slug": tenant["industry_slug"]}, {"_id": 0})
    season = ["Winter", "Spring", "Summer", "Autumn"][((datetime.now(timezone.utc).month % 12) // 3)]
    prompt = (
        f"You are an industry intelligence analyst for a {industry.get('name') if industry else tenant.get('industry_slug')} "
        f"small business in {tenant.get('address',{}).get('city') or 'the US'}. "
        f"The current season is {season}. Produce a crisp, action-oriented brief in markdown with these sections:\n"
        f"1. Three industry trends to watch this quarter\n"
        f"2. Two technology or process updates\n"
        f"3. Two seasonal customer demands (what will people call about soon)\n"
        f"4. Three marketing ideas to run this month\n"
        f"5. One operational recommendation\n"
        f"6. Compliance considerations (note broadly; recommend they verify with their state/local regulator — NEVER present yourself as a lawyer or provide definitive legal advice)\n"
        f"Max 400 words. No preamble."
    )

    content = ""
    try:
        from llm_portable import LlmChat, UserMessage, ai_api_key
        key = ai_api_key()
        chat = LlmChat(api_key=key, session_id=f"intel-{tid}", system_message="You produce short, data-aware industry briefs for SMB owners.").with_model("openai", "gpt-6-sol")
        raw = await chat.send_message(UserMessage(text=prompt))
        content = raw if isinstance(raw, str) else getattr(raw, "content", str(raw))
    except Exception as e:
        content = (
            "## Industry brief (fallback)\n\n"
            "- Keep an eye on **seasonal demand** typical for your industry this quarter.\n"
            "- **Automate review requests** right after every job — compounds reputation fast.\n"
            "- **Promote your highest-margin service** on your top channel.\n\n"
            "_(Live insights unavailable right now — showing a cached summary.)_"
        )

    brief = IndustryBrief(tenant_id=tid, industry_slug=tenant["industry_slug"], content=content,
                          topics=["trends", "tech", "seasonal", "marketing", "ops", "compliance"])
    await db.industry_briefs.insert_one(dict(brief.model_dump()))
    return brief.model_dump()


@router.get("/history")
async def history(user: dict = Depends(require_tenant_user)):
    db = get_db()
    return await db.industry_briefs.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0},
    ).sort("created_at", -1).to_list(10)
