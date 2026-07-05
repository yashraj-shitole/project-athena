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
    top_k: int | None = None,
    always_hybrid: bool | None = None,
) -> List[dict]:
    """Run lexical + vector, then fuse.

    When `always_hybrid` is True (or settings.RETRIEVAL_ALWAYS_HYBRID is
    True) we always run both retrievers and RRF-fuse the results.
    Otherwise we only run vector when the lexical top-1 score is below
    `settings.RETRIEVAL_HYBRID_THRESHOLD` (i.e. the lexical ranker is
    uncertain) — this is the default Phase 1 behaviour (FR-19, FR-21).

    Falls back to lexical hits if vector search is skipped and produces
    no results.

    RLS is set by the underlying retrievers via `set_rls_user` on the
    session (database.py wires this for any session created in a
    `user_scoped_session` context).
    """
    top_k = top_k or settings.RETRIEVAL_TOP_K
    if always_hybrid is None:
        always_hybrid = settings.RETRIEVAL_ALWAYS_HYBRID

    lex_hits = await lexical.search_lexical(session, user_id=user_id, query=query, top_k=top_k)
    if not lex_hits:
        return []

    # Decide whether to run vector.
    run_vector = always_hybrid
    if not run_vector:
        top_lex_score = lex_hits[0]["score"]
        run_vector = top_lex_score < settings.RETRIEVAL_HYBRID_THRESHOLD

    vec_hits: List[dict] = []
    if run_vector:
        # `encode` runs a CPU-bound sentence-transformer forward pass and
        # would block the async event loop; run it in a worker thread.
        qvec = await asyncio.to_thread(encode, [query], True)
        if qvec.size:
            vec_hits = await vector.search_vector(
                session,
                user_id=user_id,
                query_embedding=qvec[0].tolist(),
                top_k=top_k,
            )

    if not vec_hits:
        return lex_hits

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
