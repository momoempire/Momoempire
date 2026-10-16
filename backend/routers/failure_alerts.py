"""Predictive equipment-failure alerts — catch failures BEFORE they happen.

Watches the shop's equipment records and job history, then scores each unit
with a rule-based model (no LLM):

- age vs expected lifespan for the equipment type
- days since last service vs the recommended interval
- repair-visit frequency over the last 12 months
- anomaly flags: trouble keywords ("no cool", "breakdown", "emergency",
  "leak", ...) in recent service-event descriptions and job notes

Scores map to severity (info / warning / critical) with a predicted failure
window and a suggested preventive action. The shop can acknowledge or dismiss
alerts; acknowledged/dismissed alerts feed back into the model via a simple
per-tenant weight adjustment (reinforce the driving factor on acknowledge,
dampen it on dismiss).

Collections (all tenant-scoped):
- `failure_alerts`: emitted alerts (status new/acknowledged/dismissed).
- `failure_alert_weights`: per-tenant model weights, adjusted by feedback.
- `failure_alert_scans`: scan run metadata (equipment scanned, alerts made).
- reads `equipment`, `equipment_service_events`, `appointments` (job history).

Live-data hooks:
- TODO(LIVE): feed IoT/telemetry readings (runtime hours, vibration, pressure
  deltas) through _live_sensor_flags() when a sensor integration is
  connected per tenant; today it returns [].
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin

router = APIRouter(prefix="/api/failure-alerts", tags=["failure-alerts"])

# ---------- scoring constants ----------

# Expected useful life per equipment type (years). Free-form types that are
# not listed fall back to the "other" row.
EXPECTED_LIFESPAN_YEARS: Dict[str, int] = {
    "furnace": 20, "boiler": 25,
    "ac_condenser": 15, "air_handler": 15, "heat_pump": 15,
    "thermostat": 10, "garbage_disposal": 10,
    "water_heater": 12, "well_pump": 15,
    "electrical_panel": 30, "septic_system": 30,
    "generator": 20, "other": 15,
}

# Recommended preventive-service interval per type (days). Cooling equipment
# gets a shorter (biannual) interval; everything else is annual.
def _service_interval_days(equipment_type: str) -> int:
    if equipment_type in {"ac_condenser", "heat_pump", "air_handler"}:
        return 180
    return 365

# Trouble keywords scanned in service-event descriptions and job notes.
ANOMALY_KEYWORDS = [
    "breakdown", "failure", "failed", "failing",
    "no cool", "no cooling", "no heat", "no heating",
    "emergency", "leak", "leaking", "frozen", "tripped",
    "burned", "burnt", "cracked", "grinding", "rattling",
    "short cycle", "short cycling", "won't start", "will not start",
    "water damage", "carbon monoxide",
]

# Severity cutoffs on the 0-100 risk score. Below ALERT_THRESHOLD the unit is
# considered healthy and the scan emits nothing for it.
SEVERITY_CUTOFFS = {"critical": 80, "warning": 50, "info": 30}
ALERT_THRESHOLD = 30

FAILURE_WINDOWS = {
    "critical": "0-30 days",
    "warning": "1-3 months",
    "info": "3-6 months",
}

# Feedback cooldown: a dismissed alert suppresses re-alerting for this long;
# an acknowledged alert blocks duplicates while it is fresh.
DISMISS_COOLDOWN_DAYS = 30
ACKNOWLEDGE_COOLDOWN_DAYS = 30

FACTOR_LABELS = {
    "age": "Age vs expected lifespan",
    "service_gap": "Time since last service",
    "failure_freq": "Recent repair frequency",
    "anomaly": "Trouble signs in job history",
}

_DEFAULT_WEIGHTS: Dict[str, float] = {
    "age": 0.30,
    "service_gap": 0.25,
    "failure_freq": 0.25,
    "anomaly": 0.20,
}
_FEEDBACK_STEP = 0.05  # weight nudge per acknowledge/dismiss


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


def _live_sensor_flags(equipment_id: str, tenant_id: str) -> List[str]:
    """TODO(LIVE): query the tenant's connected IoT/telemetry provider for
    this unit (runtime hours, vibration, pressure deltas, fault codes) and
    return anomaly flag strings. No sensor integration is connected yet, so
    this always returns [] and scoring falls back to age/service/history."""
    return []


def _anomaly_hits(text: str) -> List[str]:
    low = (text or "").lower()
    return sorted({kw for kw in ANOMALY_KEYWORDS if kw in low})


def _compute_risk(equipment: dict, events: List[dict], jobs: List[dict],
                 weights: Dict[str, float], now: datetime) -> Dict:
    """Score one unit 0-100. Returns the score, severity (or None when
    healthy), and a per-factor breakdown with reasons."""
    eq_type = equipment.get("equipment_type") or "other"
    lifespan = EXPECTED_LIFESPAN_YEARS.get(eq_type, EXPECTED_LIFESPAN_YEARS["other"])
    interval = _service_interval_days(eq_type)
    components: List[Dict] = []
    reasons: List[str] = []

    # 1) age vs expected lifespan
    install_dt = _parse_iso(equipment.get("install_date") or "")
    age_years: Optional[float] = None
    if install_dt:
        age_years = max(0.0, (now - install_dt).days / 365.25)
    if age_years is None:
        age_score, age_detail = 0.0, "Install date unknown"
    else:
        age_score = min(1.0, age_years / lifespan)
        age_detail = f"{age_years:.1f}y old vs {lifespan}y expected lifespan"
        if age_score >= 0.9:
            reasons.append(f"Unit is {age_years:.1f} years old (expected lifespan {lifespan}y)")
    components.append({"factor": "age", "score": round(age_score, 3), "detail": age_detail})

    # 2) days since last service vs recommended interval
    service_dates = [_parse_iso(e.get("service_date") or "") for e in events]
    service_dates = [d for d in service_dates if d]
    last_service = max(service_dates) if service_dates else None
    reference_dt = last_service or install_dt or _parse_iso(equipment.get("created_at") or "")
    if reference_dt is None:
        gap_score, gap_detail = 0.0, "No service history"
    else:
        gap_days = max(0, (now - reference_dt).days)
        gap_score = min(1.0, gap_days / (2 * interval))
        gap_detail = f"{gap_days}d since last service (recommended every {interval}d)"
        if gap_days > interval:
            reasons.append(f"Service overdue by {gap_days - interval}d (last serviced {gap_days}d ago)")
    components.append({"factor": "service_gap", "score": round(gap_score, 3), "detail": gap_detail})

    # 3) repair-visit frequency over the last 12 months
    year_ago = now - timedelta(days=365)
    recent_repairs = [e for e in events
                      if (_parse_iso(e.get("service_date") or "") or now) >= year_ago]
    n_repairs = len(recent_repairs)
    freq_score = min(1.0, n_repairs / 4)
    freq_detail = f"{n_repairs} service visit(s) in the last 12 months"
    if n_repairs >= 2:
        reasons.append(freq_detail + " — repeat repairs are a failure precursor")
    components.append({"factor": "failure_freq", "score": round(freq_score, 3), "detail": freq_detail})

    # 4) anomaly flags from recent jobs / service notes (last 180 days)
    half_year_ago = now - timedelta(days=180)
    texts: List[str] = []
    for e in events:
        dt = _parse_iso(e.get("service_date") or "")
        if dt is None or dt >= half_year_ago:
            texts.append(e.get("description") or "")
    for j in jobs:
        dt = _parse_iso(j.get("start_at") or j.get("created_at") or "")
        if dt is None or dt >= half_year_ago:
            texts.append(j.get("notes") or "")
    # TODO(LIVE): merge in live sensor flags when an integration is connected.
    hits = sorted({kw for t in texts for kw in _anomaly_hits(t)}
                  | set(_live_sensor_flags(equipment.get("id", ""), equipment.get("tenant_id", ""))))
    anomaly_score = min(1.0, len(hits) / 3)
    anomaly_detail = ("Trouble signs: " + ", ".join(hits)) if hits else "No trouble signs in recent history"
    if hits:
        reasons.append("Recent trouble signs: " + ", ".join(hits))
    components.append({"factor": "anomaly", "score": round(anomaly_score, 3), "detail": anomaly_detail})

    total = sum(weights.get(c["factor"], 0.0) * c["score"] for c in components)
    score = int(round(100 * total))
    if score >= SEVERITY_CUTOFFS["critical"]:
        severity = "critical"
    elif score >= SEVERITY_CUTOFFS["warning"]:
        severity = "warning"
    elif score >= ALERT_THRESHOLD:
        severity = "info"
    else:
        severity = None

    return {
        "score": score,
        "severity": severity,
        "healthy": severity is None,
        "components": [
            {**c, "label": FACTOR_LABELS[c["factor"]],
             "weight": round(weights.get(c["factor"], 0.0), 3),
             "points": round(100 * weights.get(c["factor"], 0.0) * c["score"], 1)}
            for c in components
        ],
        "reasons": reasons,
    }


def _suggested_action(risk: Dict, equipment: dict) -> str:
    label = _equipment_label(equipment)
    top = max(risk["components"], key=lambda c: c["points"])["factor"]
    if top == "age":
        return (f"Schedule a replacement evaluation for the {label} — it is near or "
                "past its expected lifespan. Proactive replacement avoids an emergency call.")
    if top == "service_gap":
        return (f"Book preventive maintenance for the {label} — service is overdue. "
                "A tune-up now is cheaper than a breakdown later.")
    if top == "failure_freq":
        return (f"Run a full diagnostic on the {label} — repeated recent repairs point "
                "to an underlying problem. Quote repair vs replace.")
    return (f"Dispatch a diagnostic visit for the {label} — recent job notes show "
            "trouble signs worth catching before the next failure.")


def _equipment_label(equipment: dict) -> str:
    parts = [equipment.get("make") or "", equipment.get("model") or ""]
    mm = " ".join(p for p in parts if p).strip()
    eq_type = (equipment.get("equipment_type") or "unit").replace("_", " ")
    loc = equipment.get("location_label") or ""
    cust = equipment.get("customer_name") or ""
    label = f"{mm} {eq_type}".strip()
    if loc:
        label += f" ({loc})"
    if cust:
        label += f" — {cust}"
    return label or "unit"


def _public_alert(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "tenant_id": doc.get("tenant_id"),
        "equipment_id": doc.get("equipment_id"),
        "equipment_label": doc.get("equipment_label", ""),
        "severity": doc.get("severity"),
        "risk_score": doc.get("risk_score"),
        "breakdown": doc.get("breakdown", []),
        "reasons": doc.get("reasons", []),
        "predicted_window": doc.get("predicted_window", ""),
        "suggested_action": doc.get("suggested_action", ""),
        "status": doc.get("status", "new"),
        "acknowledged_by": doc.get("acknowledged_by"),
        "acknowledged_at": doc.get("acknowledged_at"),
        "dismissed_by": doc.get("dismissed_by"),
        "dismissed_at": doc.get("dismissed_at"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


async def _get_weights(db, tenant_id: str) -> Dict[str, float]:
    doc = await db.failure_alert_weights.find_one({"tenant_id": tenant_id})
    if doc and isinstance(doc.get("weights"), dict):
        merged = {**_DEFAULT_WEIGHTS, **{k: float(v) for k, v in doc["weights"].items()}}
        return merged
    return dict(_DEFAULT_WEIGHTS)


async def _adjust_weights(db, tenant_id: str, top_factor: str, direction: int) -> Dict[str, float]:
    """Feedback loop: acknowledging an alert reinforces its top driving
    factor (real risk), dismissing it dampens the factor (false alarm).
    Weights stay clamped and re-normalized so they always sum to 1."""
    weights = await _get_weights(db, tenant_id)
    if top_factor not in weights:
        return weights
    weights[top_factor] = min(0.80, max(0.05, weights[top_factor] + direction * _FEEDBACK_STEP))
    total = sum(weights.values()) or 1.0
    weights = {k: round(v / total, 4) for k, v in weights.items()}
    doc = await db.failure_alert_weights.find_one({"tenant_id": tenant_id})
    payload = {"tenant_id": tenant_id, "weights": weights, "updated_at": _now_iso()}
    if doc:
        await db.failure_alert_weights.update_one({"tenant_id": tenant_id}, {"$set": payload})
    else:
        payload["id"] = _uuid()
        await db.failure_alert_weights.insert_one(payload)
    return weights


async def _get_alert(db, alert_id: str, tenant_id: str) -> dict:
    doc = await db.failure_alerts.find_one({"id": alert_id, "tenant_id": tenant_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Alert not found")
    return doc


async def _get_equipment_active(db, equipment_id: str, tenant_id: str) -> dict:
    doc = await db.equipment.find_one(
        {"id": equipment_id, "tenant_id": tenant_id, "active": True}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Equipment record not found")
    return doc


async def _jobs_for_equipment(db, tenant_id: str, equipment: dict, events: List[dict]) -> List[dict]:
    """Job history relevant to one unit: jobs linked from its service events
    plus recent completed jobs for the same customer (their notes may mention
    this unit even when no event was logged)."""
    job_ids = {e.get("job_id") for e in events if e.get("job_id")}
    jobs: List[dict] = []
    for jid in job_ids:
        job = await db.appointments.find_one(
            {"id": jid, "tenant_id": tenant_id}, {"_id": 0})
        if job:
            jobs.append(job)
    seen = {j.get("id") for j in jobs}
    customer_id = equipment.get("customer_id")
    if customer_id:
        recent = await db.appointments.find(
            {"tenant_id": tenant_id, "customer_id": customer_id, "status": "completed"},
            {"_id": 0}).to_list(500)
        for j in sorted(recent, key=lambda x: str(x.get("start_at") or x.get("created_at") or ""),
                        reverse=True)[:25]:
            if j.get("id") not in seen:
                jobs.append(j)
                seen.add(j.get("id"))
    return jobs


# ---------- overview ----------

@router.get("/overview")
async def alerts_overview(user: dict = Depends(require_tenant_user)):
    """Alert counts by severity plus scan and model-weight status."""
    db = get_db()
    tenant_id = user["tenant_id"]
    alerts = await db.failure_alerts.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(10000)
    equipment = await db.equipment.find({"tenant_id": tenant_id, "active": True}, {"_id": 0}).to_list(2000)

    def count(**kw):
        return sum(1 for a in alerts if all(a.get(k) == v for k, v in kw.items()))

    at_risk = {a.get("equipment_id") for a in alerts if a.get("status") == "new"}
    scans = await db.failure_alert_scans.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(100)
    last_scan = max((s.get("started_at") or "" for s in scans), default=None)

    return {
        "critical": count(severity="critical", status="new"),
        "warning": count(severity="warning", status="new"),
        "info": count(severity="info", status="new"),
        "open": count(status="new"),
        "acknowledged": count(status="acknowledged"),
        "dismissed": count(status="dismissed"),
        "total_alerts": len(alerts),
        "equipment_at_risk": len(at_risk),
        "total_equipment": len(equipment),
        "last_scan_at": last_scan,
        "weights": await _get_weights(db, tenant_id),
    }


# ---------- alert list ----------

@router.get("/alerts")
async def list_alerts(
    severity: Optional[str] = None,
    status: Optional[str] = None,
    user: dict = Depends(require_tenant_user),
):
    """List alerts, newest first. Filter with ?severity=critical&status=new."""
    db = get_db()
    if severity and severity not in {"info", "warning", "critical"}:
        raise HTTPException(400, "severity must be one of info, warning, critical")
    if status and status not in {"new", "acknowledged", "dismissed"}:
        raise HTTPException(400, "status must be one of new, acknowledged, dismissed")
    docs = await db.failure_alerts.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).to_list(10000)
    docs = [d for d in docs
            if (severity is None or d.get("severity") == severity)
            and (status is None or d.get("status") == status)]
    docs.sort(key=lambda d: str(d.get("created_at") or ""), reverse=True)
    return {"alerts": [_public_alert(d) for d in docs], "count": len(docs)}


# ---------- acknowledge / dismiss ----------

@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str, user: dict = Depends(require_tenant_user)):
    """Acknowledge an alert ("we're on it"). Reinforces the alert's top risk
    factor in the tenant's model weights."""
    db = get_db()
    tenant_id = user["tenant_id"]
    alert = await _get_alert(db, alert_id, tenant_id)
    if alert.get("status") != "new":
        raise HTTPException(409, f"Alert is already {alert.get('status')}; only new alerts can be acknowledged")

    breakdown = alert.get("breakdown") or []
    top_factor = max(breakdown, key=lambda c: c.get("points", 0)).get("factor") if breakdown else "age"
    weights = await _adjust_weights(db, tenant_id, top_factor, +1)

    now = _now_iso()
    await db.failure_alerts.update_one(
        {"id": alert_id, "tenant_id": tenant_id},
        {"$set": {"status": "acknowledged", "acknowledged_by": user.get("id"),
                  "acknowledged_at": now, "updated_at": now}},
    )
    updated = await _get_alert(db, alert_id, tenant_id)
    return {"ok": True, "alert": _public_alert(updated), "weights": weights,
            "note": f"Acknowledged — '{top_factor}' weight reinforced for this shop"}


