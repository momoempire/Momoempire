"""Conversation intelligence + sentiment escalation — LAYER 2 of the
customer-service excellence initiative: Momo acting as customer service
for the shops' customers.

What it does: reads customer conversation threads from
inbox_threads/inbox_messages (falls back to conversations/conv_messages —
a compatible read, since no inbox.py exists yet), scores every customer
message with keyword/template-based sentiment (no LLM), builds a
thread-level sentiment trend, and fires escalation rules:
- N negative customer messages in a row (default 2),
- churn language ("cancel", "refund", "lawyer", ...),
- negative ratio in recent messages,
- a single severely negative message.

Auto-escalation flags the thread urgent, stubs an owner notification, and
optionally opens a support ticket.

Collections (all tenant-scoped):
- `sentiment_scores`: one doc per (tenant_id, thread_id) with per-message
  scores and the thread-level rollup.
- `escalations`: one doc per triggered rule per thread; status open/resolved.
- `escalation_rules`: the tenant's rule set, seeded with defaults on first read.
- `support_tickets`: auto-created from escalations when the rule asks for it.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin

router = APIRouter(prefix="/api/conversation-intel", tags=["conversation-intel"])


# ---------- sentiment lexicons (template-based, no LLM) ----------

POSITIVE_WORDS: List[str] = [
    "thank", "thanks", "great", "awesome", "excellent", "amazing", "perfect",
    "wonderful", "fantastic", "outstanding", "superb", "brilliant", "happy",
    "glad", "pleased", "love", "appreciate", "appreciated", "impressed",
    "best", "helpful", "prompt", "quick", "fast", "professional", "friendly",
    "courteous", "kind", "polite", "recommend", "satisfied", "lifesaver",
    "top notch", "very happy", "very pleased", "worked great", "great job",
    "five star", "5 star", "highly recommend",
]

NEGATIVE_WORDS: List[str] = [
    "terrible", "awful", "horrible", "worst", "hate", "angry", "furious",
    "disappointed", "disappointing", "frustrated", "frustrating", "poor",
    "bad", "rude", "unprofessional", "slow", "late", "messed up", "screw up",
    "screwed", "broken", "damaged", "scam", "rip off", "ripoff", "lied",
    "lying", "incompetent", "useless", "pathetic", "joke", "shambles",
    "disaster", "nightmare", "gross", "disgusting", "unacceptable",
    "outrageous", "ridiculous", "waste of money", "waste of time",
    "overcharged", "overcharge", "double charged", "wrong", "incorrect",
    "missing", "no show", "no-show", "ghosted", "ignored", "never showed",
    "worst ever", "stay away", "avoid", "dreadful", "shoddy", "sloppy",
]

# Churn/cancellation language — always treated as negative AND escalates.
CHURN_WORDS: List[str] = [
    "cancel", "cancelled", "canceling", "cancellation", "refund", "refunds",
    "chargeback", "charge back", "dispute", "lawyer", "attorney", "bbb",
    "small claims", "never again", "take my business", "switching to",
    "going elsewhere", "last straw", "done with", "losing my business",
    "lost my business",
]


def _compile(phrases: List[str]) -> List[Tuple[str, "re.Pattern"]]:
    return [(p, re.compile(r"\b" + re.escape(p) + r"\b", re.IGNORECASE)) for p in phrases]


_POS_RX = _compile(POSITIVE_WORDS)
_NEG_RX = _compile(NEGATIVE_WORDS)
_CHURN_RX = _compile(CHURN_WORDS)


def score_text(text: str) -> Dict[str, Any]:
    """Keyword/template sentiment score for one message. No LLM call.

    Returns {pos, neg, churn[], score, label}. Churn words count as
    negative signals and always force a negative label. Documented as
    naive: no negation handling, no sarcasm detection — a noisy first
    pass that escalation rules layer on top of.
    """
    t = text or ""
    pos = sum(1 for _, rx in _POS_RX if rx.search(t))
    neg = sum(1 for _, rx in _NEG_RX if rx.search(t))
    churn = [phrase for phrase, rx in _CHURN_RX if rx.search(t)]
    neg += len(churn)  # churn language is negative language too
    if neg > pos:
        label = "negative"
    elif pos > neg:
        label = "positive"
    else:
        label = "neutral"
    return {
        "pos": pos,
        "neg": neg,
        "churn": churn,
        "score": pos - neg,
        "label": label,
    }


# ---------- escalation rule defaults ----------

def _default_rules(tenant_id: str) -> List[Dict[str, Any]]:
    now = _now_iso()
    return [
        {
            "id": _uuid(), "tenant_id": tenant_id,
            "key": "consecutive_negatives",
            "name": "Negative messages in a row",
            "description": "Fire when a customer sends N negative messages back-to-back.",
            "enabled": True, "severity": "high", "auto_ticket": True,
            "params": {"count": 2},
            "created_at": now, "updated_at": now,
        },
        {
            "id": _uuid(), "tenant_id": tenant_id,
            "key": "churn_keyword",
            "name": "Churn / cancellation language",
            "description": "Fire when a customer message contains churn language (cancel, refund, lawyer, ...).",
            "enabled": True, "severity": "critical", "auto_ticket": True,
            "params": {"words": list(CHURN_WORDS)},
            "created_at": now, "updated_at": now,
        },
        {
            "id": _uuid(), "tenant_id": tenant_id,
            "key": "negative_ratio",
            "name": "Negative ratio in recent messages",
            "description": "Fire when at least `ratio` of the last `window` customer messages are negative.",
            "enabled": True, "severity": "medium", "auto_ticket": False,
            "params": {"window": 5, "min_messages": 3, "ratio": 0.6},
            "created_at": now, "updated_at": now,
        },
        {
            "id": _uuid(), "tenant_id": tenant_id,
            "key": "severe_message",
            "name": "Severely negative single message",
            "description": "Fire when one customer message contains at least `neg_hits` negative phrases.",
            "enabled": True, "severity": "high", "auto_ticket": True,
            "params": {"neg_hits": 3},
            "created_at": now, "updated_at": now,
        },
    ]


async def _get_rules(db, tenant_id: str) -> List[Dict[str, Any]]:
    """Tenant rule set, seeded with defaults on first read."""
    rules = await db.escalation_rules.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(100)
    have = {r.get("key") for r in rules}
    for default in _default_rules(tenant_id):
        if default["key"] not in have:
            await db.escalation_rules.insert_one(default)
            rules.append({k: v for k, v in default.items() if k != "_id"})
    return rules


# ---------- models ----------

class RuleIn(BaseModel):
    key: str = Field(min_length=1)
    name: str = ""
    description: str = ""
    enabled: bool = True
    severity: str = Field(default="medium", pattern="^(critical|high|medium|low)$")
    auto_ticket: bool = False
    params: Dict[str, Any] = Field(default_factory=dict)


class RulesIn(BaseModel):
    rules: List[RuleIn] = Field(min_length=1)


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


async def _load_thread(db, tenant_id: str, thread_id: str) -> Tuple[Optional[Dict], List[Dict], Optional[str]]:
    """Load a thread + normalized customer/business messages.

    Primary shape: inbox_threads / inbox_messages. Falls back to
    conversations / conv_messages (the AI-receptionist shape) as a
    compatible read. Returns (thread, messages, source).
    """
    thread = await db.inbox_threads.find_one({"tenant_id": tenant_id, "id": thread_id})
    if thread:
        raw = await db.inbox_messages.find(
            {"tenant_id": tenant_id, "thread_id": thread_id}, {"_id": 0}
        ).sort("created_at", 1).to_list(500)
        msgs = [
            {
                "id": m.get("id"),
                "role": "customer" if str(m.get("direction", "inbound")).lower()
                in {"inbound", "incoming", "in", "customer"} else "business",
                "text": m.get("body") or m.get("text") or "",
                "created_at": m.get("created_at", ""),
            }
            for m in raw
        ]
        return thread, msgs, "inbox"

    thread = await db.conversations.find_one({"tenant_id": tenant_id, "id": thread_id})
    if thread:
        raw = await db.conv_messages.find(
            {"tenant_id": tenant_id, "conversation_id": thread_id}, {"_id": 0}
        ).sort("created_at", 1).to_list(500)
        msgs = [
            {
                "id": m.get("id"),
                "role": "customer" if m.get("role") == "caller" else "business",
                "text": m.get("content") or "",
                "created_at": m.get("created_at", ""),
            }
            for m in raw
        ]
        return thread, msgs, "conversations"

    return None, [], None


def _analyze_customer_messages(msgs: List[Dict]) -> Dict[str, Any]:
    """Score customer messages (chronological) and build the thread rollup."""
    customer = [m for m in msgs if m["role"] == "customer"]
    scored = []
    for m in customer:
        s = score_text(m["text"])
        scored.append({
            "message_id": m.get("id"),
            "created_at": m.get("created_at", ""),
            "excerpt": (m["text"] or "")[:160],
            **s,
        })

    labels = [s["label"] for s in scored]
    run = max_run = 0
    for lab in labels:
        run = run + 1 if lab == "negative" else 0
        max_run = max(max_run, run)

    pos = sum(1 for lab in labels if lab == "positive")
    neu = sum(1 for lab in labels if lab == "neutral")
    neg = sum(1 for lab in labels if lab == "negative")
    churn_hits = sum(1 for s in scored if s["churn"])
    avg = round(sum(s["score"] for s in scored) / len(scored), 3) if scored else 0.0

    if neg >= pos and neg >= neu and neg > 0:
        overall = "negative"
    elif pos >= neu and pos > 0:
        overall = "positive"
    else:
        overall = "neutral"

    return {
        "messages": scored,
        "counts": {"positive": pos, "neutral": neu, "negative": neg},
        "max_consecutive_negatives": max_run,
        "churn_hits": churn_hits,
        "avg_score": avg,
        "overall_label": overall,
    }


def _evaluate_rules(analysis: Dict, rules: List[Dict]) -> List[Dict[str, Any]]:
    """Return the triggered rules with human-readable reasons."""
    scored = analysis["messages"]
    n = len(scored)
    triggered: List[Dict[str, Any]] = []
    for rule in rules:
        if not rule.get("enabled"):
            continue
        key = rule.get("key")
        params = rule.get("params") or {}
        reason = ""
        if key == "consecutive_negatives":
            need = int(params.get("count", 2))
            if analysis["max_consecutive_negatives"] >= need and need > 0:
                reason = (f"{analysis['max_consecutive_negatives']} negative customer "
                          f"message(s) in a row (threshold {need})")
        elif key == "churn_keyword":
            words = params.get("words") or CHURN_WORDS
            hits = [s for s in scored if any(
                re.search(r"\b" + re.escape(w) + r"\b", s["excerpt"] or "", re.IGNORECASE)
                for w in words)]
            if hits:
                found = sorted({w for s in hits for w in s["churn"]})
                reason = f"Churn language detected: {', '.join(found)}"
        elif key == "negative_ratio":
            window = int(params.get("window", 5))
            min_msgs = int(params.get("min_messages", 3))
            ratio = float(params.get("ratio", 0.6))
            tail = scored[-window:] if window > 0 else scored
            if len(tail) >= min_msgs:
                neg_in_tail = sum(1 for s in tail if s["label"] == "negative")
                if len(tail) and neg_in_tail / len(tail) >= ratio:
                    reason = (f"{neg_in_tail}/{len(tail)} of the last {len(tail)} customer "
                              f"messages negative (threshold {ratio:.0%})")
        elif key == "severe_message":
            need = int(params.get("neg_hits", 3))
            worst = max((s for s in scored), key=lambda s: s["neg"], default=None)
            if worst and worst["neg"] >= need:
                reason = (f"Severely negative message ({worst['neg']} negative phrases, "
                          f"threshold {need}): \"{(worst['excerpt'] or '')[:80]}\"")
        if reason:
            triggered.append({**rule, "reason": reason})
    return triggered


async def _flag_urgent(db, tenant_id: str, thread_id: str, source: str, urgent: bool) -> None:
    coll = db.inbox_threads if source == "inbox" else db.conversations
    await coll.update_one(
        {"tenant_id": tenant_id, "id": thread_id},
        {"$set": {"urgent": urgent, "updated_at": _now_iso()}},
    )


# ---------- analyze ----------

@router.post("/analyze/thread/{thread_id}")
async def analyze_thread(thread_id: str, user: dict = Depends(require_tenant_user)):
    """Score a thread's customer messages, store the result, and fire any
    triggered escalation rules: the thread is flagged urgent, the owner is
    notified (stubbed), and a support ticket is opened when the rule asks.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    thread, msgs, source = await _load_thread(db, tenant_id, thread_id)
    if thread is None:
        raise HTTPException(404, "Thread not found")

    analysis = _analyze_customer_messages(msgs)
    now = _now_iso()
    doc = {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "thread_id": thread_id,
        "source": source,
        "customer_name": thread.get("customer_name") or thread.get("name") or "",
        "channel": thread.get("channel") or "",
        "overall_label": analysis["overall_label"],
        "avg_score": analysis["avg_score"],
        "counts": analysis["counts"],
        "max_consecutive_negatives": analysis["max_consecutive_negatives"],
        "churn_hits": analysis["churn_hits"],
        "message_scores": analysis["messages"],
        "message_count": len(msgs),
        "customer_message_count": len(analysis["messages"]),
        "created_at": now,
        "analyzed_at": now,
    }
    existing = await db.sentiment_scores.find_one({"tenant_id": tenant_id, "thread_id": thread_id})
    if existing:
        await db.sentiment_scores.update_one(
            {"tenant_id": tenant_id, "thread_id": thread_id}, {"$set": doc})
    else:
        await db.sentiment_scores.insert_one(doc)

    rules = await _get_rules(db, tenant_id)
    triggered = _evaluate_rules(analysis, rules)

    escalations: List[Dict[str, Any]] = []
    for rule in triggered:
        dup = await db.escalations.find_one({
            "tenant_id": tenant_id, "thread_id": thread_id,
            "rule_key": rule.get("key"), "status": "open"})
        if dup:
            escalations.append({**dup, "created": False})
            continue
        esc = {
            "id": _uuid(),
            "tenant_id": tenant_id,
            "thread_id": thread_id,
            "rule_key": rule.get("key"),
            "rule_name": rule.get("name") or rule.get("key"),
            "reason": rule["reason"],
            "severity": rule.get("severity", "medium"),
            "status": "open",
            "customer_name": doc["customer_name"],
            "channel": doc["channel"],
            "message_excerpt": next(
                (s["excerpt"] for s in reversed(analysis["messages"])
                 if s["label"] == "negative"), ""),
            "created_at": now,
            "resolved_at": None,
            "resolved_by": None,
        }
        await db.escalations.insert_one(esc)
        escalations.append({**esc, "created": True})

        # TODO(LIVE): page the owner via SMS/push (Twilio + push provider).
        print(f"[CONVERSATION-INTEL escalation {esc['severity']}] tenant={tenant_id} "
              f"thread={thread_id} rule={esc['rule_key']} reason={esc['reason']} "
              f"(owner notification stubbed)")

        if rule.get("auto_ticket"):
            await db.support_tickets.insert_one({
                "id": _uuid(),
                "tenant_id": tenant_id,
                "thread_id": thread_id,
                "escalation_id": esc["id"],
                "title": f"[{esc['severity'].upper()}] {esc['rule_name']} — {doc['customer_name'] or thread_id}",
                "body": esc["reason"],
                "status": "open",
                "created_at": now,
            })

    thread_flagged_urgent = False
    if escalations:
        await _flag_urgent(db, tenant_id, thread_id, source, True)
        thread_flagged_urgent = True

    for esc in escalations:
        esc.pop("_id", None)
    doc.pop("_id", None)
    return {
        "ok": True,
        "thread_id": thread_id,
        "source": source,
        "message_count": len(msgs),
        "customer_message_count": len(analysis["messages"]),
        "overall_label": analysis["overall_label"],
        "avg_score": analysis["avg_score"],
        "counts": analysis["counts"],
        "max_consecutive_negatives": analysis["max_consecutive_negatives"],
        "churn_hits": analysis["churn_hits"],
        "escalations": escalations,
        "thread_flagged_urgent": thread_flagged_urgent,
    }


