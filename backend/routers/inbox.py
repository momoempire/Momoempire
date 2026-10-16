"""Omnichannel inbox — LAYER 2 of the customer-service excellence initiative:
Momo acting as customer service for the SHOPS' customers.

One unified inbox for the shop's customer conversations across channels —
SMS, voice-call transcripts (from the AI receptionist), email (stubbed),
web chat (stubbed). Threads are grouped by customer + channel; each thread
carries assignment (user/queue), status (open/pending/resolved), and
AI-drafted reply suggestions (deterministic templates — no LLM required).

Reuses the AI receptionist's stored outputs: voice transcripts are ingested
from the receptionist's `conversations` / `conv_messages` collections. The
receptionist is NOT rebuilt here.

Collections (all tenant-scoped):
- `inbox_threads`: one doc per (tenant_id, channel, customer contact).
- `inbox_messages`: one doc per message, keyed by thread_id.

Live wiring (stubbed): Twilio for SMS send/receive, Twilio signature
verification on /ingest/sms, SMTP provider for email send — all marked
TODO(LIVE). Voice comes from the receptionist; email/web-chat ingestion
are stubs until those channels go live.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

CHANNELS = ("sms", "voice", "email", "chat")
STATUSES = ("open", "pending", "resolved")

# Stubbed inbound channels — TODO(LIVE): wire real providers and flip to True.
CHANNEL_STATUS: List[Dict] = [
    {"channel": "sms", "connected": True, "note": "Twilio inbound — signature verification still TODO(LIVE)"},
    {"channel": "voice", "connected": True, "note": "Ingested from the AI receptionist's stored transcripts"},
    {"channel": "email", "connected": False, "note": "TODO(LIVE): connect SMTP/imap provider per tenant"},
    {"channel": "chat", "connected": False, "note": "TODO(LIVE): connect web-chat widget"},
]


# ---------- models ----------

class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class AssignIn(BaseModel):
    assignee: Optional[str] = Field(default=None, max_length=120,
                                    description="User id or queue name; null to unassign")


class SmsIngestIn(BaseModel):
    """Twilio-style inbound SMS webhook payload (subset)."""
    From: str = Field(min_length=1)
    To: str = Field(default="")
    Body: str = Field(default="")
    customer_name: str = ""


class CallTranscriptIngestIn(BaseModel):
    """Pull a finished AI-receptionist call into the inbox as a voice thread.

    Reads the receptionist's `conversations` / `conv_messages` records —
    no receptionist logic is duplicated here.
    """
    conversation_id: str = Field(min_length=1)


# ---------- helpers ----------

def _thread_key(tenant_id: str, channel: str, contact: str) -> Dict:
    return {"tenant_id": tenant_id, "channel": channel, "customer_contact": contact}


async def _get_or_create_thread(db, tenant_id: str, channel: str, *,
                               customer_name: str = "", customer_contact: str = "",
                               customer_phone: str = "", customer_email: str = "") -> Dict:
    """One thread per (tenant, channel, customer contact). Creates open threads."""
    doc = await db.inbox_threads.find_one(_thread_key(tenant_id, channel, customer_contact))
    if doc:
        return doc
    now = _now_iso()
    doc = {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "channel": channel,
        "customer_name": customer_name,
        "customer_contact": customer_contact,
        "customer_phone": customer_phone,
        "customer_email": customer_email,
        "status": "open",
        "assignee": None,
        "created_at": now,
        "updated_at": now,
        "last_message_at": now,
        "last_message_preview": "",
    }
    await db.inbox_threads.insert_one(doc)
    return doc


async def _append_message(db, thread: Dict, *, direction: str, sender: str, body: str) -> Dict:
    now = _now_iso()
    msg = {
        "id": _uuid(),
        "tenant_id": thread["tenant_id"],
        "thread_id": thread["id"],
        "channel": thread["channel"],
        "direction": direction,          # "in" (customer) | "out" (shop/AI)
        "sender": sender,                # "customer" | "shop" | "ai"
        "body": body,
        "created_at": now,
    }
    await db.inbox_messages.insert_one(msg)
    # Bump thread; inbound activity re-opens resolved threads.
    update = {
        "last_message_at": now,
        "last_message_preview": body[:120],
        "updated_at": now,
    }
    if direction == "in" and thread.get("status") == "resolved":
        update["status"] = "open"
    if direction == "in" and thread.get("status") == "pending":
        update["status"] = "open"
    await db.inbox_threads.update_one({"id": thread["id"]}, {"$set": update})
    msg.pop("_id", None)
    return msg


def _draft_suggestions(thread: Dict, last_customer_msg: Optional[Dict], business_name: str) -> List[Dict]:
    """Deterministic template-based reply suggestions. No LLM call.

    Picks up to three chips based on intent keywords in the latest customer
    message; always includes a friendly acknowledgement. Labeled drafts for
    staff review — nothing sends without a human tap.
    """
    name = (thread.get("customer_name") or "").split()[0] or "there"
    text = (last_customer_msg or {}).get("body", "").lower()
    out: List[Dict] = []

    def chip(kind: str, label: str, draft: str):
        out.append({"kind": kind, "label": label, "draft": draft,
                    "generated_by": "template-suggestion-v1"})

    chip("acknowledge", "Acknowledge",
         f"Hi {name}, thanks for reaching out! We got your message and will take care of it. — {business_name or 'the team'}")

    if any(w in text for w in ("price", "cost", "quote", "estimate", "how much")):
        chip("pricing", "Pricing info",
             f"Hi {name}, happy to help with pricing! Could you share a few details about the job so we can give you an accurate quote?")
    if any(w in text for w in ("book", "schedule", "appointment", "available", "when can", "slot")):
        chip("scheduling", "Book a visit",
             f"Hi {name}, we'd be glad to set that up. What day and time works best for you? We have morning and afternoon slots.")
    if any(w in text for w in ("broken", "not working", "wrong", "bad", "terrible", "upset", "angry", "complaint", "refund")):
        chip("apology", "Apologize + fix",
             f"Hi {name}, I'm really sorry about this — that's not the experience we want for you. A manager will call you shortly to make it right.")
    if any(w in text for w in ("thank", "great", "awesome", "excellent", "love")):
        chip("thanks", "Thank them",
             f"Hi {name}, thank you so much — it means a lot to the team! Let us know if you ever need anything else.")

    # Dedupe the acknowledgement if it is the only chip.
    return out[:4]


# ---------- thread listing ----------

@router.get("/threads")
async def list_threads(
    channel: Optional[str] = None,
    status: Optional[str] = None,
    assignee: Optional[str] = None,
    user: dict = Depends(require_tenant_user),
):
    """Thread list, newest activity first. Filter with ?channel=sms&status=open&assignee=<id>."""
    db = get_db()
    tenant_id = user["tenant_id"]
    docs = await db.inbox_threads.find(
        {"tenant_id": tenant_id}, {"_id": 0}).sort("last_message_at", -1).to_list(1000)
    out = []
    for d in docs:
        if channel and d.get("channel") != channel:
            continue
        if status and d.get("status") != status:
            continue
        if assignee == "unassigned":
            if d.get("assignee"):
                continue
        elif assignee and d.get("assignee") != assignee:
            continue
        out.append(d)
    return {"threads": out, "count": len(out), "channels": CHANNEL_STATUS}


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: str, user: dict = Depends(require_tenant_user)):
    """One thread with its full message history (oldest first)."""
    db = get_db()
    thread = await db.inbox_threads.find_one(
        {"tenant_id": user["tenant_id"], "id": thread_id}, {"_id": 0})
    if not thread:
        raise HTTPException(404, "Thread not found")
    msgs = await db.inbox_messages.find(
        {"tenant_id": user["tenant_id"], "thread_id": thread_id},
        {"_id": 0}).sort("created_at", 1).to_list(2000)
    return {"thread": thread, "messages": msgs, "count": len(msgs)}


# ---------- send / assign / lifecycle ----------

@router.post("/threads/{thread_id}/messages")
async def send_message(thread_id: str, data: MessageIn, user: dict = Depends(require_tenant_user)):
    """Shop sends a reply on a thread.

    TODO(LIVE): dispatch through the real channel provider — Twilio for SMS,
    the SMTP provider for email, the chat gateway for web chat.
    """
    db = get_db()
    thread = await db.inbox_threads.find_one({"tenant_id": user["tenant_id"], "id": thread_id})
    if not thread:
        raise HTTPException(404, "Thread not found")

    # TODO(LIVE): real dispatch by channel:
    #   sms   -> services.twilio.send_sms(to=thread["customer_phone"], body=data.body)
    #   email -> services.email.send_email(to=thread["customer_email"], body=data.body)
    #   chat  -> services.chat.post_message(thread_id=..., body=data.body)
    print(f"[INBOX send {thread.get('channel')} -> "
          f"{thread.get('customer_contact')}] {data.body[:80]} (stubbed dispatch)")

    msg = await _append_message(db, thread, direction="out", sender="shop", body=data.body)
    # A shop reply marks the thread pending (waiting on the customer).
    await db.inbox_threads.update_one(
        {"id": thread["id"]}, {"$set": {"status": "pending", "updated_at": _now_iso()}})
    return {"ok": True, **msg}


@router.post("/threads/{thread_id}/assign")
async def assign_thread(thread_id: str, data: AssignIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Assign a thread to a user id or queue name (null to unassign)."""
    db = get_db()
    thread = await db.inbox_threads.find_one({"tenant_id": user["tenant_id"], "id": thread_id})
    if not thread:
        raise HTTPException(404, "Thread not found")
    now = _now_iso()
    await db.inbox_threads.update_one(
        {"id": thread["id"]},
        {"$set": {"assignee": data.assignee, "updated_at": now,
                  "assigned_by": user.get("id"), "assigned_at": now if data.assignee else None}},
    )
    return {"ok": True, "thread_id": thread_id, "assignee": data.assignee}


