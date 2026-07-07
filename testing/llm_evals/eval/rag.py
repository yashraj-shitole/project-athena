"""RAG-specific scorers.

Wraps `precision_at_k` / `recall_at_k` in the RAG-friendly names
(so the scenario reads naturally: `scorers=[context_precision(),
context_recall()]`).
"""
from __future__ import annotations

from typing import Any, Callable

from .matchers import EvalResultLike, ScorerResult
from .metrics import precision_at_k, recall_at_k


def context_precision(k: int = 4) -> Callable[[Any], ScorerResult]:
    """Alias of `precision_at_k` with a RAG-friendly name."""
    return precision_at_k(k=k)


def context_recall(k: int = 4) -> Callable[[Any], ScorerResult]:
    """Alias of `recall_at_k` with a RAG-friendly name."""
    return recall_at_k(k=k)
