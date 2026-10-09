"""Twilio webhook router — real inbound call & SMS → runs through the AI receptionist pipeline."""
import os
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import Response
from db import get_db
from models import _uuid, _now_iso
from models_phase2 import Conversation, ConvMessage
from ai_receptionist import receptionist_reply
from services.twilio import twiml_voice_response, twiml_say_and_gather, send_sms, _tenant_twilio_cfg
from services import twilio_signature
from routers.usage import record_usage, usage_capped


async def _resolve_tenant(to_number: str) -> dict | None:
    """Match the dialed number to a tenant via its phone_numbers or integrations.config."""
    db = get_db()
    t = await db.tenants.find_one({"human_fallback_number": to_number}, {"_id": 0})
    if t:
        return t
    row = await db.integrations.find_one({"key": "twilio", "config.from_number": to_number}, {"_id": 0, "tenant_id": 1})
    if row:
        return await db.tenants.find_one({"id": row["tenant_id"]}, {"_id": 0})
    return None


async def require_twilio_signature(request: Request) -> None:
    """Router-wide guard: every /api/twilio/* route needs a valid X-Twilio-Signature.

    The token is the one of the tenant the webhook is about (same lookup as the
    handler), falling back to TWILIO_AUTH_TOKEN; see services/twilio_signature.py.
    """
    form = await request.form()
    path = request.url.path
    tenant_id = None
    if path.endswith("/voice-turn"):
        conv = await get_db().conversations.find_one(
            {"id": request.query_params.get("conv_id") or ""}, {"_id": 0, "tenant_id": 1})
        tenant_id = (conv or {}).get("tenant_id")
    elif path.endswith("/outbound-callback"):
        tenant_id = request.query_params.get("tenant_id") or None
    else:
        tenant = await _resolve_tenant(form.get("To") or "")
        tenant_id = (tenant or {}).get("id")
    if tenant_id:
        token = (await _tenant_twilio_cfg(tenant_id))["auth_token"]
    else:
        token = os.environ.get("TWILIO_AUTH_TOKEN") or ""
    await twilio_signature.verify(request, token)


router = APIRouter(prefix="/twilio", tags=["twilio-webhook"], dependencies=[Depends(require_twilio_signature)])


def _public_base_url() -> str:
    return (os.environ.get("PUBLIC_BACKEND_URL") or "").rstrip("/")


@router.post("/voice", include_in_schema=False)
async def voice_incoming(request: Request):
    """Twilio hits this when an inbound call connects. Returns TwiML <Gather>."""
    form = await request.form()
    to = form.get("To") or ""
    from_number = form.get("From") or ""
    call_sid = form.get("CallSid") or ""
    tenant = await _resolve_tenant(to)
    if not tenant:
        return Response(twiml_say_and_gather(text="This line is not configured. Please try again later.", gather_url="", end=True),
                        media_type="application/xml")
    db = get_db()
    # Create a conversation to attach messages
    conv = Conversation(tenant_id=tenant["id"], channel="call", caller_phone=from_number, is_simulation=False)
    doc = conv.model_dump()
    doc["twilio_call_sid"] = call_sid
    await db.conversations.insert_one(doc)
    greeting = (tenant.get("ai_employee") or {}).get("greeting") or f"Hi, thanks for calling {tenant.get('name')}. How can I help?"
    await db.conv_messages.insert_one(ConvMessage(conversation_id=doc["id"], tenant_id=tenant["id"], role="ai", content=greeting).model_dump())
    await record_usage(tenant["id"], "calls", 1, {"conversation_id": doc["id"], "twilio_call_sid": call_sid})
    base = _public_base_url()
    gather_url = f"{base}/api/twilio/voice-turn?conv_id={doc['id']}"
    return Response(twiml_voice_response(tenant_name=tenant.get("name", ""), gather_url=gather_url, greeting=greeting),
                    media_type="application/xml")


