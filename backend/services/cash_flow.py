"""Predictive cash-flow forecasting (deterministic, no LLM).

Model: scheduled appointments -> expected completion (stage-based probability)
-> invoice -> historical per-customer days-to-pay -> projected cash-in by week.
Recurring outflows come from the tenant's cash-flow settings (e.g. monthly
payroll) plus `sent` purchase orders from parts ordering. All inputs are read
defensively; when the shop has no invoices and no scheduled appointments we
return clearly-labeled synthetic demo data (`demo: true`).

Collections (all tenant-scoped, all reads filtered in Python so plain
tenant_id queries work against the fake mongo used in tests):
- `invoices`: open when status in {unpaid, overdue, partial, sent}; amount from
  amount_due/total/amount; due_date ISO; paid_at/created_at for history.
- `appointments`: status in {scheduled, confirmed, en_route, on_site};
  start_at ISO; value from the linked estimate (`quote_id` -> estimates.total).
- `estimates`: {id, total} lookup for job value.
- `purchase_orders`: status sent -> outflow at sent_at + supplier_payment_days.
- `tenant_settings`: `cash_flow` block {starting_balance, safety_threshold,
  monthly_payroll, default_days_to_pay, supplier_payment_days, invoice_terms_days}.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

WEEKS = 12

OPEN_INVOICE_STATUSES = {"unpaid", "overdue", "partial", "sent"}
ACTIVE_APPT_STATUSES = {"scheduled", "confirmed", "en_route", "on_site"}

# Probability a scheduled appointment completes and invoices, by stage.
STAGE_COMPLETION_PROB = {
    "scheduled": 0.60,
    "confirmed": 0.75,
    "en_route": 0.90,
    "on_site": 0.95,
}

DEFAULT_DAYS_TO_PAY = 7
DEFAULT_SUPPLIER_DAYS = 14
DEFAULT_INVOICE_TERMS_DAYS = 30
DEFAULT_SAFETY_THRESHOLD = 5000.0
DEFAULT_STARTING_BALANCE = 0.0


# ---------- date helpers ----------

def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        try:
            dt = datetime.fromtimestamp(value, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    else:
        try:
            text = str(value).strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _monday_of(dt: datetime) -> datetime:
    d = dt.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return d - timedelta(days=d.weekday())


def _week_index(event_dt: datetime, monday0: datetime, weeks: int = WEEKS) -> int:
    """Bucket an event into a week 0..weeks-1; past events land in week 0."""
    delta_days = (event_dt.astimezone(timezone.utc) - monday0).days
    idx = delta_days // 7
    return max(0, min(weeks - 1, idx))


def _fnum(value: Any) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return f if f == f and abs(f) != float("inf") else 0.0


def _invoice_amount(inv: dict) -> float:
    return _fnum(inv.get("amount_due")) or _fnum(inv.get("total")) or _fnum(inv.get("amount"))


def _customer_label(inv: dict) -> str:
    return str(inv.get("customer_name") or inv.get("customer_id") or "Customer")


# ---------- settings ----------

def _cash_settings(tenant_settings_doc: Optional[dict]) -> dict:
    cfg = (tenant_settings_doc or {}).get("cash_flow") or {}
    return {
        "starting_balance": _fnum(cfg.get("starting_balance") or DEFAULT_STARTING_BALANCE),
        "safety_threshold": _fnum(cfg.get("safety_threshold") or DEFAULT_SAFETY_THRESHOLD),
        "monthly_payroll": _fnum(cfg.get("monthly_payroll")),
        "default_days_to_pay": int(_fnum(cfg.get("default_days_to_pay")) or DEFAULT_DAYS_TO_PAY),
        "supplier_payment_days": int(_fnum(cfg.get("supplier_payment_days")) or DEFAULT_SUPPLIER_DAYS),
        "invoice_terms_days": int(_fnum(cfg.get("invoice_terms_days")) or DEFAULT_INVOICE_TERMS_DAYS),
    }


# ---------- days-to-pay history ----------

def compute_days_to_pay(paid_invoices: List[dict]) -> Tuple[float, Dict[str, dict]]:
    """avg(paid_at - created_at) per customer; (overall_avg, {customer_id: {...}})."""
    per_customer: Dict[str, List[float]] = {}
    customer_names: Dict[str, str] = {}
    all_days: List[float] = []
    for inv in paid_invoices:
        paid = _parse_iso(inv.get("paid_at"))
        billed = _parse_iso(inv.get("sent_at") or inv.get("created_at"))
        if not paid or not billed or paid < billed:
            continue
        days = (paid - billed).days
        if days > 365:
            continue
        cid = str(inv.get("customer_id") or inv.get("customer_name") or "unknown")
        per_customer.setdefault(cid, []).append(days)
        if inv.get("customer_name"):
            customer_names[cid] = str(inv.get("customer_name"))
        all_days.append(days)
    overall = round(sum(all_days) / len(all_days), 1) if all_days else DEFAULT_DAYS_TO_PAY
    summary = {
        cid: {
            "customer_id": cid,
            "customer_name": customer_names.get(cid, ""),
            "avg_days_to_pay": round(sum(v) / len(v), 1),
            "samples": len(v),
        }
        for cid, v in per_customer.items()
    }
    return overall, summary


def _customer_days(cid: str, per_customer: Dict[str, dict], fallback: float) -> float:
    entry = per_customer.get(cid)
    if entry and entry.get("samples"):
        return float(entry["avg_days_to_pay"])
    return fallback


# ---------- forecast ----------

async def load_inputs(db, tenant_id: str):
    """Raw (invoices, appointments, estimates_by_id, purchase_orders, settings)."""
    invoices = await db.invoices.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(5000)
    appointments = await db.appointments.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(5000)
    estimates = await db.estimates.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(5000)
    pos = await db.purchase_orders.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(2000)
    settings = await db.tenant_settings.find_one({"tenant_id": tenant_id}, {"_id": 0})
    return invoices, appointments, {e.get("id"): e for e in estimates}, pos, settings


def build_forecast(
    invoices: List[dict],
    appointments: List[dict],
    estimates_by_id: Dict[str, dict],
    purchase_orders: List[dict],
    settings: dict,
    now: Optional[datetime] = None,
    weeks: int = WEEKS,
    demo: bool = False,
) -> dict:
    """Deterministic 12-week cash projection. Returns the forecast payload."""
    now = now or datetime.now(timezone.utc)
    monday0 = _monday_of(now)
    horizon_end = monday0 + timedelta(weeks=weeks)

    overall_dtp, per_customer_dtp = compute_days_to_pay(
        [i for i in invoices if (i.get("status") or "") == "paid"]
    )
    fallback_dtp = float(settings.get("default_days_to_pay") or DEFAULT_DAYS_TO_PAY)
    if overall_dtp == DEFAULT_DAYS_TO_PAY and not per_customer_dtp:
        # no paid history at all -> the shop's configured default applies
        overall_dtp = float(fallback_dtp)
    terms_days = int(settings.get("invoice_terms_days") or DEFAULT_INVOICE_TERMS_DAYS)
    supplier_days = int(settings.get("supplier_payment_days") or DEFAULT_SUPPLIER_DAYS)
    starting = float(settings.get("starting_balance") or 0.0)
    safety = float(settings.get("safety_threshold") or DEFAULT_SAFETY_THRESHOLD)

    weeks_out = [
        {"week": w + 1, "week_start": (monday0 + timedelta(weeks=w)).date().isoformat(),
         "expected_in": 0.0, "expected_out": 0.0, "invoices_in": 0.0, "jobs_in": 0.0}
        for w in range(weeks)
    ]
    inflow_detail: List[dict] = []
    outflow_detail: List[dict] = []

    # 1) Open invoices -> expected collection.
    open_invoices = [i for i in invoices if (i.get("status") or "") in OPEN_INVOICE_STATUSES]
    for inv in open_invoices:
        amount = _invoice_amount(inv)
        if amount <= 0:
            continue
        due = _parse_iso(inv.get("due_date"))
        if not due:
            billed = _parse_iso(inv.get("created_at"))
            due = (billed + timedelta(days=terms_days)) if billed else now
        cid = str(inv.get("customer_id") or inv.get("customer_name") or "unknown")
        pay_date = due + timedelta(days=_customer_days(cid, per_customer_dtp, fallback_dtp))
        idx = _week_index(pay_date, monday0, weeks)
        weeks_out[idx]["expected_in"] = round(weeks_out[idx]["expected_in"] + amount, 2)
        weeks_out[idx]["invoices_in"] = round(weeks_out[idx]["invoices_in"] + amount, 2)
        inflow_detail.append({
            "type": "invoice", "label": _customer_label(inv),
            "amount": round(amount, 2), "expected_pay_date": pay_date.date().isoformat(),
            "week": idx + 1,
        })

    # 2) Scheduled appointments -> completion -> invoice -> collection.
    for appt in appointments:
        status = (appt.get("status") or "scheduled").lower()
        if status not in ACTIVE_APPT_STATUSES:
            continue
        start = _parse_iso(appt.get("start_at"))
        if not start or start >= horizon_end:
            continue
        est = (estimates_by_id or {}).get(appt.get("quote_id") or "") or {}
        value = _fnum(est.get("total")) or _fnum(appt.get("total")) or _fnum(appt.get("value"))
        if value <= 0:
            continue
        prob = STAGE_COMPLETION_PROB.get(status, 0.60)
        expected = round(value * prob, 2)
        cid = str(appt.get("customer_id") or appt.get("customer_name") or "unknown")
        # Invoice issued on completion, due after terms, paid after customer's days-to-pay.
        pay_date = max(start, now) + timedelta(days=terms_days) + timedelta(
            days=_customer_days(cid, per_customer_dtp, fallback_dtp))
        idx = _week_index(pay_date, monday0, weeks)
        weeks_out[idx]["expected_in"] = round(weeks_out[idx]["expected_in"] + expected, 2)
        weeks_out[idx]["jobs_in"] = round(weeks_out[idx]["jobs_in"] + expected, 2)
        inflow_detail.append({
            "type": "scheduled_job", "label": str(appt.get("service_name") or appt.get("customer_name") or "Job"),
            "amount": expected, "expected_pay_date": pay_date.date().isoformat(),
            "week": idx + 1, "completion_probability": prob,
        })

    # 3) Outflows: sent purchase orders.
    open_pos: List[dict] = []
    for po in purchase_orders:
        if (po.get("status") or "").lower() != "sent":
            continue
        total = sum(_fnum(l.get("qty")) * _fnum(l.get("unit_cost")) for l in (po.get("lines") or []))
        total = total or _fnum(po.get("total"))
        if total <= 0:
            continue
        sent = _parse_iso(po.get("sent_at")) or now
        due = sent + timedelta(days=supplier_days)
        idx = _week_index(due, monday0, weeks)
        weeks_out[idx]["expected_out"] = round(weeks_out[idx]["expected_out"] + total, 2)
        outflow_detail.append({
            "type": "purchase_order",
            "label": str(po.get("supplier_name") or "Supplier PO"),
            "amount": round(total, 2), "expected_pay_date": due.date().isoformat(), "week": idx + 1,
        })
        open_pos.append({"label": str(po.get("supplier_name") or "PO"), "amount": round(total, 2), "week": idx + 1})

    # 4) Recurring outflows: payroll spread weekly (bi-weekly-ish via /4.33).
    monthly_payroll = float(settings.get("monthly_payroll") or 0.0)
    if monthly_payroll > 0:
        weekly_payroll = round(monthly_payroll / 4.33, 2)
        for w in range(weeks):
            weeks_out[w]["expected_out"] = round(weeks_out[w]["expected_out"] + weekly_payroll, 2)
        outflow_detail.append({
            "type": "recurring", "label": "Payroll (estimated)", "amount": round(weekly_payroll * weeks, 2),
            "expected_pay_date": None, "week": None,
        })

    # Running balance.
    running = starting
    for w in weeks_out:
        w["net"] = round(w["expected_in"] - w["expected_out"], 2)
        running = round(running + w["net"], 2)
        w["running_balance"] = running

    # Low-balance weeks.
    warnings = [
        {"week": w["week"], "week_start": w["week_start"],
         "projected_balance": w["running_balance"],
         "shortfall": round(safety - w["running_balance"], 2)}
        for w in weeks_out if w["running_balance"] < safety
    ]

    assumptions = {
        "avg_days_to_pay": overall_dtp,
        "days_to_pay_samples": sum(e["samples"] for e in per_customer_dtp.values()),
        "per_customer": sorted(per_customer_dtp.values(), key=lambda e: -e["samples"])[:25],
        "job_completion_probability": dict(STAGE_COMPLETION_PROB),
        "invoice_terms_days": terms_days,
        "supplier_payment_days": supplier_days,
        "starting_balance": round(starting, 2),
        "safety_threshold": round(safety, 2),
        "monthly_payroll": round(monthly_payroll, 2),
        "open_invoice_count": len(open_invoices),
        "data_source": "demo" if demo else "live",
    }
    return {
        "weeks": weeks_out,
        "inflow_detail": inflow_detail,
        "outflow_detail": outflow_detail,
        "starting_balance": round(starting, 2),
        "safety_threshold": round(safety, 2),
        "warnings": warnings,
        "assumptions": assumptions,
        "demo": demo,
        "generated_at": now.isoformat(),
    }


def build_levers(forecast: dict, invoices: List[dict], purchase_orders: List[dict]) -> List[dict]:
    """What-if levers ranked by projected cash impact (desc)."""
    levers: List[dict] = []
    warnings = forecast.get("warnings") or []
    safety = float(forecast.get("safety_threshold") or DEFAULT_SAFETY_THRESHOLD)

    # Lever 1: accelerate the largest open invoices.
    open_invoices = [
        (inv, _invoice_amount(inv))
        for inv in invoices if (inv.get("status") or "") in OPEN_INVOICE_STATUSES
    ]
    open_invoices = [(inv, amt) for inv, amt in open_invoices if amt > 0]
    open_invoices.sort(key=lambda t: -t[1])
    top = open_invoices[:5]
    if top:
        total_top = round(sum(amt for _, amt in top), 2)
        # Projected: collect 80% of the top-5 within one week.
        impact = round(total_top * 0.8, 2)
        names = ", ".join(_customer_label(inv) for inv, _ in top[:3])
        suffix = f" (+{len(top) - 3} more)" if len(top) > 3 else ""
        levers.append({
            "id": "accelerate-invoices",
            "kind": "accelerate_collection",
            "title": f"Collect the 5 largest open invoices — {names}{suffix}",
            "detail": (f"${total_top:,.2f} is sitting in open invoices. Same-day outreach + a "
                       f"2% early-pay discount typically pulls most of it in within a week."),
            "projected_impact": impact,
            "timeframe": "within 7 days",
            "closes_shortfall": bool(warnings and impact >= min(w["shortfall"] for w in warnings)),
        })

    # Lever 2: defer sent purchase orders.
    for po in sorted(purchase_orders, key=lambda p: -(
            sum(_fnum(l.get("qty")) * _fnum(l.get("unit_cost")) for l in (p.get("lines") or []))
            or _fnum(p.get("total")))):
        if (po.get("status") or "").lower() != "sent":
            continue
        total = sum(_fnum(l.get("qty")) * _fnum(l.get("unit_cost")) for l in (po.get("lines") or []))
        total = total or _fnum(po.get("total"))
        if total <= 0:
            continue
        levers.append({
            "id": f"defer-po-{po.get('id') or 'unknown'}",
            "kind": "defer_outflow",
            "title": f"Defer or renegotiate the PO with {po.get('supplier_name') or 'supplier'}",
            "detail": (f"Pushing this ${total:,.2f} PO out by 2 weeks frees cash for the "
                       f"low-balance stretch."),
            "projected_impact": round(total, 2),
            "timeframe": "shifts outflow ~2 weeks",
            "closes_shortfall": bool(warnings and total >= min(w["shortfall"] for w in warnings)),
        })
        if len([l for l in levers if l["kind"] == "defer_outflow"]) >= 3:
            break

    # Lever 3: deposits on upcoming scheduled jobs.
    jobs_in = sum(w.get("jobs_in", 0) for w in forecast.get("weeks", []))
    if jobs_in > 0:
        deposit = round(jobs_in * 0.25, 2)
        levers.append({
            "id": "require-deposits",
            "kind": "accelerate_collection",
            "title": "Require a 25% deposit on upcoming scheduled jobs",
            "detail": (f"${jobs_in:,.2f} of job revenue is forecast over the next 12 weeks. "
                       f"A 25% upfront deposit pulls ~${deposit:,.2f} forward into this week."),
            "projected_impact": deposit,
            "timeframe": "this week",
            "closes_shortfall": bool(warnings and deposit >= min(w["shortfall"] for w in warnings)),
        })

    levers.sort(key=lambda l: -l["projected_impact"])
    return levers


# ---------- demo data ----------

def make_demo_inputs(now: datetime) -> Tuple[List[dict], List[dict], Dict[str, dict], List[dict], dict]:
    """Synthetic shop so the forecast is demoable with zero real data."""
    iso = lambda dt: dt.isoformat()
    customers = [
        ("cust-a", "Maple Street Homes", 9),
        ("cust-b", "Downtown Dental", 21),
        ("cust-c", "Riverside Apartments", 4),
    ]
    invoices: List[dict] = []
    # Paid history (feeds days-to-pay).
    for i, (cid, name, dtp) in enumerate(customers):
        for k in range(3):
            billed = now - timedelta(days=40 + i * 12 + k * 9)
            invoices.append({
                "id": f"demo-inv-paid-{i}-{k}", "tenant_id": "demo",
                "customer_id": cid, "customer_name": name,
                "total": 850.0 + i * 400 + k * 75, "status": "paid",
                "created_at": iso(billed), "paid_at": iso(billed + timedelta(days=dtp)),
                "due_date": iso(billed + timedelta(days=30)),
            })
    # Open invoices.
    open_defs = [
        ("cust-a", "Maple Street Homes", 2450.0, now - timedelta(days=12), "overdue"),
        ("cust-b", "Downtown Dental", 1890.0, now + timedelta(days=5), "unpaid"),
        ("cust-c", "Riverside Apartments", 1320.0, now + timedelta(days=16), "unpaid"),
        ("cust-a", "Maple Street Homes", 975.0, now + timedelta(days=30), "sent"),
    ]
    for j, (cid, name, total, due, status) in enumerate(open_defs):
        invoices.append({
            "id": f"demo-inv-open-{j}", "tenant_id": "demo",
            "customer_id": cid, "customer_name": name,
            "amount_due": total, "total": total, "status": status,
            "created_at": iso(due - timedelta(days=20)), "due_date": iso(due),
        })
    # Scheduled appointments with linked estimates.
    estimates_by_id: Dict[str, dict] = {}
    appts: List[dict] = []
    job_defs = [
        ("scheduled", 4, 1200.0, "cust-a", "Maple Street Homes", "Furnace tune-up"),
        ("confirmed", 9, 3400.0, "cust-b", "Downtown Dental", "AC install"),
        ("scheduled", 16, 750.0, "cust-c", "Riverside Apartments", "Drain clearing"),
        ("on_site", 1, 2100.0, "cust-a", "Maple Street Homes", "Water heater swap"),
        ("en_route", 2, 520.0, "cust-b", "Downtown Dental", "Filter change"),
    ]
    for j, (status, days_out, total, cid, name, svc) in enumerate(job_defs):
        qid = f"demo-est-{j}"
        estimates_by_id[qid] = {"id": qid, "total": total}
        appts.append({
            "id": f"demo-appt-{j}", "tenant_id": "demo",
            "status": status, "start_at": iso(now + timedelta(days=days_out)),
            "customer_id": cid, "customer_name": name,
            "service_name": svc, "quote_id": qid,
        })
    # Sent purchase orders.
    pos = [
        {"id": "demo-po-1", "tenant_id": "demo", "supplier_name": "ACME Supply",
         "status": "sent", "sent_at": iso(now - timedelta(days=3)),
         "lines": [{"qty": 4, "unit_cost": 210.0}, {"qty": 10, "unit_cost": 18.5}]},
        {"id": "demo-po-2", "tenant_id": "demo", "supplier_name": "Valley Distributors",
         "status": "sent", "sent_at": iso(now - timedelta(days=1)),
         "lines": [{"qty": 2, "unit_cost": 640.0}]},
    ]
    settings = {
        "starting_balance": 12000.0,
        "safety_threshold": 8000.0,
        "monthly_payroll": 9600.0,
        "default_days_to_pay": 7,
        "supplier_payment_days": 14,
        "invoice_terms_days": 30,
    }
    return invoices, appts, estimates_by_id, pos, settings
