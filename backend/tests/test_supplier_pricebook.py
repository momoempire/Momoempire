"""Unit tests for supplier-fed pricebook updates (routers/supplier_pricing.py).

Zero network, zero database: a small FastAPI app with only the supplier-pricing
router, a fake in-memory Mongo, and an overridden auth dependency.

Coverage:
- Margin math (price = cost / (1 - margin)) incl. all rounding rules.
- CSV feed parsing (header validation, bad rows, blank lines).
- Preview performs ZERO writes.
- Apply creates a NEW pricebook version; old version rows are never mutated.
- Deterministic mock feed (same supplier+sku -> same cost).
- Tenant isolation on every endpoint.
- SKU mapping override beats direct SKU match.
- /price-history returns only supplier-driven changes.
"""
from __future__ import annotations

import copy
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "supplier-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "supplier_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.supplier_pricing as sp_mod  # noqa: E402
from security import require_tenant_user  # noqa: E402
from routers.supplier_pricing import (  # noqa: E402
    REASON_SUPPLIER_COST_CHANGE,
    compute_flat_price,
    deterministic_mock_cost,
    parse_csv_feed,
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
        if doc.get(k) != cond:
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
        return SimpleNamespace(inserted_id=doc.get("id"))

    async def update_one(self, query, update):
        matched = 0
        for d in self.docs:
            if _match(d, query):
                matched += 1
                for k, v in update.get("$set", {}).items():
                    d[k] = v
                break
        return SimpleNamespace(matched_count=matched, modified_count=matched)

    async def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if _match(d, query):
                del self.docs[i]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def delete_many(self, query):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _match(d, query)]
        return SimpleNamespace(deleted_count=before - len(self.docs))


class FakeDB:
    def __init__(self):
        self.tenants = FakeCollection()
        self.pricebook_items = FakeCollection()
        self.pricebook_versions = FakeCollection()
        self.supplier_catalogs = FakeCollection()
        self.supplier_feed_lines = FakeCollection()
        self.supplier_sku_mappings = FakeCollection()


# ---------- fixtures ----------

current_user = {}
_db = None


