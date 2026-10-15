"""Real-time per-job profitability (#11): pure calculation functions.

All money math uses :class:`~decimal.Decimal` — no floats — and is rounded
to cents (``quantize(CENT)``, half-up) at every public boundary.

Data contract (dicts, never ORM models):
    job: an ``appointments`` doc. Hours come from ``logged_hours`` /
         ``actual_hours`` / ``hours`` when present; otherwise they are derived
         from ``start_at``/``end_at``; jobs with no hours degrade to 0.
    lines: invoice/estimate line items
           ``{description, quantity, unit_price, service_id, pricebook_item_id}``
    pricebook_by_id: mapping of pricebook item id -> item doc with a ``cost`` field
    settings: tenant profitability settings (see ``default_settings``)

TODO(LIVE): wire tech hourly rates + actual hours to the payroll feed once it
lands (Phase B). Until then rates come from owner-configured settings and
hours come from the job record / scheduled window.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Mapping, Optional

CENT = Decimal("0.01")
HUNDRED = Decimal("100")


def to_decimal(value: Any) -> Decimal:
    """Coerce anything money-like to Decimal. Never raises; garbage -> 0."""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip().replace(",", "").replace("$", "") or "0")
    except (InvalidOperation, ValueError, AttributeError):
        return Decimal("0")


def cents(value: Decimal) -> Decimal:
    """Round to whole cents, half-up."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------- settings --

DEFAULT_HOURLY_RATE = Decimal("45.00")   # fallback when nothing is configured
DEFAULT_OVERHEAD_RATE = Decimal("12.00")  # $12/hour or 12% depending on method


def default_settings() -> dict:
    return {
        "default_hourly_rate": str(DEFAULT_HOURLY_RATE),
        "hourly_rates": {},          # tech_id -> rate, or "role:<role>" -> rate
        "overhead_rate": str(DEFAULT_OVERHEAD_RATE),
        "overhead_method": "per-hour",  # or "pct-of-revenue"
    }


def normalize_settings(raw: Mapping[str, Any]) -> dict:
    """Validate a PUT /settings body. Raises ValueError on bad input."""
    raw = dict(raw or {})
    method = str(raw.get("overhead_method", "per-hour") or "per-hour").strip().lower()
    if method not in {"per-hour", "pct-of-revenue"}:
        raise ValueError("overhead_method must be 'per-hour' or 'pct-of-revenue'")

    rates: dict[str, str] = {}
    for k, v in (raw.get("hourly_rates") or {}).items():
        rate = to_decimal(v)
        if rate < 0:
            raise ValueError(f"hourly_rates[{k}] must be >= 0")
        rates[str(k)] = str(cents(rate))

    default_rate = to_decimal(raw.get("default_hourly_rate", DEFAULT_HOURLY_RATE))
    overhead_rate = to_decimal(raw.get("overhead_rate", DEFAULT_OVERHEAD_RATE))
    if default_rate < 0 or overhead_rate < 0:
        raise ValueError("default_hourly_rate and overhead_rate must be >= 0")

    return {
        "default_hourly_rate": str(cents(default_rate)),
        "hourly_rates": rates,
        "overhead_rate": str(cents(overhead_rate)),
        "overhead_method": method,
    }


def merge_settings(stored: Optional[Mapping[str, Any]], patch: Mapping[str, Any]) -> dict:
    """Layer a validated patch over stored (or default) settings."""
    merged = default_settings()
    for key in ("default_hourly_rate", "hourly_rates", "overhead_rate", "overhead_method"):
        if stored and stored.get(key) is not None:
            merged[key] = stored[key]
    merged.update(patch)
    return merged


# ------------------------------------------------------------------- hours ---

def _parse_dt(value: Any) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def job_hours(job: Mapping[str, Any]) -> Decimal:
    """Logged hours for a job, gracefully degrading to 0.

    Precedence: logged_hours -> actual_hours -> hours -> start_at/end_at
    scheduled window -> 0.
    """
    for key in ("logged_hours", "actual_hours", "hours"):
        val = to_decimal(job.get(key))
        if val > 0:
            return cents(val)
    start, end = _parse_dt(job.get("start_at")), _parse_dt(job.get("end_at"))
    if start and end and end > start:
        return cents(Decimal(str((end - start).total_seconds())) / Decimal("3600"))
    return Decimal("0")


def hourly_rate_for(
    tech_id: Optional[str], role: Optional[str], settings: Mapping[str, Any]
) -> Decimal:
    """Tech-specific rate wins, then role rate (``role:<role>``), then default."""
    rates = (settings or {}).get("hourly_rates") or {}
    if tech_id and str(tech_id) in rates:
        return to_decimal(rates[str(tech_id)])
    if role and f"role:{role}" in rates:
        return to_decimal(rates[f"role:{role}"])
    return to_decimal((settings or {}).get("default_hourly_rate", DEFAULT_HOURLY_RATE))


# -------------------------------------------------------------------- cost ---

def labor_cost(hours: Decimal, hourly_rate: Decimal) -> Decimal:
    return cents(to_decimal(hours) * to_decimal(hourly_rate))


def parts_cost(
    lines: Iterable[Mapping[str, Any]],
    pricebook_by_id: Mapping[str, Mapping[str, Any]],
) -> Decimal:
    """Sum of qty x pricebook cost for each line.

    Lines carry ``pricebook_item_id`` when drafted from the pricebook; older
    lines only carry ``service_id``, which we also try against the pricebook
    map. Unknown items contribute 0 (parts sold without a recorded cost).
    """
    total = Decimal("0")
    for line in lines or []:
        item = None
        for key in ("pricebook_item_id", "service_id"):
            ident = line.get(key)
            if ident and ident in pricebook_by_id:
                item = pricebook_by_id[ident]
                break
        cost = to_decimal((item or {}).get("cost"))
        qty = to_decimal(line.get("quantity")) or Decimal("1")
        total += cost * qty
    return cents(total)


