"""Customer-facing public link builders.

These paths MUST match routes registered in frontend/src/App.js:
    /q/:token  -> PublicQuote   (Review & accept)
    /i/:token  -> PublicInvoice (Pay online)
    /track/:token -> ETATracking (customer live ETA page)
tests/test_fix_customer_links.py checks quote/invoice paths.
"""
import os

QUOTE_PATH = "/q/"
INVOICE_PATH = "/i/"
ETA_PATH = "/track/"


def _base() -> str:
    return (os.environ.get("FRONTEND_URL") or "").rstrip("/")


def quote_link(token: str) -> str:
    return f"{_base()}{QUOTE_PATH}{token}"


def invoice_link(token: str) -> str:
    return f"{_base()}{INVOICE_PATH}{token}"


def eta_link(token: str) -> str:
    """Customer live-tracking page link (backend: GET /api/public/eta/{token})."""
    return f"{_base()}{ETA_PATH}{token}"
