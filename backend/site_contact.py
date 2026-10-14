"""Public contact address and site URL for emails and outbound requests.

No real domain is hard-coded: defaults are reserved placeholders until Brann picks real ones.
CONTACT_EMAIL / SITE_URL mirror the frontend's REACT_APP_CONTACT_EMAIL / REACT_APP_SITE_URL.
"""
import os

import brand

DEFAULT_CONTACT_EMAIL = "contact@example.com"


def contact_email() -> str:
    return (os.environ.get("CONTACT_EMAIL") or "").strip() or DEFAULT_CONTACT_EMAIL


def site_url() -> str:
    """Public site origin without trailing slash; defaults to the niche brand URL."""
    return (os.environ.get("SITE_URL") or "").strip().rstrip("/") or brand.brand_site_url()
