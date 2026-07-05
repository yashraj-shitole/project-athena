"""Document upload, listing, retrieval, deletion (FR-05..09)."""
from __future__ import annotations

import uuid
from pathlib import Path

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

from app.api.dependencies import CurrentUserId, DbSession
from app.core.cache import invalidate_user
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.schemas.chunk import ChunkPublic
from app.schemas.document import DocumentListResponse, DocumentPublic
from app.services.ingestion.pipeline import ingest_document

log = get_logger(__name__)
_settings = get_settings()
router = APIRouter(prefix="/documents", tags=["documents"])


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

    On any error we mark the document `failed` with the error message so
    the user sees the failure in the UI instead of a row stuck in
    `uploaded` / `processing` forever.
    """
    from app.core.database import user_scoped_session

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
            )
    except Exception as exc:  # noqa: BLE001
        log.error("ingest.background.error", doc_id=str(doc_id), error=str(exc))
        # Best-effort: flip the doc to `failed` so the UI can show it.
        try:
            from sqlalchemy import update as sa_update

            async with user_scoped_session(user_id) as session:
                await session.execute(
                    sa_update(Document)
                    .where(Document.id == doc_id, Document.user_id == user_id)
                    .values(status="failed", error_message=str(exc)[:500])
                )
                await session.commit()
        except Exception: as exc2:  # noqa: BLE001
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
