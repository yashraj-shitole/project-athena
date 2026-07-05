# Upload & Ingestion Pipeline

_8 finding(s) in this dimension._

Findings in document upload + ingestion: extension-only allowlist with no content validation, no `.doc` extractor, request body not capped before buffering, files written before DB commit (orphans on failure), no page/row/chunk caps (resource exhaustion / decompression bombs), binary accepted by text extractors, and background ingest errors swallowed. Fixed by magic-byte validation, Content-Length + streaming size caps, an explicit `.doc` rejection, orphan cleanup on commit failure, marking failed docs, NUL-byte guards, and hard caps on pages, rows, and extracted characters.

---

### `doc-extension-no-extractor`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Confidence | high |
| Category | logic-bug |
| Location | `backend/app/services/ingestion/extractors.py:110` |
| Status | **Fixed** |

**Summary.** config.py default upload_allowed_types = ['csv','xlsx','pdf','doc','docx'], but _EXTRACTORS only has handlers for pdf,docx,xlsx,csv,txt,md,html,htm " 'doc' is missing, so _EXTRACTORS['doc'] raises KeyError after the file has been persisted.

**Failure scenario.** User uploads legacy.doc. documents.py passes the allowlist check (doc is allowed), writes the file to disk under storage/<uid>/<doc_id>.doc, commits the Document row, and enqueues ingestion. Ingest calls extract_text(file_path); extract() re-checks settings.ALLOWED_UPLOAD_EXTS (doc passes) then does fn = _EXTRACTORS['doc'] -> KeyError. The pipeline catches it, marks the doc 'failed', but the .doc file remains on disk forever and the document is stuck in failed state. No user-facing validation rejects .doc up front.

**Evidence.** _EXTRACTORS = {"pdf": _ext_pdf, "docx": _ext_docx, "xlsx": _ext_xlsx, "csv": _ext_csv, "txt": _ext_text, "md": _ext_text, "html": _ext_html, "htm": _ext_html}
# config default: upload_allowed_types = ["csv","xlsx","pdf","doc","docx"] -- 'doc' has no handler

**Suggested fix.** Either remove 'doc' from the default upload_allowed_types, or add a _ext_doc handler (e.g. via olefile/antiword/textract) and ensure _EXTRACTORS keys are a superset of upload_allowed_types. Validate at request time that an extractor exists for the extension before persisting the file.

**Verification rationale.** Confirmed by reading the actual code. config.py:52-54 sets upload_allowed_types default to ["csv","xlsx","pdf","doc","docx"] (includes "doc"). extractors.py:110-119 defines _EXTRACTORS with keys pdf,docx,xlsx,csv,txt,md,html,htm " no "doc" handler. extract() at extractors.py:122-129 only re-checks settings.ALLOWED_UPLOAD_EXTS (which includes doc) then does `fn = _EXTRACTORS[ext]` with no guard, raising KeyError for "doc". documents.py:51-97 validates only against _settings.upload_allowed_types, persists the file to storage/<uid>/<doc_id>.doc, commits the Document row, and enqueues _run_ingest with no extractor-existence check. pipeline.py:97-108 catches the exception and marks the document "failed"; documents.py:120-121 logs ingest.background.error. The .doc file remains on disk and the document is stuck in failed state. No up-front validation rejects .doc. Reproduces exactly as claimed.

**Notes.** Claimed file/line (extractors.py:110) is exact " _EXTRACTORS dict begins at line 110, and the KeyError occurs at line 127 (`fn = _EXTRACTORS[ext]`). config.py default at lines 52-54. documents.py upload validation at line 52. All locations match the finding.


---

### `extension-only-allowlist-no-content-validation`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Confidence | high |
| Category | upload |
| Location | `backend/app/api/documents.py:51` |
| Status | **Fixed** |

**Summary.** The only gate is _ext_of(file.filename) against upload_allowed_types; file.content_type and file magic bytes are never inspected, and the CSV/TXT extractors read any bytes with errors='replace', so mis-typed or malicious payloads are accepted and indexed.

**Failure scenario.** Attacker uploads an ELF binary or a pickled payload as malware.csv. ext='csv' is in the allowlist, so it is stored and ingested. _ext_csv opens it with errors='replace', producing garbage text that is chunked, embedded, and stored. This both pollutes the retrieval corpus and lets attackers store arbitrary data under the guise of an allowed type. Content-type spoofing (e.g. image/png) is likewise never validated against the extension.

