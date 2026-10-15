"""Price intelligence network: anonymized cross-shop pricing benchmarks.

The data moat: opted-in tenants contribute their pricebook prices into an
anonymized pool. Tenants see aggregates ("shops like yours in Dallas charge
$X for a capacitor replacement") but never who contributed or what any
individual shop charges.

Privacy rules (non-negotiable, enforced in routers/price_intelligence.py):
- Aggregates are returned ONLY when contributor_count >= MIN_CONTRIBUTORS (5).
  Below that the API returns {"insufficient_data": True} with NO numbers.
- Opted-out tenants (the default) are excluded from every aggregation.
- Tenant ids, names, and individual prices are never exposed.
- Region always comes from the tenant's own profile; it is never
  client-supplied, so a caller cannot probe other regions.
"""
from __future__ import annotations

import re
from typing import List, Optional

# Privacy floor: fewer contributors than this => no aggregates at all.
MIN_CONTRIBUTORS = 5

# An item counts as an "opportunity" when the tenant's price sits more than
# this fraction below the network median ("you're leaving money on the table").
OPPORTUNITY_GAP = 0.15

# Within this fraction of the median a price counts as "at" market.
POSITION_TOLERANCE = 0.05


def normalize_item_name(name: str) -> str:
    """Canonical form used to match the "same" service across tenants."""
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def tenant_region(tenant: dict) -> tuple[str, str]:
    """Derive (display, key) region from the tenant's own profile.

    Prefers the first service area, falls back to address city/state.
    The key is the normalized form used for cohort matching.
    """
    areas = tenant.get("service_areas") or []
    display = str(areas[0]) if areas else ""
    if not display:
        addr = tenant.get("address") or {}
        city = str(addr.get("city") or "").strip()
        state = str(addr.get("state") or addr.get("region") or "").strip()
        display = ", ".join(p for p in (city, state) if p) or str(addr.get("country") or "").strip()
    key = re.sub(r"\s+", " ", display.lower()).strip()
    return display, key


def percentile(sorted_values: List[float], q: float) -> float:
    """q-th percentile (0..1) of an already-sorted list, linear interpolation
    (same method as numpy's default)."""
    n = len(sorted_values)
    if n == 0:
        raise ValueError("percentile of empty list")
    if n == 1:
        return float(sorted_values[0])
    pos = (n - 1) * q
    lo = int(pos)
    frac = pos - lo
    hi = min(lo + 1, n - 1)
    return float(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac)


def aggregate_prices(prices: List[float]) -> Optional[dict]:
    """Aggregate a price pool into anonymized benchmark stats.

    Returns None when there are fewer than MIN_CONTRIBUTORS prices — the
    caller must then return {insufficient_data: True} with no numbers.
    """
    clean = sorted(float(p) for p in prices if p is not None)
    if len(clean) < MIN_CONTRIBUTORS:
        return None
    return {
        "median": round(percentile(clean, 0.50), 2),
        "p25": round(percentile(clean, 0.25), 2),
        "p75": round(percentile(clean, 0.75), 2),
        "contributor_count": len(clean),
    }


def position_for(my_price: float, median: float) -> str:
    """Where my price sits vs the network median: below | at | above."""
    if median <= 0:
        return "at"
    rel = (float(my_price) - median) / median
    if rel < -POSITION_TOLERANCE:
        return "below"
    if rel > POSITION_TOLERANCE:
        return "above"
    return "at"


def is_opportunity(my_price: float, median: float) -> bool:
    """True when my price is more than OPPORTUNITY_GAP below the median."""
    if median <= 0:
        return False
    return (median - float(my_price)) / median >= OPPORTUNITY_GAP


def opportunity_gap_pct(my_price: float, median: float) -> float:
    """Percent below median (positive number), e.g. 20.0 for 20% under."""
    if median <= 0:
        return 0.0
    return round((median - float(my_price)) / median * 100, 1)


# TODO(LIVE): at larger scale, add differential privacy (Laplace noise on
# aggregates, or randomized rounding) so even the aggregate numbers cannot be
# reverse-engineered when a cohort is small or membership is known. Also
# consider cohort fuzzing (e.g. blending adjacent regions) and per-item
# minimum-cohort-size overrides for high-sensitivity trades. The 5-contributor
# floor is the v1 k-anonymity guard; DP noise is the v2 upgrade.
