"""Eval scenarios for the citation dataset.

A correct citation: the answer includes at least one [chunk:<uuid>]
inline reference, AND the answer is substantive (not just "see
[chunk:abc]"). We use `citation_count(min=1)` for the structural
half and trust the LLM-judge groundness scorer for the content half
in nightly runs.
"""
from __future__ import annotations

import pytest

from llm_evals.eval import citation_count, contains, scenario, run


@scenario(
    dataset="citations",
    scorers=[citation_count(min=1), contains("1.2")],
    tags=["citations", "rag"],
)
async def test_citation_q3_revenue():
    await run(
        question="According to the 10-page PDF, what was Q3 revenue?",
        expected="1.2",
    )


@scenario(
    dataset="citations",
    scorers=[citation_count(min=1)],
    tags=["citations", "rag"],
)
async def test_citation_remote_work():
    await run(
        question="What does the company policy say about remote work?",
    )


@scenario(
    dataset="citations",
    scorers=[citation_count(min=1)],
    tags=["citations", "rag"],
)
async def test_citation_risk_factors():
    await run(
        question="List three risk factors from the prospectus.",
    )
