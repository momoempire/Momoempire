"""Tech gamification: points, badges, streaks, and leaderboards for field techs.

Strictly positive reinforcement — there are no punitive mechanics. Techs
earn points for on-time arrivals, 5-star reviews, upsells, photo-estimate
usage, and callback-free jobs. Shop owners configure the point value of
each event type; badges are auto-awarded when count thresholds are
crossed. Everything is tenant-scoped.

Collections (all tenant-scoped):
- `gamification_events`: one doc per scored event. Idempotent on
  (tenant_id, tech_id, event_type, ref_id) — the same job/review can't
  double-score.
- `gamification_badges`: one doc per badge award (one award per badge
  per tech).
- `gamification_rules`: one doc per tenant holding point values.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin

router = APIRouter(prefix="/api/gamification", tags=["gamification"])

# Event types are part of the public API contract — keep these strings stable.
EVENT_TYPES = ("on_time", "5_star_review", "upsell", "photo_estimate", "no_callback")

DEFAULT_POINT_VALUES: Dict[str, int] = {
    "on_time": 10,
    "5_star_review": 25,
    "upsell": 20,
    "photo_estimate": 15,
    "no_callback": 30,
}

# Seeded badge catalog. Each badge counts events of one type; the award is
# written to gamification_badges the first time the threshold is crossed.
BADGE_CATALOG: List[Dict] = [
    {
        "id": "on_time_pro",
        "name": "On-Time Pro",
        "description": "10 on-time arrivals",
        "icon": "⏱️",
        "event_type": "on_time",
        "threshold": 10,
    },
    {
        "id": "five_star_machine",
        "name": "Five-Star Machine",
        "description": "25 five-star reviews",
        "icon": "⭐",
        "event_type": "5_star_review",
        "threshold": 25,
    },
    {
        "id": "closer",
        "name": "Closer",
        "description": "15 upsells",
        "icon": "🤝",
        "event_type": "upsell",
        "threshold": 15,
    },
    {
        "id": "shutterbug",
        "name": "Shutterbug",
        "description": "20 photo estimates",
        "icon": "📸",
        "event_type": "photo_estimate",
        "threshold": 20,
    },
    {
        "id": "clean_sheet",
        "name": "Clean Sheet",
        "description": "30 days with no callbacks",
        "icon": "✨",
        "event_type": "no_callback",
        "threshold": 30,
    },
]

_EVENT_LABELS = {
    "on_time": "On-time arrival",
    "5_star_review": "5-star review",
    "upsell": "Upsell",
    "photo_estimate": "Photo estimate",
    "no_callback": "Callback-free job",
}


# ---------- models ----------

class EventIn(BaseModel):
    tech_id: str = Field(min_length=1)
    tech_name: str = ""
    event_type: str
    ref_id: str = Field(min_length=1)
    notes: str = ""


class RulesIn(BaseModel):
    point_values: Dict[str, int]


# ---------- helpers ----------

def _parse_iso(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _period_start(period: str) -> Optional[datetime]:
    now = datetime.now(timezone.utc)
    if period == "week":
        return now - timedelta(days=7)
    if period == "month":
        return now - timedelta(days=30)
    return None  # "all"


def _event_in_period(doc: dict, start: Optional[datetime]) -> bool:
    if start is None:
        return True
    dt = _parse_iso(doc.get("created_at", ""))
    return bool(dt and dt >= start)


async def _get_point_values(db, tenant_id: str) -> Dict[str, int]:
    """Tenant point values: defaults merged with any owner overrides."""
    values = dict(DEFAULT_POINT_VALUES)
    doc = await db.gamification_rules.find_one({"tenant_id": tenant_id})
    if doc and isinstance(doc.get("point_values"), dict):
        for k, v in doc["point_values"].items():
            if k in EVENT_TYPES and isinstance(v, int) and v >= 0:
                values[k] = v
    return values


async def _check_and_award_badges(db, tenant_id: str, tech_id: str) -> List[Dict]:
    """Award any newly-crossed badge thresholds for a tech. Returns awards."""
    awarded: List[Dict] = []
    for badge in BADGE_CATALOG:
        already = await db.gamification_badges.find_one(
            {"tenant_id": tenant_id, "tech_id": tech_id, "badge_id": badge["id"]}
        )
        if already:
            continue
        count = len(
            await db.gamification_events.find(
                {
                    "tenant_id": tenant_id,
                    "tech_id": tech_id,
                    "event_type": badge["event_type"],
                }
            ).to_list(100000)
        )
        if count >= badge["threshold"]:
            award = {
                "id": _uuid(),
                "tenant_id": tenant_id,
                "tech_id": tech_id,
                "badge_id": badge["id"],
                "name": badge["name"],
                "icon": badge["icon"],
                "description": badge["description"],
                "awarded_at": _now_iso(),
            }
            await db.gamification_badges.insert_one(award)
            awarded.append(award)
    return awarded


def _streaks(docs: List[dict]) -> Dict[str, int]:
    """Consecutive-day scoring streaks from a tech's events (positive-only)."""
    days = sorted(
        {
            dt.date()
            for dt in (
                _parse_iso(d.get("created_at", ""))
                for d in docs
            )
            if dt is not None
        }
    )
    if not days:
        return {"current_streak_days": 0, "longest_streak_days": 0}
    today = datetime.now(timezone.utc).date()
    # Current streak may start yesterday if today's work isn't logged yet.
    cursor = today if today in days else today - timedelta(days=1)
    current = 0
    dayset = set(days)
    while cursor in dayset:
        current += 1
        cursor -= timedelta(days=1)
    longest, run, prev = 0, 0, None
    for d in days:
        run = run + 1 if prev is not None and d == prev + timedelta(days=1) else 1
        longest = max(longest, run)
        prev = d
    return {"current_streak_days": current, "longest_streak_days": longest}


