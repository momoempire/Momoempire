"""Public demo + waitlist: no-auth endpoints used by the marketing homepage.

- POST /api/public/demo/start → opens a 10-turn demo session with a mock tenant
  shaped by a chosen industry preset. No DB tenant touched.
- POST /api/public/demo/turn → caller utterance → AI reply. Rate-limited by IP.
- POST /api/public/waitlist → capture email/name/business/interest for early access.
"""
import os
import hashlib
import httpx
import time
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr
from typing import Optional
from db import get_db
from models import _uuid, _now_iso
from ai_receptionist import receptionist_reply

router = APIRouter(prefix="/public", tags=["marketing"])

# In-memory demo sessions: {session_id: {industry, history:[{role,content}], created_at, turns}}
_SESSIONS: dict[str, dict] = {}
_IP_RATE: dict[str, list[float]] = {}
_MAX_TURNS = 10
_RATE_WINDOW = 60.0
_RATE_LIMIT = 20


def _ip_ok(request: Request) -> bool:
    ip = (request.client.host if request.client else "unknown") or "unknown"
    now = time.time()
    hits = [t for t in _IP_RATE.get(ip, []) if now - t < _RATE_WINDOW]
    if len(hits) >= _RATE_LIMIT:
        return False
    hits.append(now)
    _IP_RATE[ip] = hits
    return True


INDUSTRY_PRESETS = {
    "hvac": {
        "name": "Comfort Pros HVAC",
        "industry_slug": "hvac",
        "ai_employee": {"name": "Alex", "greeting": "Hi, Comfort Pros HVAC — how can I help?"},
        "greeting_es": "Hola, Comfort Pros HVAC — ¿cómo le puedo ayudar?",
        "services": [
            {"name": "AC repair", "price": 189, "duration_minutes": 90},
            {"name": "Furnace tune-up", "price": 129, "duration_minutes": 60},
            {"name": "AC install estimate", "price": 0, "duration_minutes": 45},
        ],
        "knowledge": [
            {"question": "Hours", "answer": "Mon-Sat 7am-7pm. Emergency 24/7."},
            {"question": "Service area", "answer": "Austin and surrounding 30 miles."},
        ],
    },
    "dental": {
        "name": "Bright Smiles Dental",
        "industry_slug": "dental",
        "ai_employee": {"name": "Mia", "greeting": "Thanks for calling Bright Smiles Dental, how can I help?"},
        "greeting_es": "Gracias por llamar a Bright Smiles Dental, ¿cómo le puedo ayudar?",
        "services": [
            {"name": "Cleaning", "price": 120, "duration_minutes": 45},
            {"name": "New patient exam", "price": 95, "duration_minutes": 60},
            {"name": "Teeth whitening", "price": 299, "duration_minutes": 60},
        ],
        "knowledge": [
            {"question": "Insurance", "answer": "We accept Delta Dental, Cigna, Aetna, and most PPOs."},
            {"question": "New patient?", "answer": "Welcome! First visit includes exam, x-rays, and cleaning."},
        ],
    },
    "legal": {
        "name": "Harbor Law Offices",
        "industry_slug": "legal",
        "ai_employee": {"name": "Jordan", "greeting": "Harbor Law Offices — this is Jordan, how can I help?"},
        "greeting_es": "Harbor Law Offices — habla Jordan, ¿en qué le puedo ayudar?",
        "services": [
            {"name": "Initial consult", "price": 0, "duration_minutes": 30},
            {"name": "Estate planning package", "price": 1500, "duration_minutes": 90},
        ],
        "knowledge": [
            {"question": "Practice areas", "answer": "Estate planning, small business formation, real estate."},
            {"question": "Free consult?", "answer": "First 30 minutes are complimentary."},
        ],
    },
    "salon": {
        "name": "Lumen Hair Studio",
        "industry_slug": "salon",
        "ai_employee": {"name": "Sam", "greeting": "Lumen Hair Studio — how can I help you glow today?"},
        "greeting_es": "Lumen Hair Studio — ¿cómo le ayudamos a brillar hoy?",
        "services": [
            {"name": "Women's cut & style", "price": 85, "duration_minutes": 60},
            {"name": "Balayage", "price": 220, "duration_minutes": 180},
            {"name": "Deep conditioning", "price": 45, "duration_minutes": 30},
        ],
        "knowledge": [
            {"question": "First-timer?", "answer": "Welcome! Mention it when booking and we'll give you 10% off."},
            {"question": "Walk-ins?", "answer": "Yes, when stylists have openings — booking recommended."},
        ],
    },
}


class DemoStartIn(BaseModel):
    industry: str = "hvac"
    lang: str = "en"


class DemoTurnIn(BaseModel):
    session_id: str
    text: str