@router.post("/threads/{thread_id}/resolve")
async def resolve_thread(thread_id: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    thread = await db.inbox_threads.find_one({"tenant_id": user["tenant_id"], "id": thread_id})
    if not thread:
        raise HTTPException(404, "Thread not found")
    await db.inbox_threads.update_one(
        {"id": thread["id"]},
        {"$set": {"status": "resolved", "resolved_at": _now_iso(),
                  "resolved_by": user.get("id"), "updated_at": _now_iso()}},
    )
    return {"ok": True, "thread_id": thread_id, "status": "resolved"}


@router.post("/threads/{thread_id}/reopen")
async def reopen_thread(thread_id: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    thread = await db.inbox_threads.find_one({"tenant_id": user["tenant_id"], "id": thread_id})
    if not thread:
        raise HTTPException(404, "Thread not found")
    await db.inbox_threads.update_one(
        {"id": thread["id"]}, {"$set": {"status": "open", "updated_at": _now_iso()}})
    return {"ok": True, "thread_id": thread_id, "status": "open"}


# ---------- AI-drafted suggestions ----------

@router.get("/threads/{thread_id}/suggestions")
async def reply_suggestions(thread_id: str, user: dict = Depends(require_tenant_user)):
    """AI-drafted reply suggestions (deterministic templates, no LLM).

    Each suggestion is labeled a draft for staff review — tapping one fills
    the composer; nothing is ever sent automatically.
    """
    db = get_db()
    thread = await db.inbox_threads.find_one(
        {"tenant_id": user["tenant_id"], "id": thread_id}, {"_id": 0})
    if not thread:
        raise HTTPException(404, "Thread not found")
    msgs = await db.inbox_messages.find(
        {"tenant_id": user["tenant_id"], "thread_id": thread_id},
        {"_id": 0}).sort("created_at", -1).to_list(50)
    last_customer = next((m for m in msgs if m.get("direction") == "in"), None)

    tenant = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0, "name": 1}) or {}
    business_name = tenant.get("name") or ""

    suggestions = _draft_suggestions(thread, last_customer, business_name)
    return {"thread_id": thread_id, "suggestions": suggestions, "draft_labeled": True,
            "note": "Template-based drafts (no LLM). Staff must review before sending."}


