"""Tests for AI triage self-booking (backend/routers/triage.py).

Fake in-memory Mongo + a small FastAPI app with only the triage routers.
Coverage:
- triage categorization cases (incl. emergency detection),
- low-confidence descriptions -> clarifying questions,
- slot listing is tenant-scoped (shop A never sees shop B's schedule),
- booking creates a PENDING request visible to the tenant only,
- public routes need no auth; tenant routes do,
- confirm creates a real appointment; decline marks declined;
  double-booking a slot is rejected.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "triage-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "triage_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.triage as tri_mod  # noqa: E402
from routers.triage import classify  # noqa: E402
from security import require_tenant_owner_or_admin, require_tenant_user  # noqa: E402


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
        val = doc.get(k)
        if isinstance(cond, dict):
            for op, opval in cond.items():
                if op == "$in" and val not in opval:
                    return False
                elif op == "$ne" and val == opval:
                    return False
                elif op == "$gte" and not (val is not None and val >= opval):
                    return False
                elif op == "$lte" and not (val is not None and val <= opval):
                    return False
                elif op == "$gt" and not (val is not None and val > opval):
                    return False
                elif op == "$lt" and not (val is not None and val < opval):
                    return False
                elif op not in ("$in", "$ne", "$gte", "$lte", "$gt", "$lt"):
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
        self.tenants = FakeCollection()
        self.appointments = FakeCollection()
        self.appointment_types = FakeCollection()
        self.triage_analyses = FakeCollection()
        self.triage_requests = FakeCollection()


# ---------- fixtures ----------

current_user = {}
_db = None


@pytest.fixture
def fake_db():
    global _db
    _db = FakeDB()
    return _db


def _app():
    app = FastAPI()
    app.include_router(tri_mod.router)
    app.include_router(tri_mod.public_router)
    return app


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(tri_mod, "get_db", lambda: fake_db)
    app = _app()
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    app.dependency_overrides[require_tenant_owner_or_admin] = lambda: current_user
    return TestClient(app)


@pytest.fixture
def public_client(monkeypatch, fake_db):
    """No auth overrides at all: public routes must work, tenant routes 401."""
    monkeypatch.setattr(tri_mod, "get_db", lambda: fake_db)
    return TestClient(_app())


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def make_tenant(db, tid, slug, hours=None):
    doc = {
        "id": tid, "slug": slug, "name": f"Shop {slug}", "status": "active",
        "hours": hours or {d: "09:00-17:00" for d in
                           ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]},
    }
    db.tenants.docs.append(doc)
    return doc


def make_appointment(db, tid, start_iso, end_iso):
    db.appointments.docs.append({
        "id": f"appt-{tid}-{len(db.appointments.docs)}",
        "tenant_id": tid, "start_at": start_iso, "end_at": end_iso,
        "customer_name": "Busy", "status": "scheduled",
    })


def tomorrow_at(hour, minute=0):
    return (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        hour=hour, minute=minute, second=0, microsecond=0)


def analyze(c, slug, description):
    return c.post(f"/public/triage/{slug}/analyze", json={"description": description})


# ---------- classifier ----------

def test_classify_emergency_flood():
    r = classify("water is flooding everywhere, a pipe burst under the sink")
    assert r["category"] == "plumbing"
    assert r["urgency"] == "emergency"
    assert r["confidence"] >= 0.9
    assert r["clarifying_questions"] == []
    assert r["estimated_duration_minutes"] == 90


def test_classify_emergency_sparking_outlet():
    r = classify("there's a sparking outlet in the kitchen")
    assert r["category"] == "electrical"
    assert r["urgency"] == "emergency"


def test_classify_scheduled_clog():
    r = classify("my toilet is clogged and won't flush")
    assert r["category"] == "plumbing"
    assert r["urgency"] == "scheduled"
    assert r["estimated_duration_minutes"] == 90


def test_classify_hvac_warm_air():
    r = classify("ac is blowing warm air and won't cool the house")
    assert r["category"] == "hvac"
    assert r["urgency"] == "scheduled"


def test_classify_same_day_keyword():
    r = classify("i need someone today asap, the sink is leaking")
    assert r["category"] == "plumbing"
    assert r["urgency"] == "same-day"


def test_classify_low_confidence_asks_questions():
    r = classify("it's broken")
    assert r["category"] is None
    assert r["confidence"] < 0.5
    assert len(r["clarifying_questions"]) >= 3


def test_classify_weak_match_asks_questions():
    r = classify("the thing in the kitchen stopped working last week maybe")
    assert r["confidence"] < 0.5
    assert r["clarifying_questions"]


# ---------- public analyze ----------

def test_analyze_returns_triage_id_and_shop(client, fake_db):
    make_tenant(fake_db, "tA", "shop-a")
    as_user("tA")

    r = analyze(client, "shop-a", "water is flooding everywhere, a pipe burst under the sink")
    assert r.status_code == 200
    body = r.json()
    assert body["triage_id"]
    assert body["category"] == "plumbing"
    assert body["urgency"] == "emergency"
    assert body["shop"] == {"slug": "shop-a", "name": "Shop shop-a"}
    assert body["covered"] is True
    # emergency gets the call-first message
    assert "emergency_message" in body
    # triage record persisted for the booking step
    rec = next(d for d in fake_db.triage_analyses.docs if d["id"] == body["triage_id"])
    assert rec["tenant_id"] == "tA"


def test_analyze_low_confidence_returns_questions(client, fake_db):
    make_tenant(fake_db, "tA", "shop-a")
    as_user("tA")

    body = analyze(client, "shop-a", "it's broken").json()
    assert body["confidence"] < 0.5
    assert body["clarifying_questions"]
    assert body["category"] is None


def test_analyze_unknown_slug_404(client, fake_db):
    as_user("tA")
    r = analyze(client, "no-such-shop", "toilet is clogged")
    assert r.status_code == 404


def test_analyze_blank_description_400(client, fake_db):
    make_tenant(fake_db, "tA", "shop-a")
    as_user("tA")
    r = analyze(client, "shop-a", "   ")
    assert r.status_code == 400


def test_analyze_respects_covered_categories(client, fake_db):
    make_tenant(fake_db, "tA", "shop-a")
    fake_db.tenants.docs[0]["triage_settings"] = {"covered_categories": ["plumbing"]}
    as_user("tA")

    assert analyze(client, "shop-a", "toilet is clogged").json()["covered"] is True
    body = analyze(client, "shop-a", "ac is blowing warm air").json()
    assert body["category"] == "hvac"
    assert body["covered"] is False


def test_analyze_disabled_widget_403(client, fake_db):
    make_tenant(fake_db, "tA", "shop-a")
    fake_db.tenants.docs[0]["triage_settings"] = {"enabled": False}
    as_user("tA")

    assert analyze(client, "shop-a", "toilet is clogged").status_code == 403


def test_public_token_resolves_tenant(client, fake_db):
    make_tenant(fake_db, "tA", "shop-a")
    fake_db.tenants.docs[0]["triage_public_token"] = "widgettok123"
    as_user("tA")

    r = client.get("/public/triage/whatever-slug/slots", params={"token": "widgettok123"})
    assert r.status_code == 200
    assert r.json()["shop"]["slug"] == "shop-a"


# ---------- public slots: tenant scoping ----------

def test_slots_never_leak_across_tenants(client, fake_db):
    """Shop A's busy 10:00 must not block shop B's 10:00."""
    make_tenant(fake_db, "tA", "shop-a")
    make_tenant(fake_db, "tB", "shop-b")
    busy_start, busy_end = tomorrow_at(10).isoformat(), tomorrow_at(11).isoformat()
    make_appointment(fake_db, "tA", busy_start, busy_end)
    as_user("tA")

    a = client.get("/public/triage/shop-a/slots",
                   params={"category": "plumbing", "urgency": "scheduled"}).json()
    b = client.get("/public/triage/shop-b/slots",
                   params={"category": "plumbing", "urgency": "scheduled"}).json()

    def overlaps(start_iso, end_iso, win_start, win_end):
        return not (end_iso <= win_start or start_iso >= win_end)

    assert a["slots"], "shop-a should still have other slots"
    for s in a["slots"]:
        assert not overlaps(s["start"], s["end"], busy_start, busy_end), \
            f"shop-a slot {s['start']} overlaps its own appointment"
    assert not any(s["start"] == busy_start for s in a["slots"])

    # shop B is untouched by shop A's appointment
    assert any(s["start"] == busy_start for s in b["slots"])


def test_slots_only_within_business_hours(client, fake_db):
    make_tenant(fake_db, "tA", "shop-a")
    as_user("tA")

    slots = client.get("/public/triage/shop-a/slots",
                       params={"category": "electrical"}).json()["slots"]
    assert slots
    for s in slots:
        start = datetime.fromisoformat(s["start"])
        assert 9 <= start.hour < 17
        # 60-min duration for electrical, on the hour or half hour
        assert start.minute in (0, 30)


def test_slots_public_shape_has_no_internal_data(client, fake_db):
    make_tenant(fake_db, "tA", "shop-a")
    as_user("tA")

    body = client.get("/public/triage/shop-a/slots").json()
    raw = str(body)
    assert "tA" not in raw  # no tenant ids
    for s in body["slots"]:
        assert set(s.keys()) == {"start", "end"}


def test_slots_unknown_slug_404(client, fake_db):
    as_user("tA")
    assert client.get("/public/triage/ghost-shop/slots").status_code == 404


# ---------- public booking ----------

def _book_flow(c, slug, description="toilet is clogged and won't flush"):
    triage_id = analyze(c, slug, description).json()["triage_id"]
    slots = c.get(f"/public/triage/{slug}/slots",
                  params={"category": "plumbing"}).json()["slots"]
    slot = slots[0]
    r = c.post(f"/public/triage/{slug}/book", json={
        "triage_id": triage_id,
        "slot_start": slot["start"],
        "slot_end": slot["end"],
        "customer_name": "Jane Doe",
        "customer_phone": "555-0100",
    })
    return r, slot


def test_book_creates_pending_request(client, fake_db):
    make_tenant(fake_db, "tA", "shop-a")
    as_user("tA")

    r, slot = _book_flow(client, "shop-a")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert body["request_id"]
    req = next(d for d in fake_db.triage_requests.docs if d["id"] == body["request_id"])
    assert req["tenant_id"] == "tA"
    assert req["customer_name"] == "Jane Doe"
    assert req["status"] == "pending"


def test_book_unknown_triage_id_404(client, fake_db):
    make_tenant(fake_db, "tA", "shop-a")
    as_user("tA")
    slot_start = tomorrow_at(10).isoformat()
    r = client.post("/public/triage/shop-a/book", json={
        "triage_id": "nope", "slot_start": slot_start,
        "slot_end": tomorrow_at(11, 30).isoformat(),
        "customer_name": "Jane", "customer_phone": "555-0100",
    })
    assert r.status_code == 404


def test_book_triage_id_from_other_tenant_404(client, fake_db):
    """A triage_id minted for shop B can't book on shop A's widget."""
    make_tenant(fake_db, "tA", "shop-a")
    make_tenant(fake_db, "tB", "shop-b")
    as_user("tA")

    triage_id = analyze(client, "shop-b", "toilet is clogged").json()["triage_id"]
    r = client.post("/public/triage/shop-a/book", json={
        "triage_id": triage_id,
        "slot_start": tomorrow_at(10).isoformat(),
        "slot_end": tomorrow_at(11, 30).isoformat(),
        "customer_name": "Jane", "customer_phone": "555-0100",
    })
    assert r.status_code == 404


def test_book_same_slot_twice_rejected(client, fake_db):
    make_tenant(fake_db, "tA", "shop-a")
    as_user("tA")

    r1, slot = _book_flow(client, "shop-a")
    assert r1.status_code == 200
    # confirm it so the appointment now occupies the slot
    req_id = r1.json()["request_id"]
    client.post(f"/triage/requests/{req_id}/confirm")

    # try to book the exact same slot again -> rejected
    triage_id = analyze(client, "shop-a", "sink is leaking in the kitchen").json()["triage_id"]
    r2 = client.post("/public/triage/shop-a/book", json={
        "triage_id": triage_id,
        "slot_start": slot["start"], "slot_end": slot["end"],
        "customer_name": "John Doe", "customer_phone": "555-0200",
    })
    assert r2.status_code == 409


def test_book_past_slot_400(client, fake_db):
    make_tenant(fake_db, "tA", "shop-a")
    as_user("tA")
    triage_id = analyze(client, "shop-a", "toilet is clogged").json()["triage_id"]
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    r = client.post("/public/triage/shop-a/book", json={
        "triage_id": triage_id, "slot_start": past,
        "slot_end": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "customer_name": "Jane", "customer_phone": "555-0100",
    })
    assert r.status_code == 400


# ---------- tenant dashboard ----------

def test_requests_scoped_to_my_tenant(client, fake_db):
    make_tenant(fake_db, "tA", "shop-a")
    make_tenant(fake_db, "tB", "shop-b")
    as_user("tA")
    _book_flow(client, "shop-a")
    as_user("tB")
    _book_flow(client, "shop-b")

    as_user("tA")
    body = client.get("/triage/requests").json()
    assert body["count"] == 1
    assert body["requests"][0]["tenant_id"] == "tA"


def test_confirm_creates_appointment(client, fake_db):
    make_tenant(fake_db, "tA", "shop-a")
    as_user("tA")
    r, slot = _book_flow(client, "shop-a")
    req_id = r.json()["request_id"]

    c = client.post(f"/triage/requests/{req_id}/confirm")
    assert c.status_code == 200
    assert c.json()["status"] == "confirmed"

    req = next(d for d in fake_db.triage_requests.docs if d["id"] == req_id)
    assert req["status"] == "confirmed"
    appt = next(d for d in fake_db.appointments.docs
                if d.get("id") == req.get("appointment_id"))
    assert appt["tenant_id"] == "tA"
    assert appt["customer_name"] == "Jane Doe"
    assert appt["start_at"] == slot["start"]
    assert appt["status"] == "scheduled"

    # confirming twice is an error
    assert client.post(f"/triage/requests/{req_id}/confirm").status_code == 400


def test_decline_marks_declined(client, fake_db):
    make_tenant(fake_db, "tA", "shop-a")
    as_user("tA")
    r, _ = _book_flow(client, "shop-a")
    req_id = r.json()["request_id"]

    d = client.post(f"/triage/requests/{req_id}/decline")
    assert d.json() == {"ok": True, "status": "declined"}
    req = next(x for x in fake_db.triage_requests.docs if x["id"] == req_id)
    assert req["status"] == "declined"
    # declined request blocks its slot list view only by filter
    assert client.get("/triage/requests", params={"status": "declined"}).json()["count"] == 1
    assert client.get("/triage/requests", params={"status": "pending"}).json()["count"] == 0


def test_settings_get_put(client, fake_db):
    make_tenant(fake_db, "tA", "shop-a")
    as_user("tA")

    defaults = client.get("/triage/settings").json()
    assert defaults["enabled"] is True
    assert defaults["lead_days"] == 7

    r = client.put("/triage/settings", json={
        "enabled": True,
        "covered_categories": ["plumbing", "hvac"],
        "slot_buffer_minutes": 20,
        "lead_days": 10,
        "emergency_phone": "555-0911",
        "emergency_message": "Call us now!",
    })
    assert r.status_code == 200
    saved = r.json()["settings"]
    assert saved["covered_categories"] == ["plumbing", "hvac"]
    assert saved["slot_buffer_minutes"] == 20

    again = client.get("/triage/settings").json()
    assert again["emergency_phone"] == "555-0911"
    assert again["lead_days"] == 10


# ---------- auth boundary ----------

def test_public_routes_need_no_auth(public_client, fake_db):
    make_tenant(fake_db, "tA", "shop-a")

    assert analyze(public_client, "shop-a", "toilet is clogged").status_code == 200
    assert public_client.get("/public/triage/shop-a/slots").status_code == 200


def test_tenant_routes_require_auth(public_client, fake_db):
    make_tenant(fake_db, "tA", "shop-a")

    assert public_client.get("/triage/settings").status_code == 401
    assert public_client.put("/triage/settings", json={}).status_code == 401
    assert public_client.get("/triage/requests").status_code == 401
    assert public_client.post("/triage/requests/x/confirm").status_code == 401
    assert public_client.post("/triage/requests/x/decline").status_code == 401
