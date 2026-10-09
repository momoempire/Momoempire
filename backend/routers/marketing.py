"""Public demo + waitlist: no-auth endpoints used by the marketing homepage.

- POST /api/public/demo/start → opens a 10-turn demo session with a mock tenant
  shaped by a chosen industry preset. No DB tenant touched.
- POST /api/public/demo/turn → caller utterance → AI reply. Rate-limited by IP.
- POST /api/public/waitlist → capture email/name/business/interest for early access.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError, field_validator
from typing import Optional
from db import get_db
from models import _uuid, _now_iso
from ai_receptionist import receptionist_reply

router = APIRouter(prefix="/public", tags=["marketing"])

# In-memory demo sessions: {session_id: {industry, history:[{role,content}], created_at, turns}}
_SESSIONS: dict[str, dict] = {}
_MAX_TURNS = 10
log = logging.getLogger("marketing")


class SlidingLimiter:
    """In-memory sliding-window limiter (per process). Each instance is its own bucket."""

    MAX_KEYS = 50_000

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window = limit, window_s
        self.hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        if len(self.hits) > self.MAX_KEYS:  # bound memory under key-spraying
            self.hits = {k: v for k, v in self.hits.items() if v and now - v[-1] < self.window}
        recent = [t for t in self.hits.get(key, []) if now - t < self.window]
        if len(recent) >= self.limit:
            self.hits[key] = recent
            return False
        recent.append(now)
        self.hits[key] = recent
        return True

    def reset(self) -> None:
        self.hits.clear()


def client_ip(request: Request) -> str:
    """Peer address as resolved by uvicorn's proxy-headers middleware.

    X-Forwarded-For is honored ONLY when the direct peer is in FORWARDED_ALLOW_IPS
    (see backend/Dockerfile and docs/deploy/client-ip-and-proxies.md); never read here.
    """
    return ((request.client.host if request.client else "") or "unknown")


# AI demo bucket (unchanged limit: 20 requests / minute / IP).
_DEMO_LIMIT = SlidingLimiter(20, 60.0)


def _ip_ok(request: Request) -> bool:
    return _DEMO_LIMIT.allow(client_ip(request))


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


# ---------- Waitlist ----------
# Its own limiter buckets (never shared with the AI demo). Defaults chosen by Momoempire Builder;
# adjust freely, these are not product decisions.
WAITLIST_MAX_BODY_BYTES = 16 * 1024
_WAITLIST_IP_MINUTE = SlidingLimiter(5, 60.0)        # signups per IP per minute
_WAITLIST_IP_HOUR = SlidingLimiter(30, 3600.0)       # signups per IP per hour
_WAITLIST_INDEX_READY = False

# Honeypot: a visually hidden input on the landing form that humans leave empty.
HONEYPOT_FIELD = "website"
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

WAITLIST_RESPONSE = {"ok": True, "status": "received"}  # identical for new and duplicate signups


class WaitlistIn(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    email: EmailStr
    name: Optional[str] = Field(default="", max_length=120)
    business_name: Optional[str] = Field(default="", max_length=120)
    industry: Optional[str] = Field(default="", max_length=60)
    note: Optional[str] = Field(default="", max_length=2000)

    @field_validator("email", mode="before")
    @classmethod
    def _email_len(cls, v):
        if isinstance(v, str) and len(v.strip()) > 254:
            raise ValueError("email must be at most 254 characters")
        return v


async def _read_capped_body(request: Request, cap: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > cap:
                raise HTTPException(413, "Request body too large")
        except ValueError:
            raise HTTPException(400, "Invalid Content-Length")
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > cap:
            raise HTTPException(413, "Request body too large")
    return body


async def _ensure_waitlist_index(db) -> None:
    global _WAITLIST_INDEX_READY
    if _WAITLIST_INDEX_READY:
        return
    try:
        await db.waitlist.create_index("email", unique=True)
    except Exception:  # e.g. pre-existing duplicates; the atomic claim below still prevents re-sends
        log.warning("waitlist: could not ensure unique email index", exc_info=True)
    _WAITLIST_INDEX_READY = True


def turnstile_enabled() -> bool:
    return (os.environ.get("TURNSTILE_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


async def verify_turnstile(token: str, remote_ip: str) -> bool:
    """Cloudflare Turnstile server-side check. Only called when TURNSTILE_ENABLED is on.

    Enabled without TURNSTILE_SECRET_KEY fails closed (logged). Network errors fail closed.
    """
    secret = os.environ.get("TURNSTILE_SECRET_KEY") or ""
    if not secret:
        log.error("TURNSTILE_ENABLED is on but TURNSTILE_SECRET_KEY is not set; rejecting waitlist signups")
        return False
    if not token or len(token) > 2048:
        return False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(TURNSTILE_VERIFY_URL,
                                  data={"secret": secret, "response": token, "remoteip": remote_ip})
            return bool(r.json().get("success"))
    except Exception:
        log.exception("turnstile: verification request failed")
        return False


async def _send_waitlist_confirmation(email: str) -> None:
    try:
        from services.email import send_email, _frame
        html = _frame(
            "<p style='font-size:20px;margin:4px 0 10px'>You're on the list ✨</p>"
            "<p style='color:#555'>Thanks for raising your hand. We'll reach out as soon as your industry seat opens up. "
            "In the meantime, you can also try the free trial anytime at <a href='https://www.aioffice.io/pricing'>our pricing page</a>.</p>",
            app_name="AI Office",
        )
        await send_email(to=email, subject="You're on the AI Office waitlist", html=html)
    except Exception:
        log.exception("waitlist: confirmation email failed")


@router.post("/waitlist")
async def waitlist(request: Request, background: BackgroundTasks):
    ip = client_ip(request)
    if not (_WAITLIST_IP_MINUTE.allow(ip) and _WAITLIST_IP_HOUR.allow(ip)):
        raise HTTPException(429, "Too many requests. Please try again later.")

    raw = await _read_capped_body(request, WAITLIST_MAX_BODY_BYTES)
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        raise RequestValidationError([{"type": "json_invalid", "loc": ("body",), "msg": "Invalid JSON", "input": None}])
    if not isinstance(payload, dict):
        raise RequestValidationError([{"type": "dict_type", "loc": ("body",), "msg": "Expected an object", "input": None}])
    if payload.get(HONEYPOT_FIELD):
        # Bot filled the hidden field: same generic success, store nothing, send nothing.
        log.info("waitlist: honeypot triggered")
        return dict(WAITLIST_RESPONSE)
    try:
        data = WaitlistIn.model_validate(payload)
    except ValidationError as e:
        raise RequestValidationError(e.errors(include_url=False, include_input=False))

    if turnstile_enabled():
        token = payload.get("turnstile_token")
        if not await verify_turnstile(token if isinstance(token, str) else "", ip):
            raise HTTPException(400, "Verification failed. Please refresh the page and try again.")

    db = get_db()
    await _ensure_waitlist_index(db)
    email = data.email.lower()
    now = _now_iso()
    doc = {
        "id": _uuid(), "email": email, "name": data.name or "",
        "business_name": data.business_name or "", "industry": data.industry or "",
        "note": data.note or "", "source": "landing", "ip": ip, "created_at": now,
    }
    inserted = False
    try:
        res = await db.waitlist.update_one({"email": email}, {"$setOnInsert": doc}, upsert=True)
        inserted = getattr(res, "upserted_id", None) is not None
    except Exception:  # duplicate-key race on the unique index = already on the list
        log.info("waitlist: concurrent duplicate signup ignored")

    # Confirmation email: at most once per address, only for the request that created the entry
    # (duplicates and rows from before this change never get another email). An atomic claim
    # guards concurrent requests. Sent after the response so new vs duplicate take about the same
    # time. Signup volume is bounded by the waitlist IP limiter above. (WL-008 may remove the email.)
    if inserted:
        claim = await db.waitlist.update_one(
            {"email": email, "confirmation_sent_at": {"$exists": False}},
            {"$set": {"confirmation_sent_at": now}},
        )
        if getattr(claim, "modified_count", 0) == 1:
            background.add_task(_send_waitlist_confirmation, data.email)
    return dict(WAITLIST_RESPONSE)
