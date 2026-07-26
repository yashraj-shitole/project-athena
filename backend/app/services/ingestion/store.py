"""Bulk persistence of chunks (and their embeddings) into Postgres.

This is the single I/O boundary for chunk writes. It:
  - sets the RLS GUC for the user (per-user isolation)
  - bulk-inserts chunks with embeddings in one transaction
  - invalidates the user's retrieval cache so stale hits don't surface

Two insert paths:
  * ``insert_chunks`` — the original ORM path; used as the
    SQLite fallback (the unit suite, which can't speak asyncpg
    COPY) and as the slow-path for any non-Postgres dialect.
  * ``copy_chunks`` — the fast path. Uses asyncpg's
    ``copy_to_table`` (TEXT COPY) to ship all rows in one
    round-trip. Faster than ``add_all`` + ``flush`` for 5k+ chunks
    and avoids per-row ORM overhead. Postgres-only; falls back to
    the ORM path on other dialects.

The pipeline (per-batch loop) calls ``copy_chunks``; the per-batch
size is the embedding batch (32 chunks by default), so we never
hold more than a few hundred KB of Python records in memory at
once.

Text COPY vs binary COPY
------------------------
asyncpg's ``copy_records_to_table`` is *binary* only and has no
encoder for pgvector's `vector` type (OID 16386). We use
``copy_to_table`` with the text format instead — pre-encoding
each field into its PG text representation (``[v1,v2,...]`` for
vectors, ``[a,b]`` for int4range, etc.). Text COPY is ~1.2× slower
than binary COPY for plain text columns, but the difference is
dwarfed by the per-row ORM overhead we skip entirely. The result
is still a single round-trip with no per-row SQL parsing, which is
the whole point.
"""
from __future__ import annotations

import io
import json
import uuid
from datetime import datetime
from typing import Any, List, Sequence, Tuple

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import invalidate_user
from app.core.config import settings
from app.core.database import set_rls_user
from app.core.logging import get_logger
from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.services.ingestion.chunker import Chunk

log = get_logger(__name__)


