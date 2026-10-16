"""Proactive outreach + missed-call rescue + multilingual support.

LAYER 2 of the customer-service excellence initiative: Momo acting as
customer service for the shops' customers.

(a) Proactive outreach campaigns — the shop defines trigger-based outreach
    (e.g. no booking in 90 days -> win-back SMS; job completed 7 days ago ->
    review nudge; maintenance agreement ending -> reminder). A campaign run
    evaluates its trigger against the tenant's `customers`, `appointments`
    (jobs) and `maintenance_agreements` collections and queues messages.
    Actual dispatch is stubbed: TODO(LIVE) — a dispatcher worker picks up
    `outreach_queue` rows with status "pending" and sends via
    services.twilio.send_sms using the tenant's Twilio credentials.

(b) Missed-call rescue — missed calls are stored as `conversations` docs with
    channel="call" and status="missed" (see twilio_webhook /missed-call).
    POST /missed-calls/{id}/rescue auto-queues a "Sorry we missed you!" SMS
    (in the caller's language when known) and dedupes on conversation_id.
    Escalation: TODO(LIVE) — the inbound SMS webhook should look up
    `missed_call_rescues` by caller_phone while status="queued" and flip
    `escalated=True` + alert the shop the moment the customer replies.
    A cron/hook calling the rescue endpoint for fresh missed calls gets the
    SMS out within minutes (endpoint is idempotent per conversation).

(c) Multilingual support — message templates live in `message_templates`
    keyed by (tenant_id, key, lang), seeded with EN+ES defaults, extendable
    to more languages. Per-customer preference comes from
    `customers.language`; missing/unsupported falls back to EN.
    _detect_language() is a keyword-based stub — TODO(LIVE): replace with
    real language detection (e.g. fasttext/LLM) once live traffic flows.

Collections (all tenant-scoped):
- `outreach_campaigns`: trigger definition + run stats.
- `outreach_queue`: queued outbound messages, status pending/canceled/sent.
- `missed_call_rescues`: one doc per rescued conversation (dedupe + escalation).
- `message_templates`: multilingual templates keyed by key+lang.

Guardrails:
- One queue entry per (campaign, customer, run-day) — repeated runs never
  double-text a customer.
- Only "pending" queue entries can be canceled.
"""
from __future__ import annotations

import string
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin

router = APIRouter(prefix="/api/outreach", tags=["outreach"])

SUPPORTED_LANGS = ("en", "es")

TRIGGER_TYPES = ("no_booking_days", "job_completed_days", "maintenance_due")
TRIGGER_LABELS = {
    "no_booking_days": "No booking in N days (win-back)",
    "job_completed_days": "Job completed N days ago (review nudge)",
    "maintenance_due": "Maintenance due within N days (reminder)",
}

DEFAULT_TEMPLATES: Dict[str, Dict[str, str]] = {
    "win_back": {
        "en": (
            "Hi {name}, it's {business_name}! We haven't seen you in a while — "
            "is everything working well at home? Reply to book your next visit."
        ),
        "es": (
            "Hola {name}, ¡somos {business_name}! Hace tiempo que no te vemos — "
            "¿todo funciona bien en casa? Responde para programar tu próxima visita."
        ),
    },
    "review_nudge": {
        "en": (
            "Hi {name}, thanks for choosing {business_name}! If you had a great "
            "experience, a quick review would mean a lot: {review_link}"
        ),
        "es": (
            "Hola {name}, ¡gracias por elegir {business_name}! Si tuviste una gran "
            "experiencia, una reseña rápida significaría mucho: {review_link}"
        ),
    },
    "maintenance_reminder": {
        "en": (
            "Hi {name}, your {business_name} maintenance is due soon. Reply YES to "
            "schedule your tune-up and keep everything running smoothly."
        ),
        "es": (
            "Hola {name}, tu mantenimiento de {business_name} vence pronto. "
            "Responde SÍ para programar tu revisión y mantener todo funcionando bien."
        ),
    },
    "missed_call_rescue": {
        "en": (
            "Sorry we missed you! This is {business_name} — reply here and we'll "
            "get right back to you."
        ),
        "es": (
            "¡Perdón por no contestar! Somos {business_name} — responde aquí y te "
            "atenderemos enseguida."
        ),
    },
}


