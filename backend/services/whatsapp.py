"""Twilio WhatsApp channel — Mexico entry playbook §2 (WS-2).

WhatsApp is a first-class messaging channel alongside SMS, on the same
Twilio rails the platform already uses (playbook §2.1): the Messages API
with ``whatsapp:`` address prefixes, Content API templates via
``contentSid``, and the same webhook family. What this module adds over
raw SMS:

- Per-shop WABA config shape (integrations key "whatsapp") — config only;
  sender registration / Meta verification are owner steps (playbook §2.3)
  and never happen in code.
- Template registry (playbook §2.4): business-initiated messages outside
  the 24-hour window must be pre-approved templates.
- 24-hour session-window enforcement (playbook §2.2): free-form sends are
  refused outside the window; callers must use a template instead.
- Opt-in ledger (playbook §2.2): no business-initiated template send
  without a stored consent record; STOP / BAJA opt-outs honoured at once.
- MX posture (playbook §2.7 + scan §Mexico 4c): for MX-market tenants
  WhatsApp is the primary customer channel and SMS the fallback — MX
  long-code SMS is best-effort/OTP-grade only.

Credentials resolve per-tenant from `integrations` (demo-safe) and fall
back to the tenant's Twilio config / environment, exactly like
services/twilio.py. With nothing configured, sends degrade to logged
demo stubs — no network path exists.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from db import get_db

logger = logging.getLogger("whatsapp")

WHATSAPP_PREFIX = "whatsapp:"
WINDOW_HOURS = 24

# Opt-out / opt-in keywords (EN + ES-MX). Matching is exact, case-insensitive,
# on the whole inbound body — the way carriers and Meta expect them.
OPT_OUT_KEYWORDS = {"STOP", "BAJA", "CANCEL", "CANCELAR", "UNSUBSCRIBE", "END", "QUIT", "ALTO"}
OPT_IN_KEYWORDS = {"START", "ALTA", "UNSTOP", "SUSCRIBIR"}

# ---- Template registry (playbook §2.4) ----
# name -> {category, language, variables, description}. contentSid values are
# per-WABA (Meta approves templates per sender), so they live in tenant
# config (integrations.whatsapp.templates) or env TWILIO_CONTENT_SID_<NAME>;
# the registry only fixes the catalogue, categories and variable shapes.
TEMPLATES: dict = {
    "cita_confirmada": {
        "category": "utility", "language": "es-MX",
        "variables": ["dia", "hora", "tecnico", "direccion"],
        "description": "Booking confirmation: fecha/hora, técnico, dirección.",
    },
    "recordatorio_cita": {
        "category": "utility", "language": "es-MX",
        "variables": ["dia", "hora"],
        "description": "Appointment reminder; customer replies SÍ / CAMBIO.",
    },
    "tecnico_en_camino": {
        "category": "utility", "language": "es-MX",
        "variables": ["tecnico", "minutos"],
        "description": "On-my-way notice with ETA.",
    },
    "trabajo_terminado": {
        "category": "utility", "language": "es-MX",
        "variables": ["total", "liga_pago"],
        "description": "Job done + total + payment link.",
    },
    "factura_lista": {
        "category": "utility", "language": "es-MX",
        "variables": ["liga"],
        "description": "CFDI ready — PDF/XML download link (playbook §1 tie-in).",
    },
    "pago_recibido": {
        "category": "utility", "language": "es-MX",
        "variables": ["total"],
        "description": "Payment received confirmation.",
    },
    "saldo_pendiente": {
        "category": "utility", "language": "es-MX",
        "variables": ["dia", "total"],
        "description": "Gentle balance-due notice (strictly transactional).",
    },
    "recordatorio_mantenimiento": {
        "category": "utility", "language": "es-MX",
        "variables": ["servicio"],
        "description": "Seasonal maintenance reminder tied to an existing service plan.",
    },
    "codigo_verificacion": {
        "category": "authentication", "language": "es-MX",
        "variables": ["codigo"],
        "description": "OTP for the customer portal.",
    },
    "promo_temporada": {
        "category": "marketing", "language": "es-MX",
        "variables": ["oferta"],
        "description": "Seasonal promotion — opt-in only, frequency-capped.",
    },
    "reactivacion": {
        "category": "marketing", "language": "es-MX",
        "variables": [],
        "description": "Win-back message — opt-in only, frequency-capped.",
    },
}


def get_template(name: str) -> dict:
    """Registry entry for a template name. Raises ValueError if unknown —
    sending an unregistered template is a programming error, not a runtime
    condition to degrade around."""
    if name not in TEMPLATES:
        raise ValueError(f"Unknown WhatsApp template: {name!r}")
    return dict(TEMPLATES[name])


def list_templates(category: Optional[str] = None) -> dict:
    if category is None:
        return {k: dict(v) for k, v in TEMPLATES.items()}
    return {k: dict(v) for k, v in TEMPLATES.items() if v["category"] == category}


# ---- addressing ----

def is_whatsapp_address(addr: str | None) -> bool:
    return str(addr or "").strip().lower().startswith(WHATSAPP_PREFIX)


def parse_whatsapp_address(addr: str | None) -> str:
    """Strip the ``whatsapp:`` prefix, returning the bare E.164 number."""
    a = str(addr or "").strip()
    return a[len(WHATSAPP_PREFIX):] if is_whatsapp_address(a) else a


def to_whatsapp_address(phone: str) -> str:
    p = str(phone or "").strip()
    return p if is_whatsapp_address(p) else f"{WHATSAPP_PREFIX}{p}"


# ---- tenant config ----

async def _tenant_whatsapp_cfg(tenant_id: str) -> dict:
    """Per-shop WhatsApp config from integrations key "whatsapp".

    Shape (playbook §2.1): {waba_id, sender_number, display_name,
    quality_rating, templates: {name: content_sid}, account_sid, auth_token}.
    Account credentials fall back to the tenant's Twilio integration/env —
    a shop's WhatsApp sender lives under its Twilio (sub)account."""
    from services.twilio import _tenant_twilio_cfg

    db = get_db()
    row = await db.integrations.find_one(
        {"tenant_id": tenant_id, "key": "whatsapp"}, {"_id": 0, "config": 1},
    )
    cfg = (row or {}).get("config", {}) or {}
    twilio_cfg = await _tenant_twilio_cfg(tenant_id)
    return {
        "waba_id": cfg.get("waba_id") or "",
        "sender_number": cfg.get("sender_number") or "",
        "display_name": cfg.get("display_name") or "",
        "quality_rating": cfg.get("quality_rating") or "",
        "templates": cfg.get("templates") or {},
        "account_sid": cfg.get("account_sid") or twilio_cfg["account_sid"],
        "auth_token": cfg.get("auth_token") or twilio_cfg["auth_token"],
    }


