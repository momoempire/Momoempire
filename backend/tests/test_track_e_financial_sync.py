"""Track E (audit 2026-10-17) — financial-sync + E2E verification harness.

Runs the REAL Momo bookkeeping-link code (services/accounting.py +
routers/bookkeeping_link.py on the Track B branch, plus two Track E
client fixes) against the REAL books app (Track A branch) served over
HTTP on 127.0.0.1:8791 with an in-memory fake Mongo (Track A's FakeDB).
No production data, no real charges: every credential, tenant and
payment here is synthetic.

Requires the books harness server (state file at /tmp/track-e/state.json,
snapshots at /tmp/track-e/books_state.json). Without it the whole module
SKIPS, so the regular suite is unaffected.

Legs that cannot run in this sandbox (live Twilio voice receptionist,
live Stripe) are NOT exercised here; E2E_VERIFICATION.md states plainly
which legs ran live, at handler level, or not at all.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid

# The sandbox's proxy env breaks httpx URL parsing for localhost
# (bracketed IPv6 entries in NO_PROXY). Scrub before any httpx use.
for _v in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "ALL_PROXY", "all_proxy", "no_proxy", "NO_PROXY"):
    os.environ.pop(_v, None)

os.environ.setdefault("JWT_SECRET", "track-e-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "track_e_momo")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("BOOKKEEPING_LINK_ENCRYPTION_KEY",
                      Fernet.generate_key().decode())

import types  # noqa: E402

_stripe_stub = types.ModuleType("stripe")
_stripe_stub.api_key = None


class _StripeError(Exception):
    pass


_stripe_stub.error = types.SimpleNamespace(
    StripeError=_StripeError, InvalidRequestError=_StripeError)
_stripe_stub.Account = types.SimpleNamespace(create=None, retrieve=None)
_stripe_stub.PaymentIntent = types.SimpleNamespace(create=None)
_stripe_stub.AccountLink = types.SimpleNamespace(create=None)
sys.modules.setdefault("stripe", _stripe_stub)

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

STATE_FILE = "/tmp/track-e/state.json"
SNAP_FILE = "/tmp/track-e/books_state.json"

pytestmark = pytest.mark.skipif(
    not os.path.exists(STATE_FILE),
    reason="Track E books harness server not running (no state file)")

STATE = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}
BASE = STATE.get("base_api", "http://127.0.0.1:8791/api")
TEN = STATE.get("tenants", {})
CRASH = STATE.get("crash", {})

import routers.bookkeeping_link as bk_mod  # noqa: E402
import routers.call_to_payment as c2p_mod  # noqa: E402
import routers.crm as crm_mod  # noqa: E402
import routers.dispatch as dispatch_mod  # noqa: E402
import routers.tenants as tenants_mod  # noqa: E402
import routers.post_job as post_job_mod  # noqa: E402
import routers.csv_bridge as csv_mod  # noqa: E402
import routers.phase9 as phase9_mod  # noqa: E402
from models import (AppointmentIn, EstimateIn, InvoiceIn, LeadIn)  # noqa: E402
from services import token_vault  # noqa: E402
from services.accounting import (  # noqa: E402
    AccountingError, MindAtEaseProvider, SyncPayment, idempotency_key)

MT_HVAC = "mt-hvac"
MT_PLUMB = "mt-plumb"
BT = {MT_HVAC: "bt-hvac", MT_PLUMB: "bt-plumb"}


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------- Momo-side fake mongo (dotted keys, like Track B) ----------

def _resolve(doc, key):
    cur = doc
    for part in key.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _match(doc, query):
    for k, cond in (query or {}).items():
        val = _resolve(doc, k)
        if isinstance(cond, dict):
            for op, opval in cond.items():
                if op == "$in" and val not in opval:
                    return False
                if op == "$ne" and val == opval:
                    return False
                if op == "$exists" and (val is not None) != bool(opval):
                    return False
                if op not in ("$in", "$ne", "$exists"):
                    return False
        elif val != cond:
            return False
    return True


def _set_dotted(doc, key, value):
    parts = key.split(".")
    cur = doc
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        self.docs = sorted(self.docs, key=lambda d: str(_resolve(d, key) or ""),
                           reverse=(direction < 0))
        return self

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    async def to_list(self, n=None):
        docs = self.docs if n is None else self.docs[:n]
        return [dict(d) for d in docs]


class FakeCollection:
    def __init__(self):
        self.docs = []

    def find(self, query=None, proj=None):
        return FakeCursor([d for d in self.docs if _match(d, query or {})])

    async def find_one(self, query=None, proj=None):
        for d in self.docs:
            if _match(d, query or {}):
                out = dict(d)
                if proj and proj.get("_id") == 0:
                    out.pop("_id", None)
                return out
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("id")})()

    async def update_one(self, query, update, upsert=False):
        target = None
        for d in self.docs:
            if _match(d, query):
                target = d
                break
        if target is None and upsert:
            target = {}
            for k, v in (query or {}).items():
                if not isinstance(v, dict):
                    _set_dotted(target, k, v)
            self.docs.append(target)
            for k, v in update.get("$setOnInsert", {}).items():
                _set_dotted(target, k, v)
        if target is None:
            return type("R", (), {"matched_count": 0, "modified_count": 0})()
        for k, v in update.get("$set", {}).items():
            _set_dotted(target, k, v)
        for k, v in update.get("$inc", {}).items():
            _set_dotted(target, k, int(_resolve(target, k) or 0) + v)
        for k, v in update.get("$push", {}).items():
            cur = _resolve(target, k)
            if not isinstance(cur, list):
                cur = []
                _set_dotted(target, k, cur)
            cur.append(v)
        for k in update.get("$unset", {}):
            parts = k.split(".")
            cur = target
            for p in parts[:-1]:
                cur = cur.get(p, {}) if isinstance(cur, dict) else {}
            if isinstance(cur, dict):
                cur.pop(parts[-1], None)
        return type("R", (), {"matched_count": 1, "modified_count": 1})()

    async def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if _match(d, query):
                self.docs.pop(i)
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()

    async def count_documents(self, query=None):
        return sum(1 for d in self.docs if _match(d, query or {}))


class FakeDB:
    def __init__(self):
        self._colls = {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._colls.setdefault(name, FakeCollection())


# ---------------- helpers ----------------

def books():
    """Latest books-server snapshot (waits briefly for a fresh dump)."""
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            snap = json.load(open(SNAP_FILE))
            if time.time() - snap["ts"] < 3:
                return snap
        except Exception:
            pass
        time.sleep(0.3)
    raise AssertionError("books snapshot stale/unavailable")


def bt_snap(momo_tenant):
    return books()["tenants"][BT[momo_tenant]]


def wait_snap(pred, timeout=15):
    """Poll the books snapshot until pred(snap) holds (the server dumps
    state ~1x/sec; assertions must wait for the dump, not race it)."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = json.load(open(SNAP_FILE))
        except Exception:
            last = None
        if last and pred(last):
            return last
        time.sleep(0.25)
    raise AssertionError("books snapshot condition not met in time")


