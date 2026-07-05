# Async, Cache & Database Session Hygiene

_3 finding(s) in this dimension._

Findings in async/event-loop hygiene and DB session lifecycle: a blocking `encode()` call on the event loop, cache calls that turned a Redis outage into a 500, and `reset_rls_user` silently swallowing all exceptions. Fixed by offloading `encode` to a thread, making cache get/set/delete fail-open, and logging `reset_rls_user` failures.

---

### `blocking-encode-in-async`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Confidence | high |
| Category | async-bug |
| Location | `backend/app/services/retrieval/hybrid.py:82` |
| Status | **Fixed** |

**Summary.** hybrid_search is async but invokes the synchronous, CPU/GPU-heavy model.encode() inline, stalling the entire event loop for every vector query.

**Failure scenario.** Concurrent requests all serialize on the encode() call: while one request embeds a query (tens to hundreds of ms of pure CPU), every other async handler (other chat requests, health checks, Redis ops) is blocked, causing latency spikes and effective request starvation under modest load.

**Evidence.** hybrid.py:82 `qvec = encode([query], normalize=True)` -- encode() in embedding.py:50 calls `model.encode(...)` (sentence-transformers, synchronous, releases the GIL only intermittently); no asyncio.to_thread/loop.run_in_executor wrapping.
embedding.py:41-57 `def encode(...)` is a plain sync function with no await/executor.

**Suggested fix.** Run the encoder off the event loop: `qvec = await asyncio.to_thread(encode, [query], normalize=True)` in hybrid.py (and similarly wrap encode_one at its async call sites), or move all embedding work to a dedicated thread/process pool.

**Verification rationale.** Confirmed by reading the actual code. `hybrid_search` is declared `async def` (hybrid.py:43) and at line 82 it calls `qvec = encode([query], normalize=True)` with no `await asyncio.to_thread(...)` / `run_in_executor` wrapping. hybrid.py has no `asyncio` import at all (grep for "asyncio" returned no matches). `encode` in embedding.py:41-57 is a plain synchronous function that directly invokes `model = get_model(); vecs = model.encode(...)` (line 50), which is sentence-transformers' blocking CPU-bound encode. The only lock in embedding.py is the `threading.Lock` (line 22) guarding the lazy model singleton load, not the encode call itself, so it provides no event-loop relief. `lexical.search_lexical` is properly awaited (line 70), proving the surrounding code knows to await async I/O " but the embedding step is left sync inline. Under concurrent requests, this serializes on the GIL-holding CPU-bound encode, blocking every other async handler for tens-to-hundreds of ms per query. Severity medium is appropriate (latency/starvation under load, not a correctness or security issue).

**Notes.** File/line in the claim are exact: backend/app/services/retrieval/hybrid.py line 82, embedding.py lines 41-57 (model.encode at line 50). Suggested fix is correct: wrap with `await asyncio.to_thread(encode, [query], normalize=True)` (needs `import asyncio` added to hybrid.py), and apply the same to other async call sites of encode_one/encode.


---

### `cache-no-fail-open-redis-outage`

| Field | Value |
|---|---|
| Severity | **MEDIUM** |
| Confidence | high |
| Category | cache |
| Location | `backend/app/services/retrieval/search.py:72` |
| Status | **Fixed** |

**Summary.** The cache is meant to be a best-effort optimization (counters in cache.py:34-39 are explicitly best-effort), but the cache reads/writes in retrieve() are not wrapped, so any Redis error fails the whole request instead of degrading to a live DB search.

**Failure scenario.** Redis hiccups, restarts, or hits max connections; get_json (search.py:72) raises redis.exceptions.ConnectionError, which propagates out of retrieve() and the orchestrator returns HTTP 500 even though the database is healthy and could answer the query directly.

**Evidence.** search.py:72 `cached = await get_json(_settings.CACHE_PREFIX_RETRIEVAL, cache_key)` -- no try/except
search.py:98 `await set_json(_settings.CACHE_PREFIX_RETRIEVAL, cache_key, serializable, ttl=...)` -- no try/except
cache.py:47-58 get_json/set_json propagate exceptions from get_client().get/set
cache.py:34-39 _bump explicitly swallows Redis errors ('metrics are best-effort') -- inconsistent with the read/write path

**Suggested fix.** Wrap the cache read and write in retrieve() in try/except and log+continue: `try: cached = await get_json(...) except Exception: cached = None` and `try: await set_json(...) except Exception: log.warning('retrieval.cache.set_failed', ...)`. Cache must fail open, mirroring _bump.