# ---------- sentiment overview ----------

@router.get("/sentiment/overview")
async def sentiment_overview(user: dict = Depends(require_tenant_user)):
    """Sentiment distribution + 4-week trend across analyzed threads, plus
    open-escalation counts. This is the dashboard's health-at-a-glance."""
    db = get_db()
    tenant_id = user["tenant_id"]
    docs = await db.sentiment_scores.find(
        {"tenant_id": tenant_id}, {"_id": 0}).sort("analyzed_at", -1).to_list(2000)

    distribution = {"positive": 0, "neutral": 0, "negative": 0}
    for d in docs:
        lab = d.get("overall_label", "neutral")
        if lab in distribution:
            distribution[lab] += 1
    total = sum(distribution.values())
    avg_score = round(sum(d.get("avg_score", 0) for d in docs) / total, 3) if total else 0.0

    now = datetime.now(timezone.utc)
    trend = []
    for w in range(4):
        start = now - timedelta(days=7 * (w + 1))
        end = now - timedelta(days=7 * w)
        bucket = {"week_start": start.date().isoformat(),
                  "positive": 0, "neutral": 0, "negative": 0, "threads": 0}
        for d in docs:
            dt = _parse_iso(d.get("analyzed_at") or d.get("created_at", ""))
            if dt and start <= dt < end:
                lab = d.get("overall_label", "neutral")
                if lab in bucket:
                    bucket[lab] += 1
                bucket["threads"] += 1
        trend.append(bucket)
    trend.reverse()

    open_esc = await db.escalations.find(
        {"tenant_id": tenant_id, "status": "open"}, {"_id": 0}).to_list(10000)
    by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for e in open_esc:
        sev = e.get("severity", "medium")
        if sev in by_severity:
            by_severity[sev] += 1

    return {
        "threads_analyzed": total,
        "distribution": distribution,
        "avg_score": avg_score,
        "sentiment_trend": trend,
        "open_escalations": len(open_esc),
        "open_by_severity": by_severity,
    }


