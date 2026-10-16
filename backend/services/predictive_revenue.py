"""Predictive revenue engine: replacement/upsell scoring for equipment records.

Deterministic rules today — age-vs-lifespan ratio, recent repair frequency
from the service timeline, and a seasonal uplift (AC before summer, furnace
before winter). `TODO(LIVE)`: replace `replacement_score` with a trained model
once enough anonymized lifecycle data exists; the rule breakdown is kept in
the opportunity payload so the ML cutover stays explainable.

Zero network: pure functions + the injected `now`/`month` args the router
supplies. Tests pin determinism by passing fixed dates.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional


# ---------- lifespan table (years of useful life per equipment type) ----------

EQUIPMENT_LIFESPAN_YEARS: Dict[str, int] = {
    "furnace": 18,
    "ac_condenser": 15,
    "air_handler": 15,
    "heat_pump": 14,
    "thermostat": 10,
    "water_heater": 11,
    "boiler": 20,
    "electrical_panel": 25,
    "generator": 18,
    "garbage_disposal": 10,
    "well_pump": 12,
    "septic_system": 25,
    "other": 12,
}

DEFAULT_LIFESPAN_YEARS = 12


def expected_lifespan_years(equipment_type: str) -> int:
    return EQUIPMENT_LIFESPAN_YEARS.get((equipment_type or "").strip().lower(),
                                       DEFAULT_LIFESPAN_YEARS)


# ---------- age ----------

def age_years(install_date: Optional[str], now: Optional[datetime] = None) -> float:
    """Age in years from an ISO install date. Unknown/invalid → 0.0."""
    if not install_date:
        return 0.0
    try:
        installed = datetime.fromisoformat(str(install_date).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 0.0
    ref = now or datetime.now(timezone.utc)
    if installed.tzinfo is None:
        installed = installed.replace(tzinfo=timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    days = (ref - installed).days
    return round(max(0.0, days / 365.25), 2)


# ---------- repair frequency ----------

REPAIR_KEYWORDS = (
    "repair", "replaced", "replacement", "install", "broken", "failed",
    "failure", "faulty", "leak", "crack", "not working", "burned out",
    "burnout", "compressor", "board", "motor", "ignitor",
)


def count_recent_repairs(events: List[dict], now: Optional[datetime] = None,
                         window_days: int = 365) -> int:
    """Repair-flagged service events inside the trailing window.

    Descriptions are keyword-scored (deterministic); routine maintenance
    notes ("tune-up", "cleaned", "inspected") are not counted.
    """
    ref = now or datetime.now(timezone.utc)
    cutoff = ref - _timedelta(days=window_days)
    hits = 0
    for e in events:
        desc = str(e.get("description") or "").lower()
        if not any(k in desc for k in REPAIR_KEYWORDS):
            continue
        svc_date = _parse_date(e.get("service_date"))
        if svc_date is None:
            hits += 1  # undated event: count it rather than silently drop it
        elif svc_date >= cutoff:
            hits += 1
    return hits


def _timedelta(**kwargs):
    from datetime import timedelta
    return timedelta(**kwargs)


def _parse_date(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------- season factor ----------

COOLING_TYPES = {"ac_condenser", "air_handler", "heat_pump"}
HEATING_TYPES = {"furnace", "boiler", "heat_pump"}

# Month → uplift for cooling demand (selling AC work before/during summer)
_COOLING_SEASON = {
    1: 0.0, 2: 0.0, 3: 0.3, 4: 0.6, 5: 0.8, 6: 1.0,
    7: 1.0, 8: 0.9, 9: 0.5, 10: 0.0, 11: 0.0, 12: 0.0,
}
# Month → uplift for heating demand (selling furnace work before/during winter)
_HEATING_SEASON = {
    1: 0.9, 2: 0.7, 3: 0.3, 4: 0.0, 5: 0.0, 6: 0.0,
    7: 0.0, 8: 0.0, 9: 0.5, 10: 0.8, 11: 1.0, 12: 1.0,
}


def season_factor(equipment_type: str, month: Optional[int] = None) -> float:
    """0.0–1.0 seasonal uplift for selling replacement work on this type.

    Deterministic pure function of (type, month); `month` defaults to the
    current UTC month but tests pass it explicitly.
    """
    m = month if month else datetime.now(timezone.utc).month
    t = (equipment_type or "").strip().lower()
    factor = 0.0
    if t in COOLING_TYPES:
        factor = max(factor, _COOLING_SEASON.get(m, 0.0))
    if t in HEATING_TYPES:
        factor = max(factor, _HEATING_SEASON.get(m, 0.0))
    return factor


# ---------- scoring ----------

# Score budget: 60 pts age/lifespan ratio + 25 pts recent repairs + 15 pts season.
AGE_WEIGHT = 60.0
REPAIR_WEIGHT = 25.0
SEASON_WEIGHT = 15.0
REPAIR_POINTS_PER_EVENT = 8.0  # 3+ repairs in 12mo saturates the repair budget


def replacement_score(age_yrs: float, lifespan_yrs: float,
                      recent_repairs: int, season: float) -> int:
    """0–100 replacement urgency. Deterministic — no randomness, no I/O."""
    life = max(1.0, float(lifespan_yrs or 0))
    age_part = AGE_WEIGHT * min(1.0, max(0.0, age_yrs) / life)
    repair_part = REPAIR_WEIGHT * min(1.0, max(0, recent_repairs) * REPAIR_POINTS_PER_EVENT / REPAIR_WEIGHT)
    season_part = SEASON_WEIGHT * max(0.0, min(1.0, season))
    return int(round(min(100.0, age_part + repair_part + season_part)))


def score_equipment(equipment: dict, events: List[dict],
                    now: Optional[datetime] = None,
                    month: Optional[int] = None) -> dict:
    """Full opportunity payload for one equipment record."""
    ref = now or datetime.now(timezone.utc)
    m = month if month else ref.month
    etype = str(equipment.get("equipment_type") or "").strip()
    lifespan = expected_lifespan_years(etype)
    age = age_years(equipment.get("install_date"), now=ref)
    repairs = count_recent_repairs(events, now=ref)
    season = season_factor(etype, month=m)
    return {
        "equipment_id": equipment.get("id"),
        "equipment_type": etype,
        "make": equipment.get("make") or "",
        "model": equipment.get("model") or "",
        "customer": {
            "id": equipment.get("customer_id") or "",
            "name": equipment.get("customer_name") or "",
        },
        "location_label": equipment.get("location_label") or "",
        "age_years": age,
        "expected_lifespan_years": lifespan,
        "recent_repairs_12mo": repairs,
        "season_factor": round(season, 2),
        "season_month": m,
        "replacement_score": replacement_score(age, lifespan, repairs, season),
    }


# ---------- pricebook matching ----------

# Keywords used to surface replacement-style pricebook items for each type.
_TYPE_ITEM_KEYWORDS: Dict[str, tuple] = {
    "furnace": ("furnace replacement", "furnace install", "new furnace", "heating system"),
    "ac_condenser": ("ac replacement", "condenser replacement", "new ac", "air conditioner install", "cooling system"),
    "air_handler": ("air handler replacement", "air handler install"),
    "heat_pump": ("heat pump replacement", "heat pump install"),
    "thermostat": ("thermostat replacement", "smart thermostat", "thermostat install"),
    "water_heater": ("water heater replacement", "water heater install", "new water heater"),
    "boiler": ("boiler replacement", "boiler install"),
    "electrical_panel": ("panel upgrade", "electrical panel", "panel replacement"),
    "generator": ("generator install", "standby generator", "generator replacement"),
    "garbage_disposal": ("garbage disposal replacement", "disposal install"),
    "well_pump": ("well pump replacement", "well pump install"),
    "septic_system": ("septic",),
    "other": ("replacement", "install", "new system"),
}


def suggest_items(equipment_type: str, catalog: List[dict], limit: int = 3) -> List[dict]:
    """Pick replacement-style pricebook items for this equipment type.

    Deterministic keyword ranking (frozen catalog prices are used by the
    router when the quote draft is built). Returns up to `limit` items as
    {id, name, price, version}.
    """
    keywords = _TYPE_ITEM_KEYWORDS.get((equipment_type or "").strip().lower(),
                                       _TYPE_ITEM_KEYWORDS["other"])
    scored = []
    for item in catalog:
        text = " ".join([
            str(item.get("name") or ""),
            str(item.get("description") or ""),
            str(item.get("category") or ""),
        ]).lower()
        hits = sum(1 for k in keywords if k in text)
        if hits:
            scored.append((hits, str(item.get("name") or ""), item))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [{
        "id": it["id"],
        "name": it.get("name") or "",
        "price": float(it.get("price") or 0),
        "version": int(it.get("version") or 1),
    } for _, _, it in scored[:limit]]