async def _tech_totals(db, tenant_id: str) -> Dict[str, dict]:
    """Aggregate all tenant events into per-tech totals."""
    docs = await db.gamification_events.find({"tenant_id": tenant_id}).to_list(100000)
    week_start = _period_start("week")
    month_start = _period_start("month")
    totals: Dict[str, dict] = {}
    for d in docs:
        tid = d.get("tech_id", "")
        t = totals.setdefault(
            tid,
            {
                "tech_id": tid,
                "tech_name": d.get("tech_name", ""),
                "points": 0,
                "points_week": 0,
                "points_month": 0,
                "events": 0,
                "by_type": {et: 0 for et in EVENT_TYPES},
            },
        )
        if d.get("tech_name"):
            t["tech_name"] = d["tech_name"]
        pts = d.get("points", 0) or 0
        t["points"] += pts
        t["events"] += 1
        et = d.get("event_type")
        if et in t["by_type"]:
            t["by_type"][et] += 1
        if _event_in_period(d, week_start):
            t["points_week"] += pts
        if _event_in_period(d, month_start):
            t["points_month"] += pts
    return totals


# ---------- endpoints ----------

@router.get("/tenants/leaderboard")
async def leaderboard(
    period: str = "week",
    user: dict = Depends(require_tenant_user),
):
    """Ranked techs by points for the period (week | month | all)."""
    if period not in ("week", "month", "all"):
        raise HTTPException(400, "period must be week, month, or all")
    db = get_db()
    totals = await _tech_totals(db, user["tenant_id"])
    key = {"week": "points_week", "month": "points_month", "all": "points"}[period]
    badge_docs = await db.gamification_badges.find(
        {"tenant_id": user["tenant_id"]}
    ).to_list(100000)
    badge_counts: Dict[str, int] = {}
    for b in badge_docs:
        badge_counts[b.get("tech_id", "")] = badge_counts.get(b.get("tech_id", ""), 0) + 1
    entries = [
        {
            "tech_id": t["tech_id"],
            "tech_name": t["tech_name"] or t["tech_id"],
            "points": t[key],
            "points_all_time": t["points"],
            "badges": badge_counts.get(t["tech_id"], 0),
            "events": t["events"],
        }
        for t in totals.values()
    ]
    entries.sort(key=lambda e: (-e["points"], e["tech_name"].lower()))
    entries = [e for e in entries if e["points"] > 0]
    for i, e in enumerate(entries, 1):
        e["rank"] = i
    return {"period": period, "entries": entries}


@router.post("/tenants/events")
async def record_event(body: EventIn, user: dict = Depends(require_tenant_user)):
    """Record a scoring event. Idempotent on (tech_id, event_type, ref_id)."""
    if body.event_type not in EVENT_TYPES:
        raise HTTPException(400, f"event_type must be one of {', '.join(EVENT_TYPES)}")
    db = get_db()
    tenant_id = user["tenant_id"]
    existing = await db.gamification_events.find_one(
        {
            "tenant_id": tenant_id,
            "tech_id": body.tech_id,
            "event_type": body.event_type,
            "ref_id": body.ref_id,
        }
    )
    if existing:
        return {
            "ok": True,
            "duplicate": True,
            "event_id": existing.get("id"),
            "tech_id": body.tech_id,
            "event_type": body.event_type,
            "points": existing.get("points", 0),
            "badges_awarded": [],
        }
    values = await _get_point_values(db, tenant_id)
    doc = {
        "id": _uuid(),
        "tenant_id": tenant_id,
        "tech_id": body.tech_id,
        "tech_name": body.tech_name or "",
        "event_type": body.event_type,
        "ref_id": body.ref_id,
        "notes": body.notes,
        "points": values[body.event_type],
        "created_at": _now_iso(),
    }
    await db.gamification_events.insert_one(doc)
    awards = await _check_and_award_badges(db, tenant_id, body.tech_id)
    return {
        "ok": True,
        "duplicate": False,
        "event_id": doc["id"],
        "tech_id": body.tech_id,
        "event_type": body.event_type,
        "points": doc["points"],
        "badges_awarded": [
            {"badge_id": a["badge_id"], "name": a["name"], "icon": a["icon"]}
            for a in awards
        ],
    }


