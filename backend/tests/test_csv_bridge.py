"""Tests for the CSV bridge (routers/csv_bridge.py): QBO-shaped exports of
customers / invoices / payments and the deduped customers import with
dry-run planning.

No database or network: a small FastAPI app with only the csv-bridge
router, a fake in-memory Mongo, and overridden auth dependencies — the
same harness shape as test_bookkeeping_link.py.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("JWT_SECRET", "csv-bridge-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "csv_bridge_test")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.csv_bridge as csv_mod  # noqa: E402
from security import require_tenant_user, require_tenant_owner_or_admin  # noqa: E402


# ---------- fake mongo (same shape as test_bookkeeping_link) ----------

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
        return type("R", (), {"matched_count": matched, "modified_count": matched})()


class FakeDB:
    def __init__(self):
        self.customers = FakeCollection()
        self.invoices = FakeCollection()
        self.invoice_payments = FakeCollection()


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(csv_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(csv_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user

    def fake_owner():
        if current_user.get("role") not in {"owner", "admin"}:
            raise HTTPException(403, "Owner/admin role required")
        return current_user

    app.dependency_overrides[require_tenant_owner_or_admin] = fake_owner
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def seed_customer(fake_db, tenant_id="t1", **over):
    doc = {
        "id": over.pop("id", f"c-{len(fake_db.customers.docs) + 1}-{tenant_id}"),
        "tenant_id": tenant_id, "name": "Jane Doe",
        "email": "jane@example.com", "phone": "555-0100", "notes": "",
        "tags": [], "created_at": "2026-01-15T10:00:00+00:00",
    }
    doc.update(over)
    fake_db.customers.docs.append(doc)
    return doc


def upload(client, text, tenant="t1", role="owner", dry_run=True, name="customers.csv"):
    as_user(tenant, role=role)
    return client.post(
        "/tenants/csv-bridge/import/customers",
        files={"file": (name, text, "text/csv")},
        params={"dry_run": str(dry_run).lower()},
    )


# ---------- exports: customers ----------

def test_export_customers_shape_and_isolation(client, fake_db):
    seed_customer(fake_db, "t1", name="Jane Doe", email="jane@example.com",
                  company="Doe Plumbing LLC",
                  service_address={"line1": "12 Main St", "line2": "Ste 4",
                                   "city": "Austin", "state": "TX",
                                   "postal_code": "78701", "country": "US"})
    seed_customer(fake_db, "t1", name="Bob Ray", email="bob@example.com",
                  created_at="2026-02-01T09:00:00+00:00")
    seed_customer(fake_db, "t2", name="Other Tenant Person", email="spy@example.com")
    as_user("t1", role="staff")  # exports are reads: any tenant user
    r = client.get("/tenants/csv-bridge/export/customers")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].split(",") == csv_mod.CUSTOMER_COLUMNS
    body = "\n".join(lines[1:])
    assert "Jane Doe" in body and "Doe Plumbing LLC" in body
    assert "12 Main St Ste 4" in body and "Austin" in body and "78701" in body
    assert "Bob Ray" in body
    assert "spy@example.com" not in body  # never another tenant's rows


def test_export_customers_date_filter(client, fake_db):
    seed_customer(fake_db, "t1", name="Early", created_at="2026-01-15T10:00:00+00:00")
    seed_customer(fake_db, "t1", name="Late", email="late@example.com",
                  created_at="2026-03-01T10:00:00+00:00")
    as_user("t1")
    r = client.get("/tenants/csv-bridge/export/customers",
                   params={"start": "2026-02-01", "end": "2026-02-28"})
    assert "Late" not in r.text and "Early" not in r.text  # neither in Feb…
    r = client.get("/tenants/csv-bridge/export/customers",
                   params={"start": "2026-02-01"})
    assert "Late" in r.text and "Early" not in r.text
    r = client.get("/tenants/csv-bridge/export/customers",
                   params={"start": "not-a-date"})
    assert r.status_code == 400


# ---------- exports: invoices ----------

def test_export_invoices_both_shapes_and_isolation(client, fake_db):
    # Mini-CRM shape: dollar `total`, EstimateLine rows.
    fake_db.invoices.docs.append({
        "id": "inv-crm", "tenant_id": "t1", "customer_name": "Jane Doe",
        "title": "Water heater", "status": "paid",
        "total": 250.0, "created_at": "2026-01-20T10:00:00+00:00",
        "due_at": "2026-02-03T00:00:00+00:00", "paid_at": "2026-01-25T10:00:00+00:00",
        "lines": [{"description": "Heater unit", "quantity": 1, "unit_price": 200.0},
                  {"description": "Labor", "quantity": 2, "unit_price": 25.0}],
    })
    # Call-to-payment shape: total_cents, partially paid.
    fake_db.invoices.docs.append({
        "id": "inv-c2p", "tenant_id": "t1", "customer_name": "Bob Ray",
        "title": "AC repair", "status": "sent",
        "total_cents": 12000, "amount_paid_cents": 2000,
        "created_at": "2026-01-21T10:00:00+00:00",
        "due_at": "2026-02-04T00:00:00+00:00",
        "lines": [{"description": "AC repair labor", "quantity": 1, "unit_price": 120.0}],
    })
    # No lines at all -> one fallback row carrying the total.
    fake_db.invoices.docs.append({
        "id": "inv-bare", "tenant_id": "t1", "customer_name": "No Lines",
        "title": "Call-out fee", "status": "sent", "total_cents": 4900,
        "amount_paid_cents": 0, "created_at": "2026-01-22T10:00:00+00:00",
        "lines": [],
    })
    fake_db.invoices.docs.append({
        "id": "inv-t2", "tenant_id": "t2", "customer_name": "Spy",
        "title": "x", "status": "sent", "total_cents": 100,
        "created_at": "2026-01-20T10:00:00+00:00", "lines": [],
    })
    as_user("t1")
    r = client.get("/tenants/csv-bridge/export/invoices")
    assert r.status_code == 200
    lines = [ln for ln in r.text.strip().splitlines()]
    assert lines[0].split(",") == csv_mod.INVOICE_COLUMNS
    body = "\n".join(lines[1:])
    assert body.count("inv-crm") == 2  # one row per line item
    assert "Heater unit" in body and "200.00" in body
    assert "inv-c2p" in body and "20.00" in body and "100.00" in body  # paid/balance
    assert "paid" in body and "sent" in body  # status incl. paid state
    assert body.count("inv-bare") == 1 and "49.00" in body
    assert "inv-t2" not in body and "Spy" not in body


# ---------- exports: payments ----------

def test_export_payments_stripe_manual_and_isolation(client, fake_db):
    fake_db.invoices.docs.append({
        "id": "inv-paid-stripe", "tenant_id": "t1", "customer_name": "Jane Doe",
        "title": "AC repair", "status": "paid", "total_cents": 12000,
        "amount_paid_cents": 12000, "created_at": "2026-01-20T10:00:00+00:00",
        "paid_at": "2026-01-26T10:00:00+00:00", "lines": [],
    })
    fake_db.invoice_payments.docs.append({
        "id": "pay-1", "tenant_id": "t1", "invoice_id": "inv-paid-stripe",
        "stripe_payment_intent_id": "pi_123", "amount_cents": 12000,
        "amount_received_cents": 12000, "status": "succeeded",
        "created_at": "2026-01-26T09:59:00+00:00",
        "succeeded_at": "2026-01-26T10:00:00+00:00",
    })
    # Failed payments are not received money: excluded.
    fake_db.invoice_payments.docs.append({
        "id": "pay-2", "tenant_id": "t1", "invoice_id": "inv-paid-stripe",
        "stripe_payment_intent_id": "pi_failed", "amount_cents": 12000,
        "status": "failed", "created_at": "2026-01-25T10:00:00+00:00",
    })
    # Paid by hand (mark-paid): no electronic payment -> one manual row.
    fake_db.invoices.docs.append({
        "id": "inv-paid-manual", "tenant_id": "t1", "customer_name": "Cash Carl",
        "title": "Drain clean", "status": "paid", "total": 75.0,
        "created_at": "2026-01-18T10:00:00+00:00",
        "paid_at": "2026-01-19T12:00:00+00:00", "lines": [],
    })
    fake_db.invoice_payments.docs.append({
        "id": "pay-t2", "tenant_id": "t2", "invoice_id": "inv-x",
        "stripe_payment_intent_id": "pi_spy", "amount_cents": 100,
        "amount_received_cents": 100, "status": "succeeded",
        "created_at": "2026-01-26T10:00:00+00:00",
    })
    as_user("t1")
    r = client.get("/tenants/csv-bridge/export/payments")
    assert r.status_code == 200
    lines = r.text.strip().splitlines()
    assert lines[0].split(",") == csv_mod.PAYMENT_COLUMNS
    body = "\n".join(lines[1:])
    assert "pi_123" in body and "stripe" in body and "120.00" in body
    assert "pi_failed" not in body
    assert "inv-paid-manual" in body and "manual" in body and "75.00" in body
    assert body.count("inv-paid-stripe") == 1  # no manual duplicate
    assert "pi_spy" not in body


# ---------- import: guards + header validation ----------

def test_import_staff_forbidden(client, fake_db):
    r = upload(client, "Customer,Email\nJane,j@example.com\n", role="staff")
    assert r.status_code == 403


def test_import_missing_name_header_is_400(client, fake_db):
    r = upload(client, "Email,Phone\nj@example.com,555\n")
    assert r.status_code == 400
    assert "customer-name column" in r.json()["detail"]


def test_import_empty_file_is_400(client, fake_db):
    assert upload(client, "").status_code == 400
    assert upload(client, "   \n").status_code == 400


def test_import_first_last_name_columns(client, fake_db):
    text = "First Name,Last Name,Email\nAda,Lovelace,ada@example.com\n"
    r = upload(client, text, dry_run=False)
    assert r.status_code == 200
    assert r.json()["summary"]["create"] == 1
    assert fake_db.customers.docs[0]["name"] == "Ada Lovelace"


# ---------- import: dry-run plan ----------

PLAN_CSV = (
    "Customer,Email,Phone,Notes\n"
    "Jane Doe,jane@example.com,555-9999,\n"          # email match -> update phone
    "Bob Ray,bob@example.com,555-2222,\n"            # identical -> skip
    "New Person,new@example.com,555-3333,VIP\n"      # create
    "Bad Email,not-an-email,555-0000,\n"             # error: bad email
    ",ghost@example.com,555-0000,\n"                 # error: no name
)


def _seed_plan_tenant(fake_db):
    seed_customer(fake_db, "t1", id="c-jane", name="Jane Doe",
                  email="jane@example.com", phone="555-0100")
    seed_customer(fake_db, "t1", id="c-bob", name="Bob Ray",
                  email="bob@example.com", phone="555-2222")


def test_import_dry_run_plan_and_no_writes(client, fake_db):
    _seed_plan_tenant(fake_db)
    r = upload(client, PLAN_CSV, dry_run=True)
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert body["summary"] == {"total": 5, "create": 1, "update": 1,
                               "skip": 1, "error": 2}
    by_name = {row["name"]: row for row in body["rows"]}
    assert by_name["Jane Doe"]["action"] == "update"
    assert "phone" in by_name["Jane Doe"]["detail"]
    assert by_name["Bob Ray"]["action"] == "skip"
    assert by_name["New Person"]["action"] == "create"
    errors = [row for row in body["rows"] if row["action"] == "error"]
    assert any("invalid email" in e["detail"] for e in errors)
    assert any("missing customer name" in e["detail"] for e in errors)
    # Dry-run wrote NOTHING.
    assert len(fake_db.customers.docs) == 2
    jane = next(d for d in fake_db.customers.docs if d["id"] == "c-jane")
    assert jane["phone"] == "555-0100"


def test_import_commit_then_reimport_is_all_skips(client, fake_db):
    _seed_plan_tenant(fake_db)
    r = upload(client, PLAN_CSV, dry_run=False)
    assert r.status_code == 200
    assert r.json()["summary"]["create"] == 1
    assert len(fake_db.customers.docs) == 3
    jane = next(d for d in fake_db.customers.docs if d["id"] == "c-jane")
    assert jane["phone"] == "555-9999"
    new = next(d for d in fake_db.customers.docs if d["name"] == "New Person")
    assert new["tenant_id"] == "t1" and new["notes"] == "VIP"
    # Second commit of the same file: no duplicates, valid rows all skip.
    r2 = upload(client, PLAN_CSV, dry_run=False)
    s2 = r2.json()["summary"]
    assert s2["create"] == 0 and s2["update"] == 0 and s2["skip"] == 3
    assert s2["error"] == 2
    assert len(fake_db.customers.docs) == 3


def test_import_dedupe_by_name_never_overwrites_email(client, fake_db):
    seed_customer(fake_db, "t1", id="c-acme", name="Acme Plumbing",
                  email="office@acme.example", phone="")
    text = "Customer,Email,Phone\nAcme Plumbing,different@acme.example,555-7777\n"
    r = upload(client, text, dry_run=False)
    assert r.json()["summary"] == {"total": 1, "create": 0, "update": 1,
                                   "skip": 0, "error": 0}
    doc = fake_db.customers.docs[0]
    assert doc["email"] == "office@acme.example"  # matched by name; email kept
    assert doc["phone"] == "555-7777"             # phone filled from the file


def test_import_within_file_duplicates_resolve(client, fake_db):
    text = ("Customer,Email,Phone\n"
            "Twin One,twin@example.com,555-1\n"
            "Twin One,twin@example.com,555-1\n")
    r = upload(client, text, dry_run=False)
    s = r.json()["summary"]
    assert s["create"] == 1 and s["skip"] == 1
    assert len(fake_db.customers.docs) == 1


def test_import_is_tenant_scoped(client, fake_db):
    seed_customer(fake_db, "t2", id="c-t2", name="Jane Doe",
                  email="jane@example.com", phone="555-0100")
    text = "Customer,Email,Phone\nJane Doe,jane@example.com,555-4242\n"
    r = upload(client, text, tenant="t1", dry_run=False)
    # t2's Jane is invisible to t1: this is a create, not an update.
    assert r.json()["summary"]["create"] == 1
    t2_doc = next(d for d in fake_db.customers.docs if d["id"] == "c-t2")
    assert t2_doc["phone"] == "555-0100"


def test_import_address_columns_round_trip(client, fake_db):
    text = ("Customer,Email,Street,City,State,ZIP Code,Country\n"
            "Jane Doe,jane@example.com,9 Oak Ave,Dallas,TX,75001,US\n")
    r = upload(client, text, dry_run=False)
    assert r.json()["summary"]["create"] == 1
    addr = fake_db.customers.docs[0]["service_address"]
    assert addr["line1"] == "9 Oak Ave" and addr["city"] == "Dallas"
    assert addr["postal_code"] == "75001"
    # …and the export flattens it back into the QBO-shaped columns.
    as_user("t1")
    out = client.get("/tenants/csv-bridge/export/customers").text
    assert "9 Oak Ave" in out and "75001" in out


def test_export_import_round_trip_into_fresh_tenant(client, fake_db):
    seed_customer(fake_db, "t1", name="Jane Doe", email="jane@example.com")
    seed_customer(fake_db, "t1", name="Bob Ray", email="bob@example.com",
                  phone="555-2222")
    as_user("t1")
    exported = client.get("/tenants/csv-bridge/export/customers").text
    r = upload(client, exported, tenant="t3", dry_run=False)
    assert r.json()["summary"]["create"] == 2
    r2 = upload(client, exported, tenant="t3", dry_run=False)
    assert r2.json()["summary"]["skip"] == 2