@router.post("/demo/start")
async def demo_start(data: DemoStartIn, request: Request):
    if not _ip_ok(request):
        raise HTTPException(429, "Too many demo sessions — slow down a bit.")
    preset = INDUSTRY_PRESETS.get((data.industry or "hvac").lower()) or INDUSTRY_PRESETS["hvac"]
    sid = _uuid()
    lang = (data.lang or "en").lower()
    greeting = preset.get("greeting_es") if lang.startswith("es") else preset["ai_employee"]["greeting"]
    _SESSIONS[sid] = {
        "preset": preset,
        "lang": lang,
        "history": [{"role": "ai", "content": greeting}],
        "turns": 0,
        "created_at": time.time(),
    }
    # garbage collect old sessions (>30min)
    now = time.time()
    for k in list(_SESSIONS.keys()):
        if now - _SESSIONS[k]["created_at"] > 1800:
            _SESSIONS.pop(k, None)
    return {"session_id": sid, "greeting": greeting, "business": preset["name"],
            "ai_name": preset["ai_employee"]["name"], "max_turns": _MAX_TURNS, "lang": lang}


@router.post("/demo/turn")
async def demo_turn(data: DemoTurnIn, request: Request):
    if not _ip_ok(request):
        raise HTTPException(429, "Slow down a bit.")
    sess = _SESSIONS.get(data.session_id)
    if not sess:
        raise HTTPException(404, "Demo session expired — start a new one.")
    is_es = (sess.get("lang") or "en").startswith("es")
    if sess["turns"] >= _MAX_TURNS:
        return {"reply": "Fin de la demo. Comience la prueba gratis para seguir conversando — su oficina AI real le espera." if is_es else
                         "That's a wrap on the demo. Start a free trial to keep chatting — your real AI office waits.", "ended": True}
    text = (data.text or "").strip()
    if not text:
        raise HTTPException(400, "text required")
    sess["history"].append({"role": "caller", "content": text})
    try:
        ai = await receptionist_reply(
            tenant={"name": sess["preset"]["name"], "industry_slug": sess["preset"]["industry_slug"],
                    "ai_employee": sess["preset"]["ai_employee"]},
            industry=None,
            services=sess["preset"]["services"],
            knowledge=sess["preset"]["knowledge"],
            history=sess["history"][-20:],
            caller_utterance=text,
            upsells=[],
            lang=sess.get("lang", "en"),
        )
    except Exception as e:
        return {"reply": f"Hmm, I had a glitch there — try again. ({e})", "ended": False}
    reply = ai.get("reply") or ("Perdón, ¿podría repetir?" if is_es else "Sorry, could you repeat that?")
    sess["history"].append({"role": "ai", "content": reply})
    sess["turns"] += 1
    return {"reply": reply, "turns_used": sess["turns"], "turns_left": _MAX_TURNS - sess["turns"], "ended": False}




