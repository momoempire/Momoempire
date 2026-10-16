"""Price intelligence network: anonymized cross-shop pricing benchmarks.

Tenants opt in (default OFF) to contribute their pricebook prices to an
anonymized pool. They can then see aggregates — "shops like yours in Dallas
charge $X" — but never who contributed or what any individual shop charges.

Privacy contract (enforced here, tested in tests/test_price_intelligence.py):
- Aggregates are returned ONLY when contributor_count >= 5; below that the
  response is {"insufficient_data": True} with NO pricing numbers at all.
- Opted-out tenants are excluded from every aggregation.
- Region comes from the tenant's OWN profile — there is deliberately no
  client-supplied region parameter, so callers cannot probe other regions.
- Responses never contain tenant ids, names, or individual prices.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from db import get_db
from security import require_tenant_user
from services.price_intelligence import (
    MIN_CONTRIBUTORS,
    aggregate_prices,
    is_opportunity,
    normalize_item_name,
    opportunity_gap_pct,
    position_for,
    tenant_region,
)

router = APIRouter(prefix="/tenants/price-intel", tags=["price-intel"])


# ---------- helpers ----------

async def _get_tenant(db, tenant_id: str) -> dict:
    tenant = await db.tenants.find_one({"id": tenant_id}, {"_id": 0})
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    return tenant


async def _cohort_prices(db, tenant: dict, trade: Optional[str], region_key: str,
                         item_key: str) -> dict:
    """One price per contributing tenant for the normalized item name.

    Returns {"prices": [...], "contributor_count": n} with no tenant ids.

    Only opted-in tenants (price_intel_opt_in=True) are considered, in the
    requested trade (defaulting to the caller's own industry) and the same
    region as the caller. `region_key` always comes from the caller's own
    tenant profile — it is never client-supplied.
    """
    resolved_trade = ((trade or "").strip()
                      or (tenant.get("industry_slug") or "").strip())
    trade_query: dict = {"price_intel_opt_in": True}
    if resolved_trade:
        trade_query["industry_slug"] = resolved_trade
    candidates = await db.tenants.find(trade_query, {"_id": 0}).to_list(10000)

    prices: list[float] = []
    for other in candidates:
        _, other_key = tenant_region(other)
        if region_key and other_key != region_key:
            continue
        items = await db.pricebook_items.find(
            {"tenant_id": other.get("id"), "active": True}, {"_id": 0}
        ).to_list(2000)
        mine = sorted(
            (float(i.get("price") or 0) for i in items
             if normalize_item_name(i.get("name", "")) == item_key),
        )
        if mine:
            prices.append(mine[0])
    return {"prices": prices, "contributor_count": len(prices)}


async def _my_item_price(db, tenant_id: str, item_key: str) -> Optional[dict]:
    items = await db.pricebook_items.find(
        {"tenant_id": tenant_id, "active": True}, {"_id": 0}
    ).to_list(2000)
    for i in sorted(items, key=lambda d: str(d.get("name", ""))):
        if normalize_item_name(i.get("name", "")) == item_key:
            return {"id": i.get("id"), "name": i.get("name", ""), "price": float(i.get("price") or 0)}
    return None


# ---------- consent ----------

@router.post("/opt-in")
async def opt_in(user: dict = Depends(require_tenant_user)):
    """Join the price intelligence network (default is OFF)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await _get_tenant(db, tenant_id)
    await db.tenants.update_one({"id": tenant_id}, {"$set": {"price_intel_opt_in": True}})
    return {"ok": True, "opt_in": True}


@router.post("/opt-out")
async def opt_out(user: dict = Depends(require_tenant_user)):
    """Leave the network. Your prices are immediately excluded from
    aggregation (and you were never individually identifiable anyway)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await _get_tenant(db, tenant_id)
    await db.tenants.update_one({"id": tenant_id}, {"$set": {"price_intel_opt_in": False}})
    return {"ok": True, "opt_in": False}


# ---------- benchmarks ----------

@router.get("/benchmarks")
async def get_benchmarks(
    item_name: str = "",
    trade: str = "",
    user: dict = Depends(require_tenant_user),
):
    """Anonymized benchmark for one service: my price vs the network median,
    p25, p75 — only when >= 5 opted-in shops contribute. Region is taken from
    the tenant's own profile; it is NOT accepted as a query parameter.
    """
    item_key = normalize_item_name(item_name)
    if not item_key:
        raise HTTPException(400, "item_name is required")
    db = get_db()
    tenant = await _get_tenant(db, user["tenant_id"])
    region_display, region_key = tenant_region(tenant)

    cohort = await _cohort_prices(db, tenant, trade or None, region_key, item_key)
    agg = aggregate_prices(cohort["prices"])
    if agg is None:
        # Privacy floor: NO numbers at all below the contributor threshold.
        return {"item_name": item_name.strip(), "region": region_display,
                "insufficient_data": True}

    mine = await _my_item_price(db, user["tenant_id"], item_key)
    my_price = mine["price"] if mine else None
    return {
        "item_name": item_name.strip(),
        "region": region_display,
        "trade": (trade or "").strip() or tenant.get("industry_slug") or "",
        "my_price": my_price,
        "median": agg["median"],
        "p25": agg["p25"],
        "p75": agg["p75"],
        "contributor_count": agg["contributor_count"],
        "position": position_for(my_price if my_price is not None else agg["median"], agg["median"]),
        "insufficient_data": False,
    }


@router.get("/opportunities")
async def get_opportunities(user: dict = Depends(require_tenant_user)):
    """My pricebook items priced >15% below the network median — money left on
    the table. Items without enough network data are skipped, never guessed."""
    db = get_db()
    tenant = await _get_tenant(db, user["tenant_id"])
    region_display, region_key = tenant_region(tenant)

    my_items = await db.pricebook_items.find(
        {"tenant_id": user["tenant_id"], "active": True}, {"_id": 0}
    ).to_list(2000)

    seen: set[str] = set()
    opportunities: list[dict] = []
    for item in my_items:
        item_key = normalize_item_name(item.get("name", ""))
        if not item_key or item_key in seen:
            continue
        seen.add(item_key)
        my_price = float(item.get("price") or 0)
        cohort = await _cohort_prices(db, tenant, None, region_key, item_key)
        agg = aggregate_prices(cohort["prices"])
        if agg is None:
            continue  # not enough data — skip, never guess
        if is_opportunity(my_price, agg["median"]):
            opportunities.append({
                "item_name": item.get("name", ""),
                "my_price": my_price,
                "median": agg["median"],
                "p25": agg["p25"],
                "p75": agg["p75"],
                "gap_pct": opportunity_gap_pct(my_price, agg["median"]),
                "contributor_count": agg["contributor_count"],
            })

    opportunities.sort(key=lambda o: o["gap_pct"], reverse=True)
    return {
        "opportunities": opportunities,
        "count": len(opportunities),
        "region": region_display,
        "trade": tenant.get("industry_slug") or "",
        "min_contributors": MIN_CONTRIBUTORS,
        "opportunity_threshold_pct": 15,
    }