**Evidence.** ext = _ext_of(file.filename or "")
if ext not in _settings.upload_allowed_types:
 raise HTTPException(415, ...)
# no file.content_type check, no magic-byte sniff anywhere

**Suggested fix.** Validate file.content_type against an allowlist mapped per extension, and sniff the first bytes (python-magic / filetype) to confirm the file matches the claimed extension before persisting. For text types, reject NUL bytes or non-text content.

**Verification rationale.** Confirmed in backend/app/api/documents.py:51-56 " the only upload gate is _ext_of(file.filename) checked against settings.upload_allowed_types; file.content_type is never read and no magic-byte sniffing exists. The bytes are persisted verbatim (lines 63-77) then ingested. In backend/app/services/ingestion/extractors.py:73-81, _ext_csv opens with errors='replace' and _ext_text reads with errors='replace', so any binary uploaded as .csv is decoded lossily into garbage text that gets chunked, embedded, and stored in the retrieval corpus. backend/app/core/config.py:52-54 confirms the default allowlist includes 'csv'. The failure scenario (upload ELF/pickle as malware.csv -> passes allowlist -> stored -> ingested as garbage) reproduces exactly as described.

**Notes.** File/line in the finding (documents.py:51) is exact. Severity medium is appropriate: the attacker must already be an authenticated user (CurrentUserId dependency), impact is corpus pollution and arbitrary-data storage rather than RCE or privilege escalation; no code execution path was found since extractors only read bytes as text.


---

### `file-written-before-commit-orphan-on-failure`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Confidence | high |
| Category | async-bug |
| Location | `backend/app/api/documents.py:64` |
| Status | **Fixed** |

**Summary.** The 25MB file is fully written to its permanent storage_path, then session.add(doc)+commit happens; if the commit raises (DB error, RLS issue, asyncpg connection drop) the file is left on disk with no Document row, and no cleanup runs.