# ---------- thread timeline ----------

@router.get("/threads/{thread_id}/timeline")
async def thread_timeline(thread_id: str, user: dict = Depends(require_tenant_user)):
    """Per-conversation sentiment timeline: every customer message scored,
    in order, with the thread rollup."""
    db = get_db()
    tenant_id = user["tenant_id"]
    doc = await db.sentiment_scores.find_one(
        {"tenant_id": tenant_id, "thread_id": thread_id}, {"_id": 0})
    if not doc:
        raise HTTPException(
            404, f"No sentiment analysis for thread {thread_id} yet — "
                 "POST /conversation-intel/analyze/thread/{id} first.")
    return {"thread_id": thread_id, "analysis": doc}


# ---------- escalations queue ----------

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@router.get("/escalations")
async def list_escalations(
    status: str = "open",
    severity: Optional[str] = None,
    user: dict = Depends(require_tenant_user),
):
    """Escalation queue. ?status=open|resolved|all, optional ?severity=critical|high|medium|low."""
    db = get_db()
    q: Dict[str, Any] = {"tenant_id": user["tenant_id"]}
    if status != "all":
        q["status"] = status
    if severity:
        q["severity"] = severity
    docs = await db.escalations.find(q, {"_id": 0}).sort("created_at", -1).to_list(1000)
    docs.sort(key=lambda e: (_SEVERITY_RANK.get(e.get("severity", "medium"), 2),
                             str(e.get("created_at", ""))), reverse=False)
    # Keep newest first within the same severity.
    out: List[Dict[str, Any]] = []
    for sev in sorted(_SEVERITY_RANK, key=_SEVERITY_RANK.get):
        group = [d for d in docs if d.get("severity", "medium") == sev]
        out.extend(sorted(group, key=lambda d: str(d.get("created_at", "")), reverse=True))
    return {"escalations": out, "count": len(out), "status": status}


