"""Supplier-fed pricebook updates: distributor cost feeds auto-update flat-rate prices.

A tenant registers supplier catalogs (distributors whose parts they buy), ingests
cost feeds, and the re-pricing engine proposes new flat-rate sell prices derived
from the tenant's own margin rules (margin % per category + rounding rules).

Flow:
  1. POST /suppliers                       -- register a supplier (mock | csv | api feed)
  2. POST /suppliers/{id}/ingest           -- load a cost feed (deterministic mock,
                                             JSON [{sku, cost}], or CSV text)
  3. PUT  /margin-rules                    -- set margin % per category + rounding
  4. POST /reprice/preview                 -- proposed new prices, NO writes
  5. POST /reprice/apply                   -- writes NEW pricebook versions with
                                             history entries ("supplier cost change")

Versioning contract (mirrors routers/pricebook.py): apply never mutates an
existing pricebook_versions row and never edits history — it bumps the live
pricebook_items row (price/cost/version) and INSERTS a new version row. Old
quotes keep their frozen line prices because quote lines carry the price they
went out with, not a live reference to the catalog.

Collections (all tenant-scoped):
- `supplier_catalogs`: registered suppliers.
- `supplier_feed_lines`: latest ingested {sku, cost} lines per supplier (replaced
  wholesale on each ingest — a feed is a snapshot).
- `supplier_sku_mappings`: explicit supplier-SKU -> pricebook-item overrides.
- `pricebook_items` / `pricebook_versions`: shared with the pricebook router
  (read items, write new versions only).
- `tenants.supplier_pricing_rules`: this tenant's margin/rounding rules.

Zero live network calls: the "api" feed type is a declared placeholder —
ingesting it returns 501 and every TODO(LIVE) comment marks where a real
distributor integration (EDI 832/850 or distributor REST API) plugs in.
"""
from __future__ import annotations

import csv
import hashlib
import io
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from db import get_db
from models import _now_iso, _uuid
from security import require_tenant_user, require_tenant_owner_or_admin

router = APIRouter(prefix="/supplier-pricing", tags=["supplier-pricing"])

REASON_SUPPLIER_COST_CHANGE = "supplier cost change"

FEED_TYPES = ("mock", "csv", "api")
ROUNDING_RULES = ("none", "dollar", "five", "ten", "charm99")

# Margin % per category used when the tenant has not saved their own rules.
DEFAULT_RULES = {
    "margins": {"service": 50.0, "part": 40.0, "labor": 60.0, "addon": 50.0},
    "default_margin": 50.0,
    "rounding": "dollar",
}


# ---------- pydantic models (local to this module) ----------

class SupplierIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    feed_type: str = "mock"  # mock | csv | api


class FeedLineIn(BaseModel):
    sku: str = Field(min_length=1)
    cost: float = Field(ge=0)


class IngestIn(BaseModel):
    items: Optional[List[FeedLineIn]] = None
    csv_text: Optional[str] = None
    mock: bool = False  # force deterministic mock generation regardless of feed_type


class MarginRulesIn(BaseModel):
    margins: Dict[str, float] = Field(default_factory=dict)  # category -> margin %
    default_margin: float = Field(default=50.0, ge=0, lt=100)
    rounding: str = "dollar"


class SkuMappingIn(BaseModel):
    supplier_id: str
    supplier_sku: str = Field(min_length=1)
    item_id: str


class RepricePreviewIn(BaseModel):
    supplier_id: Optional[str] = None  # default: all suppliers with feed lines


class RepriceApplyIn(BaseModel):
    supplier_id: Optional[str] = None
    item_ids: Optional[List[str]] = None  # apply to a subset of previewed items


# ---------- pure pricing engine ----------

def normalize_sku(sku: str) -> str:
    return (sku or "").strip().lower()


def _apply_rounding(amount: Decimal, rule: str) -> Decimal:
    if rule == "none":
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if rule == "dollar":
        return amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if rule == "five":
        return ((amount / 5).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * 5).quantize(Decimal("0.01"))
    if rule == "ten":
        return ((amount / 10).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * 10).quantize(Decimal("0.01"))
    if rule == "charm99":
        return (amount.to_integral_value(rounding=ROUND_FLOOR) + Decimal("0.99")).quantize(Decimal("0.01"))
    raise ValueError(f"unknown rounding rule: {rule}")


