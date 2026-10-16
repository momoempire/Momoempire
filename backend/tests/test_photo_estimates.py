"""Unit tests for AI photo estimates (services/vision.py,
routers/photo_estimates.py).

No database, network, or browser: a small FastAPI app with only the
photo-estimates router, a fake in-memory Mongo, overridden auth
dependencies, and env-var-driven provider selection (no live vision calls).
"""
from __future__ import annotations

import os
import sys
import types

import pytest

os.environ.setdefault("JWT_SECRET", "photo-estimates-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "photo_estimates_test")

# Stub `stripe` before importing call_to_payment (unit env has no stripe pkg).
_stripe_stub = types.ModuleType("stripe")
_stripe_stub.api_key = None


class _StripeError(Exception):
    pass


_stripe_stub.error = types.SimpleNamespace(StripeError=_StripeError)
sys.modules.setdefault("stripe", _stripe_stub)

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.photo_estimates as pe_mod  # noqa: E402
from routers.photo_estimates import match_pricebook_item  # noqa: E402
from security import require_tenant_user  # noqa: E402
from services.vision import (  # noqa: E402
    GeminiVisionProvider,
    MockVisionProvider,
    OpenAICompatibleVisionProvider,
    VisionNotConfigured,
    resolve_provider,
)


# ---------- fake mongo ----------

class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        self.docs = sorted(self.docs, key=lambda d: str(d.get(key, "")),
                           reverse=(direction < 0))
        return self

    async def to_list(self, n):
        return [dict(d) for d in self.docs[:n]]


def _match(doc, query):
    for k, cond in query.items():
        if k == "$or":
            if not any(_match(doc, sub) for sub in cond):
                return False
            continue
        val = doc.get(k)
        if isinstance(cond, dict):
            for op, opval in cond.items():
                if op == "$in" and val not in opval:
                    return False
                elif op == "$ne" and val == opval:
                    return False
                elif op not in ("$in", "$ne"):
                    return False
        elif val != cond:
            return False
    return True


class FakeCollection:
    def __init__(self):
        self.docs = []

    def find(self, query, proj=None):
        return FakeCursor([d for d in self.docs if _match(d, query)])

    async def find_one(self, query, proj=None):
        for d in self.docs:
            if _match(d, query):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_one(self, query, update, upsert=False):
        matched = 0
        for d in self.docs:
            if _match(d, query):
                matched += 1
                for k, v in update.get("$set", {}).items():
                    d[k] = v
                break
        if matched == 0 and upsert:
            doc = dict(query)
            doc.update(update.get("$set", {}))
            self.docs.append(doc)
        return types.SimpleNamespace(matched_count=matched, modified_count=matched)


class FakeDB:
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        coll = FakeCollection()
        setattr(self, name, coll)
        return coll


# ---------- fixtures ----------

current_user = {"tenant_id": "t1", "id": "u1", "role": "owner"}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(pe_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(pe_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


UP = "/tenants/photo-estimates"


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200


def _upload(client, payload=PNG_BYTES, filename="job.png",
            content_type="image/jpeg", trade="hvac"):
    return client.post(
        f"{UP}/upload",
        files={"file": (filename, payload, content_type)},
        data={"trade": trade, "notes": "leaking unit"},
    )


# ---------- upload validation ----------

def test_upload_rejects_non_image_mime(client):
    r = client.post(f"{UP}/upload", files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 400
    assert "image" in r.json()["detail"].lower()


def test_upload_rejects_oversize(monkeypatch, client):
    monkeypatch.setattr(pe_mod, "MAX_IMAGE_BYTES", 100)
    r = _upload(client, payload=b"\x89PNG" + b"\x00" * 500)
    assert r.status_code == 400
    assert "10MB" in r.json()["detail"]


def test_upload_rejects_empty(client):
    r = client.post(f"{UP}/upload", files={"file": ("x.png", b"", "image/png")})
    assert r.status_code == 400


def test_upload_happy_path_hides_image_bytes(client):
    r = _upload(client)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "uploaded"
    assert body["size_bytes"] == len(PNG_BYTES)
    assert body["trade"] == "hvac"
    assert "image_bytes" not in body


def test_get_estimate_hides_image_bytes(client, fake_db):
    est_id = _upload(client).json()["id"]
    doc = fake_db.photo_estimates.docs[0]
    assert doc["image_bytes"] == PNG_BYTES  # stored server-side
    r = client.get(f"{UP}/{est_id}")
    assert r.status_code == 200
    assert "image_bytes" not in r.json()


# ---------- pricebook matching ----------

def test_match_pricebook_item_direct():
    catalog = [
        {"id": "a", "name": "HVAC diagnostic visit", "description": "",
         "sku": "", "active": True},
        {"id": "b", "name": "Capacitor replacement", "description": "",
         "sku": "", "active": True},
        {"id": "c", "name": "Old item", "description": "", "sku": "",
         "active": False},
    ]
    assert match_pricebook_item("diagnostic visit at home", catalog)["id"] == "a"
    assert match_pricebook_item("replace the capacitor", catalog)["id"] == "b"
    assert match_pricebook_item("water heater anode rod", catalog) is None
    assert match_pricebook_item("old item catalog", catalog) is None  # inactive skipped
    assert match_pricebook_item("", catalog) is None


def test_analyze_maps_suggestions_to_pricebook(client, fake_db):
    fake_db.pricebook_items.docs.append({
        "id": "pb1", "tenant_id": "t1", "name": "HVAC diagnostic visit",
        "description": "", "sku": "", "price": 149.0, "cost": 20.0,
        "options": [], "active": True, "version": 3,
    })
    est_id = _upload(client).json()["id"]
    r = client.post(f"{UP}/{est_id}/analyze")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "analyzed"
    assert body["provider"] == "mock"  # no keys in test env
    sugg = body["suggestions"]
    assert len(sugg) == 2
    # "Diagnostic visit (general)" ~ "HVAC diagnostic visit" → Jaccard 2/4 = 0.5
    first = sugg[0]
    assert first["suggested_pricebook_item_id"] == "pb1"
    assert first["pricebook_match_name"] == "HVAC diagnostic visit"
    assert 0 <= first["confidence"] <= 1
    # "Replacement part (generic)" matches nothing → generic line item
    assert sugg[1]["suggested_pricebook_item_id"] is None


def test_analyze_gemini_key_prefers_gemini_then_mock_fallback(monkeypatch, client):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    est_id = _upload(client).json()["id"]
    r = client.post(f"{UP}/{est_id}/analyze")
    assert r.status_code == 200
    body = r.json()
    # Gemini skeleton has no live implementation yet → mock fallback engages
    assert body["provider"] == "mock"
    assert all(s["mock"] for s in body["suggestions"])
    assert len(body["suggestions"]) == 2


# ---------- provider selection ----------

def test_resolve_provider_prefers_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    assert isinstance(resolve_provider(), GeminiVisionProvider)


def test_resolve_provider_openai_when_no_gemini(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    assert isinstance(resolve_provider(), OpenAICompatibleVisionProvider)


def test_resolve_provider_mock_default(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert isinstance(resolve_provider(), MockVisionProvider)


def test_resolve_provider_override():
    assert isinstance(resolve_provider("mock"), MockVisionProvider)
    assert isinstance(resolve_provider("gemini"), GeminiVisionProvider)
    assert isinstance(resolve_provider("openai"), OpenAICompatibleVisionProvider)


@pytest.mark.asyncio
async def test_mock_provider_deterministic():
    p = MockVisionProvider()
    a = await p.suggest_line_items(b"img", {"trade": "hvac"})
    b = await p.suggest_line_items(b"other", {"trade": "hvac"})
    assert [s.description for s in a] == [s.description for s in b]
    assert "hvac" in a[0].description  # trade-neutral context is embedded
    assert all(s.mock for s in a)
    assert all(0 <= s.confidence <= 1 for s in a)


@pytest.mark.asyncio
async def test_live_skeletons_raise_without_live_calls(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(VisionNotConfigured):
        await GeminiVisionProvider().suggest_line_items(b"x", {})
    with pytest.raises(VisionNotConfigured):
        await OpenAICompatibleVisionProvider().suggest_line_items(b"x", {})
    # Key present but implementation still a TODO(LIVE) stub → still raises,
    # i.e. no live network call can happen.
    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    with pytest.raises(VisionNotConfigured):
        await GeminiVisionProvider().suggest_line_items(b"x", {})
    with pytest.raises(VisionNotConfigured):
        await OpenAICompatibleVisionProvider().suggest_line_items(b"x", {})


# ---------- approve → quote draft ----------

def test_approve_creates_real_draft(client, fake_db):
    fake_db.pricebook_items.docs.append({
        "id": "pb1", "tenant_id": "t1", "name": "HVAC diagnostic visit",
        "description": "", "sku": "", "price": 149.0, "cost": 20.0,
        "options": [], "active": True, "version": 3,
    })
    est_id = _upload(client).json()["id"]
    client.post(f"{UP}/{est_id}/analyze")
    r = client.post(f"{UP}/{est_id}/approve", json={
        "customer_name": "Dana Scully",
        "customer_email": "dana@example.com",
        "title": "Photo estimate — unit not cooling",
        "lines": [
            # human accepts the pricebook match; catalog price wins over 999
            {"description": "HVAC diagnostic visit", "qty": 1,
             "unit_price": 999.0, "pricebook_item_id": "pb1"},
            # generic line keeps the human's price
            {"description": "Travel fee", "qty": 1, "unit_price": 45.0},
        ],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "approved"
    quote = body["quote"]
    assert quote["id"] == body["quote_id"]
    assert quote["status"] == "draft"
    assert quote["customer_name"] == "Dana Scully"
    assert quote["total"] == 149.0 + 45.0
    assert quote["lines"][0]["unit_price"] == 149.0
    assert quote["lines"][0]["pricebook_item_id"] == "pb1"
    assert quote["lines"][0]["pricebook_version"] == 3
    assert quote["lines"][1]["unit_price"] == 45.0
    # real doc persisted in the shared estimates collection
    assert len(fake_db.estimates.docs) == 1
    assert fake_db.estimates.docs[0]["id"] == quote["id"]
    assert fake_db.photo_estimates.docs[0]["status"] == "approved"


def test_approve_rejects_unknown_pricebook_item(client, fake_db):
    est_id = _upload(client).json()["id"]
    r = client.post(f"{UP}/{est_id}/approve", json={
        "customer_name": "X", "title": "t",
        "lines": [{"description": "x", "qty": 1, "unit_price": 1,
                   "pricebook_item_id": "nope"}],
    })
    assert r.status_code == 404


def test_approve_twice_conflicts(client):
    est_id = _upload(client).json()["id"]
    payload = {"customer_name": "X", "title": "t",
               "lines": [{"description": "x", "qty": 1, "unit_price": 1}]}
    assert client.post(f"{UP}/{est_id}/approve", json=payload).status_code == 200
    assert client.post(f"{UP}/{est_id}/approve", json=payload).status_code == 409


# ---------- tenant isolation ----------

def test_tenant_isolation(client, fake_db):
    est_id = _upload(client).json()["id"]
    current_user["tenant_id"] = "t2"
    try:
        assert client.get(f"{UP}/{est_id}").status_code == 404
        assert client.post(f"{UP}/{est_id}/analyze").status_code == 404
        assert client.post(f"{UP}/{est_id}/approve", json={
            "customer_name": "Y", "title": "t",
            "lines": [{"description": "x", "qty": 1, "unit_price": 1}],
        }).status_code == 404
        # other tenant's upload lands in its own scope
        other = _upload(client).json()
        assert other["id"] != est_id
    finally:
        current_user["tenant_id"] = "t1"