@pytest.fixture
def fake_db():
    global _db
    _db = FakeDB()
    return _db


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(sp_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(sp_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def make_tenant(db, tid):
    db.tenants.docs.append({"id": tid, "name": f"Shop {tid}"})


def make_item(db, tid, name="Capacitor replacement", price=200.0, cost=100.0,
              sku="CAP-35-5", category="part", version=1, iid=None):
    iid = iid or f"item-{tid}-{len(db.pricebook_items.docs)}"
    db.pricebook_items.docs.append({
        "id": iid, "tenant_id": tid, "name": name, "category": category,
        "price": price, "cost": cost, "sku": sku, "active": True, "version": version,
        "options": [],
    })
    return iid


def make_version(db, tid, item_id, version, price, cost, reason=None):
    doc = {
        "id": f"ver-{item_id}-{version}", "item_id": item_id, "tenant_id": tid,
        "version": version,
        "snapshot": {"name": "n", "price": price, "cost": cost, "options": []},
        "changed_by": "u", "changed_at": "2026-01-01T00:00:00",
    }
    if reason:
        doc["reason"] = reason
    db.pricebook_versions.docs.append(doc)
    return doc


def make_supplier(client, name="ACME Parts", feed_type="mock"):
    r = client.post("/supplier-pricing/suppliers",
                    json={"name": name, "feed_type": feed_type})
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---------- margin math ----------

def test_compute_flat_price_basic():
    # 50% margin on price: $100 cost -> $200 sell
    assert compute_flat_price(100.0, 50.0, "none") == 200.0
    assert compute_flat_price(100.0, 0.0, "none") == 100.0
    assert compute_flat_price(0.0, 50.0, "dollar") == 0.0


def test_compute_flat_price_rounding_rules():
    # cost 100, margin 40% -> raw 166.666...
    assert compute_flat_price(100.0, 40.0, "none") == 166.67
    assert compute_flat_price(100.0, 40.0, "dollar") == 167.0
    assert compute_flat_price(100.0, 40.0, "five") == 165.0
    assert compute_flat_price(100.0, 40.0, "ten") == 170.0
    assert compute_flat_price(100.0, 40.0, "charm99") == 166.99


def test_compute_flat_price_validation():
    with pytest.raises(ValueError):
        compute_flat_price(100.0, 100.0, "dollar")  # margin 100% -> div by zero
    with pytest.raises(ValueError):
        compute_flat_price(100.0, -5.0, "dollar")
    with pytest.raises(ValueError):
        compute_flat_price(100.0, 50.0, "bogus")


def test_deterministic_mock_cost_stable():
    a = deterministic_mock_cost("sup-1", "CAP-35-5", 100.0)
    b = deterministic_mock_cost("sup-1", "CAP-35-5", 100.0)
    assert a == b
    assert 90.0 <= a <= 110.0
    # different sku -> (almost surely) different drift
    c = deterministic_mock_cost("sup-1", "OTHER-1", 100.0)
    assert isinstance(c, float)


# ---------- CSV parsing ----------

def test_parse_csv_feed_ok():
    lines = parse_csv_feed("sku,cost\nCAP-35-5,112.50\n\ncont-40,  88\n")
    assert lines == [{"sku": "CAP-35-5", "cost": 112.50}, {"sku": "cont-40", "cost": 88.0}]


def test_parse_csv_feed_case_insensitive_headers():
    lines = parse_csv_feed("SKU,COST\nA1,10\n")
    assert lines == [{"sku": "A1", "cost": 10.0}]


def test_parse_csv_feed_errors():
    with pytest.raises(ValueError):
        parse_csv_feed("")
    with pytest.raises(ValueError):
        parse_csv_feed("sku,price\nA1,10\n")  # wrong column
    with pytest.raises(ValueError):
        parse_csv_feed("sku,cost\nA1,not-a-number\n")
    with pytest.raises(ValueError):
        parse_csv_feed("sku,cost\nA1,-5\n")
    with pytest.raises(ValueError):
        parse_csv_feed("sku,cost\n,10\n")  # missing sku


def test_parse_csv_feed_last_wins_on_dup_sku():
    lines = parse_csv_feed("sku,cost\nA1,10\nA1,12\n")
    assert lines == [{"sku": "A1", "cost": 12.0}]


# ---------- suppliers + ingest ----------

def test_create_supplier_validation(client, fake_db):
    make_tenant(fake_db, "t1")
    as_user("t1")
    r = client.post("/supplier-pricing/suppliers",
                    json={"name": "Bad", "feed_type": "pigeon"})
    assert r.status_code == 400
    r = client.post("/supplier-pricing/suppliers", json={"name": "ACME"})
    assert r.status_code == 200
    assert r.json()["feed_type"] == "mock"  # default


def test_ingest_json_items(client, fake_db):
    make_tenant(fake_db, "t1")
    as_user("t1")
    sid = make_supplier(client, feed_type="csv")
    r = client.post(f"/supplier-pricing/suppliers/{sid}/ingest",
                    json={"items": [{"sku": "CAP-35-5", "cost": 112.5},
                                    {"sku": "  ", "cost": 1},      # skipped
                                    {"sku": "CAP-35-5", "cost": 113}]})  # dup: last wins
    assert r.status_code == 200
    body = r.json()
    assert body["lines_ingested"] == 1
    assert body["skipped"] == 1
    line = fake_db.supplier_feed_lines.docs[0]
    assert line["sku"] == "CAP-35-5" and line["cost"] == 113.0
    assert line["tenant_id"] == "t1"


def test_ingest_json_rejects_bad_cost(client, fake_db):
    make_tenant(fake_db, "t1")
    as_user("t1")
    sid = make_supplier(client, feed_type="csv")
    r = client.post(f"/supplier-pricing/suppliers/{sid}/ingest",
                    json={"items": [{"sku": "A1", "cost": -2}]})
    assert r.status_code == 400


def test_ingest_csv_text_body(client, fake_db):
    make_tenant(fake_db, "t1")
    as_user("t1")
    sid = make_supplier(client, feed_type="csv")
    r = client.post(f"/supplier-pricing/suppliers/{sid}/ingest",
                    content="sku,cost\nCAP-35-5,112.50\n",
                    headers={"Content-Type": "text/csv"})
    assert r.status_code == 200, r.text
    assert r.json()["lines_ingested"] == 1


def test_ingest_csv_text_json_wrapper(client, fake_db):
    make_tenant(fake_db, "t1")
    as_user("t1")
    sid = make_supplier(client, feed_type="csv")
    r = client.post(f"/supplier-pricing/suppliers/{sid}/ingest",
                    json={"csv_text": "sku,cost\nA1,10\nB2,20\n"})
    assert r.status_code == 200
    assert r.json()["lines_ingested"] == 2


def test_ingest_csv_bad_returns_400(client, fake_db):
    make_tenant(fake_db, "t1")
    as_user("t1")
    sid = make_supplier(client, feed_type="csv")
    r = client.post(f"/supplier-pricing/suppliers/{sid}/ingest",
                    json={"csv_text": "sku,cost\nA1,banana\n"})
    assert r.status_code == 400


def test_ingest_mock_is_deterministic(client, fake_db):
    make_tenant(fake_db, "t1")
    as_user("t1")
    make_item(fake_db, "t1", sku="CAP-35-5", cost=100.0)
    sid = make_supplier(client, feed_type="mock")
    first = client.post(f"/supplier-pricing/suppliers/{sid}/ingest", json={}).json()
    second = client.post(f"/supplier-pricing/suppliers/{sid}/ingest", json={}).json()
    assert first["lines_ingested"] == second["lines_ingested"] == 1
    lines = fake_db.supplier_feed_lines.docs
    assert len(lines) == 1  # wholesale replace, no dupes
    expected = deterministic_mock_cost(sid, "CAP-35-5", 100.0)
    assert lines[0]["cost"] == expected


def test_ingest_api_feed_returns_501_todo_live(client, fake_db):
    """feed_type 'api' is the declared placeholder: 501, no live calls."""
    make_tenant(fake_db, "t1")
    as_user("t1")
    sid = make_supplier(client, name="Ferguson", feed_type="api")
    r = client.post(f"/supplier-pricing/suppliers/{sid}/ingest", json={})
    assert r.status_code == 501
    assert "TODO(LIVE)" in r.json()["detail"]


def test_ingest_unknown_supplier_404(client, fake_db):
    make_tenant(fake_db, "t1")
    as_user("t1")
    r = client.post("/supplier-pricing/suppliers/nope/ingest", json={})
    assert r.status_code == 404


def test_member_cannot_ingest(client, fake_db):
    make_tenant(fake_db, "t1")
    as_user("t1")
    sid = make_supplier(client)
    as_user("t1", role="member")
    r = client.post(f"/supplier-pricing/suppliers/{sid}/ingest", json={})
    assert r.status_code == 403


# ---------- margin rules ----------

def test_margin_rules_defaults_and_update(client, fake_db):
    make_tenant(fake_db, "t1")
    as_user("t1")
    body = client.get("/supplier-pricing/margin-rules").json()
    assert body["rules"]["rounding"] == "dollar"
    assert body["rules"]["margins"]["part"] == 40.0

    r = client.put("/supplier-pricing/margin-rules",
                   json={"margins": {"part": 35.0}, "default_margin": 45.0,
                         "rounding": "charm99"})
    assert r.status_code == 200
    rules = r.json()["rules"]
    assert rules["margins"]["part"] == 35.0
    assert rules["margins"]["service"] == 50.0  # untouched default survives
    assert rules["rounding"] == "charm99"

    # persisted on the tenant doc
    assert fake_db.tenants.docs[0]["supplier_pricing_rules"]["rounding"] == "charm99"


def test_margin_rules_validation(client, fake_db):
    make_tenant(fake_db, "t1")
    as_user("t1")
    r = client.put("/supplier-pricing/margin-rules",
                   json={"margins": {"part": 100.0}})
    assert r.status_code == 400
    r = client.put("/supplier-pricing/margin-rules",
                   json={"margins": {"part": -1.0}})
    assert r.status_code == 400
    r = client.put("/supplier-pricing/margin-rules", json={"rounding": "bogus"})
    assert r.status_code == 400


# ---------- preview: read-only ----------

def _seed_preview(client, fake_db, tid="t1"):
    make_tenant(fake_db, tid)
    as_user(tid)
    iid = make_item(fake_db, tid, name="Capacitor replacement", price=200.0,
                    cost=100.0, sku="CAP-35-5", category="part")
    make_item(fake_db, tid, name="No SKU service", price=99.0, cost=10.0,
              sku="", category="service")
    sid = make_supplier(client, feed_type="csv")
    client.post(f"/supplier-pricing/suppliers/{sid}/ingest",
                json={"items": [{"sku": "CAP-35-5", "cost": 120.0},
                                {"sku": "ORPHAN-1", "cost": 5.0}]})
    return sid, iid


def test_preview_shows_before_after_without_writing(client, fake_db):
    sid, iid = _seed_preview(client, fake_db)
    before_items = copy.deepcopy(fake_db.pricebook_items.docs)
    before_versions = copy.deepcopy(fake_db.pricebook_versions.docs)

    r = client.post("/supplier-pricing/reprice/preview", json={"supplier_id": sid})
    assert r.status_code == 200
    body = r.json()
    # part margin default 40%, dollar rounding: 120 / 0.6 = 200 -> $200.00
    row = next(x for x in body["rows"] if x["item_id"] == iid)
    assert row["old_cost"] == 100.0 and row["new_cost"] == 120.0
    assert row["old_price"] == 200.0 and row["new_price"] == 200.0
    assert row["margin_pct"] == 40.0 and row["rounding"] == "dollar"
    assert row["changed"] is True  # cost changed even though rounded price didn't
    assert body["summary"]["items_evaluated"] == 1
    assert body["summary"]["unmapped_lines"] == 1  # ORPHAN-1 has no item
    assert body["summary"]["changed"] == 1

    # ZERO writes from preview.
    assert fake_db.pricebook_items.docs == before_items
    assert fake_db.pricebook_versions.docs == before_versions


def test_preview_applies_custom_margin_rules(client, fake_db):
    sid, iid = _seed_preview(client, fake_db)
    client.put("/supplier-pricing/margin-rules",
               json={"margins": {"part": 50.0}, "rounding": "none"})
    row = client.post("/supplier-pricing/reprice/preview",
                      json={"supplier_id": sid}).json()["rows"][0]
    # 120 / 0.5 = 240.00, no rounding
    assert row["new_price"] == 240.0
    assert row["price_delta"] == 40.0


def test_preview_unknown_supplier_404(client, fake_db):
    make_tenant(fake_db, "t1")
    as_user("t1")
    r = client.post("/supplier-pricing/reprice/preview", json={"supplier_id": "nope"})
    assert r.status_code == 404


# ---------- apply: new versions, history append-only ----------

def test_apply_creates_new_version_old_untouched(client, fake_db):
    sid, iid = _seed_preview(client, fake_db)
    # seed a pre-existing version row, like pricebook.py's create_item would
    old_ver = make_version(fake_db, "t1", iid, 1, price=200.0, cost=100.0)
    old_ver_snapshot = copy.deepcopy(old_ver)
    old_item = copy.deepcopy(
        next(d for d in fake_db.pricebook_items.docs if d["id"] == iid))

    client.put("/supplier-pricing/margin-rules",
               json={"margins": {"part": 50.0}, "rounding": "none"})
    r = client.post("/supplier-pricing/reprice/apply", json={"supplier_id": sid})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    applied = body["applied"][0]
    assert applied["item_id"] == iid
    assert applied["old_price"] == 200.0 and applied["new_price"] == 240.0
    assert applied["old_cost"] == 100.0 and applied["new_cost"] == 120.0
    assert applied["version"] == 2

    # live row bumped
    item = next(d for d in fake_db.pricebook_items.docs if d["id"] == iid)
    assert item["price"] == 240.0 and item["cost"] == 120.0 and item["version"] == 2

    # old version row NEVER mutated; exactly one new row appended
    assert len(fake_db.pricebook_versions.docs) == 2
    assert fake_db.pricebook_versions.docs[0] == old_ver_snapshot
    new_ver = fake_db.pricebook_versions.docs[1]
    assert new_ver["version"] == 2
    assert new_ver["reason"] == REASON_SUPPLIER_COST_CHANGE
    assert new_ver["supplier_id"] == sid
    assert new_ver["old_price"] == 200.0 and new_ver["old_cost"] == 100.0
    assert new_ver["snapshot"]["price"] == 240.0
    assert new_ver["snapshot"]["cost"] == 120.0
    # only new keys added to the row shape; the pricebook history reader is unaffected
    assert old_item["version"] == 1


def test_apply_is_idempotent_second_run_applies_nothing(client, fake_db):
    sid, iid = _seed_preview(client, fake_db)
    client.put("/supplier-pricing/margin-rules",
               json={"margins": {"part": 50.0}, "rounding": "none"})
    first = client.post("/supplier-pricing/reprice/apply", json={"supplier_id": sid}).json()
    assert first["count"] == 1
    second = client.post("/supplier-pricing/reprice/apply", json={"supplier_id": sid}).json()
    assert second["count"] == 0  # nothing changed -> no new versions


def test_apply_subset_of_items(client, fake_db):
    make_tenant(fake_db, "t1")
    as_user("t1")
    iid1 = make_item(fake_db, "t1", name="A", price=200.0, cost=100.0, sku="A-1", category="part")
    iid2 = make_item(fake_db, "t1", name="B", price=200.0, cost=100.0, sku="B-1", category="part")
    sid = make_supplier(client, feed_type="csv")
    client.post(f"/supplier-pricing/suppliers/{sid}/ingest",
                json={"items": [{"sku": "A-1", "cost": 150.0}, {"sku": "B-1", "cost": 150.0}]})
    client.put("/supplier-pricing/margin-rules",
               json={"margins": {"part": 50.0}, "rounding": "none"})
    body = client.post("/supplier-pricing/reprice/apply",
                       json={"supplier_id": sid, "item_ids": [iid1]}).json()
    assert body["count"] == 1
    assert body["applied"][0]["item_id"] == iid1
    item2 = next(d for d in fake_db.pricebook_items.docs if d["id"] == iid2)
    assert item2["price"] == 200.0  # untouched


def test_apply_member_forbidden(client, fake_db):
    sid, _ = _seed_preview(client, fake_db)
    as_user("t1", role="member")
    r = client.post("/supplier-pricing/reprice/apply", json={"supplier_id": sid})
    assert r.status_code == 403


# ---------- sku mapping override ----------

def test_sku_mapping_override_beats_direct_match(client, fake_db):
    make_tenant(fake_db, "t1")
    as_user("t1")
    iid_direct = make_item(fake_db, "t1", name="Direct", price=200.0, cost=100.0,
                           sku="FEED-1", category="part")
    iid_mapped = make_item(fake_db, "t1", name="Mapped", price=200.0, cost=100.0,
                           sku="OTHER", category="part")
    sid = make_supplier(client, feed_type="csv")
    client.post(f"/supplier-pricing/suppliers/{sid}/ingest",
                json={"items": [{"sku": "FEED-1", "cost": 150.0}]})
    r = client.post("/supplier-pricing/sku-mappings",
                    json={"supplier_id": sid, "supplier_sku": "FEED-1",
                          "item_id": iid_mapped})
    assert r.status_code == 200
    assert r.json()["created"] is True

    rows = client.post("/supplier-pricing/reprice/preview",
                       json={"supplier_id": sid}).json()["rows"]
    assert [x["item_id"] for x in rows] == [iid_mapped]  # not the direct match
    assert rows[0]["supplier_sku"] == "FEED-1"

    # upsert same supplier+sku -> updates, no duplicate
    r = client.post("/supplier-pricing/sku-mappings",
                    json={"supplier_id": sid, "supplier_sku": "feed-1",
                          "item_id": iid_direct})
    assert r.json()["created"] is False
    assert len(fake_db.supplier_sku_mappings.docs) == 1

    # delete mapping -> direct match resumes
    mid = fake_db.supplier_sku_mappings.docs[0]["id"]
    assert client.delete(f"/supplier-pricing/sku-mappings/{mid}").status_code == 200
    rows = client.post("/supplier-pricing/reprice/preview",
                       json={"supplier_id": sid}).json()["rows"]
    assert [x["item_id"] for x in rows] == [iid_direct]


def test_sku_mapping_rejects_unknown_item(client, fake_db):
    make_tenant(fake_db, "t1")
    as_user("t1")
    sid = make_supplier(client)
    r = client.post("/supplier-pricing/sku-mappings",
                    json={"supplier_id": sid, "supplier_sku": "X", "item_id": "nope"})
    assert r.status_code == 404


# ---------- price history ----------

def test_price_history_only_supplier_driven(client, fake_db):
    sid, iid = _seed_preview(client, fake_db)
    make_version(fake_db, "t1", iid, 1, price=200.0, cost=100.0)  # manual edit, no reason
    client.put("/supplier-pricing/margin-rules",
               json={"margins": {"part": 50.0}, "rounding": "none"})
    client.post("/supplier-pricing/reprice/apply", json={"supplier_id": sid})

    body = client.get("/supplier-pricing/price-history",
                      params={"item_id": iid}).json()
    assert body["count"] == 1  # the manual v1 row is excluded
    change = body["changes"][0]
    assert change["reason"] == REASON_SUPPLIER_COST_CHANGE
    assert change["version"] == 2
    assert change["old_price"] == 200.0

    r = client.get("/supplier-pricing/price-history", params={"item_id": "nope"})
    assert r.status_code == 404


# ---------- tenant isolation ----------

def test_tenant_isolation(client, fake_db):
    sid_a, iid_a = _seed_preview(client, fake_db, tid="ta")

    make_tenant(fake_db, "tb")
    as_user("tb")
    # B sees none of A's suppliers, lines, or mappings
    assert client.get("/supplier-pricing/suppliers").json()["count"] == 0
    assert client.get(f"/supplier-pricing/suppliers/{sid_a}").status_code == 404
    assert client.post(f"/supplier-pricing/suppliers/{sid_a}/ingest",
                       json={"items": []}).status_code == 404
    assert client.post("/supplier-pricing/reprice/preview",
                       json={"supplier_id": sid_a}).status_code == 404
    assert client.post("/supplier-pricing/reprice/apply",
                       json={"supplier_id": sid_a}).status_code == 404
    assert client.get("/supplier-pricing/price-history",
                      params={"item_id": iid_a}).status_code == 404
    # B's own preview is empty even though A has feed lines
    assert client.post("/supplier-pricing/reprice/preview",
                       json={}).json()["summary"]["items_evaluated"] == 0
    # B cannot steal A's margin rules or items via mappings
    r = client.post("/supplier-pricing/sku-mappings",
                    json={"supplier_id": sid_a, "supplier_sku": "X", "item_id": iid_a})
    assert r.status_code == 404
    # A's data untouched by B's requests
    assert len([d for d in fake_db.supplier_feed_lines.docs
                if d["tenant_id"] == "ta"]) == 2


def test_supplier_list_tenant_scoped(client, fake_db):
    make_tenant(fake_db, "ta")
    as_user("ta")
    make_supplier(client, name="A-Supplier")
    make_tenant(fake_db, "tb")
    as_user("tb")
    body = client.get("/supplier-pricing/suppliers").json()
    assert body == {"suppliers": [], "count": 0}
    as_user("ta")
    assert client.get("/supplier-pricing/suppliers").json()["count"] == 1


def test_delete_supplier_cleans_lines_and_mappings(client, fake_db):
    sid, iid = _seed_preview(client, fake_db)
    client.post("/supplier-pricing/sku-mappings",
                json={"supplier_id": sid, "supplier_sku": "CAP-35-5", "item_id": iid})
    r = client.delete(f"/supplier-pricing/suppliers/{sid}")
    assert r.status_code == 200
    assert fake_db.supplier_catalogs.docs == []
    assert fake_db.supplier_feed_lines.docs == []
    assert fake_db.supplier_sku_mappings.docs == []
    # pricebook catalog + history survive
    assert len(fake_db.pricebook_items.docs) == 2
