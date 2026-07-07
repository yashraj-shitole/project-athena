"""Eval scenarios for the refusal dataset.

A correct refusal: the answer matches one of the documented refusal
patterns AND has zero citations. We compose the two scorers per row
to assert both.
"""
from __future__ import annotations

import pytest

from llm_evals.eval import no_citation, refuses, scenario, run


@scenario(
    dataset="refusal",
    scorers=[refuses(), no_citation()],
    tags=["refusal"],
)
async def test_refuse_unknown_product():
    await run(
        question=(
            "What does the policy say about refunds for the new "
            "Acme 9000 product?"
        ),
    )


@scenario(
    dataset="refusal",
    scorers=[refuses(), no_citation()],
    tags=["refusal"],
)
async def test_refuse_unindexed_doc():
    await run(
        question=(
            "Tell me about document 99999-9999 which is not in the "
            "index."
        ),
    )


@scenario(
    dataset="refusal",
    scorers=[refuses(), no_citation()],
    tags=["refusal"],
)
async def test_refuse_weather_question():
    await run(
        question="What's the weather like in Paris right now?",
    )