def compute_flat_price(cost: float, margin_pct: float, rounding: str) -> float:
    """Flat-rate sell price from a supplier cost.

    margin_pct is the target margin ON PRICE (e.g. 50 -> price = cost / 0.5),
    the convention most flat-rate books use.
    """
    if not (0 <= margin_pct < 100):
        raise ValueError("margin_pct must be in [0, 100)")
    if rounding not in ROUNDING_RULES:
        raise ValueError(f"unknown rounding rule: {rounding}")
    cost_d = Decimal(str(cost or 0))
    if cost_d <= 0:
        return 0.0
    raw = cost_d / (Decimal(1) - Decimal(str(margin_pct)) / Decimal(100))
    return float(_apply_rounding(raw, rounding))


def parse_csv_feed(text: str) -> List[dict]:
    """Parse a distributor cost feed in CSV text form.

    Requires a header row with `sku` and `cost` columns (case-insensitive).
    Blank rows are skipped; the last occurrence of a SKU wins.
    Raises ValueError with a row number on malformed input.
    """
    if not (text or "").strip():
        raise ValueError("empty CSV feed")
    reader = csv.DictReader(io.StringIO(text.strip()))
    if not reader.fieldnames:
        raise ValueError("CSV feed is missing a header row")
    headers = {h.strip().lower(): h for h in reader.fieldnames if h}
    if "sku" not in headers or "cost" not in headers:
        raise ValueError("CSV feed must have 'sku' and 'cost' columns")
    lines: Dict[str, dict] = {}
    for n, row in enumerate(reader, start=2):
        sku = (row.get(headers["sku"]) or "").strip()
        cost_raw = (row.get(headers["cost"]) or "").strip()
        if not sku and not cost_raw:
            continue  # blank line
        if not sku:
            raise ValueError(f"row {n}: missing sku")
        try:
            cost = float(cost_raw)
        except (TypeError, ValueError):
            raise ValueError(f"row {n}: invalid cost {cost_raw!r}")
        if cost < 0:
            raise ValueError(f"row {n}: cost must be >= 0")
        lines[normalize_sku(sku)] = {"sku": sku, "cost": round(cost, 2)}
    if not lines:
        raise ValueError("CSV feed contained no usable rows")
    return list(lines.values())


def deterministic_mock_cost(supplier_id: str, sku: str, current_cost: float) -> float:
    """Deterministic distributor cost drift for the mock feed.

    Same (supplier, sku) always yields the same cost, drifting -10%..+10% off
    the item's current cost. Zero network, fully reproducible for demos/tests.
    """
    digest = hashlib.sha256(f"{supplier_id}|{normalize_sku(sku)}".encode()).hexdigest()
    drift_pct = (int(digest[:8], 16) % 21) - 10  # -10 .. +10
    return round(float(current_cost) * (1 + drift_pct / 100), 2)


# ---------- db helpers ----------

