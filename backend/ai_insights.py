"""AI insights: lead scoring, CRM auto-fill, upsell matching.

All functions return structured JSON. Each prefers the AI key (AI_API_KEY)
when available but ships a deterministic keyword-based fallback so the UI
never breaks on quota / network failures.
"""
import os
import json
import re
from typing import Any, Dict, List, Optional


HOT_WORDS = [
    "today", "right now", "asap", "urgent", "emergency", "ready to book",
    "want to book", "book now", "schedule now", "sign me up", "credit card",
    "call me back", "when can you come",
]
WARM_WORDS = [
    "pricing", "quote", "cost", "estimate", "appointment", "schedule",
    "how much", "when could", "available", "openings",
]
COLD_WORDS = [
    "just looking", "window shopping", "checking prices", "comparing",
    "thinking about", "maybe later", "not sure", "wrong number",
]


def _rule_based_score(transcript: str) -> Dict[str, Any]:
    text = (transcript or "").lower()
    def _hits(words: List[str]) -> List[str]:
        return [w for w in words if w in text]
    hot_hits, warm_hits, cold_hits = _hits(HOT_WORDS), _hits(WARM_WORDS), _hits(COLD_WORDS)
    if hot_hits:
        label, reason = "hot", f"Caller signaled urgency / intent to book ({', '.join(hot_hits[:2])})."
        score = 85
    elif warm_hits and not cold_hits:
        label, reason = "warm", f"Discussed pricing or scheduling ({', '.join(warm_hits[:2])})."
        score = 60
    elif cold_hits and not warm_hits:
        label, reason = "cold", f"Low intent — {cold_hits[0]!r} language."
        score = 25
    else:
        label, reason = "warm", "No strong signal; defaulting to warm for follow-up."
        score = 45
    return {"label": label, "score": score, "reason": reason, "signals": hot_hits + warm_hits, "method": "rules"}


async def score_lead_from_transcript(transcript: str, caller_name: str = "", industry: str = "") -> Dict[str, Any]:
    """Returns {label: hot|warm|cold, score: 0-100, reason, signals[]}."""
    from llm_portable import ai_api_key
    key = ai_api_key()
    base = _rule_based_score(transcript)
    if not key or not transcript or len(transcript) < 20:
        return base
    try:
        from llm_portable import LlmChat, UserMessage
        system = (
            "You are a sales intelligence engine. Given a short call transcript, classify the lead as "
            "'hot', 'warm', or 'cold'. Also give an integer score 0-100 and a one-sentence human reason. "
            "Return ONLY valid JSON: {\"label\":..., \"score\":..., \"reason\":..., \"signals\":[...]}"
        )
        chat = LlmChat(api_key=key, session_id=f"score-{hash(transcript) & 0xffff}", system_message=system).with_model("openai", "gpt-6-sol")
        user = f"Industry: {industry or 'small business'}\nCaller: {caller_name or 'unknown'}\nTranscript:\n{transcript[:3000]}"
        raw = await chat.send_message(UserMessage(text=user))
        text = raw if isinstance(raw, str) else getattr(raw, "content", str(raw))
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return base
        parsed = json.loads(m.group(0))
        parsed.setdefault("method", "llm")
        if parsed.get("label") not in {"hot", "warm", "cold"}:
            return base
        return parsed
    except Exception:
        return base


ADDRESS_RE = re.compile(r"\b\d{1,5}\s+[A-Za-z][A-Za-z\s\.]{2,40}(?:Street|St|Ave|Avenue|Rd|Road|Blvd|Boulevard|Lane|Ln|Drive|Dr|Way|Court|Ct)\b", re.I)
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
EMAIL_RE = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")
URGENT_RE = re.compile(r"\b(emergency|urgent|asap|today|right now|flooding|leaking|no\s*heat|no\s*hot\s*water)\b", re.I)


def _rule_based_extract(transcript: str) -> Dict[str, Any]:
    addr = ADDRESS_RE.search(transcript or "")
    phone = PHONE_RE.search(transcript or "")
    email = EMAIL_RE.search(transcript or "")
    urgency = "high" if URGENT_RE.search(transcript or "") else "normal"
    service = ""
    for s in ["install", "repair", "replace", "inspection", "quote", "estimate", "maintenance", "service", "cleaning"]:
        if s in (transcript or "").lower():
            service = s.capitalize(); break
    return {
        "address": addr.group(0) if addr else "",
        "phone": phone.group(0) if phone else "",
        "email": email.group(0) if email else "",
        "service_requested": service,
        "urgency": urgency,
        "budget_hint": "",
        "preferred_time": "",
        "notes_summary": (transcript or "")[:280],
        "method": "rules",
    }


async def extract_crm_fields(transcript: str, industry: str = "") -> Dict[str, Any]:
    """LLM-powered field extraction. Falls back to regex rules."""
    from llm_portable import ai_api_key
    key = ai_api_key()
    base = _rule_based_extract(transcript)
    if not key or not transcript or len(transcript) < 20:
        return base
    try:
        from llm_portable import LlmChat, UserMessage
        system = (
            "You extract CRM fields from a service call transcript. "
            "Return ONLY valid JSON with keys: address, phone, email, service_requested, urgency "
            "(low/normal/high), budget_hint, preferred_time, notes_summary (max 240 chars). "
            "If unknown use empty string."
        )
        chat = LlmChat(api_key=key, session_id=f"extract-{hash(transcript) & 0xffff}", system_message=system).with_model("openai", "gpt-6-sol")
        raw = await chat.send_message(UserMessage(text=f"Industry: {industry}\nTranscript:\n{transcript[:3500]}"))
        text = raw if isinstance(raw, str) else getattr(raw, "content", str(raw))
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return base
        parsed = json.loads(m.group(0))
        # Keep rule-derived fields if LLM returned empty
        for k in ["address", "phone", "email", "service_requested", "urgency", "notes_summary"]:
            if not parsed.get(k) and base.get(k):
                parsed[k] = base[k]
        parsed.setdefault("method", "llm")
        return parsed
    except Exception:
        return base


def match_upsells(service_name: str, upsells: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Given a chosen service name and the tenant's upsell catalog, return matches."""
    if not service_name or not upsells:
        return []
    sn = service_name.lower()
    out = []
    for u in upsells:
        triggers = [t.lower() for t in (u.get("triggers") or [])]
        if any(t in sn or sn in t for t in triggers) or u.get("global"):
            out.append(u)
    return out
