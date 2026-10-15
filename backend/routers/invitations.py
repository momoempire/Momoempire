"""Team invitations (token-based)."""
import os
from fastapi import APIRouter, HTTPException, Depends, Response
from datetime import datetime, timezone, timedelta
from db import get_db
from models import _uuid, _now_iso
from models import InvitationIn, Invitation, InvitationAccept
from security import (
    hash_password, require_tenant_owner_or_admin,
    create_access_token, create_refresh_token, set_auth_cookies,
)

router = APIRouter(prefix="/tenants/invitations", tags=["invitations"])


@router.get("")
async def list_invites(user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    return await db.invitations.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).sort("created_at", -1).to_list(200)


@router.post("")
async def create_invite(data: InvitationIn, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    email = data.email.lower()
    inv = Invitation(
        tenant_id=user["tenant_id"], email=email,
        role=data.role, invited_by=user["id"],
    )
    doc = inv.model_dump()
    await db.invitations.insert_one(doc)
    doc.pop("_id", None)
    print(f"[INVITE] {email} invited to tenant {user['tenant_id']} role={data.role} token={doc['token']}")
    # Fire-and-forget email send
    try:
        from services.email import send_email, invite_html
        tenant = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0, "name": 1})
        frontend = os.environ.get("FRONTEND_URL") or ""
        accept_url = f"{frontend.rstrip('/')}/invite?token={doc['token']}" if frontend else f"/invite?token={doc['token']}"
        html = invite_html(inviter=user.get("name") or user.get("email") or "Your teammate",
                          business=(tenant or {}).get("name", "the team"),
                          role=data.role, accept_url=accept_url)
        await send_email(to=email, subject=f"You're invited to {(tenant or {}).get('name','the team')}",
                        html=html, from_name=(tenant or {}).get("name"))
    except Exception as e:
        print(f"[INVITE email] failed: {e}")
    return doc


