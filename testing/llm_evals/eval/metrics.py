"""Numeric / LLM-judge metrics.

Every scorer here is `Callable[[EvalResultLike], ScorerResult]`. The
LLM-judge ones build a prompt, call the judge, and convert the
returned `JudgeResult` into a ScorerResult with a threshold.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .judges import JudgeAdapter, JudgeResult
from .matchers import EvalResultLike, ScorerResult
from .scenario import get_scenario

# Cached judge lookup (per-process). The conftest already provides a
# judge fixture, but a scorer also needs to be able to fetch one when
# called outside the fixture path.
_DEFAULT_JUDGE: JudgeAdapter | None = None


def _get_default_judge() -> JudgeAdapter:
    global _DEFAULT_JUDGE
    if _DEFAULT_JUDGE is None:
        from .judges import get_judge
        _DEFAULT_JUDGE = get_judge()
    return _DEFAULT_JUDGE


# ---------------------------------------------------------------------------
# LLM-judge scorers
# ---------------------------------------------------------------------------

def groundedness(judge: JudgeAdapter | None = None, *, threshold: float = 0.7) -> Callable[[Any], ScorerResult]:
    """Every claim in the answer is supported by the cited chunks.

    Builds a prompt with `QUESTION:`, `CONTEXT:` (cited chunks joined),
    `ANSWER:`, asks the judge for a single 0..1 score. Pass when score
    >= threshold.
    """
    j = judge or _get_default_judge()

    async def _async(result: EvalResultLike) -> ScorerResult:
        return _grade_with_judge(
            j, result,
            system=_JUDGE_SYSTEM_GROUNDEDNESS,
            prompt_template=_PROMPT_GROUNDEDNESS,
            threshold=threshold,
        )

    return _wrap_async(_async)


def faithfulness(judge: JudgeAdapter | None = None, *, threshold: float = 0.7) -> Callable[[Any], ScorerResult]:
    """Every claim in the answer is entailed by the cited chunks.

    Slightly stricter than groundedness: entails = true in the literal
    NLI sense, not just "supported".
    """
    j = judge or _get_default_judge()

    async def _async(result: EvalResultLike) -> ScorerResult:
        return _grade_with_judge(
            j, result,
            system=_JUDGE_SYSTEM_FAITHFULNESS,
            prompt_template=_PROMPT_FAITHFULNESS,
            threshold=threshold,
        )

    return _wrap_async(_async)


def answer_relevance(judge: JudgeAdapter | None = None, *, threshold: float = 0.6) -> Callable[[Any], ScorerResult]:
    """The answer actually addresses the question (0..1)."""
    j = judge or _get_default_judge()

    async def _async(result: EvalResultLike) -> ScorerResult:
        return _grade_with_judge(
            j, result,
            system=_JUDGE_SYSTEM_RELEVANCE,
            prompt_template=_PROMPT_RELEVANCE,
            threshold=threshold,
        )

    return _wrap_async(_async)


def unsupported_claim_rate(judge: JudgeAdapter | None = None) -> Callable[[Any], ScorerResult]:
    """Lower is better. 0 = every claim cited; 1 = nothing cited.

    Inverted into a 0..1 score where 1.0 means "no unsupported claims"
    so the metric composes with the others.
    """
    j = judge or _get_default_judge()

    async def _async(result: EvalResultLike) -> ScorerResult:
        jr = await _grade_with_judge(
            j, result,
            system=_JUDGE_SYSTEM_UNSUPPORTED,
            prompt_template=_PROMPT_UNSUPPORTED,
            threshold=None,
        )
        # The judge returns the unsupported rate; invert.
        inv = 1.0 - jr.score
        return ScorerResult(score=inv, explanation=f"unsupported={jr.score:.2f} -> score={inv:.2f}",
                            details={"unsupported_rate": jr.score, "raw": jr.explanation})
    return _wrap_async(_async)


def missing_citation_rate(judge: JudgeAdapter | None = None) -> Callable[[Any], ScorerResult]:
    """Lower is better. 0 = every claim has a [chunk:...] reference."""
    j = judge or _get_default_judge()

    async def _async(result: EvalResultLike) -> ScorerResult:
        jr = await _grade_with_judge(
            j, result,
            system=_JUDGE_SYSTEM_CITATION,
            prompt_template=_PROMPT_CITATION,
            threshold=None,
        )
        inv = 1.0 - jr.score
        return ScorerResult(score=inv, explanation=f"missing-citation={jr.score:.2f} -> score={inv:.2f}",
                            details={"missing_citation_rate": jr.score, "raw": jr.explanation})
    return _wrap_async(_async)


# ---------------------------------------------------------------------------
# Retrieval metrics (no LLM; operate on retrieved_chunks)
# ---------------------------------------------------------------------------

def precision_at_k(k: int = 4) -> Callable[[Any], ScorerResult]:
    """Of the top-k retrieved chunks, what fraction are in the gold set?"""
    def _score(result: EvalResultLike) -> ScorerResult:
        gold = set((result.raw or {}).get("gold_chunk_ids", []))
        if not gold:
            return ScorerResult(0.0, "precision_at_k: no gold_chunk_ids on raw")
        top = result.retrieved_chunks[:k]
        hit = sum(1 for c in top if c.get("chunk_id") in gold)
        p = hit / max(1, k)
        return ScorerResult(p, f"precision@{k}={p:.2f} ({hit}/{k})",
                            details={"k": k, "hit": hit, "gold_size": len(gold)})
    return _score


def recall_at_k(k: int = 4) -> Callable[[Any], ScorerResult]:
    """Of the gold set, what fraction is in the top-k retrieved chunks?"""
    def _score(result: EvalResultLike) -> ScorerResult:
        gold = set((result.raw or {}).get("gold_chunk_ids", []))
        if not gold:
            return ScorerResult(0.0, "recall_at_k: no gold_chunk_ids on raw")
        top = result.retrieved_chunks[:k]
        top_ids = {c.get("chunk_id") for c in top}
        hit = len(gold & top_ids)
        r = hit / max(1, len(gold))
        return ScorerResult(r, f"recall@{k}={r:.2f} ({hit}/{len(gold)})",
                            details={"k": k, "hit": hit, "gold_size": len(gold)})
    return _score


def mrr() -> Callable[[Any], ScorerResult]:
    """Mean reciprocal rank of the first gold chunk in retrieved_chunks."""
    def _score(result: EvalResultLike) -> ScorerResult:
        gold = set((result.raw or {}).get("gold_chunk_ids", []))
        if not gold:
            return ScorerResult(0.0, "mrr: no gold_chunk_ids on raw")
        for i, c in enumerate(result.retrieved_chunks, start=1):
            if c.get("chunk_id") in gold:
                rr = 1.0 / i
                return ScorerResult(rr, f"mrr={rr:.3f} (rank {i})", details={"rank": i})
        return ScorerResult(0.0, "mrr: no gold chunk in retrieved list")
    return _score


def ndcg() -> Callable[[Any], ScorerResult]:
    """nDCG (binary relevance) of retrieved_chunks against gold_chunk_ids."""
    import math
    def _score(result: EvalResultLike) -> ScorerResult:
        gold = set((result.raw or {}).get("gold_chunk_ids", []))
        if not gold:
            return ScorerResult(0.0, "ndcg: no gold_chunk_ids on raw")
        # DCG
        dcg = 0.0
        for i, c in enumerate(result.retrieved_chunks, start=1):
            rel = 1.0 if c.get("chunk_id") in gold else 0.0
            dcg += (2**rel - 1) / math.log2(i + 1)
        # iDCG (perfect ranking)
        ideal = sum((2**1 - 1) / math.log2(i + 1) for i in range(1, min(len(gold), len(result.retrieved_chunks)) + 1))
        n = (dcg / ideal) if ideal > 0 else 0.0
        return ScorerResult(n, f"ndcg={n:.3f}", details={"dcg": dcg, "ideal": ideal})
    return _score


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wrap_async(async_fn: Callable[[Any], Any]) -> Callable[[Any], ScorerResult]:
    """Wrap an async scorer into the synchronous ScorerResult-returning
    contract. The runner awaits via `asyncio.run()` if it gets a
    coroutine back; for now we run synchronously to keep the contract
    simple — a real async runner would await."""
    def _sync(result: Any) -> ScorerResult:
        import asyncio
        coro = async_fn(result)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're already inside an event loop (pytest-asyncio).
                # The runner will await us instead.
                return _AwaitableScorerResult(coro)
            return loop.run_until_complete(coro)
        except RuntimeError:
            return asyncio.run(coro)
    return _sync


@dataclass
class _AwaitableScorerResult:
    """A ScorerResult-shaped coroutine wrapper. The runner detects
    these and awaits them."""
    coro: Any

    def __await__(self):
        async def _resolve():
            res = await self.coro
            return res
        return _resolve().__await__()


def _grade_with_judge(
    judge: JudgeAdapter,
    result: EvalResultLike,
    *,
    system: str,
    prompt_template: str,
    threshold: float | None,
) -> ScorerResult:
    """Build a judge prompt from the result and call the judge.

    Returns a ScorerResult. If `threshold` is set, the score is
    reported as 1.0 when >= threshold else 0.0; otherwise the raw
    judge score (clamped to [0,1]) is passed through.
    """
    import asyncio
    context = _format_context(result)
    prompt = prompt_template.format(
        question=result.question or "",
        context=context,
        answer=result.answer or "",
    )
    coro = judge.score(prompt, system=system)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return _AwaitableScorerResult(coro)
        jr: JudgeResult = loop.run_until_complete(coro)
    except RuntimeError:
        jr = asyncio.run(coro)
    if threshold is not None:
        passed = jr.score >= threshold
        return ScorerResult(
            score=1.0 if passed else 0.0,
            explanation=f"judge={judge.name} score={jr.score:.2f} threshold={threshold} -> {'pass' if passed else 'fail'}",
            details={"judge": judge.name, "score": jr.score, "threshold": threshold, "raw": jr.explanation},
        )
    return ScorerResult(
        score=jr.score,
        explanation=f"judge={judge.name} score={jr.score:.2f}",
        details={"judge": judge.name, "raw": jr.explanation},
    )


def _format_context(result: EvalResultLike) -> str:
    """Format the cited chunks as CONTEXT for the judge prompt.

    Falls back to the top-k retrieved chunks if no citations are
    attached to the assistant message.
    """
    if result.citations:
        return "\n\n---\n\n".join(
            f"[{c.get('chunk_id', '?')}] {c.get('snippet') or c.get('content', '')}"
            for c in result.citations
        )
    return "\n\n---\n\n".join(
        f"[{c.get('chunk_id', '?')}] {c.get('content', '')}"
        for c in result.retrieved_chunks[:4]
    )


# ---------------------------------------------------------------------------
# Judge prompts (versioned; mirror llm-evals/prompts/*.txt when used as files)
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM_GROUNDEDNESS = (
    "You are an impartial grader. Given a QUESTION, the CONTEXT the model "
    "cited, and the model's ANSWER, decide whether every claim in the "
    "ANSWER is supported by the CONTEXT. Respond with a single line: "
    "'Score: 0.85' where 0.0 = none of the claims are supported, "
    "1.0 = every claim is supported."
)

_PROMPT_GROUNDEDNESS = (
    "QUESTION: {question}\n\n"
    "CONTEXT (chunks the model cited):\n{context}\n\n"
    "ANSWER: {answer}\n\n"
    "Score the answer in [0,1] for groundedness."
)

_JUDGE_SYSTEM_FAITHFULNESS = (
    "You are an impartial grader. Given a QUESTION, the CONTEXT the model "
    "cited, and the model's ANSWER, decide whether every claim in the "
    "ANSWER is *entailed* by the CONTEXT (in the strict NLI sense — the "
    "CONTEXT must imply the claim, not merely mention it). Respond with a "
    "single line: 'Score: 0.85' where 0.0 = nothing is entailed, "
    "1.0 = every claim is entailed."
)

_PROMPT_FAITHFULNESS = _PROMPT_GROUNDEDNESS  # same shape; different system

_JUDGE_SYSTEM_RELEVANCE = (
    "You are an impartial grader. Given a QUESTION and the model's ANSWER, "
    "decide whether the ANSWER actually addresses the QUESTION. A refusal "
    "to a relevant question is a 0; a refusal to an out-of-scope question "
    "is a 1. Respond with a single line: 'Score: 0.85' where 0.0 = "
    "irrelevant, 1.0 = fully addresses the question."
)

_PROMPT_RELEVANCE = (
    "QUESTION: {question}\n\n"
    "ANSWER: {answer}\n\n"
    "Score the answer's relevance in [0,1]."
)

_JUDGE_SYSTEM_UNSUPPORTED = (
    "You are an impartial grader. Given the CONTEXT and the model's "
    "ANSWER, return the fraction of distinct factual claims in the "
    "ANSWER that are NOT supported by the CONTEXT. Respond with a "
    "single line: 'Score: 0.40' where 0.0 = nothing unsupported, "
    "1.0 = everything unsupported."
)

_PROMPT_UNSUPPORTED = _PROMPT_GROUNDEDNESS  # same shape; different system

_JUDGE_SYSTEM_CITATION = (
    "You are an impartial grader. Given the model's ANSWER and the "
    "context it had available, return the fraction of factual claims in "
    "the ANSWER that LACK a [chunk:<uuid>] inline reference. Respond "
    "with: 'Score: 0.20' where 0.0 = every claim is cited, "
    "1.0 = nothing is cited."
)

_PROMPT_CITATION = _PROMPT_GROUNDEDNESS
