"""Public customer portal + service request intake per tenant."""
import secrets
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Response, Request
from pydantic import BaseModel
from db import get_db
from models import _uuid, _now_iso, Lead, Customer, Appointment
from models_phase2 import ServiceRequestIn, PortalMagicLinkIn

router = APIRouter(prefix="/portal", tags=["portal"])

PORTAL_COOKIE = "portal_token"


async def _tenant_by_slug(slug: str):
    db = get_db()
    t = await db.tenants.find_one({"slug": slug, "status": "active"}, {"_id": 0})
    if not t:
        raise HTTPException(404, "Business not found")
    return t


@router.get("/{slug}")
async def portal_home(slug: str):
    db = get_db()
    t = await _tenant_by_slug(slug)
    services = await db.services.find({"tenant_id": t["id"], "active": True}, {"_id": 0}).to_list(100)
    return {
        "slug": t["slug"],
        "name": t["name"],
        "description": t.get("description"),
        "branding": t.get("branding", {}),
        "contact_phone": t.get("contact_phone"),
        "contact_email": t.get("contact_email"),
        "hours": t.get("hours"),
        "service_areas": t.get("service_areas", []),
        "faqs": t.get("faqs", []),
        "address": t.get("address") or {},
        "ai_employee": {"name": (t.get("ai_employee") or {}).get("name", "AI Receptionist")},
        "services": [{"id": s["id"], "name": s["name"], "description": s.get("description",""),
                      "price": s.get("price", 0), "duration_minutes": s.get("duration_minutes", 60)} for s in services],
    }


@router.post("/{slug}/service-request")
async def service_request(slug: str, data: ServiceRequestIn):
    db = get_db()
    t = await _tenant_by_slug(slug)
    lead = Lead(
        tenant_id=t["id"], name=data.name, phone=data.phone, email=data.email or None,
        source="portal", status="new",
        notes=f"Service: {data.service}. Preferred time: {data.preferred_time}. Notes: {data.notes}",
    )
    await db.leads.insert_one(lead.model_dump())
    return {"status": "ok", "lead_id": lead.id}


class BookIn(BaseModel):
    customer_name: str
    customer_phone: str
    service_id: str | None = None
    service_name: str | None = None
    start_at: str
    notes: str = ""


@router.post("/{slug}/book")
async def book(slug: str, data: BookIn):
    db = get_db()
    t = await _tenant_by_slug(slug)
    svc_name = data.service_name or ""
    if not svc_name and data.service_id:
        svc = await db.services.find_one({"id": data.service_id, "tenant_id": t["id"]})
        if svc:
            svc_name = svc["name"]
    appt = Appointment(
        tenant_id=t["id"], customer_name=data.customer_name, customer_phone=data.customer_phone,
        service_id=data.service_id, service_name=svc_name,
        start_at=data.start_at,
        end_at=(datetime.fromisoformat(data.start_at.replace("Z", "+00:00")) + timedelta(hours=1)).isoformat(),
        status="scheduled", notes=data.notes or f"Booked via public portal",
    )
    await db.appointments.insert_one(appt.model_dump())
    # Ensure customer
    cust = await db.customers.find_one({"tenant_id": t["id"], "phone": data.customer_phone})
    if not cust:
        c = Customer(tenant_id=t["id"], name=data.customer_name, phone=data.customer_phone)
        await db.customers.insert_one(c.model_dump())
    return {"status": "ok", "appointment_id": appt.id}


# ---------- Passwordless (magic-link) portal login for returning customers ----------
@router.post("/{slug}/magic-link")
async def magic_link(slug: str, data: PortalMagicLinkIn):
    """Issue a magic link for an existing customer. In demo mode we print the link + token."""
    db = get_db()
    t = await _tenant_by_slug(slug)
    q = {"tenant_id": t["id"]}
    if data.phone:
        q["phone"] = data.phone
    elif data.email:
        q["email"] = data.email
    else:
        raise HTTPException(400, "phone or email required")
    cust = await db.customers.find_one(q)
    # Don't leak existence
    if cust:
        token = secrets.token_urlsafe(24)
        expires = datetime.now(timezone.utc) + timedelta(hours=2)
        await db.portal_tokens.insert_one({
            "id": _uuid(), "token": token, "tenant_id": t["id"], "customer_id": cust["id"],
            "expires_at": expires, "created_at": _now_iso(), "used": False,
        })
        print(f"[PORTAL MAGIC LINK] /portal/{slug}/me?token={token}")
    return {"status": "ok", "message": "If a matching customer exists we've sent a link."}


@router.get("/{slug}/me")
async def portal_me(slug: str, request: Request, token: str | None = None):
    db = get_db()
    t = await _tenant_by_slug(slug)
    tok = token or request.cookies.get(PORTAL_COOKIE)
    if not tok:
        raise HTTPException(401, "No portal token")
    rec = await db.portal_tokens.find_one({"token": tok, "tenant_id": t["id"]})
    if not rec:
        raise HTTPException(401, "Invalid token")
    from timeutil import is_expired  # EMP-W-CF-026: same naive-vs-aware crash as reset-password
    if is_expired(rec.get("expires_at")):
        raise HTTPException(401, "Token expired")
    cust = await db.customers.find_one({"id": rec["customer_id"]}, {"_id": 0})
    if not cust:
        raise HTTPException(404, "Customer not found")
    # Load their appointments + estimates + invoices
    appts = await db.appointments.find({"tenant_id": t["id"], "customer_phone": cust.get("phone", "__none__")}, {"_id": 0}).to_list(50)
    estimates = await db.estimates.find({"tenant_id": t["id"], "customer_phone": cust.get("phone", "__none__")}, {"_id": 0}).to_list(50)
    invoices = await db.invoices.find({"tenant_id": t["id"], "customer_phone": cust.get("phone", "__none__")}, {"_id": 0}).to_list(50)
    return {"customer": cust, "appointments": appts, "estimates": estimates, "invoices": invoices}


# Response helper that also sets a cookie
@router.post("/{slug}/exchange-token")
async def exchange_token(slug: str, token: str, response: Response):
    db = get_db()
    t = await _tenant_by_slug(slug)
    rec = await db.portal_tokens.find_one({"token": token, "tenant_id": t["id"]})
    if not rec:
        raise HTTPException(401, "Invalid token")
    response.set_cookie(PORTAL_COOKIE, token, httponly=True, secure=True, samesite="none", max_age=7200, path="/")
    return {"status": "ok"}