@router.post("/alerts/{alert_id}/dismiss")
async def dismiss_alert(alert_id: str, user: dict = Depends(require_tenant_user)):
    """Dismiss an alert as a false alarm. Dampens the alert's top risk factor
    in the tenant's model weights and suppresses re-alerting for 30 days."""
    db = get_db()
    tenant_id = user["tenant_id"]
    alert = await _get_alert(db, alert_id, tenant_id)
    if alert.get("status") != "new":
        raise HTTPException(409, f"Alert is already {alert.get('status')}; only new alerts can be dismissed")

    breakdown = alert.get("breakdown") or []
    top_factor = max(breakdown, key=lambda c: c.get("points", 0)).get("factor") if breakdown else "age"
    weights = await _adjust_weights(db, tenant_id, top_factor, -1)

    now = _now_iso()
    await db.failure_alerts.update_one(
        {"id": alert_id, "tenant_id": tenant_id},
        {"$set": {"status": "dismissed", "dismissed_by": user.get("id"),
                  "dismissed_at": now, "updated_at": now}},
    )
    updated = await _get_alert(db, alert_id, tenant_id)
    return {"ok": True, "alert": _public_alert(updated), "weights": weights,
            "note": f"Dismissed — '{top_factor}' weight dampened; suppressed for {DISMISS_COOLDOWN_DAYS} days"}