# ---------- ingestion ----------

@router.post("/ingest/sms")
async def ingest_sms(data: SmsIngestIn, user: dict = Depends(require_tenant_user)):
    """Inbound SMS webhook: creates or updates the customer's SMS thread.

    TODO(LIVE): verify the Twilio X-Twilio-Signature header before trusting
    the payload, and route by the tenant's Twilio number (To field).
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    phone = data.From.strip()
    thread = await _get_or_create_thread(
        db, tenant_id, "sms",
        customer_name=data.customer_name, customer_contact=phone,
        customer_phone=phone,
    )
    msg = None
    if data.Body:
        msg = await _append_message(db, thread, direction="in", sender="customer", body=data.Body)
    thread = await db.inbox_threads.find_one({"tenant_id": tenant_id, "id": thread["id"]}, {"_id": 0})
    return {"ok": True, "thread": thread, "message": msg}


@router.post("/ingest/call-transcript")
async def ingest_call_transcript(data: CallTranscriptIngestIn, user: dict = Depends(require_tenant_user)):
    """Pull a finished AI-receptionist call into the inbox as a voice thread.

    Reuses the receptionist's stored records (`conversations` /
    `conv_messages`) — the receptionist is not re-implemented here. The
    transcript is summarized into a single customer message so staff see the
    call outcome at a glance in the inbox.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    conv = await db.conversations.find_one(
        {"tenant_id": tenant_id, "id": data.conversation_id}, {"_id": 0})
    if not conv:
        raise HTTPException(404, "Call conversation not found")

    msgs = await db.conv_messages.find(
        {"conversation_id": data.conversation_id}, {"_id": 0}).sort("created_at", 1).to_list(500)
    caller = conv.get("caller_name") or "caller"
    phone = conv.get("caller_phone") or ""
    contact = conv.get("id")  # one voice thread per call

    thread = await _get_or_create_thread(
        db, tenant_id, "voice",
        customer_name=caller, customer_contact=contact, customer_phone=phone,
    )

    lines = [f"{'AI' if m.get('role') == 'assistant' else 'Caller'}: {m.get('content', '')}"
             for m in msgs if m.get("content")]
    body = "\n".join(lines)
    summary = conv.get("summary") or (body[:600] + ("…" if len(body) > 600 else ""))
    # Avoid duplicate ingestion: skip if the latest voice message already covers it.
    existing = await db.inbox_messages.find(
        {"tenant_id": tenant_id, "thread_id": thread["id"]},
        {"_id": 0}).sort("created_at", -1).to_list(1)
    ingested = None
    if not existing:
        ingested = await _append_message(
            db, thread, direction="in", sender="customer",
            body=f"📞 Call with {caller} ({phone or 'unknown number'})\n"
                 f"Summary: {summary or 'transcript below'}\n---\n{body[:3000]}",
        )
    thread = await db.inbox_threads.find_one({"tenant_id": tenant_id, "id": thread["id"]}, {"_id": 0})
    return {"ok": True, "thread": thread, "message": ingested,
            "ingested": ingested is not None,
            "note": "Voice thread sourced from the AI receptionist's stored transcript."}