@router.post("/escalations/{escalation_id}/resolve")
async def resolve_escalation(escalation_id: str, user: dict = Depends(require_tenant_owner_or_admin)):
    """Owner resolves an escalation. When no open escalations remain for the
    thread, its urgent flag is cleared."""
    db = get_db()
    tenant_id = user["tenant_id"]
    esc = await db.escalations.find_one({"tenant_id": tenant_id, "id": escalation_id})
    if not esc:
        raise HTTPException(404, "Escalation not found")
    if esc.get("status") == "resolved":
        return {"ok": True, "already": True, "id": escalation_id}

    now = _now_iso()
    await db.escalations.update_one(
        {"tenant_id": tenant_id, "id": escalation_id},
        {"$set": {"status": "resolved", "resolved_at": now,
                  "resolved_by": user.get("id")}},
    )
    remaining = await db.escalations.find(
        {"tenant_id": tenant_id, "thread_id": esc.get("thread_id"), "status": "open"},
        {"_id": 0, "id": 1},
    ).to_list(100)
    cleared_urgent = False
    if not remaining:
        await db.inbox_threads.update_one(
            {"tenant_id": tenant_id, "id": esc.get("thread_id")},
            {"$set": {"urgent": False, "updated_at": now}},
        )
        await db.conversations.update_one(
            {"tenant_id": tenant_id, "id": esc.get("thread_id")},
            {"$set": {"urgent": False, "updated_at": now}},
        )
        cleared_urgent = True
    return {"ok": True, "id": escalation_id, "resolved": True,
            "cleared_urgent": cleared_urgent}


