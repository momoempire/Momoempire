"""Data-grounded AI Business Advisor + Receptionist knowledge pass-through."""
import os
from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime, timezone, timedelta
from db import get_db
from models import _uuid, _now_iso, AdvisorMessageIn
from security import require_tenant_user
from routers.usage import record_usage
from routers.knowledge_docs import retrieve_relevant_chunks

router = APIRouter(prefix="/advisor", tags=["advisor"])


async def _tenant_snapshot(tid: str) -> dict:
    """Compact JSON of tenant's current state so the advisor's answers are grounded."""
    db = get_db()
    now = datetime.now(timezone.utc)
    since_30 = (now - timedelta(days=30)).isoformat()
    since_60 = (now - timedelta(days=60)).isoformat()
    since_90 = (now - timedelta(days=90)).isoformat()

    async def count(coll, filter_):
        return await db[coll].count_documents({"tenant_id": tid, **filter_})

    # Revenue this period / last period
    paid_now = [p async for p in db.invoices.find({"tenant_id": tid, "status": "paid", "paid_at": {"$gte": since_30}}, {"_id": 0, "total": 1, "title": 1})]
    paid_prev = [p async for p in db.invoices.find({"tenant_id": tid, "status": "paid", "paid_at": {"$gte": since_60, "$lt": since_30}}, {"_id": 0, "total": 1})]
    rev_now = sum(float(x.get("total", 0) or 0) for x in paid_now)
    rev_prev = sum(float(x.get("total", 0) or 0) for x in paid_prev)
    rev_delta_pct = ((rev_now - rev_prev) / rev_prev * 100) if rev_prev else None

    unanswered = await count("leads", {"status": "new", "created_at": {"$lte": (now - timedelta(hours=24)).isoformat()}})
    missed = await count("conversations", {"channel": "call", "status": "missed", "created_at": {"$gte": since_30}})
    completed = await count("appointments", {"status": "completed", "created_at": {"$gte": since_30}})
    upcoming = await count("appointments", {"status": {"$in": ["scheduled", "confirmed"]}, "start_at": {"$gte": now.isoformat()}})
    reviews_done = await count("review_requests", {"status": "responded", "created_at": {"$gte": since_30}})
    overdue_cutoff = (now - timedelta(days=30)).isoformat()
    overdue_invoices = await count("invoices", {"status": {"$in": ["sent", "overdue"]}, "created_at": {"$lte": overdue_cutoff}})

    # Top 3 services by booked appointment count in last 90 days
    pipeline = [
        {"$match": {"tenant_id": tid, "created_at": {"$gte": since_90}}},
        {"$group": {"_id": "$service_name", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}, {"$limit": 5},
    ]
    top_services = await db.appointments.aggregate(pipeline).to_list(5)
    top_services = [{"name": r["_id"] or "Unspecified", "count": r["count"]} for r in top_services]

    return {
        "revenue_30d": round(rev_now, 2),
        "revenue_prev_30d": round(rev_prev, 2),
        "revenue_change_pct": round(rev_delta_pct, 1) if rev_delta_pct is not None else None,
        "unanswered_leads_over_24h": unanswered,
        "missed_calls_30d": missed,
        "completed_jobs_30d": completed,
        "upcoming_appointments": upcoming,
        "review_responses_30d": reviews_done,
        "overdue_invoices_30plus_days": overdue_invoices,
        "top_services_90d": top_services,
        "paid_invoice_count_30d": len(paid_now),
    }


def _build_system(tenant: dict, industry: dict | None, snapshot: dict, knowledge_hits: list) -> str:
    import json
    base = [
        "You are the AI Business Advisor inside an AI Office SaaS platform.",
        "You answer questions using the business's ACTUAL data provided below (JSON).",
        "Give concise plain-English answers aimed at a non-technical SMB owner.",
        "Prefer action steps and specific numbers. Use short bullets. Avoid jargon.",
        "On compliance questions you may flag considerations and recommend verifying with the appropriate regulator — do NOT present yourself as a lawyer or provide definitive legal advice.",
    ]
    base.append(f"Business: {tenant.get('name')} ({industry.get('name') if industry else tenant.get('industry_slug','')})")
    if tenant.get("description"):
        base.append(f"About: {tenant['description']}")
    base.append("Current data snapshot (last 30 days unless noted):\n" + json.dumps(snapshot, indent=2))
    if knowledge_hits:
        kb = "\n\n".join([f"[{h['doc_title']}] {h['content'][:500]}" for h in knowledge_hits])
        base.append("Relevant internal knowledge:\n" + kb)
    return "\n\n".join(base)


@router.post("/chat")
async def advisor_chat(data: AdvisorMessageIn, user: dict = Depends(require_tenant_user)):
    db = get_db()
    tenant = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0})
    industry = None
    if tenant and tenant.get("industry_slug"):
        industry = await db.industries.find_one({"slug": tenant["industry_slug"]}, {"_id": 0})

    session_id = data.session_id or _uuid()
    snapshot = await _tenant_snapshot(user["tenant_id"])
    knowledge_hits = await retrieve_relevant_chunks(user["tenant_id"], data.message, limit=4)
    system = _build_system(tenant, industry, snapshot, knowledge_hits)

    await db.advisor_messages.insert_one({
        "tenant_id": user["tenant_id"], "session_id": session_id,
        "role": "user", "content": data.message, "created_at": _now_iso(),
    })

    try:
        from llm_portable import LlmChat, UserMessage, ai_api_key
        key = ai_api_key()
        if not key:
            raise RuntimeError("AI_API_KEY missing")
        chat = LlmChat(api_key=key, session_id=session_id, system_message=system).with_model("openai", "gpt-6-sol")
        reply = await chat.send_message(UserMessage(text=data.message))
        reply_text = reply if isinstance(reply, str) else getattr(reply, "content", str(reply))
    except Exception as e:
        # Data-grounded deterministic fallback
        parts = []
        if snapshot["revenue_change_pct"] is not None:
            arrow = "up" if snapshot["revenue_change_pct"] >= 0 else "down"
            parts.append(f"Revenue is **{arrow} {abs(snapshot['revenue_change_pct'])}%** vs prior 30 days (${snapshot['revenue_30d']} this period).")
        if snapshot["unanswered_leads_over_24h"]:
            parts.append(f"You have **{snapshot['unanswered_leads_over_24h']} leads unresponded** over 24h — call them today.")
        if snapshot["missed_calls_30d"]:
            parts.append(f"**{snapshot['missed_calls_30d']} missed calls** in the last 30 days — turn on missed-call text-back.")
        if snapshot["overdue_invoices_30plus_days"]:
            parts.append(f"**{snapshot['overdue_invoices_30plus_days']} invoices** are 30+ days overdue.")
        if snapshot["top_services_90d"]:
            parts.append("Top service by volume: **" + snapshot["top_services_90d"][0]["name"] + "** — consider promoting it.")
        if not parts:
            parts.append("Looks quiet. A focused push on new leads this week will set up next month.")
        reply_text = " ".join(parts) + f"\n\n_(Model error: {type(e).__name__} — showing a data-grounded summary.)_"

    await db.advisor_messages.insert_one({
        "tenant_id": user["tenant_id"], "session_id": session_id,
        "role": "assistant", "content": reply_text, "created_at": _now_iso(),
    })
    await record_usage(user["tenant_id"], "ai_interactions", 1, {"session_id": session_id})
    return {"session_id": session_id, "reply": reply_text, "snapshot": snapshot}


@router.get("/snapshot")
async def snapshot(user: dict = Depends(require_tenant_user)):
    return await _tenant_snapshot(user["tenant_id"])


@router.get("/sessions")
async def list_sessions(user: dict = Depends(require_tenant_user)):
    db = get_db()
    pipeline = [
        {"$match": {"tenant_id": user["tenant_id"]}},
        {"$sort": {"created_at": -1}},
        {"$group": {"_id": "$session_id", "last": {"$first": "$content"}, "updated_at": {"$first": "$created_at"}}},
        {"$sort": {"updated_at": -1}},
        {"$limit": 20},
    ]
    rows = await db.advisor_messages.aggregate(pipeline).to_list(50)
    return [{"session_id": r["_id"], "last": r["last"], "updated_at": r["updated_at"]} for r in rows]


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    msgs = await db.advisor_messages.find(
        {"tenant_id": user["tenant_id"], "session_id": session_id}, {"_id": 0},
    ).sort("created_at", 1).to_list(500)
    return msgs