# ---------- pydantic models ----------

class CampaignIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    trigger_type: str = Field(pattern="^(no_booking_days|job_completed_days|maintenance_due)$")
    days: int = Field(ge=1, le=730)
    template_key: str = Field(min_length=1, max_length=60)
    channel: str = Field(default="sms", pattern="^sms$")
    enabled: bool = True


class CampaignPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    trigger_type: Optional[str] = Field(default=None, pattern="^(no_booking_days|job_completed_days|maintenance_due)$")
    days: Optional[int] = Field(default=None, ge=1, le=730)
    template_key: Optional[str] = Field(default=None, min_length=1, max_length=60)
    enabled: Optional[bool] = None


class TemplateUpsertIn(BaseModel):
    key: str = Field(min_length=1, max_length=60)
    lang: str = Field(pattern="^(en|es)$")
    text: str = Field(min_length=1, max_length=1600)


# ---------- helpers ----------

def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _detect_language(text: str) -> str:
    """Keyword-based language detection stub.

    TODO(LIVE): replace with real detection (fasttext/LLM) once live
    traffic flows. Current heuristic is conservative: Spanish markers or
    diacritics -> "es", everything else -> "en".
    """
    raw = text or ""
    lowered = raw.lower()
    es_markers = (
        "gracias", "hola", "por favor", "buenos", "buenas", "usted",
        "servicio", "cita", "llamar", "mañana", "adiós", "sí", "está",
        "cuánto", "dónde", "necesito", "ayuda",
    )
    if any(ch in raw for ch in "áéíóúñ¿¡") or any(w in lowered for w in es_markers):
        return "es"
    return "en"


def _customer_lang(customer: Optional[Dict]) -> str:
    lang = (customer or {}).get("language") or "en"
    return lang if lang in SUPPORTED_LANGS else "en"


def _render_template(text: str, ctx: Dict[str, str]) -> str:
    """Render {placeholders}; unknown placeholders become empty strings."""
    fields = [fname for _, fname, _, _ in string.Formatter().parse(text or "") if fname]
    safe = {f: str(ctx.get(f, "")) for f in fields}
    try:
        return (text or "").format(**safe)
    except Exception:
        return text or ""


async def _ensure_templates(db, tenant_id: str) -> None:
    """Seed EN+ES default templates for this tenant (idempotent)."""
    existing = await db.message_templates.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(500)
    have = {(d.get("key"), d.get("lang")) for d in existing}
    for key, langs in DEFAULT_TEMPLATES.items():
        for lang, text in langs.items():
            if (key, lang) not in have:
                await db.message_templates.insert_one({
                    "id": _uuid(),
                    "tenant_id": tenant_id,
                    "key": key,
                    "lang": lang,
                    "text": text,
                    "seeded": True,
                    "created_at": _now_iso(),
                    "updated_at": _now_iso(),
                })


async def _get_template(db, tenant_id: str, key: str, lang: str) -> Optional[Dict]:
    """Fetch a template with EN fallback for missing languages."""
    doc = await db.message_templates.find_one(
        {"tenant_id": tenant_id, "key": key, "lang": lang})
    if not doc and lang != "en":
        doc = await db.message_templates.find_one(
            {"tenant_id": tenant_id, "key": key, "lang": "en"})
    return doc


async def _business_name(db, tenant_id: str) -> str:
    tenant = await db.tenants.find_one({"id": tenant_id}, {"_id": 0, "name": 1}) or {}
    return tenant.get("name") or "our business"


