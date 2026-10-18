"""Plan seat (user) limit enforcement.

Owner-approved caps (2026-10-17, PR #97 — do not change without owner
authorization): Lite 1 / Starter 2 / Pro 12 / Empire 25 users per tenant.
Trial is 1. "enterprise" (custom) has no cap unless its plan doc sets one;
a plan whose limits explicitly store users=None is uncapped.

Resolution order for a tenant's cap (the DB is the source of truth — plan
docs are admin-managed and may be customized per deployment):

    1. tenants.plan_id -> plans doc `limits.users` (key present, may be None)
    2. models.PLAN_LIMITS[plan_key]["users"] (in-code fallback)
    3. None -> no cap

NOTE on seeded databases: seed_plans() is insert-only, so a database
seeded before PR #97 keeps the OLD limits in its plan docs (Pro 25,
Empire 100) and enforcement will honor those stored values until an
admin updates the plan docs. That is deliberate — enforcement must never
silently rewrite plan data — and is flagged for the owner in the audit
report.

"Seats in use" = user accounts belonging to the tenant. Invite creation
additionally counts pending invitations, so a tenant cannot over-invite
past its cap; acceptance re-checks against accounts alone (the race-safe
backstop).
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException

from models import PLAN_LIMITS


async def _tenant_plan_key(db, tenant_id: str) -> str:
    """Same plan-key resolution as routers/usage.py::_get_plan_limits."""
    t = await db.tenants.find_one(
        {"id": tenant_id}, {"_id": 0, "subscription_status": 1, "plan_id": 1}) or {}
    return t.get("plan_id") or ("trial" if t.get("subscription_status") != "active" else "starter")


async def get_user_limit(db, tenant_id: str) -> Optional[int]:
    """The tenant's seat cap, or None when the plan is uncapped."""
    key = await _tenant_plan_key(db, tenant_id)
    plan = await db.plans.find_one({"key": key}, {"_id": 0, "limits": 1})
    limits = (plan or {}).get("limits") or {}
    if "users" in limits:
        users = limits.get("users")
        return None if users is None else int(users)
    fallback = PLAN_LIMITS.get(key) or {}
    users = fallback.get("users")
    return None if users is None else int(users)


async def count_tenant_users(db, tenant_id: str) -> int:
    rows = await db.users.find(
        {"tenant_id": tenant_id}, {"_id": 0, "id": 1}).to_list(10000)
    return len(rows)


async def count_pending_invites(db, tenant_id: str) -> int:
    rows = await db.invitations.find(
        {"tenant_id": tenant_id, "status": "pending"}, {"_id": 0, "id": 1}).to_list(10000)
    return len(rows)


def _limit_error(plan_key: str, limit: int, used: int) -> HTTPException:
    return HTTPException(
        403,
        f"Plan seat limit reached: the '{plan_key}' plan allows up to "
        f"{limit} user(s) and {used} seat(s) are already in use. "
        "Remove a team member or upgrade the plan to add more people.",
    )


async def assert_seat_available(db, tenant_id: str, *, include_pending_invites: bool = False) -> None:
    """Raise HTTPException(403) when the tenant has no free seat.

    include_pending_invites=True is used at invite creation so the number
    of outstanding invitations cannot exceed the seats they would occupy.
    Acceptance calls it with False: pending invites are not seats yet,
    and the accepting invite is itself one of them.
    """
    limit = await get_user_limit(db, tenant_id)
    if limit is None:
        return
    used = await count_tenant_users(db, tenant_id)
    if include_pending_invites:
        used += await count_pending_invites(db, tenant_id)
    if used >= limit:
        raise _limit_error(await _tenant_plan_key(db, tenant_id), limit, used)