**Verification rationale.** Confirmed in backend/app/services/retrieval/search.py:72 and :98 " both get_json and set_json are awaited with no try/except. In backend/app/core/cache.py, get_json (lines 47-53) and set_json (lines 56-58) call get_client().get/.set with no error handling, so a redis.exceptions.ConnectionError (Redis down/restart/max-connections) propagates out of retrieve(). The orchestrator (backend/app/services/orchestrator/agent.py:367/485) only has a top-level try/except that yields sse.run_error, and builtin.py:40 calls retrieve() with no wrapping at all " so neither caller fails open to the DB. cache.py:34-39 _bump explicitly swallows Redis errors ("metrics are best-effort; do not fail the request"), establishing that the cache is meant to be best-effort, which makes the unguarded read/write path an inconsistent reliability bug: a cache outage turns a healthy-DB retrieval into a 500/run-error instead of degrading to a live hybrid_search.

**Notes.** Path correction: the finding cites "cache.py" " the actual file is backend/app/core/cache.py (the cache import in search.py is `from app.core.cache import get_json, set_json`). Line numbers in the finding (search.py:72 and :98; cache.py:34-39 and 47-58) are all accurate. Severity medium is appropriate: real resilience bug with a clean suggested fix (wrap the get_json read and set_json write in try/except, log+continue, mirroring _bump), but not critical since it requires a Redis outage to trigger and the system still functions when Redis is healthy.


---

### `reset-rls-swallows-all-exceptions`

| Field | Value |
|---|---|
| Severity | **LOW** |
| Confidence | high |
| Category | rls |
| Location | `backend/app/core/database.py:71` |
| Status | **Fixed** |

**Summary.** reset_rls_user catches bare Exception and passes; if RESET fails for a transient reason (not a closed session), the GUC is left set on the connection that returns to the pool, silently reproducing the leak.

**Failure scenario.** RESET throws a non-fatal error (e.g. a momentary connection glitch that does not tear down the session); the bare except discards it and the connection is handed back to the pool with app.current_user_id still set, leaking the prior user's RLS context to the next request.

**Evidence.** database.py:69-74 `try: await session.execute(text(f"RESET {_RLS_GUC}")) except Exception: pass` -- broad swallow with only a comment, no logging

**Suggested fix.** Narrow the catch to the specific 'session already closed' conditions (e.g. ResourceClosedError / InterfaceError) and log a warning for any other exception so a failed RESET is observable; ensure the connection is discarded from the pool on unexpected RESET failures.

**Verification rationale.** Verified at backend/app/core/database.py:69-74. `reset_rls_user` does `try: await session.execute(text(f"RESET {_RLS_GUC}")) except Exception: pass` " a bare swallow with no logging and no connection invalidation. The leak is concrete and reproducible via a more common trigger than the claim's "connection glitch": if the request handler raises any DB error during `yield` (e.g., IntegrityError), the PostgreSQL transaction enters the "current transaction is aborted, commands ignored until end of transaction block" state, so the `RESET` in the `finally` (in `get_user_db` at backend/app/api/dependencies.py:30-34 and `user_scoped_session` at database.py:80-85) raises and is swallowed. `set_rls_user` uses session-level `SET` (not `SET LOCAL`, per the comment at database.py:50-54), so the GUC survives the subsequent `session.close()` ROLLBACK. The engine (database.py:29-34) sets `pool_pre_ping=True` but no `pool_reset_on_return` (grep confirmed none configured), so SQLAlchemy's default rollback-only reset on pool return does NOT issue RESET ALL/DISCARD ALL and the GUC persists on the checked-in connection. A later request that checks out this connection via `get_db` (database.py:88-91, used by get_current_user at dependencies.py:40-45 and any route typed Annotated[AsyncSession, Depends(get_db)]) runs queries with the leaked prior-user GUC, so RLS policies filter as the wrong user. `pool_pre_ping` only runs SELECT 1 on checkout and does not reset GUCs, so it does not mitigate. The claim's line (71), file, evidence, and suggested_fix are all accurate; only the triggering example is narrower than reality. Severity kept at low because database.py:3-4 documents RLS as "defense-in-depth isolation," implying a primary query-level isolation layer bounds the real impact.

**Notes.** Line/file/summary/suggested_fix all accurate. The claim's example trigger ("momentary connection glitch that does not tear down the session") is a weaker framing of the real, more common trigger: any in-request DB error (e.g., IntegrityError) that puts the transaction into PostgreSQL's "aborted transaction" state, which causes the RESET to raise. The mechanism (GUC left set on a pooled connection, silently reproducing the leak to a later request) is correct.


---
