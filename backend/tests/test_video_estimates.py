"""Tests for routers/video_estimates.py — async video estimate requests.

Router tests use a fake mongo (same pattern as test_trade_packs.py): no
live MongoDB required. Video bytes go through the real storage stub into
a tmp dir.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("JWT_SECRET", "video-estimates-test")
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:9")
os.environ.setdefault("DB_NAME", "video_estimates_test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import routers.video_estimates as ve_mod  # noqa: E402
from security import require_tenant_user  # noqa: E402


# ---------- fake mongo (copied shape from test_trade_packs.py) ----------

class FakeCursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, key, direction=1):
        self.docs = sorted(self.docs, key=lambda d: str(d.get(key, "")),
                           reverse=(direction < 0))
        return self

    async def to_list(self, n):
        return [dict(d) for d in self.docs[:n]]


class FakeCollection:
    def __init__(self):
        self.docs = []

    def find(self, query, proj=None):
        return FakeCursor([d for d in self.docs
                           if all(d.get(k) == v for k, v in query.items())])

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
            return SimpleNamespace(matched_count=0, modified_count=0,
                                   upserted_id=True)
        return SimpleNamespace(matched_count=0, modified_count=0)


class FakeDB:
    def __init__(self):
        self.video_estimate_requests = FakeCollection()
        self.pricebook_items = FakeCollection()


current_user = {}


@pytest.fixture
def fake_db():
    return FakeDB()


@pytest.fixture
def client(monkeypatch, fake_db, tmp_path):
    monkeypatch.setattr(ve_mod, "get_db", lambda: fake_db)
    # point the storage stub at a tmp dir
    import services.video_storage as vs
    monkeypatch.setattr(vs, "STORAGE_DIR", tmp_path / "videos")
    monkeypatch.setattr(ve_mod, "save_video_bytes", vs.save_video_bytes)
    monkeypatch.setattr(ve_mod, "load_video_bytes", vs.load_video_bytes)
    app = FastAPI()
    app.include_router(ve_mod.router)
    app.dependency_overrides[require_tenant_user] = lambda: current_user
    return TestClient(app)


def as_user(tenant_id, role="owner"):
    current_user.clear()
    current_user.update({"id": f"u-{tenant_id}", "tenant_id": tenant_id, "role": role})


def create(client, **over):
    payload = {"customer_name": "Jane Homeowner", "job_type": "HVAC repair",
               "notes": "Noisy unit"}
    payload.update(over)
    return client.post("/api/video-estimates/tenants/requests", json=payload)


VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"v" * 2048  # fake but mp4-ish


def upload(client, token, content_type="video/mp4", data=VIDEO_BYTES,
           filename="walkthrough.mp4"):
    return client.post(
        f"/api/video-estimates/upload/{token}",
        files={"file": (filename, data, content_type)},
    )


# ---------- create ----------

def test_create_request_returns_shareable_link(client):
    as_user("t1")
    r = create(client)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "requested"
    assert body["upload_token"]
    assert body["upload_link"] == f"/video-upload/{body['upload_token']}"
    assert body["customer_name"] == "Jane Homeowner"
    assert body["quote_id"] is None


def test_create_request_requires_customer_name(client):
    as_user("t1")
    r = create(client, customer_name="")
    assert r.status_code == 422


# ---------- list ----------

def test_list_requests_shows_pipeline_statuses(client):
    as_user("t1")
    id_a = create(client, customer_name="A").json()["id"]
    b = create(client, customer_name="B").json()
    id_b, token_b = b["id"], b["upload_token"]
    upload(client, token_b)
    # manually flip B to in-review via review endpoint
    client.post(f"/api/video-estimates/tenants/requests/{id_b}/review",
                json={"timestamped_notes": [{"timestamp_sec": 5, "note": "rust"}]})
    r = client.get("/api/video-estimates/tenants/requests")
    assert r.status_code == 200
    by_id = {x["id"]: x["status"] for x in r.json()["requests"]}
    assert by_id[id_a] == "requested"
    assert by_id[id_b] == "in-review"


def test_list_requests_status_filter(client):
    as_user("t1")
    create(client)
    token = create(client).json()["upload_token"]
    upload(client, token)
    r = client.get("/api/video-estimates/tenants/requests?status=uploaded")
    assert r.status_code == 200
    assert len(r.json()["requests"]) == 1
    assert r.json()["requests"][0]["video"]["size_bytes"] == len(VIDEO_BYTES)
    r = client.get("/api/video-estimates/tenants/requests?status=bogus")
    assert r.status_code == 400


def test_list_requests_is_tenant_scoped(client):
    as_user("t1")
    create(client)
    as_user("t2")
    assert client.get("/api/video-estimates/tenants/requests").json()["requests"] == []
    create(client, customer_name="T2 person")
    assert len(client.get("/api/video-estimates/tenants/requests").json()["requests"]) == 1


# ---------- upload (public token flow, no auth) ----------

def test_upload_video_flips_status_and_stores_metadata(client):
    as_user("t1")
    token = create(client).json()["upload_token"]
    as_user("t1")  # upload needs no auth; ensure no dependency on the user
    r = upload(client, token)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "uploaded"
    assert body["video"]["content_type"] == "video/mp4"
    assert body["video"]["size_bytes"] == len(VIDEO_BYTES)
    assert body["video"]["storage_key"]


def test_upload_unknown_token_404(client):
    assert upload(client, "nope-not-a-token").status_code == 404


def test_upload_expired_token_410(client, fake_db):
    as_user("t1")
    body = create(client).json()
    expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    for d in fake_db.video_estimate_requests.docs:
        if d["id"] == body["id"]:
            d["expires_at"] = expired
    r = upload(client, body["upload_token"])
    assert r.status_code == 410


def test_upload_rejects_non_video_and_empty(client):
    as_user("t1")
    token = create(client).json()["upload_token"]
    assert upload(client, token, content_type="image/png",
                  data=b"\x89PNG", filename="x.png").status_code == 400
    assert upload(client, token, data=b"").status_code == 400


def test_upload_rejects_oversize(client, monkeypatch):
    as_user("t1")
    token = create(client).json()["upload_token"]
    monkeypatch.setattr(ve_mod, "MAX_VIDEO_BYTES", 10)
    assert upload(client, token).status_code == 400


def test_upload_second_time_conflicts(client):
    as_user("t1")
    token = create(client).json()["upload_token"]
    assert upload(client, token).status_code == 200
    assert upload(client, token).status_code == 409


# ---------- review ----------

def test_review_notes_flip_to_in_review_and_build_timeline(client):
    as_user("t1")
    token = create(client).json()["upload_token"]
    rid = upload(client, token).json()["id"]
    r = client.post(
        f"/api/video-estimates/tenants/requests/{rid}/review",
        json={"timestamped_notes": [
            {"timestamp_sec": 42, "note": "coil looks rusted"},
            {"timestamp_sec": 5, "note": "unit model label"},
        ]},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "in-review"
    assert r.json()["review_notes"] == 2
    detail = client.get(f"/api/video-estimates/tenants/requests/{rid}").json()
    assert detail["status"] == "in-review"
    timeline = detail["review_notes"]
    assert [n["timestamp_sec"] for n in timeline] == [5, 42]  # sorted
    assert timeline[0]["note"] == "unit model label"


def test_review_negative_timestamp_rejected(client):
    as_user("t1")
    token = create(client).json()["upload_token"]
    rid = upload(client, token).json()["id"]
    r = client.post(
        f"/api/video-estimates/tenants/requests/{rid}/review",
        json={"timestamped_notes": [{"timestamp_sec": -3, "note": "bad"}]},
    )
    assert r.status_code == 422


def test_review_before_upload_conflicts(client):
    as_user("t1")
    rid = create(client).json()["id"]
    r = client.post(
        f"/api/video-estimates/tenants/requests/{rid}/review",
        json={"timestamped_notes": [{"timestamp_sec": 1, "note": "x"}]},
    )
    assert r.status_code == 409


def test_review_converts_to_estimate_via_quote_pipeline(client, monkeypatch):
    """Lines → status `estimated` with a quote_id from the shared pipeline.
    The real `_create_estimate_doc` is stubbed; nothing else reimplements it."""
    as_user("t1")
    token = create(client).json()["upload_token"]
    rid = upload(client, token).json()["id"]
    made = {}

    async def fake_create_estimate_doc(db, tenant_id, **kw):
        made.update(kw)
        made["tenant_id"] = tenant_id
        return {"id": "quote-123"}

    monkeypatch.setattr(ve_mod, "_create_estimate_doc", fake_create_estimate_doc)
    r = client.post(
        f"/api/video-estimates/tenants/requests/{rid}/review",
        json={
            "timestamped_notes": [{"timestamp_sec": 10, "note": "see rust"}],
            "title": "AC coil replacement",
            "lines": [{"description": "Replace evaporator coil", "qty": 1,
                       "unit_price": 1450.0}],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "estimated"
    assert body["quote_id"] == "quote-123"
    assert made["tenant_id"] == "t1"
    assert made["title"] == "AC coil replacement"
    assert made["lines"][0]["unit_price"] == 1450.0

    detail = client.get(f"/api/video-estimates/tenants/requests/{rid}").json()
    assert detail["quote_id"] == "quote-123"

    # re-converting is a conflict
    r2 = client.post(
        f"/api/video-estimates/tenants/requests/{rid}/review", json={"lines": []})
    assert r2.status_code == 409


# ---------- detail / stream ----------

def test_detail_not_found_and_tenant_isolation(client):
    as_user("t1")
    assert client.get("/api/video-estimates/tenants/requests/nope").status_code == 404
    rid = create(client).json()["id"]
    as_user("t2")
    assert client.get(f"/api/video-estimates/tenants/requests/{rid}").status_code == 404


def test_stream_video_returns_bytes(client):
    as_user("t1")
    token = create(client).json()["upload_token"]
    rid = upload(client, token).json()["id"]
    r = client.get(f"/api/video-estimates/tenants/requests/{rid}/video")
    assert r.status_code == 200
    assert r.content == VIDEO_BYTES
    assert "video" in r.headers["content-type"]
    as_user("t2")
    assert client.get(
        f"/api/video-estimates/tenants/requests/{rid}/video").status_code == 404
