"""Eval runners.

`EvalRunner` is the workhorse: it takes a question + a Scenario, drives
the chat engine (or a stub), and returns an `EvalResult` that the
scorers consume.

`run()` and `run_scenario()` are the user-facing helpers used from
inside a scenario function (e.g. `await run(question=...)`).

The runner is intentionally agnostic about the chat transport:
- The default path uses the live `LLMClient` (real chat).
- A test path can substitute a `stub_runner` (deterministic_llm fixture)
  for hermetic unit-style eval testing.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from .judges import JudgeAdapter
from .matchers import ScorerResult
from .scenario import Scenario, get_scenario

# Default output directory for run records.
REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"


# ---------------------------------------------------------------------------
# Eval result
# ---------------------------------------------------------------------------

@dataclass
class EvalResult:
    """The bag of data passed to every scorer."""
    scenario: str
    dataset: str
    question: str
    answer: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    used_tools: list[dict[str, Any]] = field(default_factory=list)
    retrieved_chunks: list[dict[str, Any]] = field(default_factory=list)
    # Anything else (gold_chunk_ids, expected_tool_names, injection_payload, …)
    raw: dict[str, Any] = field(default_factory=dict)
    # Wire-level diagnostics
    connector_id: str | None = None
    model: str | None = None
    latency_ms: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)
    # Scorer output
    scores: dict[str, ScorerResult] = field(default_factory=dict)
    passed: bool = False
    error: str | None = None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class EvalRunner:
    """Drives a scenario end-to-end and grades it.

    The runner is constructed once per session (the `eval_runner`
    fixture in `testing/conftest.py` does this). It holds:
      - the JudgeAdapter (so scorers can lazily call it)
      - a chat callable (default = real LLMClient; tests inject a stub)
      - the run id (so the JSONL output is traceable)
    """

    def __init__(
        self,
        judge: JudgeAdapter | None = None,
        chat: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        reports_dir: Path | None = None,
    ) -> None:
        from .judges import get_judge
        self.judge = judge or get_judge()
        self.chat = chat or _default_chat
        self.reports_dir = Path(reports_dir or REPORTS_DIR)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = os.environ.get("ATHENA_EVAL_RUN_ID") or uuid.uuid4().hex[:12]
        # Per-process append-only stream. The report writer reads this
        # at the end of the run to produce HTML/JSON/MD/CSV outputs.
        self.stream_path = self.reports_dir / f"{self.run_id}.jsonl"
        if not self.stream_path.exists():
            self.stream_path.touch()

    async def run(self, scenario: Scenario, params: dict[str, Any]) -> EvalResult:
        """Execute one scenario, grade it, append the record."""
        started = time.perf_counter()
        result = EvalResult(
            scenario=scenario.name,
            dataset=scenario.dataset,
            question=params.get("question", ""),
            answer="",
            raw=dict(params),
        )
        try:
            chat_resp = await self.chat(params.get("question", ""), params)
            result.answer = chat_resp.get("answer", "")
            result.citations = chat_resp.get("citations", []) or []
            result.used_tools = chat_resp.get("used_tools", []) or []
            result.retrieved_chunks = chat_resp.get("retrieved_chunks", []) or []
            result.connector_id = chat_resp.get("connector_id")
            result.model = chat_resp.get("model")
            result.usage = chat_resp.get("usage", {}) or {}
        except Exception as e:
            result.error = f"{type(e).__name__}: {e}"
        result.latency_ms = (time.perf_counter() - started) * 1000.0

        # Grade
        await self._grade(result, scenario)

        # Persist
        self._append(result)
        return result

    async def _grade(self, result: EvalResult, scenario: Scenario) -> None:
        for scorer in scenario.scorers:
            name = getattr(scorer, "__name__", type(scorer).__name__)
            try:
                sr = scorer(result)
                if asyncio.iscoroutine(sr):
                    sr = await sr
                if hasattr(sr, "__await__") and not isinstance(sr, ScorerResult):
                    sr = await sr
            except Exception as e:
                sr = ScorerResult(0.0, f"scorer raised: {type(e).__name__}: {e}")
            result.scores[name] = sr
        # Overall pass = every scorer >= 0.5 (per-scorer thresholds, when
        # set, are baked into the scorer's own 0/1 reporting).
        result.passed = all(sr.score >= 0.5 for sr in result.scores.values()) if result.scores else True

    def _append(self, result: EvalResult) -> None:
        record = {
            "run_id": self.run_id,
            "scenario": result.scenario,
            "dataset": result.dataset,
            "question": result.question,
            "answer": result.answer,
            "connector_id": result.connector_id,
            "model": result.model,
            "latency_ms": result.latency_ms,
            "usage": result.usage,
            "citations": result.citations,
            "used_tools": result.used_tools,
            "retrieved_chunks": [
                {k: c.get(k) for k in ("chunk_id", "document_id", "score")}
                for c in result.retrieved_chunks
            ],
            "scores": {n: {"score": s.score, "explanation": s.explanation, "details": s.details}
                       for n, s in result.scores.items()},
            "passed": result.passed,
            "error": result.error,
        }
        with self.stream_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Default chat transport
# ---------------------------------------------------------------------------

async def _default_chat(question: str, params: dict[str, Any]) -> dict[str, Any]:
    """Drive the real LLMClient through the chat HTTP API.

    Falls back to raising clearly if the stack isn't running — the
    integration test markers gate this; the unit tests use a stub.
    """
    import httpx
    base = os.environ.get("ATHENA_TEST_BASE_URL", "http://localhost:8000").rstrip("/")
    body = {
        "message": question,
        "conversation_id": params.get("conversation_id"),
        "tool_subset": params.get("tool_subset"),
        "connector_id": params.get("connector_id"),
        "model": params.get("model"),
    }
    async with httpx.AsyncClient(base_url=base, timeout=120.0) as client:
        r = await client.post("/api/chat", json=body)
        r.raise_for_status()
        data = r.json()
    msg = data.get("message", {})
    return {
        "answer": msg.get("content", ""),
        "citations": msg.get("citations", []),
        "used_tools": msg.get("used_tools", []),
        "retrieved_chunks": msg.get("retrieved_chunks", []),
        "connector_id": msg.get("connector_id"),
        "model": msg.get("model"),
        "usage": msg.get("usage", {}) or {},
    }


# ---------------------------------------------------------------------------
# Scenario helpers (used inside scenario functions)
# ---------------------------------------------------------------------------

async def run(
    *,
    question: str,
    expected: str | None = None,
    expected_chunk_ids: list[str] | None = None,
    expected_tool_names: list[str] | None = None,
    injection_payload: str | None = None,
    **kwargs: Any,
) -> None:
    """Convenience: hand params to the active EvalRunner.

    Used from inside a scenario function:

        @scenario(dataset="general_qa", scorers=[exact_match])
        async def test_capital_of_france():
            await run(question="What is the capital of France?",
                      expected="Paris")
    """
    runner = _current_runner()
    if runner is None:
        raise RuntimeError(
            "No active EvalRunner. Use the `eval_runner` fixture or "
            "call `set_runner()` from a fixture."
        )
    # The scenario is reconstructed from the calling function's
    # `__athena_scenario__` attr; this is a bit of a hack, but it
    # keeps the public API flat.
    import inspect
    frame = inspect.currentframe()
    assert frame is not None and frame.f_back is not None
    sc = get_scenario(frame.f_back.f_globals.get("_scenario_under_test"))
    if sc is None:
        # Fallback: synthesize a minimal one from the @scenario decorator
        # (pytest has already decorated this frame).
        sc = _synthesize_scenario_from_frame(frame.f_back)
    params = {
        "question": question,
        "expected_answer": expected,
        "gold_chunk_ids": expected_chunk_ids or [],
        "expected_tool_names": expected_tool_names or [],
        "injection_payload": injection_payload,
    }
    params.update(kwargs)
    await runner.run(sc, params)


async def run_scenario(scenario: Scenario, params: dict[str, Any]) -> EvalResult:
    """Lower-level: run an arbitrary scenario with the active runner."""
    runner = _current_runner()
    if runner is None:
        raise RuntimeError("No active EvalRunner. Use the `eval_runner` fixture.")
    return await runner.run(scenario, params)


# Per-process current-runner slot. The conftest fixture sets this.
_current_runner: EvalRunner | None = None


def set_runner(runner: EvalRunner | None) -> None:
    global _current_runner
    _current_runner = runner


def _current_runner() -> EvalRunner | None:
    return _current_runner


def _synthesize_scenario_from_frame(frame: Any) -> Scenario:
    """Build a minimal Scenario from the decorated test function.

    This is a fallback for the `run()` helper: when the scenario
    function calls `run()` directly, we don't have the Scenario
    object in scope. We recover it from `frame.f_code` (the function
    the user called run() from).
    """
    fn_name = frame.f_code.co_name
    # Best-effort: the @scenario decorator stashes the Scenario on the
    # function in the calling frame's globals. We try a couple of names.
    for var in ("_scenario_under_test",):
        sc = frame.f_globals.get(var)
        if isinstance(sc, Scenario):
            return sc
    # Last-resort: a no-scorer placeholder. The Scenario object's
    # `scorers` are empty, which the runner treats as "always pass" —
    # but the framework's collection-time @scenario decorator will
    # have provided the real list. This path only fires when someone
    # calls `run()` outside the @scenario decorator.
    return Scenario(name=fn_name, dataset="unknown", scorers=(), tags=())
