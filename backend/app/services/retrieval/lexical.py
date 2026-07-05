"""Lexical (BM25-equivalent) search using Postgres tsvector + ts_rank_cd.

We rely on the `content_tsv` GENERATED column + GIN index defined in
init.sql. Tenant isolation is enforced by an explicit
`WHERE c.user_id = :uid AND d.user_id = :uid` predicate — RLS is a
defense-in-depth backstop only.
"""
from __future__ import annotations

import uuid
from typing import List

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

log = get_logger(__name__)


async def search_lexical(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    query: str,
    top_k: int = 4,
) -> List[dict]:
    """Return top-k chunks by BM25-style rank for the given user.

    Output shape: list of dicts with keys
      chunk_id, document_id, document_name, page_number, content, score
    """
    if not query.strip():
        return []
    # The `:uid` predicate is the authoritative tenant filter; RLS only
    # backs it up. `websearch_to_tsquery` is parameterized (not string
    # interpolated) so the query cannot inject SQL.
    sql = text(
        """
        SELECT
            c.id            AS chunk_id,
            c.document_id   AS document_id,
            d.filename      AS document_name,
            c.page_number   AS page_number,
            c.content       AS content,
            c.keywords      AS keywords,
            ts_rank_cd(c.content_tsv, websearch_to_tsquery('english', :q)) AS score
        FROM document_chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE c.user_id = :uid
          AND d.user_id = :uid
          AND c.content_tsv @@ websearch_to_tsquery('english', :q)
        ORDER BY score DESC
        LIMIT :limit
        """
    )
    rows = await session.execute(
        sql, {"q": query, "uid": user_id, "limit": top_k}
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
        "retrieval.lexical",
        user_id=str(user_id),
        hits=len(out),
        top_score=out[0]["score"] if out else 0.0,
    )
    return out