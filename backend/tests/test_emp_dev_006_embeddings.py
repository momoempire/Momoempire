"""EMP-DEV-006 — semantic knowledge search with fake embeddings (no network)."""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

os.environ.setdefault("JWT_SECRET", "emp-dev-006-test-secret")
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "emp_dev_006_test")
os.environ["EMBEDDINGS_PROVIDER"] = "fake"
os.environ["EMBEDDINGS_MODEL"] = "fake-hash-v1"

from services.embeddings import (  # noqa: E402
    FakeEmbeddingProvider,
    cosine_similarity,
    get_embedding_provider,
    embeddings_enabled,
)
from routers import knowledge_docs as kd  # noqa: E402


class _MemColl:
    def __init__(self):
        self.rows: list[dict] = []

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("id")})()

    async def find_one(self, query, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items() if not str(k).startswith("$")):
                return dict(r)
        return None

    def find(self, query, projection=None):
        matched = []
        for r in self.rows:
            ok = True
            for k, v in query.items():
                if k == "$or":
                    if not any(all(r.get(ik) == iv if not isinstance(iv, dict) else True
                                   for ik, iv in clause.items()
                                   if not isinstance(iv, dict)) or _match_or_clause(r, clause)
                               for clause in v):
                        # simplified: handle our backfill $or in Python
                        if not _match_complex(r, query):
                            ok = False
                            break
                    continue
                if isinstance(v, dict):
                    continue
                if r.get(k) != v:
                    ok = False
                    break
            if ok and _match_complex(r, query):
                matched.append(dict(r))
        return _Cursor(matched)

    async def update_one(self, query, update):
        for r in self.rows:
            if all(r.get(k) == v for k, v in query.items()):
                if "$set" in update:
                    r.update(update["$set"])
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    async def delete_one(self, query):
        before = len(self.rows)
        self.rows = [r for r in self.rows if not all(r.get(k) == v for k, v in query.items())]
        return type("R", (), {"deleted_count": before - len(self.rows)})()

    async def delete_many(self, query):
        before = len(self.rows)
        self.rows = [r for r in self.rows if not all(r.get(k) == v for k, v in query.items())]
        return type("R", (), {"deleted_count": before - len(self.rows)})()

    async def count_documents(self, query):
        n = 0
        for r in self.rows:
            if _match_complex(r, query):
                n += 1
        return n


def _match_or_clause(r, clause: dict) -> bool:
    for k, v in clause.items():
        if isinstance(v, dict):
            if "$exists" in v:
                exists = k in r and r[k] is not None
                want = bool(v["$exists"])
                if v.get("$ne") is None and "$ne" in v:
                    # embedding: {$exists: True, $ne: None} style merged wrongly — handle below
                    pass
                if want and not exists:
                    return False
                if not want and exists:
                    return False
            if "$ne" in v:
                if r.get(k) == v["$ne"]:
                    return False
            if "$exists" in v and "$ne" in v:
                if not (k in r and r[k] is not None):
                    return False
        else:
            if r.get(k) != v:
                return False
    return True


def _match_complex(r, query: dict) -> bool:
    if not query:
        return True
    for k, v in query.items():
        if k == "$or":
            if not any(_match_or_clause(r, c) for c in v):
                return False
            continue
        if isinstance(v, dict):
            if "$exists" in v:
                exists = (k in r) and (r[k] is not None)
                if v["$exists"] and not exists:
                    return False
                if (not v["$exists"]) and exists:
                    return False
            if "$ne" in v and r.get(k) == v["$ne"]:
                return False
        else:
            if r.get(k) != v:
                return False
    return True


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, *a, **k):
        return self

    async def to_list(self, n):
        return list(self._rows)[:n]


class _FakeDB:
    def __init__(self):
        self.knowledge_docs = _MemColl()
        self.knowledge_chunks = _MemColl()
        self.tenants = _MemColl()


@pytest.fixture
def fake_db():
    return _FakeDB()


@pytest.fixture(autouse=True)
def _force_fake_provider(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "fake")
    monkeypatch.setenv("EMBEDDINGS_MODEL", "fake-hash-v1")