def _template_content_sid(cfg: dict, template_name: str) -> str:
    sid = (cfg.get("templates") or {}).get(template_name)
    if sid:
        return sid
    return os.environ.get(f"TWILIO_CONTENT_SID_{template_name.upper()}") or ""


# ---- 24-hour session window (playbook §2.2) ----

def _parse_iso(ts: str | None) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def window_open_at(last_inbound_iso: str | None, now: Optional[datetime] = None) -> bool:
    """Pure window check: free-form replies are allowed for WINDOW_HOURS
    after the customer's last inbound message."""
    last = _parse_iso(last_inbound_iso)
    if last is None:
        return False
    now = now or datetime.now(timezone.utc)
    return now < last + timedelta(hours=WINDOW_HOURS)


async def note_inbound(tenant_id: str, phone: str, at: Optional[datetime] = None) -> dict:
    """Record a customer inbound message: (re)opens the 24-hour window."""
    at = at or datetime.now(timezone.utc)
    doc = {
        "tenant_id": tenant_id,
        "phone": parse_whatsapp_address(phone),
        "last_inbound_at": at.isoformat(),
        "window_expires_at": (at + timedelta(hours=WINDOW_HOURS)).isoformat(),
    }
    db = get_db()
    await db.whatsapp_windows.update_one(
        {"tenant_id": tenant_id, "phone": doc["phone"]}, {"$set": doc}, upsert=True,
    )
    return doc