def wait_bt(momo_tenant, pred, timeout=15):
    snap = wait_snap(lambda s: pred(s["tenants"][BT[momo_tenant]]),
                     timeout)
    return snap["tenants"][BT[momo_tenant]]


def fresh_snap(after_ts):
    """A snapshot dumped at least one full cycle after `after_ts` —
    for 'nothing changed' assertions."""
    return wait_snap(lambda s: s["ts"] > after_ts + 1.05)


def settle_bt(momo_tenant, timeout=20):
    """A trustworthy baseline/after-state: two consecutive dumps whose
    counters agree (the server is quiescent between tests, so this
    converges in ~2 dump cycles)."""
    bt = BT[momo_tenant]
    deadline = time.time() + timeout
    prev, prev_ts = None, 0.0
    while time.time() < deadline:
        try:
            snap = json.load(open(SNAP_FILE))
        except Exception:
            snap = None
        if snap and snap["ts"] > prev_ts:
            cur = snap["tenants"][bt]
            sig = (cur["journal_entries"], len(cur["invoices"]),
                   len(cur["customers"]), len(cur["credits"]))
            if prev is not None and prev[0] == sig:
                return cur
            prev, prev_ts = (sig, cur), snap["ts"]
        time.sleep(0.3)
    raise AssertionError("books state never settled")


def cred(momo_tenant):
    return TEN[momo_tenant]["credential"]


def owner_jwt(momo_tenant):
    return TEN[momo_tenant]["owner_jwt"]


