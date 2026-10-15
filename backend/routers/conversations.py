"""Conversations: calls, SMS, voicemails, receptionist simulator."""
from fastapi import APIRouter, HTTPException, Depends
from typing import List, Optional
from datetime import datetime, timezone
from db import get_db
from models import _uuid, _now_iso, Lead, Appointment, Customer
from models import (
    Conversation, ConversationIn, ConvMessage, ConvMessageIn, SimulateCallerIn,
)
from security import require_tenant_user
from ai_receptionist import receptionist_reply
from routers.usage import record_usage, usage_capped
from routers.knowledge_docs import retrieve_relevant_chunks
from routers.ai_quality import scan_conversation

router = APIRouter(prefix="/conversations", tags=["conversations"])


async def _tenant_bundle(tenant_id: str) -> tuple[dict, Optional[dict], list, list, list, Optional[dict], Optional[dict], list]:
    db = get_db()
    tenant = await db.tenants.find_one({"id": tenant_id}, {"_id": 0})
    if not tenant:
        raise HTTPException(404, "Tenant missing")
    industry = None
    if tenant.get("industry_slug"):
        industry = await db.industries.find_one({"slug": tenant["industry_slug"]}, {"_id": 0})
    services = await db.services.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(200)
    knowledge = await db.knowledge.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(200)
    upsells = await db.upsells.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(100)
    policy = await db.discount_policies.find_one({"tenant_id": tenant_id}, {"_id": 0})
    persona = await db.ai_personas.find_one({"tenant_id": tenant_id}, {"_id": 0})
    objections = await db.objections.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(100)
    return tenant, industry, services, knowledge, upsells, policy, persona, objections


async def _ensure_customer_or_lead(tenant_id: str, name: str, phone: str, email: str = "") -> dict:
    """Returns {customer_id, lead_id} — creates lead if phone/email unknown."""
    db = get_db()
    result = {"customer_id": None, "lead_id": None}
    if phone:
        cust = await db.customers.find_one({"tenant_id": tenant_id, "phone": phone}, {"_id": 0})
        if cust:
            result["customer_id"] = cust["id"]
            return result
    if not phone and not email:
        return result
    # Not an existing customer — create a lead
    lead = Lead(tenant_id=tenant_id, name=name or "Unknown caller", phone=phone, email=(email or None), source="call", status="new")
    doc = lead.model_dump()
    await db.leads.insert_one(doc)
    result["lead_id"] = doc["id"]
    return result