@router.post("/voice-turn", include_in_schema=False)
async def voice_turn(request: Request, conv_id: str):
    form = await request.form()
    speech = (form.get("SpeechResult") or "").strip()
    db = get_db()
    conv = await db.conversations.find_one({"id": conv_id}, {"_id": 0})
    if not conv:
        raise HTTPException(404, "conversation missing")

    if speech:
        await db.conv_messages.insert_one(ConvMessage(conversation_id=conv_id, tenant_id=conv["tenant_id"],
                                                     role="caller", content=speech).model_dump())

    tenant = await db.tenants.find_one({"id": conv["tenant_id"]}, {"_id": 0})
    industry = await db.industries.find_one({"slug": tenant.get("industry_slug")}, {"_id": 0}) if tenant and tenant.get("industry_slug") else None
    services = await db.services.find({"tenant_id": conv["tenant_id"]}, {"_id": 0}).to_list(200)
    knowledge = await db.knowledge.find({"tenant_id": conv["tenant_id"]}, {"_id": 0}).to_list(200)
    upsells = await db.upsells.find({"tenant_id": conv["tenant_id"]}, {"_id": 0}).to_list(100)
    policy = await db.discount_policies.find_one({"tenant_id": conv["tenant_id"]}, {"_id": 0})
    persona = await db.ai_personas.find_one({"tenant_id": conv["tenant_id"]}, {"_id": 0})
    objections = await db.objections.find({"tenant_id": conv["tenant_id"]}, {"_id": 0}).to_list(100)
    history = await db.conv_messages.find({"conversation_id": conv_id}, {"_id": 0}).sort("created_at", 1).to_list(50)
    capped = await usage_capped(conv["tenant_id"], "ai_interactions")

    ai = await receptionist_reply(tenant or {}, industry, services, knowledge, history, speech or "",
                                  usage_capped=capped, upsells=upsells, discount_policy=policy,
                                  persona=persona, objections=objections)
    reply = ai.get("reply", "Sorry, I didn't catch that.")
    await db.conv_messages.insert_one(ConvMessage(conversation_id=conv_id, tenant_id=conv["tenant_id"],
                                                 role="ai", content=reply, action=ai.get("action")).model_dump())
    await record_usage(conv["tenant_id"], "ai_interactions", 1, {"conversation_id": conv_id})

    base = _public_base_url()
    end = bool(ai.get("end")) or (ai.get("action") or {}).get("type") in {"end_call"}
    if end:
        await db.conversations.update_one({"id": conv_id}, {"$set": {"status": "completed", "ended_at": _now_iso()}})
    gather_url = f"{base}/api/twilio/voice-turn?conv_id={conv_id}"
    return Response(twiml_say_and_gather(text=reply, gather_url=gather_url, end=end), media_type="application/xml")


@router.post("/sms", include_in_schema=False)
async def sms_incoming(request: Request):
    form = await request.form()
    from_num = form.get("From") or ""
    to_num = form.get("To") or ""
    body = (form.get("Body") or "").strip()
    tenant = await _resolve_tenant(to_num)
    if not tenant:
        return Response('<?xml version="1.0" encoding="UTF-8"?><Response/>', media_type="application/xml")
    # Try confirmation shortcut first (YES / RESCHEDULE / CANCEL)
    try:
        from routers.phase9 import handle_confirmation_reply
        conf = await handle_confirmation_reply(tenant["id"], from_num, body)
    except Exception as e:
        print(f"[sms confirm reply] {e}"); conf = None
    if conf:
        reply = conf["reply"][:1500]
        twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{reply}</Message></Response>'
        return Response(twiml, media_type="application/xml")
    db = get_db()
    thread = await db.conversations.find_one({"tenant_id": tenant["id"], "channel": "sms", "caller_phone": from_num})
    if not thread:
        c = Conversation(tenant_id=tenant["id"], channel="sms", caller_phone=from_num, is_simulation=False)
        thread = c.model_dump()
        await db.conversations.insert_one(thread)
    await db.conv_messages.insert_one(ConvMessage(conversation_id=thread["id"], tenant_id=tenant["id"],
                                                 role="caller", content=body).model_dump())
    industry = await db.industries.find_one({"slug": tenant.get("industry_slug")}, {"_id": 0}) if tenant.get("industry_slug") else None
    services = await db.services.find({"tenant_id": tenant["id"]}, {"_id": 0}).to_list(200)
    knowledge = await db.knowledge.find({"tenant_id": tenant["id"]}, {"_id": 0}).to_list(200)
    upsells = await db.upsells.find({"tenant_id": tenant["id"]}, {"_id": 0}).to_list(100)
    policy = await db.discount_policies.find_one({"tenant_id": tenant["id"]}, {"_id": 0})
    persona = await db.ai_personas.find_one({"tenant_id": tenant["id"]}, {"_id": 0})
    objections = await db.objections.find({"tenant_id": tenant["id"]}, {"_id": 0}).to_list(100)
    history = await db.conv_messages.find({"conversation_id": thread["id"]}, {"_id": 0}).sort("created_at", 1).to_list(50)
    ai = await receptionist_reply(tenant, industry, services, knowledge, history, body,
                                  upsells=upsells, discount_policy=policy,
                                  persona=persona, objections=objections)
    reply = (ai.get("reply") or "").replace("<", " ")[:1500]
    await db.conv_messages.insert_one(ConvMessage(conversation_id=thread["id"], tenant_id=tenant["id"],
                                                 role="ai", content=reply, action=ai.get("action")).model_dump())
    await record_usage(tenant["id"], "sms", 2, {"from": from_num})
    await record_usage(tenant["id"], "ai_interactions", 1)
    twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{reply}</Message></Response>'
    return Response(twiml, media_type="application/xml")