async def _queue_message(
    db,
    *,
    tenant_id: str,
    kind: str,
    to_phone: str,
    customer_id: str,
    customer_name: str,
    lang: str,
    template_key: str,
    body: str,
    channel: str = "sms",
    campaign_id: Optional[str] = None,
    dedupe_key: Optional[str] = None,
    meta: Optional[Dict] = None,
) -> Dict:
    """Queue an outbound message. Real dispatch is stubbed — TODO(LIVE): a
    dispatcher worker picks up pending rows and sends via
    services.twilio.send_sms with the tenant's Twilio credentials."""
    doc = {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "kind": kind,  # "campaign" | "missed_call_rescue"
        "campaign_id": campaign_id,
        "template_key": template_key,
        "channel": channel,
        "to_phone": to_phone,
        "customer_id": customer_id,
        "customer_name": customer_name,
        "lang": lang,
        "body": body,
        "status": "pending",
        "dedupe_key": dedupe_key,
        "meta": meta or {},
        "created_at": _now_iso(),
    }
    await db.outreach_queue.insert_one(doc)
    print(f"[OUTREACH queue {channel} -> {to_phone}] kind={kind} lang={lang} "
          f"template={template_key} (stubbed send)")
    doc.pop("_id", None)
    return doc


async def _evaluate_trigger(db, tenant_id: str, campaign: Dict) -> List[Dict]:
    """Evaluate a campaign's trigger against customer/job data.

    Returns a list of match dicts: customer_id, phone, name, lang, context.
    All date-window filtering happens in Python so behavior is identical on
    the fake in-memory mongo used by tests.
    """
    now = datetime.now(timezone.utc)
    days = campaign.get("days", 0)
    tt = campaign.get("trigger_type")
    matches: List[Dict] = []

    if tt == "no_booking_days":
        cutoff = now - timedelta(days=days)
        customers = await db.customers.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(5000)
        appts = await db.appointments.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(20000)
        last_activity: Dict[str, datetime] = {}
        for a in appts:
            key = a.get("customer_id") or a.get("customer_phone") or ""
            if not key:
                continue
            dt = _parse_iso(a.get("start_at") or a.get("created_at") or "")
            if dt and (key not in last_activity or dt > last_activity[key]):
                last_activity[key] = dt
        for c in customers:
            phone = c.get("phone") or ""
            if not phone:
                continue
            key = c.get("id") or phone
            last = (last_activity.get(key) or last_activity.get(phone)
                    or _parse_iso(c.get("created_at") or ""))
            if last is None or last < cutoff:
                matches.append({
                    "customer_id": c.get("id") or "",
                    "phone": phone,
                    "name": c.get("name") or "",
                    "lang": _customer_lang(c),
                    "context": {"days_since_activity": (now - last).days if last else None},
                })

    elif tt == "job_completed_days":
        window_start = now - timedelta(days=days + 1)
        window_end = now - timedelta(days=days)
        appts = await db.appointments.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(20000)
        for a in appts:
            if a.get("status") != "completed":
                continue
            completed = _parse_iso(a.get("completed_at") or "")
            if not completed or not (window_start <= completed < window_end):
                continue
            phone = a.get("customer_phone") or ""
            if not phone:
                continue
            customer = None
            if a.get("customer_id"):
                customer = await db.customers.find_one(
                    {"tenant_id": tenant_id, "id": a.get("customer_id")})
            if not customer:
                customer = await db.customers.find_one(
                    {"tenant_id": tenant_id, "phone": phone})
            matches.append({
                "customer_id": a.get("customer_id") or (customer or {}).get("id") or "",
                "phone": phone,
                "name": a.get("customer_name") or (customer or {}).get("name") or "",
                "lang": _customer_lang(customer),
                "context": {"job_id": a.get("id") or ""},
            })

    elif tt == "maintenance_due":
        window_end = now + timedelta(days=days)
        agreements = await db.maintenance_agreements.find(
            {"tenant_id": tenant_id}, {"_id": 0}).to_list(5000)
        for a in agreements:
            end = _parse_iso(a.get("end_date") or "")
            if not end or not (now <= end <= window_end):
                continue
            phone = a.get("customer_phone") or ""
            if not phone:
                continue
            customer = None
            if a.get("customer_id"):
                customer = await db.customers.find_one(
                    {"tenant_id": tenant_id, "id": a.get("customer_id")})
            matches.append({
                "customer_id": a.get("customer_id") or (customer or {}).get("id") or "",
                "phone": phone,
                "name": a.get("customer_name") or (customer or {}).get("name") or "",
                "lang": _customer_lang(customer),
                "context": {"due_date": a.get("end_date") or ""},
            })

    return matches


