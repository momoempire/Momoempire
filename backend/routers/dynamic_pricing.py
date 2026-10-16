"""Dynamic pricing engine: rule-based, time/day/season/demand price adjustments.

The pricebook NEVER changes — dynamic pricing is a computed overlay. A quote
pulls base prices from `pricebook_items`, applies enabled pricing rules in
priority order (lowest priority number fires first), and returns a full
line-by-line breakdown of what moved and why.

Guardrails (the shop is always in control):
- Shop-level bounds live in `tenant_settings.dynamic_pricing`:
  `max_markup_pct` / `max_discount_pct` bound percent rules at create time;
  `min_price` / `max_price` clamp every computed line price at quote time.
- Optional per-rule caps (`cap.min_price` / `cap.max_price`) clamp that rule's
  effect on a line.
- Every quote is written to `pricing_quote_log` with the base price, every
  adjustment, and the final price, so any quote can be audited — and "reverted"
  (the revert marks the quote; the pricebook was never touched, so there is
  nothing to un-apply).
- Rules have an `enabled` kill switch and fire counters.

Collections (all tenant-scoped):
- `pricing_rules`: the rule catalog (collection requested by spec).
- `pricing_quote_log`: one doc per computed quote (full breakdown).
- `tenant_settings.dynamic_pricing`: shop bounds (one sub-document).
- reads `pricebook_items` for base prices and `appointments` for /simulate.
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin

router = APIRouter(prefix="/tenants/dynamic-pricing", tags=["dynamic-pricing"])

DEMAND_LEVELS = ["low", "normal", "high", "surge"]
SEASON_MONTHS = {
    "spring": [3, 4, 5],
    "summer": [6, 7, 8],
    "fall": [9, 10, 11],
    "winter": [12, 1, 2],
}
# Appointment counts per day -> demand label. Used both when labeling real
# booking history and when synthesizing stand-in data for /simulate.
DEMAND_BY_DAILY_COUNT = [(11, "surge"), (7, "high"), (3, "normal")]

DEFAULT_BOUNDS = {
    "enabled": True,
    "max_markup_pct": 30.0,
    "max_discount_pct": 20.0,
    "min_price": 0.0,
    "max_price": 10000.0,
}


# ---------- pydantic models ----------

class TimeWindowIn(BaseModel):
    start: str = Field(..., description='24h "HH:MM"')
    end: str = Field(..., description='24h "HH:MM"; may be earlier than start to wrap midnight')


class TriggerIn(BaseModel):
    """All provided criteria must hold for the rule to fire (AND semantics)."""
    day_of_week: Optional[List[int]] = Field(default=None, description="0=Mon..6=Sun")
    time_window: Optional[TimeWindowIn] = None
    months: Optional[List[int]] = Field(default=None, description="1..12")
    season: Optional[str] = Field(default=None, description="spring|summer|fall|winter")
    demand_level: Optional[str] = Field(default=None, description="fires when demand is AT LEAST this")


class AdjustmentIn(BaseModel):
    mode: str = Field(..., description="percent|flat")
    value: float = Field(..., description="percent: e.g. 15 => +15% (negative = discount); flat: dollars")


class CapIn(BaseModel):
    min_price: Optional[float] = None
    max_price: Optional[float] = None


class RuleIn(BaseModel):
    name: str
    enabled: bool = True
    priority: int = 10
    trigger: TriggerIn
    adjustment: AdjustmentIn
    cap: Optional[CapIn] = None


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None
    trigger: Optional[TriggerIn] = None
    adjustment: Optional[AdjustmentIn] = None
    cap: Optional[CapIn] = None


class BoundsIn(BaseModel):
    enabled: bool = True
    max_markup_pct: float = Field(default=30.0, ge=0)
    max_discount_pct: float = Field(default=20.0, ge=0)
    min_price: float = Field(default=0.0, ge=0)
    max_price: float = Field(default=10000.0, gt=0)


class QuoteSelectionIn(BaseModel):
    item_id: str
    qty: float = 1.0
    option_ids: List[str] = Field(default_factory=list)


class QuoteContextIn(BaseModel):
    demand_level: Optional[str] = None
    bookings_today: Optional[int] = None
    capacity_today: Optional[int] = None


class QuoteIn(BaseModel):
    selections: List[QuoteSelectionIn]
    at: Optional[str] = Field(default=None, description="ISO datetime the job happens at; defaults to now")
    context: QuoteContextIn = Field(default_factory=QuoteContextIn)
    customer_name: str = ""
    title: str = ""


# ---------- validation helpers ----------

def _parse_hhmm(value: str) -> int:
    """'HH:MM' -> minutes since midnight. Raises ValueError."""
    hh, mm = value.split(":")
    hh_i, mm_i = int(hh), int(mm)
    if not (0 <= hh_i <= 23 and 0 <= mm_i <= 59):
        raise ValueError("hour/minute out of range")
    return hh_i * 60 + mm_i


def _normalize_trigger(trigger: TriggerIn) -> dict:
    """Validate a trigger and normalize to a stored dict (seasons -> months)."""
    t = trigger
    criteria = 0
    doc: dict = {}

    if t.day_of_week is not None:
        days = list(t.day_of_week)
        if not days or any(not isinstance(d, int) or d < 0 or d > 6 for d in days):
            raise HTTPException(400, "day_of_week must be a non-empty list of ints 0 (Mon)..6 (Sun)")
        doc["day_of_week"] = sorted(set(days))
        criteria += 1

    if t.time_window is not None:
        try:
            _parse_hhmm(t.time_window.start)
            _parse_hhmm(t.time_window.end)
        except (ValueError, AttributeError):
            raise HTTPException(400, 'time_window start/end must be "HH:MM" (24h)')
        doc["time_window"] = {"start": t.time_window.start, "end": t.time_window.end}
        criteria += 1

    months: List[int] = []
    if t.months is not None:
        months = list(t.months)
        if not months or any(not isinstance(m, int) or m < 1 or m > 12 for m in months):
            raise HTTPException(400, "months must be a non-empty list of ints 1..12")
    if t.season is not None:
        season = str(t.season).lower()
        if season not in SEASON_MONTHS:
            raise HTTPException(400, "season must be one of: spring, summer, fall, winter")
        months = sorted(set(months) | set(SEASON_MONTHS[season]))
    if months:
        doc["months"] = sorted(set(months))
        criteria += 1

    if t.demand_level is not None:
        level = str(t.demand_level).lower()
        if level not in DEMAND_LEVELS:
            raise HTTPException(400, f"demand_level must be one of {DEMAND_LEVELS}")
        doc["demand_level"] = level
        criteria += 1

    if criteria == 0:
        raise HTTPException(
            400,
            "Trigger needs at least one criterion: day_of_week, time_window, months/season, or demand_level",
        )
    return doc


def _validate_adjustment(mode: str, value: float, bounds: dict) -> None:
    if mode not in ("percent", "flat"):
        raise HTTPException(400, "adjustment.mode must be 'percent' or 'flat'")
    if value == 0:
        raise HTTPException(400, "adjustment.value cannot be 0")
    if mode == "percent":
        lo = -float(bounds.get("max_discount_pct", DEFAULT_BOUNDS["max_discount_pct"]))
        hi = float(bounds.get("max_markup_pct", DEFAULT_BOUNDS["max_markup_pct"]))
        if not (lo <= float(value) <= hi):
            raise HTTPException(
                400,
                f"Percent adjustment {value}% is outside shop bounds "
                f"(allowed {lo}%..{hi}%). Change the rule or the shop bounds.",
            )
    else:
        span = float(bounds.get("max_price", DEFAULT_BOUNDS["max_price"])) - float(
            bounds.get("min_price", DEFAULT_BOUNDS["min_price"])
        )
        if abs(float(value)) > span:
            raise HTTPException(
                400,
                f"Flat adjustment ${value} exceeds the shop price span "
                f"(${span:.2f} between min_price and max_price).",
            )


def _validate_cap(cap: Optional[CapIn]) -> Optional[dict]:
    if cap is None:
        return None
    out: dict = {}
    if cap.min_price is not None:
        if cap.min_price < 0:
            raise HTTPException(400, "cap.min_price cannot be negative")
        out["min_price"] = float(cap.min_price)
    if cap.max_price is not None:
        if cap.max_price < 0:
            raise HTTPException(400, "cap.max_price cannot be negative")
        out["max_price"] = float(cap.max_price)
    if "min_price" in out and "max_price" in out and out["min_price"] > out["max_price"]:
        raise HTTPException(400, "cap.min_price cannot exceed cap.max_price")
    return out or None


async def _get_bounds(db, tenant_id: str) -> dict:
    """Shop-level pricing bounds; falls back to defaults when never set."""
    settings = await db.tenant_settings.find_one({"tenant_id": tenant_id}, {"_id": 0})
    merged = dict(DEFAULT_BOUNDS)
    if settings and isinstance(settings.get("dynamic_pricing"), dict):
        merged.update(settings["dynamic_pricing"])
    return merged


def _public_rule(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "name": doc.get("name", ""),
        "enabled": bool(doc.get("enabled", True)),
        "priority": int(doc.get("priority", 10)),
        "trigger": doc.get("trigger") or {},
        "adjustment": doc.get("adjustment") or {},
        "cap": doc.get("cap"),
        "fire_count": int(doc.get("fire_count") or 0),
        "last_fired_at": doc.get("last_fired_at"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


async def _get_rule(db, rule_id: str, tenant_id: str) -> dict:
    rule = await db.pricing_rules.find_one(
        {"id": rule_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if not rule:
        raise HTTPException(404, "Pricing rule not found")
    return rule


# ---------- matching / computation ----------

def _time_in_window(now_min: int, start_min: int, end_min: int) -> bool:
    """Midnight-wrapping time-window check (minutes since midnight)."""
    if start_min <= end_min:
        return start_min <= now_min < end_min
    return now_min >= start_min or now_min < end_min


def _trigger_matches(trigger: dict, dt: datetime, demand_level: str) -> bool:
    """True when ALL of the trigger's criteria hold."""
    if "day_of_week" in trigger and dt.weekday() not in trigger["day_of_week"]:
        return False
    if "time_window" in trigger:
        tw = trigger["time_window"]
        now_min = dt.hour * 60 + dt.minute
        if not _time_in_window(now_min, _parse_hhmm(tw["start"]), _parse_hhmm(tw["end"])):
            return False
    if "months" in trigger and dt.month not in trigger["months"]:
        return False
    if "demand_level" in trigger:
        need = DEMAND_LEVELS.index(trigger["demand_level"])
        have = DEMAND_LEVELS.index(demand_level)
        if have < need:
            return False
    return True


