"""TOTP two-factor authentication.

Flow:
  1. Authenticated user: POST /auth/2fa/setup -> {otpauth_uri, secret}
     (scan into authenticator app; secret also shown for manual entry).
  2. POST /auth/2fa/verify {code} -> enables 2FA, returns 10 single-use
     backup codes (shown once — store them somewhere safe).
  3. Login with 2FA enabled: POST /auth/login returns
     {requires_2fa: true, two_fa_token} (5-minute JWT, NOT an auth token).
  4. POST /auth/2fa/challenge {two_fa_token, code|backup_code} -> auth cookies.
  5. POST /auth/2fa/disable {password} -> removes 2FA (requires password).

All challenge attempts are rate-limited (see routers/auth.py LOGIN_LIMIT).
"""
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel

from db import get_db
from models import _now_iso
from security import (
    get_current_user, verify_password,
    create_access_token, create_refresh_token, set_auth_cookies,
    decode_2fa_token,
)
import two_factor as t2f
from rate_limit import check_rate_limit, rate_limit_headers

router = APIRouter(prefix="/auth/2fa", tags=["auth-2fa"])

# 5 attempts / 15 min per token (mirrors the login limit).
CHALLENGE_LIMIT = 5
CHALLENGE_WINDOW_S = 15 * 60


class VerifyIn(BaseModel):
    code: str


class DisableIn(BaseModel):
    password: str


class ChallengeIn(BaseModel):
    two_fa_token: str
    code: str | None = None
    backup_code: str | None = None


@router.post("/setup")
async def setup_2fa(user: dict = Depends(get_current_user)):
    """Start enrollment: generate a secret, store it pending verification."""
    db = get_db()
    full = await db.users.find_one({"id": user["id"]})
    if not full:
        raise HTTPException(404, "User not found")
    if full.get("mfa_enabled"):
        raise HTTPException(400, "Two-factor is already enabled")
    try:
        secret = t2f.generate_secret()
        enc = t2f.encrypt_secret(secret)
    except t2f.EncryptionNotConfigured as e:
        raise HTTPException(503, f"Two-factor is not configured on this server: {e}")
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"mfa_pending_secret_enc": enc, "mfa_pending_at": _now_iso()}},
    )
    return {
        "otpauth_uri": t2f.provisioning_uri(secret, full.get("email", "")),
        "secret": secret,  # manual entry fallback; also QR-encodable via the URI
    }


@router.post("/verify")
async def verify_2fa(data: VerifyIn, user: dict = Depends(get_current_user)):
    """Confirm enrollment with a TOTP code from the authenticator app."""
    db = get_db()
    full = await db.users.find_one({"id": user["id"]})
    if not full:
        raise HTTPException(404, "User not found")
    if full.get("mfa_enabled"):
        raise HTTPException(400, "Two-factor is already enabled")
    enc = full.get("mfa_pending_secret_enc")
    if not enc:
        raise HTTPException(400, "No pending enrollment — call /auth/2fa/setup first")
    try:
        secret = t2f.decrypt_secret(enc)
    except t2f.EncryptionNotConfigured as e:
        raise HTTPException(503, f"Two-factor is not configured on this server: {e}")
    if not t2f.verify_code(secret, data.code):
        raise HTTPException(401, "Invalid code — check your authenticator app and try again")
    codes = t2f.generate_backup_codes()
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {
            "mfa_enabled": True,
            "mfa_secret_enc": enc,
            "mfa_backup_hashes": [t2f.hash_backup_code(c) for c in codes],
            "mfa_enabled_at": _now_iso(),
        },
         "$unset": {"mfa_pending_secret_enc": "", "mfa_pending_at": ""}},
    )
    return {"status": "ok", "backup_codes": codes}


@router.post("/disable")
async def disable_2fa(data: DisableIn, user: dict = Depends(get_current_user)):
    """Remove 2FA. Requires the current password (proves account ownership)."""
    db = get_db()
    full = await db.users.find_one({"id": user["id"]})
    if not full:
        raise HTTPException(404, "User not found")
    if not full.get("mfa_enabled"):
        raise HTTPException(400, "Two-factor is not enabled")
    if not data.password or not verify_password(data.password, full.get("password_hash") or ""):
        raise HTTPException(401, "Password is incorrect")
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"mfa_enabled": False},
         "$unset": {"mfa_secret_enc": "", "mfa_backup_hashes": "",
                    "mfa_pending_secret_enc": "", "mfa_pending_at": "",
                    "mfa_enabled_at": ""}},
    )
    return {"status": "ok"}


@router.post("/challenge")
async def challenge_2fa(data: ChallengeIn, request: Request):
    """Exchange a 2FA token + TOTP/backup code for real auth cookies."""
    from fastapi.responses import JSONResponse

    user_id = decode_2fa_token(data.two_fa_token)
    ip = request.client.host if request.client else "unknown"
    key = f"2fa:{ip}:{user_id}"
    allowed, retry_after = await check_rate_limit(key, CHALLENGE_LIMIT, CHALLENGE_WINDOW_S)
    if not allowed:
        raise HTTPException(429, "Too many attempts. Try again later.",
                            headers=rate_limit_headers(retry_after))

    db = get_db()
    user = await db.users.find_one({"id": user_id})
    if not user or not user.get("mfa_enabled") or not user.get("mfa_secret_enc"):
        raise HTTPException(401, "Two-factor is not enabled for this account")
    try:
        secret = t2f.decrypt_secret(user["mfa_secret_enc"])
    except t2f.EncryptionNotConfigured as e:
        raise HTTPException(503, f"Two-factor is not configured on this server: {e}")

    ok = False
    used_backup_hash = None
    if data.code and t2f.verify_code(secret, data.code):
        ok = True
    elif data.backup_code:
        for h in user.get("mfa_backup_hashes") or []:
            if t2f.backup_code_matches(data.backup_code, h):
                ok = True
                used_backup_hash = h
                break
    if not ok:
        raise HTTPException(401, "Invalid code")

    if used_backup_hash:
        # Single-use: burn the backup code.
        await db.users.update_one(
            {"id": user_id}, {"$pull": {"mfa_backup_hashes": used_backup_hash}})

    access = create_access_token(user["id"], user["email"], user["role"], user.get("tenant_id"))
    refresh = create_refresh_token(user["id"])
    body = {k: v for k, v in user.items()
            if k not in ("password_hash", "_id", "mfa_secret_enc",
                         "mfa_pending_secret_enc", "mfa_backup_hashes")}
    resp = JSONResponse(body)
    set_auth_cookies(resp, access, refresh)
    return resp
