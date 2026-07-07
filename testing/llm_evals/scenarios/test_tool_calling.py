"""Eval scenarios for the tool-calling dataset.

A correct tool call: the model invoked the right tool, with the
right argument shape. We assert via `tool_call_shape` (presence +
status) and the `tool_selection_accuracy` scorer (set match).
"""
from __future__ import annotations

import pytest

from llm_evals.eval import (
    scenario,
    run,
    tool_call_shape,
    tool_selection_accuracy,
)


@scenario(
    dataset="tool_calling",
    scorers=[tool_call_shape(name="search_documents"),
             tool_selection_accuracy()],
    tags=["tool_calling"],
)
async def test_search_documents_for_payment_terms():
    await run(
        question=(
            "Find me any document that mentions 'payment terms' and "
            "summarize it."
        ),
        expected_tool_names=["search_documents"],
    )


@scenario(
    dataset="tool_calling",
    scorers=[tool_call_shape(name="list_documents"),
             tool_selection_accuracy()],
    tags=["tool_calling"],
)
async def test_list_documents():
    await run(
        question="List the documents I've uploaded in the last week.",
        expected_tool_names=["list_documents"],
    )


@scenario(
    dataset="tool_calling",
    scorers=[tool_call_shape(name="get_usage"),
             tool_selection_accuracy()],
    tags=["tool_calling"],
)
async def test_get_usage_yesterday():
    await run(
        question="How many tokens did I use yesterday?",
        expected_tool_names=["get_usage"],
    )
