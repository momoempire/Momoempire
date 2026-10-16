"""Market packs: per-country skins (colors, languages, flag) for the 46
Stripe-supported markets, plus the trade x market skin resolver.

A shop picks a trade pack (content) and a market pack (face); the resolver
merges them so every trade gets all 46 market faces.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from db import get_db  # noqa: F401  (kept for parity with other routers)
from market_packs import list_packs, load_pack
from market_packs.resolver import resolve_face
from security import require_tenant_user

router = APIRouter(prefix="/tenants/market-packs", tags=["market-packs"])


@router.get("/resolve")
async def resolve_market_face(
    trade: str = Query(..., description="Trade pack id, e.g. hvac"),
    market: str = Query(..., description="Market pack id, e.g. de"),
    user: dict = Depends(require_tenant_user),
):
    """Merged trade x market face. Market colors override trade colors;
    languages come from the market; trade content is served as-is."""
    try:
        return resolve_face(trade, market)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("")
@router.get("/")
async def market_packs_list(user: dict = Depends(require_tenant_user)):
    """Pack summaries: id, name, flag, accent, languages, extended_network."""
    packs = list_packs()
    return {"packs": packs, "count": len(packs)}


@router.get("/{market_id}")
async def market_pack_detail(market_id: str, user: dict = Depends(require_tenant_user)):
    """Full market pack dict for one market."""
    try:
        return load_pack(market_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
