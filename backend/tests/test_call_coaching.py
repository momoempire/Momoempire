"""Unit tests for the AI call-coaching feature (routers/call_coaching.py).

Zero network, zero database: a small FastAPI app with only the call-coaching
router, a fake in-memory Mongo, and an overridden auth dependency.

Coverage:
- scoring determinism: same transcript -> identical scores, twice
- rubric bounds: every score in [0, 100] across a spread of transcripts
- missed-booking detection (deferral vs confirmed booking)
- tenant isolation: no cross-tenant reads
- weekly aggregation math: totals, averages, missed counts, per-CSR output
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "callcoach-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "callcoach_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.call_coaching as cc_mod  # noqa: E402
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
        self.call_scores = FakeCollection()


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
    monkeypatch.setattr(cc_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(cc_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


GOOD_TRANSCRIPT = (
    "Thanks for calling Apex Heating! How can I help? "
    "I'm sorry to hear your AC stopped working, that sounds frustrating. "
    "When did it stop cooling? Is it making any noise? "
    "I can get someone out today — we have a tech in the area this afternoon. "
    "Shall I book you for today between 2 and 4?"
)

BOOKED_TRANSCRIPT = (
    "Thanks for calling Apex Heating! What seems to be the problem? "
    "Got it — leaking water heater. I can have someone there today. "
    "You're all set, we'll be there this afternoon. Thanks!"
)

MISSED_TRANSCRIPT = (
    "Hello? Yeah my furnace is making a weird noise. "
    "How much does that cost? OK well, let me think about it, "
    "I'll call you back later."
)

SHORT_TRANSCRIPT = "Hi. My sink leaks. Bye."


# ---------- scorer unit behavior ----------

def test_scoring_is_deterministic(client, fake_db):
    """Same transcript scored twice -> byte-identical scores."""
    as_user("t1")
    b1 = client.post("/call-coaching/calls/c1/score",
                     json={"transcript": GOOD_TRANSCRIPT, "csr_name": "Sam"}).json()
    b2 = client.post("/call-coaching/calls/c2/score",
                     json={"transcript": GOOD_TRANSCRIPT, "csr_name": "Sam"}).json()
    assert b1["rubric"] == b2["rubric"]
    assert b1["overall"] == b2["overall"]
    assert b1["booked"] == b2["booked"]
    assert b1["missed_booking"] == b2["missed_booking"]
    assert b1["missed_booking_reason"] == b2["missed_booking_reason"]


def test_rubric_bounds_zero_to_100(client, fake_db):
    """Every rubric field and the overall score must stay in [0, 100]
    across a spread of transcripts."""
    as_user("t1")
    transcripts = [
        GOOD_TRANSCRIPT,
        BOOKED_TRANSCRIPT,
        MISSED_TRANSCRIPT,
        SHORT_TRANSCRIPT,
        "x",  # minimal transcript
        " ".join(["hello"] * 200),  # long repetitive transcript
    ]
    for i, t in enumerate(transcripts):
        body = client.post(f"/call-coaching/calls/b{i}/score",
                           json={"transcript": t}).json()
        assert 0 <= body["overall"] <= 100, t
        for field, score in body["rubric"].items():
            assert field in cc_mod.RUBRIC_FIELDS
            assert 0 <= score <= 100, (t, field, score)


def test_missed_booking_detection(client, fake_db):
    as_user("t1")
    body = client.post("/call-coaching/calls/m1/score",
                       json={"transcript": MISSED_TRANSCRIPT}).json()
    assert body["missed_booking"] is True
    assert body["booked"] is False
    assert body["missed_booking_reason"], "missed call needs a reason"

    body = client.post("/call-coaching/calls/m2/score",
                       json={"transcript": BOOKED_TRANSCRIPT}).json()
    assert body["missed_booking"] is False
    assert body["booked"] is True
    assert body["missed_booking_reason"] == ""


def test_score_endpoint_validation_and_idempotency(client, fake_db):
    as_user("t1")
    # empty transcript rejected by min_length=1
    r = client.post("/call-coaching/calls/x/score", json={"transcript": ""})
    assert r.status_code == 422

    # re-scoring the same call_id replaces, not duplicates
    client.post("/call-coaching/calls/dup/score", json={"transcript": SHORT_TRANSCRIPT})
    client.post("/call-coaching/calls/dup/score", json={"transcript": GOOD_TRANSCRIPT})
    body = client.get("/call-coaching/calls").json()
    assert body["count"] == 1
    assert body["calls"][0]["overall"] == client.get("/call-coaching/calls/dup").json()["overall"]


# ---------- tenant isolation ----------

def test_tenant_isolation_no_cross_tenant_reads(client, fake_db):
    as_user("tenant-a")
    client.post("/call-coaching/calls/a1/score",
                json={"transcript": GOOD_TRANSCRIPT, "csr_name": "Sam"})

    as_user("tenant-b")
    assert client.get("/call-coaching/calls").json()["count"] == 0
    assert client.get("/call-coaching/calls/a1").status_code == 404
    assert client.get("/call-coaching/insights/weekly").json()["total_calls"] == 0

    as_user("tenant-a")
    assert client.get("/call-coaching/calls").json()["count"] == 1
    assert client.get("/call-coaching/calls/a1").status_code == 200


def test_csr_and_date_filters(client, fake_db):
    as_user("t1")
    client.post("/call-coaching/calls/f1/score",
                json={"transcript": GOOD_TRANSCRIPT, "csr_name": "Sam",
                      "occurred_at": "2026-10-05T10:00:00Z"})
    client.post("/call-coaching/calls/f2/score",
                json={"transcript": SHORT_TRANSCRIPT, "csr_name": "Jo",
                      "occurred_at": "2026-10-12T10:00:00Z"})

    assert client.get("/call-coaching/calls", params={"csr": "Sam"}).json()["count"] == 1
    body = client.get("/call-coaching/calls",
                      params={"date_from": "2026-10-10T00:00:00Z"}).json()
    assert body["count"] == 1
    assert body["calls"][0]["csr_name"] == "Jo"
    body = client.get("/call-coaching/calls",
                      params={"date_to": "2026-10-06T00:00:00Z"}).json()
    assert body["count"] == 1
    assert body["calls"][0]["csr_name"] == "Sam"


# ---------- weekly aggregation math ----------

def _score(fake, tenant, call_id, transcript, csr, occurred_at):
    fake.call_scores.docs.append({
        "id": f"id-{call_id}", "tenant_id": tenant, "call_id": call_id,
        "csr_name": csr, "transcript": transcript,
        "transcript_summary": transcript[:50],
        "rubric": cc_mod._score_transcript(transcript)[0].model_dump(),
        "overall": cc_mod._score_transcript(transcript)[1],
        "booked": False, "missed_booking": False, "missed_booking_reason": "",
        "occurred_at": occurred_at, "scored_at": occurred_at,
    })
    return fake.call_scores.docs[-1]


def test_weekly_insights_aggregation_math(client, fake_db):
    """Weekly digest math: totals, averages, missed counts, per-CSR weakest
    rubric — all computed over the last 7 days only."""
    now_iso = "2026-10-15T12:00:00Z"
    recent = "2026-10-13T12:00:00Z"
    old = "2026-09-01T12:00:00Z"  # outside the 7-day window

    # Sam: 2 recent calls (1 missed, 1 good), 1 old missed (excluded)
    sam_missed = _score(fake_db, "t1", "s1", MISSED_TRANSCRIPT, "Sam", recent)
    sam_missed["missed_booking"] = True
    sam_missed["missed_booking_reason"] = "caller ended without booking — no same-day offer was made"
    sam_good = _score(fake_db, "t1", "s2", GOOD_TRANSCRIPT, "Sam", recent)
    sam_good["booked"] = True
    old_call = _score(fake_db, "t1", "s0", MISSED_TRANSCRIPT, "Sam", old)
    old_call["missed_booking"] = True

    # Jo: 1 recent good call
    jo = _score(fake_db, "t1", "j1", GOOD_TRANSCRIPT, "Jo", recent)
    jo["booked"] = True

    # other tenant: must not leak into the digest
    other = _score(fake_db, "t2", "o1", MISSED_TRANSCRIPT, "Zed", recent)
    other["missed_booking"] = True

    as_user("t1")
    body = client.get("/call-coaching/insights/weekly").json()

    assert body["period_days"] == 7
    assert body["total_calls"] == 3  # old call excluded
    assert body["missed_bookings"] == 1
    assert body["booked"] == 2

    expected_avg = round((sam_missed["overall"] + sam_good["overall"] + jo["overall"]) / 3, 1)
    assert body["avg_overall"] == expected_avg

    assert "1 booking" in body["headline"] and "same-day" in body["headline"]

    opps = body["opportunities"]
    assert len(opps) == 2
    sam_opp = next(o for o in opps if o["csr_name"] == "Sam")
    jo_opp = next(o for o in opps if o["csr_name"] == "Jo")
    assert sam_opp["calls"] == 2
    assert sam_opp["missed_bookings"] == 1
    assert sam_opp["avg_score"] == round((sam_missed["overall"] + sam_good["overall"]) / 2, 1)
    assert sam_opp["weakest_rubric"] in cc_mod.RUBRIC_FIELDS
    assert sam_opp["coaching_tip"], "each opportunity needs a coaching tip"
    assert jo_opp["missed_bookings"] == 0
    # sorted: most missed bookings first
    assert opps[0]["csr_name"] == "Sam"


def test_weekly_insights_empty_week(client, fake_db):
    as_user("t1")
    body = client.get("/call-coaching/insights/weekly").json()
    assert body["total_calls"] == 0
    assert body["missed_bookings"] == 0
    assert body["avg_overall"] == 0.0
    assert body["opportunities"] == []
    assert "No scored calls" in body["headline"]


def test_weekly_insights_no_misses_headline(client, fake_db):
    as_user("t1")
    doc = _score(fake_db, "t1", "g1", GOOD_TRANSCRIPT, "Sam", "2026-10-13T12:00:00Z")
    doc["booked"] = True
    body = client.get("/call-coaching/insights/weekly").json()
    assert body["missed_bookings"] == 0
    assert "No bookings lost" in body["headline"]
