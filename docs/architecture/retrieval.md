# Retrieval architecture

Retrieval is the pipeline that takes a user query and returns a ranked list of relevant chunks from the user's indexed documents.

## Files

| Path | Role |
|---|---|
| `app/services/retrieval/search.py` | Public entry point. Caches results in Redis. |
| `app/services/retrieval/hybrid.py` | RRF fusion of lexical + vector results. |
| `app/services/retrieval/lexical.py` | BM25-style search using `tsvector` / `ts_rank_cd`. |
| `app/services/retrieval/vector.py` | Cosine similarity over the HNSW index on `pgvector`. |
| `app/services/retrieval/rerank.py` | Optional second-stage reranker (Phase 2). |
| `app/services/embedding.py` | Sentence-transformer wrapper; lazy-loaded. |

## Public API

```python
from app.services.retrieval import search as retrieval_search

chunks = await retrieval_search.retrieve(
    session=session,
    user_id=user_id,
    keywords=["quarterly", "revenue"],   # list[str] or a free-form string
    top_k=4,                              # default = settings.RETRIEVAL_TOP_K
)
# chunks: list[dict] with chunk_id, document_id, document_name,
# page_number, content, keywords, score
```

`retrieve()`:

1. Normalises the query (lowercases, sorts tokens, removes punctuation).
2. Computes a SHA-1 cache key: `<user_id>:<sha1-16>`.
3. Tries `cache.get_json("search", key)`. If hit, returns immediately.
4. On miss, calls `hybrid_search()` and caches the JSON-serialised result for `ATHENA_CACHE_TTL_SECONDS`.

## Hybrid search: lexical + vector with RRF

```python
async def hybrid_search(
    session, *, user_id, query, top_k, always_hybrid=None,
) -> list[dict]:
    lex_hits = await lexical.search_lexical(session, user_id, query, top_k)
    if not lex_hits: return []

    run_vector = always_hybrid or settings.RETRIEVAL_ALWAYS_HYBRID
    if not run_vector:
        run_vector = lex_hits[0]["score"] < settings.RETRIEVAL_HYBRID_THRESHOLD

    vec_hits = []
    if run_vector:
        qvec = encode([query], normalize=True)
        vec_hits = await vector.search_vector(session, user_id, qvec[0].tolist(), top_k)

    return _rrf([lex_hits, vec_hits]) if vec_hits else lex_hits
```

### Why RRF (Reciprocal Rank Fusion)?

RRF combines ranked lists from independent retrievers *without* needing score calibration. Each retriever contributes `1 / (k + rank)` (with `k = 60`) to the fused score. This is critical because:

- Lexical (BM25) and vector (cosine) scores are on different scales.
- A simple score-summation can be dominated by whichever retriever produces higher absolute numbers.
- RRF treats them symmetrically and only cares about *rank* per list.

### Adaptive vs always-hybrid (FR-21)

Default Phase 1 behaviour (`ATHENA_RETRIEVAL_ALWAYS_HYBRID=false`):
- Run lexical first.
- If the lexical top-1 score is below `ATHENA_RETRIEVAL_HYBRID_THRESHOLD` (default 0.05), the lexical ranker is uncertain — also run vector and RRF-fuse.
- Otherwise return the lexical hits.

When `ATHENA_RETRIEVAL_ALWAYS_HYBRID=true`: always run both and RRF-fuse. This is more expensive but produces better recall when the lexical index is sparse.

## Lexical search (`lexical.search_lexical`)

```sql
SELECT id, document_id, page_number, content, keywords,
       ts_rank_cd(content_tsv, websearch_to_tsquery('english', :q)) AS score
FROM document_chunks
WHERE user_id = :uid
  AND content_tsv @@ websearch_to_tsquery('english', :q)
ORDER BY score DESC
LIMIT :top_k
```

`content_tsv` is a `tsvector` generated column on `content` (English config) and indexed with GIN. We use `websearch_to_tsquery` so user input can be a Google-style query (`"foo bar" -baz`).

The query string is built from the joined keywords: `retrieve(query="quarterly revenue")` becomes `websearch_to_tsquery('english', 'quarterly revenue')`.

## Vector search (`vector.search_vector`)

```sql
SELECT id, document_id, page_number, content, keywords,
       1 - (embedding <=> :qvec) AS score
FROM document_chunks
WHERE user_id = :uid
ORDER BY embedding <=> :qvec ASC
LIMIT :top_k
```

`embedding` is a `vector(384)` column with an HNSW index (`vector_cosine_ops`). The query embedding is produced by `app/services/embedding.py` (sentence-transformers `all-MiniLM-L6-v2`, 384-dim, L2-normalised).

`pgvector`'s cosine distance is `<=>` (1 - cosine similarity). We return `1 - distance` as the score so higher is better, matching the lexical side.

## RLS in retrieval

`retrieve()` calls `set_rls_user(session, user_id)` *before* running the retrievers, so all `WHERE user_id = :uid` predicates are also enforced at the database layer by the `*_iso` policies in `infra/init.sql`. Defence in depth — the app layer is correct on its own, and a misconfigured query still cannot leak.

## Caching

`retrieve()` caches the **JSON-serialised** chunk list, not the ORM objects, under `<cache_prefix_retrieval>:<user_id>:<sha1-16>`. TTL is `ATHENA_CACHE_TTL_SECONDS` (default 300s).

Invalidation happens on:
- Document upload (`documents.upload_document` calls `invalidate_user(user_id, prefix=settings.CACHE_PREFIX_RETRIEVAL)`).
- Document delete (`documents.delete_document` does the same).
- Conversation delete (does *not* invalidate retrieval cache — keys are user-scoped, not conversation-scoped).

## Limitations / Phase 2

- `rerank.py` exists as a stub; reranking is Phase 2.
- The HNSW index is built with default parameters. For very large corpora, tune `m` and `ef_construction`.
- We don't currently index sub-token queries. `pg_trgm` extension is enabled, but we don't yet use it; a future release can fall back to trigram similarity when both tsvector and vector return nothing.
- Per-document scoping: a user could ask the orchestrator to "only use document X" via a future `tool_subset` expansion; today `search_documents` searches the full corpus.