@router.post("/outbound-callback", include_in_schema=False)
async def outbound_callback(request: Request):
    """TwiML endpoint invoked by Twilio when our scheduled callback actually dials.
    Starts a fresh AI conversation and routes further turns through /voice-turn."""
    form = await request.form()
    tenant_id = request.query_params.get("tenant_id") or form.get("tenant_id") or ""
    name = request.query_params.get("name") or ""
    note = request.query_params.get("note") or ""
    call_sid = form.get("CallSid") or ""
    db = get_db()
    tenant = await db.tenants.find_one({"id": tenant_id}, {"_id": 0}) or {}
    conv = Conversation(tenant_id=tenant_id, channel="call", caller_phone=form.get("To", ""),
                        caller_name=name or "", is_simulation=False)
    doc = conv.model_dump()
    doc["twilio_call_sid"] = call_sid
    doc["direction"] = "outbound"
    await db.conversations.insert_one(doc)
    greeting = (f"Hi {name}, this is the AI office for {tenant.get('name','us')}. "
               f"Thanks for your interest. {note}. How can I help?") if name else \
               f"Hi, this is {tenant.get('name','us')}. Just following up on your inquiry. How can I help?"
    await db.conv_messages.insert_one(ConvMessage(conversation_id=doc["id"], tenant_id=tenant_id,
                                                 role="ai", content=greeting).model_dump())
    base = _public_base_url()
    gather_url = f"{base}/api/twilio/voice-turn?conv_id={doc['id']}"
    return Response(twiml_voice_response(tenant_name=tenant.get("name",""), gather_url=gather_url, greeting=greeting),
                   media_type="application/xml")


@router.post("/missed-call", include_in_schema=False)
async def missed_call(request: Request):
    """Status-callback webhook — if a call ended as no-answer/failed, send the automation textback."""
    form = await request.form()
    status = form.get("CallStatus") or ""
    from_num = form.get("From") or ""
    to_num = form.get("To") or ""
    if status not in {"no-answer", "busy", "failed", "canceled"}:
        return {"ignored": True}
    tenant = await _resolve_tenant(to_num)
    if not tenant or not from_num:
        return {"ignored": True}
    db = get_db()
    autom = await db.automation_settings.find_one({"tenant_id": tenant["id"]}, {"_id": 0}) or {}
    if not autom.get("missed_call_textback", True):
        return {"ignored": True}
    msg = autom.get("missed_call_textback_message") or "Sorry we missed you! Reply here and we'll get right back to you."
    res = await send_sms(tenant_id=tenant["id"], to=from_num, body=msg)
    await record_usage(tenant["id"], "sms", 1, {"missed_call": True, "to": from_num})
    return {"status": status, "textback": res}
    """Status-callback webhook — if a call ended as no-answer/failed, send the automation textback."""
    form = await request.form()
    status = form.get("CallStatus") or ""
    from_num = form.get("From") or ""
    to_num = form.get("To") or ""
    if status not in {"no-answer", "busy", "failed", "canceled"}:
        return {"ignored": True}
    tenant = await _resolve_tenant(to_num)
    if not tenant or not from_num:
        return {"ignored": True}
    db = get_db()
    autom = await db.automation_settings.find_one({"tenant_id": tenant["id"]}, {"_id": 0}) or {}
    if not autom.get("missed_call_textback", True):
        return {"ignored": True}
    msg = autom.get("missed_call_textback_message") or "Sorry we missed you! Reply here and we'll get right back to you."
    res = await send_sms(tenant_id=tenant["id"], to=from_num, body=msg)
    await record_usage(tenant["id"], "sms", 1, {"missed_call": True, "to": from_num})
    return {"status": status, "textback": res}
