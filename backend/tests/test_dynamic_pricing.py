"""Tests for the dynamic pricing engine (routers/dynamic_pricing.py).

Fake in-memory Mongo (no live DB). Covers rule CRUD + bounds validation,
trigger matching, priority ordering, breakdown math, guardrail clamping,
quote logging/revert, fire counters, and /simulate (synthetic fallback).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "dynamic-pricing-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "dynamic_pricing_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.dynamic_pricing as dp  # noqa: E402
from security import require_tenant_user  # noqa: E402


# ---------- fake mongo ----------

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
        return FakeCursor([d for d in self.docs if _matches(d, query)])

    async def find_one(self, query, proj=None):
        for d in self.docs:
            if _matches(d, query):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("id"))

    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, update)
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            doc = dict(query)
            _apply_update(doc, update)
            self.docs.append(doc)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=True)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def delete_one(self, query):
        for i, d in enumerate(self.docs):
            if _matches(d, query):
                del self.docs[i]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


def _matches(doc, query):
    for k, v in query.items():
        if isinstance(v, dict) and "$in" in v:
            if doc.get(k) not in v["$in"]:
                return False
        elif doc.get(k) != v:
            return False
    return True


def _apply_update(doc, update):
    for k, v in update.get("$set", {}).items():
        doc[k] = v
    for k, v in update.get("$inc", {}).items():
        doc[k] = doc.get(k, 0) + v


class FakeDB:
    def __init__(self):
        self.pricing_rules = FakeCollection()
        self.pricebook_items = FakeCollection()
        self.tenant_settings = FakeCollection()
        self.pricing_quote_log = FakeCollection()
        self.appointments = FakeCollection()


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(dp, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(dp.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


@pytest.fixture
def pricebook(fake_db):
    fake_db.pricebook_items.docs.extend([
        {"id": "item-a", "tenant_id": "t1", "name": "Capacitor replacement",
         "price": 100.0, "cost": 30.0, "options": [], "active": True},
        {"id": "item-b", "tenant_id": "t1", "name": "Fan motor",
         "price": 250.0, "cost": 90.0,
         "options": [{"id": "opt-1", "name": "Extended warranty", "price": 25.0}],
         "active": True},
    ])


def make_rule(**over):
    body = {
        "name": "After-hours fee",
        "enabled": True,
        "priority": 10,
        "trigger": {"time_window": {"start": "17:00", "end": "23:59"}},
        "adjustment": {"mode": "percent", "value": 15.0},
    }
    body.update(over)
    return body


# ---------- rule CRUD ----------

def test_create_rule_happy_path(client):
    as_user("t1")
    r = client.post("/tenants/dynamic-pricing/rules", json=make_rule())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "After-hours fee"
    assert body["trigger"]["time_window"] == {"start": "17:00", "end": "23:59"}
    assert body["adjustment"] == {"mode": "percent", "value": 15.0}
    assert body["fire_count"] == 0
    assert body["id"]


def test_create_rule_requires_owner_or_admin(client):
    as_user("t1", role="tech")
    r = client.post("/tenants/dynamic-pricing/rules", json=make_rule())
    assert r.status_code == 403
    as_user("t1", role="admin")
    r = client.post("/tenants/dynamic-pricing/rules", json=make_rule())
    assert r.status_code == 200


def test_create_rule_percent_beyond_markup_bound_rejected(client):
    as_user("t1")
    # default shop bound: max_markup_pct = 30
    r = client.post("/tenants/dynamic-pricing/rules", json=make_rule(
        adjustment={"mode": "percent", "value": 45.0}))
    assert r.status_code == 400
    assert "bounds" in r.json()["detail"].lower()


def test_create_rule_discount_beyond_discount_bound_rejected(client):
    as_user("t1")
    # default shop bound: max_discount_pct = 20
    r = client.post("/tenants/dynamic-pricing/rules", json=make_rule(
        name="Slow-day discount", adjustment={"mode": "percent", "value": -35.0}))
    assert r.status_code == 400


def test_create_rule_zero_adjustment_rejected(client):
    as_user("t1")
    r = client.post("/tenants/dynamic-pricing/rules", json=make_rule(
        adjustment={"mode": "percent", "value": 0}))
    assert r.status_code == 400


def test_create_rule_empty_trigger_rejected(client):
    as_user("t1")
    r = client.post("/tenants/dynamic-pricing/rules", json=make_rule(trigger={}))
    assert r.status_code == 400


def test_create_rule_bad_day_of_week_rejected(client):
    as_user("t1")
    r = client.post("/tenants/dynamic-pricing/rules", json=make_rule(
        trigger={"day_of_week": [7]}))
    assert r.status_code == 400


def test_create_rule_season_normalizes_to_months(client):
    as_user("t1")
    r = client.post("/tenants/dynamic-pricing/rules", json=make_rule(
        name="Summer peak", trigger={"season": "summer"}))
    assert r.status_code == 200
    assert r.json()["trigger"]["months"] == [6, 7, 8]


def test_create_rule_flat_beyond_price_span_rejected(client):
    as_user("t1")
    # default span: max_price 10000 - min_price 0 = 10000
    r = client.post("/tenants/dynamic-pricing/rules", json=make_rule(
        adjustment={"mode": "flat", "value": 25000.0}))
    assert r.status_code == 400


def test_list_rules_sorted_by_priority(client):
    as_user("t1")
    client.post("/tenants/dynamic-pricing/rules", json=make_rule(name="low prio", priority=50))
    client.post("/tenants/dynamic-pricing/rules", json=make_rule(name="high prio", priority=5))
    r = client.get("/tenants/dynamic-pricing/rules")
    assert r.status_code == 200
    rules = r.json()["rules"]
    assert [x["name"] for x in rules] == ["high prio", "low prio"]


def test_update_rule_revalidates_bounds(client):
    as_user("t1")
    rid = client.post("/tenants/dynamic-pricing/rules", json=make_rule()).json()["id"]
    r = client.put(f"/tenants/dynamic-pricing/rules/{rid}",
                   json={"adjustment": {"mode": "percent", "value": 99.0}})
    assert r.status_code == 400
    r = client.put(f"/tenants/dynamic-pricing/rules/{rid}",
                   json={"name": "Evening fee", "enabled": False})
    assert r.status_code == 200
    assert r.json()["name"] == "Evening fee"
    assert r.json()["enabled"] is False


def test_delete_rule(client):
    as_user("t1")
    rid = client.post("/tenants/dynamic-pricing/rules", json=make_rule()).json()["id"]
    r = client.delete(f"/tenants/dynamic-pricing/rules/{rid}")
    assert r.status_code == 200
    assert client.get("/tenants/dynamic-pricing/rules").json()["count"] == 0


def test_rules_are_tenant_scoped(client):
    as_user("t1")
    client.post("/tenants/dynamic-pricing/rules", json=make_rule())
    as_user("t2")
    assert client.get("/tenants/dynamic-pricing/rules").json()["count"] == 0
    as_user("t1")
    assert client.get("/tenants/dynamic-pricing/rules").json()["count"] == 1


# ---------- bounds ----------

def test_bounds_defaults_and_update(client):
    as_user("t1")
    b = client.get("/tenants/dynamic-pricing/bounds").json()["bounds"]
    assert b["max_markup_pct"] == 30.0 and b["max_discount_pct"] == 20.0

    r = client.put("/tenants/dynamic-pricing/bounds", json={
        "enabled": True, "max_markup_pct": 50.0, "max_discount_pct": 25.0,
        "min_price": 10.0, "max_price": 2000.0,
    })
    assert r.status_code == 200
    assert r.json()["bounds"]["max_markup_pct"] == 50.0

    # new wider bound now lets a 45% rule through
    r = client.post("/tenants/dynamic-pricing/rules", json=make_rule(
        adjustment={"mode": "percent", "value": 45.0}))
    assert r.status_code == 200


def test_bounds_min_gte_max_rejected(client):
    as_user("t1")
    r = client.put("/tenants/dynamic-pricing/bounds", json={
        "enabled": True, "max_markup_pct": 30.0, "max_discount_pct": 20.0,
        "min_price": 500.0, "max_price": 100.0,
    })
    assert r.status_code == 400


# ---------- quote engine ----------

def _sat_evening():
    """A Saturday at 19:30 (weekday 5)."""
    return "2026-10-17T19:30:00"


def test_quote_priority_ordering_and_breakdown_math(client, pricebook):
    as_user("t1")
    # Rule A (priority 1): +10%. Rule B (priority 5): +$20 flat. Both fire.
    client.post("/tenants/dynamic-pricing/rules", json=make_rule(
        name="evening +10%", priority=1,
        trigger={"time_window": {"start": "17:00", "end": "23:59"}},
        adjustment={"mode": "percent", "value": 10.0}))
    client.post("/tenants/dynamic-pricing/rules", json=make_rule(
        name="weekend flat", priority=5,
        trigger={"day_of_week": [5, 6]},
        adjustment={"mode": "flat", "value": 20.0}))

    r = client.post("/tenants/dynamic-pricing/quote", json={
        "selections": [{"item_id": "item-a", "qty": 1}],
        "at": _sat_evening(),
    })
    assert r.status_code == 200, r.text
    body = r.json()
    line = body["lines"][0]
    assert line["base_unit"] == 100.0
    # priority order: +10% first (100 -> 110), then +$20 (110 -> 130)
    assert [b["rule_name"] for b in line["breakdown"]] == ["evening +10%", "weekend flat"]
    assert line["breakdown"][0]["amount"] == 10.0
    assert line["breakdown"][0]["price_before"] == 100.0
    assert line["breakdown"][0]["price_after"] == 110.0
    assert line["breakdown"][1]["amount"] == 20.0
    assert line["breakdown"][1]["price_after"] == 130.0
    assert line["final_unit"] == 130.0
    assert body["subtotal_base"] == 100.0
    assert body["subtotal_final"] == 130.0
    assert body["adjustments_total"] == 30.0
    assert len(body["rules_fired"]) == 2


def test_quote_no_matching_rules_returns_base(client, pricebook):
    as_user("t1")
    client.post("/tenants/dynamic-pricing/rules", json=make_rule(
        name="winter", trigger={"season": "winter"},
        adjustment={"mode": "percent", "value": 10.0}))
    r = client.post("/tenants/dynamic-pricing/quote", json={
        "selections": [{"item_id": "item-a", "qty": 2}],
        "at": _sat_evening(),  # October — not winter
    })
    body = r.json()
    assert body["rules_fired"] == []
    assert body["lines"][0]["final_unit"] == 100.0
    assert body["subtotal_final"] == 200.0
    assert body["adjustments_total"] == 0.0


def test_quote_clamps_to_shop_max_price(client, pricebook):
    as_user("t1")
    client.put("/tenants/dynamic-pricing/bounds", json={
        "enabled": True, "max_markup_pct": 30.0, "max_discount_pct": 20.0,
        "min_price": 0.0, "max_price": 120.0,
    })
    client.post("/tenants/dynamic-pricing/rules", json=make_rule(
        adjustment={"mode": "percent", "value": 30.0}))
    r = client.post("/tenants/dynamic-pricing/quote", json={
        "selections": [{"item_id": "item-a", "qty": 1}],
        "at": _sat_evening(),
    })
    body = r.json()
    # 100 * 1.3 = 130 -> clamped to shop max 120
    assert body["lines"][0]["final_unit"] == 120.0
    assert body["lines"][0]["shop_clamped"] is True
    assert body["clamped_lines"] == 1


def test_quote_rule_level_cap(client, pricebook):
    as_user("t1")
    client.post("/tenants/dynamic-pricing/rules", json=make_rule(
        adjustment={"mode": "percent", "value": 30.0},
        cap={"max_price": 115.0}))
    r = client.post("/tenants/dynamic-pricing/quote", json={
        "selections": [{"item_id": "item-a", "qty": 1}],
        "at": _sat_evening(),
    })
    body = r.json()
    # 100 * 1.3 = 130 -> rule cap 115
    assert body["lines"][0]["final_unit"] == 115.0
    assert body["lines"][0]["breakdown"][0]["rule_clamped"] is True


def test_quote_midnight_wrapping_window(client, pricebook):
    as_user("t1")
    client.post("/tenants/dynamic-pricing/rules", json=make_rule(
        name="night owl", trigger={"time_window": {"start": "22:00", "end": "02:00"}},
        adjustment={"mode": "percent", "value": 10.0}))
    # 23:30 matches (after start)
    r = client.post("/tenants/dynamic-pricing/quote", json={
        "selections": [{"item_id": "item-a", "qty": 1}],
        "at": "2026-10-17T23:30:00",
    })
    assert len(r.json()["rules_fired"]) == 1
    # 01:30 next day matches (before end)
    r = client.post("/tenants/dynamic-pricing/quote", json={
        "selections": [{"item_id": "item-a", "qty": 1}],
        "at": "2026-10-18T01:30:00",
    })
    assert len(r.json()["rules_fired"]) == 1
    # 15:00 does not match
    r = client.post("/tenants/dynamic-pricing/quote", json={
        "selections": [{"item_id": "item-a", "qty": 1}],
        "at": "2026-10-18T15:00:00",
    })
    assert r.json()["rules_fired"] == []


def test_quote_demand_trigger_from_bookings_ratio(client, pricebook):
    as_user("t1")
    client.post("/tenants/dynamic-pricing/rules", json=make_rule(
        name="surge", trigger={"demand_level": "high"},
        adjustment={"mode": "percent", "value": 20.0}))
    # 9 of 10 slots booked -> surge (>= high) -> fires
    r = client.post("/tenants/dynamic-pricing/quote", json={
        "selections": [{"item_id": "item-a", "qty": 1}],
        "at": _sat_evening(),
        "context": {"bookings_today": 9, "capacity_today": 10},
    })
    body = r.json()
    assert body["demand_level"] == "surge"
    assert len(body["rules_fired"]) == 1
    assert body["lines"][0]["final_unit"] == 120.0
    # 2 of 10 booked -> low -> no fire
    r = client.post("/tenants/dynamic-pricing/quote", json={
        "selections": [{"item_id": "item-a", "qty": 1}],
        "at": _sat_evening(),
        "context": {"bookings_today": 2, "capacity_today": 10},
    })
    assert r.json()["rules_fired"] == []


def test_quote_with_options_and_qty(client, pricebook):
    as_user("t1")
    client.post("/tenants/dynamic-pricing/rules", json=make_rule(
        adjustment={"mode": "percent", "value": 10.0}))
    r = client.post("/tenants/dynamic-pricing/quote", json={
        "selections": [{"item_id": "item-b", "qty": 2, "option_ids": ["opt-1"]}],
        "at": _sat_evening(),
    })
    body = r.json()
    # base = 250 + 25 option = 275; +10% = 302.5; qty 2 -> 605
    assert body["lines"][0]["base_unit"] == 275.0
    assert body["lines"][0]["final_unit"] == 302.5
    assert body["lines"][0]["line_total"] == 605.0


def test_quote_unknown_item_404(client, pricebook):
    as_user("t1")
    r = client.post("/tenants/dynamic-pricing/quote", json={
        "selections": [{"item_id": "nope", "qty": 1}],
    })
    assert r.status_code == 404


def test_quote_disabled_rule_does_not_fire(client, pricebook):
    as_user("t1")
    client.post("/tenants/dynamic-pricing/rules", json=make_rule(enabled=False))
    r = client.post("/tenants/dynamic-pricing/quote", json={
        "selections": [{"item_id": "item-a", "qty": 1}],
        "at": _sat_evening(),
    })
    assert r.json()["rules_fired"] == []


def test_quote_logs_and_increments_fire_count(client, fake_db, pricebook):
    as_user("t1")
    rid = client.post("/tenants/dynamic-pricing/rules", json=make_rule()).json()["id"]
    r = client.post("/tenants/dynamic-pricing/quote", json={
        "selections": [{"item_id": "item-a", "qty": 1}],
        "at": _sat_evening(),
    })
    quote_id = r.json()["id"]
    # log written with full breakdown
    assert len(fake_db.pricing_quote_log.docs) == 1
    log = fake_db.pricing_quote_log.docs[0]
    assert log["id"] == quote_id
    assert log["reverted"] is False
    assert log["lines"][0]["breakdown"][0]["rule_id"] == rid
    # fire counter bumped
    rule = next(d for d in fake_db.pricing_rules.docs if d["id"] == rid)
    assert rule["fire_count"] == 1
    assert rule["last_fired_at"]
    # performance endpoint reflects it
    perf = client.get("/tenants/dynamic-pricing/rules/performance").json()
    assert perf["rules"][0]["fire_count"] == 1
    assert perf["quotes_logged"] == 1
    # GET /quotes lists the log
    quotes = client.get("/tenants/dynamic-pricing/quotes").json()
    assert quotes["count"] == 1
    assert quotes["quotes"][0]["id"] == quote_id


def test_quote_revert_marks_log(client, pricebook):
    as_user("t1")
    quote_id = client.post("/tenants/dynamic-pricing/quote", json={
        "selections": [{"item_id": "item-a", "qty": 1}],
    }).json()["id"]
    r = client.put(f"/tenants/dynamic-pricing/quotes/{quote_id}/revert")
    assert r.status_code == 200
    assert r.json()["reverted"] is True
    assert r.json()["reverted_at"]
    # pricebook untouched by the whole flow
    assert client.get("/tenants/dynamic-pricing/quotes").json()["quotes"][0]["reverted"] is True


def test_quote_revert_requires_owner_or_admin(client, pricebook):
    as_user("t1")
    quote_id = client.post("/tenants/dynamic-pricing/quote", json={
        "selections": [{"item_id": "item-a", "qty": 1}],
    }).json()["id"]
    as_user("t1", role="tech")
    assert client.put(f"/tenants/dynamic-pricing/quotes/{quote_id}/revert").status_code == 403


# ---------- simulate ----------

def test_simulate_saved_rule_with_real_appointments(client, fake_db):
    as_user("t1")
    rid = client.post("/tenants/dynamic-pricing/rules", json=make_rule(
        name="weekend", trigger={"day_of_week": [5, 6]},
        adjustment={"mode": "percent", "value": 10.0})).json()["id"]
    # 6 appointments: 4 on a Saturday, 2 on a Monday (recent dates)
    sat = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    while sat.weekday() != 5:
        sat = sat - timedelta(days=1)
    mon = sat - timedelta(days=5)
    for i in range(4):
        fake_db.appointments.docs.append({
            "id": f"s{i}", "tenant_id": "t1", "start": sat.replace(hour=9 + i).isoformat()})
    for i in range(2):
        fake_db.appointments.docs.append({
            "id": f"m{i}", "tenant_id": "t1", "start": mon.replace(hour=9 + i).isoformat()})
    r = client.get("/tenants/dynamic-pricing/simulate", params={"rule_id": rid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["synthetic_data"] is False
    assert body["appointments_evaluated"] == 6
    assert body["would_fire"] == 4
    assert body["fire_rate"] == pytest.approx(4 / 6, abs=1e-3)


def test_simulate_preview_rule_with_synthetic_fallback(client):
    as_user("t1")  # no appointments seeded
    r = client.get("/tenants/dynamic-pricing/simulate", params={
        "day_of_week": "5,6", "window_days": 30,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["synthetic_data"] is True
    assert body["appointments_evaluated"] > 0
    assert body["would_fire"] > 0  # synthetic weekends exist
    assert 0 < body["fire_rate"] < 1
    assert len(body["per_day"]) == 30


def test_simulate_deterministic_synthetic(client):
    as_user("t1")
    b1 = client.get("/tenants/dynamic-pricing/simulate", params={"day_of_week": "5,6"}).json()
    b2 = client.get("/tenants/dynamic-pricing/simulate", params={"day_of_week": "5,6"}).json()
    assert b1["would_fire"] == b2["would_fire"]
    assert b1["appointments_evaluated"] == b2["appointments_evaluated"]


def test_simulate_empty_trigger_rejected(client):
    as_user("t1")
    r = client.get("/tenants/dynamic-pricing/simulate")
    assert r.status_code == 400


def test_simulate_unknown_rule_404(client):
    as_user("t1")
    r = client.get("/tenants/dynamic-pricing/simulate", params={"rule_id": "nope"})
    assert r.status_code == 404


def test_trigger_match_unit_day_and_month():
    # direct unit checks of the matcher
    sat_july = datetime(2026, 7, 18, 19, 0)  # Saturday, July
    assert dp._trigger_matches({"day_of_week": [5, 6], "months": [6, 7, 8]}, sat_july, "normal")
    assert not dp._trigger_matches({"day_of_week": [0]}, sat_july, "normal")
    assert not dp._trigger_matches({"months": [1]}, sat_july, "normal")
    assert dp._trigger_matches({"demand_level": "normal"}, sat_july, "surge")
    assert not dp._trigger_matches({"demand_level": "surge"}, sat_july, "normal")
