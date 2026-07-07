"""JudgeAdapter ABC + three concrete judges.

The `JudgeAdapter` is the seam where ragas / deepeval could be dropped
in later. The default is OllamaJudge (hermetic, free, offline).
HeuristicJudge is the offline-only fallback used by the CI gate when
the Ollama container is not available.

Selection (see `get_judge`):
  - If $OPENAI_API_KEY is set and `--llm-judge=openai` -> OpenAIJudge
  - If `--llm-judge=heuristic`                      -> HeuristicJudge
  - Otherwise                                       -> OllamaJudge
"""
from __future__ import annotations

import abc
import asyncio
import json
import os
import re
import string
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class JudgeResult:
    """A single judge score, in [0.0, 1.0]."""

    score: float
    explanation: str = ""
    raw: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            # Clamp — judge models sometimes emit 1.0001 or -0.01.
            self.score = max(0.0, min(1.0, float(self.score)))


# ---------------------------------------------------------------------------
# ABC
# ---------------------------------------------------------------------------

class JudgeAdapter(abc.ABC):
    """Abstract LLM judge. Implementations must be safe to instantiate
    without environment side-effects (no I/O until `score()` is called)."""

    name: str = ""

    @abc.abstractmethod
    async def score(
        self,
        prompt: str,
        *,
        system: str = "",
        max_tokens: int = 256,
    ) -> JudgeResult:
        """Run the judge. Returns a JudgeResult with score in [0, 1]."""


# ---------------------------------------------------------------------------
# Ollama judge (default, hermetic)
# ---------------------------------------------------------------------------

class OllamaJudge(JudgeAdapter):
    """Calls the in-process Ollama server. Model defaults to the one
    configured in `ATHENA_OLLAMA_MODEL` (env), so the same model that
    powers the chat engine also grades it — keeping the eval loop
    hermetic and free.
    """

    name = "ollama"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self.base_url = (
            base_url or os.environ.get("ATHENA_OLLAMA_URL") or "http://localhost:11434"
        ).rstrip("/")
        self.model = model or os.environ.get("ATHENA_OLLAMA_MODEL") or "qwen2.5:1.5b-instruct"
        self.timeout_s = float(os.environ.get("ATHENA_OLLAMA_TIMEOUT", timeout_s))

    async def score(
        self,
        prompt: str,
        *,
        system: str = "",
        max_tokens: int = 256,
    ) -> JudgeResult:
        import httpx

        body = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": max_tokens},
        }
        if system:
            body["system"] = system

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            r = await client.post(f"{self.base_url}/api/generate", json=body)
            r.raise_for_status()
            data = r.json()
        text = (data.get("response") or "").strip()
        score = _parse_score_from_text(text)
        return JudgeResult(score=score, explanation=text, raw=data)


# ---------------------------------------------------------------------------
# OpenAI judge (gold standard; opt-in via $OPENAI_API_KEY)
# ---------------------------------------------------------------------------

class OpenAIJudge(JudgeAdapter):
    """Calls an OpenAI-compatible /chat/completions endpoint. Used by
    the nightly suite for the gold-standard eval pass. Requires
    $OPENAI_API_KEY in the environment.
    """

    name = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout_s: float = 60.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required for OpenAIJudge")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    async def score(
        self,
        prompt: str,
        *,
        system: str = "",
        max_tokens: int = 256,
    ) -> JudgeResult:
        import httpx

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            r = await client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
            r.raise_for_status()
            data = r.json()
        text = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        score = _parse_score_from_text(text)
        return JudgeResult(score=score, explanation=text, raw=data)


# ---------------------------------------------------------------------------
# Heuristic judge (offline, no LLM)
# ---------------------------------------------------------------------------

class HeuristicJudge(JudgeAdapter):
    """A no-LLM judge. Returns a similarity score in [0,1] based on
    token overlap (Jaccard) between the prompt's `REFERENCE:` and
    `ANSWER:` sections. Used by CI as the offline gate when Ollama
    is unavailable.
    """

    name = "heuristic"

    def __init__(self) -> None:
        # Eagerly compile a stop-word list once.
        self._stop = _STOPWORDS

    async def score(
        self,
        prompt: str,
        *,
        system: str = "",
        max_tokens: int = 256,
    ) -> JudgeResult:
        ref, ans = _split_reference_answer(prompt)
        if not ref or not ans:
            return JudgeResult(score=0.0, explanation="missing REFERENCE or ANSWER")
        ref_toks = _tokenize(ref, self._stop)
        ans_toks = _tokenize(ans, self._stop)
        if not ref_toks or not ans_toks:
            return JudgeResult(score=0.0, explanation="empty token sets")
        inter = len(ref_toks & ans_toks)
        union = len(ref_toks | ans_toks)
        jaccard = inter / union if union else 0.0
        # Stretch jaccard into a more useful scale: 0.5 jaccard ~ 0.7 useful
        score = min(1.0, jaccard * 1.4)
        return JudgeResult(
            score=score,
            explanation=f"jaccard={jaccard:.3f} overlap={inter}/{len(ref_toks)} ref-tokens",
        )


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def get_judge(choice: str | None = None) -> JudgeAdapter:
    """Pick a JudgeAdapter per the runner flag (or env var)."""
    choice = (choice or os.environ.get("ATHENA_EVAL_JUDGE") or "ollama").lower()
    if choice == "openai":
        return OpenAIJudge()
    if choice == "heuristic":
        return HeuristicJudge()
    return OllamaJudge()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Common stop-words for the heuristic judge. Deliberately small.
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "in", "on", "at", "to", "for", "by", "with", "and", "or", "but",
    "as", "if", "then", "than", "so", "do", "does", "did", "have", "has",
    "had", "this", "that", "these", "those", "i", "you", "we", "they",
})


def _tokenize(text: str, stop: frozenset[str]) -> set[str]:
    text = text.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    return {w for w in text.split() if w and w not in stop}


def _split_reference_answer(prompt: str) -> tuple[str, str]:
    """Pull REFERENCE: / ANSWER: sections out of a judge prompt."""
    ref = ""
    ans = ""
    for line in prompt.splitlines():
        if line.startswith("REFERENCE:"):
            ref = line[len("REFERENCE:"):].strip()
        elif line.startswith("ANSWER:"):
            ans = line[len("ANSWER:"):].strip()
    return ref, ans


_SCORE_RE = re.compile(r"\b(?:score|rating)\s*[:=]\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
_FRAC_RE = re.compile(r"\b(0(?:\.[0-9]+)?|1(?:\.0+)?)\b")


def _parse_score_from_text(text: str) -> float:
    """Extract a 0..1 score from a judge model's free-text response.

    Accepts:
      - "Score: 0.85"
      - "Rating: 0.7/1"
      - bare decimal 0.62
      - percentage (0.85 -> 0.85; 85% -> 0.85)
    """
    m = _SCORE_RE.search(text)
    if m:
        v = float(m.group(1))
        return v / 100.0 if v > 1.0 else v
    # Fall back: take the first 0..1 number in the text.
    m2 = _FRAC_RE.search(text)
    if m2:
        v = float(m2.group(1))
        if "%" in text and v > 1.0:
            return v / 100.0
        return v
    return 0.0
