"""Public demo + waitlist: no-auth endpoints used by the marketing homepage.

- POST /api/public/demo/start → opens a 10-turn demo session with a mock tenant
  shaped by a chosen industry preset. No DB tenant touched.
- POST /api/public/demo/turn → caller utterance → AI reply. Rate-limited by IP.
- POST /api/public/waitlist → capture email/name/business/interest for early access.
"""
import asyncio
import json
import logging
import re
import os
import sys
import time
from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pymongo.errors import DuplicateKeyError
from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError, field_validator
from typing import Literal, Optional
import db as _db_module
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
_WAITLIST_INDEX_LOCK = asyncio.Lock()
# EMP-WL-040: None = not built yet, True = unique index in place, False = build failed (health
# reports "degraded"; production refuses to start). Retry delays between build attempts (seconds).
_WAITLIST_INDEX_OK: Optional[bool] = None
_INDEX_RETRY_DELAYS = (0.5, 2.0)
# EMP-WL-041: reply when the signup could not be saved (no internals, no PII).
WAITLIST_UNAVAILABLE = "Sorry, we couldn't save your signup right now. Please try again in a few minutes."
WAITLIST_DUPLICATES_COLLECTION = "waitlist_duplicates"  # archive for de-duplicated rows (never deleted)

# Honeypot: a visually hidden input on the landing form that humans leave empty.
HONEYPOT_FIELD = "website"
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

WAITLIST_RESPONSE = {"ok": True, "status": "received"}  # identical for new and duplicate signups


_TAG_RE = re.compile(r"</?[A-Za-z!][^>]*>")  # real tags/comments only: "a < b > c" keeps its text
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(value):
    """EMP-W-CF-031: plain text only for stored free-text fields.

    Removes HTML tags (e.g. "<script>x</script>" -> "x"), any stray "<" or ">", and control
    characters except newline/tab. Anything that later renders waitlist rows (admin page, CSV/
    email export) must STILL escape on output; this is defense in depth, not a substitute.
    """
    if not isinstance(value, str):
        return value
    value = _TAG_RE.sub("", value)
    value = value.replace("<", "").replace(">", "")
    return _CTRL_RE.sub("", value).strip()


