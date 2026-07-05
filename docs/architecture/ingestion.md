# Ingestion pipeline

The ingestion pipeline converts an uploaded file into a set of indexed chunks ready for retrieval. It runs in the background after upload.

## Trigger

`POST /api/documents` (`app/api/documents.py:upload_document`) saves the file to `ATHENA_STORAGE_DIR/<user_id>/<doc_id>.<ext>`, creates a `Document` row with `status='uploaded'`, and schedules `_run_ingest()` as a FastAPI `BackgroundTask`. The HTTP response returns immediately with `202 Accepted`.

## Files

| Path | Role |
|---|---|
| `app/services/ingestion/pipeline.py` | `ingest_document()` — orchestrates the steps. |
| `app/services/ingestion/extractors.py` | File-type aware text extractors. |
| `app/services/ingestion/chunker.py` | Token-aware chunker. |
| `app/services/ingestion/keywords.py` | MMR-diversified keyword extractor (TF-IDF + similarity filter). |
| `app/services/ingestion/store.py` | Bulk-insert chunks + embed + invalidate cache. |
| `app/services/embedding.py` | `encode(texts)` — sentence-transformers wrapper. |
| `app/services/text.py` | `clean_text`, `count_tokens`, `truncate_tokens` (tiktoken). |

## The pipeline

```python
async def ingest_document(session, *, document, file_path) -> int:
    extraction = extract(file_path)            # extractors.py
    cleaned = [clean_text(t) for t in extraction.texts] if extraction.mode == "prose" else ...
    chunks_text = chunk(cleaned, target=settings.CHUNK_TARGET_TOKENS, overlap=settings.CHUNK_OVERLAP_TOKENS)
    rows = []
    for idx, text in enumerate(chunks_text):
        kw = extract_keywords(text, top_n=settings.KEYWORD_TOP_N, min_sim=settings.KEYWORD_MIN_SIM)
        emb = embed_one(text)  # 384-dim
        rows.append({...})
    await store_chunks(session, document, rows)
    await mark_indexed(session, document)
    await invalidate_user(str(document.user_id), prefix=settings.CACHE_PREFIX_RETRIEVAL)
    return len(rows)
```

## Step 1 — Extract

`extract(path)` dispatches on file extension:

| Extension | Function | Output |
|---|---|---|
| `.pdf`  | `_ext_pdf`  | `mode="prose"`, joined page text |
| `.docx` | `_ext_docx` | `mode="prose"`, joined paragraph text |
| `.xlsx` | `_ext_xlsx` | `mode="tabular"`, list of `(sheet_name, rows)` |
| `.csv`  | `_ext_csv`  | `mode="tabular"`, single `(filename, rows)` |
| `.txt`, `.md` | `_ext_text` | `mode="prose"`, raw file |
| `.html`, `.htm` | `_ext_html` | `mode="prose"`, stripped of `<script>`/`<style>` |

The pipeline branches on `mode`. For tabular data each row becomes a chunk with `meta={"sheet": name, "row": n}`. For prose, the entire document is one big string that gets split by the chunker.

## Step 2 — Clean

`clean_text(text)` (`app/services/text.py`) normalises:
- Strip BOM and non-printable characters.
- Collapse runs of whitespace (preserving paragraph breaks).
- Normalize unicode.

## Step 3 — Chunk

`chunk()` (`app/services/ingestion/chunker.py`) is a token-aware recursive splitter. It:

1. Tries to fit the whole document in one chunk.
2. If too large, splits on paragraph boundaries (`\n\n`).
3. If still too large, splits on sentence boundaries (`. ` / `? ` / `! `).
4. If still too large, splits on word boundaries.
5. Maintains a `overlap_tokens`-sized overlap between adjacent chunks for context continuity.

`CHUNK_TARGET_TOKENS=300`, `CHUNK_OVERLAP_TOKENS=50` are the defaults. 300 tokens ≈ 1,200 characters, which works well for a 1.5B model with a 3,000-token budget — we can fit ~4 chunks comfortably with the system prompt and tool defs.

## Step 4 — Embed

Each chunk is encoded with `all-MiniLM-L6-v2` (384-dim, L2-normalised). The embedding model is loaded lazily on first use and cached in process memory.

If the model is unavailable, the chunk is still indexed but `embedding=NULL` — lexical search still works, vector search will simply not find it.

## Step 5 — Keywords

`extract_keywords(text, top_n, min_sim)` (`app/services/ingestion/keywords.py`) does:

1. Tokenise and TF-IDF score the document.
2. Sort candidates by TF-IDF descending.
3. Use Maximal Marginal Relevance (MMR) to pick the top-N diverse keywords.
4. Drop candidates whose cosine similarity to an already-chosen keyword is above `min_sim`.

This produces a small set of high-signal keywords that we store as a `TEXT[]` column on the chunk. The keyword list is what the LLM receives in the `search_documents` tool's tool-call arguments.

## Step 6 — Persist + index

`store_chunks()` does a bulk `INSERT INTO document_chunks (...)` (so a 200-page book takes one round trip, not 200). After the insert, the document's `status` is set to `indexed` and the page count is recorded.

## Step 7 — Invalidate cache

Finally, `invalidate_user(user_id, prefix="search")` is called to evict any stale retrieval cache for this user. This is critical: without it, the user could ask a question and get a cached answer that doesn't include the new document.

## Background vs foreground

The entire pipeline runs in a `BackgroundTask` after upload. The HTTP request returns immediately so the UI can show "uploaded" → "processing" → "indexed" states. The frontend polls `GET /api/documents` (see [frontend.md](../frontend.md)).

If ingestion fails, the document is marked `failed` with `error_message` set. The user can see this in the document list and delete the file.

## Concurrency

`asyncpg` pool size is configurable (`ATHENA_DB_POOL_SIZE`, default 10). Ingestion is a single async coroutine per upload. If you need parallel ingestion across many documents, you'd want a separate worker (RQ/Celery) — Phase 2.
