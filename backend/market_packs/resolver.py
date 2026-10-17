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
  Exception (WS-1, Mexico playbook §3.1): market "mx" additionally carries
  an es-MX overlay (trade name / terms / MXN display prices) from the
  ``locales`` pack — additive keys only; the base face is unchanged.

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

    face = {
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

    if market_id == "mx":
        # WS-1 (Mexico playbook §3.1): the MX face carries the es-MX overlay —
        # trade name + worker noun as Mexican customers say them, the ES-MX
        # role terms, and MXN display prices for the trade pricebook
        # (display-only conversion at the playbook FX assumption; the
        # pricebook itself stays in its authored currency).
        from locales import es_mx_terms, es_mx_trade_overlay, usd_to_mxn_display

        overlay = es_mx_trade_overlay(trade_id) or {}
        face["locale"] = "es-MX"
        face["currency"] = "MXN"
        face["trade_es_mx"] = overlay.get("trade", "")
        face["terms_es_mx"] = {**es_mx_terms(), **{
            k: overlay[k] for k in ("worker", "worker_plural") if overlay.get(k)
        }}
        face["pricebook_display"] = [
            {"name": item.get("name", ""), "price_display": usd_to_mxn_display(float(item.get("price") or 0))}
            for item in face["pricebook"]
        ]

    return face
