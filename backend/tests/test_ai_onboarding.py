"""Unit tests for AI onboarding (routers/ai_onboarding.py + services/telephony.py).

Self-contained: a small FastAPI app with only the onboarding router, a fake
in-memory Mongo, and overridden auth dependencies. Zero network: the
MockTelephonyProvider is deterministic and the XLSX path is exercised without
openpyxl installed (expecting the documented 422).
"""
from __future__ import annotations

import io
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "ai-onboarding-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "ai_onboarding_test")
# Ensure the real Twilio provider is never selected.
os.environ.pop("TWILIO_PARENT_SID", None)
os.environ.pop("TWILIO_PARENT_TOKEN", None)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.ai_onboarding as ob_mod  # noqa: E402
import routers.pricebook as pb_mod  # noqa: E402
import routers.tenants as tenants_mod  # noqa: E402
from security import require_tenant_user  # noqa: E402
from services.telephony import MockTelephonyProvider, _deterministic_sid  # noqa: E402


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
        self.onboarding_uploads = FakeCollection()
        self.onboarding_batches = FakeCollection()
        self.customers = FakeCollection()
        self.pricebook_items = FakeCollection()
        self.pricebook_versions = FakeCollection()
        self.telephony = FakeCollection()


# ---------- fixtures ----------