def _resolve_demand(context: QuoteContextIn) -> str:
    if context.demand_level:
        level = str(context.demand_level).lower()
        if level not in DEMAND_LEVELS:
            raise HTTPException(400, f"context.demand_level must be one of {DEMAND_LEVELS}")
        return level
    if context.bookings_today is not None and context.capacity_today:
        ratio = float(context.bookings_today) / float(context.capacity_today)
        if ratio >= 0.9:
            return "surge"
        if ratio >= 0.7:
            return "high"
        if ratio >= 0.4:
            return "normal"
        return "low"
    return "normal"


def _apply_rule_to_line(price: float, rule: dict) -> tuple:
    """Apply one rule to a running line price. Returns (new_price, amount, clamped)."""
    adj = rule.get("adjustment") or {}
    mode = adj.get("mode")
    value = float(adj.get("value") or 0)
    before = round(price, 2)
    amount = round(price * value / 100.0, 2) if mode == "percent" else round(value, 2)
    price = round(price + amount, 2)

    clamped = False
    cap = rule.get("cap") or {}
    if cap.get("min_price") is not None and price < float(cap["min_price"]):
        price, clamped = round(float(cap["min_price"]), 2), True
    if cap.get("max_price") is not None and price > float(cap["max_price"]):
        price, clamped = round(float(cap["max_price"]), 2), True
    return price, amount, clamped, before


