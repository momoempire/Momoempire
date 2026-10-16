"""Pluggable text embeddings for knowledge semantic search.

OFF by default. Enable only when Brann approves a provider/spend and sets:
  EMBEDDINGS_PROVIDER=openai
  EMBEDDINGS_MODEL=text-embedding-3-small   # optional
  OPENAI_API_KEY=...                       # existing key

When disabled or on any error, callers MUST fall back to keyword search.
Vectors are stored on knowledge_chunks documents (tenant-scoped). In-app
cosine similarity is used for small corpora; see docs/embeddings.md for a
seam to MongoDB Atlas Vector Search later.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
from typing import Optional, Protocol, Sequence

log = logging.getLogger("embeddings")

DEFAULT_OPENAI_MODEL = "text-embedding-3-small"
DEFAULT_OPENAI_DIMS = 1536


class EmbeddingProvider(Protocol):
    name: str
    model: str
    dimensions: int

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        ...


def embeddings_enabled() -> bool:
    raw = (os.environ.get("EMBEDDINGS_PROVIDER") or "").strip().lower()
    return raw not in {"", "off", "none", "disabled", "false", "0"}


def get_embedding_provider() -> Optional[EmbeddingProvider]:
    """Return an active provider, or None when embeddings are off / misconfigured."""
    if not embeddings_enabled():
        return None
    provider = (os.environ.get("EMBEDDINGS_PROVIDER") or "").strip().lower()
    model = (os.environ.get("EMBEDDINGS_MODEL") or DEFAULT_OPENAI_MODEL).strip()
    if provider == "openai":
        if not os.environ.get("OPENAI_API_KEY"):
            log.warning("EMBEDDINGS_PROVIDER=openai but OPENAI_API_KEY missing — embeddings off")
            return None
        return OpenAIEmbeddingProvider(model=model)
    if provider == "fake":
        # Deterministic local vectors for tests / offline demos — no network.
        return FakeEmbeddingProvider(model=model or "fake-hash-v1", dimensions=32)
    log.warning("Unknown EMBEDDINGS_PROVIDER=%r — embeddings off", provider)
    return None


class OpenAIEmbeddingProvider:
    name = "openai"

    def __init__(self, model: str = DEFAULT_OPENAI_MODEL, dimensions: int = DEFAULT_OPENAI_DIMS):
        self.model = model
        self.dimensions = dimensions

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
        # Batch in chunks of 64 to stay under request limits.
        out: list[list[float]] = []
        batch_size = 64
        for i in range(0, len(texts), batch_size):
            batch = [t if t.strip() else " " for t in texts[i : i + batch_size]]
            resp = await client.embeddings.create(model=self.model, input=batch)
            # API returns data sorted by index
            ordered = sorted(resp.data, key=lambda d: d.index)
            out.extend([list(d.embedding) for d in ordered])
        return out


class FakeEmbeddingProvider:
    """Deterministic bag-of-chars hash → unit vector. No network. For tests."""

    name = "fake"

    def __init__(self, model: str = "fake-hash-v1", dimensions: int = 32):
        self.model = model
        self.dimensions = dimensions

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        return [_fake_embed(t, self.dimensions) for t in texts]


def _fake_embed(text: str, dims: int) -> list[float]:
    """Map overlapping char trigrams into a fixed-dim unit vector (deterministic)."""
    vec = [0.0] * dims
    normed = (text or "").lower()
    if not normed:
        vec[0] = 1.0
        return vec
    for i in range(len(normed)):
        tri = normed[i : i + 3]
        h = int(hashlib.sha256(tri.encode("utf-8")).hexdigest(), 16)
        vec[h % dims] += 1.0
        vec[(h // dims) % dims] += 0.5
    return l2_normalize(vec)


def l2_normalize(vec: Sequence[float]) -> list[float]:
    s = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / s for x in vec]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    # Prefer dot product (OpenAI embeddings are L2-normalized; fake ones are too).
    return float(sum(x * y for x, y in zip(a, b)))


def keyword_score(content: str, terms: Sequence[str]) -> float:
    if not terms:
        return 0.0
    lc = (content or "").lower()
    return float(sum(lc.count(t) for t in terms))