@router.get("")
async def list_conversations(user: dict = Depends(require_tenant_user), channel: Optional[str] = None, limit: int = 100):
    db = get_db()
    q = {"tenant_id": user["tenant_id"]}
    if channel:
        q["channel"] = channel
    rows = await db.conversations.find(q, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return rows


@router.get("/{conv_id}")
async def get_conversation(conv_id: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    conv = await db.conversations.find_one({"id": conv_id, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not conv:
        raise HTTPException(404, "Conversation not found")
    msgs = await db.conv_messages.find(
        {"conversation_id": conv_id, "tenant_id": user["tenant_id"]}, {"_id": 0}
    ).sort("created_at", 1).to_list(500)
    return {"conversation": conv, "messages": msgs}


@router.post("/start")
async def start_conversation(data: ConversationIn, user: dict = Depends(require_tenant_user)):
    db = get_db()
    tenant, _, _, _, _, _, _, _ = await _tenant_bundle(user["tenant_id"])
    conv = Conversation(
        tenant_id=user["tenant_id"],
        **data.model_dump(),
    )
    # tie to existing customer if phone matches
    link = await _ensure_customer_or_lead(user["tenant_id"], data.caller_name, data.caller_phone, data.caller_email)
    doc = conv.model_dump()
    doc.update(link)
    await db.conversations.insert_one(doc)
    doc.pop("_id", None)
    # Initial greeting as AI message
    greeting = (tenant.get("ai_employee") or {}).get("greeting") or f"Hi, thanks for calling {tenant.get('name')}. How can I help?"
    greet_msg = ConvMessage(conversation_id=doc["id"], tenant_id=user["tenant_id"], role="ai", content=greeting)
    await db.conv_messages.insert_one(greet_msg.model_dump())
    await record_usage(user["tenant_id"], "calls", 1, {"conversation_id": doc["id"], "sim": data.is_simulation})
    return {"conversation": doc, "greeting": greeting}


@router.post("/{conv_id}/caller-turn")
async def caller_turn(conv_id: str, data: ConvMessageIn, user: dict = Depends(require_tenant_user)):
    """Caller sends an utterance. AI processes, may take an action, replies."""
    db = get_db()
    conv = await db.conversations.find_one({"id": conv_id, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not conv:
        raise HTTPException(404, "Conversation not found")

    # Store caller message
    caller_msg = ConvMessage(conversation_id=conv_id, tenant_id=user["tenant_id"], role="caller", content=data.text)
    await db.conv_messages.insert_one(caller_msg.model_dump())

    # Load context
    tenant, industry, services, knowledge, upsells, policy, persona, objections = await _tenant_bundle(user["tenant_id"])
    history = await db.conv_messages.find(
        {"conversation_id": conv_id}, {"_id": 0}
    ).sort("created_at", 1).to_list(50)

    capped = await usage_capped(user["tenant_id"], "ai_interactions")
    ai = await receptionist_reply(tenant, industry, services, knowledge, history, data.text,
                                  usage_capped=capped, upsells=upsells, discount_policy=policy,
                                  persona=persona, objections=objections)

    # Execute structured action (idempotent, side-effect side)
    action = ai.get("action") or None
    action_result = None
    update_conv: dict = {}
    if action and isinstance(action, dict):
        atype = action.get("type")
        payload = action.get("payload") or {}
        if atype == "book_appointment":
            start_at = payload.get("start_at") or datetime.now(timezone.utc).isoformat()
            end_at = payload.get("end_at") or start_at
            name = payload.get("customer_name") or conv.get("caller_name") or "New caller"
            phone = payload.get("customer_phone") or conv.get("caller_phone") or ""
            svc_name = payload.get("service_name") or ""
            appt = Appointment(
                tenant_id=user["tenant_id"], customer_name=name, customer_phone=phone,
                service_name=svc_name, start_at=start_at, end_at=end_at,
                status="scheduled", notes=payload.get("notes") or f"Booked by AI from conv {conv_id}",
            )
            await db.appointments.insert_one(appt.model_dump())
            # ensure customer record
            cust = await db.customers.find_one({"tenant_id": user["tenant_id"], "phone": phone}) if phone else None
            if not cust and phone:
                c = Customer(tenant_id=user["tenant_id"], name=name, phone=phone)
                await db.customers.insert_one(c.model_dump())
                update_conv["customer_id"] = c.id
            update_conv["appointment_id"] = appt.id
            action_result = {"appointment_id": appt.id}
        elif atype == "create_lead":
            lead = Lead(
                tenant_id=user["tenant_id"],
                name=payload.get("name") or conv.get("caller_name") or "Caller",
                phone=payload.get("phone") or conv.get("caller_phone") or "",
                email=payload.get("email") or None,
                source=payload.get("source") or "call",
                notes=payload.get("notes") or "",
                status="new",
            )
            await db.leads.insert_one(lead.model_dump())
            update_conv["lead_id"] = lead.id
            action_result = {"lead_id": lead.id}
        elif atype in {"take_message", "take_voicemail"}:
            update_conv["status"] = "voicemail" if atype == "take_voicemail" else "completed"
            update_conv["summary"] = payload.get("summary") or payload.get("body") or ""
        elif atype == "escalate_to_human":
            update_conv["status"] = "escalated"
            update_conv["summary"] = payload.get("reason") or "Escalated to human"
        elif atype == "end_call":
            update_conv["status"] = "completed"

    # Store AI message
    ai_msg = ConvMessage(
        conversation_id=conv_id, tenant_id=user["tenant_id"],
        role="ai", content=ai.get("reply", ""),
        action=action if action else None,
    )
    await db.conv_messages.insert_one(ai_msg.model_dump())

    if ai.get("end"):
        update_conv.setdefault("status", "completed")
        update_conv["ended_at"] = _now_iso()

    if update_conv:
        await db.conversations.update_one({"id": conv_id}, {"$set": update_conv})

    await record_usage(user["tenant_id"], "ai_interactions", 1, {"conversation_id": conv_id})

    conv2 = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    return {"reply": ai.get("reply", ""), "action": action, "action_result": action_result, "ended": ai.get("end", False), "conversation": conv2}


@router.post("/{conv_id}/end")
async def end_conversation(conv_id: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    conv = await db.conversations.find_one({"id": conv_id, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not conv:
        raise HTTPException(404, "Conversation not found")
    # Generate quick summary from last few messages
    msgs = await db.conv_messages.find({"conversation_id": conv_id}, {"_id": 0}).sort("created_at", 1).to_list(200)
    summary = conv.get("summary") or " ".join([m["content"] for m in msgs[-6:] if m["role"] == "caller"])[:300]
    await db.conversations.update_one({"id": conv_id}, {"$set": {"status": "completed", "ended_at": _now_iso(), "summary": summary}})
    # Fire-and-forget quality scan
    try:
        await scan_conversation(user["tenant_id"], conv_id)
    except Exception:
        pass
    # Auto lead-score + schedule follow-up cadence for warm/hot leads
    try:
        from ai_insights import score_lead_from_transcript
        from routers.sales_intel import _schedule_followups_for_lead
        transcript = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in msgs)
        tenant_doc = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0, "industry_slug": 1})
        score = await score_lead_from_transcript(transcript, conv.get("caller_name") or "", (tenant_doc or {}).get("industry_slug", ""))
        now = _now_iso()
        await db.conversations.update_one({"id": conv_id}, {"$set": {"lead_score": score, "lead_scored_at": now}})
        lead_id = conv.get("lead_id")
        if lead_id:
            await db.leads.update_one({"id": lead_id, "tenant_id": user["tenant_id"]}, {"$set": {"lead_score": score, "lead_scored_at": now}})
            lead = await db.leads.find_one({"id": lead_id, "tenant_id": user["tenant_id"]}, {"_id": 0})
            if lead and score.get("label") in {"hot", "warm"} and lead.get("phone"):
                await _schedule_followups_for_lead(user["tenant_id"], lead, score["label"])
    except Exception as _e:
        print(f"[end_conversation] insight pipeline failed: {_e}")
    return {"status": "ok"}


# ---------- SMS: a conversation per phone number ----------
@router.get("/sms/threads")
async def list_sms_threads(user: dict = Depends(require_tenant_user)):
    db = get_db()
    rows = await db.conversations.find(
        {"tenant_id": user["tenant_id"], "channel": "sms"}, {"_id": 0}
    ).sort("created_at", -1).to_list(200)
    return rows


@router.post("/sms/send")
async def send_sms(data: dict, user: dict = Depends(require_tenant_user)):
    """Outbound SMS from the business. In demo mode we only log + record."""
    db = get_db()
    to = (data.get("to") or "").strip()
    body = (data.get("body") or "").strip()
    if not to or not body:
        raise HTTPException(400, "to + body required")
    # Find/create SMS thread
    thread = await db.conversations.find_one({"tenant_id": user["tenant_id"], "channel": "sms", "caller_phone": to})
    if not thread:
        link = await _ensure_customer_or_lead(user["tenant_id"], data.get("name") or "", to)
        conv = Conversation(tenant_id=user["tenant_id"], channel="sms", caller_phone=to, caller_name=data.get("name") or "")
        thread_doc = conv.model_dump()
        thread_doc.update(link)
        await db.conversations.insert_one(thread_doc)
        thread = thread_doc
    msg = ConvMessage(conversation_id=thread["id"], tenant_id=user["tenant_id"], role="ai", content=body)
    await db.conv_messages.insert_one(msg.model_dump())
    await record_usage(user["tenant_id"], "sms", 1, {"to": to})
    print(f"[SMS->{to}] {body}")
    return {"thread_id": thread["id"], "message_id": msg.id}


@router.post("/sms/inbound-sim")
async def inbound_sms_sim(data: dict, user: dict = Depends(require_tenant_user)):
    """Simulate an inbound SMS from a customer. AI responds using its playbook."""
    db = get_db()
    frm = (data.get("from") or "").strip()
    body = (data.get("body") or "").strip()
    if not frm or not body:
        raise HTTPException(400, "from + body required")
    thread = await db.conversations.find_one({"tenant_id": user["tenant_id"], "channel": "sms", "caller_phone": frm})
    if not thread:
        link = await _ensure_customer_or_lead(user["tenant_id"], data.get("name") or "", frm)
        conv = Conversation(tenant_id=user["tenant_id"], channel="sms", caller_phone=frm, caller_name=data.get("name") or "")
        thread_doc = conv.model_dump()
        thread_doc.update(link)
        await db.conversations.insert_one(thread_doc)
        thread = thread_doc
    msg_in = ConvMessage(conversation_id=thread["id"], tenant_id=user["tenant_id"], role="caller", content=body)
    await db.conv_messages.insert_one(msg_in.model_dump())
    # Try the appointment confirmation shortcut first (YES / RESCHEDULE / CANCEL)
    try:
        from routers.phase9 import handle_confirmation_reply
        conf = await handle_confirmation_reply(user["tenant_id"], frm, body)
    except Exception:
        conf = None
    if conf:
        reply = conf["reply"]
        action = conf.get("action")
        msg_out = ConvMessage(conversation_id=thread["id"], tenant_id=user["tenant_id"], role="ai",
                              content=reply, action=action)
        await db.conv_messages.insert_one(msg_out.model_dump())
        return {"thread_id": thread["id"], "reply": reply, "action": action}
    # Reuse receptionist pipeline for a quick auto-reply
    tenant, industry, services, knowledge, upsells, policy, persona, objections = await _tenant_bundle(user["tenant_id"])
    history = await db.conv_messages.find({"conversation_id": thread["id"]}, {"_id": 0}).sort("created_at", 1).to_list(50)
    ai = await receptionist_reply(tenant, industry, services, knowledge, history, body,
                                  upsells=upsells, discount_policy=policy,
                                  persona=persona, objections=objections)
    reply = ai.get("reply", "Thanks — we'll be right back with you.")
    out = ConvMessage(conversation_id=thread["id"], tenant_id=user["tenant_id"], role="ai", content=reply, action=ai.get("action") or None)
    await db.conv_messages.insert_one(out.model_dump())
    await record_usage(user["tenant_id"], "sms", 2, {"from": frm})
    await record_usage(user["tenant_id"], "ai_interactions", 1)
    return {"thread_id": thread["id"], "reply": reply}
