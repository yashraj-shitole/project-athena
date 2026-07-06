"""Document upload, listing, retrieval, deletion (FR-05..09)."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.api.dependencies import CurrentUserId, DbSession
from app.core.cache import invalidate_user
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.schemas.chunk import ChunkPublic
from app.schemas.document import DocumentListResponse, DocumentPublic
from app.services.ingestion import events
from app.services.ingestion.pipeline import ingest_document
from app.services.llm.streamer import sse

log = get_logger(__name__)
_settings = get_settings()
router = APIRouter(prefix="/documents", tags=["documents"])


# Stable id of the "uploading" stage for the wire format. The pipeline
# itself doesn't emit this (upload completes before the background
# task starts); we send it on the initial STATE so the UI can show
# the progress bar at 0% from the moment a tab connects.
_STAGE_UPLOADING = "uploading"
_STAGE_EXTRACTING = "extracting"
_STAGE_CHUNKING = "chunking"
_STAGE_EMBEDDING = "embedding"
_STAGE_INDEXING = "indexing"
_STAGE_COMPLETED = "completed"
_STAGE_FAILED = "failed"

# Weight per stage for the `overall_pct` derived field the UI uses
# to drive the progress bar. `uploading` is tiny (most of the time
# is in the SSE-receiving client's own upload), and embedding gets
# the lion's share because that's where the CPU work lives.
_STAGE_WEIGHTS: dict[str, int] = {
    _STAGE_UPLOADING: 5,
    _STAGE_EXTRACTING: 20,
    _STAGE_CHUNKING: 15,
    _STAGE_EMBEDDING: 40,
    _STAGE_INDEXING: 20,
}


def _overall_pct(stage: str | None, stage_progress: dict | None) -> int:
    """Compute an overall 0..100 percent from per-stage progress.

    `stage_progress` is `{stage: 0..100}`. We weight each stage by
    `_STAGE_WEIGHTS` and sum, then clamp to [0, 100]. The current
    stage is bumped to the *minimum* of its stored pct and the
    midpoint of its weight — that way a doc stuck mid-stage reads as
    "<current stage>'s share so far", not "stage X is at 100% but
    nothing past it has run yet".
    """
    if not stage or not stage_progress:
        return 0
    total_weight = sum(_STAGE_WEIGHTS.values()) or 1
    pct = 0
    for st, weight in _STAGE_WEIGHTS.items():
        if st in stage_progress:
            # For a finished stage (stage != current and pct == 100)
            # the full weight counts. For the active stage we use the
            # stored pct (a partial value), and for everything before
            # it we count its full weight.
            if st == stage:
                p = min(100, max(0, int(stage_progress.get(st, 0) or 0)))
                pct += int(round(weight * p / 100))
            else:
                # If a later stage is reporting, treat earlier stages
                # as 100% regardless of stored value.
                p = 100
                pct += int(round(weight * p / 100))
    return max(0, min(100, int(round(pct * 100 / total_weight))))


def _state_payload(doc: Document) -> dict:
    """Build the SSE `STATE` payload from a Document row.

    `overall_pct` is derived (we never persist it). `page_count`,
    `chunk_count`, `embedding_model`, and `processing_time_ms` are
    all `Optional` in the model — we leave them as `None` for docs
    that haven't reached the relevant stage.
    """
    sp = dict(doc.stage_progress or {})
    return {
        "document_id": str(doc.id),
        "status": doc.status,
        "current_stage": doc.current_stage,
        "stage_progress": sp,
        "overall_pct": _overall_pct(doc.current_stage, sp),
        "chunk_count": doc.chunk_count,
        "page_count": doc.page_count,
        "embedding_model": doc.embedding_model,
        "error_message": doc.error_message,
        "started_at": doc.started_at,
        "processed_at": doc.processed_at,
        "processing_time_ms": doc.processing_time_ms,
        # Identity fields used by the UI's "filename · type · size"
        # header — cheap to include and avoids a second GET.
        "filename": doc.filename,
        "file_type": doc.file_type,
        "size_bytes": doc.size_bytes,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
    }


def _ext_of(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


# Magic-byte signatures for the file types we accept. Used to reject
# renamed/mismatched uploads (e.g. an .exe renamed to .pdf) at the
# boundary instead of trusting the client-supplied extension.
_MAGIC = {
    "pdf": (b"%PDF",),
    "docx": (b"PK\x03\x04",),  # OOXML / zip
    "xlsx": (b"PK\x03\x04",),
    # .doc, .csv, .txt, .md, .html have no reliable magic bytes — they're
    # text-based and validated structurally by the extractor instead.
}


def _matches_magic(path: Path, ext: str) -> bool:
    sigs = _MAGIC.get(ext)
    if not sigs:
        return True  # no signature to check
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return False
    return any(head.startswith(s) for s in sigs)


@router.post(
    "",
    response_model=DocumentPublic,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    user_id: CurrentUserId = None,  # type: ignore[assignment]
    session: DbSession = None,  # type: ignore[assignment]
) -> Document:
    """FR-05 / FR-06: validate → persist → enqueue ingestion."""
    ext = _ext_of(file.filename or "")
    if ext not in _settings.upload_allowed_types:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type: .{ext}",
        )

    # Early size rejection based on Content-Length so we don't buffer a
    # huge body to disk before noticing it's over the cap. The streaming
    # check below still defends against lying/absent Content-Length.
    declared = file.size
    if declared is not None and declared > _settings.upload_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {_settings.upload_max_bytes} bytes",
        )

    user_dir = _settings.storage_dir / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    doc_id = uuid.uuid4()
    storage_path = user_dir / f"{doc_id}.{ext}"

    size = 0
    try:
        with open(storage_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _settings.upload_max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"File exceeds {_settings.upload_max_bytes} bytes",
                    )
                f.write(chunk)
    except HTTPException:
        storage_path.unlink(missing_ok=True)
        raise
    except Exception:
        storage_path.unlink(missing_ok=True)
        raise

    # Content matches the claimed extension? Reject renamed binaries.
    if not _matches_magic(storage_path, ext):
        storage_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"File content does not match extension .{ext}",
        )

    doc = Document(
        id=doc_id,
        user_id=user_id,
        filename=file.filename or f"upload.{ext}",
        file_type=ext,
        storage_path=str(storage_path),
        size_bytes=size,
        status="uploaded",
    )
    session.add(doc)
    try:
        await session.commit()
        await session.refresh(doc)
    except Exception:
        # DB commit failed — don't leave an orphaned file on disk.
        storage_path.unlink(missing_ok=True)
        raise

    # Invalidate any cached retrieval results for this user (the corpus
    # is about to change).
    await invalidate_user(str(user_id))

    # Kick off the ingestion pipeline in the background.
    background.add_task(_run_ingest, doc_id, user_id, str(storage_path))
    return doc


async def _run_ingest(doc_id: uuid.UUID, user_id: uuid.UUID, storage_path: str) -> None:
    """Run the ingestion pipeline on a fresh session (own transaction).

    A `status_cb` closure is built and passed into the pipeline. The
    closure translates pipeline events to SSE wire-format events
    (via `sse.sse()`) and publishes them on the in-process event bus
    so any connected SSE handler (current or future) sees them. On
    a terminal status (`indexed` / `failed`), the closure also calls
    `events.mark_terminal` so the SSE handler can close its stream
    after the last event.

    On any error we mark the document `failed` with the error message so
    the user sees the failure in the UI instead of a row stuck in
    `uploaded` / `processing` forever.
    """
    from app.core.database import user_scoped_session

    async def status_cb(event: str, payload: dict) -> None:
        """Bridge pipeline events → SSE wire events → event bus.

        The pipeline already persists each transition to the DB
        (via `store.mark_document_status`); the cb's job is purely
        to broadcast and, on terminal, tell the bus to close.
        """
        # Map the pipeline's event names to the SSE wire format.
        # The wire-format `STAGE` event signals a stage transition;
        # `PROGRESS` is mid-stage; `TERMINAL` closes the stream.
        # The pipeline's `processing` event is implicit (we're
        # already in `processing` from the upload handler) and we
        # don't emit anything for it.
        sse_event: str | None = None
        sse_payload: dict = {"document_id": str(doc_id)}
        is_terminal = False
        terminal_status: str | None = None

        if event == "processing":
            # First event after the doc is enqueued. We send a STAGE
            # so the UI knows the doc is now moving.
            sse_event = "STAGE"
            sse_payload.update({"from_stage": None, "to_stage": _STAGE_EXTRACTING})
        elif event == "extracted":
            sse_event = "STAGE"
            sse_payload.update({"from_stage": _STAGE_EXTRACTING, "to_stage": _STAGE_CHUNKING})
        elif event == "chunked":
            sse_event = "STAGE"
            sse_payload.update({"from_stage": _STAGE_CHUNKING, "to_stage": _STAGE_EMBEDDING})
        elif event == "embedding":
            sse_event = "PROGRESS"
            sse_payload.update(
                {
                    "stage": _STAGE_EMBEDDING,
                    "current": payload.get("current"),
                    "total": payload.get("total"),
                    "percent": payload.get("percent"),
                }
            )
        elif event == "embedded":
            sse_event = "STAGE"
            sse_payload.update({"from_stage": _STAGE_EMBEDDING, "to_stage": _STAGE_INDEXING})
        elif event == "indexed":
            sse_event = "TERMINAL"
            is_terminal = True
            terminal_status = "indexed"
            sse_payload.update(
                {
                    "status": "indexed",
                    "chunk_count": payload.get("chunks"),
                    "embedding_model": payload.get("embedding_model"),
                    "processing_time_ms": payload.get("processing_time_ms"),
                }
            )
        elif event == "failed":
            sse_event = "TERMINAL"
            is_terminal = True
            terminal_status = "failed"
            sse_payload.update(
                {
                    "status": "failed",
                    "error_message": payload.get("error"),
                    "processing_time_ms": payload.get("processing_time_ms"),
                }
            )
        # Unknown event names are ignored (forward compatibility).

        if sse_event is None:
            return
        bytes_ = sse(sse_event, sse_payload)
        await events.publish(doc_id, bytes_)
        if is_terminal:
            await events.mark_terminal(doc_id)

    try:
        async with user_scoped_session(user_id) as session:
            res = await session.execute(
                select(Document).where(
                    Document.id == doc_id, Document.user_id == user_id
                )
            )
            doc = res.scalar_one_or_none()
            if doc is None:
                return
            await ingest_document(
                session,
                document=doc,
                file_path=Path(storage_path),
                status_cb=status_cb,
            )
    except Exception as exc:  # noqa: BLE001
        log.error("ingest.background.error", doc_id=str(doc_id), error=str(exc))
        # Best-effort: flip the doc to `failed` so the UI can show it.
        try:
            from sqlalchemy import update as sa_update

            async with user_scoped_session(user_id) as session:
                # Stamp timing + terminal stage so the row is complete
                # even if the failure happened *outside* the pipeline
                # (e.g. an exception in the cb or the publish path).
                now = datetime.now(timezone.utc)
                started = (
                    await session.execute(
                        select(Document.started_at).where(
                            Document.id == doc_id,
                            Document.user_id == user_id,
                        )
                    )
                ).scalar_one_or_none()
                elapsed_ms = 0
                if started is not None:
                    elapsed_ms = max(0, int((now - started).total_seconds() * 1000))
                await session.execute(
                    sa_update(Document)
                    .where(Document.id == doc_id, Document.user_id == user_id)
                    .values(
                        status="failed",
                        current_stage="failed",
                        error_message=str(exc)[:500],
                        processed_at=now,
                        processing_time_ms=elapsed_ms,
                    )
                )
                await session.commit()
                # Notify any connected SSE handler that the doc is done.
                terminal_bytes = sse(
                    "TERMINAL",
                    {
                        "document_id": str(doc_id),
                        "status": "failed",
                        "error_message": str(exc)[:500],
                        "processing_time_ms": elapsed_ms,
                    },
                )
                await events.publish(doc_id, terminal_bytes)
                await events.mark_terminal(doc_id)
        except Exception as exc2:  # noqa: BLE001
            log.error("ingest.background.mark_failed", doc_id=str(doc_id), error=str(exc2))


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    user_id: CurrentUserId,
    session: DbSession,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status_filter: str | None = Query(None, alias="status"),
) -> DocumentListResponse:
    """FR-07: list documents for the current user."""
    stmt = select(Document).where(Document.user_id == user_id)
    if status_filter:
        stmt = stmt.where(Document.status == status_filter)
    total_stmt = select(func.count()).select_from(Document).where(
        Document.user_id == user_id
    )
    if status_filter:
        total_stmt = total_stmt.where(Document.status == status_filter)
    total = (await session.execute(total_stmt)).scalar_one()
    rows = (
        await session.execute(stmt.order_by(Document.created_at.desc()).offset(offset).limit(limit))
    ).scalars()
    return DocumentListResponse(items=list(rows), total=int(total or 0))


@router.get("/{doc_id}", response_model=DocumentPublic)
async def get_document(
    doc_id: uuid.UUID,
    user_id: CurrentUserId,
    session: DbSession,
) -> Document:
    """FR-08: single document detail (RLS also enforces ownership)."""
    res = await session.execute(
        select(Document).where(
            Document.id == doc_id, Document.user_id == user_id
        )
    )
    doc = res.scalar_one_or_none()
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )
    return doc


@router.get("/{doc_id}/chunks", response_model=list[ChunkPublic])
async def list_document_chunks(
    doc_id: uuid.UUID,
    user_id: CurrentUserId,
    session: DbSession,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[ChunkPublic]:
    """Inspect the indexed chunks for a document (debugging / transparency)."""
    # Ensure the document exists and belongs to the user.
    res = await session.execute(
        select(Document.id).where(
            Document.id == doc_id, Document.user_id == user_id
        )
    )
    if res.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )
    res = await session.execute(
        select(DocumentChunk)
        .where(
            DocumentChunk.document_id == doc_id,
            DocumentChunk.user_id == user_id,
        )
        .order_by(DocumentChunk.chunk_index)
        .offset(offset)
        .limit(limit)
    )
    rows = list(res.scalars())
    return [
        ChunkPublic(
            id=r.id,
            document_id=r.document_id,
            chunk_index=r.chunk_index,
            content=r.content,
            keywords=list(r.keywords or []),
            page_number=r.page_number,
            meta=dict(r.meta or {}),
        )
        for r in rows
    ]


@router.delete(
    "/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_document(
    doc_id: uuid.UUID,
    user_id: CurrentUserId,
    session: DbSession,
):
    """FR-09: delete the document, its chunks, and the stored file."""
    res = await session.execute(
        select(Document).where(
            Document.id == doc_id, Document.user_id == user_id
        )
    )
    doc = res.scalar_one_or_none()
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )
    # Best-effort file removal
    try:
        Path(doc.storage_path).unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("documents.delete.file", path=doc.storage_path, error=str(exc))
    await session.delete(doc)
    await session.commit()
    await invalidate_user(str(user_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{doc_id}/events")
async def document_events(
    doc_id: uuid.UUID,
    user_id: CurrentUserId,
    session: DbSession,
) -> StreamingResponse:
    """SSE: per-document status stream.

    On connect:
      - 404 if the doc doesn't exist or isn't owned by the user.
      - 200 SSE otherwise. We send a STATE immediately so a fresh
        browser tab can render the current state without a separate
        GET, then we replay any events from the broker's ring buffer
        (so a tab that was backgrounded catches up), then we live-
        forward events until the doc reaches a terminal state
        (`indexed` / `failed`) at which point we close.

    Wire format (single `data:` line per event):
      STATE     full row mirror + overall_pct
      STAGE     from/to stage names on a transition
      PROGRESS  mid-stage {stage, current, total, percent}
      TERMINAL  final event; SSE stream closes after this
    """
    res = await session.execute(
        select(Document).where(
            Document.id == doc_id, Document.user_id == user_id
        )
    )
    doc = res.scalar_one_or_none()
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )

    # Capture the doc's progress snapshot up-front. The ORM session
    # that loaded `doc` is closed by the time the streaming response
    # actually runs (FastAPI's request dependency teardown happens
    # when the response body iterator starts). Reading the row's
    # columns from a detached instance triggers a refresh that fails
    # with DetachedInstanceError. Snapshot to a dict and use that.
    state_snapshot = _state_payload(doc)
    sub = await events.subscribe(doc_id)
    is_terminal = await events.is_terminal(doc_id)
    history = await events.history(doc_id)

    async def event_iter() -> AsyncIterator[bytes]:
        try:
            # 1) Always send STATE first — gives the client an
            #    authoritative row snapshot, even if `history` is
            #    empty (e.g. a doc that just transitioned to
            #    `indexed` between request and subscribe).
            yield sse("STATE", state_snapshot)
            # 2) Replay the ring buffer so the client catches up on
            #    anything since the last connect.
            for evt in history:
                yield evt
            if is_terminal:
                # Nothing more to come; the STATE was the only useful
                # event. (We could send a synthetic TERMINAL here, but
                # `history` already contains the real one if the
                # pipeline ran to completion.)
                return
            # 3) Live forward until the broker closes us.
            while True:
                # Race the queue against a slow-consumer close. If
                # the broker marked us `closed` (queue overflowed),
                # exit the loop and let the response close.
                if sub.closed:
                    return
                get_task = asyncio.create_task(sub.queue.get())
                try:
                    chunk = await get_task
                except asyncio.CancelledError:
                    return
                if chunk is None:
                    # End-of-stream sentinel.
                    return
                yield chunk
        finally:
            await events.unsubscribe(doc_id, sub)

    return StreamingResponse(
        event_iter(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/{doc_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DocumentPublic,
)
async def retry_document(
    doc_id: uuid.UUID,
    user_id: CurrentUserId,
    session: DbSession,
    background: BackgroundTasks,
) -> Document:
    """Re-run ingestion for a document that failed (or never finished).

    404 if the doc doesn't exist; 409 if it's currently processing or
    already indexed (idempotent retries are a future feature — for
    now, we re-ingest only on `uploaded` or `failed`).

    Resets progress columns (current_stage, stage_progress, error
    message, timing) but keeps the doc row itself, so the file and
    the user-visible identity (id, filename) stay stable across
    retries.
    """
    res = await session.execute(
        select(Document).where(
            Document.id == doc_id, Document.user_id == user_id
        )
    )
    doc = res.scalar_one_or_none()
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )
    if doc.status in ("processing", "indexed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot retry document in status '{doc.status}'",
        )

    # Reset progress + identity columns. We KEEP chunk_count and
    # embedding_model as historical info — they'll be overwritten on
    # the next success, but in the meantime the UI sees the old
    # numbers (better than blanks) if the user reloads mid-retry.
    doc.status = "processing"
    doc.current_stage = "extracting"
    doc.stage_progress = {"extracting": 0}
    doc.error_message = None
    now = datetime.now(timezone.utc)
    doc.started_at = now
    doc.processed_at = None
    doc.processing_time_ms = None
    await session.commit()
    await session.refresh(doc)

    # Notify any connected SSE handler that the doc is moving again.
    # (Browsers refreshing on the retry button will get a fresh SSE
    # stream; the existing one, if still open, sees this STAGE.)
    await events.publish(
        doc_id,
        sse(
            "STAGE",
            {
                "document_id": str(doc_id),
                "from_stage": "failed",
                "to_stage": "extracting",
            },
        ),
    )
    # Mark not-terminal so subscribers that reconnect after the
    # previous terminal get live updates again. (We only schedule
    # this if the doc was previously terminal; otherwise it's a
    # no-op.)
    if await events.is_terminal(doc_id):
        await events.reopen(doc_id)

    background.add_task(_run_ingest, doc.id, user_id, doc.storage_path)
    return doc