# ---------- rules ----------

@router.get("/rules")
async def list_rules(user: dict = Depends(require_tenant_user)):
    """List the tenant's escalation rules (seeded with defaults on first read)."""
    db = get_db()
    rules = await _get_rules(db, user["tenant_id"])
    return {"rules": [{k: v for k, v in r.items() if k != "_id"} for r in rules],
            "count": len(rules)}


@router.put("/rules")
async def update_rules(data: RulesIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Owner replaces/updates escalation rules. Upserts by rule key; defaults
    not mentioned here are seeded, not overwritten."""
    db = get_db()
    tenant_id = user["tenant_id"]
    now = _now_iso()
    for rule in data.rules:
        doc = {
            "tenant_id": tenant_id,
            "key": rule.key,
            "name": rule.name or rule.key,
            "description": rule.description,
            "enabled": rule.enabled,
            "severity": rule.severity,
            "auto_ticket": rule.auto_ticket,
            "params": rule.params,
            "updated_at": now,
        }
        existing = await db.escalation_rules.find_one({"tenant_id": tenant_id, "key": rule.key})
        if existing:
            await db.escalation_rules.update_one(
                {"tenant_id": tenant_id, "key": rule.key}, {"$set": doc})
        else:
            doc["id"] = _uuid()
            doc["created_at"] = now
            await db.escalation_rules.insert_one(doc)
    rules = await _get_rules(db, tenant_id)
    return {"ok": True,
            "rules": [{k: v for k, v in r.items() if k != "_id"} for r in rules],
            "count": len(rules)}
