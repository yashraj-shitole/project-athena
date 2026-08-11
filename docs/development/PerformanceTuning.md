# Performance Tuning Notes

This note captures the latest documentation-relevant behavior in Project Athena after the ingestion, retrieval, and provider-runtime performance work.

## Prompt budget and model context

- `ATHENA_TOKEN_BUDGET` now defaults to `8000`.
- `ATHENA_HISTORY_RESERVE` defaults to `1200`.
- `ATHENA_CHUNK_RESERVE` defaults to `2500`.
- `ATHENA_ANSWER_RESERVE` defaults to `768`.
- `ATHENA_MODEL_CONTEXT_TOKENS` defaults to `32768` and is used for the Ollama `num_ctx` value.

Why this matters:
- The prompt builder can keep more retrieval context and history in play.
- The answer cap is large enough to avoid mid-sentence truncation without consuming the whole window.
- The model context limit is tracked separately from the prompt budget so the two knobs can evolve independently.

## Ingestion pipeline

- Embedding batches are now larger by default: `ATHENA_INGEST_EMBED_BATCH_SIZE = 64`.
- Parallel ingestion embeddings are controlled by `ATHENA_INGEST_EMBED_WORKERS`.
  - `0` means auto-select `min(8, cpu_count)`.
  - Torch intra-op threads are capped to reduce oversubscription when workers run in parallel.
- HNSW bulk-load tuning is applied transaction-locally during ingestion:
  - `ATHENA_INGEST_HNSW_EF_INSERT = 10`
  - `ATHENA_INGEST_MAINTENANCE_WORK_MEM = 256MB`

Operational note:
- These ingestion GUCs reset on commit, so they do not leak into normal retrieval traffic.
- The pipeline now emits stage timings and sub-stage timings so you can tell whether ingestion is encoder-bound or database-bound.

## Retrieval

- Hybrid retrieval now treats the raw user message as the semantic query and keeps keywords separate for lexical search.
- `ATHENA_RETRIEVAL_TOP_K` now defaults to `6`.
- `ATHENA_RETRIEVAL_VECTOR_MIN_SIM` defaults to `0.2` and filters low-similarity vector hits.

Why this matters:
- The semantic embedding sees the full request, not just extracted keywords.
- Low-similarity vector hits are less likely to pollute the prompt.
- Retrieval output is more stable when one retriever produces hits and the other does not.

## Provider runtime and streaming

- Provider adapters now keep a warm cache keyed by connector identity and `updated_at`, so connector edits invalidate stale clients automatically.
- Streaming tool calls are surfaced on the terminal event across supported adapters instead of being dropped silently.
- Anthropic, Gemini, Ollama, OpenAI-compatible, Azure OpenAI, and custom adapters now normalize more cross-vendor option names.

## Docs impact

If you update any of the knobs above, keep the README configuration table and any deployment notes in sync with the code defaults.
