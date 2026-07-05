# Retrieval & Prompt-Injection

_4 finding(s) in this dimension._

Findings in retrieval correctness / hygiene: a dead bigram guard, a cache key that omitted `top_k`, an unvalidated embedding dimension, and a dead `to_tsquery` helper. Fixed by correcting the bigram gap check, hashing `top_k` into the cache key, validating the query embedding dimension against the configured `embedding_dim`, and removing the dead helper.

---

### `bigram-generation-dead`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Confidence | high |
| Category | logic-bug |
| Location | `backend/app/services/ingestion/keywords.py:66` |
| Status | **Fixed** |

**Summary.** The bigram adjacency guard uses `e2 - e <= 2` (end-to-end distance) but since every token is at least 3 chars long, e2-e is always >= 3, so the condition is never true and no bigram is ever appended; the 'unigrams + bigrams' strategy degrades to unigrams only.

**Failure scenario.** For tokens t (end e) and nxt (start s2, end e2), e2-e = len(nxt) + (s2-e) >= 3 always (min token length 3). The check `e2 - e <= 2` is always False, so the bigram branch on line 67 never executes. Keyword diversity/phrase coverage is silently degraded for every chunk.

**Evidence.** keywords.py:66 `if e2 - e <= 2 and nxt not in _STOPWORDS:` then line 67 `out.append((f"{t} {nxt}", s, e2))`. `_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")` (line 44) enforces min length 3.

**Suggested fix.** Use the gap between tokens: `if s2 - e <= 2 and nxt not in _STOPWORDS:` (where s2 is the start offset of nxt).

**Verification rationale.** Confirmed in Y:\AI_Projects\project-athena\backend\app\services\ingestion\keywords.py. Line 44 `_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}")` enforces a minimum token length of 3 (1 letter + 2+ more chars). Tokens come from re.finditer, which yields non-overlapping matches in increasing position order, so for consecutive tokens `s2 >= e` always, i.e. `s2 - e >= 0`. Line 66 checks `e2 - e <= 2`, but `e2 - e = (s2 - e) + len(nxt) >= 0 + 3 = 3`, so the condition is always False and line 67 (`out.append((f"{t} {nxt}", s, e2))`) never executes. No bigram is ever produced; the documented "unigrams + bigrams" strategy (docstring lines 3-5) degrades silently to unigrams only. The comment on line 64-65 ("bigrams that span a huge gap") confirms the intent was to measure the inter-token gap, which is `s2 - e`, not `e2 - e`. Suggested fix `s2 - e <= 2` is correct.

**Notes.** File/line accurate: keywords.py:66. Severity medium retained (silent quality degradation of a documented feature; unigram path still functions, so not high/critical). Fix: replace `e2 - e <= 2` with `s2 - e <= 2` on line 66.


---

### `cache-key-omits-top-k`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Confidence | high |
| Category | logic-bug |
| Location | `backend/app/services/retrieval/search.py:38` |
| Status | **Fixed** |

**Summary.** _cache_key is built only from (user_id, normalized query); top_k is not part of the key, so two calls with the same query but different top_k collide, and cached[:top_k] silently returns fewer items than requested when the cached payload was stored with a smaller top_k.

**Failure scenario.** User A calls retrieve with top_k=4 ' cache stores 4 items. User A later calls retrieve with the same query and top_k=10 ' cache hit returns cached[:10] which is only 4 items, silently under-serving the request and degrading answer quality without any error.

**Evidence.** search.py: `_cache_key` (lines 38-41) hashes only user_id + normalized query. `return cached[:top_k]` (line 75). hybrid_search already slices to top_k before caching (hybrid.py:103 `return fused[:top_k]`), so the stored payload is bounded by the first call's top_k.

**Suggested fix.** Either include top_k in the cache key (`f"{user_id}:{digest}:{top_k}"`) or always cache a generous pool (e.g. fetch max(top_k, DEFAULT_POOL)) and slice on read.

**Verification rationale.** Confirmed against the actual code. In search.py, `_cache_key` (lines 38-41) builds the Redis key as `f"{user_id}:{digest}"` where `digest` is a hash of only the normalized query string " top_k is not part of the key. On a cache hit, line 75 does `return cached[:top_k]`, which silently truncates to whatever was stored. hybrid.py:103 confirms `return fused[:top_k]` " hybrid_search already slices to the first call's top_k before the result is cached, so the cached payload is bounded by the first caller's top_k. Reproducing the scenario: User A calls retrieve(query="X", top_k=4) ' cache stores 4 items. User A later calls retrieve(query="X", top_k=10) ' cache_key collides (same user_id+query), cache hit returns cached[:10] which is only 4 items. The request for 10 is silently under-served with no error, degrading answer quality. The cache key omits top_k exactly as claimed.

**Notes.** File/line accurate as claimed (search.py:38 for _cache_key, line 75 for cached[:top_k], hybrid.py:103 for fused[:top_k]). The suggested fix to always cache a generous pool and slice on read is the more robust option; simply adding top_k to the key would reduce hit rate but also fix correctness.


---

### `vector-dim-not-validated`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Confidence | medium |
| Category | dos |
| Location | `backend/app/services/retrieval/vector.py:27` |
| Status | **Fixed** |

