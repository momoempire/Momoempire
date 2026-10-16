"""Unit tests for the predictive revenue engine
(services/predictive_revenue.py, routers/predictive_revenue.py).

No database, network, or browser: a small FastAPI app with only the revenue
router, a fake in-memory Mongo, overridden auth dependencies, and the quote
pipeline (`_create_estimate_doc`) monkeypatched — no live calls anywhere.
"""
from __future__ import annotations

import os
import sys
import types
from datetime import datetime, timezone, timedelta

import pytest

os.environ.setdefault("JWT_SECRET", "predictive-revenue-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "predictive_revenue_test")

# Stub `stripe` before importing call_to_payment (unit env has no stripe pkg).
_stripe_stub = types.ModuleType("stripe")
_stripe_stub.api_key = None


class _StripeError(Exception):
    pass


_stripe_stub.error = types.SimpleNamespace(StripeError=_StripeError)
sys.modules.setdefault("stripe", _stripe_stub)

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.predictive_revenue as pr_mod  # noqa: E402
from security import require_tenant_user, require_tenant_owner_or_admin  # noqa: E402
from services.predictive_revenue import (  # noqa: E402
    age_years,
    count_recent_repairs,
    expected_lifespan_years,
    replacement_score,
    score_equipment,
    season_factor,
    suggest_items,
)


# ---------- fake mongo (same shape as test_photo_estimates.py) ----------

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

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)  # fixed "today" for determinism

