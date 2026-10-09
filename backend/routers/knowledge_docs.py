"""Knowledge document upload + keyword / semantic hybrid search."""
from __future__ import annotations

import io
import logging
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Query
from pypdf import PdfReader
from docx import Document as DocxDoc

from db import get_db
from models_phase3 import KnowledgeDoc, KnowledgeChunk
from models import _uuid, _now_iso
from security import require_tenant_user, require_tenant_owner_or_admin, require_platform_admin
from services.embeddings import (
    get_embedding_provider,
    embeddings_enabled,
    cosine_similarity,
    keyword_score,
    l2_normalize,
)

log = logging.getLogger("knowledge-docs")

router = APIRouter(prefix="/tenants/knowledge-docs", tags=["knowledge-docs"])
admin_router = APIRouter(
    prefix="/admin/knowledge",
    tags=["admin-knowledge"],
    dependencies=[Depends(require_platform_admin)],
)

CHUNK = 1200  # characters per chunk
SEMANTIC_WEIGHT = 0.7
KEYWORD_WEIGHT = 0.3


def _extract_text(filename: str, content: bytes) -> str:
    name = filename.lower()
    try:
        if name.endswith(".pdf"):
            r = PdfReader(io.BytesIO(content))
            return "\n".join([(p.extract_text() or "") for p in r.pages])
        if name.endswith(".docx"):
            d = DocxDoc(io.BytesIO(content))
            return "\n".join([p.text for p in d.paragraphs])
        return content.decode("utf-8", errors="ignore")
    except Exception as e:
        raise HTTPException(400, f"Failed to extract text: {e}")


def _chunk(text: str, size: int = CHUNK):
    text = re.sub(r"[\t ]+", " ", text).strip()
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out = []
    cur = ""
    for p in paras:
        if len(cur) + len(p) + 2 <= size:
            cur = (cur + "\n\n" + p).strip()
        else:
            if cur:
                out.append(cur)
            if len(p) <= size:
                cur = p
            else:
                for i in range(0, len(p), size):
                    out.append(p[i : i + size])
                cur = ""
    if cur:
        out.append(cur)
    return out


def _terms(q: str, min_len: int = 2) -> list[str]:
    return [t.lower() for t in re.findall(r"\w+", q or "") if len(t) > min_len]


async def _embed_chunk_docs(chunks: list[dict]) -> int:
    """Best-effort embed + persist. Returns count newly embedded. Never raises to callers of upload."""
    provider = get_embedding_provider()
    if not provider or not chunks:
        return 0
    try:
        vectors = await provider.embed_texts([c["content"] for c in chunks])
    except Exception as e:
        log.warning("embed failed (non-fatal): %s", e)
        return 0
    db = get_db()
    now = _now_iso()
    n = 0
    for c, vec in zip(chunks, vectors):
        if not vec:
            continue
        unit = l2_normalize(vec)
        await db.knowledge_chunks.update_one(
            {"id": c["id"], "tenant_id": c["tenant_id"]},
            {"$set": {
                "embedding": unit,
                "embedding_model": provider.model,
                "embedding_provider": provider.name,
                "embedded_at": now,
            }},
        )
        c["embedding"] = unit
        c["embedding_model"] = provider.model
        c["embedding_provider"] = provider.name
        c["embedded_at"] = now
        n += 1
    return n


