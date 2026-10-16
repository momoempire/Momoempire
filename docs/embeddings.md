# Knowledge embeddings (EMP-DEV-006)

Semantic search for knowledge chunks. **Off by default** — no paid provider is activated until Brann approves spend and sets env vars.

## Enable (after approval)

```
EMBEDDINGS_PROVIDER=openai
EMBEDDINGS_MODEL=text-embedding-3-small
OPENAI_API_KEY=...          # existing key; never commit
```

`EMBEDDINGS_PROVIDER` empty / `off` / `none` / `disabled` → keyword search only (current Phase 3 behavior).

Test-only provider (no network): `EMBEDDINGS_PROVIDER=fake`.

## Behavior

| Path | Behavior |
| --- | --- |
| Upload | Chunks stored as today; if provider active, vectors written best-effort (upload still succeeds on embed failure). |
| `GET /tenants/knowledge-docs/search` | Hybrid ranking when vectors exist (cosine + keyword); else keyword. On embed error → keyword. |
| `retrieve_relevant_chunks` (receptionist / advisor) | Same hybrid path. |
| `POST /tenants/knowledge-docs/embeddings/backfill` | Owner/admin; tenant-scoped; skips chunks already matching provider+model unless `force=true`. |
| `POST /admin/knowledge/embeddings/backfill?tenant_id=` | Platform admin; still scoped to one tenant. |

Vectors live on `knowledge_chunks` as `embedding` (+ `embedding_model`, `embedding_provider`, `embedded_at`). Search loads the tenant’s chunks and scores in Python (fine for small corpora).

## Cost estimate (sourced)

OpenAI `text-embedding-3-small`: **$0.02 per 1M input tokens** (standard API).  
Source: [OpenAI text-embedding-3-small model page](https://developers.openai.com/api/docs/models/text-embedding-3-small), [OpenAI embeddings guide](https://developers.openai.com/api/docs/guides/embeddings).

Rough estimate (not a quote): ~800 tokens/page of text → ~**62,500 pages per US dollar** at that rate (OpenAI’s own example table on the embeddings guide). A 500-chunk tenant backfill at ~200 tokens/chunk ≈ 100k tokens ≈ **$0.002** estimate.

No keys are provisioned by this change. Live calls require Brann’s approval.

## Future: MongoDB Atlas Vector Search (seam)

Today: in-app cosine over tenant chunks. Swap later by replacing the rank step with `$vectorSearch` while keeping the same `embedding` field and provider interface.

Atlas notes (estimates / limits — confirm before buying):

| Item | Detail | Source |
| --- | --- | --- |
| Free (M0) storage | 512 MB | [MongoDB Atlas pricing](https://www.mongodb.com/pricing), [Free cluster limits](https://www.mongodb.com/docs/atlas/reference/free-shared-limitations) |
| Free index cap | Max **3** search/vector indexes total on Free | [Vector Search compatibility & limitations](https://www.mongodb.com/docs/vector-search/deployment/compatibility-limitations/) |
| Free / Flex | OK for prototype; may see resource contention; dedicated Search Nodes need **M10+** | [Vector Search deployment options](https://www.mongodb.com/docs/vector-search/deployment/deployment-options) |
| Vector dims | Indexes support ≤ 8192 dimensions | [Vector Search compatibility](https://www.mongodb.com/docs/vector-search/deployment/compatibility-limitations/) |

**Open decision for Brann:** stay on in-app cosine (current default when embeddings on) vs enable Atlas Vector Search (may require a paid Atlas tier for production).
