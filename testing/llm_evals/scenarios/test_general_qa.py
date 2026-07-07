"""Eval scenarios for the general-QA dataset.

Uses the deterministic `exact_match` scorer. No LLM judge required;
the default judge (Ollama) is still instantiated for fixture parity,
but these tests do not call it.
"""
from __future__ import annotations

import pytest

from llm_evals.eval import exact_match, contains, scenario, run


@scenario(dataset="general_qa", scorers=[exact_match()], tags=["general_qa"])
async def test_capital_of_france():
    await run(
        question="What is the capital of France?",
        expected="Paris",
    )


@scenario(dataset="general_qa", scorers=[exact_match()], tags=["general_qa"])
async def test_capital_of_japan():
    await run(
        question="What is the capital of Japan?",
        expected="Tokyo",
    )


@scenario(dataset="general_qa", scorers=[contains("100")], tags=["general_qa"])
async def test_boiling_point_of_water():
    await run(
        question="What is the boiling point of water at sea level in Celsius?",
        expected="100",
    )


@scenario(dataset="general_qa", scorers=[contains("Austen")], tags=["general_qa"])
async def test_author_pride_and_prejudice():
    await run(
        question="Who wrote 'Pride and Prejudice'?",
        expected="Jane Austen",
    )


@scenario(dataset="general_qa", scorers=[contains("Jupiter")], tags=["general_qa"])
async def test_largest_planet():
    await run(
        question="What is the largest planet in our solar system?",
        expected="Jupiter",
    )


@scenario(dataset="general_qa", scorers=[contains("1945")], tags=["general_qa"])
async def test_end_of_ww2():
    await run(
        question="In what year did World War II end?",
        expected="1945",
    )


@scenario(dataset="general_qa", scorers=[contains("Au")], tags=["general_qa"])
async def test_symbol_for_gold():
    await run(
        question="What is the chemical symbol for gold?",
        expected="Au",
    )


@scenario(dataset="general_qa", scorers=[contains("7")], tags=["general_qa"])
async def test_number_of_continents():
    await run(
        question="How many continents are there on Earth?",
        expected="7",
    )


@scenario(dataset="general_qa", scorers=[contains("Vatican")], tags=["general_qa"])
async def test_smallest_country():
    await run(
        question="What is the smallest country in the world by area?",
        expected="Vatican",
    )
