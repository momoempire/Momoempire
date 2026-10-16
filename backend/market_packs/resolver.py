"""Trade x market skin resolver.

Every trade pack gets all 46 market faces: a shop picks a trade (e.g. HVAC)
plus a market (e.g. Germany) and gets the combined face.

Composition rules (user-locked):
- Market pack contributes: colors (theme accent), languages, flag.
- Trade pack contributes: all content (pricebook, scripts, triage,
  templates, etc.) plus its own colors as FALLBACK.
- Resolution: market colors override trade colors when the market defines
  them; otherwise trade colors apply.
- Languages always come from the market pack.
- Trade content is served as-is; translating trade content is a future step.

``resolve_face(trade_id, market_id)`` raises ValueError for unknown ids so
the API layer can map it to 404.
"""
from __future__ import annotations

from typing import Dict

from market_packs import load_pack as load_market_pack
from trade_packs import load_pack as load_trade_pack


def resolve_face(trade_id: str, market_id: str) -> Dict:
    """Merge one trade pack with one market pack into a single face dict."""
    trade = load_trade_pack(trade_id)      # raises ValueError("Unknown trade pack…")
    market = load_market_pack(market_id)   # raises ValueError("Unknown market pack…")

    trade_colors = trade.get("colors") or {}
    market_colors = market.get("colors") or {}
    # Market wins where it defines a color; trade colors are the fallback.
    colors = {**trade_colors, **market_colors}

    return {
        "trade_id": trade_id,
        "market_id": market_id,
        "trade": trade.get("trade", ""),
        "market": {
            "id": market.get("id", market_id),
            "name": market.get("name", ""),
            "flag": market.get("flag", ""),
            "extended_network": bool(market.get("extended_network", False)),
        },
        "flag": market.get("flag", ""),
        "colors": colors,
        "languages": list(market.get("languages") or []),
        # Trade content, served as-is (translation is a future step).
        "tagline": trade.get("tagline", ""),
        "terms": trade.get("terms", {}),
        "pricebook": trade.get("pricebook", []),
        "equipment_types": trade.get("equipment_types", []),
        "appointment_types": trade.get("appointment_types", []),
        "receptionist_script": trade.get("receptionist_script", ""),
        "triage": trade.get("triage", []),
        "automation_templates": trade.get("automation_templates", []),
        "review_templates": trade.get("review_templates", []),
    }
