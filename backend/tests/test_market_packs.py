"""Tests for market packs (46 Stripe markets) + the trade x market skin resolver.

Uses the real YAML packs on disk (no DB). API tests use the fake
in-memory mongo pattern from test_reputation.py.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

os.environ.setdefault("JWT_SECRET", "market-packs-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "market_packs_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import market_packs as mp_mod  # noqa: E402
from market_packs import list_packs, load_pack, validate_pack  # noqa: E402
from market_packs.resolver import resolve_face  # noqa: E402
import routers.market_packs as mp_router  # noqa: E402
from security import require_tenant_user  # noqa: E402

PACKS_DIR = Path(mp_mod.__file__).resolve().parent / "packs"

EXPECTED_IDS = sorted([
    "au", "at", "be", "br", "bg", "ca", "cy", "cz", "dk", "ee", "fi",
    "fr", "de", "gh", "gi", "hk", "hu", "ie", "it", "jp", "ke", "lv",
    "li", "lt", "lu", "my", "mt", "mx", "nl", "nz", "ng", "no", "pl",
    "pt", "ro", "sg", "sk", "si", "za", "es", "se", "ch", "th", "ae",
    "gb", "us",
])

EXTENDED_NETWORK = {"gh", "ke", "ng", "za"}


# ---------- schema validation: all 46 packs ----------

def _all_pack_files():
    return sorted(PACKS_DIR.glob("*.yaml"), key=lambda p: p.stem)


def test_pack_count_is_46():
    files = _all_pack_files()
    assert len(files) == 46, f"expected 46 market packs, got {len(files)}"
    assert sorted(p.stem for p in files) == EXPECTED_IDS


@pytest.mark.parametrize("pack_id", EXPECTED_IDS)
def test_each_pack_validates(pack_id):
    data = load_pack(pack_id)
    assert validate_pack(data) is data
    assert data["id"] == pack_id
    assert data["name"].strip()
    assert data["flag"].strip()
    assert data["colors"]["accent"].strip()
    assert isinstance(data["languages"], list) and len(data["languages"]) >= 1
    assert data["extended_network"] is (pack_id in EXTENDED_NETWORK)


def test_list_packs_returns_46_valid():
    packs = list_packs()
    assert len(packs) == 46
    assert all(p["valid"] for p in packs), [p for p in packs if not p["valid"]]
    assert {p["id"] for p in packs} == set(EXPECTED_IDS)


def test_unknown_pack_raises():
    with pytest.raises(ValueError, match="Unknown market pack"):
        load_pack("xx")


# ---------- resolver ----------

def test_resolve_override_hvac_germany():
    face = resolve_face("hvac", "de")
    assert face["trade_id"] == "hvac"
    assert face["market_id"] == "de"
    # Market colors override trade colors
    assert face["colors"]["accent"] == "#FFCC00"  # Germany, not HVAC's #1E6FB8
    # Languages come from the market
    assert face["languages"] == ["de", "en"]
    assert face["flag"] == "🇩🇪"
    # Trade content served as-is
    assert face["trade"] == "HVAC"
    assert len(face["pricebook"]) >= 8
    assert face["receptionist_script"].strip()
    assert len(face["triage"]) >= 2


def test_resolve_plumbing_japan():
    face = resolve_face("plumbing", "jp")
    assert face["colors"]["accent"] == "#BC002D"
    assert face["languages"] == ["ja", "en"]
    assert face["flag"] == "🇯🇵"
    assert face["trade"] == "Plumbing"


def test_resolve_fallback_to_trade_colors(monkeypatch):
    """When the market defines no colors, trade colors apply (fallback rule)."""
    import market_packs.resolver as resolver_mod

    real_loader = resolver_mod.load_market_pack

    def no_color_market(market_id):
        data = dict(real_loader(market_id))
        data["colors"] = {}
        return data

    monkeypatch.setattr(resolver_mod, "load_market_pack", no_color_market)
    face = resolve_face("hvac", "de")
    assert face["colors"]["accent"] == "#1E6FB8"  # HVAC trade fallback


def test_resolve_unknown_trade_404():
    with pytest.raises(ValueError, match="Unknown trade pack"):
        resolve_face("nope", "de")


def test_resolve_unknown_market_404():
    with pytest.raises(ValueError, match="Unknown market pack"):
        resolve_face("hvac", "xx")


# ---------- API ----------

class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        return self

    async def to_list(self, n):
        return [dict(d) for d in self.docs[:n]]


class FakeCollection:
    def __init__(self):
        self.docs = []

    def find(self, query, proj=None):
        return FakeCursor([])

    async def find_one(self, query, proj=None):
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("id"))

    async def update_one(self, query, update, upsert=False):
        return SimpleNamespace(matched_count=0)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(mp_router.router)

    async def fake_user():
        return {"id": "u1", "tenant_id": "t1", "role": "owner"}

    app.dependency_overrides[require_tenant_user] = fake_user
    return TestClient(app)


def test_api_list(client):
    r = client.get("/tenants/market-packs")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 46
    assert len(body["packs"]) == 46


def test_api_detail(client):
    r = client.get("/tenants/market-packs/de")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Germany"
    assert body["flag"] == "🇩🇪"


def test_api_detail_404(client):
    r = client.get("/tenants/market-packs/xx")
    assert r.status_code == 404


def test_api_resolve(client):
    r = client.get("/tenants/market-packs/resolve", params={"trade": "hvac", "market": "de"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["colors"]["accent"] == "#FFCC00"
    assert body["languages"] == ["de", "en"]
    assert body["trade"] == "HVAC"


def test_api_resolve_404(client):
    r = client.get("/tenants/market-packs/resolve", params={"trade": "hvac", "market": "xx"})
    assert r.status_code == 404
    r = client.get("/tenants/market-packs/resolve", params={"trade": "xx", "market": "de"})
    assert r.status_code == 404