# ---------- campaigns ----------

@router.get("/campaigns")
async def list_campaigns(user: dict = Depends(require_tenant_user)):
    """List this tenant's outreach campaigns with last-run stats."""
    db = get_db()
    docs = await db.outreach_campaigns.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}).to_list(500)
    docs.sort(key=lambda d: d.get("created_at", ""))
    for d in docs:
        d["trigger_label"] = TRIGGER_LABELS.get(d.get("trigger_type"), d.get("trigger_type"))
    return {"campaigns": docs, "count": len(docs)}


@router.post("/campaigns")
async def create_campaign(data: CampaignIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Owner: define a trigger-based outreach campaign (template is picked up
    in the recipient's language at run time, EN fallback)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await _ensure_templates(db, tenant_id)
    doc = {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "name": data.name,
        "trigger_type": data.trigger_type,
        "days": data.days,
        "template_key": data.template_key,
        "channel": data.channel,
        "enabled": data.enabled,
        "last_run_at": None,
        "last_run_queued": 0,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    await db.outreach_campaigns.insert_one(doc)
    doc.pop("_id", None)
    return {"ok": True, **doc}


@router.patch("/campaigns/{campaign_id}")
async def update_campaign(campaign_id: str, data: CampaignPatch,
                          user: dict = Depends(require_tenant_owner_or_admin)):
    """Owner: edit a campaign's trigger or template."""
    db = get_db()
    tenant_id = user["tenant_id"]
    camp = await db.outreach_campaigns.find_one({"tenant_id": tenant_id, "id": campaign_id})
    if not camp:
        raise HTTPException(404, "Campaign not found")
    patch = {k: v for k, v in data.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(400, "Nothing to update")
    patch["updated_at"] = _now_iso()
    await db.outreach_campaigns.update_one(
        {"tenant_id": tenant_id, "id": campaign_id}, {"$set": patch})
    updated = await db.outreach_campaigns.find_one(
        {"tenant_id": tenant_id, "id": campaign_id}, {"_id": 0})
    return {"ok": True, **updated}


@router.post("/campaigns/{campaign_id}/toggle")
async def toggle_campaign(campaign_id: str, user: dict = Depends(require_tenant_owner_or_admin)):
    """Owner: flip a campaign's enabled flag."""
    db = get_db()
    tenant_id = user["tenant_id"]
    camp = await db.outreach_campaigns.find_one({"tenant_id": tenant_id, "id": campaign_id})
    if not camp:
        raise HTTPException(404, "Campaign not found")
    new_state = not camp.get("enabled", True)
    await db.outreach_campaigns.update_one(
        {"tenant_id": tenant_id, "id": campaign_id},
        {"$set": {"enabled": new_state, "updated_at": _now_iso()}})
    return {"ok": True, "id": campaign_id, "enabled": new_state}


@router.post("/campaigns/{campaign_id}/run")
async def run_campaign(campaign_id: str, user: dict = Depends(require_tenant_owner_or_admin)):
    """Owner: evaluate this campaign's trigger and queue messages.

    Dedupes on (tenant, campaign, customer, run-day) — repeated runs never
    double-text. Recipients get the template in their language (EN fallback).
    Sends are stubbed (TODO(LIVE)): queued rows wait in `outreach_queue`.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    camp = await db.outreach_campaigns.find_one({"tenant_id": tenant_id, "id": campaign_id})
    if not camp:
        raise HTTPException(404, "Campaign not found")
    if not camp.get("enabled", True):
        raise HTTPException(409, "Campaign is disabled; enable it before running")

    await _ensure_templates(db, tenant_id)
    business_name = await _business_name(db, tenant_id)
    matches = await _evaluate_trigger(db, tenant_id, camp)

    today = datetime.now(timezone.utc).date().isoformat()
    queued = skipped = 0
    for m in matches:
        dedupe_key = f"{tenant_id}:{campaign_id}:{m['customer_id'] or m['phone']}:{today}"
        if await db.outreach_queue.find_one({"dedupe_key": dedupe_key}):
            skipped += 1
            continue
        tpl = await _get_template(db, tenant_id, camp.get("template_key", ""), m["lang"])
        if not tpl:
            skipped += 1  # no template for this key — skip rather than send blank
            continue
        body = _render_template(tpl["text"], {
            "name": m["name"].split()[0] if m["name"] else "there",
            "business_name": business_name,
            "days": str(camp.get("days", "")),
            "review_link": m["context"].get("review_link", ""),
        })
        await _queue_message(
            db,
            tenant_id=tenant_id,
            kind="campaign",
            to_phone=m["phone"],
            customer_id=m["customer_id"],
            customer_name=m["name"],
            lang=m["lang"],
            template_key=camp.get("template_key", ""),
            body=body,
            channel=camp.get("channel", "sms"),
            campaign_id=campaign_id,
            dedupe_key=dedupe_key,
            meta={"trigger_type": camp.get("trigger_type"), "context": m["context"]},
        )
        queued += 1

    now = _now_iso()
    await db.outreach_campaigns.update_one(
        {"tenant_id": tenant_id, "id": campaign_id},
        {"$set": {"last_run_at": now, "last_run_queued": queued, "updated_at": now}})
    return {"ok": True, "campaign_id": campaign_id, "evaluated": len(matches),
            "queued": queued, "skipped": skipped}


# ---------- message queue ----------

@router.get("/queue")
async def list_queue(status: str = "pending", user: dict = Depends(require_tenant_user)):
    """Owner-visible queue: pending messages waiting for dispatch
    (TODO(LIVE) dispatcher). Filter with ?status=canceled|sent."""
    db = get_db()
    if status not in {"pending", "canceled", "sent"}:
        raise HTTPException(400, "status must be pending, canceled, or sent")
    docs = await db.outreach_queue.find(
        {"tenant_id": user["tenant_id"], "status": status}, {"_id": 0}).to_list(500)
    docs.sort(key=lambda d: d.get("created_at", ""), reverse=True)
    return {"messages": docs, "count": len(docs), "status": status}


@router.post("/queue/{message_id}/cancel")
async def cancel_queued_message(message_id: str, user: dict = Depends(require_tenant_owner_or_admin)):
    """Owner: cancel a pending queued message before it dispatches."""
    db = get_db()
    tenant_id = user["tenant_id"]
    doc = await db.outreach_queue.find_one({"tenant_id": tenant_id, "id": message_id})
    if not doc:
        raise HTTPException(404, "Queued message not found")
    if doc.get("status") != "pending":
        raise HTTPException(409, "Only pending messages can be canceled")
    await db.outreach_queue.update_one(
        {"tenant_id": tenant_id, "id": message_id},
        {"$set": {"status": "canceled", "canceled_at": _now_iso()}})
    return {"ok": True, "id": message_id, "status": "canceled"}


# ---------- missed-call rescue ----------

@router.get("/missed-calls")
async def list_missed_calls(user: dict = Depends(require_tenant_user)):
    """Recent missed calls (conversations with channel=call, status=missed)
    with their rescue status attached."""
    db = get_db()
    tenant_id = user["tenant_id"]
    convs = await db.conversations.find(
        {"tenant_id": tenant_id, "channel": "call", "status": "missed"},
        {"_id": 0}).to_list(200)
    convs.sort(key=lambda c: c.get("created_at", ""), reverse=True)
    rescues = await db.missed_call_rescues.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(500)
    by_conv = {r.get("conversation_id"): r for r in rescues}
    out = []
    for c in convs[:50]:
        rescue = by_conv.get(c.get("id"))
        out.append({**c, "rescue": rescue, "rescued": rescue is not None})
    return {"missed_calls": out, "count": len(out)}


@router.post("/missed-calls/{conversation_id}/rescue")
async def rescue_missed_call(conversation_id: str,
                             user: dict = Depends(require_tenant_owner_or_admin)):
    """Queue a rescue SMS for a missed call (idempotent per conversation).

    The message goes out in the caller's language when a matching customer
    record exists, otherwise EN (template EN fallback). Escalation is stubbed:
    TODO(LIVE) — the inbound SMS webhook flips the rescue doc's
    `escalated=True` and alerts the shop the moment the customer replies.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    conv = await db.conversations.find_one({"tenant_id": tenant_id, "id": conversation_id})
    if not conv:
        raise HTTPException(404, "Conversation not found")
    if conv.get("status") != "missed":
        raise HTTPException(400, "Conversation is not a missed call")
    existing = await db.missed_call_rescues.find_one(
        {"tenant_id": tenant_id, "conversation_id": conversation_id})
    if existing:
        raise HTTPException(409, "A rescue SMS is already queued for this call")

    phone = conv.get("caller_phone") or ""
    if not phone:
        raise HTTPException(400, "No caller phone on record for this call")

    await _ensure_templates(db, tenant_id)
    customer = await db.customers.find_one({"tenant_id": tenant_id, "phone": phone})
    lang = _customer_lang(customer)
    business_name = await _business_name(db, tenant_id)
    tpl = await _get_template(db, tenant_id, "missed_call_rescue", lang)
    body = _render_template((tpl or {}).get("text", "Sorry we missed you!"), {
        "name": ((customer or {}).get("name") or conv.get("caller_name") or "").split()[0] or "there",
        "business_name": business_name,
    })

    queued = await _queue_message(
        db,
        tenant_id=tenant_id,
        kind="missed_call_rescue",
        to_phone=phone,
        customer_id=(customer or {}).get("id") or "",
        customer_name=(customer or {}).get("name") or conv.get("caller_name") or "",
        lang=lang,
        template_key="missed_call_rescue",
        body=body,
        dedupe_key=f"{tenant_id}:rescue:{conversation_id}",
        meta={"conversation_id": conversation_id},
    )
    rescue = {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "caller_phone": phone,
        "caller_name": (customer or {}).get("name") or conv.get("caller_name") or "",
        "lang": lang,
        "status": "queued",
        "queue_id": queued["id"],
        "escalated": False,
        "created_at": _now_iso(),
    }
    await db.missed_call_rescues.insert_one(rescue)
    # TODO(LIVE): inbound SMS webhook — when the customer replies, set
    # escalated=True here and notify the shop (in-app alert / push).
    rescue.pop("_id", None)
    return {"ok": True, "duplicate": False, "rescue": rescue, "queued_message": queued}


# ---------- templates ----------

@router.get("/templates")
async def list_templates(user: dict = Depends(require_tenant_user)):
    """Multilingual message templates, grouped by key. EN+ES seeded by
    default; more languages are extendable via PUT."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await _ensure_templates(db, tenant_id)
    docs = await db.message_templates.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(500)
    by_key: Dict[str, Dict[str, str]] = {}
    for d in docs:
        by_key.setdefault(d.get("key", ""), {})[d.get("lang", "")] = d.get("text", "")
    return {"templates": by_key, "supported_langs": list(SUPPORTED_LANGS),
            "default_keys": list(DEFAULT_TEMPLATES.keys())}


@router.put("/templates")
async def upsert_template(data: TemplateUpsertIn,
                          user: dict = Depends(require_tenant_owner_or_admin)):
    """Owner: create or update a template for a key+lang (extendable to new
    languages beyond en/es as the product grows)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    existing = await db.message_templates.find_one(
        {"tenant_id": tenant_id, "key": data.key, "lang": data.lang})
    now = _now_iso()
    if existing:
        await db.message_templates.update_one(
            {"tenant_id": tenant_id, "id": existing["id"]},
            {"$set": {"text": data.text, "updated_at": now, "seeded": False}})
    else:
        await db.message_templates.insert_one({
            "id": _uuid(),
            "tenant_id": tenant_id,
            "key": data.key,
            "lang": data.lang,
            "text": data.text,
            "seeded": False,
            "created_at": now,
            "updated_at": now,
        })
    doc = await db.message_templates.find_one(
        {"tenant_id": tenant_id, "key": data.key, "lang": data.lang}, {"_id": 0})
    return {"ok": True, "template": doc}
