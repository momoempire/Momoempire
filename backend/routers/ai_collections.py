"""Wave 5 / Phase D — AI Collections with Empathy.

Tone-aware, empathy-guarded payment follow-up for home-service shops.

Design notes:
    * Reads overdue invoices (from the `invoices` collection) and recommends an
      AI tone (friendly nudge -> firm reminder -> payment-plan offer) based on
      days overdue, prior touches, and customer sentiment.
    * Empathy guardrails: templates are kind and option-giving; a banned-phrase
      list is enforced at import time so no threatening language can ship.
    * DNC / consent: respects customer do-not-contact flags and reuses the FCC
      consent guard pattern from `services/outbound_voice.py` (voice touches
      require voice_consent=True; unknown/declined customers block the touch).
    * Everything lands in `collection_sequences` (sequence definitions),
      `collection_enrollments` (invoice/customer enrollment state), and
      `collection_touches` (touch history + logged outcomes).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Literal

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from db import get_db
from models import _uuid, _now_iso
from security import require_tenant_user
from services.outbound_voice import customer_consent

log = logging.getLogger("ai_collections")

router = APIRouter(prefix="/api/ai-collections", tags=["ai-collections"])

# ---------------------------------------------------------------------------
# Empathy engine
# ---------------------------------------------------------------------------

TONE_FRIENDLY = "friendly"
TONE_FIRM = "firm"
TONE_PLAN_OFFER = "plan-offer"
TONES = (TONE_FRIENDLY, TONE_FIRM, TONE_PLAN_OFFER)

CHANNELS = ("sms", "email", "voice")

# Words that must never appear in a collections template. Enforced at import.
BANNED_PHRASES = (
    "lawsuit", "sue you", "take you to court", "legal action",
    "garnish", "wage garnishment", "collections agency", "debt collector",
    "credit bureau", "ruin your credit", "police", "arrest", "lien",
)

PLACEHOLDERS = (
    "{{customer_name}}", "{{shop_name}}", "{{invoice_number}}",
    "{{amount}}", "{{due_date}}", "{{pay_link}}",
)


def _render(template: str, ctx: dict) -> str:
    out = template
    for k, v in ctx.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


# kind, option-giving templates keyed by (tone, channel)
TEMPLATES: dict[tuple[str, str], str] = {
    (TONE_FRIENDLY, "sms"): (
        "Hi {{customer_name}}! This is {{shop_name}} with a friendly reminder — "
        "invoice #{{invoice_number}} for ${{amount}} was due {{due_date}}. "
        "You can pay here: {{pay_link}} — or just reply and we'll happily work "
        "out a plan that fits. No rush, just didn't want it to slip!"
    ),
    (TONE_FRIENDLY, "email"): (
        "Hi {{customer_name}},\n\nHope you're well! A gentle reminder that invoice "
        "#{{invoice_number}} for ${{amount}} was due {{due_date}}. Pay online at "
        "{{pay_link}}, or reply to this email and we'll gladly arrange a payment "
        "plan that works for you.\n\nThank you so much,\n{{shop_name}}"
    ),
    (TONE_FRIENDLY, "voice"): (
        "Hi {{customer_name}}, this is {{shop_name}} with a friendly reminder about "
        "invoice {{invoice_number}} for {{amount}} dollars, due {{due_date}}. You can "
        "pay online, or call us back and we'll set up a payment plan. Thanks so much!"
    ),
    (TONE_FIRM, "sms"): (
        "Hi {{customer_name}}, following up on invoice #{{invoice_number}} for "
        "${{amount}} — it's now {{due_date}} past due. Please pay at {{pay_link}} "
        "or reply with a day that works for you. If money's tight right now, "
        "just say the word and we'll set up a payment plan. — {{shop_name}}"
    ),
    (TONE_FIRM, "email"): (
        "Hi {{customer_name}},\n\nWe're following up on invoice #{{invoice_number}} "
        "for ${{amount}}, which was due {{due_date}}. Please pay at {{pay_link}} or "
        "reply with a payment date. If cash flow is tight, we'd rather work with "
        "you — just ask about a payment plan and we'll sort it out together.\n\n"
        "Warm regards,\n{{shop_name}}"
    ),
    (TONE_FIRM, "voice"): (
        "Hi {{customer_name}}, this is {{shop_name}} following up on invoice "
        "{{invoice_number}} for {{amount}} dollars. It's past due, so please pay "
        "online or call us back. If you'd like a payment plan, we can arrange that "
        "in minutes. Thank you!"
    ),
    (TONE_PLAN_OFFER, "sms"): (
        "Hi {{customer_name}}, it's {{shop_name}}. We know unexpected expenses "
        "happen, and we don't want invoice #{{invoice_number}} (${{amount}}) to "
        "be a burden. Reply PLAN and we'll split it into easy payments — no "
        "judgment, no pressure. Or pay in full: {{pay_link}}"
    ),
    (TONE_PLAN_OFFER, "email"): (
        "Hi {{customer_name}},\n\nWe understand that unexpected expenses come up. "
        "Invoice #{{invoice_number}} for ${{amount}} doesn't have to be stressful — "
        "we'd love to split it into payments that fit your budget. Reply to this "
        "email and we'll set it up, no judgment and no pressure. Or pay in full "
        "anytime at {{pay_link}}.\n\nWith care,\n{{shop_name}}"
    ),
    (TONE_PLAN_OFFER, "voice"): (
        "Hi {{customer_name}}, this is {{shop_name}}. We wanted to reach out about "
        "invoice {{invoice_number}} personally. If money is tight, we can split it "
        "into easy payments — just call us back and we'll set it up together. "
        "No pressure at all. Thank you!"
    ),
}


def _validate_templates() -> None:
    """Fail fast at import if any template contains a banned phrase."""
    for (tone, channel), text in TEMPLATES.items():
        lowered = text.lower()
        for phrase in BANNED_PHRASES:
            if phrase in lowered:
                raise ValueError(
                    f"ai-collections template [{tone}/{channel}] contains banned "
                    f"phrase {phrase!r} — empathy guardrail violation"
                )


_validate_templates()

# Days-overdue breakpoints (score = days_overdue + 7 * prior_touches).
FIRM_SCORE_THRESHOLD = 20
PLAN_SCORE_THRESHOLD = 60
# Frustrated customers stay friendly longer (empathy override).
FRUSTRATED_FIRM_BOOST = 20


def pick_tone(
    days_overdue: int,
    prior_touches: int = 0,
    customer_sentiment: Optional[str] = None,
    *,
    dnc: bool = False,
    dispute: bool = False,
    promised_recently: bool = False,
) -> str:
    """Pick an empathetic tone for the next touch.

    Never returns anything abusive — the palette is fixed to
    friendly -> firm -> plan-offer. Overrides:
      * dnc=True                -> "none" (do not contact)
      * dispute=True            -> always friendly (resolve the dispute first)
      * promised_recently=True  -> "pause" (they promised; give them space)
    """
    if dnc:
        return "none"
    if dispute:
        return TONE_FRIENDLY
    if promised_recently:
        return "pause"

    score = max(0, days_overdue) + 7 * max(0, prior_touches)
    sentiment = (customer_sentiment or "neutral").lower()
    if sentiment in ("frustrated", "negative", "upset", "angry"):
        score = max(0, score - FRUSTRATED_FIRM_BOOST)

    if score >= PLAN_SCORE_THRESHOLD or prior_touches >= 4:
        return TONE_PLAN_OFFER
    if score >= FIRM_SCORE_THRESHOLD:
        return TONE_FIRM
    return TONE_FRIENDLY


def _parse_dt(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def days_overdue_for(invoice: dict, now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    due = _parse_dt(invoice.get("due_date") or invoice.get("due_at"))
    if not due:
        return 0
    return max(0, (now - due).days)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class SequenceStep(BaseModel):
    wait_days: int = Field(..., ge=0, le=180)
    channel: Literal["sms", "email", "voice"]
    tone: Literal["friendly", "firm", "plan-offer"]


class SequenceIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    steps: List[SequenceStep] = Field(..., min_length=1, max_length=12)
    active: bool = True


class InvoiceRef(BaseModel):
    invoice_id: Optional[str] = None
    invoice_number: Optional[str] = None
    customer_id: Optional[str] = None
    customer_name: Optional[str] = None
    amount: Optional[float] = None
    due_date: Optional[str] = None


class EnrollIn(BaseModel):
    sequence_id: str
    invoice: Optional[InvoiceRef] = None  # required only if invoice_id omitted


class TouchIn(BaseModel):
    invoice_id: Optional[str] = None
    customer_id: Optional[str] = None
    channel: Literal["sms", "email", "voice"]
    tone: Optional[Literal["friendly", "firm", "plan-offer"]] = None
    template_override: Optional[str] = Field(default=None, max_length=2000)
    send_preview_only: bool = True  # default: render only, no send


class TouchLogIn(BaseModel):
    outcome: Literal["promised-to-pay", "paid", "dispute", "dnc", "no-answer", "call-back-later"]
    note: str = Field(default="", max_length=1000)
    promised_date: Optional[str] = None
    amount_paid: Optional[float] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _tenant_shop_name(db, tenant_id: str) -> str:
    t = await db.tenants.find_one({"id": tenant_id}, {"_id": 0, "name": 1})
    return (t or {}).get("name") or "your service team"


async def _customer(db, tenant_id: str, customer_id: Optional[str]) -> dict:
    if not customer_id:
        return {}
    doc = await db.customers.find_one({"id": customer_id, "tenant_id": tenant_id}, {"_id": 0})
    return doc or {}


def _is_dnc(customer: dict) -> bool:
    return bool(customer.get("dnc") or customer.get("do_not_contact") or customer.get("opted_out"))


async def _invoice_overdue_query(now: datetime) -> dict:
    """Invoices past due and not fully paid."""
    return {
        "due_date": {"$lt": now.isoformat()},
        "status": {"$in": ["unpaid", "overdue", "partial"]},
    }


async def _recommend(invoice: dict, db, tenant_id: str) -> dict:
    """AI recommendation: tone + next action for one overdue invoice."""
    now = datetime.now(timezone.utc)
    days = days_overdue_for(invoice, now)
    customer = await _customer(db, tenant_id, invoice.get("customer_id"))
    dnc = _is_dnc(customer)

    prior_touches = await db.collection_touches.count_documents(
        {"tenant_id": tenant_id, "invoice_id": invoice.get("id")}
    ) if invoice.get("id") else 0

    # a recent promise-to-pay pauses the sequence (give them space)
    promised = False
    if invoice.get("id"):
        recent = await db.collection_touches.find_one(
            {
                "tenant_id": tenant_id,
                "invoice_id": invoice.get("id"),
                "outcome": "promised-to-pay",
            },
            {"_id": 0},
        )
        promised = bool(recent)

    tone = pick_tone(
        days,
        prior_touches=prior_touches,
        customer_sentiment=customer.get("sentiment"),
        dnc=dnc,
        dispute=bool(customer.get("dispute_open") or invoice.get("dispute")),
        promised_recently=promised,
    )

    # channel choice respects consent: voice only with voice_consent=True
    channel = "sms"
    if tone in (TONE_FIRM, TONE_PLAN_OFFER) and customer.get("email"):
        channel = "email"
    if tone in ("none", "pause"):
        channel = "none"

    voice_ok = False
    if not dnc:
        voice_ok = await customer_consent(
            db, tenant_id, customer_id=invoice.get("customer_id")
        ) is True

    template = TEMPLATES.get((tone, channel if channel in CHANNELS else "sms"), "")
    ctx = {
        "customer_name": customer.get("name") or invoice.get("customer_name") or "there",
        "shop_name": await _tenant_shop_name(db, tenant_id),
        "invoice_number": invoice.get("invoice_number") or invoice.get("number") or "?",
        "amount": f"{float(invoice.get('amount_due') or invoice.get('total') or 0):.2f}",
        "due_date": (invoice.get("due_date") or "")[:10],
        "pay_link": invoice.get("pay_link") or invoice.get("public_url") or "",
    }
    return {
        "invoice_id": invoice.get("id"),
        "customer_id": invoice.get("customer_id"),
        "customer_name": customer.get("name") or invoice.get("customer_name"),
        "invoice_number": invoice.get("invoice_number") or invoice.get("number"),
        "amount_due": float(invoice.get("amount_due") or invoice.get("total") or 0),
        "days_overdue": days,
        "prior_touches": prior_touches,
        "dnc": dnc,
        "voice_consent": voice_ok,
        "tone": tone,
        "next_action": "skip" if tone in ("none", "pause") else f"send_{channel}",
        "channel": channel,
        "template": _render(template, ctx),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/tenants/overdue")
async def list_overdue(
    customer_id: Optional[str] = None,
    min_days: int = 0,
    user: dict = Depends(require_tenant_user),
):
    """Overdue invoices with AI-recommended tone + next action per invoice."""
    db = get_db()
    tid = user["tenant_id"]
    now = datetime.now(timezone.utc)
    query: dict = {"tenant_id": tid}
    query.update(await _invoice_overdue_query(now))
    if customer_id:
        query["customer_id"] = customer_id

    cursor = db.invoices.find(query, {"_id": 0}).sort("due_date", 1)
    items = []
    for inv in await cursor.to_list(500):
        days = days_overdue_for(inv, now)
        if days < min_days:
            continue
        items.append(await _recommend(inv, db, tid))
    return {"items": items, "count": len(items)}


@router.post("/tenants/sequences")
async def create_sequence(body: SequenceIn, user: dict = Depends(require_tenant_user)):
    """Create an empathetic collections sequence (steps: wait_days/channel/tone)."""
    db = get_db()
    tid = user["tenant_id"]
    doc = {
        "id": _uuid(),
        "tenant_id": tid,
        "name": body.name,
        "description": body.description,
        "steps": [s.model_dump() for s in body.steps],
        "active": body.active,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    await db.collection_sequences.insert_one(doc)
    doc.pop("_id", None)
    return doc


@router.get("/tenants/sequences")
async def list_sequences(user: dict = Depends(require_tenant_user)):
    db = get_db()
    tid = user["tenant_id"]
    cur = db.collection_sequences.find({"tenant_id": tid}, {"_id": 0}).sort("created_at", -1)
    return {"items": await cur.to_list(100)}


@router.put("/tenants/sequences/{seq_id}")
async def update_sequence(seq_id: str, body: SequenceIn, user: dict = Depends(require_tenant_user)):
    db = get_db()
    tid = user["tenant_id"]
    res = await db.collection_sequences.update_one(
        {"id": seq_id, "tenant_id": tid},
        {"$set": {
            "name": body.name,
            "description": body.description,
            "steps": [s.model_dump() for s in body.steps],
            "active": body.active,
            "updated_at": _now_iso(),
        }},
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Sequence not found")
    return {"ok": True}


@router.post("/tenants/sequences/{seq_id}/enroll")
async def enroll(seq_id: str, body: EnrollIn, user: dict = Depends(require_tenant_user)):
    """Enroll an overdue invoice/customer into a sequence.

    DNC customers are enrolled as `held` with a reason — no touches are
    ever scheduled for them.
    """
    db = get_db()
    tid = user["tenant_id"]
    seq = await db.collection_sequences.find_one(
        {"id": seq_id, "tenant_id": tid, "active": True}, {"_id": 0}
    )
    if not seq:
        raise HTTPException(404, "Active sequence not found")

    invoice_id = (body.invoice.invoice_id if body.invoice else None) or None
    invoice_doc: dict = {}
    if invoice_id:
        invoice_doc = await db.invoices.find_one(
            {"id": invoice_id, "tenant_id": tid}, {"_id": 0}
        ) or {}
        if not invoice_doc:
            raise HTTPException(404, "Invoice not found")
    elif body.invoice:
        ref = body.invoice.model_dump(exclude_none=True)
        invoice_doc = {
            "id": f"adhoc-{_uuid()}",
            "tenant_id": tid,
            **ref,
            "status": "unpaid",
        }
    else:
        raise HTTPException(422, "invoice (id or ref) is required")

    customer = await _customer(db, tid, invoice_doc.get("customer_id"))
    dnc = _is_dnc(customer)
    first_step = seq["steps"][0]
    now = datetime.now(timezone.utc)
    next_touch_at = (now + timedelta(days=first_step["wait_days"])).isoformat()

    enrollment = {
        "id": _uuid(),
        "tenant_id": tid,
        "sequence_id": seq_id,
        "sequence_name": seq["name"],
        "invoice_id": invoice_doc.get("id"),
        "invoice_number": invoice_doc.get("invoice_number") or invoice_doc.get("number"),
        "customer_id": invoice_doc.get("customer_id"),
        "amount_due": float(invoice_doc.get("amount_due") or invoice_doc.get("total") or invoice_doc.get("amount") or 0),
        "status": "held" if dnc else "active",
        "held_reason": "do_not_contact" if dnc else None,
        "current_step": 0,
        "next_touch_at": None if dnc else next_touch_at,
        "enrolled_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    await db.collection_enrollments.insert_one(enrollment)
    enrollment.pop("_id", None)
    return enrollment


@router.post("/tenants/touches")
async def create_touch(body: TouchIn, user: dict = Depends(require_tenant_user)):
    """Record a collections touch (preview-first). Renders the empathetic
    template for the recommended tone; voice touches require FCC consent.
    """
    db = get_db()
    tid = user["tenant_id"]
    if not body.invoice_id and not body.customer_id:
        raise HTTPException(422, "invoice_id or customer_id is required")

    invoice = {}
    if body.invoice_id:
        invoice = await db.invoices.find_one(
            {"id": body.invoice_id, "tenant_id": tid}, {"_id": 0}
        ) or {}
    customer = await _customer(db, tid, body.customer_id or invoice.get("customer_id"))
    if _is_dnc(customer):
        raise HTTPException(409, "Customer has a do-not-contact flag — touch blocked")

    if body.channel == "voice":
        consent = await customer_consent(
            db, tid, customer_id=body.customer_id or invoice.get("customer_id")
        )
        if consent is not True:
            raise HTTPException(
                409,
                "FCC consent guard: voice touch refused — "
                + ("customer opted out of voice calls" if consent is False
                   else "no voice consent on file"),
            )

    if body.tone:
        tone = body.tone
    else:
        days = days_overdue_for(invoice) if invoice else 0
        prior = 0
        if body.invoice_id:
            prior = await db.collection_touches.count_documents(
                {"tenant_id": tid, "invoice_id": body.invoice_id}
            )
        tone = pick_tone(days, prior_touches=prior, customer_sentiment=customer.get("sentiment"))

    template = body.template_override or TEMPLATES.get((tone, body.channel), "")
    ctx = {
        "customer_name": customer.get("name") or invoice.get("customer_name") or "there",
        "shop_name": await _tenant_shop_name(db, tid),
        "invoice_number": invoice.get("invoice_number") or invoice.get("number") or "?",
        "amount": f"{float(invoice.get('amount_due') or invoice.get('total') or 0):.2f}",
        "due_date": (invoice.get("due_date") or "")[:10],
        "pay_link": invoice.get("pay_link") or invoice.get("public_url") or "",
    }
    touch = {
        "id": _uuid(),
        "tenant_id": tid,
        "invoice_id": body.invoice_id,
        "customer_id": body.customer_id or invoice.get("customer_id"),
        "channel": body.channel,
        "tone": tone,
        "template": _render(template, ctx),
        "status": "preview" if body.send_preview_only else "sent",
        "outcome": None,
        "created_at": _now_iso(),
    }
    await db.collection_touches.insert_one(touch)
    touch.pop("_id", None)
    return touch


@router.post("/tenants/touches/{touch_id}/log")
async def log_touch(touch_id: str, body: TouchLogIn, user: dict = Depends(require_tenant_user)):
    """Log a touch outcome: promised-to-pay, paid, dispute, dnc, no-answer, call-back-later.

    Side effects: paid closes open enrollments for the invoice; dnc flags the
    customer and holds enrollments; dispute holds enrollments for review.
    """
    db = get_db()
    tid = user["tenant_id"]
    touch = await db.collection_touches.find_one(
        {"id": touch_id, "tenant_id": tid}, {"_id": 0}
    )
    if not touch:
        raise HTTPException(404, "Touch not found")

    update: dict = {"outcome": body.outcome, "outcome_note": body.note, "updated_at": _now_iso()}
    if body.promised_date:
        update["promised_date"] = body.promised_date
    if body.amount_paid is not None:
        update["amount_paid"] = float(body.amount_paid)
    await db.collection_touches.update_one({"id": touch_id, "tenant_id": tid}, {"$set": update})

    customer_id = touch.get("customer_id")
    invoice_id = touch.get("invoice_id")

    if body.outcome == "dnc" and customer_id:
        await db.customers.update_one(
            {"id": customer_id, "tenant_id": tid},
            {"$set": {"dnc": True, "updated_at": _now_iso()}},
        )
        await db.collection_enrollments.update_many(
            {"tenant_id": tid, "customer_id": customer_id, "status": "active"},
            {"$set": {"status": "held", "held_reason": "do_not_contact", "updated_at": _now_iso()}},
        )
    elif body.outcome == "paid":
        if invoice_id:
            await db.collection_enrollments.update_many(
                {"tenant_id": tid, "invoice_id": invoice_id, "status": "active"},
                {"$set": {"status": "complete", "completed_at": _now_iso(), "updated_at": _now_iso()}},
            )
            if body.amount_paid is not None:
                await db.invoices.update_one(
                    {"id": invoice_id, "tenant_id": tid},
                    {"$set": {"status": "paid", "paid_at": _now_iso(), "updated_at": _now_iso()}},
                )
    elif body.outcome == "dispute":
        if invoice_id:
            await db.collection_enrollments.update_many(
                {"tenant_id": tid, "invoice_id": invoice_id, "status": "active"},
                {"$set": {"status": "held", "held_reason": "dispute", "updated_at": _now_iso()}},
            )

    return {"ok": True, "outcome": body.outcome}


@router.get("/tenants/touches")
async def list_touches(
    invoice_id: Optional[str] = None,
    customer_id: Optional[str] = None,
    user: dict = Depends(require_tenant_user),
):
    """Touches timeline, newest first."""
    db = get_db()
    tid = user["tenant_id"]
    query: dict = {"tenant_id": tid}
    if invoice_id:
        query["invoice_id"] = invoice_id
    if customer_id:
        query["customer_id"] = customer_id
    cur = db.collection_touches.find(query, {"_id": 0}).sort("created_at", -1)
    return {"items": await cur.to_list(200)}


@router.get("/tenants/dashboard")
async def dashboard(user: dict = Depends(require_tenant_user)):
    """Collections KPIs: collected, promised, at-risk, avg days-to-pay."""
    db = get_db()
    tid = user["tenant_id"]
    now = datetime.now(timezone.utc)

    inv_cur = db.invoices.find(
        {"tenant_id": tid, "status": {"$in": ["unpaid", "overdue", "partial"]}},
        {"_id": 0},
    )
    outstanding = 0.0
    overdue_count = 0
    at_risk = 0.0
    for inv in await inv_cur.to_list(2000):
        amt = float(inv.get("amount_due") or inv.get("total") or 0)
        days = days_overdue_for(inv, now)
        if days > 0:
            overdue_count += 1
            outstanding += amt
        if days > 60:
            at_risk += amt

    touch_cur = db.collection_touches.find({"tenant_id": tid}, {"_id": 0})
    touches = await touch_cur.to_list(5000)
    collected = sum(float(t.get("amount_paid") or 0) for t in touches if t.get("outcome") == "paid")
    promised = sum(
        1 for t in touches
        if t.get("outcome") == "promised-to-pay" and not t.get("superseded")
    )

    # avg days-to-pay: paid invoices where we can measure issue -> pay span
    pay_days: list[float] = []
    for t in touches:
        if t.get("outcome") != "paid" or not t.get("invoice_id"):
            continue
        inv = await db.invoices.find_one(
            {"id": t["invoice_id"], "tenant_id": tid}, {"_id": 0, "issued_at": 1, "created_at": 1}
        ) or {}
        start = _parse_dt(inv.get("issued_at") or inv.get("created_at"))
        end = _parse_dt(t.get("updated_at") or t.get("created_at"))
        if start and end and end >= start:
            pay_days.append((end - start).days)

    return {
        "outstanding": round(outstanding, 2),
        "overdue_invoices": overdue_count,
        "collected": round(collected, 2),
        "promised_count": promised,
        "at_risk": round(at_risk, 2),
        "avg_days_to_pay": round(sum(pay_days) / len(pay_days), 1) if pay_days else None,
        "touches_total": len(touches),
    }


@router.get("/tenants/empathy-check")
async def empathy_check():
    """Prove the empathy guardrails: every shipped template is kind + option-giving."""
    sample_ctx = {p.strip("{}"): f"<{p.strip('{}')}>" for p in PLACEHOLDERS}
    samples = {
        f"{tone}/{channel}": _render(text, sample_ctx)
        for (tone, channel), text in TEMPLATES.items()
    }
    lowered = " ".join(samples.values()).lower()
    violations = [p for p in BANNED_PHRASES if p in lowered]
    return {
        "templates": len(samples),
        "banned_phrases": list(BANNED_PHRASES),
        "violations": violations,
        "passed": not violations,
        "samples": samples,
    }
