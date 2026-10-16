"""Unit tests for the flat-rate pricebook (routers/pricebook.py).

No database, network, or browser needed: a small FastAPI app with only the
pricebook router, a fake in-memory Mongo (with $or support for the shared
customer helper), and an overridden auth dependency.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "pricebook-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "pricebook_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.pricebook as pb_mod  # noqa: E402
from security import require_tenant_user, require_tenant_owner_or_admin  # noqa: E402


# ---------- fake mongo ----------

class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        self.docs = sorted(self.docs, key=lambda d: str(d.get(key, "")), reverse=(direction < 0))
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
                elif op not in ("$in", "$ne", "$or"):
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


class FakeDB:
    def __init__(self):
        self.pricebook_items = FakeCollection()
        self.pricebook_versions = FakeCollection()
        self.estimates = FakeCollection()
        self.customers = FakeCollection()


# ---------- fixtures ----------

current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(pb_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(pb_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def make_item(name="Capacitor replacement", price=189.0, cost=42.0, category="service", **kw):
    body = {"name": name, "category": category, "price": price, "cost": cost,
            "sku": kw.get("sku"), "description": kw.get("description", ""),
            "options": kw.get("options", []), "active": kw.get("active", True)}
    return body


# ---------- CRUD ----------

def test_create_item_owner_ok(client, fake_db):
    as_user("t1")
    r = client.post("/tenants/pricebook/items", json=make_item())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 1
    assert body["margin"] == 147.0
    assert body["active"] is True
    # version history seeded
    hist = client.get(f"/tenants/pricebook/items/{body['id']}/history").json()
    assert len(hist["versions"]) == 1
    assert hist["versions"][0]["snapshot"]["price"] == 189.0


def test_create_item_member_forbidden(client, fake_db):
    as_user("t1", role="member")
    r = client.post("/tenants/pricebook/items", json=make_item())
    assert r.status_code == 403


def test_update_and_delete_member_forbidden(client, fake_db):
    as_user("t1")
    item_id = client.post("/tenants/pricebook/items", json=make_item()).json()["id"]
    as_user("t1", role="member")
    assert client.put(f"/tenants/pricebook/items/{item_id}", json={"price": 200}).status_code == 403
    assert client.delete(f"/tenants/pricebook/items/{item_id}").status_code == 403


def test_tenant_isolation(client, fake_db):
    as_user("t1")
    item_id = client.post("/tenants/pricebook/items", json=make_item()).json()["id"]
    as_user("t2")
    assert client.get("/tenants/pricebook/items").json()["count"] == 0
    assert client.get(f"/tenants/pricebook/items/{item_id}").status_code == 404
    assert client.put(f"/tenants/pricebook/items/{item_id}", json={"price": 1}).status_code == 404
    assert client.delete(f"/tenants/pricebook/items/{item_id}").status_code == 404
    assert client.get(f"/tenants/pricebook/items/{item_id}/history").status_code == 404


def test_list_search_category_active_filters(client, fake_db):
    as_user("t1")
    client.post("/tenants/pricebook/items", json=make_item(name="Capacitor replacement", category="service"))
    client.post("/tenants/pricebook/items", json=make_item(name="Contactor 40A", category="part", sku="CON-40A"))
    client.post("/tenants/pricebook/items", json=make_item(name="Old service", active=False))

    assert client.get("/tenants/pricebook/items").json()["count"] == 2  # active only default
    r = client.get("/tenants/pricebook/items", params={"q": "con-40a"}).json()
    assert r["count"] == 1 and r["items"][0]["name"] == "Contactor 40A"
    r = client.get("/tenants/pricebook/items", params={"category": "part"}).json()
    assert r["count"] == 1
    r = client.get("/tenants/pricebook/items", params={"active_only": "false"}).json()
    assert r["count"] == 3


def test_categories_lists_in_use(client, fake_db):
    as_user("t1")
    client.post("/tenants/pricebook/items", json=make_item(category="hvac-service"))
    client.post("/tenants/pricebook/items", json=make_item(name="x", category="part"))
    cats = client.get("/tenants/pricebook/categories").json()
    assert set(cats["categories"]) == {"hvac-service", "part"}
    assert "service" in cats["suggested"]


# ---------- versioning ----------

def test_price_change_bumps_version_and_history(client, fake_db):
    as_user("t1")
    item = client.post("/tenants/pricebook/items", json=make_item(price=189.0)).json()

    # non-pricing change: no version bump
    r = client.put(f"/tenants/pricebook/items/{item['id']}", json={"description": "new desc"})
    assert r.json()["version"] == 1

    # pricing change: bump + history row
    r = client.put(f"/tenants/pricebook/items/{item['id']}", json={"price": 209.0})
    assert r.json()["version"] == 2
    hist = client.get(f"/tenants/pricebook/items/{item['id']}/history").json()["versions"]
    assert [v["version"] for v in hist] == [1, 2]
    assert hist[0]["snapshot"]["price"] == 189.0
    assert hist[1]["snapshot"]["price"] == 209.0


def test_soft_delete_hides_but_preserves_history(client, fake_db):
    as_user("t1")
    item_id = client.post("/tenants/pricebook/items", json=make_item()).json()["id"]
    assert client.delete(f"/tenants/pricebook/items/{item_id}").json()["active"] is False

    assert client.get("/tenants/pricebook/items").json()["count"] == 0
    # still retrievable directly, history intact
    got = client.get(f"/tenants/pricebook/items/{item_id}").json()
    assert got["active"] is False
    hist = client.get(f"/tenants/pricebook/items/{item_id}/history").json()
    assert len(hist["versions"]) == 1
    # reactivate via update
    r = client.put(f"/tenants/pricebook/items/{item_id}", json={"active": True})
    assert r.json()["active"] is True


# ---------- quote integration ----------

def _draft(client, selections, **kw):
    body = {"customer_name": "Jane Doe", "title": "AC repair", "selections": selections}
    body.update(kw)
    return client.post("/tenants/pricebook/quote-draft", json=body)


def test_quote_draft_from_items_freezes_prices(client, fake_db):
    as_user("t1")
    item = client.post("/tenants/pricebook/items", json=make_item(
        price=189.0, options=[{"name": "After-hours", "price": 75.0}])).json()
    opt_id = item["options"][0]["id"]
    other = client.post("/tenants/pricebook/items", json=make_item(name="Filter", price=29.0, cost=8.0)).json()

    r = _draft(client, [
        {"item_id": item["id"], "qty": 1, "option_ids": [opt_id]},
        {"item_id": other["id"], "qty": 2, "option_ids": []},
    ])
    assert r.status_code == 200, r.text
    quote = r.json()
    assert quote["status"] == "draft"
    assert len(quote["lines"]) == 2
    l1, l2 = quote["lines"]
    assert l1["unit_price"] == 264.0  # 189 + 75 option
    assert l1["quantity"] == 1
    assert l1["pricebook_item_id"] == item["id"]
    assert l1["pricebook_version"] == 1
    assert "After-hours" in l1["description"]
    assert l2["unit_price"] == 29.0 and l2["quantity"] == 2
    assert quote["total"] == 264.0 + 58.0


def test_quote_keeps_old_price_after_price_change(client, fake_db):
    as_user("t1")
    item = client.post("/tenants/pricebook/items", json=make_item(price=189.0)).json()

    old_quote = _draft(client, [{"item_id": item["id"], "qty": 1}]).json()
    assert old_quote["lines"][0]["unit_price"] == 189.0

    client.put(f"/tenants/pricebook/items/{item['id']}", json={"price": 229.0})

    # stored quote is untouched
    stored = [e for e in fake_db.estimates.docs if e["id"] == old_quote["id"]][0]
    assert stored["lines"][0]["unit_price"] == 189.0
    assert stored["total"] == 189.0

    # new drafts use the new price and record the new version
    new_quote = _draft(client, [{"item_id": item["id"], "qty": 1}]).json()
    assert new_quote["lines"][0]["unit_price"] == 229.0
    assert new_quote["lines"][0]["pricebook_version"] == 2


def test_quote_draft_rejects_inactive_or_unknown_items(client, fake_db):
    as_user("t1")
    item_id = client.post("/tenants/pricebook/items", json=make_item(active=False)).json()["id"]
    assert _draft(client, [{"item_id": item_id, "qty": 1}]).status_code == 404
    assert _draft(client, [{"item_id": "nope", "qty": 1}]).status_code == 404
    assert _draft(client, []).status_code == 400


def test_quote_draft_cross_tenant_item_rejected(client, fake_db):
    as_user("t1")
    item_id = client.post("/tenants/pricebook/items", json=make_item()).json()["id"]
    as_user("t2")
    assert _draft(client, [{"item_id": item_id, "qty": 1}]).status_code == 404


def test_quote_draft_bad_option_rejected(client, fake_db):
    as_user("t1")
    item = client.post("/tenants/pricebook/items", json=make_item()).json()
    r = _draft(client, [{"item_id": item["id"], "qty": 1, "option_ids": ["bad-opt"]}])
    assert r.status_code == 404
