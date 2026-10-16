"""Expert-mode AI employee features:
  - Objection handler library (admin-curated rebuttals)
  - Persona depth (Receptionist / Sales Pro / Industry Expert)
  - Instant quote estimator (ballpark from pricing docs)
  - Real-time coach for human takeover
"""
import os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
from db import get_db
from models import _uuid, _now_iso
from security import require_tenant_user, require_tenant_owner_or_admin

router = APIRouter(prefix="/sales", tags=["sales-expert"])


# ---------- Objection library ----------
class ObjectionIn(BaseModel):
    pattern: str                                     # trigger phrase/keywords
    rebuttal: str                                    # what the AI says back
    tags: List[str] = Field(default_factory=list)


@router.get("/objections")
async def list_objections(user: dict = Depends(require_tenant_user)):
    db = get_db()
    return await db.objections.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).sort("created_at", -1).to_list(200)


@router.post("/objections")
async def create_objection(data: ObjectionIn, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    doc = {"id": _uuid(), "tenant_id": user["tenant_id"], "pattern": data.pattern.strip(),
           "rebuttal": data.rebuttal.strip(), "tags": data.tags, "created_at": _now_iso()}
    await db.objections.insert_one(doc.copy())
    doc.pop("_id", None)
    return doc


@router.put("/objections/{oid}")
async def update_objection(oid: str, data: ObjectionIn, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    res = await db.objections.update_one(
        {"id": oid, "tenant_id": user["tenant_id"]},
        {"$set": {"pattern": data.pattern, "rebuttal": data.rebuttal, "tags": data.tags, "updated_at": _now_iso()}},
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Not found")
    return await db.objections.find_one({"id": oid}, {"_id": 0})


@router.delete("/objections/{oid}")
async def delete_objection(oid: str, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    res = await db.objections.delete_one({"id": oid, "tenant_id": user["tenant_id"]})
    if res.deleted_count == 0:
        raise HTTPException(404, "Not found")
    return {"status": "ok"}


# ---------- Persona depth ----------
PERSONA_PRESETS = {
    "receptionist": {
        "label": "Receptionist",
        "tone": "Friendly and brisk. Books jobs, takes messages, keeps it short.",
    },
    "sales_pro": {
        "label": "Sales Pro",
        "tone": "Consultative. Qualifies the lead, highlights value, closes with urgency.",
    },
    "industry_expert": {
        "label": "Industry Expert",
        "tone": "Technically fluent. Uses correct industry terms, cites specs, builds trust.",
    },
}


class PersonaIn(BaseModel):
    key: str                      # one of PERSONA_PRESETS keys OR 'custom'
    custom_tone: str = ""         # when key='custom'


@router.get("/persona")
async def get_persona(user: dict = Depends(require_tenant_user)):
    db = get_db()
    doc = await db.ai_personas.find_one({"tenant_id": user["tenant_id"]}, {"_id": 0})
    return doc or {"tenant_id": user["tenant_id"], "key": "receptionist", "custom_tone": "",
                   "presets": PERSONA_PRESETS}


@router.put("/persona")
async def set_persona(data: PersonaIn, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    await db.ai_personas.update_one(
        {"tenant_id": user["tenant_id"]},
        {"$set": {"tenant_id": user["tenant_id"], "key": data.key,
                  "custom_tone": data.custom_tone or "", "updated_at": _now_iso()}},
        upsert=True,
    )
    return await db.ai_personas.find_one({"tenant_id": user["tenant_id"]}, {"_id": 0})


# ---------- Instant quote estimator ----------
class QuoteIn(BaseModel):
    service_description: str
    customer_notes: str = ""


@router.post("/quote-estimate")
async def quote_estimate(data: QuoteIn, user: dict = Depends(require_tenant_user)):
    """Use services + knowledge base to produce a ballpark range. LLM-grounded."""
    db = get_db()
    services = await db.services.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).to_list(200)
    knowledge = await db.knowledge.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).to_list(50)
    tenant = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0, "industry_slug": 1, "name": 1}) or {}

    svc_lines = [f"- {s.get('name')} (${s.get('price') or '?'}, ~{s.get('duration_minutes','?')}min)" for s in services[:30]]
    kb_lines = [f"- Q: {k.get('question')}\n  A: {k.get('answer')}" for k in knowledge[:15] if k.get("question")]
    svc_block = "\n".join(svc_lines) or "(no services configured)"
    kb_block = "\n".join(kb_lines) or "(no knowledge configured)"

    # Deterministic fallback: find services with matching keyword
    desc = (data.service_description or "").lower()
    hits = [s for s in services if (s.get("name") or "").lower() in desc or any(w in desc for w in (s.get("name") or "").lower().split())]
    fallback_low = min([s.get("price", 0) or 0 for s in hits] or [0])
    fallback_high = max([s.get("price", 0) or 0 for s in hits] or [0])
    fallback = {
        "low": int(fallback_low or 0),
        "high": int(fallback_high or 0) if fallback_high else int(fallback_low or 0),
        "confidence": "low" if not hits else "medium",
        "rationale": (
            f"Matched {len(hits)} service(s) from the catalog." if hits
            else "No service price match found — ask the caller for more details or escalate."
        ),
        "disclaimer": "Ballpark only — final price depends on on-site assessment.",
        "method": "rules",
    }

    from llm_portable import ai_api_key
    key = ai_api_key()
    if not key:
        return fallback
    try:
        from llm_portable import LlmChat, UserMessage
        system = (
            "You are a quote estimator for a service business. Given the business's services + knowledge, "
            "produce a conservative BALLPARK range. Return ONLY JSON: "
            '{"low":number, "high":number, "confidence":"low|medium|high", "rationale":"...", "disclaimer":"..."}. '
            "If the request is outside the catalog, say confidence='low' and recommend an on-site assessment."
        )
        chat = LlmChat(api_key=key, session_id=f"quote-{user['tenant_id'][:6]}", system_message=system).with_model("openai", "gpt-6-sol")
        user_text = (
            f"Business: {tenant.get('name','')} ({tenant.get('industry_slug','small business')})\n"
            f"Services catalog:\n{svc_block}\n\nKnowledge:\n{kb_block}\n\n"
            f"Customer wants: {data.service_description}\nNotes: {data.customer_notes or '(none)'}"
        )
        raw = await chat.send_message(UserMessage(text=user_text))
        text = raw if isinstance(raw, str) else getattr(raw, "content", str(raw))
        import re as _re, json as _json
        m = _re.search(r"\{[\s\S]*\}", text)
        if not m:
            return fallback
        parsed = _json.loads(m.group(0))
        parsed.setdefault("method", "llm")
        parsed.setdefault("disclaimer", "Ballpark only — final price depends on on-site assessment.")
        return parsed
    except Exception as e:
        print(f"[quote-estimate] LLM failed: {e}")
        return fallback


# ---------- Real-time coach ----------
class CoachIn(BaseModel):
    transcript: str                 # recent conversation text
    scenario: str = "general"       # 'objection' | 'close' | 'upsell' | 'general'


@router.post("/coach")
async def coach_suggestions(data: CoachIn, user: dict = Depends(require_tenant_user)):
    """Return 3-5 one-liners a human agent can read verbatim."""
    db = get_db()
    tenant = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0, "name": 1, "industry_slug": 1}) or {}
    objections = await db.objections.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).to_list(50)
    obj_block = "\n".join(f"- Pattern: {o['pattern']}\n  Rebuttal: {o['rebuttal']}" for o in objections[:12]) or "(none)"

    from llm_portable import ai_api_key
    key = ai_api_key()
    if not key:
        # Deterministic fallback — surface objection matches
        hits = []
        txt = (data.transcript or "").lower()
        for o in objections:
            if o.get("pattern") and o["pattern"].lower() in txt:
                hits.append(o["rebuttal"])
        if not hits:
            hits = [
                "Could you tell me a bit more about what you're trying to solve?",
                "What timeline are you working with?",
                "Would it help if we scheduled a quick look? I have an opening this week.",
            ]
        return {"suggestions": hits[:5], "method": "rules"}
    try:
        from llm_portable import LlmChat, UserMessage
        system = (
            "You are a sales coach whispering lines to a human agent in real time. Return ONLY JSON: "
            '{"suggestions":["...","...","..."]} with 3-5 short sentences the agent can read verbatim. '
            "Prefer rebuttals from the business's objection library when relevant."
        )
        chat = LlmChat(api_key=key, session_id=f"coach-{user['tenant_id'][:6]}", system_message=system).with_model("openai", "gpt-6-sol")
        user_text = (
            f"Business: {tenant.get('name','')} ({tenant.get('industry_slug','')})\n"
            f"Scenario: {data.scenario}\n\n"
            f"Objection library:\n{obj_block}\n\n"
            f"Recent transcript:\n{data.transcript[:3000]}\n\n"
            "Suggest the next 3-5 lines for the human agent."
        )
        raw = await chat.send_message(UserMessage(text=user_text))
        text = raw if isinstance(raw, str) else getattr(raw, "content", str(raw))
        import re as _re, json as _json
        m = _re.search(r"\{[\s\S]*\}", text)
        if not m:
            return {"suggestions": [], "method": "llm-empty"}
        parsed = _json.loads(m.group(0))
        parsed["method"] = "llm"
        parsed["suggestions"] = (parsed.get("suggestions") or [])[:5]
        return parsed
    except Exception as e:
        print(f"[coach] LLM failed: {e}")
        return {"suggestions": [
            "Could you tell me a bit more about what you're trying to solve?",
            "What timeline are you working with?",
            "I have an opening this week — want me to pencil you in?",
        ], "method": "fallback"}