def _hybrid_rank(
    rows: list[dict],
    query_vec: Optional[list[float]],
    terms: list[str],
    limit: int,
) -> list[dict]:
    """Combine cosine (if available) + keyword. Falls back to keyword-only."""
    kw_raw = [keyword_score(r.get("content") or "", terms) for r in rows]
    max_kw = max(kw_raw) if kw_raw else 0.0

    scored: list[dict] = []
    for r, kw in zip(rows, kw_raw):
        sem = 0.0
        if query_vec and r.get("embedding"):
            try:
                sem = cosine_similarity(query_vec, r["embedding"])
            except Exception:
                sem = 0.0
        kw_n = (kw / max_kw) if max_kw > 0 else 0.0
        if query_vec and r.get("embedding"):
            score = SEMANTIC_WEIGHT * sem + KEYWORD_WEIGHT * kw_n
            mode = "hybrid"
        else:
            score = kw_n if max_kw > 0 else 0.0
            mode = "keyword"
        if score > 0 or (query_vec and r.get("embedding") and sem > 0.15):
            # Strip bulky embedding from API response
            out = {k: v for k, v in r.items() if k != "embedding"}
            out["score"] = round(float(score), 6)
            out["score_semantic"] = round(float(sem), 6)
            out["score_keyword"] = round(float(kw_n), 6)
            out["match_mode"] = mode
            scored.append(out)

    # If semantic path produced nothing useful, keyword-only fallback over all rows
    if not scored and terms:
        for r, kw in zip(rows, kw_raw):
            if kw <= 0:
                continue
            out = {k: v for k, v in r.items() if k != "embedding"}
            out["score"] = float(kw)
            out["score_semantic"] = 0.0
            out["score_keyword"] = float(kw)
            out["match_mode"] = "keyword"
            scored.append(out)

    scored.sort(key=lambda x: -x["score"])
    return scored[:limit]


async def _search_chunks(tenant_id: str, question: str, limit: int = 8, min_term_len: int = 2) -> list[dict]:
    db = get_db()
    terms = _terms(question, min_len=min_term_len)
    rows = await db.knowledge_chunks.find(
        {"tenant_id": tenant_id},
        {"_id": 0},
    ).to_list(2000)

    query_vec = None
    provider = get_embedding_provider()
    if provider and question and question.strip():
        try:
            vectors = await provider.embed_texts([question])
            if vectors and vectors[0]:
                query_vec = l2_normalize(vectors[0])
        except Exception as e:
            log.warning("query embed failed — keyword fallback: %s", e)
            query_vec = None

    if not terms and not query_vec:
        return []
    return _hybrid_rank(rows, query_vec, terms, limit)


@router.get("")
async def list_docs(user: dict = Depends(require_tenant_user)):
    db = get_db()
    return await db.knowledge_docs.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).sort("created_at", -1).to_list(200)


@router.post("")
async def upload_doc(
    file: UploadFile = File(...),
    title: str = Form(""),
    tags: str = Form(""),
    user: dict = Depends(require_tenant_owner_or_admin),
):
    db = get_db()
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "Max 10MB per file")
    text = _extract_text(file.filename, content)
    chunks = _chunk(text)
    doc = KnowledgeDoc(
        tenant_id=user["tenant_id"],
        title=(title or file.filename),
        filename=file.filename,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(content),
        chunk_count=len(chunks),
        tags=[t.strip() for t in tags.split(",") if t.strip()],
    )
    doc_payload = dict(doc.model_dump())
    await db.knowledge_docs.insert_one(doc_payload)
    chunk_docs = []
    for i, chunk in enumerate(chunks):
        kc = KnowledgeChunk(
            tenant_id=user["tenant_id"], doc_id=doc.id, doc_title=doc.title,
            chunk_idx=i, content=chunk,
        )
        payload = dict(kc.model_dump())
        await db.knowledge_chunks.insert_one(payload)
        chunk_docs.append(payload)
    # Best-effort embed when provider is enabled (never blocks upload success).
    embedded = await _embed_chunk_docs(chunk_docs)
    result = doc.model_dump()
    result["embeddings_written"] = embedded
    result["embeddings_enabled"] = embeddings_enabled() and get_embedding_provider() is not None
    return result


@router.delete("/{doc_id}")
async def delete_doc(doc_id: str, user: dict = Depends(require_tenant_owner_or_admin)):
    db = get_db()
    res = await db.knowledge_docs.delete_one({"id": doc_id, "tenant_id": user["tenant_id"]})
    if res.deleted_count == 0:
        raise HTTPException(404, "Not found")
    await db.knowledge_chunks.delete_many({"doc_id": doc_id, "tenant_id": user["tenant_id"]})
    return {"status": "ok"}


@router.get("/search")
async def search_knowledge(q: str, user: dict = Depends(require_tenant_user), limit: int = 8):
    return await _search_chunks(user["tenant_id"], q, limit=limit, min_term_len=2)


