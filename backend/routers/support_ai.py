"""AI support agent + shop health scoring with churn prediction — LAYER 1 of the
customer-service excellence initiative (Momo supporting its shops).

What it does:
(a) Platform-trained AI support: a tenant asks a question, the agent retrieves
    relevant articles from the EXISTING tenant knowledge base
    (`knowledge_chunks` written by routers/knowledge_docs.py — keyword match,
    template-based drafting, no LLM required) and drafts an answer with cited
    sources. If confidence is below the escalation threshold, it opens a human
    ticket in the `support_tickets` collection instead. Every AI answer is
    logged for QA sampling (thumbs up/down feedback).
(b) Shop health scoring: per-tenant 0-100 score from usage breadth, login /
    activity recency, ticket sentiment trend, and payment health.
(c) Churn prediction: rule-based risk tiers (low/watch/high) from score drops,
    support-ticket spikes, and plan-downgrade signals. High-risk shops get a
    proactive save playbook.

Collections (all tenant-scoped):
- `knowledge_chunks`: READ-ONLY here — the existing KB from knowledge_docs.py
  ({id, tenant_id, doc_id, doc_title, chunk_idx, content}).
- `support_answers`: one doc per /ask call — question, drafted answer, cited
  sources, confidence, escalated flag, asked_by.
- `support_feedback`: one doc per (answer_id, created_by) — thumbs up/down QA.
- `support_tickets`: human tickets created on escalation, in the canonical
  helpdesk shape routers/support_tickets.py creates (subject, description,
  priority in low|medium|high|urgent, status lifecycle fields) plus the
  AI-escalation provenance (source, question, confidence, answer_id).
  (Wave 8 wrote priority "normal" — not a valid helpdesk priority — and
  omitted the lifecycle fields the SLA snapshot reads; fixed in
  wave/service-excellence-plus.)
- `shop_health_scores`: one snapshot doc per health computation
  ({id, tenant_id, score, factors, plan_id, computed_at}).
- `churn_flags`: one unacknowledged flag doc per (tenant, tier)
  ({id, tenant_id, tier, reasons, playbook, acknowledged, created_at,
  updated_at}).

TODO(LIVE): swap `_draft_answer` (deterministic template) for an LLM call
grounded on the retrieved chunks; stamp `users.last_login_at` on login in
routers/auth.py (login-recency factor currently falls back to usage activity);
have routers/plans.py log plan changes so downgrade detection is explicit
rather than inferred from consecutive health snapshots.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin

router = APIRouter(prefix="/api/support-ai", tags=["support-ai"])

ESCALATE_BELOW = 0.45      # confidence < this -> open a human ticket
MIN_MATCHED_TERMS = 2      # need at least this many question terms in a chunk
RETRIEVE_LIMIT = 500

METERED = ["ai_minutes", "calls", "sms", "ai_interactions", "voice_seconds"]
PLAN_RANK = {"trial": 0, "lite": 1, "starter": 2, "pro": 3, "empire": 4}

USAGE_W, LOGIN_W, TICKET_W, PAYMENT_W = 35, 20, 20, 25


# ---------- input models ----------

class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class FeedbackIn(BaseModel):
    helpful: bool
    note: str = Field(default="", max_length=500)


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


def _terms(q: str) -> List[str]:
    return [t.lower() for t in re.findall(r"\w+", q or "") if len(t) > 2]


def _excerpt(content: str, n: int = 220) -> str:
    c = (content or "").strip()
    return c if len(c) <= n else c[:n].rstrip() + "..."


async def _retrieve_chunks(db, tenant_id: str, question: str, top_k: int = 3) -> tuple[List[Dict], float]:
    """Keyword retrieval over the tenant's existing `knowledge_chunks`.

    Returns (top chunks with scores, confidence of the best chunk).
    Confidence = term coverage of the question in the best chunk.
    """
    terms = _terms(question)
    if not terms:
        return [], 0.0
    rows = await db.knowledge_chunks.find(
        {"tenant_id": tenant_id}, {"_id": 0}).to_list(RETRIEVE_LIMIT)
    scored = []
    term_set = set(terms)
    for row in rows:
        content = (row.get("content") or "").lower()
        matched = {t for t in term_set if t in content}
        coverage = len(matched) / len(term_set)
        if matched:
            scored.append({**row, "_matched": len(matched), "_coverage": coverage})
    scored.sort(key=lambda r: (r["_coverage"], r["_matched"]), reverse=True)
    top = [s for s in scored if s["_matched"] >= MIN_MATCHED_TERMS][:top_k]
    confidence = round(min(0.95, scored[0]["_coverage"]), 2) if scored else 0.0
    return top, confidence


def _draft_answer(question: str, chunks: List[Dict], business_name: str) -> str:
    """Deterministic template draft grounded on retrieved chunks.

    TODO(LIVE): replace with an LLM answer grounded on these same chunks.
    """
    lines = [
        f"Based on {business_name}'s knowledge base, here's what I found:",
        "",
    ]
    for i, c in enumerate(chunks, 1):
        lines.append(f"{i}. {c.get('doc_title') or 'Untitled'}: {_excerpt(c.get('content', ''))}")
    lines += [
        "",
        "This answer was drafted by Momo's support agent (template v1) from your "
        "shop's knowledge base. If it misses the mark, give it a thumbs down or "
        "ask me to open a human ticket.",
    ]
    return "\n".join(lines)


def _escalation_answer(ticket_id: str) -> str:
    return (
        "I couldn't find a confident answer in your shop's knowledge base, so I've "
        f"opened a human support ticket for you (ref {ticket_id}). Our support team "
        "will follow up shortly — nothing else you need to do."
    )


async def _open_ticket(db, tenant_id: str, question: str, confidence: float,
                       answer_id: str, asked_by: Optional[str]) -> Dict:
    """Write an escalation ticket in the canonical `support_tickets` shape
    (same fields routers/support_tickets.py's create path writes, so the
    helpdesk queue and SLA timers treat it like any other ticket)."""
    now = _now_iso()
    ticket = {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "subject": (question[:80] + "...") if len(question) > 80 else question,
        "description": question,
        "status": "open",
        "priority": "medium",
        "category": "ai-escalation",
        "assignee": None,
        "contact_name": "",
        "contact_email": "",
        "contact_phone": "",
        "created_by": asked_by,
        "status_changed_at": now,
        "waiting_seconds": 0.0,
        "resolved_at": None,
        "source": "ai-support-escalation",
        "question": question,
        "confidence": confidence,
        "answer_id": answer_id,
        "asked_by": asked_by,
        "assigned_to": None,
        "created_at": now,
        "updated_at": now,
    }
    await db.support_tickets.insert_one(ticket)
    # TODO(LIVE): notify the on-call support agent (SMS/email/Slack) here.
    print(f"[SUPPORT-AI escalation] ticket={ticket['id']} tenant={tenant_id} "
          f"confidence={confidence} (stubbed notify)")
    ticket.pop("_id", None)
    return ticket


# ---------- health scoring ----------

async def _usage_breadth(db, tenant_id: str, now: datetime) -> Dict:
    """35 pts: distinct metered metrics with usage in the last 30 days."""
    cutoff = now - timedelta(days=30)
    rows = await db.usage_events.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(10000)
    active = {
        r.get("metric") for r in rows
        if r.get("metric") in METERED
        and (r.get("value") or 0) > 0
        and (_parse_iso(r.get("created_at", "")) or cutoff) >= cutoff
    }
    score = min(USAGE_W, round(USAGE_W * len(active) / len(METERED)))
    return {"score": score, "max": USAGE_W,
            "detail": {"metrics_active": sorted(active), "lookback_days": 30}}


async def _login_recency(db, tenant_id: str, now: datetime) -> Dict:
    """20 pts: most recent login / platform activity.

    Login signal: users.last_login_at (or users.updated_at when present).
    Falls back to the most recent usage event — activity implies a session.
    TODO(LIVE): routers/auth.py should stamp users.last_login_at on login.
    """
    last: Optional[datetime] = None
    source = "none"
    users = await db.users.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(500)
    for u in users:
        for key in ("last_login_at", "updated_at"):
            dt = _parse_iso(u.get(key, ""))
            if dt and (last is None or dt > last):
                last = dt
                source = f"users.{key}"
    if last is None:
        rows = await db.usage_events.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(10000)
        for r in rows:
            dt = _parse_iso(r.get("created_at", ""))
            if dt and (last is None or dt > last):
                last = dt
                source = "usage_events"
    if last is None:
        return {"score": 0, "max": LOGIN_W,
                "detail": {"days_ago": None, "source": source,
                           "note": "no login or activity signal"}}
    days = (now - last).days
    if days <= 2:
        score = LOGIN_W
    elif days <= 7:
        score = 14
    elif days <= 14:
        score = 8
    elif days <= 30:
        score = 4
    else:
        score = 0
    return {"score": score, "max": LOGIN_W,
            "detail": {"days_ago": days, "source": source}}


async def _ticket_sentiment(db, tenant_id: str, now: datetime) -> Dict:
    """20 pts: open support tickets and negative AI-answer feedback, last 30d."""
    cutoff = now - timedelta(days=30)
    tickets = await db.support_tickets.find(
        {"tenant_id": tenant_id}, {"_id": 0}).to_list(5000)
    recent = [t for t in tickets
              if (_parse_iso(t.get("created_at", "")) or cutoff) >= cutoff]
    open_count = sum(1 for t in recent if t.get("status") in {"open", "escalated", "pending"})
    feedback = await db.support_feedback.find(
        {"tenant_id": tenant_id}, {"_id": 0}).to_list(5000)
    recent_fb = [f for f in feedback
                 if (_parse_iso(f.get("created_at", "")) or cutoff) >= cutoff]
    down = sum(1 for f in recent_fb if f.get("helpful") is False)
    down_rate = (down / len(recent_fb)) if recent_fb else 0.0

    score = TICKET_W - min(12, 3 * open_count) - (6 if down_rate > 0.3 and recent_fb else 0)
    score = max(0, score)
    detail = {"open_tickets_30d": open_count, "thumbs_down_rate_30d": round(down_rate, 2)}
    if not recent and not recent_fb:
        detail["note"] = "no support signals — healthy"
    return {"score": score, "max": TICKET_W, "detail": detail}


async def _payment_health(db, tenant_id: str) -> Dict:
    """25 pts: subscription status of the tenant."""
    t = await db.tenants.find_one({"id": tenant_id},
                                  {"_id": 0, "subscription_status": 1, "plan_id": 1}) or {}
    status = t.get("subscription_status") or "trial"
    mapping = {"active": 25, "trial": 18, "past_due": 8, "canceled": 0}
    return {"score": mapping.get(status, 0), "max": PAYMENT_W,
            "detail": {"subscription_status": status, "plan_id": t.get("plan_id") or "trial"}}


async def _compute_health(db, tenant_id: str, persist: bool = True) -> Dict:
    now = datetime.now(timezone.utc)
    factors = {
        "usage_breadth": await _usage_breadth(db, tenant_id, now),
        "login_recency": await _login_recency(db, tenant_id, now),
        "ticket_sentiment": await _ticket_sentiment(db, tenant_id, now),
        "payment_health": await _payment_health(db, tenant_id),
    }
    score = sum(f["score"] for f in factors.values())
    plan_id = factors["payment_health"]["detail"]["plan_id"]
    result = {"tenant_id": tenant_id, "score": score, "max_score": 100,
              "factors": factors, "plan_id": plan_id,
              "computed_at": _now_iso()}
    if persist:
        doc = {"id": _uuid(), "tenant_id": tenant_id, "score": score,
               "factors": factors, "plan_id": plan_id,
               "computed_at": result["computed_at"]}
        await db.shop_health_scores.insert_one(doc)
    return result


async def _health_history(db, tenant_id: str, limit: int = 30) -> List[Dict]:
    rows = await db.shop_health_scores.find(
        {"tenant_id": tenant_id}, {"_id": 0}).to_list(limit)
    rows.sort(key=lambda r: str(r.get("computed_at", "")), reverse=True)
    return rows[:limit]


# ---------- churn prediction ----------

def _churn_playbook(tier: str) -> List[str]:
    if tier == "high":
        return [
            "Call the owner within 24 hours — personal outreach beats automation at this risk level.",
            "Resolve or post a status update on every open support ticket today.",
            "Run a 15-minute walkthrough of the features they've stopped using.",
            "Get approval for a retention offer (discount or a free month of a Books add-on).",
            "Schedule a 7-day check-in and re-run the health score.",
        ]
    if tier == "watch":
        return [
            "Send a proactive check-in message from the shop's success manager this week.",
            "Review open tickets and negative answer feedback with the support lead.",
            "Offer a free refresher training session on under-used features.",
        ]
    return [
        "Keep the monthly health report going — nothing to fix, everything to protect.",
    ]


async def _churn_assessment(db, tenant_id: str, health: Dict,
                           history: List[Dict]) -> Dict:
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    reasons: List[str] = []

    # Score drop vs the previous snapshot.
    drop = 0
    if history:
        drop = (history[0].get("score") or 0) - health["score"]
    if drop >= 20:
        reasons.append(f"score_drop: health fell {drop} pts since the last check")
    elif drop >= 10:
        reasons.append(f"score_drop: health fell {drop} pts since the last check")

    # Ticket spike: 3+ tickets opened in the last 7 days.
    tickets = await db.support_tickets.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(5000)
    recent_7d = sum(1 for t in tickets
                    if (_parse_iso(t.get("created_at", "")) or week_ago) >= week_ago)
    open_count = sum(1 for t in tickets if t.get("status") in {"open", "escalated", "pending"})
    spike = recent_7d >= 3
    if spike:
        reasons.append(f"ticket_spike: {recent_7d} support tickets opened in the last 7 days")

    # Plan-downgrade signal: consecutive snapshots show a lower-ranked plan, or an
    # explicit downgrade flag on the tenant doc.
    tenant = await db.tenants.find_one({"id": tenant_id},
                                       {"_id": 0, "plan_id": 1, "subscription_status": 1,
                                        "plan_downgraded_at": 1, "downgrade_requested": 1}) or {}
    downgraded = bool(tenant.get("downgrade_requested") or tenant.get("plan_downgraded_at"))
    if history and history[0].get("plan_id"):
        prev_rank = PLAN_RANK.get(str(history[0].get("plan_id")), 0)
        cur_rank = PLAN_RANK.get(str(health.get("plan_id")), 0)
        if cur_rank < prev_rank:
            downgraded = True
    if downgraded:
        reasons.append("plan_downgrade: shop moved to a lower plan (or requested one)")

    # Sentiment: majority of recent AI-answer feedback is thumbs down.
    feedback = await db.support_feedback.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(5000)
    recent_fb = [f for f in feedback
                 if (_parse_iso(f.get("created_at", "")) or month_ago) >= month_ago]
    down_rate = (sum(1 for f in recent_fb if f.get("helpful") is False) / len(recent_fb)) \
        if recent_fb else 0.0
    if recent_fb and down_rate >= 0.5:
        reasons.append(f"sentiment_drop: {round(down_rate * 100)}% thumbs down on recent AI answers")

    status = (tenant.get("subscription_status") or "trial")
    score = health["score"]

    # Tier rules.
    if score < 40 or status in {"past_due", "canceled"} \
            or (drop >= 20 and spike) or (downgraded and score < 60):
        tier = "high"
    elif score < 60 or drop >= 10 or spike or downgraded \
            or (recent_fb and down_rate >= 0.5):
        tier = "watch"
    else:
        tier = "low"

    assessment = {"tenant_id": tenant_id, "tier": tier, "reasons": reasons,
                  "signals": {"score": score, "score_drop": drop,
                              "tickets_7d": recent_7d, "open_tickets": open_count,
                              "plan_downgraded": downgraded,
                              "thumbs_down_rate_30d": round(down_rate, 2)},
                  "playbook": _churn_playbook(tier),
                  "assessed_at": _now_iso()}

    # Persist an unacknowledged flag for watch/high (dedupe per tenant+tier).
    flag_id = None
    if tier != "low":
        existing = await db.churn_flags.find_one(
            {"tenant_id": tenant_id, "tier": tier, "acknowledged": False})
        if existing:
            flag_id = existing.get("id")
            await db.churn_flags.update_one(
                {"tenant_id": tenant_id, "id": flag_id},
                {"$set": {"reasons": reasons, "signals": assessment["signals"],
                          "updated_at": assessment["assessed_at"]}})
        else:
            flag_id = _uuid()
            await db.churn_flags.insert_one({
                "id": flag_id, "tenant_id": tenant_id, "tier": tier,
                "reasons": reasons, "signals": assessment["signals"],
                "playbook": assessment["playbook"],
                "acknowledged": False,
                "created_at": assessment["assessed_at"],
                "updated_at": assessment["assessed_at"],
            })
    assessment["flag_id"] = flag_id
    return assessment


# ---------- AI support endpoints ----------

@router.post("/ask")
async def ask(data: AskIn, user: dict = Depends(require_tenant_user)):
    """Tenant asks a support question.

    Retrieves relevant articles from the tenant's existing knowledge base
    (keyword match, no LLM), drafts a template-based answer with cited sources,
    and logs the answer for QA. Below the confidence threshold the question is
    escalated to a human ticket instead.
    """
    db = get_db()
    tenant_id = user["tenant_id"]

    chunks, confidence = await _retrieve_chunks(db, tenant_id, data.question)
    tenant = await db.tenants.find_one({"id": tenant_id}, {"_id": 0, "name": 1}) or {}
    business_name = tenant.get("name") or "your shop"

    escalated = not chunks or confidence < ESCALATE_BELOW
    sources = [{
        "doc_id": c.get("doc_id"),
        "doc_title": c.get("doc_title") or "Untitled",
        "chunk_idx": c.get("chunk_idx"),
        "excerpt": _excerpt(c.get("content", "")),
    } for c in chunks]

    answer_id = _uuid()
    ticket = None
    if escalated:
        ticket = await _open_ticket(db, tenant_id, data.question, confidence,
                                    answer_id, user.get("id"))
        answer_text = _escalation_answer(ticket["id"])
    else:
        answer_text = _draft_answer(data.question, chunks, business_name)
    # TODO(LIVE): upgrade drafting from templates to an LLM answer grounded on
    # the retrieved chunks (keep keyword retrieval as the offline fallback).

    doc = {
        "id": answer_id,
        "tenant_id": tenant_id,
        "question": data.question,
        "answer": answer_text,
        "sources": sources,
        "confidence": confidence,
        "escalated": escalated,
        "ticket_id": ticket["id"] if ticket else None,
        "asked_by": user.get("id"),
        "generated_by": "support-ai-template-v1",
        "created_at": _now_iso(),
    }
    await db.support_answers.insert_one(doc)
    doc.pop("_id", None)
    return {"ok": True, **doc}


@router.get("/answers")
async def list_answers(limit: int = 50, user: dict = Depends(require_tenant_owner_or_admin)):
    """QA log: recent AI answers with their feedback attached (owner/admin)."""
    db = get_db()
    answers = await db.support_answers.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}).to_list(limit)
    feedback = await db.support_feedback.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}).to_list(5000)
    latest: Dict[str, Dict] = {}
    for f in feedback:
        aid = f.get("answer_id")
        if aid and (aid not in latest or str(f.get("created_at", "")) > str(latest[aid].get("created_at", ""))):
            latest[aid] = f
    for a in answers:
        fb = latest.get(a.get("id"))
        a["feedback"] = ({"helpful": fb.get("helpful"), "note": fb.get("note", ""),
                          "created_at": fb.get("created_at")} if fb else None)
    answers.sort(key=lambda a: str(a.get("created_at", "")), reverse=True)
    return {"answers": answers[:limit], "count": min(len(answers), limit)}


@router.post("/answers/{answer_id}/feedback")
async def give_feedback(answer_id: str, data: FeedbackIn,
                        user: dict = Depends(require_tenant_user)):
    """Thumbs up/down QA feedback on an AI answer (one vote per user, updatable)."""
    db = get_db()
    answer = await db.support_answers.find_one(
        {"tenant_id": user["tenant_id"], "id": answer_id})
    if not answer:
        raise HTTPException(404, "Answer not found")

    existing = await db.support_feedback.find_one(
        {"tenant_id": user["tenant_id"], "answer_id": answer_id,
         "created_by": user.get("id")})
    now = _now_iso()
    if existing:
        await db.support_feedback.update_one(
            {"tenant_id": user["tenant_id"], "id": existing["id"]},
            {"$set": {"helpful": data.helpful, "note": data.note, "updated_at": now}})
        return {"ok": True, "updated": True, "answer_id": answer_id,
                "helpful": data.helpful}
    doc = {"id": _uuid(), "tenant_id": user["tenant_id"], "answer_id": answer_id,
           "helpful": data.helpful, "note": data.note,
           "created_by": user.get("id"), "created_at": now, "updated_at": now}
    await db.support_feedback.insert_one(doc)
    doc.pop("_id", None)
    return {"ok": True, "updated": False, **doc}


# ---------- health endpoints ----------

@router.get("/health")
async def get_health(user: dict = Depends(require_tenant_user)):
    """Current shop health score (0-100) with the factor breakdown.

    Factors: usage breadth (35), login/activity recency (20), ticket sentiment
    trend (20), payment health (25). Persists a snapshot for history/churn.
    """
    db = get_db()
    return await _compute_health(db, user["tenant_id"], persist=True)


@router.get("/health/history")
async def get_health_history(limit: int = 30, user: dict = Depends(require_tenant_user)):
    """Recent health snapshots, newest first."""
    db = get_db()
    rows = await _health_history(db, user["tenant_id"], limit)
    return {"history": rows, "count": len(rows)}


# ---------- churn endpoint ----------

@router.get("/churn")
async def get_churn(user: dict = Depends(require_tenant_owner_or_admin)):
    """Churn risk tier (low/watch/high) with reasons and a proactive save
    playbook. High-risk shops also get an unacknowledged churn flag written."""
    db = get_db()
    tenant_id = user["tenant_id"]
    history = await _health_history(db, tenant_id, 5)
    health = await _compute_health(db, tenant_id, persist=True)
    return await _churn_assessment(db, tenant_id, health, history)