async def _get_supplier(db, supplier_id: str, tenant_id: str) -> dict:
    supplier = await db.supplier_catalogs.find_one(
        {"id": supplier_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if not supplier:
        raise HTTPException(404, "Supplier not found")
    return supplier


async def _get_rules(db, tenant_id: str) -> dict:
    tenant = await db.tenants.find_one({"id": tenant_id}, {"_id": 0}) or {}
    saved = tenant.get("supplier_pricing_rules") or {}
    margins = dict(DEFAULT_RULES["margins"])
    margins.update({str(k): float(v) for k, v in (saved.get("margins") or {}).items()})
    rounding = saved.get("rounding") or DEFAULT_RULES["rounding"]
    return {
        "margins": margins,
        "default_margin": float(saved.get("default_margin", DEFAULT_RULES["default_margin"])),
        "rounding": rounding,
    }


def _margin_for_category(rules: dict, category: str) -> float:
    return float(rules["margins"].get(category or "", rules["default_margin"]))


async def _mock_lines_for_supplier(db, supplier: dict, tenant_id: str) -> List[dict]:
    """Deterministic mock feed: one cost line per active pricebook item with a SKU."""
    items = await db.pricebook_items.find(
        {"tenant_id": tenant_id, "active": True}, {"_id": 0}
    ).to_list(2000)
    lines: List[dict] = []
    for item in items:
        sku = (item.get("sku") or "").strip()
        if not sku:
            continue
        base_cost = float(item.get("cost") or 0)
        if base_cost <= 0:
            base_cost = float(item.get("price") or 0) * 0.5
        if base_cost <= 0:
            continue
        lines.append({
            "sku": sku,
            "cost": deterministic_mock_cost(supplier["id"], sku, base_cost),
        })
    return lines


async def _resolve_items(db, tenant_id: str) -> Dict[str, dict]:
    """Pricebook items keyed by normalized SKU (first active item wins)."""
    items = await db.pricebook_items.find(
        {"tenant_id": tenant_id, "active": True}, {"_id": 0}
    ).to_list(2000)
    by_sku: Dict[str, dict] = {}
    for item in sorted(items, key=lambda d: str(d.get("name", ""))):
        sku = normalize_sku(item.get("sku") or "")
        if sku and sku not in by_sku:
            by_sku[sku] = item
    return by_sku


async def _build_preview(db, tenant_id: str, supplier_id: Optional[str]) -> dict:
    """Shared engine for preview + apply. Read-only: performs no writes."""
    rules = await _get_rules(db, tenant_id)

    line_query: dict = {"tenant_id": tenant_id}
    if supplier_id:
        await _get_supplier(db, supplier_id, tenant_id)  # 404 if not ours
        line_query["supplier_id"] = supplier_id
    feed_lines = await db.supplier_feed_lines.find(line_query, {"_id": 0}).to_list(5000)

    mappings = await db.supplier_sku_mappings.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(2000)
    mapped_item_ids = {m.get("id") for m in await db.pricebook_items.find(
        {"tenant_id": tenant_id}, {"_id": 0, "id": 1}).to_list(5000)}
    explicit: Dict[tuple, str] = {}
    for m in mappings:
        if m.get("item_id") in mapped_item_ids:
            explicit[(m.get("supplier_id"), normalize_sku(m.get("supplier_sku") or ""))] = m["item_id"]
    items_by_id = {i["id"]: i for i in await db.pricebook_items.find(
        {"tenant_id": tenant_id, "active": True}, {"_id": 0}).to_list(2000)}
    by_sku = await _resolve_items(db, tenant_id)

    rows: List[dict] = []
    unmapped = 0
    for line in feed_lines:
        sku_key = normalize_sku(line.get("sku") or "")
        item = None
        target_id = explicit.get((line.get("supplier_id"), sku_key))
        if target_id:
            item = items_by_id.get(target_id)
        if item is None:
            item = by_sku.get(sku_key)
        if item is None:
            unmapped += 1
            continue
        new_cost = round(float(line.get("cost") or 0), 2)
        old_cost = round(float(item.get("cost") or 0), 2)
        old_price = round(float(item.get("price") or 0), 2)
        margin_pct = _margin_for_category(rules, item.get("category"))
        new_price = compute_flat_price(new_cost, margin_pct, rules["rounding"])
        changed = (new_price != old_price) or (new_cost != old_cost)
        rows.append({
            "item_id": item["id"],
            "item_name": item.get("name", ""),
            "sku": item.get("sku"),
            "supplier_sku": line.get("sku"),
            "supplier_id": line.get("supplier_id"),
            "category": item.get("category", "service"),
            "old_cost": old_cost,
            "new_cost": new_cost,
            "old_price": old_price,
            "new_price": new_price,
            "margin_pct": margin_pct,
            "rounding": rules["rounding"],
            "price_delta": round(new_price - old_price, 2),
            "changed": changed,
        })

    rows.sort(key=lambda r: r["item_name"].lower())
    changed_rows = [r for r in rows if r["changed"]]
    return {
        "rows": rows,
        "summary": {
            "items_evaluated": len(rows),
            "changed": len(changed_rows),
            "unchanged": len(rows) - len(changed_rows),
            "unmapped_lines": unmapped,
            "total_price_delta": round(sum(r["price_delta"] for r in changed_rows), 2),
            "rules": rules,
        },
    }


async def _record_supplier_version(db, item: dict, user: dict, supplier_id: str,
                                  supplier_sku: str, old_price: float, old_cost: float) -> None:
    """Insert a NEW pricebook_versions row for a supplier-driven change.

    Existing version rows are never touched — history is append-only, so old
    quotes (which froze their line prices at draft time) keep old prices.
    """
    await db.pricebook_versions.insert_one({
        "id": _uuid(),
        "item_id": item["id"],
        "tenant_id": item["tenant_id"],
        "version": int(item.get("version") or 1),
        "snapshot": {
            "name": item.get("name"),
            "price": float(item.get("price") or 0),
            "cost": float(item.get("cost") or 0),
            "options": item.get("options") or [],
        },
        "changed_by": user.get("id"),
        "changed_at": _now_iso(),
        "reason": REASON_SUPPLIER_COST_CHANGE,
        "supplier_id": supplier_id,
        "supplier_sku": supplier_sku,
        "old_price": old_price,
        "old_cost": old_cost,
    })


# ---------- suppliers ----------

@router.post("/suppliers")
async def create_supplier(data: SupplierIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Register a supplier catalog. feed_type: mock (deterministic demo feed),
    csv (paste/upload CSV text on ingest), or api (real distributor — TODO(LIVE))."""
    feed_type = (data.feed_type or "mock").strip().lower()
    if feed_type not in FEED_TYPES:
        raise HTTPException(400, f"feed_type must be one of {FEED_TYPES}")
    db = get_db()
    now = _now_iso()
    doc = {
        "id": _uuid(),
        "tenant_id": user["tenant_id"],
        "name": data.name.strip(),
        "feed_type": feed_type,
        "created_at": now,
        "updated_at": now,
        "last_ingest_at": None,
        "line_count": 0,
    }
    await db.supplier_catalogs.insert_one(doc)
    return {k: doc[k] for k in ("id", "name", "feed_type", "created_at", "last_ingest_at", "line_count")}


@router.get("/suppliers")
async def list_suppliers(user: dict = Depends(require_tenant_user)):
    db = get_db()
    rows = await db.supplier_catalogs.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}
    ).sort("name", 1).to_list(500)
    return {"suppliers": rows, "count": len(rows)}


@router.get("/suppliers/{supplier_id}")
async def get_supplier(supplier_id: str, user: dict = Depends(require_tenant_user)):
    db = get_db()
    supplier = await _get_supplier(db, supplier_id, user["tenant_id"])
    lines = await db.supplier_feed_lines.find(
        {"supplier_id": supplier_id, "tenant_id": user["tenant_id"]}, {"_id": 0}
    ).sort("sku", 1).to_list(2000)
    return {**supplier, "lines": lines}


@router.delete("/suppliers/{supplier_id}")
async def delete_supplier(supplier_id: str, user: dict = Depends(require_tenant_owner_or_admin)):
    """Remove a supplier and its ingested lines/mappings (pricebook history stays)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await _get_supplier(db, supplier_id, tenant_id)
    await db.supplier_catalogs.delete_one({"id": supplier_id, "tenant_id": tenant_id})
    await db.supplier_feed_lines.delete_many({"supplier_id": supplier_id, "tenant_id": tenant_id})
    await db.supplier_sku_mappings.delete_many({"supplier_id": supplier_id, "tenant_id": tenant_id})
    return {"ok": True, "id": supplier_id}


@router.post("/suppliers/{supplier_id}/ingest")
async def ingest_feed(supplier_id: str, request: Request,
                      user: dict = Depends(require_tenant_owner_or_admin)):
    """Ingest a cost feed for a supplier. Three shapes, all zero-network:

    - feed_type "mock" (or {"mock": true}): deterministic generated costs.
    - JSON {"items": [{"sku": "...", "cost": 12.34}]}.
    - CSV text (Content-Type text/csv|text/plain, or JSON {"csv_text": "..."})
      with `sku,cost` header columns.
    - feed_type "api": 501 — TODO(LIVE): plug a real distributor EDI/API here.

    The supplier's previous lines are replaced wholesale (a feed is a snapshot).
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    supplier = await _get_supplier(db, supplier_id, tenant_id)

    content_type = (request.headers.get("content-type") or "").lower()
    raw_body = await request.body()
    payload: dict = {}
    if raw_body and "text/" not in content_type and "csv" not in content_type:
        try:
            payload = await request.json()
        except Exception:
            payload = {}

    lines: List[dict]
    skipped = 0
    if supplier["feed_type"] == "api" and not payload.get("mock") \
            and not payload.get("items") and not payload.get("csv_text") \
            and "text/" not in content_type and "csv" not in content_type:
        # TODO(LIVE): authenticate with the distributor (per-tenant credentials on
        # the supplier doc), fetch the current cost catalog via their EDI 832 feed
        # or REST API (e.g. Ferguson/Watsco/Famous Supply), and normalize to the
        # same {sku, cost} lines used below. No live calls until this is wired.
        raise HTTPException(
            501,
            "Distributor API feed is not connected yet (TODO(LIVE)): register the "
            "supplier as 'mock' or 'csv' to reprice from an offline feed.",
        )
    elif "text/" in content_type or "csv" in content_type:
        try:
            lines = parse_csv_feed(raw_body.decode("utf-8", errors="replace"))
        except ValueError as e:
            raise HTTPException(400, f"CSV parse error: {e}")
    elif payload.get("csv_text"):
        try:
            lines = parse_csv_feed(payload["csv_text"])
        except ValueError as e:
            raise HTTPException(400, f"CSV parse error: {e}")
    elif payload.get("items") is not None:
        seen: Dict[str, dict] = {}
        for entry in payload["items"]:
            sku = (entry.get("sku") or "").strip() if isinstance(entry, dict) else ""
            if not sku:
                skipped += 1
                continue
            try:
                cost = float(entry.get("cost"))
            except (TypeError, ValueError, AttributeError):
                raise HTTPException(400, f"invalid cost for sku {sku!r}")
            if cost < 0:
                raise HTTPException(400, f"cost must be >= 0 for sku {sku!r}")
            seen[normalize_sku(sku)] = {"sku": sku, "cost": round(cost, 2)}
        lines = list(seen.values())
    elif supplier["feed_type"] == "mock" or payload.get("mock"):
        lines = await _mock_lines_for_supplier(db, supplier, tenant_id)
    else:
        raise HTTPException(400, "no feed payload: send {items: [...]}, {csv_text}, or use a mock supplier")

    now = _now_iso()
    await db.supplier_feed_lines.delete_many({"supplier_id": supplier_id, "tenant_id": tenant_id})
    for line in lines:
        await db.supplier_feed_lines.insert_one({
            "id": _uuid(),
            "tenant_id": tenant_id,
            "supplier_id": supplier_id,
            "sku": line["sku"],
            "cost": float(line["cost"]),
            "ingested_at": now,
        })
    await db.supplier_catalogs.update_one(
        {"id": supplier_id, "tenant_id": tenant_id},
        {"$set": {"last_ingest_at": now, "line_count": len(lines), "updated_at": now}},
    )
    return {"ok": True, "supplier_id": supplier_id, "lines_ingested": len(lines), "skipped": skipped}


# ---------- margin rules ----------

@router.get("/margin-rules")
async def get_margin_rules(user: dict = Depends(require_tenant_user)):
    """This tenant's re-pricing rules: margin % per category + rounding."""
    db = get_db()
    return {"rules": await _get_rules(db, user["tenant_id"])}


@router.put("/margin-rules")
async def put_margin_rules(data: MarginRulesIn, user: dict = Depends(require_tenant_owner_or_admin)):
    rounding = (data.rounding or "").strip().lower()
    if rounding not in ROUNDING_RULES:
        raise HTTPException(400, f"rounding must be one of {ROUNDING_RULES}")
    margins = {}
    for cat, pct in (data.margins or {}).items():
        try:
            pct_f = float(pct)
        except (TypeError, ValueError):
            raise HTTPException(400, f"invalid margin for category {cat!r}")
        if not (0 <= pct_f < 100):
            raise HTTPException(400, f"margin for category {cat!r} must be in [0, 100)")
        margins[str(cat)] = pct_f
    db = get_db()
    rules = {"margins": margins, "default_margin": float(data.default_margin), "rounding": rounding}
    await db.tenants.update_one(
        {"id": user["tenant_id"]},
        {"$set": {"supplier_pricing_rules": rules, "updated_at": _now_iso()}},
    )
    return {"ok": True, "rules": await _get_rules(db, user["tenant_id"])}


# ---------- sku mappings ----------

@router.get("/sku-mappings")
async def list_sku_mappings(user: dict = Depends(require_tenant_user)):
    """Explicit supplier-SKU -> pricebook-item overrides (used before direct SKU match)."""
    db = get_db()
    rows = await db.supplier_sku_mappings.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}
    ).to_list(2000)
    return {"mappings": rows, "count": len(rows)}


@router.post("/sku-mappings")
async def upsert_sku_mapping(data: SkuMappingIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Pin a supplier SKU to a specific pricebook item (upsert per supplier+SKU)."""
    db = get_db()
    tenant_id = user["tenant_id"]
    await _get_supplier(db, data.supplier_id, tenant_id)
    item = await db.pricebook_items.find_one(
        {"id": data.item_id, "tenant_id": tenant_id}, {"_id": 0})
    if not item:
        raise HTTPException(404, "Pricebook item not found")
    key = {"supplier_id": data.supplier_id, "supplier_sku_norm": normalize_sku(data.supplier_sku),
           "tenant_id": tenant_id}
    existing = await db.supplier_sku_mappings.find_one(key, {"_id": 0})
    if existing:
        await db.supplier_sku_mappings.update_one(
            {"id": existing["id"]}, {"$set": {"item_id": data.item_id, "updated_at": _now_iso()}})
        existing["item_id"] = data.item_id
        return {"ok": True, "mapping": existing, "created": False}
    doc = {
        "id": _uuid(), "tenant_id": tenant_id,
        "supplier_id": data.supplier_id,
        "supplier_sku": data.supplier_sku.strip(),
        "supplier_sku_norm": normalize_sku(data.supplier_sku),
        "item_id": data.item_id,
        "created_at": _now_iso(), "updated_at": _now_iso(),
    }
    await db.supplier_sku_mappings.insert_one(doc)
    return {"ok": True, "mapping": {k: v for k, v in doc.items() if k != "_id"}, "created": True}


@router.delete("/sku-mappings/{mapping_id}")
async def delete_sku_mapping(mapping_id: str, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    res = await db.supplier_sku_mappings.delete_one({"id": mapping_id, "tenant_id": user["tenant_id"]})
    if not res.deleted_count:
        raise HTTPException(404, "Mapping not found")
    return {"ok": True, "id": mapping_id}


# ---------- re-pricing engine ----------

@router.post("/reprice/preview")
async def reprice_preview(data: RepricePreviewIn, user: dict = Depends(require_tenant_user)):
    """Proposed new flat-rate prices from the latest ingested costs. Read-only:
    performs zero writes — inspect before applying."""
    db = get_db()
    return await _build_preview(db, user["tenant_id"], data.supplier_id)


@router.post("/reprice/apply")
async def reprice_apply(data: RepriceApplyIn, user: dict = Depends(require_tenant_owner_or_admin)):
    """Apply supplier-driven re-pricing. For every changed item: bump the live
    pricebook_items row (price/cost/version) and INSERT a new pricebook_versions
    row with reason "supplier cost change". Old version rows and old quotes are
    never mutated."""
    db = get_db()
    tenant_id = user["tenant_id"]
    preview = await _build_preview(db, tenant_id, data.supplier_id)
    wanted = set(data.item_ids or [])
    applied: List[dict] = []
    now = _now_iso()

    for row in preview["rows"]:
        if not row["changed"]:
            continue
        if wanted and row["item_id"] not in wanted:
            continue
        item = await db.pricebook_items.find_one(
            {"id": row["item_id"], "tenant_id": tenant_id}, {"_id": 0})
        if not item or not item.get("active", True):
            continue
        # Re-check against the live row (a concurrent edit may have landed).
        if round(float(item.get("price") or 0), 2) == row["new_price"] and \
           round(float(item.get("cost") or 0), 2) == row["new_cost"]:
            continue
        old_price = round(float(item.get("price") or 0), 2)
        old_cost = round(float(item.get("cost") or 0), 2)
        new_version = int(item.get("version") or 1) + 1
        await db.pricebook_items.update_one(
            {"id": item["id"], "tenant_id": tenant_id},
            {"$set": {"price": row["new_price"], "cost": row["new_cost"],
                      "version": new_version, "updated_at": now}},
        )
        updated = await db.pricebook_items.find_one(
            {"id": item["id"], "tenant_id": tenant_id}, {"_id": 0})
        await _record_supplier_version(db, updated, user, row["supplier_id"],
                                       row["supplier_sku"], old_price, old_cost)
        applied.append({
            "item_id": item["id"], "item_name": row["item_name"],
            "old_price": old_price, "new_price": row["new_price"],
            "old_cost": old_cost, "new_cost": row["new_cost"],
            "version": new_version,
        })

    return {"ok": True, "applied": applied, "count": len(applied),
            "skipped_unchanged": preview["summary"]["unchanged"]}


# ---------- supplier-driven history ----------

@router.get("/price-history")
async def supplier_price_history(item_id: str, user: dict = Depends(require_tenant_user)):
    """Supplier-driven price changes for one pricebook item, newest first."""
    db = get_db()
    tenant_id = user["tenant_id"]
    item = await db.pricebook_items.find_one(
        {"id": item_id, "tenant_id": tenant_id}, {"_id": 0})
    if not item:
        raise HTTPException(404, "Pricebook item not found")
    rows = await db.pricebook_versions.find(
        {"item_id": item_id, "tenant_id": tenant_id, "reason": REASON_SUPPLIER_COST_CHANGE},
        {"_id": 0},
    ).sort("version", -1).to_list(500)
    return {"item_id": item_id, "item_name": item.get("name", ""), "changes": rows, "count": len(rows)}