current_user = {"tenant_id": "t1", "id": "u1", "role": "owner"}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(pr_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(pr_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    app.dependency_overrides[require_tenant_owner_or_admin] = lambda: current_user
    return TestClient(app)


UP = "/tenants/revenue"


def _equipment(eid, etype, install_date, name="Acme Home", tenant="t1", **kw):
    doc = {
        "id": eid, "tenant_id": tenant, "customer_id": f"c-{eid}",
        "customer_name": name, "location_label": "", "service_address": {},
        "equipment_type": etype, "make": "Trane", "model": "XR14",
        "serial_number": "", "install_date": install_date, "notes": "",
        "active": True, "service_event_count": 0,
    }
    doc.update(kw)
    return doc


def _event(eid, desc, days_ago, tenant="t1", eq_id="eq-x"):
    return {
        "id": eid, "tenant_id": tenant, "equipment_id": eq_id,
        "service_date": (NOW - timedelta(days=days_ago)).isoformat(),
        "job_id": None, "description": desc, "tech": "",
    }


def _seed_shop(fake_db, tenant="t1"):
    """Two old units (one with a repair-heavy timeline) + one new unit."""
    fake_db.equipment.docs.extend([
        _equipment("eq-old-furnace", "furnace", "2004-02-01",
                   name="Acme Home", tenant=tenant),
        _equipment("eq-old-ac", "ac_condenser", "2008-06-15",
                   name="Beta Casa", tenant=tenant),
        _equipment("eq-new-ac", "ac_condenser", "2025-03-01",
                   name="Gamma Loft", tenant=tenant),
    ])
    fake_db.equipment_service_events.docs.extend([
        _event("ev1", "Compressor repair — capacitor replaced", 60, tenant, "eq-old-furnace"),
        _event("ev2", "Ignitor failed, repair done", 200, tenant, "eq-old-furnace"),
        _event("ev3", "Blower motor replaced after failure", 340, tenant, "eq-old-furnace"),
        _event("ev4", "Annual tune-up, cleaned coils", 90, tenant, "eq-old-ac"),
        _event("ev5", "Filter change + inspection", 400, tenant, "eq-new-ac"),
    ])
    fake_db.pricebook_items.docs.extend([
        {"id": "pb-f1", "tenant_id": tenant, "name": "Furnace replacement — high efficiency",
         "description": "", "category": "service", "price": 8500.0, "cost": 5000.0,
         "options": [], "active": True, "version": 2},
        {"id": "pb-f2", "tenant_id": tenant, "name": "Furnace install labor",
         "description": "", "category": "labor", "price": 1200.0, "cost": 400.0,
         "options": [], "active": True, "version": 1},
        {"id": "pb-a1", "tenant_id": tenant, "name": "AC replacement install 3-ton",
         "description": "", "category": "service", "price": 7200.0, "cost": 4200.0,
         "options": [], "active": True, "version": 1},
        {"id": "pb-misc", "tenant_id": tenant, "name": "Drain line cleaning",
         "description": "", "category": "service", "price": 189.0, "cost": 30.0,
         "options": [], "active": True, "version": 1},
    ])


# ---------- scoring engine determinism ----------

def test_lifespan_table_and_fallback():
    assert expected_lifespan_years("furnace") == 18
    assert expected_lifespan_years("AC_CONDENSER") == 15
    assert expected_lifespan_years("mystery_type") == 12
    assert expected_lifespan_years("") == 12


def test_age_years_missing_or_invalid():
    assert age_years(None) == 0.0
    assert age_years("") == 0.0
    assert age_years("not-a-date") == 0.0
    assert age_years("2006-10-15T00:00:00+00:00", now=NOW) == 20.0


def test_old_unit_with_frequent_repairs_outranks_new_unit():
    old = _equipment("x", "furnace", "2004-02-01")
    new = _equipment("y", "ac_condenser", "2025-03-01")
    old_events = [
        _event("a", "repair: ignitor replaced", 30, eq_id="x"),
        _event("b", "blower motor failure, repaired", 150, eq_id="x"),
        _event("c", "gas valve replaced", 300, eq_id="x"),
    ]
    new_events = [_event("d", "tune-up, cleaned", 90, eq_id="y")]
    a = score_equipment(old, old_events, now=NOW, month=10)["replacement_score"]
    b = score_equipment(new, new_events, now=NOW, month=10)["replacement_score"]
    assert a > b
    assert a >= 60  # aged-out furnace floor, repairs push it higher
    assert b < 40


def test_scoring_is_deterministic():
    eq = _equipment("x", "water_heater", "2013-05-01")
    evs = [_event("a", "tank leak, repaired", 100, eq_id="x")]
    first = score_equipment(eq, evs, now=NOW, month=6)
    second = score_equipment(eq, evs, now=NOW, month=6)
    assert first == second
    assert 0 <= first["replacement_score"] <= 100


def test_season_factor_changes_ranking():
    """Identical units except type: AC wins in July, furnace wins in December."""
    ac = _equipment("ac", "ac_condenser", "2010-01-01")
    furn = _equipment("f", "furnace", "2010-01-01")
    summer_ac = score_equipment(ac, [], now=NOW, month=7)["replacement_score"]
    summer_furn = score_equipment(furn, [], now=NOW, month=7)["replacement_score"]
    winter_ac = score_equipment(ac, [], now=NOW, month=12)["replacement_score"]
    winter_furn = score_equipment(furn, [], now=NOW, month=12)["replacement_score"]
    assert summer_ac > summer_furn
    assert winter_furn > winter_ac
    # pure function of (type, month)
    assert season_factor("ac_condenser", 7) == 1.0
    assert season_factor("furnace", 7) == 0.0
    assert season_factor("furnace", 12) == 1.0
    assert season_factor("thermostat", 6) == 0.0


def test_repair_count_ignores_maintenance_notes():
    evs = [
        _event("a", "Annual tune-up, cleaned coils, inspected", 30),
        _event("b", "Filter change", 60),
    ]
    assert count_recent_repairs(evs, now=NOW) == 0
    evs.append(_event("c", "Compressor failed — repair", 10))
    assert count_recent_repairs(evs, now=NOW) == 1
    # event older than the 365-day window does not count
    assert count_recent_repairs([_event("d", "repair done", 400)], now=NOW) == 0


def test_replacement_score_caps_at_100():
    assert replacement_score(30, 12, 10, 1.0) == 100
    assert replacement_score(0, 12, 0, 0.0) == 0


def test_suggest_items_keyword_ranking():
    catalog = [
        {"id": "i1", "name": "Furnace replacement — high efficiency", "price": 8500.0, "version": 2},
        {"id": "i2", "name": "Drain line cleaning", "price": 189.0, "version": 1},
        {"id": "i3", "name": "Furnace install labor", "price": 1200.0, "version": 1},
    ]
    got = suggest_items("furnace", catalog)
    # both hit 1 keyword; tie broken deterministically by name
    assert [g["id"] for g in got] == ["i3", "i1"]  # cleaning is unrelated
    assert got[1]["price"] == 8500.0
    assert got[1]["version"] == 2
    assert suggest_items("furnace", []) == []
    assert suggest_items("mystery_type", catalog)  # fallback keywords still match


# ---------- GET /opportunities ----------

def test_list_opportunities_ranked_with_expected_shape(client, fake_db):
    _seed_shop(fake_db)
    r = client.get(f"{UP}/opportunities")
    assert r.status_code == 200
    body = r.json()
    opps = body["opportunities"]
    # furnace (oldest + 3 repairs) first, then old AC, new AC below MIN_SCORE
    assert [o["equipment_id"] for o in opps] == ["eq-old-furnace", "eq-old-ac"]
    first = opps[0]
    assert first["customer"] == {"id": "c-eq-old-furnace", "name": "Acme Home"}
    assert first["equipment_type"] == "furnace"
    assert first["age_years"] > 20
    assert first["expected_lifespan_years"] == 18
    assert 0 <= first["replacement_score"] <= 100
    assert 0.0 <= first["season_factor"] <= 1.0
    assert first["suggested_items"]  # furnace items matched
    assert first["estimated_value"] == sum(
        i["price"] for i in first["suggested_items"])
    assert first["estimated_value"] == 8500.0 + 1200.0
    assert first["quote_drafted"] is False
    # descending scores
    scores = [o["replacement_score"] for o in opps]
    assert scores == sorted(scores, reverse=True)


def test_dismiss_hides_opportunity(client, fake_db):
    _seed_shop(fake_db)
    r = client.post(f"{UP}/opportunities/eq-old-ac/dismiss",
                    json={"reason": "customer just replaced it"})
    assert r.status_code == 200
    assert r.json()["equipment_id"] == "eq-old-ac"
    # reason + tuning fields persisted
    row = fake_db.revenue_dismissals.docs[0]
    assert row["reason"] == "customer just replaced it"
    assert row["equipment_type"] == "ac_condenser"
    assert 0 <= row["score_at_dismissal"] <= 100
    ids = [o["equipment_id"] for o in client.get(f"{UP}/opportunities").json()["opportunities"]]
    assert "eq-old-ac" not in ids
    assert "eq-old-furnace" in ids


def test_dismiss_requires_reason(client, fake_db):
    _seed_shop(fake_db)
    assert client.post(f"{UP}/opportunities/eq-old-ac/dismiss",
                       json={"reason": "  "}).status_code == 400
    assert client.post(f"{UP}/opportunities/nope/dismiss",
                       json={"reason": "x"}).status_code == 404


def test_tenant_isolation(client, fake_db):
    _seed_shop(fake_db)                      # tenant t1
    _seed_shop(fake_db, tenant="t2")          # same-shaped units for t2
    try:
        # t2's dismissal of its own copy must not hide t1's unit
        current_user["tenant_id"] = "t2"
        assert client.post(f"{UP}/opportunities/eq-old-furnace/dismiss",
                           json={"reason": "t2 only"}).status_code == 200
        t2_ids = [o["equipment_id"] for o in
                  client.get(f"{UP}/opportunities").json()["opportunities"]]
        assert "eq-old-furnace" not in t2_ids
    finally:
        current_user["tenant_id"] = "t1"
    t1_ids = [o["equipment_id"] for o in
              client.get(f"{UP}/opportunities").json()["opportunities"]]
    assert "eq-old-furnace" in t1_ids  # t1 untouched by t2's dismissal
    # t2 sees nothing of t1's revenue draft bookkeeping either
    assert {d["tenant_id"] for d in fake_db.revenue_dismissals.docs} == {"t2"}


# ---------- POST /opportunities/{id}/draft-quote (mocked pipeline) ----------

def _mock_pipeline(monkeypatch, fake_db, calls):
    async def fake_create_estimate_doc(db, tenant_id, *, lead_id, customer_name,
                                       customer_email, customer_phone, title,
                                       lines, notes="", expires_in_days=14):
        calls.append({
            "tenant_id": tenant_id, "customer_name": customer_name,
            "title": title, "lines": lines, "notes": notes,
            "expires_in_days": expires_in_days,
        })
        doc = {
            "id": "q-mocked", "tenant_id": tenant_id,
            "customer_name": customer_name, "title": title,
            "lines": lines, "notes": notes,
            "total": round(sum(l["quantity"] * l["unit_price"] for l in lines), 2),
            "status": "draft",
        }
        db.estimates.docs.append(dict(doc))
        return dict(doc)

    monkeypatch.setattr(pr_mod, "_create_estimate_doc", fake_create_estimate_doc)


def test_draft_quote_uses_mocked_pipeline_and_freezes_prices(client, fake_db, monkeypatch):
    _seed_shop(fake_db)
    calls = []
    _mock_pipeline(monkeypatch, fake_db, calls)

    r = client.post(f"{UP}/opportunities/eq-old-furnace/draft-quote")
    assert r.status_code == 200
    body = r.json()
    assert body["already_drafted"] is False
    quote = body["quote"]
    assert quote["status"] == "draft"
    assert quote["customer_name"] == "Acme Home"
    assert "Furnace" in quote["title"] or "furnace" in quote["title"].lower()
    assert quote["total"] == 8500.0 + 1200.0
    # frozen pricebook prices on the lines
    lines = quote["lines"]
    assert {(l["description"], l["unit_price"], l["pricebook_item_id"])
            for l in lines} == {
        ("Furnace replacement — high efficiency", 8500.0, "pb-f1"),
        ("Furnace install labor", 1200.0, "pb-f2"),
    }
    assert all(l["pricebook_version"] for l in lines)
    # financing note attached, referencing the offers endpoint — no live call
    assert "GET /tenants/financing/offers" in quote["notes"]
    assert "financing" in body["financing_note"].lower()
    # pipeline received tenant-scoped args
    assert calls[0]["tenant_id"] == "t1"
    # idempotent: second tap returns the original quote, no new draft
    r2 = client.post(f"{UP}/opportunities/eq-old-furnace/draft-quote")
    assert r2.json()["already_drafted"] is True
    assert r2.json()["quote"]["id"] == "q-mocked"
    assert len(calls) == 1


def test_draft_quote_unknown_equipment_404(client, fake_db):
    _seed_shop(fake_db)
    assert client.post(f"{UP}/opportunities/nope/draft-quote").status_code == 404


def test_draft_quote_no_matching_items_409(client, fake_db, monkeypatch):
    _seed_shop(fake_db)
    # wipe furnace-relevant items; only a cleaning item remains
    fake_db.pricebook_items.docs = [d for d in fake_db.pricebook_items.docs
                                    if d["id"] == "pb-misc"]
    calls = []
    _mock_pipeline(monkeypatch, fake_db, calls)
    r = client.post(f"{UP}/opportunities/eq-old-furnace/draft-quote")
    assert r.status_code == 409
    assert not calls


def test_draft_quote_dismissed_stays_dismissed(client, fake_db, monkeypatch):
    _seed_shop(fake_db)
    calls = []
    _mock_pipeline(monkeypatch, fake_db, calls)
    client.post(f"{UP}/opportunities/eq-old-ac/dismiss", json={"reason": "done"})
    # drafting directly by id still works for owner (dismiss only hides listing)
    r = client.post(f"{UP}/opportunities/eq-old-ac/draft-quote")
    assert r.status_code == 200
    assert len(calls) == 1
