"""Tests for the trade-pack infrastructure (trade_packs package + routers/trade_packs.py).

Unit tests for load/validate always run. Pack-count-dependent tests
(every-file validation, list==33) SKIP until all 33 pack YAML files
exist — pack authors are still writing them.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "trade-packs-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "trade_packs_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import trade_packs as tp_mod  # noqa: E402
import routers.trade_packs as tp_router_mod  # noqa: E402
from security import require_tenant_user  # noqa: E402

PACKS_DIR = Path(tp_mod.PACKS_DIR)
EXPECTED_PACK_COUNT = 33


def pack_files():
    if not PACKS_DIR.is_dir():
        return []
    return sorted(PACKS_DIR.glob("*.yaml"), key=lambda p: p.stem)


def packs_ready() -> bool:
    return len(pack_files()) >= EXPECTED_PACK_COUNT


def valid_pack_ids():
    """Ids of files that currently parse+validate (subset while packs are drafted)."""
    ids = []
    for path in pack_files():
        try:
            tp_mod.load_pack(path.stem)
            ids.append(path.stem)
        except ValueError:
            continue
    return ids


# ---------- minimal valid pack for unit tests ----------

def _price_item(i):
    return {
        "name": f"Service {i}",
        "category": ["service", "part", "labor", "addon"][i % 4],
        "price": 100.0 + i,
        "cost": 40.0,
        "duration_min": 60,
        "description": f"Description {i}",
    }


def make_valid_pack(**over):
    data = {
        "id": "test-trade",
        "trade": "Test Trade",
        "tagline": "A test trade pack",
        "terms": {
            "worker": "tech", "worker_plural": "techs", "shop": "shop",
            "job": "job", "job_plural": "jobs",
            "customer": "customer", "customer_plural": "customers",
        },
        "colors": {"accent": "#123456"},
        "pricebook": [_price_item(i) for i in range(8)],
        "equipment_types": [{"name": "Unit", "fields": ["brand", "model"]}],
        "appointment_types": [
            {"name": "Diagnostic", "duration_min": 60, "description": "Diag visit"},
            {"name": "Install", "duration_min": 240, "description": "Install visit"},
        ],
        "receptionist_script": "Hello, thank you for calling. How can we help?",
        "triage": [
            {"id": "q1", "question": "Is it urgent?", "options": ["yes", "no"], "route": "dispatch"},
            {"id": "q2", "question": "Where is it?", "options": ["home", "business"], "route": "book"},
        ],
        "automation_templates": [
            {"name": "Reminder", "trigger": "day_before", "steps": ["send_sms"]},
            {"name": "Review ask", "trigger": "job_done", "steps": ["send_sms"]},
        ],
        "review_templates": [
            {"name": "SMS ask", "channel": "sms", "template": "Thanks! Leave a review: {{link}}"},
            {"name": "Email ask", "channel": "email", "template": "Thanks! Leave a review: {{link}}"},
        ],
    }
    data.update(over)
    return data


# ---------- validate_pack unit tests ----------

def test_validate_pack_happy_path():
    data = make_valid_pack()
    assert tp_mod.validate_pack(data) is data


@pytest.mark.parametrize("key", tp_mod.TOP_LEVEL_KEYS)
def test_validate_pack_missing_top_level_key_names_field(key):
    data = make_valid_pack()
    del data[key]
    with pytest.raises(ValueError, match=key):
        tp_mod.validate_pack(data)


def test_validate_pack_pricebook_too_short():
    data = make_valid_pack(pricebook=[_price_item(i) for i in range(7)])
    with pytest.raises(ValueError, match="pricebook"):
        tp_mod.validate_pack(data)


def test_validate_pack_negative_price_names_field():
    item = _price_item(0)
    item["price"] = -5
    data = make_valid_pack(pricebook=[item] + [_price_item(i) for i in range(1, 8)])
    with pytest.raises(ValueError, match="price"):
        tp_mod.validate_pack(data)


def test_validate_pack_bad_category_names_field():
    item = _price_item(0)
    item["category"] = "hourly"
    data = make_valid_pack(pricebook=[item] + [_price_item(i) for i in range(1, 8)])
    with pytest.raises(ValueError, match="category"):
        tp_mod.validate_pack(data)


def test_validate_pack_empty_script_names_field():
    data = make_valid_pack(receptionist_script="  ")
    with pytest.raises(ValueError, match="receptionist_script"):
        tp_mod.validate_pack(data)


def test_validate_pack_review_channel_names_field():
    data = make_valid_pack(review_templates=[
        {"name": "X", "channel": "carrier_pigeon", "template": "Hi"},
        {"name": "Y", "channel": "sms", "template": "Hi"},
    ])
    with pytest.raises(ValueError, match="channel"):
        tp_mod.validate_pack(data)


def test_validate_pack_missing_term_names_field():
    data = make_valid_pack()
    del data["terms"]["worker"]
    with pytest.raises(ValueError, match="terms"):
        tp_mod.validate_pack(data)


# ---------- loader unit tests ----------

def test_load_pack_unknown_raises_valueerror():
    with pytest.raises(ValueError, match="Unknown trade pack"):
        tp_mod.load_pack("definitely-not-a-real-pack")


def test_list_packs_summaries_have_shape():
    tp_mod._clear_cache()
    for s in tp_mod.list_packs():
        assert {"id", "trade", "tagline", "accent"} <= set(s.keys())


# ---------- pack-count-dependent tests ----------

def _need_packs():
    return pytest.mark.skipif(
        not packs_ready(),
        reason=f"pack authors still drafting: {len(pack_files())}/{EXPECTED_PACK_COUNT} files present",
    )


@_need_packs()
@pytest.mark.parametrize("pack_id", [p.stem for p in pack_files()] or ["__none__"])
def test_every_pack_file_parses_and_validates(pack_id):
    tp_mod._clear_cache()
    data = tp_mod.load_pack(pack_id)
    tp_mod.validate_pack(data)
    assert data["id"] == pack_id


@_need_packs()
def test_list_packs_returns_all_33():
    tp_mod._clear_cache()
    packs = tp_mod.list_packs()
    assert len(packs) == EXPECTED_PACK_COUNT
    # Per-file validity is asserted by test_every_pack_file_parses_and_validates;
    # the endpoint lists every file, flagging broken ones with valid=False.


# ---------- router tests (fake mongo) ----------

class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        self.docs = sorted(self.docs, key=lambda d: str(d.get(key, "")), reverse=(direction < 0))
        return self

    async def to_list(self, n):
        return [dict(d) for d in self.docs[:n]]


class FakeCollection:
    def __init__(self):
        self.docs = []

    def find(self, query, proj=None):
        return FakeCursor([d for d in self.docs if all(d.get(k) == v for k, v in query.items())])

    async def find_one(self, query, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("id"))

    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                d.update(update.get("$set", {}))
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            doc = dict(query)
            doc.update(update.get("$set", {}))
            self.docs.append(doc)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=True)
        return SimpleNamespace(matched_count=0, modified_count=0)


class FakeDB:
    def __init__(self):
        self.pricebook_items = FakeCollection()
        self.tenant_settings = FakeCollection()


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(tp_router_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(tp_router_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


@pytest.fixture
def sample_pack(monkeypatch, tmp_path):
    """A valid on-disk pack the router can load, independent of the 33 draft files."""
    tp_mod._clear_cache()
    data = make_valid_pack(id="sample-hvac", trade="Sample HVAC")
    import yaml as _yaml

    path = tmp_path / "sample-hvac.yaml"
    path.write_text(_yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setattr(tp_mod, "PACKS_DIR", tmp_path)
    yield data
    tp_mod._clear_cache()


def test_list_endpoint_shape(client, sample_pack):
    as_user("t1")
    r = client.get("/tenants/trade-packs")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    summary = body["packs"][0]
    assert summary["id"] == "sample-hvac"
    assert summary["trade"] == "Sample HVAC"
    assert summary["tagline"] == sample_pack["tagline"]
    assert summary["accent"] == "#123456"


@_need_packs()
def test_list_endpoint_returns_33_when_packs_ready(client):
    as_user("t1")
    r = client.get("/tenants/trade-packs")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == EXPECTED_PACK_COUNT
    assert len(body["packs"]) == EXPECTED_PACK_COUNT


def test_get_endpoint_returns_full_pack(client, sample_pack):
    as_user("t1")
    r = client.get("/tenants/trade-packs/sample-hvac")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "sample-hvac"
    assert len(body["pricebook"]) == 8
    assert body["receptionist_script"]
    assert len(body["triage"]) == 2


def test_get_unknown_pack_404(client):
    as_user("t1")
    r = client.get("/tenants/trade-packs/nope-not-real")
    assert r.status_code == 404


def test_apply_seeds_items_and_is_idempotent(client, fake_db, sample_pack):
    as_user("t1")
    r1 = client.post("/tenants/trade-packs/sample-hvac/apply")
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1 == {"applied": 8, "skipped": 0, "pack_id": "sample-hvac"}

    rows = fake_db.pricebook_items.docs
    assert len(rows) == 8
    row = rows[0]
    assert row["tenant_id"] == "t1"
    assert row["source"] == "trade-pack:sample-hvac"
    assert row["active"] is True
    assert row["created_at"]
    assert row["id"]

    # tenant_settings upserted
    settings = fake_db.tenant_settings.docs
    assert len(settings) == 1
    assert settings[0]["tenant_id"] == "t1"
    assert settings[0]["trade_pack"]["pack_id"] == "sample-hvac"
    assert len(settings[0]["trade_pack"]["appointment_types"]) == 2
    assert len(settings[0]["trade_pack"]["equipment_types"]) == 1

    # second apply: everything skipped, no duplicates
    r2 = client.post("/tenants/trade-packs/sample-hvac/apply")
    assert r2.json() == {"applied": 0, "skipped": 8, "pack_id": "sample-hvac"}
    assert len(fake_db.pricebook_items.docs) == 8
    # upsert again, still one settings doc
    assert len(fake_db.tenant_settings.docs) == 1


def test_apply_skips_only_preexisting_names(client, fake_db, sample_pack):
    as_user("t1")
    fake_db.pricebook_items.docs.append({
        "id": "x", "tenant_id": "t1", "name": "Service 0", "active": True,
    })
    r = client.post("/tenants/trade-packs/sample-hvac/apply")
    assert r.json() == {"applied": 7, "skipped": 1, "pack_id": "sample-hvac"}
    assert len(fake_db.pricebook_items.docs) == 8


def test_apply_is_tenant_scoped(client, fake_db, sample_pack):
    as_user("t1")
    client.post("/tenants/trade-packs/sample-hvac/apply")
    as_user("t2")
    r = client.post("/tenants/trade-packs/sample-hvac/apply")
    assert r.json()["applied"] == 8
    names_t2 = [d["name"] for d in fake_db.pricebook_items.docs if d["tenant_id"] == "t2"]
    assert len(names_t2) == 8


def test_apply_unknown_pack_404(client):
    as_user("t1")
    r = client.post("/tenants/trade-packs/nope-not-real/apply")
    assert r.status_code == 404


def test_apply_requires_owner_or_admin(client, sample_pack):
    as_user("t1", role="tech")
    r = client.post("/tenants/trade-packs/sample-hvac/apply")
    assert r.status_code == 403
    as_user("t1", role="admin")
    r = client.post("/tenants/trade-packs/sample-hvac/apply")
    assert r.status_code == 200
