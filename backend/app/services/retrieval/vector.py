"""Vector (semantic) search using pgvector cosine distance.

Phase 2: triggered when the lexical top-1 score is below a confidence
threshold, or when the orchestrator asks for hybrid search explicitly.

Tenant isolation is enforced by an explicit `WHERE c.user_id = :uid`
predicate; RLS is a defense-in-depth backstop only.
"""
from __future__ import annotations

import uuid
from typing import List

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)
_settings = get_settings()


async def search_vector(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    query_embedding: List[float],
    top_k: int = 4,
) -> List[dict]:
    """Return top-k chunks by cosine similarity (descending)."""
    if not query_embedding:
        return []
    # Validate the embedding dimension. A mismatched vector would otherwise
    # raise a Postgres error at query time; failing early with a clear
    # message is friendlier and prevents a partial/empty result being
    # cached and returned for a misconfigured model.
    expected = _settings.EMBED_DIM
    if len(query_embedding) != expected:
        raise ValueError(
            f"query embedding dim {len(query_embedding)} != configured "
            f"embedding_dim {expected}"
        )
    # pgvector: <=> is cosine distance; 1 - distance = similarity in [0,1].
    sql = text(
        """
        SELECT
            c.id          AS chunk_id,
            c.document_id AS document_id,
            d.filename    AS document_name,
            c.page_number AS page_number,
            c.content     AS content,
            c.keywords    AS keywords,
            1 - (c.embedding <=> :qvec) AS score
        FROM document_chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE c.user_id = :uid
          AND d.user_id = :uid
          AND c.embedding IS NOT NULL
        ORDER BY c.embedding <=> :qvec
        LIMIT :limit
        """
    )
    rows = await session.execute(
        sql, {"qvec": query_embedding, "uid": user_id, "limit": top_k}
    )
    out: list[dict] = []
    for r in rows.mappings():
        out.append(
            {
                "chunk_id": r["chunk_id"],
                "document_id": r["document_id"],
                "document_name": r["document_name"],
                "page_number": r["page_number"],
                "content": r["content"],
                "keywords": list(r["keywords"] or []),
                "score": float(r["score"] or 0.0),
            }
        )
    log.info(
        "retrieval.vector",
        user_id=str(user_id),
        hits=len(out),
        top_score=out[0]["score"] if out else 0.0,
    )
    return out