@router.get("/tenants/techs/{tech_id}")
async def tech_profile(tech_id: str, user: dict = Depends(require_tenant_user)):
    """Tech profile: points, badges, streaks, and recent events."""
    db = get_db()
    tenant_id = user["tenant_id"]
    docs = await db.gamification_events.find(
        {"tenant_id": tenant_id, "tech_id": tech_id}
    ).to_list(100000)
    week_start = _period_start("week")
    month_start = _period_start("month")
    points = sum(d.get("points", 0) or 0 for d in docs)
    by_type = {et: 0 for et in EVENT_TYPES}
    for d in docs:
        if d.get("event_type") in by_type:
            by_type[d["event_type"]] += 1
    badges = await db.gamification_badges.find(
        {"tenant_id": tenant_id, "tech_id": tech_id}
    ).to_list(1000)
    recent = sorted(docs, key=lambda d: str(d.get("created_at", "")), reverse=True)[:10]
    return {
        "tech_id": tech_id,
        "tech_name": next((d.get("tech_name") for d in docs if d.get("tech_name")), "") or tech_id,
        "points": points,
        "points_week": sum(d.get("points", 0) or 0 for d in docs if _event_in_period(d, week_start)),
        "points_month": sum(d.get("points", 0) or 0 for d in docs if _event_in_period(d, month_start)),
        "events_total": len(docs),
        "by_type": by_type,
        "badges": [
            {
                "badge_id": b.get("badge_id"),
                "name": b.get("name"),
                "icon": b.get("icon"),
                "description": b.get("description"),
                "awarded_at": b.get("awarded_at"),
            }
            for b in sorted(badges, key=lambda b: str(b.get("awarded_at", "")))
        ],
        "streaks": _streaks(docs),
        "recent_events": [
            {
                "id": d.get("id"),
                "event_type": d.get("event_type"),
                "label": _EVENT_LABELS.get(d.get("event_type"), d.get("event_type")),
                "points": d.get("points", 0),
                "ref_id": d.get("ref_id"),
                "notes": d.get("notes", ""),
                "created_at": d.get("created_at"),
            }
            for d in recent
        ],
    }


@router.get("/tenants/events")
async def recent_events(
    limit: int = 50,
    user: dict = Depends(require_tenant_user),
):
    """Recent scoring events for the tenant, newest first."""
    if limit < 1 or limit > 200:
        raise HTTPException(400, "limit must be between 1 and 200")
    db = get_db()
    docs = await db.gamification_events.find(
        {"tenant_id": user["tenant_id"]}
    ).to_list(100000)
    docs.sort(key=lambda d: str(d.get("created_at", "")), reverse=True)
    return {
        "events": [
            {
                "id": d.get("id"),
                "tech_id": d.get("tech_id"),
                "tech_name": d.get("tech_name") or d.get("tech_id"),
                "event_type": d.get("event_type"),
                "label": _EVENT_LABELS.get(d.get("event_type"), d.get("event_type")),
                "points": d.get("points", 0),
                "ref_id": d.get("ref_id"),
                "notes": d.get("notes", ""),
                "created_at": d.get("created_at"),
            }
            for d in docs[:limit]
        ]
    }


@router.get("/tenants/badges")
async def badge_catalog(user: dict = Depends(require_tenant_user)):
    """Seeded badge catalog with tenant-wide earn counts."""
    db = get_db()
    awards = await db.gamification_badges.find(
        {"tenant_id": user["tenant_id"]}
    ).to_list(100000)
    earned: Dict[str, int] = {}
    for a in awards:
        earned[a.get("badge_id", "")] = earned.get(a.get("badge_id", ""), 0) + 1
    return {
        "badges": [
            {**b, "earned_count": earned.get(b["id"], 0)} for b in BADGE_CATALOG
        ]
    }


@router.get("/tenants/rules")
async def get_rules(user: dict = Depends(require_tenant_user)):
    """Current point values per event type (defaults + owner overrides)."""
    db = get_db()
    values = await _get_point_values(db, user["tenant_id"])
    return {"point_values": values, "defaults": dict(DEFAULT_POINT_VALUES)}


@router.post("/tenants/rules")
async def set_rules(body: RulesIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Owner/admin configures point values per event type."""
    for k, v in body.point_values.items():
        if k not in EVENT_TYPES:
            raise HTTPException(400, f"unknown event_type '{k}' (valid: {', '.join(EVENT_TYPES)})")
        if not isinstance(v, int) or v < 0:
            raise HTTPException(400, f"point value for '{k}' must be a non-negative integer")
    db = get_db()
    tenant_id = user["tenant_id"]
    current = await _get_point_values(db, tenant_id)
    current.update(body.point_values)
    await db.gamification_rules.update_one(
        {"tenant_id": tenant_id},
        {"$set": {"tenant_id": tenant_id, "point_values": current,
                  "updated_at": _now_iso(), "updated_by": user.get("id")}},
        upsert=True,
    )
    return {"ok": True, "point_values": current}