async def delete_existing_chunks(
    session: AsyncSession,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """Wipe prior chunks for a document (idempotent re-ingest)."""
    await session.execute(
        delete(DocumentChunk).where(
            DocumentChunk.document_id == document_id,
            DocumentChunk.user_id == user_id,
        )
    )


async def set_ingest_bulk_load_gucs(session: AsyncSession) -> None:
    """Set transaction-local GUCs that speed up HNSW bulk-load during
    the per-batch chunk COPY.

    - ``hnsw.ef_insert`` lowered (default 40 → 10): insert-time graph
      search depth. ~2-4x faster HNSW inserts; search recall is governed
      separately by ``hnsw.ef_search`` (left at its default), so this
      is a pure insert-side win.
    - ``maintenance_work_mem`` raised (typically 64MB → 256MB): HNSW
      graph build is memory-hungry.

    Uses ``set_config(..., true)`` (``SET LOCAL``) so the values bind to
    the pipeline's current transaction and reset at its commit — no leak
    onto the pooled connection, retrieval and other requests unaffected.

    No-op on non-Postgres dialects (the SQLite unit suite can't speak
    asyncpg COPY and doesn't have HNSW) and when the configured value is
    ``0`` / empty (use the server default). Caller must invoke this
    *inside* the ingestion transaction (the pipeline does, before the
    COPY loop).

    Defensive about the bind: the unit suite drives the pipeline with a
    stub session that has no ``get_bind`` (it patches ``copy_chunks``
    for the same reason). We treat "can't confirm Postgres" as a no-op
    rather than crashing the whole ingest.
    """
    get_bind = getattr(session, "get_bind", None)
    if get_bind is None:
        return
    try:
        dialect = get_bind().dialect.name
    except Exception:  # noqa: BLE001
        return
    if dialect != "postgresql":
        return
    ef_insert = settings.INGEST_HNSW_EF_INSERT
    if ef_insert and ef_insert > 0:
        await session.execute(
            text("SELECT set_config('hnsw.ef_insert', :value, true)"),
            {"value": str(int(ef_insert))},
        )
    work_mem = (settings.INGEST_MAINTENANCE_WORK_MEM or "").strip()
    if work_mem:
        await session.execute(
            text("SELECT set_config('maintenance_work_mem', :value, true)"),
            {"value": work_mem},
        )


def _format_vector(vec: Sequence[float]) -> str:
    """Format a vector as the pgvector COPY wire format: '[v1,v2,...]'.

    The pgvector extension reads its COPY text representation
    directly. asyncpg's ``copy_records_to_table`` does NOT
    understand pgvector's type adapter (it only knows the built-in
    Postgres types), so we have to pre-encode here.
    """
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"


def _format_int4range(rng: Tuple[int, int] | None) -> str | None:
    """Format an inclusive [a, b] int4range for COPY, or NULL."""
    if rng is None:
        return None
    a, b = rng
    return f"[{a},{b}]"


def _escape_copy(value: str) -> str:
    """Escape a string for Postgres COPY text format.

    The text-COPY protocol is tab-separated columns, LF-terminated
    rows. Within a field, backslash escapes a tab / newline /
    carriage-return / backslash. NULL is the literal ``\\N`` (the
    special marker, not the two characters backslash + N).
    """
    return (
        value.replace("\\", "\\\\")
        .replace("\t", "\\t")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def _format_pg_array(values: Sequence[str]) -> str:
    """Format a Python list of strings as a Postgres text[] literal.

    Postgres accepts the literal ``{a,b,"c d"}`` form on COPY
    input. Strings containing ``,``, ``"``, ``{``, ``}``, or
    whitespace are quoted with double-quotes; embedded double-
    quotes are escaped by doubling.
    """
    parts: list[str] = []
    for v in values:
        s = str(v)
        if any(ch in s for ch in [",", '"', "{", "}", " ", "\t", "\n"]):
            s = '"' + s.replace('"', '""') + '"'
        parts.append(s)
    return "{" + ",".join(parts) + "}"


def _row_to_text(parts: tuple) -> bytes:
    """Encode one chunk row as a single tab-separated line of bytes
    in PG COPY text format. ``\\N`` for NULL. Newline at the end so
    asyncpg can flush it to the COPY stream.

    The `parts` tuple order is the COPY column order — see
    ``_row_to_copy_record``. We special-case the two non-scalar
    columns (keywords as text[], metadata as JSONB) so they end
    up as Postgres literals rather than Python ``repr()`` strings.
    """
    # Column order (matches _row_to_copy_record and the COPY
    # `columns=` argument in `_copy_chunks_pg`).
    #   0 document_id   uuid    — str
    #   1 user_id       uuid    — str
    #   2 chunk_index   int     — int
    #   3 content       text    — str (escape)
    #   4 embedding     vector  — str (pre-formatted "[v1,v2,...]")
    #   5 keywords      text[]  — list[str]   (PG array literal)
    #   6 page_number   int     — int | None
    #   7 row_range     int4range — str | None
    #   8 char_start    int     — int | None
    #   9 char_end      int     — int | None
    #  10 metadata      jsonb   — str (JSON-encoded)
    fields: list[str] = []
    for i, p in enumerate(parts):
        if p is None:
            fields.append(r"\N")
        elif i == 5:
            # keywords: text[] literal.
            fields.append(_format_pg_array(p))
        elif i == 10:
            # metadata: JSONB. We've already JSON-encoded in
            # _row_to_copy_record; the COPY wire format is the
            # JSON string verbatim. Escape any literal tab /
            # newline / backslash that might appear in the JSON
            # (the encoder's default separators don't include
            # those, but escape defensively).
            fields.append(_escape_copy(p))
        elif isinstance(p, bool):
            fields.append("t" if p else "f")
        elif isinstance(p, (int, float)):
            fields.append(str(p))
        elif isinstance(p, str):
            fields.append(_escape_copy(p))
        else:
            fields.append(_escape_copy(str(p)))
    return ("\t".join(fields) + "\n").encode("utf-8")


def _row_to_copy_record(
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    chunk_index: int,
    chunk: Chunk,
    embedding: Sequence[float],
    keywords: Sequence[str],
) -> tuple:
    """Convert a chunk + its embedding into a tuple matching the
    column order of `copy_chunks` below. The `id` and `created_at`
    columns are omitted — they have server defaults (gen_random_uuid
    and now(), respectively) so the COPY doesn't need to supply
    them."""
    return (
        str(document_id),
        str(user_id),
        int(chunk_index),
        chunk.content,
        _format_vector(embedding),
        list(keywords),
        chunk.page_number,
        _format_int4range(chunk.row_range),
        chunk.char_start,
        chunk.char_end,
        json.dumps(chunk.meta or {}),
    )


async def _copy_chunks_pg(
    session: AsyncSession,
    *,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    chunks: Sequence[Chunk],
    embeddings: Sequence[Sequence[float]],
    keywords: Sequence[Sequence[str]],
) -> None:
    """Fast path: asyncpg text-format COPY.

    Caller (copy_chunks) is responsible for the dialect check and
    for calling ``set_rls_user`` on the same session first.

    asyncpg's ``copy_to_table`` accepts a file-like object (or
    async-iterable of bytes) and writes it straight into a COPY
    FROM STDIN stream. We pre-encode each row in Python so the
    network sees one tab-separated line per chunk. The whole
    payload fits in a single ``BytesIO`` — for 32 chunks at
    ~500 bytes/row that's 16KB, which we hold briefly before
    sending. (We *could* stream with an async-iterable, but the
    per-batch size is small enough that buffering is simpler and
    avoids interleaving encode/COPY work.)

    This is text COPY (not binary) because asyncpg's binary COPY
    has no encoder for pgvector's OID 16386 — text COPY works for
    any type pgvector knows how to read, and the cost is a ~1.2×
    slowdown vs binary on plain text columns, which is dwarfed
    by the per-row ORM overhead we skip.
    """
    conn = await session.connection()
    raw = await conn.get_raw_connection()
    # `get_raw_connection` returns the pool-proxied
    # `_ConnectionFairy`; the real asyncpg `Connection` is
    # at `.driver_connection`.
    candidate = getattr(raw, "driver_connection", raw)
    if candidate is None or not _is_asyncpg_conn(candidate):
        raise RuntimeError(
            "copy_chunks called on a non-asyncpg session — this is a bug"
        )

    buf = io.BytesIO()
    for idx, (chunk, emb, kws) in enumerate(
        zip(chunks, embeddings, keywords)
    ):
        buf.write(
            _row_to_text(
                _row_to_copy_record(
                    document_id=document_id,
                    user_id=user_id,
                    chunk_index=idx,
                    chunk=chunk,
                    embedding=emb,
                    keywords=kws,
                )
            )
        )
    buf.seek(0)

    await candidate.copy_to_table(
        "document_chunks",
        source=buf,
        columns=(
            "document_id",
            "user_id",
            "chunk_index",
            "content",
            "embedding",
            "keywords",
            "page_number",
            "row_range",
            "char_start",
            "char_end",
            "metadata",  # NB: the SQL column is `metadata`, not `meta`.
        ),
    )


def _is_asyncpg_conn(obj: Any) -> bool:
    """True if `obj` is an asyncpg.Connection (we duck-type it)."""
    return hasattr(obj, "copy_to_table")


async def copy_chunks(
    session: AsyncSession,
    *,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    chunks: Sequence[Chunk],
    embeddings: Sequence[Sequence[float]],
    keywords: Sequence[Sequence[str]],
) -> None:
    """Bulk-insert chunks into ``document_chunks``.

    For Postgres dialects this uses asyncpg's text COPY (fast).
    For anything else (SQLite, the unit suite) it falls back to the
    ORM ``add_all`` + ``flush`` path. Caller is expected to have
    already called ``set_rls_user`` on the same session; this
    function does not set the GUC.

    Empty input is a no-op.
    """
    if not chunks:
        return
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        await _copy_chunks_pg(
            session,
            document_id=document_id,
            user_id=user_id,
            chunks=chunks,
            embeddings=embeddings,
            keywords=keywords,
        )
    else:
        # Fallback: ORM bulk insert. Sets RLS again defensively in
        # case the caller didn't.
        await set_rls_user(session, user_id)
        objs: list[DocumentChunk] = []
        for idx, (chunk, emb, kws) in enumerate(zip(chunks, embeddings, keywords)):
            objs.append(
                DocumentChunk(
                    document_id=document_id,
                    user_id=user_id,
                    chunk_index=idx,
                    content=chunk.content,
                    embedding=list(emb),
                    keywords=list(kws),
                    page_number=chunk.page_number,
                    row_range=chunk.row_range,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    meta=chunk.meta,
                )
            )
        session.add_all(objs)
        await session.flush()
    log.info(
        "store.chunks.inserted",
        document_id=str(document_id),
        count=len(chunks),
        path="copy" if dialect == "postgresql" else "orm",
    )


async def insert_chunks(
    session: AsyncSession,
    *,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    chunks: Sequence[Chunk],
    embeddings: Sequence[Sequence[float]],
    keywords: Sequence[Sequence[str]],
) -> List[DocumentChunk]:
    """Insert all chunks + their vectors in a single transaction.

    Kept as the public API for back-compat. ``copy_chunks`` is the
    preferred path on Postgres; this is a thin wrapper that
    delegates there.
    """
    if not chunks:
        return []
    await set_rls_user(session, user_id)
    await delete_existing_chunks(session, document_id, user_id)
    await copy_chunks(
        session,
        document_id=document_id,
        user_id=user_id,
        chunks=chunks,
        embeddings=embeddings,
        keywords=keywords,
    )
    return []  # the old return value (the ORM list) is no longer built


async def mark_document_status(
    session: AsyncSession,
    document: Document,
    status: str,
    *,
    error_message: str | None = None,
    page_count: int | None = None,
    chunk_count: int | None = None,
    embedding_model: str | None = None,
    current_stage: str | None = None,
    stage_progress: dict | None = None,
    started_at: datetime | None = None,
    processed_at: datetime | None = None,
    processing_time_ms: int | None = None,
) -> None:
    """Update a document row's lifecycle status (and any side metadata).

    All extra fields are keyword-only and default-None so this stays
    drop-in compatible with the call sites that previously passed just
    `(session, document, status)`. `None` means "leave alone" — a
    non-None value overwrites the column. `stage_progress` is a dict
    that replaces the JSONB column wholesale (callers pass the
    full per-stage map, not a diff).
    """
    document.status = status
    if error_message is not None:
        document.error_message = error_message
    if page_count is not None:
        document.page_count = page_count
    if chunk_count is not None:
        document.chunk_count = chunk_count
    if embedding_model is not None:
        document.embedding_model = embedding_model
    if current_stage is not None:
        document.current_stage = current_stage
    if stage_progress is not None:
        document.stage_progress = stage_progress
    if started_at is not None:
        document.started_at = started_at
    if processed_at is not None:
        document.processed_at = processed_at
    if processing_time_ms is not None:
        document.processing_time_ms = processing_time_ms
    await session.flush()


async def mark_document_progress(
    session: AsyncSession,
    document: Document,
    *,
    current_stage: str,
    stage_progress: dict,
    chunk_count: int | None = None,
    page_count: int | None = None,
    started_at: datetime | None = None,
) -> None:
    """Update only the mid-pipeline progress fields.

    Unlike `mark_document_status`, this NEVER changes `status` — the
    doc stays in `processing` (or whatever it was) while we tick the
    stage and percentage columns. `stage_progress` replaces the column
    wholesale (it's a small dict). `chunk_count`/`page_count` are
    accepted as a convenience for the moments they become known mid-
    pipeline.
    """
    document.current_stage = current_stage
    document.stage_progress = stage_progress
    if chunk_count is not None:
        document.chunk_count = chunk_count
    if page_count is not None:
        document.page_count = page_count
    if started_at is not None:
        document.started_at = started_at
    await session.flush()


async def finalize_indexing(
    session: AsyncSession,
    user_id: uuid.UUID,
) -> None:
    """After a document is indexed, drop its retrieval cache entries."""
    await invalidate_user(user_id, prefix=settings.CACHE_PREFIX_RETRIEVAL)