@pytest.mark.asyncio
async def test_provider_resolves_fake():
    p = get_embedding_provider()
    assert p is not None
    assert p.name == "fake"
    vecs = await p.embed_texts(["hello world", "hello world"])
    assert vecs[0] == vecs[1]
    assert abs(sum(x * x for x in vecs[0]) - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_disabled_falls_back_to_keyword(fake_db, monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "off")
    assert get_embedding_provider() is None

    # Seed two chunks for tenant A
    fake_db.knowledge_chunks.rows = [
        {"id": "c1", "tenant_id": "tA", "doc_id": "d1", "doc_title": "A", "chunk_idx": 0,
         "content": "Our emergency AC repair fee is eighty nine dollars."},
        {"id": "c2", "tenant_id": "tA", "doc_id": "d1", "doc_title": "A", "chunk_idx": 1,
         "content": "We also sell winter furnace tune-ups every autumn."},
    ]
    with patch.object(kd, "get_db", return_value=fake_db):
        hits = await kd._search_chunks("tA", "emergency AC repair fee", limit=5)
    assert hits
    assert "eighty nine" in hits[0]["content"]
    assert hits[0]["match_mode"] == "keyword"


@pytest.mark.asyncio
async def test_semantic_ranks_related_above_unrelated(fake_db):
    provider = FakeEmbeddingProvider(dimensions=32)
    texts = [
        "Central air conditioning not cooling — schedule same-day AC technician.",
        "Employee handbook vacation policy and paid time off rules.",
        "Air conditioner refrigerant leak diagnosis and cooling repair.",
    ]
    vecs = await provider.embed_texts(texts)
    fake_db.knowledge_chunks.rows = [
        {"id": "c1", "tenant_id": "tA", "doc_title": "HVAC", "content": texts[0], "embedding": vecs[0],
         "embedding_model": "fake-hash-v1", "embedding_provider": "fake"},
        {"id": "c2", "tenant_id": "tA", "doc_title": "HR", "content": texts[1], "embedding": vecs[1],
         "embedding_model": "fake-hash-v1", "embedding_provider": "fake"},
        {"id": "c3", "tenant_id": "tA", "doc_title": "HVAC", "content": texts[2], "embedding": vecs[2],
         "embedding_model": "fake-hash-v1", "embedding_provider": "fake"},
    ]
    with patch.object(kd, "get_db", return_value=fake_db):
        hits = await kd._search_chunks("tA", "AC not cooling same day repair", limit=3)
    assert hits
    # Top hit should be HVAC-related, not the vacation policy
    assert "vacation" not in hits[0]["content"].lower()
    assert hits[0]["match_mode"] in {"hybrid", "keyword"}


@pytest.mark.asyncio
async def test_tenant_isolation(fake_db):
    provider = FakeEmbeddingProvider(dimensions=32)
    a_text = "Tenant Alpha exclusive pricing for gold plan."
    b_text = "Tenant Beta exclusive pricing for platinum plan."
    va, vb = await provider.embed_texts([a_text, b_text])
    fake_db.knowledge_chunks.rows = [
        {"id": "ca", "tenant_id": "tA", "doc_title": "A", "content": a_text, "embedding": va,
         "embedding_model": "fake-hash-v1", "embedding_provider": "fake"},
        {"id": "cb", "tenant_id": "tB", "doc_title": "B", "content": b_text, "embedding": vb,
         "embedding_model": "fake-hash-v1", "embedding_provider": "fake"},
    ]
    with patch.object(kd, "get_db", return_value=fake_db):
        hits_a = await kd._search_chunks("tA", "exclusive pricing gold", limit=5)
        hits_b = await kd._search_chunks("tB", "exclusive pricing platinum", limit=5)
    assert all("Alpha" in h["content"] or "gold" in h["content"] for h in hits_a)
    assert all(h.get("tenant_id", "tA") != "tB" for h in hits_a)  # response strips tenant sometimes
    assert not any("Beta" in h["content"] for h in hits_a)
    assert not any("Alpha" in h["content"] for h in hits_b)


@pytest.mark.asyncio
async def test_provider_error_falls_back_to_keyword(fake_db, monkeypatch):
    class Boom(FakeEmbeddingProvider):
        async def embed_texts(self, texts):
            raise RuntimeError("network down")

    fake_db.knowledge_chunks.rows = [
        {"id": "c1", "tenant_id": "tA", "doc_title": "A", "content": "Warranty covers compressor failure for five years."},
    ]
    with patch.object(kd, "get_embedding_provider", return_value=Boom()), \
         patch.object(kd, "get_db", return_value=fake_db):
        hits = await kd._search_chunks("tA", "compressor warranty", limit=3)
    assert hits and hits[0]["match_mode"] == "keyword"


@pytest.mark.asyncio
async def test_backfill_idempotent(fake_db):
    fake_db.knowledge_chunks.rows = [
        {"id": "c1", "tenant_id": "tA", "doc_title": "A", "content": "First chunk about duct cleaning."},
        {"id": "c2", "tenant_id": "tA", "doc_title": "A", "content": "Second chunk about filter replacement."},
    ]
    user = {"id": "u1", "tenant_id": "tA", "role": "owner"}
    with patch.object(kd, "get_db", return_value=fake_db):
        r1 = await kd.backfill_embeddings(user=user, limit=100, force=False)
        assert r1["status"] == "ok"
        assert r1["embedded"] == 2
        # Second run should examine 0 needing work (idempotent skip)
        r2 = await kd.backfill_embeddings(user=user, limit=100, force=False)
        assert r2["embedded"] == 0
        assert all(c.get("embedding") for c in fake_db.knowledge_chunks.rows)
        # force re-embeds
        r3 = await kd.backfill_embeddings(user=user, limit=100, force=True)
        assert r3["embedded"] == 2


@pytest.mark.asyncio
async def test_backfill_skipped_when_disabled(fake_db, monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "off")
    user = {"id": "u1", "tenant_id": "tA", "role": "owner"}
    with patch.object(kd, "get_db", return_value=fake_db):
        r = await kd.backfill_embeddings(user=user, limit=10, force=False)
    assert r["status"] == "skipped"
    assert r["reason"] == "embeddings_disabled"


def test_cosine_identical_is_one():
    v = [0.0] * 8
    v[0] = 1.0
    assert abs(cosine_similarity(v, v) - 1.0) < 1e-9