def overhead_allocated(
    hours: Decimal, revenue: Decimal, settings: Mapping[str, Any]
) -> Decimal:
    """Per-hour: overhead_rate x hours. Pct-of-revenue: rate% x revenue."""
    method = str((settings or {}).get("overhead_method") or "per-hour").strip().lower()
    rate = to_decimal((settings or {}).get("overhead_rate", DEFAULT_OVERHEAD_RATE))
    if method == "pct-of-revenue":
        return cents(to_decimal(revenue) * rate / HUNDRED)
    return cents(to_decimal(hours) * rate)


def gross_profit(revenue: Decimal, total_cost: Decimal) -> Decimal:
    return cents(to_decimal(revenue) - to_decimal(total_cost))


def margin_pct(profit: Decimal, revenue: Decimal) -> Decimal:
    """Margin as a percentage (e.g. 42.50). 0 when revenue is 0 — no crash."""
    revenue = to_decimal(revenue)
    if revenue == 0:
        return Decimal("0")
    return (to_decimal(profit) / revenue * HUNDRED).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# -------------------------------------------------------------- breakdowns ---

def _revenue_from_doc(doc: Optional[Mapping[str, Any]]) -> tuple[Decimal, str]:
    """Revenue + its source label from an invoice or estimate doc.

    Invoices store ``total_cents``; estimates store ``total`` (dollars).
    Returns (revenue, source) where source is 'invoice' | 'quote' | 'none'.
    """
    if not doc:
        return Decimal("0"), "none"
    if doc.get("total_cents") is not None:
        return cents(to_decimal(doc.get("total_cents")) / HUNDRED), "invoice"
    if doc.get("total") is not None:
        return cents(to_decimal(doc.get("total"))), "quote"
    return Decimal("0"), "none"


def job_breakdown(
    job: Mapping[str, Any],
    revenue_doc: Optional[Mapping[str, Any]],
    pricebook_by_id: Mapping[str, Mapping[str, Any]],
    settings: Mapping[str, Any],
) -> dict:
    """Full live margin breakdown for one job. All values are Decimal."""
    settings = {**default_settings(), **(settings or {})}
    revenue, revenue_source = _revenue_from_doc(revenue_doc)
    lines = (revenue_doc or {}).get("lines") or []

    hours = job_hours(job)
    rate = hourly_rate_for(job.get("staff_id"), job.get("staff_role"), settings)
    labor = labor_cost(hours, rate)
    parts = parts_cost(lines, pricebook_by_id)
    overhead = overhead_allocated(hours, revenue, settings)
    total_cost = cents(labor + parts + overhead)
    profit = gross_profit(revenue, total_cost)

    return {
        "job_id": job.get("id"),
        "tenant_id": job.get("tenant_id"),
        "customer_name": job.get("customer_name", ""),
        "service_name": job.get("service_name") or job.get("title") or "",
        "status": job.get("status", ""),
        "tech": {"id": job.get("staff_id") or "", "name": job.get("staff") or ""},
        "revenue": revenue,
        "revenue_source": revenue_source,
        "hours": hours,
        "hourly_rate": rate,
        "labor_cost": labor,
        "parts_cost": parts,
        "overhead_allocated": overhead,
        "total_cost": total_cost,
        "gross_profit": profit,
        "margin_pct": margin_pct(profit, revenue),
    }


def aggregate_breakdowns(breakdowns: list[dict]) -> dict:
    """Period rollup: totals + per-tech + per-job-type groups."""
    revenue = sum((b["revenue"] for b in breakdowns), Decimal("0"))
    cost = sum((b["total_cost"] for b in breakdowns), Decimal("0"))
    profit = gross_profit(revenue, cost)

    by_tech: dict[str, dict] = {}
    by_job_type: dict[str, dict] = {}
    for b in breakdowns:
        tech_key = b["tech"]["name"] or b["tech"]["id"] or "unassigned"
        group = by_tech.setdefault(tech_key, {"revenue": Decimal("0"), "cost": Decimal("0"), "jobs": 0})
        group["revenue"] += b["revenue"]
        group["cost"] += b["total_cost"]
        group["jobs"] += 1

        jt = b["service_name"] or "other"
        jgroup = by_job_type.setdefault(jt, {"revenue": Decimal("0"), "cost": Decimal("0"), "jobs": 0})
        jgroup["revenue"] += b["revenue"]
        jgroup["cost"] += b["total_cost"]
        jgroup["jobs"] += 1

    def _group_row(group: dict) -> dict:
        gprofit = gross_profit(group["revenue"], group["cost"])
        return {
            "revenue": group["revenue"],
            "cost": group["cost"],
            "profit": gprofit,
            "margin_pct": margin_pct(gprofit, group["revenue"]),
            "jobs": group["jobs"],
        }

    return {
        "jobs": len(breakdowns),
        "revenue": revenue,
        "cost": cost,
        "profit": profit,
        "margin_pct": margin_pct(profit, revenue),
        "by_tech": {k: _group_row(v) for k, v in sorted(by_tech.items())},
        "by_job_type": {k: _group_row(v) for k, v in sorted(by_job_type.items())},
    }