async def _compute_quote(
    db,
    tenant_id: str,
    selections: List[QuoteSelectionIn],
    at_iso: Optional[str],
    context: QuoteContextIn,
) -> dict:
    """Core quote engine: shared by POST /quote (logs) and unit-testable logic."""
    if not selections:
        raise HTTPException(400, "Select at least one pricebook item")
    bounds = await _get_bounds(db, tenant_id)

    if at_iso:
        try:
            dt = datetime.fromisoformat(at_iso)
        except ValueError:
            raise HTTPException(400, "at must be an ISO datetime")
    else:
        dt = datetime.now()
    demand_level = _resolve_demand(context)

    item_ids = [s.item_id for s in selections]
    catalog = await db.pricebook_items.find(
        {"tenant_id": tenant_id, "id": {"$in": item_ids}, "active": True}, {"_id": 0}
    ).to_list(500)
    by_id = {c["id"]: c for c in catalog}

    rule_rows = await db.pricing_rules.find(
        {"tenant_id": tenant_id, "enabled": True}, {"_id": 0}
    ).to_list(1000)
    # Priority order: lowest priority number applies first (then oldest).
    rule_rows.sort(key=lambda r: (int(r.get("priority", 10)), str(r.get("created_at") or "")))
    matching = [r for r in rule_rows if _trigger_matches(r.get("trigger") or {}, dt, demand_level)]

    lines: List[dict] = []
    fired: Dict[str, dict] = {}
    shop_min = float(bounds.get("min_price", 0.0))
    shop_max = float(bounds.get("max_price", 10000.0))
    clamped_lines = 0

    for sel in selections:
        item = by_id.get(sel.item_id)
        if not item:
            raise HTTPException(404, f"Pricebook item not found or inactive: {sel.item_id}")
        option_map = {o.get("id"): o for o in (item.get("options") or [])}
        add_on = 0.0
        for oid in sel.option_ids:
            opt = option_map.get(oid)
            if not opt:
                raise HTTPException(404, f"Option {oid} not found on item {item['id']}")
            add_on += float(opt.get("price") or 0)
        qty = float(sel.qty or 1)
        if qty <= 0:
            raise HTTPException(400, "Quantity must be positive")

        base_unit = round(float(item.get("price") or 0) + add_on, 2)
        price = base_unit
        breakdown: List[dict] = []
        line_rule_ids: List[str] = []

        for rule in matching:
            price, amount, rule_clamped, before = _apply_rule_to_line(price, rule)
            breakdown.append({
                "rule_id": rule["id"],
                "rule_name": rule.get("name", ""),
                "mode": (rule.get("adjustment") or {}).get("mode"),
                "value": (rule.get("adjustment") or {}).get("value"),
                "amount": amount,
                "price_before": before,
                "price_after": price,
                "rule_clamped": rule_clamped,
            })
            line_rule_ids.append(rule["id"])
            fired[rule["id"]] = {"id": rule["id"], "name": rule.get("name", "")}

        shop_clamped = False
        if price < shop_min:
            price, shop_clamped = round(shop_min, 2), True
        if price > shop_max:
            price, shop_clamped = round(shop_max, 2), True
        if shop_clamped:
            clamped_lines += 1

        lines.append({
            "item_id": item["id"],
            "name": item.get("name", ""),
            "qty": qty,
            "base_unit": base_unit,
            "breakdown": breakdown,
            "final_unit": price,
            "line_total": round(price * qty, 2),
            "rules_fired": line_rule_ids,
            "shop_clamped": shop_clamped,
        })

    subtotal_base = round(sum(l["base_unit"] * l["qty"] for l in lines), 2)
    subtotal_final = round(sum(l["line_total"] for l in lines), 2)
    return {
        "dt": dt.isoformat(),
        "demand_level": demand_level,
        "bounds": bounds,
        "lines": lines,
        "subtotal_base": subtotal_base,
        "subtotal_final": subtotal_final,
        "adjustments_total": round(subtotal_final - subtotal_base, 2),
        "rules_fired": list(fired.values()),
        "clamped_lines": clamped_lines,
        "matched_rule_ids": list(fired.keys()),
    }