def raw(method, path, token=None, key=None, body=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if key:
        headers["Idempotency-Key"] = key
    with httpx.Client(trust_env=False, timeout=15) as c:
        return c.request(method, f"{BASE}{path}", headers=headers, json=body)


def provider(momo_tenant, token=None):
    return MindAtEaseProvider(api_base_url=BASE,
                              api_token=token or cred(momo_tenant))


def uniq(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def seed_link(fake, momo_tenant, token=None, name="Test Shop"):
    s = dict(bk_mod.DEFAULT_SETTINGS)
    s["health"] = bk_mod._default_health()
    s["sync_failures"] = []
    s.update({"enabled": True, "auto_sync_on_paid": True, "api_base_url": BASE})
    bk_mod._apply_credential(s, token or cred(momo_tenant))
    doc = {"id": momo_tenant, "name": name, "bookkeeping_link_settings": s}
    fake.tenants.docs.append(doc)
    return doc


def paid_invoice(inv_id, total_cents, name, email, phone="555-0100",
                 tenant=MT_HVAC, lines=None):
    return {
        "id": inv_id, "tenant_id": tenant,
        "customer_name": name, "customer_email": email,
        "customer_phone": phone, "title": "Job",
        "lines": lines or [{"description": "Labor", "quantity": 1,
                            "unit_price": total_cents / 100.0}],
        "total_cents": total_cents, "amount_paid_cents": total_cents,
        "status": "paid", "due_at": "2026-11-01T00:00:00+00:00",
    }


def bind_db(monkeypatch, fake, *mods):
    for m in mods:
        monkeypatch.setattr(m, "get_db", lambda: fake)


USER_HVAC = {"id": "u-hvac", "tenant_id": MT_HVAC, "role": "owner"}
USER_PLUMB = {"id": "u-plumb", "tenant_id": MT_PLUMB, "role": "owner"}


# ================= Task 1 — contract reconciliation, runtime proofs =========

def test_classify_mae1_credential_as_integration(monkeypatch):
    """A real mae1.<client_id>.<secret> token has THREE dot segments.
    Track B's shape heuristic alone would call it a legacy JWT; the
    classified kind must be integration and the migration flag clear."""
    fake = FakeDB()
    bind_db(monkeypatch, fake, bk_mod)
    fake.tenants.docs.append({"id": MT_HVAC, "name": "HVAC"})
    body = bk_mod.BookkeepingSettingsIn(api_token=cred(MT_HVAC))
    out = run(bk_mod.put_settings(body, user=USER_HVAC))
    assert out["credential_kind"] == "integration", out["credential_kind"]
    assert out["migration"]["required"] is False
    # A real JWT still classifies as legacy.
    body = bk_mod.BookkeepingSettingsIn(api_token=owner_jwt(MT_HVAC))
    out = run(bk_mod.put_settings(body, user=USER_HVAC))
    assert out["credential_kind"] == "legacy_jwt"
    assert out["migration"]["required"] is True


def test_connection_mae1_uses_integrations_me(monkeypatch):
    """Post-fix: test-connection with an integration credential succeeds
    via GET /integrations/me. (Pre-fix client called /auth/me, which
    books rejects for mae1 tokens with 401 — see raw probe in
    E2E_VERIFICATION.md.)"""
    fake = FakeDB()
    bind_db(monkeypatch, fake, bk_mod)
    seed_link(fake, MT_HVAC, name="HVAC Books Tenant")
    out = run(bk_mod.test_connection(user=USER_HVAC))
    assert out["ok"] is True, out
    assert "HVAC Books Tenant" in out["detail"]


def test_connection_legacy_jwt_still_works(monkeypatch):
    fake = FakeDB()
    bind_db(monkeypatch, fake, bk_mod)
    seed_link(fake, MT_HVAC, token=owner_jwt(MT_HVAC), name="HVAC Books Tenant")
    out = run(bk_mod.test_connection(user=USER_HVAC))
    assert out["ok"] is True, out


def test_rotate_then_revoke_flow(monkeypatch):
    """Issue a second credential in books, rotate to it from Momo
    (validate-then-swap), then revoke it in books: the next health
    check must classify the failure as revoked and pause sync."""
    fake = FakeDB()
    bind_db(monkeypatch, fake, bk_mod)
    tenant_doc = seed_link(fake, MT_HVAC)
    # Books-side: issue credential #2 (owner JWT), per contract §1.2.
    r = raw("POST", "/integrations/credentials", token=owner_jwt(MT_HVAC),
            body={"label": "Momo sync rotation"})
    assert r.status_code == 200, r.text
    cred2, cred2_id = r.json()["credential"], r.json()["id"]
    # Momo-side rotation validates the candidate then swaps.
    out = run(bk_mod.rotate_credential(
        bk_mod.RotateCredentialIn(api_token=cred2), user=USER_HVAC))
    assert out["credential_fingerprint"] == bk_mod.credential_fingerprint(cred2)
    assert out["credential_kind"] == "integration"
    # Books-side: revoke credential #2.
    r = raw("POST", f"/integrations/credentials/{cred2_id}/revoke",
            token=owner_jwt(MT_HVAC))
    assert r.status_code == 200, r.text
    # Health check now reports revoked (books detail: "Integration
    # credential revoked" -> client kind=revoked), and sync pauses.
    res = run(bk_mod.check_connection_health(fake, MT_HVAC))
    assert res["ok"] is False and res["health"]["status"] == "revoked", res
    inv = paid_invoice(uniq("inv-revoked"), 5000, "Revoked Case",
                       "revoked@example.com")
    fake.invoices.docs.append(inv)
    out = run(bk_mod.sync_invoice_paid(fake, MT_HVAC, inv))
    assert out == {"synced": False, "reason": "credential_revoked"}, out
    # The raw provider classifies the revocation too.
    with pytest.raises(AccountingError) as ei:
        run(provider(MT_HVAC, token=cred2).test_connection())
    assert ei.value.kind == "revoked"


def test_bad_secret_is_generic_auth_error():
    bad = cred(MT_HVAC)[:-2] + "xx"
    with pytest.raises(AccountingError) as ei:
        run(provider(MT_HVAC, token=bad).test_connection())
    assert ei.value.kind == "auth"
    # Books must not reveal which part failed.
    r = raw("GET", "/integrations/me", token=bad)
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid integration credential"


def test_credential_cannot_touch_other_tenant():
    """Tenant A's credential paying tenant B's books invoice -> 404:
    the tenant comes from the credential record, never the request."""
    # Create a plumbing books invoice with the plumbing credential.
    r = raw("POST", "/invoices", token=cred(MT_PLUMB), key=uniq("k"),
            body={"client_name": "Plumb Only", "lines": [
                {"name": "Pipe", "amount": 40.0, "revenue_code": "4020"}],
                "tax": 0, "terms": "Net 30"})
    assert r.status_code == 200, r.text
    plumb_inv = r.json()["id"]
    r = raw("POST", f"/invoices/{plumb_inv}/pay", token=cred(MT_HVAC),
            key=uniq("k"), body={"payment_method": "card", "amount": 40.0})
    assert r.status_code == 404, r.text


# ================= Task 2 — financial sync matrix =================

def test_successful_payment_syncs_and_reconciles(monkeypatch):
    fake = FakeDB()
    bind_db(monkeypatch, fake, bk_mod)
    seed_link(fake, MT_HVAC)
    before = settle_bt(MT_HVAC)
    name = uniq("Cust")
    inv = paid_invoice(uniq("inv-ok"), 25000, name, "ok@example.com")
    fake.invoices.docs.append(inv)
    out = run(bk_mod.sync_invoice_paid(fake, MT_HVAC, inv))
    assert out["synced"] is True and out["steps"] == [
        "customer", "invoice", "payment"], out
    after = wait_bt(MT_HVAC, lambda d: len(d["invoices"]) ==
                    len(before["invoices"]) + 1)
    assert len(after["customers"]) == len(before["customers"]) + 1
    assert len(after["invoices"]) == len(before["invoices"]) + 1
    binv = after["invoices"][-1]
    assert binv["total_cents"] == 25000 and binv["paid_cents"] == 25000
    assert binv["status"] == "paid" and binv["currency"] == "USD"
    assert len(binv["payments"]) == 1
    # Exactly two new journal entries (issue + payment), books balance.
    assert after["journal_entries"] == before["journal_entries"] + 2
    delta = {c: after["balances"].get(c, 0) - before["balances"].get(c, 0)
             for c in set(after["balances"]) | set(before["balances"])}
    assert sum(delta.values()) == 0, delta          # debits == credits
    assert delta.get("1200", 0) == 0, delta          # AR in and out
    assert delta.get("4020", 0) == -25000, delta      # revenue credited
    assert max(delta.values()) == 25000, delta        # one cash account +250


def test_duplicate_delivery_posts_exactly_once(monkeypatch):
    """Same Idempotency-Key delivered twice (the lost-response retry):
    books replays the recorded response; nothing posts twice. The
    replay is proven at the raw HTTP layer via the
    Idempotency-Replayed header, which the provider discards."""
    fake = FakeDB()
    bind_db(monkeypatch, fake, bk_mod)
    seed_link(fake, MT_HVAC)
    inv = paid_invoice(uniq("inv-dupe"), 12000, uniq("Dupe"),
                       "dupe@example.com")
    fake.invoices.docs.append(inv)
    out = run(bk_mod.sync_invoice_paid(fake, MT_HVAC, inv))
    assert out["synced"] is True
    bk_inv_id = out["invoice_provider_id"]
    pay_key = idempotency_key(MT_HVAC, inv["id"], "payment")
    before = settle_bt(MT_HVAC)
    # Provider-level redelivery of the payment step, same key.
    entry = run(provider(MT_HVAC).push_payment(
        SyncPayment(invoice_provider_id=bk_inv_id, amount_cents=12000),
        idempotency_key=pay_key))
    assert entry == out["payment_provider_id"]
    # Raw redelivery: replay header + verbatim body.
    r = raw("POST", f"/invoices/{bk_inv_id}/pay", token=cred(MT_HVAC),
            key=pay_key, body={"payment_method": "card", "amount": 120.0})
    assert r.status_code == 200
    assert r.headers.get("Idempotency-Replayed") == "true", dict(r.headers)
    assert str(r.json()["entry_number"]) == out["payment_provider_id"]
    after = settle_bt(MT_HVAC)
    assert after["journal_entries"] == before["journal_entries"]
    binv = [i for i in after["invoices"] if i["id"] == bk_inv_id][0]
    assert len(binv["payments"]) == 1 and binv["paid_cents"] == 12000


def test_same_key_different_payload_is_409():
    key = uniq("conflict")
    co = uniq("Conflict Co")
    email = f"{uniq('c')}@example.com"
    body = {"name": co, "email": email, "phone": "555-0100"}
    r = raw("POST", "/customers", token=cred(MT_HVAC), key=key, body=body)
    assert r.status_code == 200, r.text
    changed = dict(body, name=f"{co} Renamed")
    r = raw("POST", "/customers", token=cred(MT_HVAC), key=key, body=changed)
    assert r.status_code == 409, r.text
    assert "different request payload" in r.json()["detail"]
    # The provider surfaces it as a hard (http) error, not a retryable one.
    from services.accounting import SyncCustomer
    with pytest.raises(AccountingError) as ei:
        run(provider(MT_HVAC).push_customer(
            SyncCustomer(name=f"{co} Renamed", email=email,
                         phone="555-0100"),
            idempotency_key=key))
    assert ei.value.kind == "http" and "409" in str(ei.value)


def test_repeat_customer_second_invoice_blocked(monkeypatch):
    """KNOWN GAP, asserted as-is: Momo pushes the customer once per
    invoice (key is per-invoice), and books' natural dedupe (same name
    + email/phone) answers the second push with 409 'Customer already
    exists'. The client does not adopt the returned existing_id, so a
    repeat customer's second paid invoice does not sync."""
    fake = FakeDB()
    bind_db(monkeypatch, fake, bk_mod)
    seed_link(fake, MT_HVAC)
    name, email = uniq("Repeat"), "repeat@example.com"
    inv1 = paid_invoice(uniq("inv-r1"), 10000, name, email)
    fake.invoices.docs.append(inv1)
    out1 = run(bk_mod.sync_invoice_paid(fake, MT_HVAC, inv1))
    assert out1["synced"] is True, out1
    inv2 = paid_invoice(uniq("inv-r2"), 8000, name, email)
    fake.invoices.docs.append(inv2)
    out2 = run(bk_mod.sync_invoice_paid(fake, MT_HVAC, inv2))
    assert out2["synced"] is False, out2
    assert "409" in (out2.get("error") or ""), out2


def test_books_partial_payments_and_retry_semantics():
    """Books /pay natively supports partials. A failed (422) keyed
    request records a FAILED idempotency record that a retry may
    re-claim (attempts increments); reusing the key with a FIXED
    payload is a 409 mismatch by design."""
    token = cred(MT_PLUMB)
    r = raw("POST", "/invoices", token=token, key=uniq("k"),
            body={"client_name": "Partial Co", "lines": [
                {"name": "Job", "amount": 300.0, "revenue_code": "4020"}],
                "tax": 0, "terms": "Net 30"})
    assert r.status_code == 200, r.text
    inv_id = r.json()["id"]
    r = raw("POST", f"/invoices/{inv_id}/pay", token=token, key=uniq("k"),
            body={"payment_method": "card", "amount": 100.0})
    assert r.status_code == 200, r.text
    assert r.json()["paid_cents"] == 10000
    assert r.json()["balance_cents"] == 20000
    assert r.json()["status"] != "paid"
    r = raw("POST", f"/invoices/{inv_id}/pay", token=token, key=uniq("k"),
            body={"payment_method": "card", "amount": 200.0})
    assert r.json()["status"] == "paid" and r.json()["balance_cents"] == 0
    # Paying a settled invoice is an error (payment retry after failure
    # at the business layer, not a duplicate posting).
    r = raw("POST", f"/invoices/{inv_id}/pay", token=token, key=uniq("k"),
            body={"payment_method": "card", "amount": 1.0})
    assert r.status_code >= 400
    # Failed-record reclaim: bad currency 422s twice under one key.
    key = uniq("failkey")
    bad = {"client_name": "Bad Currency", "lines": [
        {"name": "X", "amount": 5.0, "revenue_code": "4020"}],
        "tax": 0, "terms": "Net 30", "currency": "XXX"}
    r1 = raw("POST", "/invoices", token=token, key=key, body=bad)
    r2 = raw("POST", "/invoices", token=token, key=key, body=bad)
    assert r1.status_code == 422 and r2.status_code == 422
    snap = wait_bt(MT_PLUMB, lambda d: any(
        x["key"] == key and x["attempts"] >= 2
        for x in d["idempotency_records"]))
    rec = [x for x in snap["idempotency_records"] if x["key"] == key]
    assert rec and rec[0]["status"] == "failed" and rec[0]["attempts"] >= 2
    fixed = dict(bad, currency="USD")
    r3 = raw("POST", "/invoices", token=token, key=key, body=fixed)
    assert r3.status_code == 409  # same key, different payload


def test_overpayment_credit_and_refund_books_side():
    """Overpayment parks in customer credit (2210); refunding the
    credit is an owner-JWT books operation. The integration credential
    must NOT be able to refund (least privilege)."""
    token = cred(MT_PLUMB)
    jwt = owner_jwt(MT_PLUMB)
    before = settle_bt(MT_PLUMB)
    co_name = uniq("Overpay Co")
    r = raw("POST", "/invoices", token=token, key=uniq("k"),
            body={"client_name": co_name, "lines": [
                {"name": "Job", "amount": 100.0, "revenue_code": "4020"}],
                "tax": 0, "terms": "Net 30"})
    inv_id = r.json()["id"]
    r = raw("POST", f"/invoices/{inv_id}/pay", token=token, key=uniq("k"),
            body={"payment_method": "card", "amount": 150.0})
    assert r.status_code == 200, r.text
    assert r.json()["overpayment_cents"] == 5000
    assert r.json()["status"] == "paid"
    snap = wait_bt(MT_PLUMB, lambda d: any(
        c["client_name"] == co_name for c in d["credits"]))
    credits = [c for c in snap["credits"]
               if c["client_name"] == co_name]
    assert credits and credits[0]["remaining_cents"] == 5000, credits
    credit_id = credits[0]["id"]
    # Integration credential cannot refund. (The credits surface lives
    # under the invoices router: /api/invoices/customer-credits/...)
    r = raw("POST", f"/invoices/customer-credits/{credit_id}/refund",
            token=token, body={"payment_method": "bank"})
    assert r.status_code in (401, 403), r.status_code
    # Owner can.
    r = raw("POST", f"/invoices/customer-credits/{credit_id}/refund",
            token=jwt, body={"payment_method": "bank"})
    assert r.status_code == 200, r.text
    assert r.json()["refunded_cents"] == 5000
    after = wait_bt(MT_PLUMB, lambda d: any(
        c["id"] == credit_id and c["remaining_cents"] == 0
        for c in d["credits"]))
    delta = {c: after["balances"].get(c, 0) - before["balances"].get(c, 0)
             for c in set(after["balances"]) | set(before["balances"])}
    assert sum(delta.values()) == 0, delta
    assert delta.get("2210", 0) == 0, delta  # credit in, credit refunded out


def test_unpaid_invoice_never_syncs(monkeypatch):
    fake = FakeDB()
    bind_db(monkeypatch, fake, bk_mod)
    seed_link(fake, MT_HVAC)
    before = settle_bt(MT_HVAC)
    inv = paid_invoice(uniq("inv-open"), 9900, uniq("Open"),
                       "open@example.com")
    inv["status"] = "sent"  # payment failed / not yet paid
    inv["amount_paid_cents"] = 0
    fake.invoices.docs.append(inv)
    out = run(bk_mod.sync_invoice_paid(fake, MT_HVAC, inv))
    assert out == {"synced": False, "reason": "not_paid"}
    after = settle_bt(MT_HVAC)
    assert len(after["invoices"]) == len(before["invoices"])
    assert len(after["customers"]) == len(before["customers"])


def test_crash_window_retry_returns_stamped_invoice(monkeypatch):
    """Seeded remnant: books already holds the invoice (+journal) for
    Momo invoice 'inv-crash', but the idempotency outcome record was
    lost. The retry must adopt the stamped invoice — no duplicate —
    and the payment then posts exactly once."""
    fake = FakeDB()
    bind_db(monkeypatch, fake, bk_mod)
    seed_link(fake, MT_HVAC)
    before = settle_bt(MT_HVAC)
    inv = paid_invoice(CRASH["momo_invoice_id"], CRASH["total_cents"],
                       CRASH["customer_name"], "crash@example.com",
                       phone="555-0199")
    inv["bookkeeping_sync"] = {"customer_provider_id":
                               CRASH["books_customer_id"]}
    fake.invoices.docs.append(inv)
    out = run(bk_mod.sync_invoice_paid(fake, MT_HVAC, inv))
    assert out["synced"] is True, out
    assert out["invoice_provider_id"] == CRASH["books_invoice_id"]
    after = wait_bt(MT_HVAC, lambda d: d["journal_entries"] ==
                    before["journal_entries"] + 1)
    matches = [i for i in after["invoices"]
               if i["client_name"] == CRASH["customer_name"]]
    assert len(matches) == 1, matches
    assert matches[0]["status"] == "paid"
    assert len(matches[0]["payments"]) == 1
    # Only the payment entry is new; the issue entry pre-existed.
    assert after["journal_entries"] == before["journal_entries"] + 1


def test_tenant_separation_identical_keys():
    key = "shared-key-string-for-both-tenants"
    body = {"name": "Same Key Co", "email": "same@example.com",
            "phone": "555-0100"}
    r1 = raw("POST", "/customers", token=cred(MT_HVAC), key=key, body=body)
    r2 = raw("POST", "/customers", token=cred(MT_PLUMB), key=key, body=body)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["id"] != r2.json()["id"]
    for mt in (MT_HVAC, MT_PLUMB):
        snap = wait_bt(mt, lambda d: any(
            x["key"] == key for x in d["idempotency_records"]))
        recs = [x for x in snap["idempotency_records"] if x["key"] == key]
        assert len(recs) == 1 and recs[0]["status"] == "succeeded"


def test_currency_spot_check():
    """Books honors an explicit currency on the sync surface (EUR
    invoice posts, currency recorded). The Momo client never sends a
    currency — Momo shop invoices are USD-denominated, and the synced
    invoice lands as USD (asserted in the reconciliation test)."""
    r = raw("POST", "/invoices", token=cred(MT_PLUMB), key=uniq("k"),
            body={"client_name": "Euro Co", "lines": [
                {"name": "Job", "amount": 100.0, "revenue_code": "4020"}],
                "tax": 0, "terms": "Net 30", "currency": "EUR"})
    assert r.status_code == 200, r.text
    assert r.json()["currency"] == "EUR"


def test_csv_bridge_roundtrip(monkeypatch):
    fake = FakeDB()
    bind_db(monkeypatch, fake, csv_mod)
    fake.tenants.docs.append({"id": MT_HVAC, "name": "CSV Shop"})
    fake.customers.docs.append({
        "id": "cust-1", "tenant_id": MT_HVAC, "name": "Csv Customer",
        "email": "csv@example.com", "phone": "555-0100"})
    fake.invoices.docs.append({
        "id": "inv-csv", "tenant_id": MT_HVAC, "customer_name": "Csv Customer",
        "title": "AC repair", "total_cents": 12000,
        "lines": [{"description": "Labor", "quantity": 1,
                   "unit_price": 120.0}], "status": "paid"})
    out = run(csv_mod.export_customers(start=None, end=None,
                                       user=USER_HVAC))
    text = out.body.decode() if hasattr(out, "body") else str(out)
    assert "Csv Customer" in text and "csv@example.com" in text
    out = run(csv_mod.export_invoices(start=None, end=None,
                                      user=USER_HVAC))
    text = out.body.decode() if hasattr(out, "body") else str(out)
    assert "Csv Customer" in text
    # Import is dry-run by default and dedupes on email/name.
    import io as _io
    from starlette.datastructures import UploadFile as _UF
    csv_text = ("Customer,Email,Phone\n"
                "Csv Customer,csv@example.com,555-0100\n"
                "New Person,new@example.com,555-0111\n")
    plan = run(csv_mod.import_customers(
        file=_UF(filename="in.csv", file=_io.BytesIO(csv_text.encode())),
        dry_run=True, user=USER_HVAC))
    assert plan is not None
    # The dry run must not have created the new customer.
    names = [d.get("name") for d in fake.customers.docs]
    assert "New Person" not in names
    # No QuickBooks API provider exists in the client — by design.
    from services.accounting import get_provider
    with pytest.raises(AccountingError):
        get_provider("quickbooks")


def _synced_invoice(monkeypatch, fake, tenant=MT_HVAC, total=18000):
    seed_link(fake, tenant)
    user = USER_HVAC if tenant == MT_HVAC else USER_PLUMB
    inv = paid_invoice(uniq("inv-gap"), total, uniq("Gap"),
                       "gap@example.com", tenant=tenant)
    fake.invoices.docs.append(inv)
    out = run(bk_mod.sync_invoice_paid(fake, tenant, inv))
    assert out["synced"] is True, out
    return inv, out


def test_refund_and_dispute_never_reach_books(monkeypatch):
    """Momo represents refunds (charge.refunded) and disputes
    (charge.dispute.created) locally, but neither handler syncs to
    books: books keeps the invoice fully paid. Asserted as-is."""
    fake = FakeDB()
    bind_db(monkeypatch, fake, bk_mod, c2p_mod)
    inv, out = _synced_invoice(monkeypatch, fake)
    pi_id = "pi_gap_1"
    stored = next(d for d in fake.invoices.docs if d["id"] == inv["id"])
    stored["last_payment_intent_id"] = pi_id
    before = settle_bt(MT_HVAC)
    run(c2p_mod._apply_refund(fake, {"payment_intent": pi_id,
                                     "amount_refunded": 18000}))
    stored = next(d for d in fake.invoices.docs if d["id"] == inv["id"])
    assert stored["amount_paid_cents"] == 0 and stored["status"] == "sent"
    run(c2p_mod._apply_dispute(fake, {"id": "dp_gap_1",
                                      "payment_intent": pi_id}))
    stored = next(d for d in fake.invoices.docs if d["id"] == inv["id"])
    assert stored["dispute_id"] == "dp_gap_1"
    after = settle_bt(MT_HVAC)
    binv = [i for i in after["invoices"]
            if i["id"] == out["invoice_provider_id"]][0]
    assert binv["status"] == "paid" and binv["paid_cents"] == 18000
    assert after["journal_entries"] == before["journal_entries"]


def test_mark_paid_does_not_sync_but_manual_sync_does(monkeypatch):
    """CRM mark-paid flips Momo state only; the books link is not
    triggered (gap). The link's manual 'Sync now' endpoint is the
    existing recovery path and does sync the same invoice."""
    fake = FakeDB()
    bind_db(monkeypatch, fake, bk_mod, crm_mod)
    seed_link(fake, MT_HVAC)
    before = settle_bt(MT_HVAC)
    inv = paid_invoice(uniq("inv-mp"), 6000, uniq("Manual"),
                       "manual@example.com")
    inv["status"] = "sent"
    inv["amount_paid_cents"] = 0
    fake.invoices.docs.append(inv)
    run(crm_mod.mark_invoice_paid(inv["id"], user=USER_HVAC))
    stored = next(d for d in fake.invoices.docs if d["id"] == inv["id"])
    assert stored["status"] == "paid"
    mid = settle_bt(MT_HVAC)
    assert len(mid["invoices"]) == len(before["invoices"])  # nothing synced
    out = run(bk_mod.manual_sync(inv["id"], user=USER_HVAC))
    assert out["synced"] is True, out
    after = wait_bt(MT_HVAC, lambda d: len(d["invoices"]) ==
                    len(before["invoices"]) + 1)
    assert len(after["invoices"]) == len(before["invoices"]) + 1


# ================= Task 3 — E2E workflow =================

def _journey(monkeypatch, fake, *, tenant, user, books_tenant_key,
             service_name, line_desc, unit_price, tech_id):
    """inquiry -> lead -> appointment -> tech assignment -> estimate
    -> (customer accepts) job appointment + invoice -> payment
    (handler-level synthetic Stripe success) -> books sync (live HTTP)
    -> follow-up (post-job automation). Returns evidence dict."""
    bind_db(monkeypatch, fake, bk_mod, tenants_mod, crm_mod,
            dispatch_mod, c2p_mod, post_job_mod, phase9_mod)
    tenant_doc = seed_link(fake, tenant, name=f"{service_name} Shop")
    tenant_doc["stripe_connect_id"] = f"acct_test_{tenant}"
    tenant_doc["review_url"] = "https://reviews.example.com/x"
    # Tech the dispatcher can assign (dispatch reads schedule_staff).
    fake.users.docs.append({"id": tech_id, "tenant_id": tenant,
                            "name": "Tech One", "role": "tech"})
    fake.schedule_staff.docs.append({"id": tech_id, "tenant_id": tenant,
                                     "name": "Tech One"})
    # The payment-success receipt email is a channel send: stub it at
    # the call_to_payment boundary (no SMTP in this sandbox).
    async def _no_email(**kw):
        return True

    monkeypatch.setattr(c2p_mod, "send_email", _no_email)

    # Lead (the inquiry's landing record).
    lead = run(tenants_mod.create_lead(
        LeadIn(name="Journey Customer", email="journey@example.com",
               phone="555-0142", source="ai_receptionist"),
        user=user))
    # Appointment (diagnostic visit from the industry's pack).
    appt = run(tenants_mod.create_appointment(
        AppointmentIn(customer_name="Journey Customer",
                      customer_phone="555-0142",
                      customer_email="journey@example.com",
                      lead_id=lead["id"], service_name=service_name,
                      start_at="2026-10-20T14:00:00+00:00",
                      end_at="2026-10-20T15:00:00+00:00"),
        user=user))
    # Tech assignment.
    card = run(dispatch_mod.assign(
        dispatch_mod.AssignIn(job_id=appt["id"], tech_id=tech_id,
                              start_at="2026-10-20T14:00:00+00:00"),
        user=user))
    assert card.get("staff") == "Tech One" or \
        card.get("staff_id") == tech_id, card
    # Estimate -> send (owner approval opens online acceptance).
    est = run(crm_mod.create_estimate(
        EstimateIn(customer_name="Journey Customer",
                   customer_phone="555-0142", title=service_name,
                   lines=[{"description": line_desc, "quantity": 1,
                           "unit_price": unit_price}]),
        user=user))
    run(crm_mod.mark_estimate_sent(est["id"], user=user))
    est_doc = next(d for d in fake.estimates.docs if d["id"] == est["id"])
    # Customer accepts online -> job appointment + invoice are created.
    accepted = run(c2p_mod.accept_quote(
        est_doc["public_token"], c2p_mod.QuoteAcceptIn(deposit_cents=0)))
    invoice = accepted["invoice"]
    total = invoice["total_cents"]
    # Payment: the PI record create_invoice_payment would have made,
    # then the Stripe success handler (live Stripe not runnable here).
    pi_id = f"pi_{tenant}_1"
    fake.invoice_payments.docs.append({
        "id": f"ip-{tenant}", "tenant_id": tenant,
        "invoice_id": invoice["id"], "stripe_payment_intent_id": pi_id,
        "amount_cents": total, "connected_account": f"acct_test_{tenant}",
        "status": "requires_payment"})
    before = settle_bt(tenant)
    run(c2p_mod._apply_payment_success(
        fake, {"id": pi_id, "amount_received": total, "currency": "usd",
               "metadata": {"invoice_id": invoice["id"],
                            "tenant_id": tenant}},
        account=f"acct_test_{tenant}"))
    stored = next(d for d in fake.invoices.docs
                  if d["id"] == invoice["id"])
    assert stored["status"] == "paid", stored
    bk = stored.get("bookkeeping_sync", {})
    assert bk.get("invoice_provider_id"), bk
    assert bk.get("payment_provider_id"), (
        f"books payment step did not land: sync state={bk} "
        f"settings_error={tenant_doc.get('bookkeeping_link_settings', {}).get('last_error')}")
    bk_inv_id = bk["invoice_provider_id"]
    after = wait_bt(tenant, lambda d: any(
        i["id"] == bk_inv_id and i["paid_cents"] == total
        for i in d["invoices"]))
    binv = [i for i in after["invoices"] if i["id"] == bk_inv_id]
    assert len(binv) == 1 and binv[0]["paid_cents"] == total
    assert after["journal_entries"] == before["journal_entries"] + 2
    # Follow-up: completing the job appointment fires post-job
    # automation (thank-you + 60-day win-back job). Channel sends are
    # stubbed at the post_job module boundary (no Twilio/SMTP here).
    async def _sms(**kw):
        return {"status": "queued"}

    async def _email(**kw):
        return True

    monkeypatch.setattr(post_job_mod, "send_sms", _sms)
    monkeypatch.setattr(post_job_mod, "send_email", _email)
    job_appt = accepted["appointment_id"]
    appt_doc = next(d for d in fake.appointments.docs
                    if d["id"] == job_appt)
    upd = AppointmentIn(
        customer_name=appt_doc["customer_name"],
        customer_phone=appt_doc.get("customer_phone", ""),
        customer_email=appt_doc.get("customer_email"),
        service_name=appt_doc.get("service_name", ""),
        start_at=appt_doc["start_at"], end_at=appt_doc["end_at"],
        status="completed")
    run(tenants_mod.update_appointment(job_appt, upd, user=user))
    jobs = [d for d in fake.followup_jobs.docs
            if d.get("appointment_id") == job_appt
            and d.get("kind") == "winback-60d"]
    assert jobs and jobs[0]["status"] == "scheduled", jobs
    return {"invoice_id": invoice["id"], "total_cents": total,
            "books_invoice_id": binv[0]["id"]}


def test_e2e_hvac_journey(monkeypatch):
    fake = FakeDB()
    ev = _journey(monkeypatch, fake, tenant=MT_HVAC, user=USER_HVAC,
                  books_tenant_key=MT_HVAC,
                  service_name="Diagnostic Visit",
                  line_desc="AC compressor replacement",
                  unit_price=250.0, tech_id="tech-hvac-1")
    assert ev["total_cents"] == 25000


def test_e2e_plumbing_journey(monkeypatch):
    fake = FakeDB()
    ev = _journey(monkeypatch, fake, tenant=MT_PLUMB, user=USER_PLUMB,
                  books_tenant_key=MT_PLUMB,
                  service_name="Water Heater Install",
                  line_desc="50-gal water heater install",
                  unit_price=300.0, tech_id="tech-plumb-1")
    assert ev["total_cents"] == 30000
