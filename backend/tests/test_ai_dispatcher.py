"""Unit tests for the AI dispatcher (routers/ai_dispatcher.py + services/ai_dispatcher.py).

No database, network, or browser needed: a small FastAPI app with only the
AI-dispatcher router, a fake in-memory Mongo, and overridden auth deps.
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "ai-dispatcher-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "ai_dispatcher_test")

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.ai_dispatcher as aid_mod  # noqa: E402
from security import require_tenant_user, require_tenant_owner_or_admin  # noqa: E402
from services.ai_dispatcher import rank_for_job, suggest_for_day  # noqa: E402


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
                elif op == "$gte" and not (val >= opval):
                    return False
                elif op == "$lte" and not (val <= opval):
                    return False
                elif op not in ("$in", "$ne", "$gte", "$lte"):
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

    async def update_many(self, query, update):
        matched = 0
        for d in self.docs:
            if _match(d, query):
                matched += 1
                for k, v in update.get("$set", {}).items():
                    d[k] = v
        return SimpleNamespace(matched_count=matched, modified_count=matched)


class FakeDB:
    def __init__(self):
        self.appointments = FakeCollection()
        self.schedule_staff = FakeCollection()
        self.tech_locations = FakeCollection()
        self.dispatch_suggestions = FakeCollection()


# ---------- fixtures ----------

current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(aid_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(aid_mod.router)

    async def fake_user():
        return current_user

    async def fake_owner_admin():
        if current_user.get("role") not in {"owner", "admin"}:
            raise HTTPException(403, "Owner/admin role required")
        if not current_user.get("tenant_id"):
            raise HTTPException(403, "No tenant assigned")
        return current_user

    app.dependency_overrides[require_tenant_user] = fake_user
    app.dependency_overrides[require_tenant_owner_or_admin] = fake_owner_admin
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


DAY = "2026-10-20"


async def seed_tech(db, tenant_id, tid, name="Tech", skills=None, lat=None, lng=None,
                    weekly_hours=None):
    doc = {"id": tid, "tenant_id": tenant_id, "name": name, "role": "technician",
           "service_slugs": skills or []}
    if weekly_hours:
        doc["weekly_hours"] = weekly_hours
    await db.schedule_staff.insert_one(doc)
    if lat is not None and lng is not None:
        await db.tech_locations.insert_one(
            {"id": f"loc-{tid}", "tenant_id": tenant_id, "tech_id": tid,
             "lat": lat, "lng": lng, "updated_at": "2026-10-20T08:00:00+00:00"})


async def seed_job(db, tenant_id, jid, hour=9, staff_id="", service_id="ac-repair",
                   service_name="AC Repair", price=450, lat=None, lng=None,
                   status="scheduled"):
    doc = {"id": jid, "tenant_id": tenant_id, "customer_name": f"Cust {jid}",
           "customer_phone": "555-0100", "service_id": service_id,
           "service_name": service_name, "price": price,
           "staff_id": staff_id, "staff": "",
           "start_at": f"{DAY}T{hour:02d}:00:00+00:00",
           "end_at": f"{DAY}T{hour + 1:02d}:00:00+00:00",
           "status": status, "notes": ""}
    if lat is not None and lng is not None:
        doc["service_lat"] = lat
        doc["service_lng"] = lng
    await db.appointments.insert_one(doc)


def snapshot(db):
    return sorted((d["id"], d.get("staff_id", "")) for d in db.appointments.docs)


# ---------- scoring engine: determinism ----------

def test_scoring_is_deterministic():
    job = {"id": "j1", "service_id": "ac-repair", "service_name": "AC Repair",
           "start_at": f"{DAY}T09:00:00+00:00", "end_at": f"{DAY}T10:00:00+00:00",
           "price": 450, "service_lat": 30.0, "service_lng": -97.7}
    techs = [
        {"id": "t-a", "name": "Amy", "service_slugs": ["ac-repair"]},
        {"id": "t-b", "name": "Bo", "service_slugs": ["plumbing"]},
    ]
    locations = {"t-a": {"lat": 30.01, "lng": -97.71}, "t-b": {"lat": 31.0, "lng": -98.5}}
    jobs_by_tech = {}

    first = rank_for_job(job, techs, locations, jobs_by_tech)
    second = rank_for_job(job, techs, locations, jobs_by_tech)
    assert first == second
    assert [r["tech_id"] for r in first] == ["t-a", "t-b"]  # best first

    day_first = suggest_for_day([job], techs, locations, jobs_by_tech)
    day_second = suggest_for_day([job], techs, locations, jobs_by_tech)
    assert [c["score"] for c in day_first[0]["candidates"]] == \
           [c["score"] for c in day_second[0]["candidates"]]


def test_skill_mismatch_downranks():
    job = {"id": "j1", "service_id": "ac-repair", "service_name": "AC Repair",
           "start_at": f"{DAY}T09:00:00+00:00", "end_at": f"{DAY}T10:00:00+00:00"}
    match = {"id": "t-a", "name": "Amy", "service_slugs": ["ac-repair"]}
    mismatch = {"id": "t-b", "name": "Bo", "service_slugs": ["plumbing"]}
    ranked = rank_for_job(job, [match, mismatch], {}, {})
    by_id = {r["tech_id"]: r for r in ranked}
    assert by_id["t-a"]["score"] > by_id["t-b"]["score"]
    assert any("mismatch" in r.lower() for r in by_id["t-b"]["reasons"])
    assert any("match" in r.lower() for r in by_id["t-a"]["reasons"])


def test_conflict_and_overload_penalized():
    job = {"id": "j1", "service_id": "ac-repair",
           "start_at": f"{DAY}T09:00:00+00:00", "end_at": f"{DAY}T10:00:00+00:00"}
    busy = {"id": "t-b", "name": "Bo", "service_slugs": ["ac-repair"]}
    free = {"id": "t-a", "name": "Amy", "service_slugs": ["ac-repair"]}
    # Bo has an overlapping job and a full day already.
    clash = {"id": "jX", "start_at": f"{DAY}T09:30:00+00:00",
             "end_at": f"{DAY}T10:30:00+00:00"}
    long_jobs = [{"id": f"j{i}", "start_at": f"{DAY}T{i:02d}:00:00+00:00",
                  "end_at": f"{DAY}T{i + 1:02d}:00:00+00:00"} for i in range(11, 17)]
    jobs_by_tech = {"t-b": [clash] + long_jobs}
    ranked = rank_for_job(job, [busy, free], {}, jobs_by_tech)
    by_id = {r["tech_id"]: r for r in ranked}
    assert by_id["t-a"]["score"] > by_id["t-b"]["score"]
    assert any("Conflict" in r for r in by_id["t-b"]["reasons"])


# ---------- endpoints ----------

def test_suggestions_endpoint_ranks_and_persists(client, fake_db):
    as_user("t1")
    run(seed_tech(fake_db, "t1", "tech1", "Amy", skills=["ac-repair"], lat=30.01, lng=-97.71))
    run(seed_tech(fake_db, "t1", "tech2", "Bo", skills=["plumbing"], lat=30.02, lng=-97.72))
    run(seed_job(fake_db, "t1", "j1", hour=9, lat=30.0, lng=-97.7))

    r = client.get("/tenants/ai-dispatcher/suggestions", params={"date": DAY})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["date"] == DAY
    assert body["unassigned_count"] == 1
    assert len(body["suggestions"]) == 1
    sug = body["suggestions"][0]
    assert sug["job_id"] == "j1"
    assert sug["tech_id"] == "tech1"  # skill-matched tech wins
    assert 0 <= sug["score"] <= 100
    assert len(sug["reasons"]) >= 5
    assert sug["status"] == "pending"

    # Persisted so accept-by-id works.
    stored = run(fake_db.dispatch_suggestions.find_one({"id": sug["id"]}))
    assert stored and stored["tenant_id"] == "t1" and stored["status"] == "pending"


def test_suggestions_regenerates_not_duplicates(client, fake_db):
    as_user("t1")
    run(seed_tech(fake_db, "t1", "tech1", skills=["ac-repair"]))
    run(seed_job(fake_db, "t1", "j1", hour=9))

    client.get("/tenants/ai-dispatcher/suggestions", params={"date": DAY})
    client.get("/tenants/ai-dispatcher/suggestions", params={"date": DAY})
    pending = [d for d in fake_db.dispatch_suggestions.docs if d["status"] == "pending"]
    assert len(pending) == 1  # second call expired the first set


def test_tenant_isolation(client, fake_db):
    as_user("t1")
    run(seed_tech(fake_db, "t1", "tech1", skills=["ac-repair"]))
    run(seed_tech(fake_db, "t2", "techX", "Xavier", skills=["ac-repair"]))
    run(seed_job(fake_db, "t1", "jA", hour=9))
    run(seed_job(fake_db, "t2", "jB", hour=9))

    body = client.get("/tenants/ai-dispatcher/suggestions", params={"date": DAY}).json()
    assert [s["job_id"] for s in body["suggestions"]] == ["jA"]
    assert all(s["tech_id"] == "tech1" for s in body["suggestions"])

    # Mint t2's suggestion set, then confirm t1 cannot accept it.
    as_user("t2")
    body2 = client.get("/tenants/ai-dispatcher/suggestions", params={"date": DAY}).json()
    sug_b = body2["suggestions"][0]
    assert sug_b["job_id"] == "jB"

    as_user("t1")
    r = client.post(f"/tenants/ai-dispatcher/suggestions/{sug_b['id']}/accept")
    assert r.status_code == 404


def test_accept_applies_assignment_via_dispatch_path(client, fake_db):
    as_user("t1")
    run(seed_tech(fake_db, "t1", "tech1", "Amy", skills=["ac-repair"]))
    run(seed_tech(fake_db, "t1", "tech2", "Bo", skills=["ac-repair"]))
    run(seed_job(fake_db, "t1", "j1", hour=9))

    sug = client.get("/tenants/ai-dispatcher/suggestions", params={"date": DAY}).json()["suggestions"][0]
    r = client.post(f"/tenants/ai-dispatcher/suggestions/{sug['id']}/accept")
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    assert job["staff_id"] == sug["tech_id"]
    assert job["staff"] == "Amy" or job["staff"] == "Bo"
    assert job["start_at"] == f"{DAY}T09:00:00+00:00"  # time preserved

    stored = run(fake_db.dispatch_suggestions.find_one({"id": sug["id"]}))
    assert stored["status"] == "accepted"

    # Accepting twice is rejected; the suggestion is no longer pending.
    r2 = client.post(f"/tenants/ai-dispatcher/suggestions/{sug['id']}/accept")
    assert r2.status_code == 400


def test_writes_require_owner_or_admin(client, fake_db):
    as_user("t1", role="tech")
    run(seed_tech(fake_db, "t1", "tech1", skills=["ac-repair"]))
    run(seed_job(fake_db, "t1", "j1", hour=9))
    sug = client.get("/tenants/ai-dispatcher/suggestions", params={"date": DAY}).json()["suggestions"][0]

    r = client.post(f"/tenants/ai-dispatcher/suggestions/{sug['id']}/accept")
    assert r.status_code == 403
    r = client.post("/tenants/ai-dispatcher/auto-assign", json={"date": DAY, "dry_run": False})
    assert r.status_code == 403


def test_dry_run_makes_no_writes(client, fake_db):
    as_user("t1")
    run(seed_tech(fake_db, "t1", "tech1", "Amy", skills=["ac-repair"], lat=30.01, lng=-97.71))
    run(seed_tech(fake_db, "t1", "tech2", "Bo", skills=["ac-repair"], lat=30.02, lng=-97.72))
    run(seed_job(fake_db, "t1", "j1", hour=9, price=600, lat=30.0, lng=-97.7))
    run(seed_job(fake_db, "t1", "j2", hour=11, price=600, lat=30.0, lng=-97.7))
    before = snapshot(fake_db)

    r = client.post("/tenants/ai-dispatcher/auto-assign", json={"date": DAY, "dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["writes"] == 0
    assert len(body["plan"]) == 2
    assert all("reasons" in p and "score" in p for p in body["plan"])

    # Zero writes: appointments untouched, no suggestions accepted or persisted.
    assert snapshot(fake_db) == before
    assert fake_db.dispatch_suggestions.docs == []


def test_auto_assign_apply_writes_all(client, fake_db):
    as_user("t1")
    run(seed_tech(fake_db, "t1", "tech1", "Amy", skills=["ac-repair"], lat=30.01, lng=-97.71))
    run(seed_tech(fake_db, "t1", "tech2", "Bo", skills=["ac-repair"], lat=30.02, lng=-97.72))
    run(seed_job(fake_db, "t1", "j1", hour=9, price=600, lat=30.0, lng=-97.7))
    run(seed_job(fake_db, "t1", "j2", hour=11, price=600, lat=30.0, lng=-97.7))
    run(seed_job(fake_db, "t1", "j3", hour=14, price=600, lat=30.0, lng=-97.7))

    r = client.post("/tenants/ai-dispatcher/auto-assign", json={"date": DAY, "dry_run": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is False
    assert body["writes"] == 3
    assert body["failed"] == []

    jobs = {d["id"]: d for d in fake_db.appointments.docs}
    assert all(jobs[j]["staff_id"] for j in ("j1", "j2", "j3"))
    assert jobs["j1"]["start_at"] == f"{DAY}T09:00:00+00:00"  # times preserved

    accepted = [d for d in fake_db.dispatch_suggestions.docs if d["status"] == "accepted"]
    assert len(accepted) == 3


def test_auto_assign_skips_low_score_jobs(client, fake_db):
    as_user("t1")
    # Only tech has the wrong skill AND the job is outside her hours:
    # the top score stays well below the auto-assign floor.
    run(seed_tech(fake_db, "t1", "tech1", "Amy", skills=["plumbing"],
                 weekly_hours={"tue": "13:00-17:00"}))
    run(seed_job(fake_db, "t1", "j1", hour=9, service_id="hvac-install"))

    r = client.post("/tenants/ai-dispatcher/auto-assign", json={"date": DAY, "dry_run": False})
    body = r.json()
    assert body["writes"] == 0
    job = run(fake_db.appointments.find_one({"id": "j1"}))
    assert job["staff_id"] == ""  # still unassigned


def test_bad_date_rejected(client, fake_db):
    as_user("t1")
    assert client.get("/tenants/ai-dispatcher/suggestions", params={"date": "nope"}).status_code == 400
    r = client.post("/tenants/ai-dispatcher/auto-assign", json={"date": "nope", "dry_run": True})
    assert r.status_code == 400
