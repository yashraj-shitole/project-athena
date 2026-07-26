"""Hybrid retrieval via Reciprocal Rank Fusion (RRF).

RRF combines ranked lists from independent retrievers without needing
score calibration. Each retriever contributes `1 / (k + rank)` to the
fused score; results are then sorted by fused score.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import List, Sequence

from app.core.config import settings
from app.core.logging import get_logger
from app.services.embedding import encode
from app.services.retrieval import lexical, vector

log = get_logger(__name__)


def _rrf(rankings: Sequence[List[dict]], k: int = 60) -> List[dict]:
    """Fuse multiple ranked lists by RRF. Preserves lexical order on ties."""
    fused: dict[str, dict] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            key = str(item["chunk_id"])
            entry = fused.setdefault(
                key,
                {
                    **item,
                    "rrf": 0.0,
                    "lexical_rank": None,
                    "vector_rank": None,
                },
            )
            entry["rrf"] += 1.0 / (k + rank + 1)
    out = sorted(fused.values(), key=lambda e: -e["rrf"])
    # Replace the original score with the RRF score for downstream display.
    for item in out:
        item["score"] = item["rrf"]
    return out


async def hybrid_search(
    session,
    *,
    user_id: uuid.UUID,
    query: str,
    keywords: str | None = None,
    top_k: int | None = None,
    always_hybrid: bool | None = None,
) -> List[dict]:
    """Run lexical + vector, then fuse.

    `query` is the raw user message — used to build the vector embedding
    so the semantic search sees the user's natural-language intent, not a
    stopword-stripped keyword bag. `keywords` (defaults to `query`) is the
    string fed to the lexical tsquery, where keyword-style tokenization is
    what `websearch_to_tsquery` expects.

    When `always_hybrid` is True (or settings.RETRIEVAL_ALWAYS_HYBRID is
    True) we always run both retrievers and RRF-fuse the results.
    Otherwise we run vector when the lexical top-1 score is below
    `settings.RETRIEVAL_HYBRID_THRESHOLD` (the lexical ranker is
    uncertain) — OR when lexical returned nothing. That empty-lexical
    case is exactly what semantic search is meant to rescue (a relevant
    chunk with no lexical overlap); the previous code returned ``[]``
    and the prompt got "(no context chunks available)".

    RLS is set by the underlying retrievers via `set_rls_user` on the
    session (database.py wires this for any session created in a
    `user_scoped_session` context).
    """
    top_k = top_k or settings.RETRIEVAL_TOP_K
    if always_hybrid is None:
        always_hybrid = settings.RETRIEVAL_ALWAYS_HYBRID

    sem_query = (query or "").strip()
    lex_query = (keywords if keywords is not None else query) or ""
    lex_query = lex_query.strip()

    lex_hits: List[dict] = []
    if lex_query:
        lex_hits = await lexical.search_lexical(
            session, user_id=user_id, query=lex_query, top_k=top_k
        )

    # Decide whether to run vector. We ALWAYS run it when lexical found
    # nothing — that is the rescue case semantic search exists for.
    run_vector = always_hybrid or not lex_hits
    if not run_vector:
        top_lex_score = lex_hits[0]["score"]
        run_vector = top_lex_score < settings.RETRIEVAL_HYBRID_THRESHOLD

    vec_hits: List[dict] = []
    if run_vector and sem_query:
        # `encode` runs a CPU-bound sentence-transformer forward pass and
        # would block the async event loop; run it in a worker thread.
        qvec = await asyncio.to_thread(encode, [sem_query], True)
        if qvec.size:
            vec_hits = await vector.search_vector(
                session,
                user_id=user_id,
                query_embedding=qvec[0].tolist(),
                top_k=top_k,
            )
            # Drop semantically distant hits. Vector cosine (1 - pgvector
            # <=> distance) lives in [0, 1]; unrelated text scores well
            # below RETRIEVAL_VECTOR_MIN_SIM for MiniLM-L6. Filtering
            # here keeps irrelevant chunks out of the prompt. Lexical
            # and RRF-fused scores are on different scales and are NOT
            # filtered by this threshold.
            min_sim = settings.RETRIEVAL_VECTOR_MIN_SIM
            if min_sim > 0:
                vec_hits = [h for h in vec_hits if (h.get("score") or 0.0) >= min_sim]

    if not lex_hits and not vec_hits:
        return []
    # If only one retriever produced hits, skip RRF (it needs two lists
    # to be meaningful) and return that list directly.
    if not vec_hits:
        return lex_hits[:top_k]
    if not lex_hits:
        return vec_hits[:top_k]

    fused = _rrf([lex_hits, vec_hits])
    log.info(
        "retrieval.hybrid",
        user_id=str(user_id),
        lex=len(lex_hits),
        vec=len(vec_hits),
        fused=len(fused),
        always=always_hybrid,
    )
    return fused[:top_k]