current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(ob_mod, "get_db", lambda: fake_db)
    monkeypatch.setattr(pb_mod, "get_db", lambda: fake_db)
    monkeypatch.setattr(tenants_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(ob_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


INVOICE_CSV = """Customer,Email,Phone,Description,Amount,Service Date
Acme Plumbing,ops@acmeplumb.com,555-111-2222,Capacitor replacement,$189.00,2026-09-01
Acme Plumbing,ops@acmeplumb.com,555-111-2222,Contactor 40A,42.50,2026-09-03
Jane Homeowner,jane@example.com,555-333-4444,Capacitor replacement,189,2026-09-10
"""

CUSTOMER_CSV = """full name,email address,phone number
Bob Builder,bob@builder.com,555-000-1111
"""


def upload(client, csv_text, filename="invoices.csv"):
    return client.post(
        "/tenants/onboarding/upload",
        files={"file": (filename, io.BytesIO(csv_text.encode()), "text/csv")},
    )


# ---------- column mapping + preview ----------

def test_upload_preview_shape_and_mapping(client):
    as_user("t1")
    r = upload(client, INVOICE_CSV)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file_type"] == "invoices"
    assert body["row_count"] == 3
    m = body["column_mapping"]
    assert m["customer_name"] == "Customer"
    assert m["email"] == "Email"
    assert m["phone"] == "Phone"
    assert m["item"] == "Description"
    assert m["price"] == "Amount"
    assert m["date"] == "Service Date"
    # customers detected + deduped, spend aggregated
    customers = {c["name"]: c for c in body["customers"]}
    assert customers["Acme Plumbing"]["invoice_count"] == 2
    assert customers["Acme Plumbing"]["total_spent"] == pytest.approx(231.50)
    assert customers["Jane Homeowner"]["email"] == "jane@example.com"
    # pricebook candidates grouped with averaged prices
    cands = {c["name"]: c for c in body["pricebook_candidates"]}
    assert cands["Capacitor replacement"]["occurrences"] == 2
    assert cands["Capacitor replacement"]["suggested_price"] == pytest.approx(189.0)
    assert cands["Contactor 40A"]["suggested_price"] == pytest.approx(42.50)


def test_upload_customers_only_file(client):
    as_user("t1")
    r = upload(client, CUSTOMER_CSV, filename="customers.csv")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file_type"] == "customers"
    assert body["column_mapping"]["customer_name"] == "full name"
    assert body["column_mapping"]["email"] == "email address"
    assert len(body["customers"]) == 1
    assert body["pricebook_candidates"] == []


def test_upload_xlsx_without_openpyxl_is_422(client):
    as_user("t1")
    try:
        import openpyxl  # noqa: F401
        pytest.skip("openpyxl installed; skip the missing-dep branch")
    except ImportError:
        pass
    r = client.post(
        "/tenants/onboarding/upload",
        files={"file": ("invoices.xlsx", io.BytesIO(b"not-a-real-xlsx"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 422
    assert "CSV" in r.json()["detail"]


# ---------- import ----------

def _import(client, upload_id, batch_id="batch-1", **kw):
    body = {"upload_id": upload_id, "onboarding_batch_id": batch_id}
    body.update(kw)
    return client.post("/tenants/onboarding/import", json=body)


def test_import_creates_records(client, fake_db):
    as_user("t1")
    upload_id = upload(client, INVOICE_CSV).json()["upload_id"]
    r = _import(client, upload_id)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "complete"
    assert body["idempotent_replay"] is False
    assert {c["name"] for c in body["customers_created"]} == {"Acme Plumbing", "Jane Homeowner"}
    assert {i["name"] for i in body["pricebook_items_created"]} == {"Capacitor replacement", "Contactor 40A"}
    # records are tenant-scoped and reuse the real routers' shapes
    cust_names = [d["name"] for d in fake_db.customers.docs if d["tenant_id"] == "t1"]
    assert sorted(cust_names) == ["Acme Plumbing", "Jane Homeowner"]
    item_names = [d["name"] for d in fake_db.pricebook_items.docs if d["tenant_id"] == "t1"]
    assert sorted(item_names) == ["Capacitor replacement", "Contactor 40A"]
    # pricebook version history seeded by the real create path
    assert len(fake_db.pricebook_versions.docs) == 2


def test_import_is_idempotent_on_retry(client, fake_db):
    as_user("t1")
    upload_id = upload(client, INVOICE_CSV).json()["upload_id"]
    first = _import(client, upload_id, batch_id="batch-9").json()
    second = _import(client, upload_id, batch_id="batch-9").json()
    assert second["idempotent_replay"] is True
    assert second["status"] == "complete"
    assert len(second["customers_created"]) == len(first["customers_created"])
    # no duplicates created on replay
    assert len(fake_db.customers.docs) == 2
    assert len(fake_db.pricebook_items.docs) == 2
    assert len(fake_db.onboarding_batches.docs) == 1


def test_import_skips_already_existing_records(client, fake_db):
    as_user("t1")
    upload_id = upload(client, INVOICE_CSV).json()["upload_id"]
    _import(client, upload_id, batch_id="batch-a")
    # second batch, same data: existing names are skipped, not duplicated
    upload_id2 = upload(client, INVOICE_CSV).json()["upload_id"]
    body = _import(client, upload_id2, batch_id="batch-b").json()
    assert body["customers_created"] == []
    assert len(body["customers_skipped"]) == 2
    assert len(body["pricebook_items_skipped"]) == 2
    assert len(fake_db.customers.docs) == 2
    assert len(fake_db.pricebook_items.docs) == 2


def test_import_tenant_isolation(client, fake_db):
    as_user("t1")
    upload_id = upload(client, INVOICE_CSV).json()["upload_id"]
    _import(client, upload_id, batch_id="batch-t1")
    as_user("t2")
    # t2 cannot see t1's draft
    r = _import(client, upload_id, batch_id="batch-t2")
    assert r.status_code == 404
    # t2's own import creates its own records
    upload_id2 = upload(client, INVOICE_CSV).json()["upload_id"]
    body = _import(client, upload_id2, batch_id="batch-t2").json()
    assert len(body["customers_created"]) == 2
    t2_custs = [d for d in fake_db.customers.docs if d["tenant_id"] == "t2"]
    assert len(t2_custs) == 2


# ---------- telephony ----------

def test_telephony_mock_deterministic(client, fake_db):
    as_user("t1")
    r = client.post("/tenants/onboarding/telephony/provision", json={"mode": "provisioned"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "mock"
    assert body["subaccount_sid"] == _deterministic_sid("t1")
    assert body["mode"] == "provisioned"
    assert len(body["numbers"]) == 1
    assert body["numbers"][0]["type"] == "provisioned"
    # fee model recorded, usage tracked, no charging logic
    assert body["fee_model"]["plan"] == "bundled_flat_fee"
    assert body["fee_model"]["usage"] == {"sms_segments": 0, "voice_minutes": 0}
    # re-provision is idempotent: same record returned
    r2 = client.post("/tenants/onboarding/telephony/provision", json={"mode": "provisioned"})
    assert r2.json()["idempotent_replay"] is True
    assert r2.json()["subaccount_sid"] == body["subaccount_sid"]
    assert len(fake_db.telephony.docs) == 1


def test_telephony_port_in_mode(client):
    as_user("t2")
    r = client.post(
        "/tenants/onboarding/telephony/provision",
        json={"mode": "ported", "port_numbers": ["+15551234567"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "ported"
    assert body["numbers"] == [{"number": "+15551234567", "type": "ported", "status": "port_requested"}]


def test_telephony_both_mode(client):
    as_user("t3")
    r = client.post(
        "/tenants/onboarding/telephony/provision",
        json={"mode": "both", "port_numbers": ["+15559876543"], "count": 2},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "both"
    types = [n["type"] for n in body["numbers"]]
    assert "provisioned" in types and "ported" in types
    # same tenant always maps to same SID, even across instances
    assert body["subaccount_sid"] == _deterministic_sid("t3")


def test_telephony_mock_provider_direct():
    import asyncio

    p = MockTelephonyProvider()

    async def run():
        a = await p.provision(tenant_id="x1", tenant_name="X", mode="both", port_numbers=["+1"])
        b = await p.provision(tenant_id="x1", tenant_name="X", mode="both", port_numbers=["+1"])
        return a, b

    a, b = asyncio.run(run())
    assert a == b  # fully deterministic


# ---------- authz ----------

def test_non_owner_forbidden_everywhere(client):
    as_user("t1", role="member")
    assert upload(client, INVOICE_CSV).status_code == 403
    assert _import(client, "nope", batch_id="b").status_code == 403
    assert client.post("/tenants/onboarding/telephony/provision", json={"mode": "provisioned"}).status_code == 403
    assert client.get("/tenants/onboarding/telephony").status_code == 403


def test_import_unknown_draft_404(client):
    as_user("t1")
    assert _import(client, "missing-upload", batch_id="b1").status_code == 404


def test_get_telephony_404_before_provision(client):
    as_user("t9")
    assert client.get("/tenants/onboarding/telephony").status_code == 404