**Failure scenario.** Upload completes, file is written to storage_dir/<uid>/<doc_id>.csv. session.commit() then fails (e.g., DB connection lost, unique violation, RLS policy misconfig). The HTTPException path is not taken (it's outside the with block), so storage_path is not unlinked; the file leaks on disk with no DB record referencing it, accumulating across failures. The same applies if the BackgroundTask _run_ingest fails before finding the document (silent return at documents.py:113-114) " the doc row stays 'uploaded' forever and the file is never reprocessed or cleaned.

**Evidence.** with open(storage_path, "wb") as f:
 ...
 f.write(chunk)

doc = Document(...)
session.add(doc)
await session.commit() # if this raises, storage_path is orphaned
# no try/except + storage_path.unlink around the commit

**Suggested fix.** Wrap the commit in try/except and unlink storage_path on failure, or write to a temp path and rename after commit succeeds. Add a janitor that scans storage_dir for files not referenced by any Document row. Also surface background ingest startup failures and add a re-ingest/retry mechanism for documents stuck in 'uploaded'.

**Verification rationale.** Read backend/app/api/documents.py:64-90. The full file is written to its final storage_path (user_dir/<doc_id>.<ext>) inside the `with open(storage_path, "wb")` block at lines 64-77. The only cleanup there is the size-limit branch at line 72 (`storage_path.unlink(missing_ok=True)`). Immediately after, lines 79-90 build the Document, session.add(doc), and `await session.commit()` with NO try/except around the commit. Verified there is no mitigating wrapper: get_db (database.py:88-91) does not auto-rollback or wrap the commit; get_user_db (dependencies.py:20-34) only resets the RLS GUC in finally, it does not unlink files. Therefore if session.commit() raises (asyncpg connection drop, unique violation, RLS misconfig, DB error), the exception propagates out as a 500, storage_path stays on disk, no Document row references it, and no janitor exists to reclaim it. Files accumulate across commit failures " a genuine orphan-on-failure resource leak. Severity medium is appropriate (operational leak/disk waste, not data corruption or a security hole).

**Notes.** Line 64 in the finding is the start of the `with open(storage_path, "wb")` block; the commit that can fail is at line 89 (not 64). The core bug is real as described. The secondary part of the failure scenario is partially inaccurate: the _run_ingest 'doc is None' early-return at documents.py:113-114 cannot leave a row 'stuck in uploaded forever' because if doc is None the row does not exist. The real 'stuck in uploaded' risk is via the except handler at lines 120-121 which swallows ingest_document errors and only logs them, leaving rows in status='uploaded' with no retry " a related but distinct issue from the orphaned-file bug.


---

### `no-page-row-or-chunk-cap-resource-exhaustion`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Confidence | high |
| Category | dos |
| Location | `backend/app/services/ingestion/extractors.py:73` |
| Status | **Fixed** |

**Summary.** _ext_csv reads the whole file into a single rows list; _ext_xlsx iterates every row of every sheet into memory; _ext_pdf extracts all pages and joins them; the pipeline then embeds every chunk in one encode() batch with no chunk-count limit, so a maximal allowed upload can OOM the worker.

**Failure scenario.** Attacker uploads a 25MB CSV of tiny cells that expands to ~5M rows. _ext_csv builds a 5M-row list; chunk_tabular produces hundreds of thousands of chunks; pipeline.py calls encode(texts, normalize=True) on the entire list in one shot, allocating a (chunks x 384) float32 matrix and running the sentence-transformers model on all chunks at once, OOMing the worker and crashing the background ingest task. A 25MB PDF with millions of pages or huge decompressed streams similarly exhausts memory in _ext_pdf before any chunking cap applies.

**Evidence.** def _ext_csv(path):
 with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
 reader = csv.reader(f)
 rows = [[c for c in row] for row in reader] # entire file in memory
 return ExtractionResult(mode="tabular", tables=[(path.stem, rows)])
...
vecs = encode(texts, normalize=True) # pipeline.py:64 " all chunks at once, no cap

**Suggested fix.** Cap extracted text size and chunk count (e.g., drop documents whose extracted text exceeds N chars or whose chunk count exceeds M; truncate or reject). Stream the CSV reader rather than building the full rows list. Embed in bounded mini-batches with a max total chunk count per document.

**Verification rationale.** Confirmed by reading the actual code. extractors.py:73-77 (_ext_csv) builds the entire CSV into a Python list-of-lists with no row cap: `rows = [[c for c in row] for row in reader]`. extractors.py:43-48 (_ext_pdf) joins all pages with no page/text cap. extractors.py:64-67 (_ext_xlsx) builds full in-memory rows per sheet. The 25MB byte cap is enforced at documents.py:70, but that is exactly the attack premise: a 25MB CSV of tiny cells (e.g. `a,a,a\n` = 6 bytes/row) yields ~4M rows whose list-of-lists representation (~88B/row list + ~50B per str cell) exceeds ~1GB, OOMing the worker before chunking. A 25MB PDF with highly compressed FlateDecode streams can decompress to gigabytes via pypdf. pipeline.py:62-65 then calls encode(texts) on all chunks and `emb_list = [v.tolist() for v in vecs]`, materializing N ---384 Python floats (~24B each) " for ~67K chunks that's ~600MB more. ingest runs in-process via FastAPI BackgroundTasks (documents.py:97), so an OOM kills the whole worker (the except at documents.py:120 cannot recover from OOM). This is a genuine authenticated DoS with strong byte 'memory amplification. One minor wording nit: the claim says encode() runs the model on all chunks 'in one shot'; embedding.py:50-56 actually passes batch_size=32 to sentence-transformers, so model inference is mini-batched " but the returned numpy array is the full (N,384) matrix and the subsequent .tolist() conversion is the real memory bomb, so the outcome described is correct.

**Notes.** Line 73 (start of _ext_csv) is correct; the load-bearing line is 76 `rows = [[c for c in row] for row in reader]`. Same unbounded-list pattern at extractors.py:64-67 (_ext_xlsx) and extractors.py:43-48 (_ext_pdf). The encode() step is at pipeline.py:63-65, not pipeline.py:64. The 'embeds in one batch' framing is slightly imprecise " model inference is internally mini-batched (batch_size=32 at embedding.py:52) " but the full (N,384) numpy result and the .tolist() conversion still materialize the entire embedding set in memory, so the DoS conclusion holds. Requires authentication (CurrentUserId) and an upload within the 25MB byte cap, which bounds but does not eliminate the risk " medium severity is appropriate.


---

### `upload-body-not-capped-before-buffering`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Confidence | high |
| Category | dos |
| Location | `backend/app/api/documents.py:63` |
| Status | **Fixed** |

**Summary.** The 25MB size limit is checked inside the application read loop, but Starlette/python-multipart has already spooled the whole request body to a SpooledTemporaryFile before the handler runs, so an attacker can consume gigabytes of temp disk per request regardless of upload_max_bytes.

**Failure scenario.** Attacker POSTs a 50GB multipart file to /api/documents with filename='x.csv'. python-multipart streams the entire body into a SpooledTemporaryFile before the handler runs. Only once the handler's read loop reaches 25MB does it unlink and raise 413 " but the 50GB temp file already consumed disk. Repeating this exhausts /tmp and the storage volume, crashing the service for all users. No Content-Length pre-check or framework max-body-size middleware exists (main.py registers only CORS).

**Evidence.** size = 0
with open(storage_path, "wb") as f:
 while True:
 chunk = await file.read(1024 * 1024)
 if not chunk:
 break
 size += len(chunk)
 if size > _settings.upload_max_bytes:
 f.close()
 storage_path.unlink(missing_ok=True)
 raise HTTPException(... 413 ...)
 f.write(chunk)

**Suggested fix.** Add a request-body-size guard before accepting the upload: a middleware that rejects requests with Content-Length > upload_max_bytes, and/or use Starlette's streaming form parser with an early-abort when accumulated bytes exceed the limit. Also set --limit-max-request-body (uvicorn) or a reverse-proxy (nginx) client_max_body_size matching upload_max_bytes.

**Verification rationale.** The application-layer gap is real and verified. In backend/app/api/documents.py (lines 63-77), the 25MB upload_max_bytes check lives inside the handler's read loop. Starlette's formparsers.py (lines 147, 230) confirms multipart parts are written to a SpooledTemporaryFile (max_size=1MB then spills to disk) during await request.form(), which FastAPI runs as part of the UploadFile=File(...) dependency BEFORE the handler executes. So the entire multipart body is buffered to a temp file before the read loop's size check can fire. backend/main.py confirms only CORSMiddleware is registered " no app-level body-size guard, no uvicorn --limit-max-request-body.

The reason for downgrading high 'medium: the claim's headline scenario (gigabytes of temp disk per request) is mitigated at the primary production ingress. infra/nginx.conf and infra/nginx-prod.conf both set `client_max_body_size 30m`, and infra/docker-compose.yml fronts the API with nginx on port 80. Through that path, an upload is capped at 30MB by the proxy, so worst-case temp disk per request is ~30MB (only 5MB over the app limit) " not a meaningful DoS.

The bug remains exploitable as described via the directly-exposed backend port: docker-compose.yml line 118 exposes "8000:8000" on the host (bypassing nginx), and the web-dev profile proxies Vite ' localhost:8000 (also bypassing nginx). Against port 8000, a 50GB POST is fully spooled to temp disk before the 25MB check fires, exactly as the failure scenario describes. The application does not protect itself and relies entirely on an external proxy that is not always in the request path.

Suggested fix (app-level body-size middleware and/or uvicorn --limit-max-request-body matching upload_max_bytes, plus removing the public 8000 port exposure) is appropriate defense-in-depth.

**Notes.** File/line accurate: backend/app/api/documents.py line 63 (read loop at lines 63-77). Mitigation exists at infra/nginx.conf:9 and infra/nginx-prod.conf:24 (client_max_body_size 30m), which the claim did not acknowledge. Exploitable via the directly-exposed port 8000 (infra/docker-compose.yml:118) and in dev mode (web-dev profile proxies to :8000), but the primary prod nginx ingress caps body at 30MB, limiting worst-case temp disk to ~30MB per request through that path. Severity corrected high 'medium accordingly.


---

### `background-ingest-errors-swallowed`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Confidence | medium |
| Category | async-bug |
| Location | `backend/app/api/documents.py:101` |
| Status | **Fixed** |

**Summary.** The background ingest task catches every exception with only a log, and returns silently if the Document row is gone, so transient DB failures or a deleted-while-queued document leave the document row in 'uploaded' status indefinitely with no retry and no user feedback.

**Failure scenario.** Upload succeeds; Document is committed with status='uploaded'; BackgroundTask fires. Before it runs, a concurrent delete or a DB connectivity blip means the select returns None. _run_ingest returns at line 114 without changing status or retrying. The Document row stays 'uploaded' forever (never 'failed'), and the file sits on disk. Even on real exceptions, only a log line is emitted " no retry, no status transition, no alert.

**Evidence.** async with user_scoped_session(user_id) as session:
 res = await session.execute(select(Document).where(...))
 doc = res.scalar_one_or_none()
 if doc is None:
 return
 await ingest_document(...)
except Exception as exc: # noqa: BLE001
 log.error("ingest.background.error", doc_id=str(doc_id), error=str(exc))

**Suggested fix.** On missing doc, log a warning and ensure the file is cleaned up if no row exists. On exception, mark the document 'failed' (with error_message) rather than only logging, and add a bounded retry/backoff (tenacity is already a dependency). Surface stuck-'uploaded' documents via a health/monitoring check.

**Verification rationale.** The core defect exists at backend/app/api/documents.py lines 120-121: `_run_ingest`'s `except Exception` only emits `log.error(...)` and does not mark the document 'failed' or retry. If an exception is raised BEFORE `ingest_document` is invoked (e.g., a transient DB error during `await session.execute(select(Document)...)` at lines 107-111, or failure creating the user_scoped_session), the document row remains in 'uploaded' indefinitely with no status transition, no retry, and no user feedback. tenacity is indeed available for a bounded retry.

However, the finding's stated mechanism is partly wrong, which lowers confidence: (1) The "select returns None" path (lines 113-114) is essentially only the already-deleted case " a "DB connectivity blip" raises rather than returning None, and a completed concurrent delete removes both the row (delete_document line 238) and the file (line 235), so nothing is "stuck in 'uploaded'." Returning silently there is benign/correct. (2) The "no status transition" claim is false for exceptions raised INSIDE `ingest_document`: pipeline.py lines 94-109 catch, roll back, mark the document 'failed' with error_message, commit, then re-raise " so the document is transitioned to 'failed' before reaching `_run_ingest`'s except. The genuinely un-handled case is exceptions in `_run_ingest` before `ingest_document` runs (the select/session phase).

**Notes.** The bug is real but narrower than described. The claimed failure_scenario conflates two paths: (a) `doc is None` (line 113-114) " this is the already-deleted case, which is benign (row+file are gone via delete_document lines 235/238), and a "DB blip" does NOT cause None here, it raises; (b) the `except` at line 120-121 " this is the real gap, but only for exceptions raised before `ingest_document` is called (the select/session phase), because `ingest_document` (pipeline.py lines 94-109) already transitions the document to 'failed' and commits on its own exceptions. Corrected summary: `_run_ingest`'s except (line 120-121) only logs and does not mark 'failed' or retry for pre-pipeline exceptions (e.g., transient DB error on the select at lines 107-111 or session-creation failure), leaving the row stuck in 'uploaded'. Line 101 is correct as the function start; the load-bearing lines are 120-121.


---

### `csv-text-ingest-accepts-binary`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Confidence | high |
| Category | upload |
| Location | `backend/app/services/ingestion/extractors.py:73` |
| Status | **Fixed** |

**Summary.** _ext_csv, _ext_text, and _ext_html open files as UTF-8 with errors='replace', so any binary content (executable, archive, encrypted blob) uploaded under a .csv/.txt/.md/.html extension is converted to garbage unicode text and indexed, polluting the corpus and bypassing the intent of the type allowlist.

**Failure scenario.** Attacker uploads a 25MB encrypted zip renamed to dump.csv. _ext_csv reads it with errors='replace', producing ~25MB of replacement characters. clean_text normalizes whitespace but the content is non-empty, so chunking proceeds and ~thousands of garbage chunks with embeddings and keywords are written to the user's corpus, inflating storage and retrieval noise.

**Evidence.** with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
 reader = csv.reader(f)
 rows = [[c for c in row] for row in reader]
# _ext_text: path.read_text(encoding="utf-8", errors="replace")

**Suggested fix.** Reject files containing NUL bytes or a high ratio of replacement characters; for CSV, validate CSV dialect and require a minimum valid-row ratio before indexing. Apply a content-sniff check (magic bytes) before falling back to text extraction.

**Verification rationale.** Verified in extractors.py:73-77 (_ext_csv), :80-81 (_ext_text), :84-107 (_ext_html): all decode with errors='replace', converting invalid bytes to U+FFFD. Upload validation in api/documents.py:51-56 only checks the file extension against upload_allowed_types " there is no magic-byte / content-sniff / MIME check anywhere in the upload path; only size is capped (lines 70-76). clean_text in services/text.py:19 uses _NONPRINT_RE=[^\x09\x0a\x0d\x20-\x7e - ], and U+FFFD (0xFFFD) falls inside - so it is preserved, not stripped. pipeline.py:58 only rejects when chunks is empty. For a binary file renamed to .csv, csv.reader splits on randomly-occurring 0x0A bytes producing many short garbage rows; chunk_tabular (chunker.py:90-129) builds header+row chunks from them; for .txt/.md/.html the garbage prose passes through chunk_prose. Embeddings and keywords are then computed and stored. The scenario is reproducible. Severity remains low: the attack requires an authenticated user, the garbage is written to the attacker's OWN corpus (user_id is enforced throughout, RLS applies), and per-file size is bounded by upload_max_bytes " there is no cross-user impact or privilege escalation. The 'bypasses the intent of the type allowlist' framing is mildly overstated since the allowlist is extension-based by design, but the core defect (errors='replace' silently masking binary-as-text) is genuine.

**Notes.** Line 73 is correct for _ext_csv. _ext_text is at lines 80-81 and _ext_html at lines 84-107 (all in the same file). The fix should also cover clean_text not stripping U+FFFD, but the suggested fix (magic-byte sniff + NUL/replacement-char ratio check) is the right place to address it.


---

### `pdf-no-page-cap-decompression`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Confidence | high |
| Category | dos |
| Location | `backend/app/services/ingestion/extractors.py:38` |
| Status | **Fixed** |

**Summary.** _ext_pdf loops over every page and calls extract_text() with no page-count limit and no per-page output cap; a 25MB PDF can be crafted to have millions of pages or streams that decompress to huge text, blocking the ingest worker.

**Failure scenario.** Attacker uploads crafted.pdf (under 25MB) that decompresses to tens of thousands of pages or to per-page text that runs to megabytes. _ext_pdf iterates all pages, accumulating a giant parts list and joining into a multi-GB string, blocking the background task and exhausting memory. pypdf 5.0.1 does not impose such caps.

**Evidence.** reader = PdfReader(str(path))
parts: list[str] = []
for i, page in enumerate(reader.pages):
 try:
 parts.append(page.extract_text() or "")
 except Exception as exc:
 log.warning(...)
return ExtractionResult(mode="prose", text="\n\n".join(parts), meta={"pages": len(parts)})

**Suggested fix.** Enforce a max page count (reject/truncate beyond N pages) and a per-page character cap; fail ingestion with a clear error if exceeded. Consider streaming pages and short-circuiting once extracted text reaches a sane maximum.

**Verification rationale.** Verified against the actual code. extractors.py:38-48 defines _ext_pdf which loops `for i, page in enumerate(reader.pages)` (line 43), appending `page.extract_text() or ""` (line 45) to an unbounded `parts` list and joining into a single string at line 48, with no page-count limit and no per-page/per-total character cap. The only upstream mitigation is a 25MB raw-byte cap on the compressed upload enforced in documents.py:70 (upload_max_bytes = 25*1024*1024, config.py:51), which does NOT bound the decompressed page count or extracted text length " confirming the decompression/amplification DoS vector. pipeline.py:51 calls extract_text(file_path) inside a background task (_run_ingest, documents.py:101-121) with no timeout. pypdf imposes no such caps. The scenario (crafted <25MB PDF with many pages / huge decompressed text blocking the ingest worker and exhausting memory) is genuinely reproducible. Severity "low" is correct because exploitation requires an authenticated user (CurrentUserId) and only blocks a background task rather than the request thread, with impact bounded to a single worker. Suggested fix (max page count + per-page char cap + short-circuit on total extracted text) is appropriate.

**Notes.** Line 38 is the function-def line; the unbounded loop is at lines 43-47 and the join at line 48. File/path are correct. No mitigation exists elsewhere " the 25MB cap in documents.py:70 only bounds the compressed upload, not decompressed pages/text, so the amplification vector stands.


---