# ---------- endpoints ----------

@router.get("/rules")
async def list_rules(user: dict = Depends(require_tenant_user)):
    """All pricing rules for this tenant, priority order (lowest first)."""
    db = get_db()
    rows = await db.pricing_rules.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}
    ).sort("priority", 1).to_list(1000)
    return {"rules": [_public_rule(r) for r in rows], "count": len(rows)}


@router.post("/rules")
async def create_rule(data: RuleIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Create a rule. Percent/flat adjustments are validated against the
    shop bounds — the AI can never price outside what the shop allows."""
    if not data.name.strip():
        raise HTTPException(400, "Rule name is required")
    db = get_db()
    bounds = await _get_bounds(db, user["tenant_id"])
    trigger = _normalize_trigger(data.trigger)
    _validate_adjustment(data.adjustment.mode, float(data.adjustment.value), bounds)
    cap = _validate_cap(data.cap)
    now = _now_iso()
    doc = {
        "id": _uuid(),
        "tenant_id": user["tenant_id"],
        "name": data.name.strip(),
        "enabled": bool(data.enabled),
        "priority": int(data.priority),
        "trigger": trigger,
        "adjustment": {"mode": data.adjustment.mode, "value": float(data.adjustment.value)},
        "cap": cap,
        "fire_count": 0,
        "last_fired_at": None,
        "created_at": now,
        "updated_at": now,
    }
    await db.pricing_rules.insert_one(doc)
    return _public_rule(doc)


@router.get("/rules/performance")
async def rules_performance(user: dict = Depends(require_tenant_user)):
    """Fire counts per rule — how often each rule has actually adjusted a quote."""
    db = get_db()
    tenant_id = user["tenant_id"]
    rows = await db.pricing_rules.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(1000)
    quotes = await db.pricing_quote_log.find({"tenant_id": tenant_id}, {"_id": 0, "id": 1}).to_list(100000)
    return {
        "rules": [
            {
                "id": r.get("id"),
                "name": r.get("name", ""),
                "enabled": bool(r.get("enabled", True)),
                "priority": int(r.get("priority", 10)),
                "fire_count": int(r.get("fire_count") or 0),
                "last_fired_at": r.get("last_fired_at"),
            }
            for r in sorted(rows, key=lambda x: (int(x.get("priority", 10)), str(x.get("created_at") or "")))
        ],
        "quotes_logged": len(quotes),
    }


@router.put("/rules/{rule_id}")
async def update_rule(
    rule_id: str, data: RuleUpdate, user: dict = Depends(require_tenant_owner_or_admin)
):
    """Update a rule. Adjustment changes are re-validated against shop bounds."""
    db = get_db()
    tenant_id = user["tenant_id"]
    rule = await _get_rule(db, rule_id, tenant_id)
    bounds = await _get_bounds(db, tenant_id)

    patch: dict = {}
    if data.name is not None:
        if not data.name.strip():
            raise HTTPException(400, "Rule name cannot be blank")
        patch["name"] = data.name.strip()
    if data.enabled is not None:
        patch["enabled"] = bool(data.enabled)
    if data.priority is not None:
        patch["priority"] = int(data.priority)
    if data.trigger is not None:
        patch["trigger"] = _normalize_trigger(data.trigger)
    mode = data.adjustment.mode if data.adjustment else (rule.get("adjustment") or {}).get("mode")
    value = float(data.adjustment.value) if data.adjustment else float((rule.get("adjustment") or {}).get("value") or 0)
    if data.adjustment is not None:
        _validate_adjustment(mode, value, bounds)
        patch["adjustment"] = {"mode": mode, "value": value}
    if data.cap is not None:
        patch["cap"] = _validate_cap(data.cap)
    patch["updated_at"] = _now_iso()

    await db.pricing_rules.update_one({"id": rule_id, "tenant_id": tenant_id}, {"$set": patch})
    updated = await _get_rule(db, rule_id, tenant_id)
    return _public_rule(updated)


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    await _get_rule(db, rule_id, user["tenant_id"])
    await db.pricing_rules.delete_one({"id": rule_id, "tenant_id": user["tenant_id"]})
    return {"ok": True, "id": rule_id}


@router.get("/bounds")
async def get_bounds(user: dict = Depends(require_tenant_user)):
    """Shop-level pricing bounds (what keeps the AI from pricing absurdly)."""
    db = get_db()
    return {"bounds": await _get_bounds(db, user["tenant_id"])}


@router.put("/bounds")
async def set_bounds(data: BoundsIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Set the shop's pricing bounds (owner/admin). Existing rules are NOT
    retro-validated — rules that now exceed bounds simply fail validation on
    their next edit."""
    db = get_db()
    if data.min_price >= data.max_price:
        raise HTTPException(400, "min_price must be less than max_price")
    bounds = {
        "enabled": bool(data.enabled),
        "max_markup_pct": float(data.max_markup_pct),
        "max_discount_pct": float(data.max_discount_pct),
        "min_price": float(data.min_price),
        "max_price": float(data.max_price),
    }
    await db.tenant_settings.update_one(
        {"tenant_id": user["tenant_id"]},
        {"$set": {"dynamic_pricing": bounds, "updated_at": _now_iso()}},
        upsert=True,
    )
    return {"bounds": bounds}


@router.post("/quote")
async def compute_quote(data: QuoteIn, user: dict = Depends(require_tenant_user)):
    """Compute a live quote: base pricebook prices + matching rules in priority
    order, with a full line-by-line breakdown. The quote is logged for audit;
    the pricebook is never modified."""
    db = get_db()
    tenant_id = user["tenant_id"]
    result = await _compute_quote(db, tenant_id, data.selections, data.at, data.context)

    quote_id = _uuid()
    now = _now_iso()
    log_doc = {
        "id": quote_id,
        "tenant_id": tenant_id,
        "at": result["dt"],
        "demand_level": result["demand_level"],
        "customer_name": data.customer_name,
        "title": data.title,
        "selections": [
            {"item_id": s.item_id, "qty": float(s.qty or 1), "option_ids": list(s.option_ids)}
            for s in data.selections
        ],
        "lines": result["lines"],
        "subtotal_base": result["subtotal_base"],
        "subtotal_final": result["subtotal_final"],
        "adjustments_total": result["adjustments_total"],
        "rules_fired": result["rules_fired"],
        "guardrails": {
            "min_price": result["bounds"].get("min_price"),
            "max_price": result["bounds"].get("max_price"),
            "clamped_lines": result["clamped_lines"],
        },
        "reverted": False,
        "reverted_at": None,
        "created_by": user.get("id"),
        "created_at": now,
    }
    await db.pricing_quote_log.insert_one(log_doc)

    # Rule performance counters.
    for rid in result["matched_rule_ids"]:
        await db.pricing_rules.update_one(
            {"id": rid, "tenant_id": tenant_id},
            {"$inc": {"fire_count": 1}, "$set": {"last_fired_at": now}},
        )

    return {
        "id": quote_id,
        "at": result["dt"],
        "demand_level": result["demand_level"],
        "lines": result["lines"],
        "subtotal_base": result["subtotal_base"],
        "subtotal_final": result["subtotal_final"],
        "adjustments_total": result["adjustments_total"],
        "rules_fired": result["rules_fired"],
        "clamped_lines": result["clamped_lines"],
        "logged_at": now,
    }


@router.get("/quotes")
async def list_quotes(user: dict = Depends(require_tenant_user), limit: int = 50):
    """Logged quotes, newest first — the audit trail of every dynamic price."""
    db = get_db()
    limit = max(1, min(int(limit), 200))
    rows = await db.pricing_quote_log.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}
    ).sort("created_at", -1).to_list(limit)
    return {"quotes": rows, "count": len(rows)}