@router.post("/{inv_id}/revoke")
async def revoke_invite(inv_id: str, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    res = await db.invitations.update_one(
        {"id": inv_id, "tenant_id": user["tenant_id"]},
        {"$set": {"status": "revoked"}},
    )
    if res.matched_count == 0:
        raise HTTPException(404, "Not found")
    return {"status": "ok"}


# ---------- Staff listing + role management ----------
@router.get("/staff")
async def list_staff(user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    return await db.users.find(
        {"tenant_id": user["tenant_id"]},
        {"_id": 0, "password_hash": 0},
    ).sort("created_at", 1).to_list(200)


@router.put("/staff/{user_id}/role")
async def set_staff_role(user_id: str, role: str, user: dict = Depends(require_tenant_owner_or_admin)):
    if role not in {"owner", "admin", "staff"}:
        raise HTTPException(400, "Invalid role")
    db = get_db()
    res = await db.users.update_one(
        {"id": user_id, "tenant_id": user["tenant_id"]},
        {"$set": {"role": role}},
    )
    if res.matched_count == 0:
        raise HTTPException(404, "User not found")
    return {"status": "ok"}


# ---------- Public (anyone with the token) accept endpoint ----------
public_router = APIRouter(prefix="/public/invitations", tags=["invitations-public"])


@public_router.get("/{token}")
async def peek(token: str):
    db = get_db()
    inv = await db.invitations.find_one({"token": token}, {"_id": 0})
    if not inv or inv.get("status") != "pending":
        raise HTTPException(404, "Invite not available")
    tenant = await db.tenants.find_one({"id": inv["tenant_id"]}, {"_id": 0})
    return {"invitation": inv, "tenant_name": tenant.get("name"), "tenant_slug": tenant.get("slug")}


@public_router.post("/accept")
async def accept(data: InvitationAccept, response: Response):
    db = get_db()
    inv = await db.invitations.find_one({"token": data.token})
    if not inv or inv.get("status") != "pending":
        raise HTTPException(400, "Invite invalid or already used")
    email = inv["email"].lower()
    if await db.users.find_one({"email": email}):
        raise HTTPException(400, "Email already registered — log in instead")
    user_id = _uuid()
    await db.users.insert_one({
        "id": user_id,
        "email": email,
        "password_hash": hash_password(data.password),
        "name": data.name,
        "role": inv["role"],
        "tenant_id": inv["tenant_id"],
        "email_verified": True,
        "mfa_enabled": False,
        "created_at": _now_iso(),
    })
    await db.invitations.update_one({"token": data.token}, {"$set": {"status": "accepted", "accepted_at": _now_iso()}})
    access = create_access_token(user_id, email, inv["role"], inv["tenant_id"])
    refresh = create_refresh_token(user_id)
    set_auth_cookies(response, access, refresh)
    return {"id": user_id, "email": email, "role": inv["role"], "tenant_id": inv["tenant_id"], "name": data.name}


# ---------- Google-based invite acceptance ----------
import httpx
from pydantic import BaseModel


class InvitationAcceptGoogle(BaseModel):
    token: str
    session_id: str  # from Emergent OAuth callback hash


@public_router.post("/accept-google")
async def accept_google(data: InvitationAcceptGoogle, response: Response):
    """Accept an invitation using Emergent-managed Google auth.
    Requires the Google-authenticated email to match the invited email."""
    db = get_db()
    inv = await db.invitations.find_one({"token": data.token})
    if not inv or inv.get("status") != "pending":
        raise HTTPException(400, "Invite invalid or already used")
    inv_email = inv["email"].lower()

    try:
        async with httpx.AsyncClient(timeout=10.0) as hc:
            r = await hc.get(
                "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
                headers={"X-Session-ID": data.session_id},
            )
            r.raise_for_status()
            info = r.json()
    except Exception as e:
        raise HTTPException(401, f"Google session exchange failed: {e}")

    google_email = (info.get("email") or "").lower()
    if not google_email:
        raise HTTPException(400, "No email on Google session")
    if google_email != inv_email:
        raise HTTPException(400, f"Google account '{google_email}' does not match the invited email '{inv_email}'.")

    session_token = info.get("session_token") or ""
    name = info.get("name") or inv_email.split("@")[0]
    picture = info.get("picture") or ""

    user = await db.users.find_one({"email": inv_email})
    if user:
        # Existing user → attach to tenant with the invited role.
        patch = {
            "tenant_id": inv["tenant_id"],
            "role": inv["role"],
            "name": name,
            "email_verified": True,
        }
        if picture:
            patch["picture"] = picture
        await db.users.update_one({"id": user["id"]}, {"$set": patch})
        user_id = user["id"]
    else:
        user_id = _uuid()
        await db.users.insert_one({
            "id": user_id,
            "email": inv_email,
            "password_hash": "",
            "name": name,
            "role": inv["role"],
            "tenant_id": inv["tenant_id"],
            "email_verified": True,
            "mfa_enabled": False,
            "picture": picture,
            "created_at": _now_iso(),
        })

    await db.invitations.update_one(
        {"token": data.token}, {"$set": {"status": "accepted", "accepted_at": _now_iso()}}
    )

    expires = datetime.now(timezone.utc) + timedelta(days=7)
    await db.user_sessions.insert_one({
        "id": _uuid(), "user_id": user_id, "session_token": session_token,
        "expires_at": expires, "created_at": _now_iso(),
    })
    response.set_cookie("session_token", session_token, httponly=True, secure=True,
                        samesite="none", max_age=7 * 86400, path="/")
    access = create_access_token(user_id, inv_email, inv["role"], inv["tenant_id"])
    refresh = create_refresh_token(user_id)
    set_auth_cookies(response, access, refresh)
    return {"id": user_id, "email": inv_email, "role": inv["role"], "tenant_id": inv["tenant_id"], "name": name}