def _public_voice_demo_enabled() -> bool:
    return (os.environ.get("PUBLIC_VOICE_DEMO_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}


def _voice_demo_max_seconds() -> int:
    try:
        return max(15, min(300, int(os.environ.get("PUBLIC_VOICE_DEMO_MAX_SECONDS") or "60")))
    except ValueError:
        return 60


def _voice_instructions(preset: dict, lang: str) -> str:
    """Ground the realtime voice session in the same industry preset as the text demo."""
    is_es = (lang or "en").lower().startswith("es")
    biz = preset["name"]
    ai = preset["ai_employee"]["name"]
    services = ", ".join(
        f"{s['name']}" + (f" (${s['price']})" if s.get("price") else "")
        for s in (preset.get("services") or [])[:6]
    )
    knowledge = "; ".join(
        f"{k.get('question')}: {k.get('answer')}" for k in (preset.get("knowledge") or [])[:6]
    )
    if is_es:
        return (
            f"Eres {ai}, recepcionista AI de {biz}. Habla en español latinoamericano, claro y breve "
            f"(1–3 oraciones). Ayuda a agendar, cotizar y responder preguntas frecuentes. "
            f"Servicios: {services}. Conocimiento: {knowledge}. "
            f"No inventes precios fuera de la lista. Si no sabes, ofrece transferir a un humano. "
            f"Esta es una demo corta de marketing — sé amable y termina con una invitación a probar el plan gratis."
        )
    return (
        f"You are {ai}, the AI receptionist for {biz}. Speak clearly and briefly (1–3 sentences). "
        f"Help with booking, quotes, and FAQs. Services: {services}. Knowledge: {knowledge}. "
        f"Do not invent prices outside the list. If unsure, offer to hand off to a human. "
        f"This is a short marketing demo — be warm and end by inviting them to start a free trial."
    )


class DemoVoiceTokenIn(BaseModel):
    industry: str = "hvac"
    lang: str = "en"


@router.post("/demo/voice-token")
async def demo_voice_token(data: DemoVoiceTokenIn, request: Request):
    """Mint a short-lived OpenAI Realtime ephemeral client secret for the public WebRTC demo.

    OFF unless PUBLIC_VOICE_DEMO_ENABLED=true AND OPENAI_API_KEY is set.
    Never returns the real API key — only an ephemeral `ek_…` client secret.
    """
    if not _ip_ok(request):
        raise HTTPException(429, "Too many voice demo requests — slow down a bit.")

    if not _public_voice_demo_enabled():
        return {
            "available": False,
            "reason": "PUBLIC_VOICE_DEMO_ENABLED is off",
            "fallback": "web-speech",
        }

    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return {
            "available": False,
            "reason": "OPENAI_API_KEY not configured",
            "fallback": "web-speech",
        }

    industry = (data.industry or "hvac").lower()
    preset = INDUSTRY_PRESETS.get(industry) or INDUSTRY_PRESETS["hvac"]
    lang = (data.lang or "en").lower()
    model = (os.environ.get("PUBLIC_VOICE_DEMO_MODEL") or os.environ.get("OPENAI_REALTIME_MODEL") or "gpt-realtime").strip()
    voice = (os.environ.get("PUBLIC_VOICE_DEMO_VOICE") or os.environ.get("OPENAI_REALTIME_VOICE") or "alloy").strip()
    max_seconds = _voice_demo_max_seconds()
    # Secret TTL slightly longer than session cap so clients can connect, then hang up by timer.
    secret_ttl = max(60, min(600, max_seconds + 60))
    instructions = _voice_instructions(preset, lang)

    # Privacy-preserving safety identifier (hashed IP + industry), never the API key.
    ip = (request.client.host if request.client else "unknown") or "unknown"
    safety_id = hashlib.sha256(f"voice-demo:{ip}:{industry}".encode("utf-8")).hexdigest()[:32]

    payload = {
        "expires_after": {"anchor": "created_at", "seconds": secret_ttl},
        "session": {
            "type": "realtime",
            "model": model,
            "instructions": instructions,
            "audio": {"output": {"voice": voice}},
        },
    }

    try:
        async with httpx.AsyncClient(timeout=20) as hc:
            r = await hc.post(
                "https://api.openai.com/v1/realtime/client_secrets",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "OpenAI-Safety-Identifier": safety_id,
                },
                json=payload,
            )
        if r.status_code >= 400:
            return {
                "available": False,
                "reason": (r.text or "")[:200],
                "fallback": "web-speech",
            }
        data_out = r.json() or {}
    except Exception as e:
        return {"available": False, "reason": str(e)[:200], "fallback": "web-speech"}

    # OpenAI returns { value: "ek_…", expires_at: … } (and optionally nested session).
    ephemeral = data_out.get("value") or (data_out.get("client_secret") or {}).get("value")
    expires_at = data_out.get("expires_at") or (data_out.get("client_secret") or {}).get("expires_at")
    if not ephemeral or not str(ephemeral).startswith("ek_"):
        # Refuse anything that looks like a long-lived sk_ key
        return {
            "available": False,
            "reason": "Ephemeral client secret missing from provider response",
            "fallback": "web-speech",
        }
    if str(ephemeral).startswith("sk_"):
        return {
            "available": False,
            "reason": "Refusing to return a non-ephemeral key",
            "fallback": "web-speech",
        }

    return {
        "available": True,
        "value": ephemeral,
        "expires_at": expires_at,
        "model": model,
        "voice": voice,
        "max_duration_seconds": max_seconds,
        "webrtc_url": "https://api.openai.com/v1/realtime/calls",
        "business": preset["name"],
        "ai_name": preset["ai_employee"]["name"],
        "industry": industry,
        "lang": lang,
        "fallback": "web-speech",
    }

# ---------- Waitlist ----------
class WaitlistIn(BaseModel):
    email: EmailStr
    name: Optional[str] = ""
    business_name: Optional[str] = ""
    industry: Optional[str] = ""
    note: Optional[str] = ""


@router.post("/waitlist")
async def waitlist(data: WaitlistIn, request: Request):
    if not _ip_ok(request):
        raise HTTPException(429, "slow down")
    db = get_db()
    existing = await db.waitlist.find_one({"email": data.email.lower()})
    if existing:
        return {"ok": True, "status": "already-on-list"}
    doc = {
        "id": _uuid(), "email": data.email.lower(), "name": data.name or "",
        "business_name": data.business_name or "", "industry": data.industry or "",
        "note": data.note or "", "source": "landing",
        "ip": request.client.host if request.client else "",
        "created_at": _now_iso(),
    }
    await db.waitlist.insert_one(doc)
    # Fire-and-forget confirmation email
    try:
        from services.email import send_email, _frame
        html = _frame(
            "<p style='font-size:20px;margin:4px 0 10px'>You're on the list ✨</p>"
            "<p style='color:#555'>Thanks for raising your hand. We'll reach out as soon as your industry seat opens up. "
            "In the meantime, you can also try the free trial anytime at <a href='https://www.aioffice.io/pricing'>our pricing page</a>.</p>",
            app_name="AI Office",
        )
        await send_email(to=data.email, subject="You're on the AI Office waitlist", html=html)
    except Exception as e:
        print(f"[waitlist email] {e}")
    return {"ok": True, "status": "added"}