@router.put("/quotes/{quote_id}/revert")
async def revert_quote(quote_id: str, user: dict = Depends(require_tenant_owner_or_admin)):
    """Mark a logged quote as reverted (honor original pricing). The pricebook
    was never touched, so reversal is a bookkeeping flag on the quote itself —
    adjustments are computed overlays, fully reversible by definition."""
    db = get_db()
    quote = await db.pricing_quote_log.find_one(
        {"id": quote_id, "tenant_id": user["tenant_id"]}, {"_id": 0}
    )
    if not quote:
        raise HTTPException(404, "Quote not found")
    now = _now_iso()
    await db.pricing_quote_log.update_one(
        {"id": quote_id, "tenant_id": user["tenant_id"]},
        {"$set": {"reverted": True, "reverted_at": now, "reverted_by": user.get("id")}},
    )
    quote.update({"reverted": True, "reverted_at": now, "reverted_by": user.get("id")})
    return quote


# ---------- simulation ----------

def _extract_appt_dt(doc: dict) -> Optional[datetime]:
    for key in ("start", "start_time", "scheduled_at", "created_at"):
        val = doc.get(key)
        if not val:
            continue
        try:
            return datetime.fromisoformat(str(val))
        except (ValueError, TypeError):
            continue
    return None


def _demand_label_for_count(count: int) -> str:
    for threshold, label in DEMAND_BY_DAILY_COUNT:
        if count >= threshold:
            return label
    return "low"