@router.get("/embeddings/status")
async def embeddings_status(user: dict = Depends(require_tenant_user)):
    """Report whether embeddings are active and how many chunks are embedded for this tenant."""
    db = get_db()
    tid = user["tenant_id"]
    total = await db.knowledge_chunks.count_documents({"tenant_id": tid})
    embedded = await db.knowledge_chunks.count_documents({
        "tenant_id": tid,
        "embedding": {"$exists": True, "$ne": None},
    })
    provider = get_embedding_provider()
    return {
        "enabled": provider is not None,
        "provider": provider.name if provider else None,
        "model": provider.model if provider else None,
        "chunks_total": total,
        "chunks_embedded": embedded,
    }


@router.post("/embeddings/backfill")
async def backfill_embeddings(
    user: dict = Depends(require_tenant_owner_or_admin),
    limit: int = Query(500, ge=1, le=5000),
    force: bool = Query(False, description="Re-embed even when vector already present for current model"),
):
    """Idempotent tenant-scoped backfill. Admin/owner only. No-op when provider off."""
    provider = get_embedding_provider()
    if not provider:
        return {
            "status": "skipped",
            "reason": "embeddings_disabled",
            "embedded": 0,
            "skipped_existing": 0,
            "examined": 0,
        }
    db = get_db()
    tid = user["tenant_id"]
    query: dict = {"tenant_id": tid}
    if not force:
        query["$or"] = [
            {"embedding": {"$exists": False}},
            {"embedding": None},
            {"embedding_model": {"$ne": provider.model}},
            {"embedding_provider": {"$ne": provider.name}},
        ]
    rows = await db.knowledge_chunks.find(query, {"_id": 0}).to_list(limit)
    if not rows:
        return {"status": "ok", "embedded": 0, "skipped_existing": 0, "examined": 0, "provider": provider.name, "model": provider.model}

    # Count already-good chunks for reporting when force=False
    skipped_existing = 0
    if not force:
        skipped_existing = await db.knowledge_chunks.count_documents({
            "tenant_id": tid,
            "embedding_model": provider.model,
            "embedding_provider": provider.name,
            "embedding": {"$exists": True, "$ne": None},
        })

    try:
        vectors = await provider.embed_texts([r["content"] for r in rows])
    except Exception as e:
        raise HTTPException(502, f"Embedding provider error: {e}")

    now = _now_iso()
    embedded = 0
    for r, vec in zip(rows, vectors):
        if not vec:
            continue
        unit = l2_normalize(vec)
        await db.knowledge_chunks.update_one(
            {"id": r["id"], "tenant_id": tid},
            {"$set": {
                "embedding": unit,
                "embedding_model": provider.model,
                "embedding_provider": provider.name,
                "embedded_at": now,
            }},
        )
        embedded += 1

    return {
        "status": "ok",
        "embedded": embedded,
        "skipped_existing": skipped_existing,
        "examined": len(rows),
        "provider": provider.name,
        "model": provider.model,
        "force": force,
    }


@admin_router.post("/embeddings/backfill")
async def admin_backfill_embeddings(
    tenant_id: str = Query(..., description="Target tenant id"),
    limit: int = Query(500, ge=1, le=5000),
    force: bool = False,
):
    """Platform-admin backfill for a single tenant (still tenant-isolated)."""
    provider = get_embedding_provider()
    if not provider:
        return {"status": "skipped", "reason": "embeddings_disabled", "embedded": 0}
    db = get_db()
    tenant = await db.tenants.find_one({"id": tenant_id}, {"_id": 0, "id": 1})
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    # Reuse tenant path by synthesizing a minimal user
    fake_user = {"tenant_id": tenant_id, "role": "owner", "id": "admin-backfill"}
    return await backfill_embeddings(user=fake_user, limit=limit, force=force)


async def retrieve_relevant_chunks(tenant_id: str, question: str, limit: int = 5) -> list[dict]:
    """Used by the receptionist + advisor system prompts."""
    hits = await _search_chunks(tenant_id, question, limit=limit, min_term_len=3)
    # Keep legacy shape: content + doc_title (+ score extras ok)
    return [{"content": h.get("content"), "doc_title": h.get("doc_title"), "score": h.get("score")} for h in hits]
