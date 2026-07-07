"""@scenario decorator + Scenario dataclass.

A Scenario is a single test case: an input (question, optional
document context) + a set of scorers that grade the answer.

Scenarios are the unit of collection for pytest — one
`@scenario(dataset="general_qa", scorers=[...])` per test function
becomes one `pytest` item. The actual body of the test is the
`run()` call (a thin wrapper around the chat engine).
"""
from __future__ import annotations

import dataclasses
import functools
import inspect
from typing import Any, Awaitable, Callable, Iterable


@dataclasses.dataclass(frozen=True)
class Scenario:
    """A single eval scenario.

    Attributes:
        name:        Unique test name. Defaults to the function's __name__.
        dataset:     Logical dataset name (matches a JSONL file under
                     `llm-evals/datasets/`). The runner looks up rows
                     by `id` to hydrate the test params.
        scorers:     List of scorer callables. Each receives the
                     EvalResult and returns a ScorerResult.
        tags:        Free-form labels (e.g. "rag", "tool_calling", "refusal").
                     Pytest markers are derived from these at collection.
        timeout_s:   Per-scenario timeout. Default 60s.
    """

    name: str
    dataset: str
    scorers: tuple[Callable[..., Any], ...]
    tags: tuple[str, ...] = ()
    timeout_s: float = 60.0


def scenario(
    *,
    dataset: str,
    scorers: Iterable[Callable[..., Any]],
    tags: Iterable[str] = (),
    timeout_s: float = 60.0,
    name: str | None = None,
) -> Callable[[Callable[..., Awaitable[None]]], Callable[..., Awaitable[None]]]:
    """Mark a test function as an eval scenario.

    The decorator stores the Scenario on the function; the pytest
    plugin (`llm-evals/eval/runners.py::pytest_collectstart`) reads
    it and adds the appropriate markers (`eval`, `rag`, etc.).

    Example:
        @scenario(dataset="general_qa", scorers=[exact_match])
        async def test_capital_of_france():
            await run(question="What is the capital of France?",
                      expected="Paris")
    """
    scorers_t = tuple(scorers)
    tags_t = tuple(tags)
    if not scorers_t:
        raise ValueError("scenario() needs at least one scorer")

    def decorator(fn: Callable[..., Awaitable[None]]) -> Callable[..., Awaitable[None]]:
        scenario_name = name or fn.__name__
        sc = Scenario(
            name=scenario_name,
            dataset=dataset,
            scorers=scorers_t,
            tags=tags_t,
            timeout_s=timeout_s,
        )
        # Stash on the function for the runner to pick up.
        fn.__athena_scenario__ = sc  # type: ignore[attr-defined]
        # Surface the dataset as a pytest marker so `-m "general_qa"`
        # works at the CLI.
        existing = list(getattr(fn, "pytestmark", []) or [])

        import pytest

        existing.append(pytest.mark.eval)
        for tag in tags_t:
            existing.append(getattr(pytest.mark, tag))
        existing.append(pytest.mark.timeout(timeout_s))
        fn.pytestmark = existing  # type: ignore[attr-defined]

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await fn(*args, **kwargs)

        # Preserve the dataclass on the wrapper.
        wrapper.__athena_scenario__ = sc  # type: ignore[attr-defined]
        # Preserve the source signature for nicer pytest ids.
        try:
            wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
        except AttributeError:
            pass
        return wrapper

    return decorator


def get_scenario(fn: Callable[..., Any]) -> Scenario | None:
    """Read the Scenario stashed by `@scenario` on a function."""
    return getattr(fn, "__athena_scenario__", None)


def is_scenario_function(fn: Callable[..., Any]) -> bool:
    """True if `fn` was decorated with @scenario()."""
    return get_scenario(fn) is not None
