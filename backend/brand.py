"""Public brand used in emails, invoices and widget text. One central setting.

Pattern: trade faces are "Mind At Ease Solutions <Niche>" at
https://<niche>.mindateasesolutions.com; with NO niche selected the face is the
GENERIC parent brand — plain "Mind At Ease Solutions" on the apex domain, no trade
attached (mirrors the frontend's src/config/brand.js and the bookkeeping app's
GENERIC_PACK default). NICHE (default "generic") picks the niche; BRAND_NAME /
BRAND_SLOGAN override. Keep in sync with the frontend. Never translated.
"""
import os
import re

BRAND_ROOT = "Mind At Ease Solutions"
BRAND_DOMAIN = "mindateasesolutions.com"

# The generic parent face: parent name, generic descriptor, neutral accent, neutral
# glyph. NO trade-specific wording, imagery or terminology — a future pack edit must
# not leak a trade in here (guarded by tests/test_brand_config.py).
GENERIC_NICHE = "generic"
GENERIC_DESCRIPTOR = "AI Office Assistant"
GENERIC_ACCENT = "#334155"  # neutral slate
GENERIC_GLYPH = "Sparkles"  # neutral glyph — the landing chrome's neutral fallback
GENERIC_BRAND = {
    "niche": GENERIC_NICHE,
    "name": BRAND_ROOT,
    "descriptor": GENERIC_DESCRIPTOR,
    "site_url": f"https://{BRAND_DOMAIN}",
    "accent": GENERIC_ACCENT,
    "glyph": GENERIC_GLYPH,
}

DEFAULT_NICHE = GENERIC_NICHE
NICHE_LABELS = {
    "hvac": "HVAC",
    "plumbing": "Plumbing",
    "electrical": "Electrical",
    "roofing": "Roofing",
    "landscaping": "Landscaping",
    "appliance-repair": "Appliance Repair",
    "auto-repair": "Auto Repair",
    "barbershop": "Barbershop",
    "cleaning": "Cleaning",
    "decks-outdoor": "Decks & Outdoor Living",
    "fencing-concrete": "Fencing & Concrete",
    "flooring": "Flooring",
    "foundation-repair": "Foundation Repair",
    "freelancer": "Freelancer",
    "garage-door": "Garage Door",
    "generators": "Whole-Home Generators",
    "handyman": "Handyman",
    "home-inspection": "Home Inspection",
    "irrigation": "Irrigation",
    "junk-removal": "Junk Removal",
    "kitchen-bath": "Kitchen & Bath Remodeling",
    "locksmith": "Locksmith",
    "painting": "Painting",
    "pest-control": "Pest Control",
    "pool-service": "Pool Service",
    "real-estate": "Real Estate",
    "restoration": "Restoration",
    "septic-well": "Septic & Well",
    "siding-gutters": "Siding & Gutters",
    "smart-home": "Smart Home & Security",
    "solar": "Solar",
    "tree-service": "Tree Service",
    "windows-doors": "Windows & Doors",
}
DEFAULT_BRAND_SLOGAN = "Your slogan here"


def niche_slug(raw: str | None = None) -> str:
    s = (raw if raw is not None else os.environ.get("NICHE") or "").strip().lower()
    return s if re.fullmatch(r"[a-z0-9-]+", s) else DEFAULT_NICHE


def brand_for_niche(raw: str | None = None) -> dict:
    niche = niche_slug(raw)
    if niche == GENERIC_NICHE:
        return dict(GENERIC_BRAND)
    label = NICHE_LABELS.get(niche) or " ".join(w.capitalize() for w in niche.split("-"))
    # Trade faces take their accent/icon from their trade pack, not the brand config.
    return {"niche": niche, "name": f"{BRAND_ROOT} {label}", "descriptor": label,
            "site_url": f"https://{niche}.{BRAND_DOMAIN}", "accent": None, "glyph": None}


DEFAULT_BRAND_NAME = brand_for_niche(DEFAULT_NICHE)["name"]
DEFAULT_SITE_URL = brand_for_niche(DEFAULT_NICHE)["site_url"]


def brand_name() -> str:
    return (os.environ.get("BRAND_NAME") or "").strip() or brand_for_niche()["name"]


def brand_descriptor() -> str:
    return brand_for_niche()["descriptor"]


def brand_slogan() -> str:
    return (os.environ.get("BRAND_SLOGAN") or "").strip() or DEFAULT_BRAND_SLOGAN


def brand_site_url() -> str:
    return brand_for_niche()["site_url"]