**Summary.** search_vector only checks `if not query_embedding` (empty list) but never checks len(query_embedding) == EMBED_DIM; a mismatched-dimension vector is sent to pgvector, which raises a hard SQL error per request rather than degrading gracefully.

**Failure scenario.** A misconfigured caller (or a model swap that changes EMBED_DIM without re-embedding chunks) passes a 384-dim query vector against a 768-dim column. Every vector/hybrid search request raises a Postgres error until config is fixed " full outage of the retrieval path with no graceful fallback to lexical.

**Evidence.** vector.py:27 `if not query_embedding: return []` " no dimension check. embedding.py:48 returns shape (0, EMBED_DIM) for empty; encode_one (line 65) returns `[0.0]*EMBED_DIM` only when texts is empty, not on dim mismatch.

**Suggested fix.** Validate `len(query_embedding) == settings.EMBED_DIM` at the top of search_vector and raise a typed error (or fall back to lexical) instead of sending an incompatible vector to Postgres.

**Verification rationale.** Confirmed at vector.py:27 the only guard is `if not query_embedding: return []` " there is no `len(query_embedding) == settings.EMBED_DIM` check. The query vector is produced by `encode([query])` (hybrid.py:82) using the SentenceTransformer loaded from `settings.EMBED_MODEL_NAME`, while the pgvector column is `Vector(settings.EMBED_DIM)` (chunk.py:51). `config.py:59-61` exposes `embedding_dim` and `embedding_model_name` as INDEPENDENT config fields, so an operator can set them inconsistently (e.g., point embedding_model_name at a 768-dim model but leave embedding_dim=384, or swap the model after chunks are already embedded without re-embedding+migrating). In that state every vector/hybrid search sends a mismatched-dim vector to pgvector, which raises a hard `data_exception`. Confirmed there is no graceful fallback: hybrid.py:84-89 calls `vector.search_vector` with no try/except, and search.py:77-82 has none either, so the exception propagates and the already-fetched lexical hits (hybrid.py:70) are discarded. The bug (missing dim validation + no degradation on pgvector error) genuinely exists and is reproducible under the stated misconfiguration. Caveat lowering confidence: the failure requires operator misconfiguration, not attacker input " the user only supplies a text query and cannot control the vector dimension, so the 'dos / retrieval-injection' framing is inaccurate.

**Notes.** File/line accurate (vector.py:27; embedding.py:48 and 64; chunk.py:51). Severity kept at low (matches original). Category/framing correction: this is NOT retrieval-injection or attacker-exploitable DoS " the query_embedding dimension is determined entirely by the server-side model/config, never by user input. It is a config-drift / model-swap-without-re-embedding robustness gap that surfaces as a hard Postgres error with no lexical fallback. Suggested fix (validate len(query_embedding) == settings.EMBED_DIM at top of search_vector and raise a typed error or fall back to lexical) is reasonable defense-in-depth; alternatively wrap the search_vector call in hybrid.py in try/except to degrade to the lexical hits already in hand.


---

### `dead-to-tsquery-helper`

| Field | Value |
|---|---|
| Severity | **INFO** |
| Confidence | high |
| Category | logic-bug |
| Location | `backend/app/services/retrieval/lexical.py:20` |
| Status | **Fixed** |

**Summary.** to_tsquery() is defined to 'build a safe tsquery string' but is never called anywhere; its try/except wraps a static return string with no exception-raising code, and the actual SQL in search_lexical hardcodes websearch_to_tsquery directly, giving a false impression that tsquery sanitization exists.

**Failure scenario.** A maintainer reads to_tsquery, assumes it sanitizes special chars (!, :, &, \|) before they reach the DB, and later wires user input through a path that calls to_tsquery expecting protection " but the function just returns a fixed SQL fragment string and performs no sanitization. (Note: websearch_to_tsquery itself is safe against tsquery injection because :q is parameter-bound, but the helper implies extra handling that isn't real.)

**Evidence.** lexical.py:20-33 defines to_tsquery returning the literal string `"websearch_to_tsquery('english', :q)"` inside a try/except that can never raise. Grep shows no callers. search_lexical (line 64) inlines the same expression.

**Suggested fix.** Delete the unused to_tsquery helper, or actually use it from search_lexical so there is a single source of truth for the tsquery construction.

**Verification rationale.** Read lexical.py:20-33. to_tsquery(query) returns the static string "websearch_to_tsquery('english', :q)" inside a try/except that cannot raise (a bare string return never throws), making the plainto_tsquery fallback unreachable and the docstring's claimed "split on whitespace, drop junk" / "falls back" strategy unimplemented. search_lexical (lines 64, 67) hardcodes websearch_to_tsquery directly instead of calling the helper. Grep across the repo confirms no callers of the local to_tsquery exist in backend/ (only docs and the unrelated sqlalchemy venv). The helper is dead, misleading code that implies sanitization it does not perform. The finding's own caveat is correct: websearch_to_tsquery is safe because :q is parameter-bound in search_lexical, so this is not an injection vulnerability " just dead/misleading code. Severity info is appropriate; the only nit is the "logic-bug" category label is a stretch (this is maintainability/dead-code), but the substance of the finding is accurate.

**Notes.** File/line accurate: Y:\AI_Projects\project-athena\backend\app\services\retrieval\lexical.py line 20 (def to_tsquery). No correction needed. Category is more accurately "dead-code/maintainability" than "logic-bug", but the finding as described is real.


---
