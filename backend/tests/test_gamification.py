"""Tests for routers/gamification.py (tech gamification).

Uses a fake in-memory mongo (same pattern as test_trade_packs.py): no live
database. Covers event recording, idempotency, badge auto-awards, rules
config, leaderboards, tenant scoping, and streaks.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "gamification-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "gamification_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.gamification as gam_mod  # noqa: E402
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
        return FakeCursor([d for d in self.docs if all(d.get(k) == v for k, v in query.items())])

    async def find_one(self, query, proj=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("id"))

    async def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                d.update(update.get("$set", {}))
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            doc = dict(query)
            doc.update(update.get("$set", {}))
            self.docs.append(doc)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=True)
        return SimpleNamespace(matched_count=0, modified_count=0)


class FakeDB:
    def __init__(self):
        self.gamification_events = FakeCollection()
        self.gamification_badges = FakeCollection()
        self.gamification_rules = FakeCollection()


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db):
    monkeypatch.setattr(gam_mod, "get_db", lambda: fake_db)
    app = FastAPI()
    app.include_router(gam_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id="t1", role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def iso_days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


def post_event(client, tech_id="tech-1", event_type="on_time", ref_id=None, **kw):
    payload = {"tech_id": tech_id, "tech_name": kw.get("tech_name", "Sam"),
               "event_type": event_type, "ref_id": ref_id or f"ref-{tech_id}-{event_type}"}
    payload.update({k: v for k, v in kw.items() if k != "tech_name"})
    return client.post("/api/gamification/tenants/events", json=payload)


# ---------- event recording ----------

def test_record_event_scores_default_points(client, fake_db):
    as_user()
    r = post_event(client, event_type="on_time", ref_id="job-1")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["duplicate"] is False
    assert body["points"] == 10  # DEFAULT_POINT_VALUES["on_time"]
    assert len(fake_db.gamification_events.docs) == 1


def test_event_idempotent_same_ref_cannot_double_score(client, fake_db):
    as_user()
    r1 = post_event(client, event_type="5_star_review", ref_id="rev-9")
    r2 = post_event(client, event_type="5_star_review", ref_id="rev-9")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r2.json()["duplicate"] is True
    assert len(fake_db.gamification_events.docs) == 1


def test_same_ref_different_event_type_scores_separately(client, fake_db):
    as_user()
    post_event(client, event_type="on_time", ref_id="job-2")
    r = post_event(client, event_type="upsell", ref_id="job-2")
    assert r.status_code == 200
    assert r.json()["duplicate"] is False
    assert len(fake_db.gamification_events.docs) == 2


def test_invalid_event_type_rejected(client, fake_db):
    as_user()
    r = post_event(client, event_type="punished", ref_id="x")
    assert r.status_code == 400
    assert len(fake_db.gamification_events.docs) == 0


# ---------- leaderboards ----------

def test_leaderboard_ranks_techs_by_points(client):
    as_user()
    post_event(client, tech_id="tech-a", tech_name="Abe", event_type="on_time", ref_id="a1")
    post_event(client, tech_id="tech-b", tech_name="Bea", event_type="5_star_review", ref_id="b1")
    post_event(client, tech_id="tech-b", tech_name="Bea", event_type="upsell", ref_id="b2")
    r = client.get("/api/gamification/tenants/leaderboard", params={"period": "all"})
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert [e["tech_id"] for e in entries] == ["tech-b", "tech-a"]
    assert entries[0]["rank"] == 1
    assert entries[0]["points"] == 25 + 20
    assert entries[1]["points"] == 10


def test_leaderboard_period_excludes_old_events(client, fake_db):
    as_user()
    fake_db.gamification_events.docs.append({
        "id": "old", "tenant_id": "t1", "tech_id": "tech-old", "tech_name": "Old",
        "event_type": "on_time", "ref_id": "old-1", "points": 10,
        "created_at": iso_days_ago(60),
    })
    post_event(client, tech_id="tech-new", tech_name="New", event_type="on_time", ref_id="new-1")
    week = client.get("/api/gamification/tenants/leaderboard", params={"period": "week"}).json()["entries"]
    assert [e["tech_id"] for e in week] == ["tech-new"]
    all_time = client.get("/api/gamification/tenants/leaderboard", params={"period": "all"}).json()["entries"]
    assert {e["tech_id"] for e in all_time} == {"tech-old", "tech-new"}


def test_leaderboard_is_tenant_scoped(client):
    as_user("t1")
    post_event(client, tech_id="tech-1", tech_name="T1", event_type="on_time", ref_id="r1")
    as_user("t2")
    r = client.get("/api/gamification/tenants/leaderboard", params={"period": "all"})
    assert r.json()["entries"] == []
    post_event(client, tech_id="tech-2", tech_name="T2", event_type="on_time", ref_id="r2")
    entries = client.get("/api/gamification/tenants/leaderboard", params={"period": "all"}).json()["entries"]
    assert [e["tech_id"] for e in entries] == ["tech-2"]


def test_recent_events_feed_returns_newest_first(client):
    as_user()
    post_event(client, tech_id="tech-a", tech_name="Abe", event_type="on_time", ref_id="e1")
    post_event(client, tech_id="tech-b", tech_name="Bea", event_type="upsell", ref_id="e2")
    r = client.get("/api/gamification/tenants/events")
    assert r.status_code == 200
    events = r.json()["events"]
    assert len(events) == 2
    assert events[0]["tech_id"] == "tech-b"  # newest first
    assert events[0]["label"] == "Upsell"
    assert all("points" in e for e in events)


# ---------- badges ----------

def test_badge_catalog_lists_five_seeded_defaults(client):
    as_user()
    r = client.get("/api/gamification/tenants/badges")
    assert r.status_code == 200
    badges = r.json()["badges"]
    assert [b["id"] for b in badges] == [
        "on_time_pro", "five_star_machine", "closer", "shutterbug", "clean_sheet"]
    assert all(b["earned_count"] == 0 for b in badges)


def test_badge_awarded_when_threshold_crossed(client, fake_db):
    as_user()
    awarded = None
    for i in range(10):
        r = post_event(client, event_type="on_time", ref_id=f"job-{i}")
        awarded = r.json()["badges_awarded"]
    assert awarded and awarded[0]["badge_id"] == "on_time_pro"
    assert len(fake_db.gamification_badges.docs) == 1
    # earned_count now reflects the award
    catalog = client.get("/api/gamification/tenants/badges").json()["badges"]
    pro = next(b for b in catalog if b["id"] == "on_time_pro")
    assert pro["earned_count"] == 1


def test_badge_not_awarded_twice(client, fake_db):
    as_user()
    for i in range(11):
        post_event(client, event_type="on_time", ref_id=f"job-{i}")
    awards = [d for d in fake_db.gamification_badges.docs if d["badge_id"] == "on_time_pro"]
    assert len(awards) == 1


def test_tech_profile_returns_badges_streaks_and_recent(client, fake_db):
    as_user()
    # three consecutive days of scoring -> current streak 3
    for n in (2, 1, 0):
        fake_db.gamification_events.docs.append({
            "id": f"e{n}", "tenant_id": "t1", "tech_id": "tech-1", "tech_name": "Sam",
            "event_type": "on_time", "ref_id": f"s-{n}", "points": 10,
            "created_at": iso_days_ago(n),
        })
    fake_db.gamification_badges.docs.append({
        "id": "b1", "tenant_id": "t1", "tech_id": "tech-1", "badge_id": "on_time_pro",
        "name": "On-Time Pro", "icon": "⏱️", "description": "10 on-time arrivals",
        "awarded_at": iso_days_ago(0),
    })
    r = client.get("/api/gamification/tenants/techs/tech-1")
    assert r.status_code == 200
    body = r.json()
    assert body["tech_name"] == "Sam"
    assert body["points"] == 30
    assert body["points_week"] == 30
    assert body["events_total"] == 3
    assert body["by_type"]["on_time"] == 3
    assert [b["badge_id"] for b in body["badges"]] == ["on_time_pro"]
    assert body["streaks"]["current_streak_days"] == 3
    assert body["streaks"]["longest_streak_days"] == 3
    assert len(body["recent_events"]) == 3


# ---------- rules ----------

def test_rules_get_returns_defaults(client):
    as_user()
    r = client.get("/api/gamification/tenants/rules")
    assert r.status_code == 200
    assert r.json()["point_values"]["on_time"] == 10
    assert r.json()["point_values"]["5_star_review"] == 25


def test_owner_can_configure_rules_and_new_events_use_them(client, fake_db):
    as_user("t1", "owner")
    r = client.post("/api/gamification/tenants/rules",
                    json={"point_values": {"on_time": 5, "upsell": 50}})
    assert r.status_code == 200
    assert r.json()["point_values"]["on_time"] == 5
    assert r.json()["point_values"]["5_star_review"] == 25  # untouched default
    ev = post_event(client, event_type="on_time", ref_id="ruled")
    assert ev.json()["points"] == 5
    assert len(fake_db.gamification_rules.docs) == 1


def test_tech_role_cannot_configure_rules(client):
    as_user("t1", "tech")
    r = client.post("/api/gamification/tenants/rules", json={"point_values": {"on_time": 1}})
    assert r.status_code == 403


def test_rules_reject_unknown_event_type_and_negative_values(client):
    as_user("t1", "owner")
    r = client.post("/api/gamification/tenants/rules", json={"point_values": {"nope": 5}})
    assert r.status_code == 400
    r = client.post("/api/gamification/tenants/rules", json={"point_values": {"on_time": -1}})
    assert r.status_code == 400
