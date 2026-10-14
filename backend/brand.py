"""Public brand used in emails, invoices and widget text. One central setting.

Pattern: "Mind At Ease Solutions <Niche>" at https://<niche>.mindateasesolutions.com.
NICHE (default "hvac") picks the niche; BRAND_NAME / BRAND_SLOGAN override. Keep in sync with the
frontend's src/config/brand.js (REACT_APP_NICHE / _BRAND_NAME / _BRAND_SLOGAN). Never translated.
"""
import os
import re

BRAND_ROOT = "Mind At Ease Solutions"
BRAND_DOMAIN = "mindateasesolutions.com"
DEFAULT_NICHE = "hvac"
NICHE_LABELS = {
    "hvac": "HVAC",
    "plumbing": "Plumbing",
    "electrical": "Electrical",
    "roofing": "Roofing",
    "landscaping": "Landscaping",
}
DEFAULT_BRAND_SLOGAN = "Your slogan here"


def niche_slug(raw: str | None = None) -> str:
    s = (raw if raw is not None else os.environ.get("NICHE") or "").strip().lower()
    return s if re.fullmatch(r"[a-z0-9-]+", s) else DEFAULT_NICHE


def brand_for_niche(raw: str | None = None) -> dict:
    niche = niche_slug(raw)
    label = NICHE_LABELS.get(niche) or " ".join(w.capitalize() for w in niche.split("-"))
    return {"niche": niche, "name": f"{BRAND_ROOT} {label}", "site_url": f"https://{niche}.{BRAND_DOMAIN}"}


DEFAULT_BRAND_NAME = brand_for_niche(DEFAULT_NICHE)["name"]
DEFAULT_SITE_URL = brand_for_niche(DEFAULT_NICHE)["site_url"]


def brand_name() -> str:
    return (os.environ.get("BRAND_NAME") or "").strip() or brand_for_niche()["name"]


def brand_slogan() -> str:
    return (os.environ.get("BRAND_SLOGAN") or "").strip() or DEFAULT_BRAND_SLOGAN


def brand_site_url() -> str:
    return brand_for_niche()["site_url"]
