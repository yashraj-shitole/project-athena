"""Top-level cached retrieval used by the orchestrator and builtin tool.

Wraps `hybrid.hybrid_search` with a Redis cache keyed by
`(user_id, normalized query)`. Returns a thin list of dicts ready for
the prompter / tool result.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any, List

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_json, set_json
from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.retrieval.hybrid import hybrid_search
from app.services.retrieval.rerank import rerank

log = get_logger(__name__)
_settings = get_settings()


def _normalize_keywords(keywords: List[str] | str) -> str:
    """Stable, lowercased, whitespace-collapsed representation for cache key."""
    if isinstance(keywords, str):
        toks = re.findall(r"[A-Za-z0-9_\-]+", keywords.lower())
    else:
        toks = []
        for k in keywords or []:
            toks.extend(re.findall(r"[A-Za-z0-9_\-]+", str(k).lower()))
    toks.sort()
    return " ".join(toks)


def _cache_key(user_id: uuid.UUID, query: str, top_k: int) -> str:
    """Cache key includes user_id, normalized query, AND top_k.

    Without top_k, a later call with a larger top_k would receive a
    result list truncated to the smaller cached top_k.
    """
    norm = _normalize_keywords(query)
    digest = hashlib.sha1(f"{top_k}:{norm}".encode("utf-8")).hexdigest()[:16]
    return f"{user_id}:{digest}"


async def retrieve(
    *,
    session: AsyncSession,
    user_id: uuid.UUID,
    keywords: List[str] | str | None = None,
    query: str | None = None,
    top_k: int | None = None,
) -> List[dict[str, Any]]:
    """Cached hybrid retrieval.

    Inputs:
        session:   an open AsyncSession
        user_id:   the requesting user (used for RLS GUC and cache key)
        keywords:  list of keyword strings, or a free-form query string,
                   fed to the lexical tsquery. Optional when `query` is
                   supplied.
        query:     the raw user message, used to build the vector
                   embedding. Strongly preferred over passing keywords
                   as `query` — a stopword-stripped keyword bag produces
                   a weaker semantic embedding than the natural-language
                   sentence. Falls back to `keywords` when omitted.
        top_k:     default = settings.RETRIEVAL_TOP_K

    Returns: a list of chunk dicts in the shape consumed by the prompter
        and the `search_documents` tool.
    """
    sem_query = (query or "").strip()
    if not keywords and not sem_query:
        return []
    top_k = top_k or _settings.RETRIEVAL_TOP_K
    # NOTE: we intentionally do NOT call set_rls_user(session, user_id) here.
    # The per-request RLS GUC is bound exactly once in `get_user_db`
    # (backend/app/api/dependencies.py) using the authenticated principal.
    # Re-binding it from this function's `user_id` argument would let a
    # caller (e.g. /tools/{id}/invoke or a prompt-injected tool call) pass
    # another tenant's uuid and silently exfiltrate their documents.
    # App-layer `WHERE user_id = :uid` predicates in lexical/vector SQL
    # provide the authoritative tenant filter.

    # Lexical query string from keywords; fall back to the raw semantic
    # query when no keywords were supplied.
    if isinstance(keywords, str):
        lex_query = keywords
    elif keywords:
        lex_query = " ".join(keywords)
    else:
        lex_query = sem_query

    # Cache key includes the raw semantic query when supplied — vector
    # results depend on it even when the lexical keywords are identical,
    # so keying only on keywords would return stale vector hits.
    cache_query = sem_query or lex_query
    cache_key = _cache_key(user_id, cache_query, top_k)
    cached = await get_json(_settings.CACHE_PREFIX_RETRIEVAL, cache_key)
    if cached is not None:
        log.debug("retrieval.cache.hit", user_id=str(user_id), q=cache_query[:64])
        return cached[:top_k]

    hits = await hybrid_search(
        session,
        user_id=user_id,
        query=sem_query or lex_query,
        keywords=lex_query or None,
        top_k=top_k,
    )
    # Rerank is currently a model-free re-slice (identity order) — but
    # routing through it here is what makes a future cross-encoder
    # reranker drop-in. It also applies the final top_k consistently
    # across the lexical-only, vector-only, and fused paths.
    hits = rerank(sem_query or lex_query, hits, top_k=top_k)
    # Cache as JSON-serialisable lists (uuids → str, etc.)
    serializable = []
    for h in hits:
        serializable.append(
            {
                "chunk_id": str(h.get("chunk_id")),
                "document_id": str(h.get("document_id")),
                "document_name": h.get("document_name"),
                "page_number": h.get("page_number"),
                "content": h.get("content"),
                "keywords": list(h.get("keywords") or []),
                "score": float(h.get("score") or 0.0),
            }
        )

    await set_json(
        _settings.CACHE_PREFIX_RETRIEVAL,
        cache_key,
        serializable,
        ttl=_settings.cache_ttl_seconds,
    )
    log.debug(
        "retrieval.cache.miss",
        user_id=str(user_id),
        q=cache_query[:64],
        hits=len(serializable),
    )
    return serializable


__all__ = ["retrieve", "normalize_keywords"]

# Backwards-compat (private name was previously exported).
normalize_keywords = _normalize_keywords