class WaitlistIn(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    email: EmailStr
    name: Optional[str] = Field(default="", max_length=120)
    business_name: Optional[str] = Field(default="", max_length=120)
    industry: Optional[str] = Field(default="", max_length=60)
    note: Optional[str] = Field(default="", max_length=2000)
    # Instant-quote estimator (EMP-FEAT-001). One lead source per lead (Brann's rule): estimator
    # leads are source "website form" with source_detail "estimator"; other signups unchanged.
    source_detail: Optional[Literal["estimator"]] = None
    estimated_tier: Optional[str] = Field(default=None, max_length=40, pattern=r"^[a-z0-9_]+$")

    @field_validator("name", "business_name", "industry", "note", mode="after")
    @classmethod
    def _plain_text(cls, v):
        return sanitize_text(v)

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


def normalize_email(value) -> str:
    """Canonical waitlist key: trimmed + lowercased (EMP-WL-012)."""
    return (value or "").strip().lower() if isinstance(value, str) else ""


_NEVER = datetime.max.replace(tzinfo=timezone.utc)


def _as_utc(value) -> Optional[datetime]:
    """created_at as an aware UTC datetime: BSON date (naive = UTC, as pymongo returns it), or an
    ISO string (with Z, an offset, or naive = UTC). None if missing or unparseable."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    return None


def _doc_time(doc) -> datetime:
    """Sort key for 'earliest' (EMP-WL-042): created_at as a real datetime or an ISO string, else
    the ObjectId's creation time, else last. Compared as datetimes, so mixed formats sort right."""
    ts = _as_utc(doc.get("created_at"))
    if ts is not None:
        return ts
    gen = getattr(doc.get("_id"), "generation_time", None)
    return _as_utc(gen) if gen is not None else _NEVER


async def dedupe_waitlist(db, apply: bool = True) -> dict:
    """De-duplicate waitlist rows by normalized email before the unique index is built (EMP-WL-024).

    Keeps the EARLIEST row per normalized email. Later duplicates are copied in full into
    `waitlist_duplicates` (with duplicate_of/archived_at) and only then removed from `waitlist`;
    nothing is merged into the kept row. A kept row whose email isn't normalized gets the
    normalized value, with the original kept in `email_original`. Idempotent and safe to run in
    several processes at once (archive is an upsert by _id; removal is by _id).
    apply=False is a dry run: counts only, no writes.
    """
    groups: dict[str, list] = {}
    scanned = 0
    async for d in db.waitlist.find({}, {"_id": 1, "email": 1, "created_at": 1}):
        scanned += 1
        norm = normalize_email(d.get("email"))
        if norm:
            groups.setdefault(norm, []).append(d)
    stats = {"scanned": scanned, "duplicate_groups": 0, "archived": 0, "normalized": 0, "dry_run": not apply}
    now = _now_iso()
    for norm, docs in groups.items():
        docs.sort(key=lambda d: (_doc_time(d), str(d.get("_id"))))
        keep, extra = docs[0], docs[1:]
        if extra:
            stats["duplicate_groups"] += 1
        for d in extra:
            stats["archived"] += 1
            if not apply:
                continue
            full = await db.waitlist.find_one({"_id": d["_id"]})
            if full is None:  # already handled by another process
                continue
            archived = {**full, "duplicate_of": keep.get("_id"), "archived_at": now, "archived_reason": "duplicate email"}
            await db[WAITLIST_DUPLICATES_COLLECTION].replace_one({"_id": full["_id"]}, archived, upsert=True)
            await db.waitlist.delete_one({"_id": full["_id"]})
        if keep.get("email") != norm:
            stats["normalized"] += 1
            if apply:
                await db.waitlist.update_one(
                    {"_id": keep["_id"]}, {"$set": {"email": norm, "email_original": keep.get("email")}})
    if stats["duplicate_groups"] or stats["normalized"]:
        log.warning("waitlist dedupe%s: scanned=%d duplicate_groups=%d archived=%d normalized=%d",
                    "" if apply else " (dry run)", stats["scanned"], stats["duplicate_groups"],
                    stats["archived"], stats["normalized"])
    else:
        log.info("waitlist dedupe: scanned=%d, no duplicates", stats["scanned"])
    return stats


async def _ensure_waitlist_index(db) -> None:
    """Once per process: de-duplicate, then build the unique index on the normalized email.

    Runs at app startup (router startup hook) and again lazily before the first signup if that
    didn't complete (e.g. the DB wasn't reachable at boot, or the WL-002 waitlist-only app).
    """
    global _WAITLIST_INDEX_READY, _WAITLIST_INDEX_OK
    if _WAITLIST_INDEX_READY:
        return
    async with _WAITLIST_INDEX_LOCK:
        if _WAITLIST_INDEX_READY:
            return
        # EMP-WL-040: retry briefly (re-running the dedupe, in case a row arrived between the scan
        # and the build), then fail LOUDLY: health says "degraded" and production won't start.
        attempts = len(_INDEX_RETRY_DELAYS) + 1
        last = None
        for attempt in range(attempts):
            await dedupe_waitlist(db, apply=True)  # DB errors propagate: retried on the next call
            try:
                await build_waitlist_unique_index(db)
                _WAITLIST_INDEX_OK = True
                break
            except Exception as e:  # noqa: BLE001 - reported below without the error text (may hold an email)
                last = e
                log.warning("waitlist: unique email index build failed (attempt %d/%d: %s)",
                            attempt + 1, attempts, type(e).__name__)
                if attempt < attempts - 1:
                    await asyncio.sleep(_INDEX_RETRY_DELAYS[attempt])
        else:
            _WAITLIST_INDEX_OK = False
            log.critical(
                "waitlist: NO unique email index (%s). Duplicate signups are possible and /api/health "
                "reports 'degraded'. Usual causes: an existing non-unique 'email_1' index (drop it), or "
                "duplicates the dedupe can't resolve. Run backend/scripts/dedupe_waitlist.py and restart.",
                type(last).__name__)
        _WAITLIST_INDEX_READY = True


async def build_waitlist_unique_index(db) -> None:
    """Unique index on the normalized email; raises if it can't be in place. Shared with
    scripts/dedupe_waitlist.py. Unique only for real (non-empty string) emails, so legacy rows with
    no email or "" can't block it (Watcher: two rows without an email gave E11000). A unique
    email_1 that already exists (e.g. built by PR #11) is kept; a NON-unique one is never dropped
    automatically, it fails instead."""
    existing = (await _index_info(db)).get("email_1")
    if existing is None:
        await db.waitlist.create_index("email", unique=True, partialFilterExpression={"email": {"$gt": ""}})
    elif not existing.get("unique"):
        raise RuntimeError("existing non-unique email_1 index")


async def _index_info(db) -> dict:
    """Existing indexes; {} if they can't be listed (create_index then reports any real problem)."""
    try:
        return await db.waitlist.index_information() or {}
    except Exception:  # noqa: BLE001
        return {}


def waitlist_index_healthy() -> bool:
    """False only after the unique-index build has failed (EMP-WL-040). Used by /api/health."""
    return _WAITLIST_INDEX_OK is not False


def _strict_env() -> bool:
    return (os.environ.get("APP_ENV") or "development").strip().lower() in ("production", "staging")


@router.on_event("startup")
async def _waitlist_startup() -> None:
    try:
        await _ensure_waitlist_index(get_db())
    except Exception:
        # DB unreachable at boot: not an index problem; retried before the first signup.
        log.warning("waitlist: startup dedupe/index skipped (will retry on first signup)", exc_info=True)
    if _WAITLIST_INDEX_OK is False and _strict_env():
        raise RuntimeError("Refusing to start: the waitlist unique email index could not be built "
                           "(see the CRITICAL log line above). APP_ENV=" + (os.environ.get("APP_ENV") or ""))


def turnstile_enabled() -> bool:
    return (os.environ.get("TURNSTILE_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


def check_turnstile_config() -> None:
    """EMP-WL-023: refuse to start when Turnstile is on but has no secret.

    Otherwise every real signup would get 400. Called at import, so uvicorn exits with this error
    in both the full app and the WL-002 waitlist-only app.
    """
    if turnstile_enabled() and not (os.environ.get("TURNSTILE_SECRET_KEY") or "").strip():
        msg = ("TURNSTILE_ENABLED is on but TURNSTILE_SECRET_KEY is empty. Refusing to start: every "
               "waitlist signup would be rejected. Set TURNSTILE_SECRET_KEY or turn TURNSTILE_ENABLED off.")
        log.critical(msg)
        raise RuntimeError(msg)


def _int(raw) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return 1


def configured_workers(argv=None, env=None) -> tuple[int, str]:
    """Worker count from the command line (`--workers N` / `--workers=N`, EMP-WL-043), then
    UVICORN_WORKERS, then WEB_CONCURRENCY. With --workers, uvicorn's worker processes get the
    parent's argv, so each worker sees the flag when it imports the app."""
    argv = sys.argv if argv is None else argv
    env = os.environ if env is None else env
    for i, arg in enumerate(argv):
        if arg == "--workers" and i + 1 < len(argv):
            return _int(argv[i + 1]), f"--workers {argv[i + 1]}"
        if arg.startswith("--workers="):
            return _int(arg.split("=", 1)[1]), arg
    for name in ("UVICORN_WORKERS", "WEB_CONCURRENCY"):
        raw = (env.get(name) or "").strip()
        if raw:
            return _int(raw), f"{name}={raw}"
    return 1, ""


def check_single_worker(argv=None, env=None) -> None:
    """EMP-WL-021: the waitlist/demo limiters live in process memory. More than one worker
    multiplies the limits (2 workers let 7 of 12 through instead of 5). Warn loudly."""
    workers, source = configured_workers(argv, env)
    if workers > 1:
        log.error("%s: the waitlist and demo rate limits are per process, so each worker "
                  "allows its own quota. Run ONE worker for the waitlist deploy "
                  "(docs/deploy/client-ip-and-proxies.md, 'One worker').", source)


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


def _signup_db():
    """EMP-WL-061: the signup path uses the short-timeout client (db.get_waitlist_db). If get_db
    has been swapped (tests, or another app wiring in its own DB), that one is used as is."""
    if getattr(get_db, "app_default", False):
        return _db_module.get_waitlist_db()
    return get_db()


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

    db = _signup_db()
    try:
        await _ensure_waitlist_index(db)
    except Exception as e:  # DB unreachable: say so instead of pretending (EMP-WL-041)
        log.error("waitlist: database unavailable, signup not saved (%s)", type(e).__name__)
        raise HTTPException(503, WAITLIST_UNAVAILABLE)
    email = normalize_email(data.email)
    now = _now_iso()
    doc = {
        "id": _uuid(), "email": email, "name": data.name or "",
        "business_name": data.business_name or "", "industry": data.industry or "",
        "note": data.note or "",
        "source": "website form" if data.source_detail == "estimator" else "landing",
        "source_detail": data.source_detail or "",
        "estimated_tier": data.estimated_tier or "",
        "ip": ip, "created_at": now,
    }
    inserted = False
    try:
        # EMP-WL-062: "$inc" makes a repeat signup a real write too. With only "$setOnInsert", an
        # existing email was a no-op match, so a DB state that blocks inserts but not matches
        # (e.g. a schema validator) answered 200 for a known email and 503 for a new one, which
        # told an attacker the address was on the list. Now both paths write and fail alike.
        # signup_count is internal (never exported, never shown); nothing the visitor sent is
        # overwritten (CF-029/031).
        res = await db.waitlist.update_one(
            {"email": email}, {"$setOnInsert": doc, "$inc": {"signup_count": 1}}, upsert=True)
        inserted = getattr(res, "upserted_id", None) is not None
    except DuplicateKeyError:  # concurrent signup won the race on the unique index = already on the list
        log.info("waitlist: concurrent duplicate signup ignored")
    except Exception as e:
        # EMP-WL-041: any OTHER database error means nothing was saved. Never report success.
        # Logged by type only: driver messages can echo the document (and so the email).
        log.error("waitlist: database error, signup not saved (%s)", type(e).__name__)
        raise HTTPException(503, WAITLIST_UNAVAILABLE)

    # Repeat signup (EMP-W-CF-029 / CF-031): never overwrite what the first signup stored, since
    # anyone who knows an address could otherwise change that person's row. Only fill fields that
    # are still blank (estimated_tier, source_detail, note), each with an atomic "still blank"
    # filter. Same response, no email resend: the reply doesn't reveal whether the email existed.
    if not inserted:
        fills = {"estimated_tier": data.estimated_tier or "", "source_detail": data.source_detail or "",
                 "note": data.note or ""}
        for field, value in fills.items():
            if not value:
                continue
            try:
                await db.waitlist.update_one({"email": email, field: {"$in": ["", None]}}, {"$set": {field: value}})
            except Exception:
                log.warning("waitlist: could not fill blank %s on existing entry", field, exc_info=True)

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


check_turnstile_config()
check_single_worker()
