"""Marketing automation engine (roadmap item #31) — DUAL PURPOSE.

One campaign engine, two contexts:
  (a) SHOP context    — shops market to homeowners. Endpoints under
      /tenants/marketing, tenant-scoped via require_tenant_user; writes
      require owner/admin. Audience = the tenant's `customers`.
  (b) PLATFORM context — WE market to prospective shops (trial drip,
      feature announcements, case-study blasts). Endpoints under
      /admin/marketing, restricted to require_platform_admin.
      Audience = `platform_prospects`.

Building blocks: rule-based segments (field/operator/value + preview),
campaigns (email/SMS, one-shot), nurture sequences (ordered steps with
day-delays, drip via enroll + process-due), send/test-send, per-campaign
unsubscribe tokens + suppression list, campaign stats.

Delivery is provider-abstracted (services.marketing_automation): log-only
providers by default, TODO(LIVE) skeletons for Resend/SendGrid + Twilio.

Every query is scoped by (tenant_id, context) taken from the auth
dependency — never from client input.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin, require_platform_admin
from services.marketing_automation import (
    get_email_provider,
    get_sms_provider,
    make_unsubscribe_token,
    verify_unsubscribe_token,
    unsubscribe_url,
)

logger = logging.getLogger("marketing_automation")

# ---------------------------------------------------------------------------
# Scope helpers
# ---------------------------------------------------------------------------

SHOP = "shop"
PLATFORM = "platform"
_PLATFORM_TENANT = None  # platform-context docs carry tenant_id=None + context="platform"

# Audience source collections per context.
_AUDIENCE_COLLECTION = {SHOP: "customers", PLATFORM: "platform_prospects"}


def _scope(tenant_id: Optional[str], context: str) -> dict:
    return {"tenant_id": tenant_id, "context": context}


# ---------------------------------------------------------------------------
# Pydantic models (kept here per contract; backend/models.py untouched)
# ---------------------------------------------------------------------------

_OPERATORS = {
    "equals", "not_equals", "contains", "not_contains", "starts_with",
    "gt", "gte", "lt", "lte", "in", "not_in", "is_empty", "is_not_empty",
}
_CHANNELS = {"email", "sms"}


class SegmentRule(BaseModel):
    field: str = Field(..., min_length=1, max_length=80,
                       description="Dot-path into the contact doc, e.g. equipment_age_years")
    operator: str = Field(...)
    value: Any = None

    @field_validator("operator")
    @classmethod
    def _op_valid(cls, v: str) -> str:
        if v not in _OPERATORS:
            raise ValueError(f"operator must be one of {sorted(_OPERATORS)}")
        return v

    @field_validator("field")
    @classmethod
    def _field_safe(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.]+", v):
            raise ValueError("field must be alphanumeric/dot/underscore only")
        return v


class SegmentIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    rules: list[SegmentRule] = Field(default_factory=list, max_length=25)
    match: Literal["all", "any"] = "all"


class SegmentOut(SegmentIn):
    id: str
    context: str
    audience: str
    created_at: str
    updated_at: str


class SequenceStep(BaseModel):
    channel: Literal["email", "sms"]
    delay_days: int = Field(default=0, ge=0, le=365)
    subject: str = Field(default="", max_length=200)
    body: str = Field(..., min_length=1, max_length=5000)

    @field_validator("subject")
    @classmethod
    def _subject_for_email(cls, v: str, info) -> str:
        if info.data.get("channel") == "email" and not v.strip():
            raise ValueError("email steps require a subject")
        return v


class SequenceIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    steps: list[SequenceStep] = Field(..., min_length=1, max_length=20)


class SequenceOut(SequenceIn):
    id: str
    context: str
    created_at: str
    updated_at: str


class CampaignIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    channel: Literal["email", "sms"]
    segment_id: Optional[str] = None
    sequence_id: Optional[str] = None
    subject: str = Field(default="", max_length=200)
    body: str = Field(..., min_length=1, max_length=8000)

    @field_validator("subject")
    @classmethod
    def _subject_for_email(cls, v: str, info) -> str:
        if info.data.get("channel") == "email" and not v.strip():
            raise ValueError("email campaigns require a subject")
        return v


class CampaignOut(CampaignIn):
    id: str
    context: str
    status: str
    stats: dict
    created_at: str
    updated_at: str


class TestSendIn(BaseModel):
    to: str = Field(..., min_length=3, max_length=200)


class EnrollIn(BaseModel):
    segment_id: Optional[str] = None
    contact_ids: list[str] = Field(default_factory=list, max_length=500)


# ---------------------------------------------------------------------------
# Segment rule evaluation (Python-side so it works identically on real and
# fake Mongo in tests. TODO(LIVE): push simple rules into a Mongo query for
# large audiences instead of scanning.)
# ---------------------------------------------------------------------------

def _field_value(doc: dict, field: str) -> Any:
    cur: Any = doc
    for part in field.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _as_number(v: Any) -> Optional[float]:
    try:
        if isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _rule_matches(doc: dict, rule: SegmentRule) -> bool:
    actual = _field_value(doc, rule.field)
    op, want = rule.operator, rule.value
    if op == "is_empty":
        return actual is None or actual == "" or actual == []
    if op == "is_not_empty":
        return not (actual is None or actual == "" or actual == [])
    if op == "equals":
        return str(actual) == str(want)
    if op == "not_equals":
        return str(actual) != str(want)
    if op == "contains":
        return str(want).lower() in str(actual or "").lower()
    if op == "not_contains":
        return str(want).lower() not in str(actual or "").lower()
    if op == "starts_with":
        return str(actual or "").lower().startswith(str(want).lower())
    if op == "in":
        want_list = want if isinstance(want, list) else [want]
        return str(actual) in {str(x) for x in want_list}
    if op == "not_in":
        want_list = want if isinstance(want, list) else [want]
        return str(actual) not in {str(x) for x in want_list}
    # numeric comparisons, with lexicographic fallback (ISO-8601 dates
    # compare correctly as strings, e.g. last_visit_at win-back rules)
    a_num, w_num = _as_number(actual), _as_number(want)
    if a_num is not None and w_num is not None:
        a, w = a_num, w_num
    else:
        a, w = str(actual or ""), str(want)
        if not a:
            return False
    if op == "gt":
        return a > w
    if op == "gte":
        return a >= w
    if op == "lt":
        return a < w
    if op == "lte":
        return a <= w
    return False


def _segment_matches(doc: dict, rules: list[dict], match: str) -> bool:
    parsed = [SegmentRule(**r) for r in rules]
    results = [_rule_matches(doc, r) for r in parsed]
    if not results:
        return True
    return all(results) if match == "all" else any(results)


async def _audience_docs(db, tenant_id: Optional[str], context: str) -> list[dict]:
    coll = _AUDIENCE_COLLECTION[context]
    query = {"tenant_id": tenant_id} if context == SHOP else {}
    cursor = getattr(db, coll).find(query, {"_id": 0})
    return await cursor.to_list(50_000)


async def _resolve_segment(db, tenant_id: Optional[str], context: str, segment_id: str) -> list[dict]:
    seg = await db.marketing_segments.find_one(
        {"id": segment_id, **_scope(tenant_id, context)}, {"_id": 0})
    if not seg:
        raise HTTPException(404, "Segment not found")
    docs = await _audience_docs(db, tenant_id, context)
    return [d for d in docs if _segment_matches(d, seg.get("rules", []), seg.get("match", "all"))]


# ---------------------------------------------------------------------------
# Suppression list + template rendering
# ---------------------------------------------------------------------------

def _normalize_contact(channel: str, to: str) -> str:
    to = (to or "").strip()
    return to.lower() if channel == "email" else re.sub(r"\s+", "", to)


async def _is_suppressed(db, tenant_id: Optional[str], context: str,
                         contact: str, campaign_id: Optional[str] = None) -> bool:
    base = {"tenant_id": tenant_id, "context": context, "contact": contact}
    if await db.marketing_suppressions.find_one(base, {"_id": 0}):
        return True  # global unsubscribe
    if campaign_id and await db.marketing_suppressions.find_one(
            {**base, "campaign_id": campaign_id}, {"_id": 0}):
        return True
    return False


_TOKEN_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _render(template: str, contact: dict, extra: dict) -> str:
    def _rep(m: re.Match) -> str:
        key = m.group(1)
        if key in extra:
            return str(extra[key])
        return str(contact.get(key, ""))
    return _TOKEN_RE.sub(_rep, template or "")


def _contact_channel_address(channel: str, contact: dict) -> Optional[str]:
    return contact.get("email") if channel == "email" else contact.get("phone")


# ---------------------------------------------------------------------------
# Core operations (shared by shop + platform routers)
# ---------------------------------------------------------------------------

async def _get_campaign(db, tenant_id, context, campaign_id) -> dict:
    camp = await db.marketing_campaigns.find_one(
        {"id": campaign_id, **_scope(tenant_id, context)}, {"_id": 0})
    if not camp:
        raise HTTPException(404, "Campaign not found")
    return camp


async def _record_send(db, *, tenant_id, context, campaign_id, contact: dict,
                       to: str, channel: str, result: dict,
                       sequence_id=None, enrollment_id=None, test=False,
                       error: str = "") -> None:
    await db.marketing_sends.insert_one({
        "id": _uuid(),
        "tenant_id": tenant_id,
        "context": context,
        "campaign_id": campaign_id,
        "sequence_id": sequence_id,
        "enrollment_id": enrollment_id,
        "contact_id": contact.get("id"),
        "to": to,
        "channel": channel,
        "status": "sent" if not error else "failed",
        "provider": result.get("provider"),
        "message_id": result.get("message_id"),
        "demo": bool(result.get("demo")),
        "test": test,
        "error": error,
        "created_at": _now_iso(),
    })


async def _deliver_one(db, *, tenant_id, context, campaign_id, contact: dict,
                       channel: str, subject: str, body: str,
                       sequence_id=None, enrollment_id=None, test=False) -> str:
    """Returns 'sent' | 'skipped_suppressed' | 'failed' | 'skipped_no_address'."""
    to = _contact_channel_address(channel, contact)
    if not to:
        return "skipped_no_address"
    norm = _normalize_contact(channel, to)
    if not test and await _is_suppressed(db, tenant_id, context, norm, campaign_id):
        return "skipped_suppressed"
    token = make_unsubscribe_token(campaign_id=campaign_id, contact=norm)
    extra = {"unsubscribe_url": unsubscribe_url(
        base_url=os.environ.get("PUBLIC_BACKEND_URL", ""), token=token)}
    rendered_subject = _render(subject, contact, extra)
    rendered_body = _render(body, contact, extra)
    try:
        if channel == "email":
            provider = get_email_provider(tenant_id)
            result = await provider.send(to=to, subject=rendered_subject,
                                         body=rendered_body)
        else:
            provider = get_sms_provider(tenant_id)
            result = await provider.send(to=to, body=rendered_body)
        await _record_send(db, tenant_id=tenant_id, context=context,
                           campaign_id=campaign_id, contact=contact, to=to,
                           channel=channel, result=result,
                           sequence_id=sequence_id, enrollment_id=enrollment_id,
                           test=test)
        return "sent"
    except Exception as e:  # never let one bad contact kill a blast
        logger.exception("marketing send failed campaign=%s to=%s", campaign_id, to)
        await _record_send(db, tenant_id=tenant_id, context=context,
                           campaign_id=campaign_id, contact=contact, to=to,
                           channel=channel, result={"provider": "error"},
                           sequence_id=sequence_id, enrollment_id=enrollment_id,
                           test=test, error=str(e)[:300])
        return "failed"


async def _send_campaign(db, *, tenant_id, context, campaign_id) -> dict:
    camp = await _get_campaign(db, tenant_id, context, campaign_id)
    if camp.get("status") not in {"draft", "scheduled"}:
        raise HTTPException(400, f"Campaign already {camp.get('status')}; duplicate sends are blocked")
    counts = {"sent": 0, "skipped_suppressed": 0, "failed": 0, "skipped_no_address": 0}

    if camp.get("sequence_id"):
        # Drip campaign: enroll the segment, then process steps due now.
        enrolled = await _enroll_contacts(db, tenant_id=tenant_id, context=context,
                                          sequence_id=camp["sequence_id"],
                                          campaign_id=campaign_id,
                                          segment_id=camp.get("segment_id"))
        due = await _process_due(db, tenant_id=tenant_id, context=context,
                                 sequence_id=camp["sequence_id"])
        counts["enrolled"] = enrolled
        counts["steps_sent_now"] = due["steps_sent"]
    elif camp.get("segment_id"):
        contacts = await _resolve_segment(db, tenant_id, context, camp["segment_id"])
        for contact in contacts:
            outcome = await _deliver_one(db, tenant_id=tenant_id, context=context,
                                         campaign_id=campaign_id, contact=contact,
                                         channel=camp["channel"],
                                         subject=camp.get("subject", ""),
                                         body=camp["body"])
            counts[outcome] = counts.get(outcome, 0) + 1
    else:
        raise HTTPException(400, "Campaign has neither segment_id nor sequence_id")

    await db.marketing_campaigns.update_one(
        {"id": campaign_id, **_scope(tenant_id, context)},
        {"$set": {"status": "sent", "stats": counts, "updated_at": _now_iso(),
                  "sent_at": _now_iso()}})
    return {"campaign_id": campaign_id, "status": "sent", **counts}


async def _test_send(db, *, tenant_id, context, campaign_id, to: str) -> dict:
    camp = await _get_campaign(db, tenant_id, context, campaign_id)
    channel = camp["channel"]
    contact = {"email": to} if channel == "email" else {"phone": to}
    outcome = await _deliver_one(db, tenant_id=tenant_id, context=context,
                                 campaign_id=campaign_id, contact=contact,
                                 channel=channel, subject=camp.get("subject", ""),
                                 body=camp["body"], test=True)
    if outcome == "skipped_no_address":
        raise HTTPException(400, "No address to send to")
    return {"campaign_id": campaign_id, "to": to, "outcome": outcome, "test": True}


async def _campaign_stats(db, tenant_id, context, campaign_id) -> dict:
    await _get_campaign(db, tenant_id, context, campaign_id)
    sends = await db.marketing_sends.find(
        {"campaign_id": campaign_id, **_scope(tenant_id, context)}, {"_id": 0}).to_list(50_000)
    by_status: dict[str, int] = {}
    by_channel: dict[str, int] = {}
    for s in sends:
        if s.get("test"):
            continue
        by_status[s.get("status", "sent")] = by_status.get(s.get("status", "sent"), 0) + 1
        by_channel[s.get("channel", "?")] = by_channel.get(s.get("channel", "?"), 0) + 1
    supp = await db.marketing_suppressions.find(
        {"campaign_id": campaign_id, **_scope(tenant_id, context)}, {"_id": 0}).to_list(50_000)
    return {"campaign_id": campaign_id, "total_sends": sum(by_status.values()),
            "by_status": by_status, "by_channel": by_channel,
            "suppressed": len(supp)}


# ---------------------------------------------------------------------------
# Sequences: enroll + drip processing
# ---------------------------------------------------------------------------

def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def _enroll_contacts(db, *, tenant_id, context, sequence_id: str,
                           campaign_id: Optional[str] = None,
                           segment_id: Optional[str] = None,
                           contact_ids: Optional[list[str]] = None) -> int:
    seq = await db.marketing_sequences.find_one(
        {"id": sequence_id, **_scope(tenant_id, context)}, {"_id": 0})
    if not seq:
        raise HTTPException(404, "Sequence not found")
    if segment_id:
        contacts = await _resolve_segment(db, tenant_id, context, segment_id)
    elif contact_ids:
        docs = await _audience_docs(db, tenant_id, context)
        wanted = set(contact_ids)
        contacts = [d for d in docs if d.get("id") in wanted]
    else:
        raise HTTPException(400, "Provide segment_id or contact_ids")
    now = _now_iso()
    created = 0
    for contact in contacts:
        exists = await db.marketing_enrollments.find_one(
            {"sequence_id": sequence_id, "contact_id": contact.get("id"),
             "status": "active", **_scope(tenant_id, context)}, {"_id": 0})
        if exists:
            continue
        await db.marketing_enrollments.insert_one({
            "id": _uuid(),
            "tenant_id": tenant_id,
            "context": context,
            "sequence_id": sequence_id,
            "campaign_id": campaign_id,
            "contact_id": contact.get("id"),
            "contact": {k: contact.get(k) for k in
                        ("id", "name", "first_name", "email", "phone", "company") if contact.get(k)},
            "current_step": 0,
            "next_due_at": now,  # step 0 sends immediately
            "status": "active",
            "created_at": now,
        })
        created += 1
    return created


async def _process_due(db, *, tenant_id, context, sequence_id: Optional[str] = None) -> dict:
    """Send every due drip step for active enrollments. Returns counts."""
    query = {"status": "active", **_scope(tenant_id, context)}
    if sequence_id:
        query["sequence_id"] = sequence_id
    enrollments = await db.marketing_enrollments.find(query, {"_id": 0}).to_list(50_000)
    now = _now_iso()
    steps_sent = 0
    completed = 0
    for enr in enrollments:
        if enr.get("next_due_at") and enr["next_due_at"] > now:
            continue
        seq = await db.marketing_sequences.find_one(
            {"id": enr["sequence_id"], **_scope(tenant_id, context)}, {"_id": 0})
        if not seq:
            continue
        steps = sorted(seq.get("steps", []), key=lambda s: (s.get("order", 0)))
        idx = enr.get("current_step", 0)
        if idx >= len(steps):
            await db.marketing_enrollments.update_one(
                {"id": enr["id"]}, {"$set": {"status": "completed"}})
            completed += 1
            continue
        step = steps[idx]
        outcome = await _deliver_one(
            db, tenant_id=tenant_id, context=context,
            campaign_id=enr.get("campaign_id") or f"seq:{seq['id']}",
            contact=enr.get("contact", {}), channel=step["channel"],
            subject=step.get("subject", ""), body=step["body"],
            sequence_id=seq["id"], enrollment_id=enr["id"])
        nxt = idx + 1
        if nxt >= len(steps):
            update = {"current_step": nxt, "status": "completed", "next_due_at": None}
            completed += 1
        else:
            delay = steps[nxt].get("delay_days", 0)
            due_at = (datetime.now(timezone.utc) + timedelta(days=delay)).isoformat()
            update = {"current_step": nxt, "next_due_at": due_at}
        await db.marketing_enrollments.update_one({"id": enr["id"]}, {"$set": update})
        if outcome == "sent":
            steps_sent += 1
    return {"steps_sent": steps_sent, "completed": completed,
            "evaluated": len(enrollments)}


# ---------------------------------------------------------------------------
# Response shaping
# ---------------------------------------------------------------------------

def _seg_out(doc: dict) -> dict:
    return {"id": doc["id"], "name": doc["name"], "description": doc.get("description", ""),
            "rules": doc.get("rules", []), "match": doc.get("match", "all"),
            "context": doc["context"], "audience": doc.get("audience", ""),
            "created_at": doc.get("created_at", ""), "updated_at": doc.get("updated_at", "")}


def _camp_out(doc: dict) -> dict:
    return {"id": doc["id"], "name": doc["name"], "channel": doc["channel"],
            "segment_id": doc.get("segment_id"), "sequence_id": doc.get("sequence_id"),
            "subject": doc.get("subject", ""), "body": doc.get("body", ""),
            "context": doc["context"], "status": doc.get("status", "draft"),
            "stats": doc.get("stats", {}),
            "created_at": doc.get("created_at", ""), "updated_at": doc.get("updated_at", "")}


def _seq_out(doc: dict) -> dict:
    return {"id": doc["id"], "name": doc["name"],
            "description": doc.get("description", ""), "steps": doc.get("steps", []),
            "context": doc["context"],
            "created_at": doc.get("created_at", ""), "updated_at": doc.get("updated_at", "")}


def _new_stats() -> dict:
    return {"sent": 0, "skipped_suppressed": 0, "failed": 0, "skipped_no_address": 0}


# ---------------------------------------------------------------------------
# Shop router — /tenants/marketing
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/tenants/marketing", tags=["marketing"],
                   dependencies=[Depends(require_tenant_user)])
_owner = Depends(require_tenant_owner_or_admin)


def _shop_ctx(user: dict) -> tuple[Optional[str], str]:
    return user.get("tenant_id"), SHOP


# ---- segments ----
@router.post("/segments", response_model=SegmentOut)
async def create_segment(data: SegmentIn, user: dict = _owner):
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    doc = {"id": _uuid(), **_scope(tenant_id, context), "name": data.name,
           "description": data.description, "rules": [r.model_dump() for r in data.rules],
           "match": data.match, "audience": _AUDIENCE_COLLECTION[context],
           "created_at": _now_iso(), "updated_at": _now_iso()}
    await db.marketing_segments.insert_one(doc)
    return _seg_out(doc)


@router.get("/segments", response_model=list[SegmentOut])
async def list_segments(user: dict = Depends(require_tenant_user)):
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    docs = await db.marketing_segments.find(_scope(tenant_id, context), {"_id": 0}).to_list(500)
    return [_seg_out(d) for d in docs]


@router.get("/segments/{segment_id}", response_model=SegmentOut)
async def get_segment(segment_id: str, user: dict = Depends(require_tenant_user)):
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    doc = await db.marketing_segments.find_one(
        {"id": segment_id, **_scope(tenant_id, context)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Segment not found")
    return _seg_out(doc)


@router.put("/segments/{segment_id}", response_model=SegmentOut)
async def update_segment(segment_id: str, data: SegmentIn, user: dict = _owner):
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    res = await db.marketing_segments.update_one(
        {"id": segment_id, **_scope(tenant_id, context)},
        {"$set": {"name": data.name, "description": data.description,
                  "rules": [r.model_dump() for r in data.rules],
                  "match": data.match, "updated_at": _now_iso()}})
    if not res.matched_count:
        raise HTTPException(404, "Segment not found")
    doc = await db.marketing_segments.find_one(
        {"id": segment_id, **_scope(tenant_id, context)}, {"_id": 0})
    return _seg_out(doc)


@router.delete("/segments/{segment_id}")
async def delete_segment(segment_id: str, user: dict = _owner):
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    res = await db.marketing_segments.delete_one(
        {"id": segment_id, **_scope(tenant_id, context)})
    if not res.deleted_count:
        raise HTTPException(404, "Segment not found")
    return {"deleted": segment_id}


@router.post("/segments/{segment_id}/preview")
async def preview_segment(segment_id: str, user: dict = Depends(require_tenant_user)):
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    contacts = await _resolve_segment(db, tenant_id, context, segment_id)
    sample = [{"id": c.get("id"), "name": c.get("name"), "email": c.get("email"),
               "phone": c.get("phone")} for c in contacts[:10]]
    return {"segment_id": segment_id, "count": len(contacts), "sample": sample}


# ---- campaigns ----
@router.post("/campaigns", response_model=CampaignOut)
async def create_campaign(data: CampaignIn, user: dict = _owner):
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    doc = {"id": _uuid(), **_scope(tenant_id, context), "name": data.name,
           "channel": data.channel, "segment_id": data.segment_id,
           "sequence_id": data.sequence_id, "subject": data.subject, "body": data.body,
           "status": "draft", "stats": _new_stats(),
           "created_at": _now_iso(), "updated_at": _now_iso()}
    await db.marketing_campaigns.insert_one(doc)
    return _camp_out(doc)


@router.get("/campaigns", response_model=list[CampaignOut])
async def list_campaigns(user: dict = Depends(require_tenant_user)):
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    docs = await db.marketing_campaigns.find(_scope(tenant_id, context), {"_id": 0}).to_list(500)
    return [_camp_out(d) for d in docs]


@router.get("/campaigns/{campaign_id}", response_model=CampaignOut)
async def get_campaign(campaign_id: str, user: dict = Depends(require_tenant_user)):
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    return _camp_out(await _get_campaign(db, tenant_id, context, campaign_id))


@router.put("/campaigns/{campaign_id}", response_model=CampaignOut)
async def update_campaign(campaign_id: str, data: CampaignIn, user: dict = _owner):
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    camp = await _get_campaign(db, tenant_id, context, campaign_id)
    if camp.get("status") not in {"draft", "scheduled"}:
        raise HTTPException(400, "Only draft/scheduled campaigns can be edited")
    await db.marketing_campaigns.update_one(
        {"id": campaign_id, **_scope(tenant_id, context)},
        {"$set": {"name": data.name, "channel": data.channel,
                  "segment_id": data.segment_id, "sequence_id": data.sequence_id,
                  "subject": data.subject, "body": data.body, "updated_at": _now_iso()}})
    return _camp_out(await _get_campaign(db, tenant_id, context, campaign_id))


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: str, user: dict = _owner):
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    res = await db.marketing_campaigns.delete_one(
        {"id": campaign_id, **_scope(tenant_id, context)})
    if not res.deleted_count:
        raise HTTPException(404, "Campaign not found")
    return {"deleted": campaign_id}


@router.post("/campaigns/{campaign_id}/test-send")
async def test_send_campaign(campaign_id: str, data: TestSendIn, user: dict = _owner):
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    return await _test_send(db, tenant_id=tenant_id, context=context,
                            campaign_id=campaign_id, to=data.to)


@router.post("/campaigns/{campaign_id}/send")
async def send_campaign(campaign_id: str, user: dict = _owner):
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    return await _send_campaign(db, tenant_id=tenant_id, context=context,
                                campaign_id=campaign_id)


@router.get("/campaigns/{campaign_id}/stats")
async def campaign_stats(campaign_id: str, user: dict = Depends(require_tenant_user)):
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    return await _campaign_stats(db, tenant_id, context, campaign_id)


# ---- sequences ----
@router.post("/sequences", response_model=SequenceOut)
async def create_sequence(data: SequenceIn, user: dict = _owner):
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    steps = [dict(s.model_dump(), order=i) for i, s in enumerate(data.steps)]
    doc = {"id": _uuid(), **_scope(tenant_id, context), "name": data.name,
           "description": data.description, "steps": steps,
           "created_at": _now_iso(), "updated_at": _now_iso()}
    await db.marketing_sequences.insert_one(doc)
    return _seq_out(doc)


@router.get("/sequences", response_model=list[SequenceOut])
async def list_sequences(user: dict = Depends(require_tenant_user)):
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    docs = await db.marketing_sequences.find(_scope(tenant_id, context), {"_id": 0}).to_list(500)
    return [_seq_out(d) for d in docs]


@router.get("/sequences/{sequence_id}", response_model=SequenceOut)
async def get_sequence(sequence_id: str, user: dict = Depends(require_tenant_user)):
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    doc = await db.marketing_sequences.find_one(
        {"id": sequence_id, **_scope(tenant_id, context)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Sequence not found")
    return _seq_out(doc)


@router.put("/sequences/{sequence_id}", response_model=SequenceOut)
async def update_sequence(sequence_id: str, data: SequenceIn, user: dict = _owner):
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    steps = [dict(s.model_dump(), order=i) for i, s in enumerate(data.steps)]
    res = await db.marketing_sequences.update_one(
        {"id": sequence_id, **_scope(tenant_id, context)},
        {"$set": {"name": data.name, "description": data.description,
                  "steps": steps, "updated_at": _now_iso()}})
    if not res.matched_count:
        raise HTTPException(404, "Sequence not found")
    doc = await db.marketing_sequences.find_one(
        {"id": sequence_id, **_scope(tenant_id, context)}, {"_id": 0})
    return _seq_out(doc)


@router.delete("/sequences/{sequence_id}")
async def delete_sequence(sequence_id: str, user: dict = _owner):
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    res = await db.marketing_sequences.delete_one(
        {"id": sequence_id, **_scope(tenant_id, context)})
    if not res.deleted_count:
        raise HTTPException(404, "Sequence not found")
    return {"deleted": sequence_id}


@router.post("/sequences/{sequence_id}/enroll")
async def enroll_sequence(sequence_id: str, data: EnrollIn, user: dict = _owner):
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    created = await _enroll_contacts(db, tenant_id=tenant_id, context=context,
                                     sequence_id=sequence_id,
                                     segment_id=data.segment_id,
                                     contact_ids=data.contact_ids or None)
    return {"sequence_id": sequence_id, "enrolled": created}


@router.get("/sequences/{sequence_id}/schedule-preview")
async def sequence_schedule_preview(sequence_id: str, user: dict = Depends(require_tenant_user)):
    """Absolute send dates per step if enrolled now — ordered, day 0/3/7 style."""
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    doc = await db.marketing_sequences.find_one(
        {"id": sequence_id, **_scope(tenant_id, context)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Sequence not found")
    now = datetime.now(timezone.utc)
    steps = sorted(doc.get("steps", []), key=lambda s: s.get("order", 0))
    out = []
    for i, step in enumerate(steps):
        delay = step.get("delay_days", 0)
        out.append({"order": i, "channel": step["channel"], "delay_days": delay,
                    "send_at": (now + timedelta(days=delay)).isoformat(),
                    "subject": step.get("subject", "")})
    return {"sequence_id": sequence_id, "steps": out}


@router.post("/sequences/process-due")
async def process_due_sequences(user: dict = _owner):
    """Send every drip step that is due now (call from a cron; manual here)."""
    tenant_id, context = _shop_ctx(user)
    db = get_db()
    return await _process_due(db, tenant_id=tenant_id, context=context)


# ---------------------------------------------------------------------------
# Public unsubscribe (NO auth — link is clicked from an email/SMS)
# ---------------------------------------------------------------------------

public_router = APIRouter(prefix="/tenants/marketing", tags=["marketing"])


@public_router.get("/unsubscribe/{token}", response_class=HTMLResponse)
async def unsubscribe(token: str):
    data = verify_unsubscribe_token(token)
    if not data:
        raise HTTPException(400, "Invalid or expired unsubscribe link")
    db = get_db()
    campaign_id, contact = data["campaign_id"], data["contact"]
    # Resolve scope from the campaign so the suppression lands in the right tenant.
    camp = await db.marketing_campaigns.find_one({"id": campaign_id}, {"_id": 0})
    if not camp:
        # Sequence-only sends use campaign_id "seq:<id>"; fall back to the sequence scope.
        seq_id = campaign_id[4:] if campaign_id.startswith("seq:") else None
        seq = await db.marketing_sequences.find_one({"id": seq_id}, {"_id": 0}) if seq_id else None
        if not seq:
            raise HTTPException(404, "Campaign not found")
        scope = _scope(seq.get("tenant_id"), seq.get("context"))
    else:
        scope = _scope(camp.get("tenant_id"), camp.get("context"))
    # A click is a global opt-out for that address: no more marketing from
    # this sender (the token's campaign_id is kept for attribution only).
    await db.marketing_suppressions.insert_one({
        "id": _uuid(), **scope, "contact": contact, "campaign_id": None,
        "via_campaign_id": campaign_id, "created_at": _now_iso(),
    })
    return HTMLResponse(
        "<html><body style='font-family:sans-serif;text-align:center;padding:3rem'>"
        "<h2>You've been unsubscribed</h2>"
        "<p>You won't receive further marketing messages from us.</p>"
        "</body></html>")


# ---------------------------------------------------------------------------
# Platform admin router — /admin/marketing (platform context)
# ---------------------------------------------------------------------------

admin_router = APIRouter(prefix="/admin/marketing", tags=["marketing"],
                         dependencies=[Depends(require_platform_admin)])


def _platform_ctx() -> tuple[Optional[str], str]:
    return _PLATFORM_TENANT, PLATFORM


@admin_router.post("/segments", response_model=SegmentOut)
async def admin_create_segment(data: SegmentIn):
    tenant_id, context = _platform_ctx()
    db = get_db()
    doc = {"id": _uuid(), **_scope(tenant_id, context), "name": data.name,
           "description": data.description, "rules": [r.model_dump() for r in data.rules],
           "match": data.match, "audience": _AUDIENCE_COLLECTION[context],
           "created_at": _now_iso(), "updated_at": _now_iso()}
    await db.marketing_segments.insert_one(doc)
    return _seg_out(doc)


@admin_router.get("/segments", response_model=list[SegmentOut])
async def admin_list_segments():
    tenant_id, context = _platform_ctx()
    db = get_db()
    docs = await db.marketing_segments.find(_scope(tenant_id, context), {"_id": 0}).to_list(500)
    return [_seg_out(d) for d in docs]


@admin_router.get("/segments/{segment_id}", response_model=SegmentOut)
async def admin_get_segment(segment_id: str):
    tenant_id, context = _platform_ctx()
    db = get_db()
    doc = await db.marketing_segments.find_one(
        {"id": segment_id, **_scope(tenant_id, context)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Segment not found")
    return _seg_out(doc)


@admin_router.put("/segments/{segment_id}", response_model=SegmentOut)
async def admin_update_segment(segment_id: str, data: SegmentIn):
    tenant_id, context = _platform_ctx()
    db = get_db()
    res = await db.marketing_segments.update_one(
        {"id": segment_id, **_scope(tenant_id, context)},
        {"$set": {"name": data.name, "description": data.description,
                  "rules": [r.model_dump() for r in data.rules],
                  "match": data.match, "updated_at": _now_iso()}})
    if not res.matched_count:
        raise HTTPException(404, "Segment not found")
    doc = await db.marketing_segments.find_one(
        {"id": segment_id, **_scope(tenant_id, context)}, {"_id": 0})
    return _seg_out(doc)


@admin_router.delete("/segments/{segment_id}")
async def admin_delete_segment(segment_id: str):
    tenant_id, context = _platform_ctx()
    db = get_db()
    res = await db.marketing_segments.delete_one(
        {"id": segment_id, **_scope(tenant_id, context)})
    if not res.deleted_count:
        raise HTTPException(404, "Segment not found")
    return {"deleted": segment_id}


@admin_router.post("/segments/{segment_id}/preview")
async def admin_preview_segment(segment_id: str):
    tenant_id, context = _platform_ctx()
    db = get_db()
    contacts = await _resolve_segment(db, tenant_id, context, segment_id)
    sample = [{"id": c.get("id"), "name": c.get("name") or c.get("business_name"),
               "email": c.get("email"), "phone": c.get("phone")} for c in contacts[:10]]
    return {"segment_id": segment_id, "count": len(contacts), "sample": sample}


@admin_router.post("/campaigns", response_model=CampaignOut)
async def admin_create_campaign(data: CampaignIn):
    tenant_id, context = _platform_ctx()
    db = get_db()
    doc = {"id": _uuid(), **_scope(tenant_id, context), "name": data.name,
           "channel": data.channel, "segment_id": data.segment_id,
           "sequence_id": data.sequence_id, "subject": data.subject, "body": data.body,
           "status": "draft", "stats": _new_stats(),
           "created_at": _now_iso(), "updated_at": _now_iso()}
    await db.marketing_campaigns.insert_one(doc)
    return _camp_out(doc)


@admin_router.get("/campaigns", response_model=list[CampaignOut])
async def admin_list_campaigns():
    tenant_id, context = _platform_ctx()
    db = get_db()
    docs = await db.marketing_campaigns.find(_scope(tenant_id, context), {"_id": 0}).to_list(500)
    return [_camp_out(d) for d in docs]


@admin_router.get("/campaigns/{campaign_id}", response_model=CampaignOut)
async def admin_get_campaign(campaign_id: str):
    tenant_id, context = _platform_ctx()
    db = get_db()
    return _camp_out(await _get_campaign(db, tenant_id, context, campaign_id))


@admin_router.put("/campaigns/{campaign_id}", response_model=CampaignOut)
async def admin_update_campaign(campaign_id: str, data: CampaignIn):
    tenant_id, context = _platform_ctx()
    db = get_db()
    camp = await _get_campaign(db, tenant_id, context, campaign_id)
    if camp.get("status") not in {"draft", "scheduled"}:
        raise HTTPException(400, "Only draft/scheduled campaigns can be edited")
    await db.marketing_campaigns.update_one(
        {"id": campaign_id, **_scope(tenant_id, context)},
        {"$set": {"name": data.name, "channel": data.channel,
                  "segment_id": data.segment_id, "sequence_id": data.sequence_id,
                  "subject": data.subject, "body": data.body, "updated_at": _now_iso()}})
    return _camp_out(await _get_campaign(db, tenant_id, context, campaign_id))


@admin_router.delete("/campaigns/{campaign_id}")
async def admin_delete_campaign(campaign_id: str):
    tenant_id, context = _platform_ctx()
    db = get_db()
    res = await db.marketing_campaigns.delete_one(
        {"id": campaign_id, **_scope(tenant_id, context)})
    if not res.deleted_count:
        raise HTTPException(404, "Campaign not found")
    return {"deleted": campaign_id}


@admin_router.post("/campaigns/{campaign_id}/test-send")
async def admin_test_send_campaign(campaign_id: str, data: TestSendIn):
    tenant_id, context = _platform_ctx()
    db = get_db()
    return await _test_send(db, tenant_id=tenant_id, context=context,
                            campaign_id=campaign_id, to=data.to)


@admin_router.post("/campaigns/{campaign_id}/send")
async def admin_send_campaign(campaign_id: str):
    tenant_id, context = _platform_ctx()
    db = get_db()
    return await _send_campaign(db, tenant_id=tenant_id, context=context,
                                campaign_id=campaign_id)


@admin_router.get("/campaigns/{campaign_id}/stats")
async def admin_campaign_stats(campaign_id: str):
    tenant_id, context = _platform_ctx()
    db = get_db()
    return await _campaign_stats(db, tenant_id, context, campaign_id)


@admin_router.post("/sequences", response_model=SequenceOut)
async def admin_create_sequence(data: SequenceIn):
    tenant_id, context = _platform_ctx()
    db = get_db()
    steps = [dict(s.model_dump(), order=i) for i, s in enumerate(data.steps)]
    doc = {"id": _uuid(), **_scope(tenant_id, context), "name": data.name,
           "description": data.description, "steps": steps,
           "created_at": _now_iso(), "updated_at": _now_iso()}
    await db.marketing_sequences.insert_one(doc)
    return _seq_out(doc)


@admin_router.get("/sequences", response_model=list[SequenceOut])
async def admin_list_sequences():
    tenant_id, context = _platform_ctx()
    db = get_db()
    docs = await db.marketing_sequences.find(_scope(tenant_id, context), {"_id": 0}).to_list(500)
    return [_seq_out(d) for d in docs]


@admin_router.get("/sequences/{sequence_id}", response_model=SequenceOut)
async def admin_get_sequence(sequence_id: str):
    tenant_id, context = _platform_ctx()
    db = get_db()
    doc = await db.marketing_sequences.find_one(
        {"id": sequence_id, **_scope(tenant_id, context)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Sequence not found")
    return _seq_out(doc)


@admin_router.put("/sequences/{sequence_id}", response_model=SequenceOut)
async def admin_update_sequence(sequence_id: str, data: SequenceIn):
    tenant_id, context = _platform_ctx()
    db = get_db()
    steps = [dict(s.model_dump(), order=i) for i, s in enumerate(data.steps)]
    res = await db.marketing_sequences.update_one(
        {"id": sequence_id, **_scope(tenant_id, context)},
        {"$set": {"name": data.name, "description": data.description,
                  "steps": steps, "updated_at": _now_iso()}})
    if not res.matched_count:
        raise HTTPException(404, "Sequence not found")
    doc = await db.marketing_sequences.find_one(
        {"id": sequence_id, **_scope(tenant_id, context)}, {"_id": 0})
    return _seq_out(doc)


@admin_router.delete("/sequences/{sequence_id}")
async def admin_delete_sequence(sequence_id: str):
    tenant_id, context = _platform_ctx()
    db = get_db()
    res = await db.marketing_sequences.delete_one(
        {"id": sequence_id, **_scope(tenant_id, context)})
    if not res.deleted_count:
        raise HTTPException(404, "Sequence not found")
    return {"deleted": sequence_id}


@admin_router.post("/sequences/{sequence_id}/enroll")
async def admin_enroll_sequence(sequence_id: str, data: EnrollIn):
    tenant_id, context = _platform_ctx()
    db = get_db()
    created = await _enroll_contacts(db, tenant_id=tenant_id, context=context,
                                     sequence_id=sequence_id,
                                     segment_id=data.segment_id,
                                     contact_ids=data.contact_ids or None)
    return {"sequence_id": sequence_id, "enrolled": created}


@admin_router.post("/sequences/process-due")
async def admin_process_due_sequences():
    tenant_id, context = _platform_ctx()
    db = get_db()
    return await _process_due(db, tenant_id=tenant_id, context=context)


@admin_router.get("/suppressions")
async def admin_list_suppressions():
    """Platform-wide suppression list (opt-outs across all platform campaigns)."""
    tenant_id, context = _platform_ctx()
    db = get_db()
    docs = await db.marketing_suppressions.find(
        _scope(tenant_id, context), {"_id": 0}).to_list(5000)
    return {"count": len(docs), "suppressions": docs}
