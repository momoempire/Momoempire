"""AI call coaching for CSRs: score inbound calls, flag missed bookings.

Deterministic keyword-rubric scorer over transcript text. This is a mock
scorer — production intent is an LLM rubric scorer plugged into
`_score_transcript` (see TODO(LIVE)). The request/response schema stays the
same; only the scorer function changes.

Rubric (each 0-100):
- greeting        — did the CSR open with a proper greeting?
- needs_discovery — did the CSR ask diagnostic questions about the problem?
- same_day_offer  — did the CSR offer same-day/today service or at least
                    propose a concrete booking window?
- close           — did the CSR attempt to close the booking?
- empathy         — did the CSR acknowledge the caller's frustration/urgency?

A call counts as a missed booking when the transcript shows the caller
deferring ("I'll call back", "let me think about it", "just looking", ...)
with no booking confirmation.

Tenants are fully isolated: every read/write is scoped to the caller's
tenant_id. Zero live network calls.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from db import get_db
from security import require_tenant_user

router = APIRouter(prefix="/call-coaching", tags=["call-coaching"])

# ---------- pydantic models (local to this router; models.py untouched) ----------

RUBRIC_FIELDS = ("greeting", "needs_discovery", "same_day_offer", "close", "empathy")


class RubricScores(BaseModel):
    greeting: int = Field(0, ge=0, le=100)
    needs_discovery: int = Field(0, ge=0, le=100)
    same_day_offer: int = Field(0, ge=0, le=100)
    close: int = Field(0, ge=0, le=100)
    empathy: int = Field(0, ge=0, le=100)


class ScoreCallRequest(BaseModel):
    transcript: str = Field(..., min_length=1, max_length=20000)
    csr_name: str = Field("", max_length=120)
    occurred_at: Optional[str] = Field(
        None, description="ISO-8601 timestamp of the call; defaults to now")


class CallScoreOut(BaseModel):
    id: str
    tenant_id: str
    call_id: str
    csr_name: str
    transcript_summary: str
    rubric: RubricScores
    overall: int
    booked: bool
    missed_booking: bool
    missed_booking_reason: str
    occurred_at: str
    scored_at: str


class CallScoreDetail(CallScoreOut):
    transcript: str


# ---------- deterministic mock scorer ----------

_GREETING_PATTERNS = [
    r"thanks? for calling",
    r"good (morning|afternoon|evening)",
    r"how (can|may) i help",
    r"\bhello\b",
    r"\bhi\b",
]

_DISCOVERY_KEYWORDS = [
    "what's going on", "what is going on", "when did", "how long",
    "what kind", "what model", "how old", "where is it", "tell me about",
    "is it making", "does it still", "have you tried",
]

_SAME_DAY_PATTERNS = [
    "same day", "today", "this afternoon", "this morning", "right away",
    "as soon as possible", "asap", "emergency", "i can get someone out",
    "tech in the area", "before the end of the day",
]

_BOOKING_PATTERNS = [
    "appointment", "schedule", "book you", "booked", "you're all set",
    "all set", "we'll be there", "see you", "between 2 and 4",
]

_CLOSE_PATTERNS = [
    "shall i book", "let's get you scheduled", "does that work",
    "would you like me to", "can i get you on the schedule",
    "want me to book", "i'll get you booked", "let me get you on the",
]

_EMPATHY_PATTERNS = [
    "sorry", "understand", "frustrat", "appreciate", "that sounds",
    "i hear", "makes sense", "don't worry", "no worries",
    "we'll take care", "i get it",
]

_DEFER_PATTERNS = [
    "call back", "call you back", "think about it", "let me check",
    "just looking", "not right now", "maybe later", "no thanks",
    "can't afford", "too expensive", "i'll pass", "have to ask",
]


def _hits(text: str, patterns: List[str]) -> int:
    return sum(1 for p in patterns if re.search(p, text, re.IGNORECASE))


def _score_transcript(transcript: str):
    """Score a call transcript deterministically.

    TODO(LIVE): replace this keyword rubric with an LLM scorer (e.g. a
    structured-output prompt returning the same five rubric scores plus
    booked/missed_booking). Everything downstream — the endpoint contract,
    the weekly insights math, the frontend — stays unchanged.
    """
    text = transcript or ""
    lower = text.lower()

    # --- greeting ---
    greeting_hits = _hits(text, _GREETING_PATTERNS)
    if _hits(text, [_GREETING_PATTERNS[0]]) > 0:
        greeting = 95          # "thanks for calling ..."
    elif greeting_hits >= 2:
        greeting = 85
    elif greeting_hits == 1:
        greeting = 70
    else:
        greeting = 20

    # --- needs discovery ---
    n_questions = text.count("?")
    n_disc = min(_hits(text, _DISCOVERY_KEYWORDS), 3)
    needs_discovery = min(95, 25 + 20 * min(n_questions, 2) + 10 * n_disc)
    if n_questions == 0 and n_disc == 0:
        needs_discovery = 20

    # --- same-day offer ---
    n_same = _hits(text, _SAME_DAY_PATTERNS)
    n_book = _hits(text, _BOOKING_PATTERNS)
    if n_same >= 2:
        same_day_offer = 95
    elif n_same == 1:
        same_day_offer = 85
    elif n_book >= 2:
        same_day_offer = 65
    elif n_book == 1:
        same_day_offer = 50
    else:
        same_day_offer = 20

    # --- close ---
    n_close = _hits(text, _CLOSE_PATTERNS)
    if n_close >= 2:
        close = 95
    elif n_close == 1:
        close = 85
    elif n_book >= 1:
        close = 55
    else:
        close = 25

    # --- empathy ---
    n_emp = _hits(text, _EMPATHY_PATTERNS)
    if n_emp >= 2:
        empathy = 90
    elif n_emp == 1:
        empathy = 70
    else:
        empathy = 40

    rubric = RubricScores(
        greeting=greeting,
        needs_discovery=needs_discovery,
        same_day_offer=same_day_offer,
        close=close,
        empathy=empathy,
    )
    overall = int(round(
        greeting * 0.15
        + needs_discovery * 0.25
        + same_day_offer * 0.25
        + close * 0.25
        + empathy * 0.10
    ))
    overall = max(0, min(100, overall))

    # --- outcome: booked vs missed ---
    n_defer = _hits(text, _DEFER_PATTERNS)
    confirm = _hits(text, ["you're all set", "all set", "booked", "see you then",
                           "we'll be there", "appointment confirmed", "confirmed"]) > 0
    booked = bool(n_book > 0 and n_defer == 0) or confirm
    missed_booking = bool(n_defer > 0 and not booked)

    reason = ""
    if missed_booking:
        if same_day_offer <= 50:
            reason = "caller ended without booking — no same-day offer was made"
        elif close <= 55:
            reason = "caller ended without booking — no close attempt"
        elif needs_discovery <= 45:
            reason = "caller ended without booking — weak needs discovery"
        else:
            reason = "caller deferred without booking"
    return rubric, overall, booked, missed_booking, reason


def _summary(transcript: str, limit: int = 140) -> str:
    s = " ".join(transcript.split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _doc_to_out(doc: dict, include_transcript: bool = False) -> dict:
    base = {
        "id": doc["id"],
        "tenant_id": doc["tenant_id"],
        "call_id": doc["call_id"],
        "csr_name": doc.get("csr_name", ""),
        "transcript_summary": doc.get("transcript_summary", ""),
        "rubric": doc["rubric"],
        "overall": doc["overall"],
        "booked": doc.get("booked", False),
        "missed_booking": doc.get("missed_booking", False),
        "missed_booking_reason": doc.get("missed_booking_reason", ""),
        "occurred_at": doc["occurred_at"],
        "scored_at": doc["scored_at"],
    }
    if include_transcript:
        base["transcript"] = doc.get("transcript", "")
    return base


def _sort_key(doc: dict) -> str:
    return doc.get("occurred_at", "") or doc.get("scored_at", "")


# ---------- endpoints ----------

@router.post("/calls/{call_id}/score")
async def score_call(
    call_id: str,
    req: ScoreCallRequest,
    user: dict = Depends(require_tenant_user),
) -> dict:
    """Score one inbound call from its transcript. Idempotent per
    (tenant, call_id): re-scoring replaces the previous score."""
    db = get_db()
    tenant_id = user["tenant_id"]

    rubric, overall, booked, missed_booking, reason = _score_transcript(req.transcript)

    now = datetime.now(timezone.utc)
    occurred = _parse_iso(req.occurred_at) or now
    doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "call_id": call_id,
        "csr_name": (req.csr_name or "").strip(),
        "transcript": req.transcript,
        "transcript_summary": _summary(req.transcript),
        "rubric": rubric.model_dump(),
        "overall": overall,
        "booked": booked,
        "missed_booking": missed_booking,
        "missed_booking_reason": reason,
        "occurred_at": occurred.isoformat(),
        "scored_at": now.isoformat(),
    }
    # Idempotent upsert on (tenant_id, call_id) — manual, no upsert flag.
    existing = await db.call_scores.find_one(
        {"tenant_id": tenant_id, "call_id": call_id}, {"_id": 0})
    if existing:
        doc["id"] = existing["id"]
        await db.call_scores.update_one(
            {"tenant_id": tenant_id, "call_id": call_id}, {"$set": doc})
    else:
        await db.call_scores.insert_one(doc)
    return _doc_to_out(doc)


@router.get("/calls")
async def list_calls(
    date_from: Optional[str] = Query(None, description="ISO date, inclusive"),
    date_to: Optional[str] = Query(None, description="ISO date, inclusive"),
    csr: Optional[str] = Query(None, description="Filter by CSR name"),
    user: dict = Depends(require_tenant_user),
) -> dict:
    """Scored calls for this tenant, newest first. Filterable by date
    range and CSR."""
    db = get_db()
    tenant_id = user["tenant_id"]

    query: dict = {"tenant_id": tenant_id}
    if csr:
        query["csr_name"] = csr.strip()
    docs = await db.call_scores.find(query, {"_id": 0}).to_list(5000)

    from_dt = _parse_iso(date_from)
    to_dt = _parse_iso(date_to)
    filtered = []
    for d in docs:
        occ = _parse_iso(d.get("occurred_at"))
        if from_dt and (occ is None or occ < from_dt):
            continue
        if to_dt and (occ is None or occ > to_dt):
            continue
        filtered.append(d)
    filtered.sort(key=_sort_key, reverse=True)
    return {
        "calls": [_doc_to_out(d) for d in filtered],
        "count": len(filtered),
    }


@router.get("/calls/{call_id}")
async def get_call(
    call_id: str,
    user: dict = Depends(require_tenant_user),
) -> dict:
    """One scored call, with the full transcript and rubric breakdown."""
    db = get_db()
    doc = await db.call_scores.find_one(
        {"tenant_id": user["tenant_id"], "call_id": call_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Call score not found")
    return _doc_to_out(doc, include_transcript=True)


@router.get("/insights/weekly")
async def weekly_insights(
    user: dict = Depends(require_tenant_user),
) -> dict:
    """Aggregate digest for the last 7 days: bookings lost, and the top
    coaching opportunity per CSR."""
    db = get_db()
    tenant_id = user["tenant_id"]

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    docs = await db.call_scores.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(5000)
    week = [d for d in docs
            if (_parse_iso(d.get("occurred_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]

    total = len(week)
    avg_overall = round(sum(d["overall"] for d in week) / total, 1) if total else 0.0
    missed = [d for d in week if d.get("missed_booking")]
    missed_count = len(missed)

    # Headline digest, e.g. "You lost 14 bookings this week — nobody offered same-day."
    if missed_count:
        same_day_misses = sum(
            1 for d in missed
            if d.get("missed_booking_reason", "").startswith("caller ended without booking — no same-day"))
        if same_day_misses and same_day_misses == missed_count:
            headline = (f"You lost {missed_count} booking{'s' if missed_count != 1 else ''} "
                        "this week — nobody offered same-day.")
        elif same_day_misses:
            headline = (f"You lost {missed_count} booking{'s' if missed_count != 1 else ''} "
                        f"this week — {same_day_misses} had no same-day offer.")
        else:
            headline = (f"You lost {missed_count} booking{'s' if missed_count != 1 else ''} "
                        "this week — callers deferred without booking.")
    elif total:
        headline = f"No bookings lost this week across {total} scored calls. Nice."
    else:
        headline = "No scored calls this week yet."

    # Per-CSR coaching opportunities: weakest rubric on average.
    by_csr: Dict[str, list] = {}
    for d in week:
        by_csr.setdefault(d.get("csr_name") or "Unassigned", []).append(d)

    tips = {
        "greeting": "Open every call with a warm greeting — 'Thanks for calling …'",
        "needs_discovery": "Ask 2–3 diagnostic questions before quoting anything.",
        "same_day_offer": "Always offer same-day service before quoting next week.",
        "close": "End with a direct close: 'Shall I book you for today?'",
        "empathy": "Acknowledge the caller's frustration before jumping to fixes.",
    }
    opportunities = []
    for csr_name, calls in sorted(by_csr.items()):
        rub_avgs = {
            f: sum(c["rubric"][f] for c in calls) / len(calls)
            for f in RUBRIC_FIELDS
        }
        weakest = min(rub_avgs, key=lambda f: rub_avgs[f])
        opportunities.append({
            "csr_name": csr_name,
            "calls": len(calls),
            "avg_score": round(sum(c["overall"] for c in calls) / len(calls), 1),
            "missed_bookings": sum(1 for c in calls if c.get("missed_booking")),
            "weakest_rubric": weakest,
            "weakest_rubric_avg": round(rub_avgs[weakest], 1),
            "coaching_tip": tips[weakest],
        })
    opportunities.sort(key=lambda o: (-o["missed_bookings"], o["weakest_rubric_avg"]))

    return {
        "period_days": 7,
        "total_calls": total,
        "avg_overall": avg_overall,
        "missed_bookings": missed_count,
        "booked": sum(1 for d in week if d.get("booked")),
        "headline": headline,
        "opportunities": opportunities,
    }
