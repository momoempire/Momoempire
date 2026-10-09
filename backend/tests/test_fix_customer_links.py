"""Fix #4 — customer 'Review & accept' / 'Pay online' links point at real frontend routes.

No network: fake DB, Stripe mocked.
"""
from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "fix004-test")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "fix004")

import public_links  # noqa: E402
from routers import call_to_payment as c2p  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
APP_JS = REPO / "frontend" / "src" / "App.js"


def _frontend_routes() -> list[str]:
    return re.findall(r'<Route\s+path="([^"]+)"', APP_JS.read_text())


def _route_matches(url_path: str, routes: list[str]) -> bool:
    for r in routes:
        pattern = "^" + re.sub(r":[A-Za-z_]+", "[^/]+", r) + "$"
        if re.match(pattern, url_path):
            return True
    return False


# ---------- link builders vs frontend routes ----------
@pytest.mark.parametrize("builder", [public_links.quote_link, public_links.invoice_link])
def test_generated_links_match_registered_routes(builder, monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.com/")
    url = builder("tok-123")
    assert url.startswith("https://app.example.com/") and "//q" not in url and "//i" not in url
    path = url.replace("https://app.example.com", "")
    assert _route_matches(path, _frontend_routes()), (path, _frontend_routes())


def test_c2p_uses_link_builders_not_hardcoded_paths():
    src = Path(c2p.__file__).read_text()
    assert "quote_link(" in src and "invoice_link(" in src
    assert 'f"{base}/q/' not in src and 'f"{base}/i/' not in src


def test_dashboard_copy_link_points_at_registered_route():
    payments = (REPO / "frontend/src/pages/dashboard/Payments.jsx").read_text()
    m = re.search(r"window\.location\.origin\}(/[a-z]+/)\$\{e\.public_token\}", payments)
    assert m, "copy-link not found"
    assert _route_matches(f"{m.group(1)}abc", _frontend_routes())


def test_no_backend_generates_unregistered_customer_paths():
    """Grep backend for customer-facing /estimates/, /invoices/ frontend links (old dead routes)."""
    routes = _frontend_routes()
    for py in (REPO / "backend" / "routers").glob("*.py"):
        for m in re.finditer(r'f"\{(?:base|frontend)[^}]*\}(/[a-z]+/)\{', py.read_text()):
            assert _route_matches(m.group(1) + "x", routes), (py.name, m.group(1))


# ---------- public endpoints ----------
def _match(d, q):
    return all(d.get(k) == v for k, v in q.items())


class Coll:
    def __init__(self, docs=None):
        self.docs = docs or []

    async def find_one(self, q, proj=None):
        for d in self.docs:
            if _match(d, q):
                out = copy.deepcopy(d)
                out.pop("_id", None)
                return out
        return None

    async def update_one(self, q, u, upsert=False):
        return None


class DB:
    def __init__(self):
        self.invoices = Coll([{
            "id": "inv1", "tenant_id": "t1", "public_token": "itok", "title": "AC repair",
            "lines": [{"description": "Repair", "quantity": 1, "unit_price": 150}],
            "total_cents": 15000, "amount_paid_cents": 0, "status": "sent",
        }])
        self.tenants = Coll([{"id": "t1", "name": "Comfort Pros", "stripe_connect_id": "acct_123"}])
        self.estimates = Coll([])
        self.invoice_payments = Coll([])


@pytest.fixture
def client(monkeypatch):
    db = DB()
    monkeypatch.setattr(c2p, "get_db", lambda: db)
    app = FastAPI()
    app.include_router(c2p.public_router, prefix="/api")
    return TestClient(app), db


def test_public_invoice_view(client):
    c, _ = client
    r = c.get("/api/public/c2p/invoices/itok")
    assert r.status_code == 200
    body = r.json()
    assert body["amount_due_cents"] == 15000
    assert body["business"]["accepts_online_pay"] is True
    assert c.get("/api/public/c2p/invoices/nope").status_code == 404


def test_pay_returns_stripe_js_fields(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(c2p.stripe, "api_key", "sk_test_unit")
    monkeypatch.setenv("STRIPE_PUBLISHABLE_KEY", "pk_test_unit")
    fake_intent = {"id": "pi_1", "client_secret": "pi_1_secret_x"}
    with patch.object(c2p.stripe.PaymentIntent, "create", return_value=fake_intent) as create:
        r = c.post("/api/public/c2p/invoices/itok/pay", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["client_secret"] == "pi_1_secret_x"
    assert body["stripe_account"] == "acct_123"
    assert body["publishable_key"] == "pk_test_unit"
    assert "sk_" not in str(body)
    assert create.call_args.kwargs["stripe_account"] == "acct_123"
    assert create.call_args.kwargs["metadata"]["invoice_id"] == "inv1"


def test_pay_unavailable_without_stripe(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(c2p.stripe, "api_key", "")
    r = c.post("/api/public/c2p/invoices/itok/pay", json={})
    assert r.status_code == 503  # frontend shows "online payment isn't available"
