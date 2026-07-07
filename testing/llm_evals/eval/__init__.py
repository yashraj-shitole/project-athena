"""Project Athena's LLM evaluation framework.

The eval package is hand-rolled and swappable. The default judge is
Ollama (hermetic, offline). The interface — `JudgeAdapter` — is the
seam where ragas / deepeval could be dropped in later without
changing the scenario or scorer code.

Layout:
    scenario   - the @scenario decorator + Scenario dataclass
    runners    - pytest collection + execution
    matchers   - deterministic scorers (exact, regex, contains, schema,
                 citation count, tool-call shape, refusal, no-citation)
    judges     - JudgeAdapter ABC + OllamaJudge / OpenAIJudge / HeuristicJudge
    metrics    - groundedness, faithfulness, relevance, MRR, nDCG, P@K, R@K
    rag        - RAG-specific scorers (context_precision, context_recall)
    tool_calls - tool-selection + parameter-schema scorers
    baselines  - load/save/compare JSON baselines; regression detection
    reports    - HTML + JSON + Markdown + CSV writers

Public surface (re-exported here):
    from llm_evals.eval import (
        scenario, Scenario,
        run, run_scenario, EvalRunner,
        exact_match, contains, regex, json_schema, citation_count,
        tool_call_shape, refuses, no_citation, refuses_injection,
        groundedness, faithfulness, answer_relevance,
        precision_at_k, recall_at_k, mrr, ndcg,
        JudgeAdapter, OllamaJudge, OpenAIJudge, HeuristicJudge, get_judge,
    )
"""
from __future__ import annotations

from .scenario import Scenario, scenario
from .runners import EvalRunner, run, run_scenario, set_runner
from .matchers import (
    exact_match,
    contains,
    regex,
    json_schema,
    citation_count,
    tool_call_shape,
    refuses,
    no_citation,
    refuses_injection,
)
from .judges import (
    JudgeAdapter,
    JudgeResult,
    OllamaJudge,
    OpenAIJudge,
    HeuristicJudge,
    get_judge,
)
from .metrics import (
    groundedness,
    faithfulness,
    answer_relevance,
    precision_at_k,
    recall_at_k,
    mrr,
    ndcg,
    unsupported_claim_rate,
    missing_citation_rate,
)
from .rag import context_precision, context_recall
from .tool_calls import tool_selection_accuracy, parameter_schema_adherence

__all__ = [
    "Scenario",
    "scenario",
    "EvalRunner",
    "run",
    "run_scenario",
    "set_runner",
    # matchers
    "exact_match",
    "contains",
    "regex",
    "json_schema",
    "citation_count",
    "tool_call_shape",
    "refuses",
    "no_citation",
    "refuses_injection",
    # judges
    "JudgeAdapter",
    "JudgeResult",
    "OllamaJudge",
    "OpenAIJudge",
    "HeuristicJudge",
    "get_judge",
    # metrics
    "groundedness",
    "faithfulness",
    "answer_relevance",
    "precision_at_k",
    "recall_at_k",
    "mrr",
    "ndcg",
    "unsupported_claim_rate",
    "missing_citation_rate",
    # rag
    "context_precision",
    "context_recall",
    # tool calls
    "tool_selection_accuracy",
    "parameter_schema_adherence",
]