def _synthetic_appointments(tenant_id: str, days: int) -> List[dict]:
    """Seeded pseudo-history so /simulate works with zero real bookings.
    Weekdays get more bookings; a few spike days create high/surge demand so
    demand triggers get a fair shot at firing."""
    seed = int(hashlib.sha256(tenant_id.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    appts: List[dict] = []
    now = datetime.now()
    tier_choices = ["low", "low", "normal", "normal", "normal", "high", "surge"]
    count_range = {"low": (1, 3), "normal": (3, 6), "high": (7, 10), "surge": (11, 15)}
    for d in range(days):
        day = now - timedelta(days=d)
        tier = rng.choice(tier_choices)
        lo, hi = count_range[tier]
        n = rng.randint(lo, hi)
        if day.weekday() >= 5:
            n = max(1, n - 2)  # weekends run lighter
        for _ in range(n):
            hour = rng.choices([9, 10, 11, 13, 14, 15, 17, 18, 19], weights=[3, 4, 4, 4, 4, 3, 2, 2, 1])[0]
            appts.append({
                "dt": day.replace(hour=hour, minute=rng.choice([0, 15, 30, 45]), second=0, microsecond=0),
                "demand": tier,
            })
    return appts


@router.get("/simulate")
async def simulate_rule(
    user: dict = Depends(require_tenant_user),
    rule_id: Optional[str] = None,
    window_days: int = 30,
    day_of_week: Optional[str] = None,
    time_start: Optional[str] = None,
    time_end: Optional[str] = None,
    months: Optional[str] = None,
    season: Optional[str] = None,
    demand_level: Optional[str] = None,
):
    """Preview how often a rule WOULD have fired against the last N days of
    bookings (defaults 30). Pass rule_id to test a saved rule, or pass trigger
    criteria directly to preview an unsaved rule. Uses synthetic, seeded booking
    data when the shop has no real appointment history."""
    db = get_db()
    tenant_id = user["tenant_id"]
    window_days = max(1, min(int(window_days or 30), 90))

    if rule_id:
        rule = await _get_rule(db, rule_id, tenant_id)
        trigger = rule.get("trigger") or {}
        rule_name = rule.get("name", "")
    else:
        if (time_start or time_end) and not (time_start and time_end):
            raise HTTPException(400, "time_start and time_end must be provided together")
        try:
            days = [int(x) for x in day_of_week.split(",")] if day_of_week else None
            mons = [int(x) for x in months.split(",")] if months else None
        except ValueError:
            raise HTTPException(400, "day_of_week and months must be comma-separated ints")
        t = TriggerIn(
            day_of_week=days,
            time_window=TimeWindowIn(start=time_start, end=time_end) if time_start else None,
            months=mons,
            season=season,
            demand_level=demand_level,
        )
        trigger = _normalize_trigger(t)
        rule_name = "preview"

    cutoff = datetime.now() - timedelta(days=window_days)
    rows = await db.appointments.find(
        {"tenant_id": tenant_id}, {"_id": 0}
    ).to_list(10000)

    real: List[dict] = []
    for r in rows:
        dt = _extract_appt_dt(r)
        if dt and dt >= cutoff:
            real.append({"dt": dt})

    synthetic = False
    if not real:
        synthetic = True
        real = _synthetic_appointments(tenant_id, window_days)

    # Demand labels per day (only needed for real history; synthetic carries them).
    if not synthetic:
        counts: Dict[str, int] = {}
        for a in real:
            key = a["dt"].date().isoformat()
            counts[key] = counts.get(key, 0) + 1
        for a in real:
            a["demand"] = _demand_label_for_count(counts[a["dt"].date().isoformat()])

    fired = 0
    per_day: Dict[str, dict] = {}
    for a in real:
        dt = a["dt"]
        if _trigger_matches(trigger, dt, a.get("demand", "normal")):
            fired += 1
        key = dt.date().isoformat()
        day = per_day.setdefault(key, {"count": 0, "demand": a.get("demand", "normal"), "fired": 0})
        day["count"] += 1
        if _trigger_matches(trigger, dt, a.get("demand", "normal")):
            day["fired"] += 1

    total = len(real)
    return {
        "rule_id": rule_id,
        "rule_name": rule_name,
        "trigger": trigger,
        "window_days": window_days,
        "appointments_evaluated": total,
        "would_fire": fired,
        "fire_rate": round(fired / total, 4) if total else 0.0,
        "synthetic_data": synthetic,
        "note": (
            "No appointment history found — evaluated against seeded synthetic "
            "booking data. Connect real bookings for a true picture."
            if synthetic else
            f"Evaluated against {total} real appointments in the last {window_days} days."
        ),
        "per_day": [
            {"date": k, **v} for k, v in sorted(per_day.items())
        ],
    }
