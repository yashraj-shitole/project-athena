"""Built-in tool implementations.

These are the actual Python functions invoked when a tool of
`handler_type='internal'` is called. The `registry` resolves the
`handler_cfg.impl` string (e.g. `app.tools.builtin.search_documents:run`)
to one of these callables.
"""
from __future__ import annotations

import uuid
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)


async def run(
    user_id: str,
    keywords: list[str],
    top_k: int = 4,
    session=None,
) -> dict[str, Any]:
    """The single built-in tool exposed to the LLM in Phase 1.

    Wired in init.sql: handler_cfg.impl = "app.tools.builtin.search_documents:run"
    The `session` argument is injected by the orchestrator before calling
    this function, so the LLM never sees a DB session in its tool
    description.
    """
    if not keywords:
        return {"results": [], "note": "no_keywords_provided"}
    from app.services.retrieval import search as retrieval_search

    try:
        uid = uuid.UUID(str(user_id))
    except (TypeError, ValueError):
        return {"error": "invalid_user_id"}

    chunks = await retrieval_search.retrieve(
        session=session,
        user_id=uid,
        keywords=keywords,
        top_k=top_k,
    )
    return {
        "results": [
            {
                "chunk_id": str(c["chunk_id"]),
                "document_id": str(c["document_id"]),
                "document_name": c.get("document_name"),
                "page_number": c.get("page_number"),
                "score": c.get("score"),
                "snippet": c.get("content", "")[:1200],
                "keywords": c.get("keywords", []),
            }
            for c in chunks
        ],
        "count": len(chunks),
    }


# Backwards-compat alias (early scaffolding used this name)
search_documents = run


__all__ = ["run", "search_documents"]
