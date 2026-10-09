"""Auth: password hashing, JWT, dependencies, tenant isolation."""
import os
import bcrypt
import jwt
from datetime import datetime, timezone, timedelta
from fastapi import Request, HTTPException, Depends
from db import get_db

JWT_ALGORITHM = "HS256"
ACCESS_TTL_MIN = 60 * 24  # 24h for simpler UX
REFRESH_TTL_DAYS = 30


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def _secret() -> str:
    return os.environ["JWT_SECRET"]


def create_access_token(user_id: str, email: str, role: str, tenant_id: str | None) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "tenant_id": tenant_id,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TTL_MIN),
        "type": "access",
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=REFRESH_TTL_DAYS),
        "type": "refresh",
    }
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def set_auth_cookies(response, access: str, refresh: str):
    response.set_cookie("access_token", access, httponly=True, secure=True,
                        samesite="none", max_age=ACCESS_TTL_MIN * 60, path="/")
    response.set_cookie("refresh_token", refresh, httponly=True, secure=True,
                        samesite="none", max_age=REFRESH_TTL_DAYS * 86400, path="/")


def clear_auth_cookies(response):
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/")


# While a user must change their password, only these API paths are allowed.
MUST_CHANGE_ALLOWED_PATHS = frozenset({
    "/api/auth/me",
    "/api/auth/logout",
    "/api/auth/set-password",
    "/api/auth/refresh",
})


def _enforce_must_change(request: Request, user: dict) -> dict:
    if user and user.get("must_change_password"):
        path = request.url.path.rstrip("/") or "/"
        if path not in MUST_CHANGE_ALLOWED_PATHS:
            raise HTTPException(403, "Password change required before continuing")
    return user


async def get_current_user(request: Request) -> dict:
    return _enforce_must_change(request, await _load_current_user(request))


async def _load_current_user(request: Request) -> dict:
    db = get_db()
    # 1) Try Emergent Google session cookie first
    session_token = request.cookies.get("session_token")
    if session_token:
        sess = await db.user_sessions.find_one({"session_token": session_token})
        if sess:
            from timeutil import as_utc  # EMP-W-CF-026: one normalizer for every stored expiry
            exp = as_utc(sess.get("expires_at"))
            if exp and exp >= datetime.now(timezone.utc):
                user = await db.users.find_one({"id": sess["user_id"]}, {"_id": 0, "password_hash": 0})
                if user:
                    return user

    # 2) Fallback to JWT access cookie / Authorization bearer
    token = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(401, "Invalid token type")
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")
    user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0, "password_hash": 0})
    if not user:
        raise HTTPException(401, "User not found")
    return user


async def require_platform_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "platform_admin":
        raise HTTPException(403, "Platform admin only")
    return user


async def require_tenant_user(user: dict = Depends(get_current_user)) -> dict:
    if not user.get("tenant_id"):
        raise HTTPException(403, "No tenant assigned")
    return user


async def require_tenant_owner_or_admin(user: dict = Depends(require_tenant_user)) -> dict:
    if user.get("role") not in {"owner", "admin"}:
        raise HTTPException(403, "Owner/admin role required")
    return user