# ---------- risk breakdown for one unit ----------

@router.get("/equipment/{equipment_id}/risk")
async def equipment_risk(equipment_id: str, user: dict = Depends(require_tenant_user)):
    """Fresh risk score breakdown for one piece of equipment, computed live
    from its record, service timeline, and recent job history."""
    db = get_db()
    tenant_id = user["tenant_id"]
    equipment = await _get_equipment_active(db, equipment_id, tenant_id)
    events = await db.equipment_service_events.find(
        {"tenant_id": tenant_id, "equipment_id": equipment_id}, {"_id": 0}).to_list(1000)
    jobs = await _jobs_for_equipment(db, tenant_id, equipment, events)
    weights = await _get_weights(db, tenant_id)
    risk = _compute_risk(equipment, events, jobs, weights, datetime.now(timezone.utc))
    return {
        "equipment_id": equipment_id,
        "equipment_label": _equipment_label(equipment),
        **risk,
        "predicted_window": FAILURE_WINDOWS.get(risk["severity"]) if risk["severity"] else None,
        "suggested_action": _suggested_action(risk, equipment) if risk["severity"] else "No action needed",
        "weights": weights,
    }


# ---------- scan ----------

@router.post("/scan")
async def run_scan(user: dict = Depends(require_tenant_owner_or_admin)):
    """Trigger a prediction scan over the shop's active equipment. In
    production this runs on a cron schedule; this endpoint forces a run.

    One alert per at-risk unit: an existing open alert is refreshed with the
    latest score; acknowledged/dismissed alerts suppress re-alerting while
    fresh (30 days)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    now = datetime.now(timezone.utc)
    started_at = _now_iso()
    weights = await _get_weights(db, tenant_id)

    equipment = await db.equipment.find({"tenant_id": tenant_id, "active": True}, {"_id": 0}).to_list(2000)
    created = updated = skipped_healthy = suppressed = 0
    new_alerts: List[dict] = []

    for eq in equipment:
        eq_id = eq.get("id")
        events = await db.equipment_service_events.find(
            {"tenant_id": tenant_id, "equipment_id": eq_id}, {"_id": 0}).to_list(1000)
        jobs = await _jobs_for_equipment(db, tenant_id, eq, events)
        risk = _compute_risk(eq, events, jobs, weights, now)

        if risk["healthy"]:
            skipped_healthy += 1
            continue

        # Dedupe against this unit's existing alerts (Python-side filtering,
        # same pattern as the rest of the router).
        prior = await db.failure_alerts.find(
            {"tenant_id": tenant_id, "equipment_id": eq_id}, {"_id": 0}).to_list(100)
        open_alert = next((a for a in prior if a.get("status") == "new"), None)
        fresh_ack = [a for a in prior
                     if a.get("status") == "acknowledged"
                     and (_parse_iso(a.get("acknowledged_at") or "") or now) >= now - timedelta(days=ACKNOWLEDGE_COOLDOWN_DAYS)]
        fresh_dismiss = [a for a in prior
                         if a.get("status") == "dismissed"
                         and (_parse_iso(a.get("dismissed_at") or "") or now) >= now - timedelta(days=DISMISS_COOLDOWN_DAYS)]
        if fresh_ack or fresh_dismiss:
            suppressed += 1
            continue

        severity = risk["severity"]
        payload = {
            "severity": severity,
            "risk_score": risk["score"],
            "breakdown": risk["components"],
            "reasons": risk["reasons"],
            "predicted_window": FAILURE_WINDOWS[severity],
            "suggested_action": _suggested_action(risk, eq),
            "equipment_label": _equipment_label(eq),
            "updated_at": started_at,
        }
        if open_alert:
            await db.failure_alerts.update_one(
                {"id": open_alert["id"], "tenant_id": tenant_id}, {"$set": payload})
            updated += 1
        else:
            doc = {"id": _uuid(), "tenant_id": tenant_id, "equipment_id": eq_id,
                   "status": "new", "created_at": started_at,
                   "acknowledged_by": None, "acknowledged_at": None,
                   "dismissed_by": None, "dismissed_at": None, **payload}
            await db.failure_alerts.insert_one(doc)
            created += 1
            doc.pop("_id", None)
            new_alerts.append(_public_alert(doc))

    # TODO(LIVE): in production this scan is scheduled via the cron service
    # (see routers/cron.py) — e.g. nightly at 02:00 tenant-local time — and
    # pushes critical alerts to the shop via SMS/push. The /scan endpoint
    # stays as the manual trigger / test hook.
    scan_doc = {
        "id": _uuid(), "tenant_id": tenant_id, "started_at": started_at,
        "equipment_scanned": len(equipment), "alerts_created": created,
        "alerts_updated": updated, "suppressed": suppressed,
        "skipped_healthy": skipped_healthy,
    }
    await db.failure_alert_scans.insert_one(scan_doc)

    return {
        "ok": True,
        "equipment_scanned": len(equipment),
        "alerts_created": created,
        "alerts_updated": updated,
        "suppressed": suppressed,
        "skipped_healthy": skipped_healthy,
        "new_alerts": new_alerts,
    }