async def window_open(tenant_id: str, phone: str, now: Optional[datetime] = None) -> bool:
    db = get_db()
    row = await db.whatsapp_windows.find_one(
        {"tenant_id": tenant_id, "phone": parse_whatsapp_address(phone)}, {"_id": 0},
    )
    return window_open_at((row or {}).get("last_inbound_at"), now)


# ---- opt-in ledger (playbook §2.2) ----

async def record_opt_in(tenant_id: str, phone: str, *, source: str,
                        categories: Optional[list] = None) -> dict:
    """Store an explicit opt-in. Sources: booking form, widget, keyword,
    click-to-whatsapp, in-store QR (playbook §2.2 capture points)."""
    doc = {
        "tenant_id": tenant_id,
        "phone": parse_whatsapp_address(phone),
        "status": "opted_in",
        "source": source,
        "categories": categories or ["utility", "authentication"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    db = get_db()
    await db.whatsapp_optins.update_one(
        {"tenant_id": tenant_id, "phone": doc["phone"]}, {"$set": doc}, upsert=True,
    )
    return doc


async def record_opt_out(tenant_id: str, phone: str, *, keyword: str = "STOP") -> dict:
    """Store an opt-out. Honoured immediately: subsequent business-initiated
    sends check the ledger before sending (see send_whatsapp_template)."""
    doc = {
        "tenant_id": tenant_id,
        "phone": parse_whatsapp_address(phone),
        "status": "opted_out",
        "source": f"keyword:{keyword.upper()}",
        "categories": [],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    db = get_db()
    await db.whatsapp_optins.update_one(
        {"tenant_id": tenant_id, "phone": doc["phone"]}, {"$set": doc}, upsert=True,
    )
    return doc


async def get_opt_in(tenant_id: str, phone: str) -> Optional[dict]:
    db = get_db()
    return await db.whatsapp_optins.find_one(
        {"tenant_id": tenant_id, "phone": parse_whatsapp_address(phone)}, {"_id": 0},
    )


async def is_opted_in(tenant_id: str, phone: str, *, category: str = "utility") -> bool:
    row = await get_opt_in(tenant_id, phone)
    if not row or row.get("status") != "opted_in":
        return False
    cats = row.get("categories") or []
    return category in cats or "all" in cats


def classify_keyword(body: str | None) -> Optional[str]:
    """Classify an inbound body as an opt-out / opt-in keyword, else None."""
    word = str(body or "").strip().upper()
    if word in OPT_OUT_KEYWORDS:
        return "opt_out"
    if word in OPT_IN_KEYWORDS:
        return "opt_in"
    return None


# ---- sending ----

def _creds_ready(cfg: dict) -> bool:
    return bool(cfg["account_sid"] and cfg["auth_token"] and cfg["sender_number"])


async def send_whatsapp(*, tenant_id: str, to: str, body: str) -> dict:
    """Send a free-form (session) WhatsApp message.

    Refused outside the 24-hour window (playbook §2.2) — the caller must
    fall back to send_whatsapp_template. Returns {sid, status, demo} like
    services.twilio.send_sms; degrades to a logged demo stub with no creds."""
    if not await window_open(tenant_id, to):
        logger.info("[WHATSAPP window-closed tenant=%s to=%s] %s", tenant_id, to, body)
        return {"sid": None, "status": "window_closed", "demo": False,
                "error": "24-hour customer-service window is closed; use a template"}
    cfg = await _tenant_whatsapp_cfg(tenant_id)
    if not _creds_ready(cfg):
        logger.info("[WHATSAPP demo-send tenant=%s to=%s] %s", tenant_id, to, body)
        return {"sid": f"WA_demo_{tenant_id[:6]}", "status": "demo", "demo": True}
    try:
        from twilio.rest import Client
        client = Client(cfg["account_sid"], cfg["auth_token"])
        msg = client.messages.create(
            from_=to_whatsapp_address(cfg["sender_number"]),
            to=to_whatsapp_address(to),
            body=body,
        )
        return {"sid": msg.sid, "status": msg.status or "queued", "demo": False}
    except Exception as e:  # SDK missing or API error — same posture as send_sms
        logger.exception("WhatsApp send failed: %s", e)
        return {"sid": None, "status": "error", "error": str(e), "demo": False}


async def send_whatsapp_template(*, tenant_id: str, to: str, template: str,
                                 variables: Optional[dict] = None) -> dict:
    """Send a pre-approved template (Content API contentSid + variables).

    Business-initiated sends require a stored opt-in for the template's
    category (playbook §2.2); without one the send is refused with
    status "opt_in_required" and nothing is sent."""
    entry = get_template(template)  # ValueError on unknown template
    if not await is_opted_in(tenant_id, to, category=entry["category"]):
        logger.info("[WHATSAPP opt-in-required tenant=%s to=%s template=%s]",
                    tenant_id, to, template)
        return {"sid": None, "status": "opt_in_required", "demo": False, "template": template,
                "error": "no stored opt-in for this customer/category"}
    cfg = await _tenant_whatsapp_cfg(tenant_id)
    content_sid = _template_content_sid(cfg, template)
    if not _creds_ready(cfg) or not content_sid:
        logger.info("[WHATSAPP demo-template tenant=%s to=%s template=%s vars=%s]",
                    tenant_id, to, template, variables)
        return {"sid": f"WA_demo_{tenant_id[:6]}", "status": "demo", "demo": True,
                "template": template}
    try:
        from twilio.rest import Client
        client = Client(cfg["account_sid"], cfg["auth_token"])
        msg = client.messages.create(
            from_=to_whatsapp_address(cfg["sender_number"]),
            to=to_whatsapp_address(to),
            content_sid=content_sid,
            content_variables=json.dumps(variables or {}),
        )
        return {"sid": msg.sid, "status": msg.status or "queued", "demo": False,
                "template": template}
    except Exception as e:
        logger.exception("WhatsApp template send failed: %s", e)
        return {"sid": None, "status": "error", "error": str(e), "demo": False,
                "template": template}


# ---- channel preference (playbook §2.7 / scan §Mexico 4c) ----

async def preferred_channel(tenant_id: str, tenant: dict, phone: str) -> str:
    """Outbound channel for a customer conversation: "whatsapp" or "sms".

    WhatsApp wins when the tenant is MX-market (WhatsApp-primary posture)
    or has a WhatsApp sender configured AND the customer has opted in.
    Everyone else stays on SMS — the pre-WS-2 default."""
    cfg = await _tenant_whatsapp_cfg(tenant_id)
    if not cfg["sender_number"]:
        return "sms"
    country = str((tenant or {}).get("country") or "").strip().upper()
    if country == "MX":
        return "whatsapp"
    if await is_opted_in(tenant_id, phone, category="utility"):
        return "whatsapp"
    return "sms"


# ---- status callbacks (playbook §2.1: sent/delivered/read/failed) ----

async def record_message_status(tenant_id: str, form: dict) -> dict:
    """Persist a Twilio message-status callback for telemetry. WhatsApp
    adds "read" to the SMS status set; both are stored the same way."""
    doc = {
        "tenant_id": tenant_id,
        "message_sid": form.get("MessageSid") or form.get("SmsSid") or "",
        "status": form.get("MessageStatus") or form.get("SmsStatus") or "",
        "from": parse_whatsapp_address(form.get("From") or ""),
        "to": parse_whatsapp_address(form.get("To") or ""),
        "channel": "whatsapp" if is_whatsapp_address(form.get("From") or form.get("To")) else "sms",
        "error_code": form.get("ErrorCode") or "",
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    db = get_db()
    await db.message_status_events.insert_one(doc)
    return doc
